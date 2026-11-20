"""The declaration import reads pages without a text layer with the reading model (issue #499, ADR 0017).

Everything here runs the real queue-fenced pipeline against the test database with the in-process fake readers:
no provider call is made, and Tesseract is made unusable for the cases that prove it is no longer in the path.
"""
from __future__ import annotations

import hashlib
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
from sqlalchemy import func, select, text

from app.core.config import settings
from app.core.db import get_worker_tenant_session as get_tenant_session
from app.declarations.fake_provider import FakeDocumentReadingProvider
from app.declarations.pipeline import DeclarationPipeline, PipelineFailure, _Ledger
from app.declarations.preflight import PdfPreflight
from app.declarations.provider import ProviderFailure, ProviderUsage
from app.declarations.provider_runtime import CompatibleDeclarationProvider
from app.declarations.schemas import ReadingBlock
from app.models.declaration_candidate import DeclarationCandidate, DeclarationCandidateEvidence
from app.models.declaration_import import (
    DeclarationExtractionRun,
    DeclarationImport,
    DeclarationImportDocument,
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


@pytest.fixture
def running_run():
    """Commit RUNNING runs of imports (the worker's sessions must see them) and settle them afterwards, so that their
    cap does not count for a later test of the shared organization."""
    import psycopg2
    from tests.conftest import MIGRATOR_SYNC_URL

    created: list[uuid.UUID] = []

    def seed(lease: WorkerLease, *, model_name: str, document_model_name: str | None,
             provider: str = "OpenAiDeclarationProvider") -> None:
        run = uuid.uuid4()
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO declaration_extraction_run(id,organization_id,import_id,status,provider,model_name,
                   document_model_name,prompt_version,schema_version,harness_version)
                   VALUES (%s,%s,%s,'RUNNING',%s,%s,%s,'test','1','2')""",
                (str(run), str(lease.organization_id), str(lease.import_id), provider, model_name, document_model_name))
        created.append(run)

    yield seed
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE declaration_extraction_run SET status='FAILED', completed_at=now() WHERE id=ANY(%s::uuid[])",
                       ([str(run) for run in created],))


async def _month_to_date(organization_id: uuid.UUID) -> Decimal:
    """What the shared organization has already used this month: other tests retain costs and running imports in it,
    so the limits below are set relative to it."""
    from app.core.ai_models import MODEL_PRICES

    async with get_tenant_session(organization_id) as session:
        return await session.scalar(text("SELECT organization_ai_monthly_usage(:org,CAST(:models AS text[]))"),
                                    {"org": organization_id, "models": list(MODEL_PRICES)})


async def _monthly_budget_check(organization_id: uuid.UUID, amount: Decimal, model: str) -> str | None:
    """The check an import start and a question reservation run; the refusal code, or None when it passes."""
    from fastapi import HTTPException
    from app.services.ai_settings_service import require_organization_ai_budget

    async with get_tenant_session(organization_id) as session:
        try:
            await require_organization_ai_budget(session, organization_id, amount, model=model, provider="openai")
        except HTTPException as refusal:
            return refusal.detail["code"]
    return None


@pytest.mark.parametrize(("running_extraction", "running_reading", "refused"), [
    ("unpriced-extraction-model", READING_MODEL, True),  # only the reading model is priced: it counts, like the other
    (EXTRACTION_MODEL, "unpriced-vision-model", True),  # as before: a priced extraction model
    ("unpriced-extraction-model", "unpriced-vision-model", False),  # as before: nothing priced, nothing held
])
async def test_a_running_import_is_held_at_its_cap_when_its_extraction_or_its_reading_model_is_priced(
    seed_data, configured_ai, running_run, running_extraction, running_reading, refused
) -> None:
    org = seed_data["org_a_id"]
    configure_ai(org, model=EXTRACTION_MODEL, document_model=READING_MODEL,
                 monthly_limit_usd=await _month_to_date(org) + Decimal("1.50"))
    first, _key, _store = await _seed_pipeline_import(seed_data, _scan())  # its cap is 1.00 USD, nothing charged yet
    running_run(first, model_name=running_extraction, document_model_name=running_reading)

    # A priced request that would take the month past the limit once the running import's cap counts.
    code = await _monthly_budget_check(org, Decimal("1"), READING_MODEL)

    assert code == ("ORGANIZATION_AI_BUDGET_EXCEEDED" if refused else None)


async def test_a_second_import_is_refused_at_the_limit_while_a_priced_reading_model_holds_the_first_ones_cap(
    seed_data, configured_ai, running_run
) -> None:
    org = seed_data["org_a_id"]
    configure_ai(org, model="unpriced-extraction-model", document_model=READING_MODEL,
                 monthly_limit_usd=await _month_to_date(org) + Decimal("1.50"))
    first, _key, _store = await _seed_pipeline_import(seed_data, _scan())
    running_run(first, model_name="unpriced-extraction-model", document_model_name=READING_MODEL)
    lease, key, store = await _seed_pipeline_import(seed_data, _scan())
    extraction = ModelRecording(_RecordingProvider(key, usage=UNPRICED), "unpriced-extraction-model")
    reader = FakeDocumentReadingProvider(transcripts={(key, 1): _text()})

    await DeclarationImportWorker(_pipeline(extraction, reader, store))._run_claimed(lease)

    item = await _item(lease)
    assert (item.status, item.error_code) == ("FAILED", "ORGANIZATION_AI_BUDGET_EXCEEDED")
    assert reader.reads == [] and extraction.records == []


# ---- the per-import token limits fit the permitted 200 scanned pages --------------------------------------------------

# What a dense scanned A4 page costs in a declaration import, by the derivation beside the limits in
# app/core/config.py. Usage is reported without a price: the cost limit stays the money guard and is not under test.
DENSE_PAGE_CHARS = 4_000  # a dense page; 80,000 characters per request leave 19 to 20 of them in a batch
READ_PAGE_USAGE = ProviderUsage(5_000, 2_000, Decimal("0"), 1)  # image 3,000 to 4,100 + prompt/schema; the transcript
PASS_INPUT_PER_PAGE = 1_500  # one dense transcript page, in each of the three passes over all pages
PASS_CALL_OVERHEAD = {"inventory": 1_000, "extract_core": 2_300, "extract_rules": 2_300}  # prompt and schema per call
PASS_OUTPUT_PER_CALL = 1_500
RECONCILE_USAGE = ProviderUsage(40_000, 6_000, Decimal("0"), 1)  # a full 80,000-character payload
A4_AT_180_DPI = (1_489, 2_105)  # a scanned A4 page at the worker's resolution


def _flat_png(size: tuple[int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("L", size, 255).save(out, "PNG")
    return out.getvalue()


class _FlatRenderPreflight(PdfPreflight):
    """The real preflight with one cheap render: every page is the same flat A4 image at the worker's resolution."""

    page_png = _flat_png(A4_AT_180_DPI)

    def render_page_for_reading(self, pdf_bytes, page, *, budget):
        return self.page_png


class _WorstCaseReading(FakeDocumentReadingProvider):
    """Reports the usage of a dense page and reserves like the transport with the largest worst case."""

    def __init__(self, estimate, transcripts) -> None:
        super().__init__(transcripts=transcripts, usage=READ_PAGE_USAGE)
        self.estimate_reservation = estimate


class _DenseDeclaration(_RecordingProvider):
    """Reports the usage of the three passes over dense transcripts, and reserves like the largest transport."""

    def __init__(self, document_key: str, estimate) -> None:
        super().__init__(document_key)
        self.estimate_reservation = estimate
        self.reported_input = self.reported_output = 0

    def _report(self, usage: ProviderUsage) -> None:
        self._usage = usage
        self.reported_input += usage.input_tokens
        self.reported_output += usage.output_tokens

    def _bill(self, method: str, documents) -> None:
        pages = sum(len(document.pages) for document in documents)
        self._report(ProviderUsage(pages * PASS_INPUT_PER_PAGE + PASS_CALL_OVERHEAD[method], PASS_OUTPUT_PER_CALL, Decimal("0"), 1))

    async def inventory(self, documents, *, max_output_tokens):
        self._bill("inventory", documents)
        return await super().inventory(documents, max_output_tokens=max_output_tokens)

    async def extract_core(self, documents, *, max_output_tokens):
        self._bill("extract_core", documents)
        return await super().extract_core(documents, max_output_tokens=max_output_tokens)

    async def extract_rules(self, documents, *, max_output_tokens):
        self._bill("extract_rules", documents)
        return await super().extract_rules(documents, max_output_tokens=max_output_tokens)

    async def reconcile(self, inventory, core, rules, *, max_output_tokens):
        self._report(RECONCILE_USAGE)
        return await super().reconcile(inventory, core, rules, max_output_tokens=max_output_tokens)


@pytest.fixture
async def worst_case_estimate():
    """The real reservation estimator of a compatible endpoint: it publishes no image formula and counts every UTF-8
    byte as a token, so it reserves the most of all transports."""
    transport = CompatibleDeclarationProvider(EffectiveAiSettings(
        provider="openai_compatible", api_key=SecretStr("synthetic-key"), model="local-vl", base_url="http://localhost:11434/v1"))
    yield transport.estimate_reservation
    await transport._client.close()


async def test_a_declaration_of_the_permitted_200_scanned_pages_fits_the_import_token_limits(
    seed_data, worst_case_estimate
) -> None:
    pages = settings.declaration_ocr_max_pages_per_import
    assert pages == 200
    lease, key, store = await _seed_pipeline_import(seed_data, _scan(pages))
    filler = "Die Kosten der Instandhaltung des gemeinschaftlichen Eigentums tragen die Eigentümer nach Miteigentumsanteilen. "
    transcripts = {
        (key, number): [ReadingBlock(kind="TEXT", text=((f"Teilungserklärung {number} " if number == 1 else f"Seite {number} ") + filler * 40)[:DENSE_PAGE_CHARS])]
        for number in range(1, pages + 1)
    }
    reader = _WorstCaseReading(worst_case_estimate, transcripts)
    declaration = _DenseDeclaration(key, worst_case_estimate)
    extraction = ModelRecording(declaration, EXTRACTION_MODEL)

    await DeclarationImportWorker(_pipeline(extraction, reader, store, preflight=_FlatRenderPreflight()))._run_claimed(lease)

    item = await _item(lease)
    assert item.error_code is None
    assert item.status == "NEEDS_REVIEW"
    assert len(reader.reads) == pages
    reading_input, reading_output = pages * READ_PAGE_USAGE.input_tokens, pages * READ_PAGE_USAGE.output_tokens
    # Every reservation settled to the reported usage; this import is far past the limits of the Tesseract era
    # (500,000 input, 60,000 output tokens).
    assert item.input_tokens == reading_input + declaration.reported_input
    assert item.output_tokens == reading_output + declaration.reported_output
    assert item.input_tokens > 1_900_000 and item.output_tokens > 450_000


def test_an_import_far_beyond_the_permitted_pages_still_stops_at_the_import_token_limits() -> None:
    ledger = _Ledger(0, 0, Decimal("0"), 0, settings.declaration_max_input_tokens, settings.declaration_max_output_tokens,
                     settings.declaration_max_cost_usd, settings)
    # One page's part of the passes: 3 x 211/200 x 1,500 input tokens plus 12 x 5,600 for prompt and schema, over 200.
    extraction_share = ProviderUsage(5_083, 300, Decimal("0"), 1)
    completed = 0
    with pytest.raises(PipelineFailure) as stopped:
        for _ in range(2 * settings.declaration_ocr_max_pages_per_import):
            for usage in (READ_PAGE_USAGE, extraction_share):
                reservation = ledger.reserve("", reserved_input_tokens=2 * usage.input_tokens, model="unpriced-model")
                ledger.charge_reservation(reservation)
                # A call cannot report more output than its reservation allows (max_output_tokens).
                reported = ProviderUsage(usage.input_tokens, min(usage.output_tokens, reservation.output_tokens), Decimal("0"), 1)
                ledger.settle_reservation(reported, reservation)
            completed += 1
    assert stopped.value.code == "IMPORT_TOKEN_BUDGET_EXCEEDED"
    assert settings.declaration_ocr_max_pages_per_import <= completed < 2 * settings.declaration_ocr_max_pages_per_import


# ---- every document passes the precheck before the first paid reading --------------------------------------------------


async def _add_scan(lease: WorkerLease, store, pdf: bytes, sequence_no: int) -> str:
    """A further document of the import; its key is returned."""
    document_id = uuid.uuid4()
    store._data[f"memory/{document_id}.pdf"] = pdf
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.get(DeclarationImport, lease.import_id)
        session.add(PropertyDocument(
            id=document_id, organization_id=lease.organization_id, doc_type="AMENDMENT", title="Nachtrag",
            original_filename="nachtrag.pdf", mime_type="application/pdf", content_byte_size=len(pdf),
            content_sha256=hashlib.sha256(pdf).hexdigest(), storage_uri=f"memory/{document_id}.pdf",
            storage_backend="TEST", page_count=1, uploaded_by=item.initiated_by))
        await session.flush()
        session.add(DeclarationImportDocument(
            organization_id=lease.organization_id, import_id=lease.import_id, document_id=document_id,
            sequence_no=sequence_no, document_role="AMENDMENT"))
    return str(document_id)


@pytest.mark.parametrize("limit", [
    {"declaration_ocr_max_pages_per_import": 1},
    {"declaration_ocr_max_pixels_per_import": 600_000},  # one 200 x 300 pt page at 180 dpi is 375,000 pixels
], ids=["page-bound", "pixel-bound"])
async def test_a_refusal_of_a_later_document_costs_no_paid_reading_of_an_earlier_one(seed_data, limit) -> None:
    lease, key, store = await _seed_pipeline_import(seed_data, _scan())
    second_key = await _add_scan(lease, store, _scan(), 2)
    extraction = ModelRecording(_RecordingProvider(key), EXTRACTION_MODEL)
    reader = FakeDocumentReadingProvider(transcripts={(key, 1): _text(), (second_key, 1): _text("Nachtrag")})

    await DeclarationImportWorker(_pipeline(
        extraction, reader, store, configured=settings.model_copy(update=limit)))._run_claimed(lease)

    item = await _item(lease)
    code = "OCR_IMPORT_PAGE_LIMIT_EXCEEDED" if "declaration_ocr_max_pages_per_import" in limit else "OCR_IMPORT_PIXEL_LIMIT_EXCEEDED"
    assert (item.status, item.error_code) == ("FAILED", code)
    assert reader.reads == [], "no page was read, so none was paid"
    assert extraction.records == [] and (item.input_tokens, item.output_tokens) == (0, 0)


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


# ---- the cost view: the usage of each stage in the import response ----------------------------------------------


class _RefusingExtraction(_RecordingProvider):
    """Extraction that finishes the inventory, then refuses the core pass after having consumed usage."""

    async def extract_core(self, documents, *, max_output_tokens):
        self.calls.append("extract_core")
        usage = ProviderUsage(10, 3, Decimal("0.000001"), 1)  # within the 13 tokens the test provider reserves
        metadata = _safe_attempt_metadata(pass_id="extract_core", usage=usage, failure_class="PROVIDER_REFUSAL")
        raise ProviderFailure("PROVIDER_REFUSAL", False, "The model refused.", usage=usage, attempt_metadata=(metadata,))


async def _stage_rows(client, lease) -> tuple[dict, list[tuple]]:
    response = await client.get(f"/api/declaration-imports/{lease.import_id}")
    assert response.status_code == 200, response.text
    (run,) = response.json()["runs"]
    return run, [(s["stage"], s["model"], s["usage_status"], s["input_tokens"], s["output_tokens"]) for s in run["stages"]]


async def test_a_finished_scan_reports_reading_and_extraction_apart_and_they_add_up_to_the_run(
    seed_data, client_a_admin, tesseract_unusable
) -> None:
    pdf = (ROOT / "docs/acceptance/data/teilungserklaerung-lindenallee-12.pdf").read_bytes()
    lease, key, store = await _seed_pipeline_import(seed_data, pdf)
    reader = FakeDocumentReadingProvider(transcripts={(key, 1): lindenallee_reading_blocks()})

    await DeclarationImportWorker(_pipeline(ModelRecording(fictional_provider(key), EXTRACTION_MODEL), reader, store))._run_claimed(lease)

    run, rows = await _stage_rows(client_a_admin, lease)
    assert [row[:3] for row in rows] == [("READING", READING_MODEL, "REPORTED"), ("EXTRACTION", EXTRACTION_MODEL, "REPORTED")]
    assert rows[0][3:] == (17, 11), "one page was read"
    assert sum(row[3] for row in rows) == run["input_tokens"] and sum(row[4] for row in rows) == run["output_tokens"]
    assert run["usage_status"] == "REPORTED" and run["unassigned"] is None


async def test_a_job_that_failed_in_reading_still_shows_the_usage_the_failed_call_consumed(
    seed_data, client_a_admin, tesseract_unusable
) -> None:
    lease, key, store = await _seed_pipeline_import(seed_data, _scan())
    usage = ProviderUsage(40, 3, Decimal("0.000002"), 1)
    reader = FakeDocumentReadingProvider(failures={(key, 1): _failure("PROVIDER_REFUSAL", usage=usage)})

    await DeclarationImportWorker(_pipeline(ModelRecording(_RecordingProvider(key), EXTRACTION_MODEL), reader, store))._run_claimed(lease)

    _run, rows = await _stage_rows(client_a_admin, lease)
    assert rows == [("READING", READING_MODEL, "REPORTED", 40, 3)], "nothing was extracted, so there is no extraction block"


async def test_a_job_that_failed_in_extraction_shows_the_finished_reading_and_the_failed_extraction_call(
    seed_data, client_a_admin, tesseract_unusable
) -> None:
    lease, key, store = await _seed_pipeline_import(seed_data, _scan())
    reader = FakeDocumentReadingProvider(transcripts={(key, 1): _text()})

    await DeclarationImportWorker(_pipeline(ModelRecording(_RefusingExtraction(key), EXTRACTION_MODEL), reader, store))._run_claimed(lease)

    assert (await _item(lease)).error_code == "PROVIDER_REFUSAL"
    _run, rows = await _stage_rows(client_a_admin, lease)
    # The inventory (13 + 7 tokens) finished before the core pass refused after 10 + 3 tokens.
    assert rows == [("READING", READING_MODEL, "REPORTED", 17, 11), ("EXTRACTION", EXTRACTION_MODEL, "REPORTED", 23, 10)]


class _CrashingExtraction(_RecordingProvider):
    """Extraction whose first call stops without a structured provider failure: its reservation stays, no entry."""

    async def inventory(self, documents, *, max_output_tokens):
        raise RuntimeError("the call was cancelled")


async def test_the_reservation_of_a_cancelled_extraction_call_is_not_shown_under_reading(
    seed_data, client_a_admin, tesseract_unusable
) -> None:
    lease, key, store = await _seed_pipeline_import(seed_data, _scan())
    reader = FakeDocumentReadingProvider(transcripts={(key, 1): _text()})

    await DeclarationImportWorker(_pipeline(ModelRecording(_CrashingExtraction(key), EXTRACTION_MODEL), reader, store))._run_claimed(lease)

    assert (await _item(lease)).error_code == "PROVIDER_UNEXPECTED_FAILURE"
    run, rows = await _stage_rows(client_a_admin, lease)
    assert rows == [("READING", READING_MODEL, "REPORTED", 17, 11)], "the finished reading, and nothing else is attributed"
    held = run["unassigned"]
    assert held is not None and held["input_tokens"] > 0 and held["output_tokens"] > 0
    assert sum(row[3] for row in rows) + held["input_tokens"] == run["input_tokens"]
    assert sum(row[4] for row in rows) + held["output_tokens"] == run["output_tokens"]


async def test_a_text_layer_document_has_no_reading_block(seed_data, client_a_admin) -> None:
    from tests.declaration_fixtures import make_text_pdf

    lease, key, store = await _seed_pipeline_import(seed_data, make_text_pdf(f"{TEXT}\nEinheit 1 Wohnung im Erdgeschoss"))

    await DeclarationImportWorker(_pipeline(ModelRecording(_RecordingProvider(key), EXTRACTION_MODEL), FakeDocumentReadingProvider(), store))._run_claimed(lease)

    _run, rows = await _stage_rows(client_a_admin, lease)
    assert [row[:2] for row in rows] == [("EXTRACTION", EXTRACTION_MODEL)]


async def test_the_stage_figures_are_part_of_the_organization_isolated_import_response(
    seed_data, client_a_admin, client_b_admin, tesseract_unusable
) -> None:
    lease, key, store = await _seed_pipeline_import(seed_data, _scan())
    await DeclarationImportWorker(_pipeline(ModelRecording(_RecordingProvider(key), EXTRACTION_MODEL),
                                            FakeDocumentReadingProvider(), store))._run_claimed(lease)

    assert (await client_a_admin.get(f"/api/declaration-imports/{lease.import_id}")).json()["runs"][0]["stages"]
    foreign = await client_b_admin.get(f"/api/declaration-imports/{lease.import_id}")
    unknown = await client_b_admin.get(f"/api/declaration-imports/{uuid.uuid4()}")
    assert foreign.status_code == unknown.status_code == 404 and foreign.json() == unknown.json()
