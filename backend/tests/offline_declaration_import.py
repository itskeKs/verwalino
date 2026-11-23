"""Process the queued declaration imports of one organization once, with offline providers (issue #499).

The user check of a pull request runs on an acceptance instance (scripts/acceptance-env.sh) that has no provider
access: the instance runs only the API. This is the worker for it: the real ``DeclarationImportWorker`` (claim,
lease, heartbeat, pipeline, ledger, budget) with the two providers replaced by the deterministic fakes of the browser
journey, which know the fictional Lindenallee declaration (docs/acceptance/data). Both model ids come from the
organization's saved settings the way the worker takes them (the job's first run wins on a resume), so the records
printed at the end show which model was asked for which page. Test tree only: no product switch, no configuration key.

``--inventory-answer`` changes what the stand-in extraction model's inventory says, for the user check of the failures and
hints that an inventory answer can cause: ``relation-to-unuploaded-document`` (the declaration REFERENCES a document that
was not uploaded: the import reaches the review with a hint) or ``unknown-document`` (the answer names a document of its
own that is not in the import: the import fails before any extraction call). Without it the answer is the plain one.

The stored API key is neither decrypted nor used here (an instance has its own session secret, and no provider is
called), so the saved key only has to exist, as for the real worker.

By hand, against the database and the document directory of the running acceptance instance (the models come
from what the administrator saved under Einstellungen; the instance has the queued import to process):

    cd <repository>/backend && . ../scripts/test-env.sh && uv run --locked --extra dev --extra ops \\
      python -m tests.offline_declaration_import --organization "<name or id>" \\
      --documents "$(sed -n 's/^scratch=//p' /tmp/hausverwaltung-acceptance.lock/instance)/documents" \\
      [--inventory-answer relation-to-unuploaded-document|unknown-document]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from unittest.mock import patch

from pydantic import SecretStr
from sqlalchemy import select

from app.core.ai_models import model_price
from app.core.db import get_worker_tenant_session
from app.core.storage import DocumentStore
from app.declarations.fake_provider import FakeDocumentReadingProvider, ReadingRecord
from app.declarations.pipeline import DeclarationPipeline
from app.declarations.provider import ProviderUsage
from app.models.declaration_import import DeclarationImport, DeclarationImportDocument
from app.repositories.declaration_import_repo import DeclarationImportRepository, WorkerLease
from app.services import ai_settings_service
from app.workers.declaration_import import DeclarationImportWorker, WorkerResult
from tests.declaration_fixtures import ModelRecording, SlowReadingProvider, lindenallee_reading_blocks

MAX_IMPORTS = 25
# The inventory answers besides the plain one, and the state every import is expected to end in for each.
INVENTORY_ANSWERS = {
    "relation-to-unuploaded-document": ("NEEDS_REVIEW", None),
    "unknown-document": ("FAILED", "UNKNOWN_INVENTORY_DOCUMENT"),
}


def _usage(model: str) -> ProviderUsage:
    """What one offline call consumed: a small reported usage, at no cost for a model without a price (as a provider
    reports it; the offline providers have no price provider of their own, so the pipeline prices them as OpenAI)."""
    return ProviderUsage(17, 11, Decimal("0.000001") if model_price(model, "openai") else Decimal("0"), 1)


def fictional_provider(document_id, usage: ProviderUsage | None = None, inventory_answer: str | None = None):
    """The offline extraction provider for the fictional Lindenallee declaration: its units and shares.

    ``inventory_answer`` is one of ``INVENTORY_ANSWERS``: the inventory then relates to a document that was not
    uploaded, or names a document of its own that is not in the import.
    """
    from app.declarations.fake_provider import FakeDeclarationProvider
    from app.declarations.schemas import (
        CoreExtractionOutput, DocumentInventoryOutput, EvidencePayload, FactPayload, IntegerValue, MeaFraction,
        ReconciliationOutput, RuleExtractionOutput, TargetedRecheckOutput, TextValue,
    )
    facts = []
    def fact(subject, field, value, quote):
        unit_quotes = {'unit:1':'1 Wohnung / Erdgeschoss 450 / 1.000',
                       'unit:2':'2 Wohnung / Obergeschoss 450 / 1.000',
                       'unit:G1':'G1 Garage / Hof 100 / 1.000'}
        facts.append(FactPayload(subject_key=subject,field=field,raw_value=quote,normalized_value=value,confidence=.99,
            evidence=[EvidencePayload(document_key=document_id,pdf_page=1,quote=unit_quotes.get(subject,quote))]))
    for field_name,value,quote in [('name','WEG Lindenallee 12','WEG Lindenallee 12'), ('street','Lindenallee 12','Lindenallee 12'),
                              ('zip_code','01234','01234'), ('city','Musterstadt','Musterstadt'), ('management_type','WEG','WEG Lindenallee 12')]:
        fact('property:main',field_name,TextValue(kind='TEXT',value=value),quote)
    fact('property:main','mea_denominator',IntegerValue(kind='INTEGER',value=1000),'1.000')
    for number,share,kind,location in [('1',450,'WOHNUNG','Erdgeschoss'),('2',450,'WOHNUNG','Obergeschoss'),('G1',100,'STELLPLATZ','Garage / Hof')]:
        subject = f'unit:{number}'
        fact(subject,'number',TextValue(kind='TEXT',value=number),number)
        fact(subject,'usage_type',TextValue(kind='TEXT',value=kind),'Wohnung' if kind=='WOHNUNG' else 'Garage')
        fact(subject,'location',TextValue(kind='TEXT',value=location),location)
        fact(subject,'mea',MeaFraction(kind='MEA',numerator=share,denominator=1000),str(share))
    entry = {'document_key':document_id,'document_type':'DECLARATION','title':'Fiktive Teilungserklärung'}
    if inventory_answer == 'relation-to-unuploaded-document':
        entry['relations'] = [{'relation_type':'REFERENCES','target_document_key':'Aufteilungsplan',
            'evidence':[{'document_key':document_id,'pdf_page':1,'quote':'WEG Lindenallee 12'}]}]
    elif inventory_answer == 'unknown-document':
        entry['document_key'] = 'not-a-document-of-the-import'
    return FakeDeclarationProvider(
        inventory_output=DocumentInventoryOutput(documents=[entry]),
        core_output=CoreExtractionOutput(facts=facts), rules_output=RuleExtractionOutput(rules=[]),
        reconciliation_output=ReconciliationOutput(facts=facts,rules=[],mea_set_status='COMPLETE'), recheck_output=TargetedRecheckOutput(),
        usage=usage)


@dataclass
class ImportRecords:
    """What the offline providers were asked while one import was processed: the model of every call."""

    import_id: uuid.UUID
    reading_model: str
    extraction_model: str
    reads: list[ReadingRecord] = field(default_factory=list)
    calls: list[tuple[str, str, tuple[tuple[str, int], ...]]] = field(default_factory=list)
    status: str | None = None
    error_code: str | None = None

    def lines(self) -> list[str]:
        state = self.status + (f" ({self.error_code})" if self.error_code else "")
        lines = [f"import {self.import_id}: {state}",
                 f"  models from the saved settings: reading {self.reading_model}, extraction {self.extraction_model}"]
        lines += [f"  reading     model={read.model} document={read.document_key} page={read.pdf_page}" for read in self.reads]
        lines += [f"  extraction  model={model} call={method} pages={','.join(f'{key}:{page}' for key, page in pages) or '-'}"
                  for method, model, pages in self.calls]
        return lines


@dataclass
class OfflineRun:
    results: list[WorkerResult]
    imports: list[ImportRecords]

    def lines(self) -> list[str]:
        lines = [line for records in self.imports for line in records.lines()]
        lines.append(f"worker results: {', '.join(result.code for result in self.results)}")
        return lines


async def _saved_settings(db, organization_id):
    """The organization's saved settings as the worker reads them, without decrypting the stored key."""
    from app.repositories.organization_ai_settings_repo import OrganizationAiSettingsRepository
    row = await OrganizationAiSettingsRepository(db, organization_id).get()
    if row is None:
        return ai_settings_service.EffectiveAiSettings()
    return ai_settings_service.EffectiveAiSettings(
        row.provider, SecretStr("offline") if row.encrypted_api_key else None, row.model, row.import_enabled,
        row.qa_enabled, row.monthly_limit_usd, row.base_url, row.request_timeout_seconds, row.document_model)


