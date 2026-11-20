"""The declaration import reads pages without a text layer with the reading model (issue #499, ADR 0017).

Everything here runs the real queue-fenced pipeline against the test database with the in-process fake readers:
no provider call is made, and Tesseract is made unusable for the cases that prove it is no longer in the path.
"""
from __future__ import annotations

import io
import subprocess
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import func, select

from app.core.config import settings
from app.core.db import get_worker_tenant_session as get_tenant_session
from app.declarations.fake_provider import FakeDocumentReadingProvider
from app.declarations.pipeline import DeclarationPipeline
from app.declarations.provider import ProviderFailure, ProviderUsage
from app.declarations.schemas import ReadingBlock
from app.models.declaration_candidate import DeclarationCandidate, DeclarationCandidateEvidence
from app.models.declaration_import import (
    DeclarationExtractionRun,
    DeclarationImport,
    DeclarationImportPass,
    DeclarationPageReading,
)
from app.models.property_document import PropertyDocument
from app.repositories.declaration_import_repo import WorkerLease
from app.services.ai_settings_service import EffectiveAiSettings
from app.workers.declaration_import import DeclarationImportWorker
from tests.ai_settings_support import configure_ai
from tests.declaration_fixtures import ModelRecording, lindenallee_reading_blocks
from tests.test_declaration_pipeline import (
    _RecordingProvider,
    _safe_attempt_metadata,
    _seed_pipeline_import,
)
from tests.bdd.steps.browser_declaration import fictional_provider

ROOT = Path(__file__).resolve().parents[2]
EXTRACTION_MODEL, READING_MODEL = "gpt-5.6-luna", "gpt-5.4-mini"
TEXT = "Teilungserklärung Hauptstraße 1"


def _scan(pages: int = 1) -> bytes:
    """A scanned declaration: one image per page and no text layer."""
    images = [Image.new("L", (200, 300), 255) for _ in range(pages)]
    out = io.BytesIO()
    images[0].save(out, "PDF", save_all=True, append_images=images[1:], resolution=72)
    return out.getvalue()


# A model without a known price reports a cost of zero, like the real providers do.
UNPRICED = ProviderUsage(17, 11, Decimal("0"), 1)


def _text(value: str = TEXT) -> list[ReadingBlock]:
    return [ReadingBlock(kind="TEXT", text=value)]


def _failure(code: str, *, retryable: bool = False, usage: ProviderUsage | None = None) -> ProviderFailure:
    metadata = _safe_attempt_metadata(pass_id="read_page", usage=usage, failure_class=code)
    return ProviderFailure(code, retryable, "The page could not be read.", usage=usage, attempt_metadata=(metadata,))


@pytest.fixture(autouse=True)
def leave_no_cost_behind():
    """The tests share the organization and its monthly usage: the reservations they keep must not count later."""
    yield
    import psycopg2
    from tests.conftest import MIGRATOR_SYNC_URL

    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE declaration_extraction_run SET estimated_cost_usd=0 WHERE estimated_cost_usd<>0")