class _OfflineWorker(DeclarationImportWorker):
    """The real worker; only the pipeline's providers are the offline ones, built per claimed import."""

    def __init__(self, store: DocumentStore, gate, inventory_answer: str | None = None, **options) -> None:
        super().__init__(**options)
        self._store, self._gate, self._inventory_answer = store, gate, inventory_answer
        self.records: list[ImportRecords] = []

    async def _run_claimed(self, lease: WorkerLease) -> WorkerResult:
        # The models the worker would use: the saved settings, and a job keeps those of its first run.
        effective = self._active_ai
        async with self._tenant_session_factory(lease.organization_id) as session:
            identities = await DeclarationImportRepository(session, lease.organization_id).job_model_identities(lease.import_id)
            document_id = await session.scalar(
                select(DeclarationImportDocument.document_id)
                .where(DeclarationImportDocument.organization_id == lease.organization_id)
                .where(DeclarationImportDocument.import_id == lease.import_id)
                .order_by(DeclarationImportDocument.sequence_no).limit(1))
        extraction_model, reading_model = identities or (effective.model, effective.document_model)
        key = str(document_id)
        extraction = ModelRecording(fictional_provider(key, _usage(extraction_model), self._inventory_answer), extraction_model)
        reader = FakeDocumentReadingProvider(transcripts={(key, 1): lindenallee_reading_blocks()}, usage=_usage(reading_model))
        records = ImportRecords(lease.import_id, reading_model, extraction_model, reader.reads, extraction.calls)
        self.records.append(records)
        self._pipeline = DeclarationPipeline(
            extraction, store=self._store, reading_model=reading_model, tenant_session_factory=self._tenant_session_factory,
            reading_provider=reader if self._gate is None else SlowReadingProvider(self._gate, reader))
        try:
            return await super()._run_claimed(lease)
        finally:
            self._pipeline = None


@contextmanager
def _saved_settings_without_the_key():
    with patch.object(ai_settings_service, "effective_ai_settings", _saved_settings):
        yield


async def process_queued_imports(
    organization_id: uuid.UUID, *, store: DocumentStore, gate=None, inventory_answer: str | None = None
) -> OfflineRun:
    """Process every queued declaration import of the organization once and return what the providers were asked.

    ``gate`` makes the page reading slow like a real model call, so that the lease has to be renewed meanwhile;
    ``inventory_answer`` is one of ``INVENTORY_ANSWERS``.
    """
    results: list[WorkerResult] = []
    with _saved_settings_without_the_key():
        worker = _OfflineWorker(
            store, gate, inventory_answer, **({"sleep": gate.release_after_renewal} if gate is not None else {}))
        for _ in range(MAX_IMPORTS):
            results.append(await worker.run_once(organization_id=organization_id))
            if results[-1].code in {"IDLE", "DECLARATION_IMPORT_DISABLED", "SCHEMA_NOT_READY"}:
                break
    for records in worker.records:
        async with get_worker_tenant_session(organization_id) as session:
            item = await session.get(DeclarationImport, records.import_id)
            records.status, records.error_code = item.status, item.error_code
    return OfflineRun(results, worker.records)


def _organization_id(name_or_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(name_or_id)
    except ValueError:
        pass
    import psycopg2
    with psycopg2.connect(os.environ["HV_DATABASE_URL_SYNC"]) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM public.organization WHERE name = %s", (name_or_id,))
        ids = [row[0] for row in cursor.fetchall()]
    if len(ids) != 1:
        raise SystemExit(f"{len(ids)} organizations are named {name_or_id!r}; pass the id of the one to process")
    return ids[0]


@asynccontextmanager
async def offline_worker_engine():
    """The worker role of the disposable test database as this process's worker engine (shared with the invoice command)."""
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.core.config import settings
    from app.core.db import override_worker_engine

    # The disposable test database only, with the worker role its init script creates (as the backend tests do).
    engine = create_async_engine(make_url(settings.database_url).set(username="app_worker", password="worker_secret"))
    override_worker_engine(engine)
    try:
        yield
    finally:
        override_worker_engine(None)
        await engine.dispose()


async def _main(organization_id: uuid.UUID, store: DocumentStore, inventory_answer: str | None) -> OfflineRun:
    async with offline_worker_engine():
        return await process_queued_imports(organization_id, store=store, inventory_answer=inventory_answer)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--organization", required=True, help="name or id of the organization")
    parser.add_argument("--documents", required=True, help="the document storage root of the instance")
    parser.add_argument("--inventory-answer", choices=sorted(INVENTORY_ANSWERS),
                        help="what the stand-in inventory says: a relation to a document that was not uploaded, or a document "
                             "that is not in the import (the import then fails before any extraction call)")
    arguments = parser.parse_args(argv)
    from app.core.config import settings
    from app.core.declaration_test_database import require_disposable_database_settings
    from app.core.storage import LocalFsDocumentStore

    require_disposable_database_settings(settings)
    run = asyncio.run(_main(_organization_id(arguments.organization), LocalFsDocumentStore(arguments.documents),
                            arguments.inventory_answer))
    print("\n".join(run.lines()))
    expected = INVENTORY_ANSWERS.get(arguments.inventory_answer, ("NEEDS_REVIEW", None))
    return 0 if run.imports and all((item.status, item.error_code) == expected for item in run.imports) else 1


if __name__ == "__main__":
    sys.exit(main())