@pytest.fixture
def tesseract_unusable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any pytesseract use or tesseract process raises: the declaration path must not need either."""
    blocked = types.ModuleType("pytesseract")

    def refuse(name: str):
        raise AssertionError(f"pytesseract.{name} was used")

    blocked.__getattr__ = refuse  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pytesseract", blocked)
    real_popen = subprocess.Popen

    class GuardedPopen(real_popen):
        def __init__(self, args, *rest, **options):
            argv = args if isinstance(args, (list, tuple)) else [args]
            if any("tesseract" in str(part).lower() for part in argv):
                raise AssertionError("a tesseract process was started")
            super().__init__(args, *rest, **options)

    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)


def _pipeline(extraction, reader, store, *, configured=settings, preflight=None, reading_model=READING_MODEL):
    return DeclarationPipeline(
        extraction,
        store=store,
        configured=configured,
        preflight=preflight,
        reading_provider=reader,
        reading_model=reading_model if reader is not None else None,
    )


async def _item(lease: WorkerLease) -> DeclarationImport:
    async with get_tenant_session(lease.organization_id) as session:
        return await session.get(DeclarationImport, lease.import_id)


async def _read_page_passes(lease: WorkerLease) -> list[dict]:
    async with get_tenant_session(lease.organization_id) as session:
        rows = (await session.scalars(
            select(DeclarationImportPass).join(
                DeclarationExtractionRun,
                DeclarationExtractionRun.id == DeclarationImportPass.declaration_extraction_run_id,
            ).where(DeclarationExtractionRun.import_id == lease.import_id).order_by(
                DeclarationExtractionRun.started_at, DeclarationImportPass.attempt_no)
        )).all()
    return [row.safe_metadata for row in rows if row.safe_metadata["pass_id"] == "read_page"]


async def _rearm(lease: WorkerLease) -> WorkerLease:
    """A later claim of the same job after a retryable failure: a new lease generation."""
    token, expires = uuid.uuid4(), datetime.now(timezone.utc) + timedelta(minutes=30)
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.get(DeclarationImport, lease.import_id)
        item.lease_owner, item.lease_token, item.lease_expires_at = "test-worker", token, expires
        item.lease_generation = lease.lease_generation + 1
    return WorkerLease(lease.organization_id, lease.import_id, lease.lease_generation + 1, token, expires, lease.queue_request_id)


# ---- the journey: a scanned declaration reaches review without Tesseract -------------------------------------------


async def test_a_scanned_declaration_reaches_review_through_the_reading_model_without_tesseract(
    seed_data, migrator_session, tesseract_unusable
) -> None:
    pdf = (ROOT / "docs/acceptance/data/teilungserklaerung-lindenallee-12.pdf").read_bytes()
    lease, key, store = await _seed_pipeline_import(seed_data, pdf)
    extraction = ModelRecording(fictional_provider(key), EXTRACTION_MODEL)
    reader = FakeDocumentReadingProvider(transcripts={(key, 1): lindenallee_reading_blocks()})

    result = await DeclarationImportWorker(_pipeline(extraction, reader, store))._run_claimed(lease)

    assert result.code == "PROCESSED"
    item = await _item(lease)
    assert (item.status, item.error_code) == ("NEEDS_REVIEW", None)
    candidates = (await migrator_session.scalars(select(DeclarationCandidate).where(
        DeclarationCandidate.import_id == lease.import_id, DeclarationCandidate.candidate_type == "UNIT_CREATE"))).all()
    shares = {c.proposed_value_json["number"]["value"]: (c.proposed_value_json["mea"]["numerator"],
                                                         c.proposed_value_json["mea"]["denominator"]) for c in candidates}
    assert shares == {"1": (450, 1000), "2": (450, 1000), "G1": (100, 1000)}
    evidence = (await migrator_session.scalars(select(DeclarationCandidateEvidence).where(
        DeclarationCandidateEvidence.import_id == lease.import_id))).all()
    assert evidence and {row.pdf_page for row in evidence} == {1}
    # Page reading used only the reading selection, extraction only the extraction selection.
    assert [(read.model, read.pdf_page) for read in reader.reads] == [(READING_MODEL, 1)]
    assert {model for _method, model in extraction.records} == {EXTRACTION_MODEL}
    assert {method for method, _model in extraction.records} >= {"inventory", "extract_core", "extract_rules", "reconcile"}


async def test_the_page_transcript_is_the_extraction_input_with_its_table_unreadable_marker_and_page(
    seed_data, tesseract_unusable
) -> None:
    lease, key, store = await _seed_pipeline_import(seed_data, _scan(2))
    extraction = ModelRecording(_RecordingProvider(key), EXTRACTION_MODEL)
    reader = FakeDocumentReadingProvider(transcripts={
        (key, 1): [ReadingBlock(kind="TEXT", text=TEXT),
                   ReadingBlock(kind="TABLE", rows=[["Einheit", "MEA"], ["1", "450 / 1.000"]])],
        (key, 2): [ReadingBlock(kind="TEXT", text="Anteil [unreadable] Stimmen"), ReadingBlock(kind="UNREADABLE")],
    })

    await DeclarationImportWorker(_pipeline(extraction, reader, store))._run_claimed(lease)

    pages = {(page.document_key, page.pdf_page): page.text
             for document in extraction.documents for page in document.pages}
    assert pages[(key, 1)] == f"{TEXT}\n[table]\nEinheit | MEA\n1 | 450 / 1.000\n[/table]"
    assert pages[(key, 2)] == "Anteil [unreadable] Stimmen\n[unreadable]"
    assert (await _item(lease)).status == "NEEDS_REVIEW"


async def test_a_page_with_a_text_layer_keeps_the_deterministic_text_path_and_is_not_read(seed_data) -> None:
    from tests.declaration_fixtures import make_text_pdf

    lease, key, store = await _seed_pipeline_import(seed_data, make_text_pdf(f"{TEXT}\nEinheit 1 Wohnung im Erdgeschoss"))
    reader = FakeDocumentReadingProvider()

    await DeclarationImportWorker(_pipeline(ModelRecording(_RecordingProvider(key), EXTRACTION_MODEL), reader, store))._run_claimed(lease)

    assert reader.reads == [] and (await _item(lease)).status == "NEEDS_REVIEW"


# ---- failures end the job with their structured code, never with a fallback -------------------------------------


@pytest.mark.parametrize("code", ["PROVIDER_IMAGE_INPUT_UNSUPPORTED", "PROVIDER_REFUSAL", "PROVIDER_SCHEMA_FAILURE"])
async def test_a_failed_reading_ends_the_job_keeps_the_original_and_the_consumed_usage(
    seed_data, migrator_session, tesseract_unusable, code
) -> None:
    lease, key, store = await _seed_pipeline_import(seed_data, _scan())
    extraction = ModelRecording(_RecordingProvider(key), EXTRACTION_MODEL)
    usage = ProviderUsage(40, 3, Decimal("0.000002"), 1)
    # The fake reserves what its usage says: 100 input tokens, more than the failed call consumed.
    reader = FakeDocumentReadingProvider(usage=ProviderUsage(100, 20, Decimal("0.00001"), 1),
                                         failures={(key, 1): _failure(code, usage=usage)})

    await DeclarationImportWorker(_pipeline(extraction, reader, store))._run_claimed(lease)

    item = await _item(lease)
    assert (item.status, item.error_code) == ("FAILED", code)
    assert extraction.records == [], "no extraction call after a failed reading"
    assert (await migrator_session.scalar(select(func.count()).select_from(DeclarationCandidate).where(
        DeclarationCandidate.import_id == lease.import_id))) == 0, "no fact is applied"
    assert (await migrator_session.scalar(select(func.count()).select_from(PropertyDocument).where(
        PropertyDocument.organization_id == lease.organization_id))) >= 1 and store.get(f"memory/{key}.pdf")
    # The failed call's usage is durable on the job, on its run and in the attempt ledger.
    assert (item.input_tokens, item.output_tokens) == (40, 3)
    passes = await _read_page_passes(lease)
    assert [(p["failure_class"], p["input_tokens"], p["output_tokens"]) for p in passes] == [(code, 40, 3)]
    assert (await migrator_session.scalar(select(func.count()).select_from(DeclarationPageReading).where(
        DeclarationPageReading.import_id == lease.import_id))) == 0


async def test_a_missing_reading_model_ends_the_job_before_any_call(seed_data, tesseract_unusable) -> None:
    lease, key, store = await _seed_pipeline_import(seed_data, _scan())
    extraction = ModelRecording(_RecordingProvider(key), EXTRACTION_MODEL)

    await DeclarationImportWorker(_pipeline(extraction, None, store))._run_claimed(lease)

    item = await _item(lease)
    assert (item.status, item.error_code) == ("FAILED", "DOCUMENT_MODEL_NOT_CONFIGURED")
    assert extraction.records == []


# ---- reading is metered in the shared ledger -----------------------------------------------------------------------


async def test_reading_counts_against_the_import_budget_and_stops_before_the_next_call(seed_data) -> None:
    lease, key, store = await _seed_pipeline_import(seed_data, _scan(2))
    extraction = ModelRecording(_RecordingProvider(key), EXTRACTION_MODEL)
    reader = FakeDocumentReadingProvider(transcripts={(key, 1): _text(), (key, 2): _text("Seite zwei")})
    tight = settings.model_copy(update={"declaration_max_input_tokens": 30})  # the fake reads 17 tokens per page

    await DeclarationImportWorker(_pipeline(extraction, reader, store, configured=tight))._run_claimed(lease)

    item = await _item(lease)
    assert (item.status, item.error_code) == ("FAILED", "IMPORT_TOKEN_BUDGET_EXCEEDED")
    assert [read.pdf_page for read in reader.reads] == [1], "the exhausted budget stopped the second request"
    assert item.input_tokens == 17 and extraction.records == []


async def test_a_call_with_unknown_usage_keeps_its_reservation(seed_data) -> None:
    lease, key, store = await _seed_pipeline_import(seed_data, _scan())
    reader = FakeDocumentReadingProvider(failures={(key, 1): _failure("PROVIDER_TIMEOUT", retryable=True)})

    await DeclarationImportWorker(_pipeline(ModelRecording(_RecordingProvider(key), EXTRACTION_MODEL), reader, store))._run_claimed(lease)

    item = await _item(lease)
    assert (item.status, item.error_code) == ("RETRY_WAIT", "PROVIDER_TIMEOUT")
    # The fake reserves 17 input tokens and the model's full output allowance; none of it is given back.
    assert item.input_tokens == 17 and item.output_tokens == 12_000
    assert item.estimated_cost_usd > Decimal("0.05")


@pytest.mark.parametrize(("reading_model", "refused"), [(READING_MODEL, True), ("unpriced-vision-model", False)])
async def test_the_reading_models_price_counts_against_the_monthly_limit(
    seed_data, configured_ai, reading_model, refused
) -> None:
    configure_ai(seed_data["org_a_id"], model="unpriced-extraction-model", document_model=reading_model,
                 monthly_limit_usd=Decimal("0.50"))
    lease, key, store = await _seed_pipeline_import(seed_data, _scan())
    extraction = ModelRecording(_RecordingProvider(key, usage=UNPRICED), "unpriced-extraction-model")
    reader = FakeDocumentReadingProvider(transcripts={(key, 1): _text()}, usage=None if refused else UNPRICED)

    await DeclarationImportWorker(_pipeline(extraction, reader, store, reading_model=reading_model))._run_claimed(lease)

    item = await _item(lease)
    if refused:
        # The import may cost up to $1.00, the limit is $0.50: only the priced reading model makes that count.
        assert (item.status, item.error_code) == ("FAILED", "ORGANIZATION_AI_BUDGET_EXCEEDED") and reader.reads == []
    else:
        assert (item.status, item.error_code) == ("NEEDS_REVIEW", None) and len(reader.reads) == 1


# ---- resume ---------------------------------------------------------------------------------------------------------


async def test_a_resumed_job_neither_reads_nor_pays_for_a_page_it_already_transcribed(seed_data, migrator_session) -> None:
    lease, key, store = await _seed_pipeline_import(seed_data, _scan(3))
    first_reader = FakeDocumentReadingProvider(
        transcripts={(key, 1): _text(), (key, 2): _text("Seite zwei"), (key, 3): _text("Seite drei")},
        failures={(key, 2): _failure("PROVIDER_TIMEOUT", retryable=True, usage=ProviderUsage(5, 0, Decimal("0.0000005"), 1))},
    )

    await DeclarationImportWorker(_pipeline(ModelRecording(_RecordingProvider(key), EXTRACTION_MODEL), first_reader, store))._run_claimed(lease)

    assert (await _item(lease)).status == "RETRY_WAIT"
    assert [read.pdf_page for read in first_reader.reads] == [1, 2]
    saved = (await migrator_session.scalars(select(DeclarationPageReading.pdf_page).where(
        DeclarationPageReading.import_id == lease.import_id))).all()
    assert saved == [1], "only the finished page is saved"

    second_lease = await _rearm(lease)
    extraction = ModelRecording(_RecordingProvider(key), EXTRACTION_MODEL)
    second_reader = FakeDocumentReadingProvider(transcripts={(key, 1): _text("NEVER READ AGAIN"), (key, 2): _text("Seite zwei"),
                                                             (key, 3): _text("Seite drei")})
    await DeclarationImportWorker(_pipeline(extraction, second_reader, store))._run_claimed(second_lease)

    assert (await _item(lease)).status == "NEEDS_REVIEW"
    assert [read.pdf_page for read in second_reader.reads] == [2, 3], "page 1 is not asked for again"
    pages = {page.pdf_page: page.text for document in extraction.documents for page in document.pages}
    assert pages[1] == TEXT, "the saved transcript is the page's text"
    charges = [(p["input_tokens"], p["output_tokens"]) for p in await _read_page_passes(lease)]
    assert charges == [(17, 11), (5, 0), (17, 11), (17, 11)], "page 1 was charged once"


# ---- a job keeps the model identities it started with ----------------------------------------------------------------


async def test_a_settings_change_does_not_change_the_two_model_identities_of_a_running_or_resumed_job(
    seed_data, configured_ai, monkeypatch, migrator_session
) -> None:
    org = seed_data["org_a_id"]
    lease, key, store = await _seed_pipeline_import(seed_data, _scan(2))
    seen: list[tuple[str, str]] = []
    readers: list[FakeDocumentReadingProvider] = []

    class ChangingReader(FakeDocumentReadingProvider):
        async def read_page(self, page, *, model, max_output_tokens):
            result = await super().read_page(page, model=model, max_output_tokens=max_output_tokens)
            # The administrator picks two other models while the job is running.
            configure_ai(org, model="gpt-other-extraction", document_model="gpt-other-reading")
            return result

    def create_extraction(effective, **_options):
        seen.append(("extraction", effective.model))
        return ModelRecording(_RecordingProvider(key, usage=UNPRICED), effective.model)

    def create_reading(effective, **_options):
        seen.append(("reading", effective.document_model))
        reader = (ChangingReader if not readers else FakeDocumentReadingProvider)(
            transcripts={(key, 1): _text(), (key, 2): _text("Seite zwei")},
            failures={(key, 2): _failure("PROVIDER_TIMEOUT", retryable=True)} if not readers else None,
            usage=UNPRICED,
        )
        readers.append(reader)
        reader._client = None
        return reader

    monkeypatch.setattr("app.declarations.pipeline.get_store", lambda: store)  # the pipeline the worker builds
    monkeypatch.setattr("app.declarations.provider_runtime.create_extraction_provider", create_extraction)
    monkeypatch.setattr("app.declarations.provider_runtime.create_reading_provider", create_reading)

    def claim(model: str, document_model: str) -> DeclarationImportWorker:
        worker = DeclarationImportWorker(pipeline=None, configured=settings)
        worker._active_ai = EffectiveAiSettings(provider="openai", api_key=SecretStr("synthetic"), model=model,
                                                import_enabled=True, document_model=document_model)
        return worker

    await claim("gpt-first-extraction", "gpt-first-reading")._run_claimed(lease)
    first = await _item(lease)
    assert (first.status, first.error_code) == ("RETRY_WAIT", "PROVIDER_TIMEOUT")
    # The next claim sees the changed settings, but the job runs with its own two models.
    await claim("gpt-other-extraction", "gpt-other-reading")._run_claimed(await _rearm(lease))

    assert (await _item(lease)).status == "NEEDS_REVIEW"
    assert seen == [("extraction", "gpt-first-extraction"), ("reading", "gpt-first-reading")] * 2
    assert {read.model for reader in readers for read in reader.reads} == {"gpt-first-reading"}
    runs = (await migrator_session.scalars(select(DeclarationExtractionRun).where(
        DeclarationExtractionRun.import_id == lease.import_id).order_by(DeclarationExtractionRun.started_at))).all()
    assert [(run.model_name, run.document_model_name) for run in runs] == [("gpt-first-extraction", "gpt-first-reading")] * 2
