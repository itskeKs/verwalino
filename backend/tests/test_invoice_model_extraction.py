"""The invoice worker routes an unstructured PDF through the organization's two models (issue #500, ADR 0017).

Everything here runs the real queue-fenced worker against the test database with the in-process fake providers: no
provider call is made, and Tesseract is made unusable, because this path has none.
"""
import io
import subprocess
import sys
import types
from decimal import Decimal

import psycopg2
import pytest
from PIL import Image
from sqlalchemy import select, text

from app.core.db import get_worker_session_factory, get_worker_tenant_session
from app.core.storage import LocalFsDocumentStore, set_store
from app.declarations.fake_provider import (
    FakeDocumentReadingProvider,
    FakeInvoiceExtractionProvider,
    synthetic_invoice_output,
)
from app.declarations.invoice_pipeline import INVOICE_MAX_COST_USD, INVOICE_MAX_PAGES, InvoiceProviders
from app.declarations.provider import ProviderFailure, ProviderUsage
from app.declarations.schemas import ReadingBlock
from app.models.invoice_model_call import InvoiceModelCall
from app.repositories.organization_ai_settings_repo import OrganizationAiSettingsRepository
from app.workers.invoice_extraction import process_once
from tests.ai_settings_support import configure_ai
from tests.conftest import MIGRATOR_SYNC_URL
from tests.invoice_model_support import no_invoice_model_data as no_invoice_model_data
from tests.test_invoice_extraction_worker import pdf_bytes, worker_tenant as worker_tenant, zugferd_pdf
from tests.test_m5_einvoice import UBL

EXTRACTION_MODEL, READING_MODEL = "gpt-5.6-luna", "gpt-5.4-mini"
READING_USAGE = ProviderUsage(1000, 200, Decimal("0.00165"), 1)
EXTRACTION_USAGE = ProviderUsage(2000, 100, Decimal("0.00085"), 1)


def scan_pdf(pages: int = 1) -> bytes:
    """A scanned invoice: one A4 image per page and no text layer."""
    images = [Image.new("L", (595, 842), 255) for _ in range(pages)]
    out = io.BytesIO()
    images[0].save(out, "PDF", save_all=True, append_images=images[1:], resolution=72)
    return out.getvalue()


def transcript(page: int, text_: str) -> dict:
    return {("invoice", page): [ReadingBlock(kind="TEXT", text=text_)]}


class Harness:
    """The provider factory the worker is given: it records every construction and serves the two fakes."""

    def __init__(self, reader=None, extractor=None):
        self.reader = reader or FakeDocumentReadingProvider()
        self.extractor = extractor or FakeInvoiceExtractionProvider()
        self.built = []

    def __call__(self, effective):
        self.built.append(effective)
        return InvoiceProviders(reader=self.reader if effective.document_model else None, extractor=self.extractor)


@pytest.fixture(autouse=True)
def leave_no_invoice_model_data(no_invoice_model_data):
    """These tests make metering records and model-read proposals; the migration-chain tests must not meet them."""


@pytest.fixture(autouse=True)
def store(tmp_path):
    set_store(LocalFsDocumentStore(str(tmp_path)))
    yield
    set_store(None)


@pytest.fixture(autouse=True)
def tesseract_unusable(monkeypatch):
    """Any pytesseract use or tesseract process raises: the invoice path must not need either."""
    blocked = types.ModuleType("pytesseract")

    def refuse(name):
        raise AssertionError(f"pytesseract.{name} was used")

    blocked.__getattr__ = refuse
    monkeypatch.setitem(sys.modules, "pytesseract", blocked)
    real_popen = subprocess.Popen

    class GuardedPopen(real_popen):
        def __init__(self, args, *rest, **options):
            argv = args if isinstance(args, (list, tuple)) else [args]
            if any("tesseract" in str(part).lower() for part in argv):
                raise AssertionError("a tesseract process was started")
            super().__init__(args, *rest, **options)

    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)


def ai(org, **updates):
    configure_ai(org, **{"model": EXTRACTION_MODEL, "document_model": READING_MODEL, "import_enabled": True, "monthly_limit_usd": None, **updates})


async def upload(api, data, name="invoice.pdf", mime="application/pdf"):
    response = await api.post("/api/invoice-documents/upload", files={"file": (name, data, mime)})
    assert response.status_code == 201, response.text
    return response.json()


async def process(org, harness):
    assert await process_once(get_worker_session_factory(), org, providers=harness)


async def state(api, doc):
    document = (await api.get(f"/api/invoice-documents/{doc['id']}")).json()
    attempts = (await api.get(f"/api/invoice-documents/{doc['id']}/processing-attempts")).json()
    return document, attempts


def no_proposal(document):
    """The stub every upload starts with: a stored proposal carries the id of the attempt that made it."""
    payload = document["extraction_payload"] or {}
    return "attempt_id" not in payload and not any(payload.get(name) for name in (
        "vendor_name", "invoice_date", "document_number", "gross_amount", "vat_amount"))


async def ledger(org):
    async with get_worker_tenant_session(org) as db:
        return list((await db.scalars(select(InvoiceModelCall).order_by(InvoiceModelCall.created_at, InvoiceModelCall.id))).all())


async def monthly(org):
    async with get_worker_tenant_session(org) as db:
        return await OrganizationAiSettingsRepository(db, org).monthly_usage()


def sql(query, *params):
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall() if cursor.description else None


# ---- the two stages -----------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_unstructured_scan_goes_through_both_stages_with_the_two_configured_models(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    reader = FakeDocumentReadingProvider(transcripts={
        **transcript(1, "Mustermann Versicherungen AG Rechnung RE-2026-0042"), **transcript(2, "Gesamtbetrag 312,00 EUR")})
    harness = Harness(reader=reader)
    doc = await upload(api, scan_pdf(2))
    await process(org, harness)
    # The reading model read each page in order; the extraction model saw both transcripts and the seeded categories.
    assert [(read.model, read.pdf_page) for read in reader.reads] == [(READING_MODEL, 1), (READING_MODEL, 2)]
    assert len(harness.extractor.calls) == 1
    call = harness.extractor.calls[0]
    assert call.model == EXTRACTION_MODEL and call.pages == (1, 2)
    assert "Rechnung RE-2026-0042" in call.text and "Gesamtbetrag 312,00 EUR" in call.text
    assert "GEBAEUDEVERSICHERUNG" in call.categories
    document, attempts = await state(api, doc)
    assert document["processing_status"] == "succeeded"
    payload = document["extraction_payload"]
    assert (payload["gross_amount"], payload["invoice_date"], payload["vendor_name"]) == ("312.00", "2026-01-15", "Mustermann Versicherungen AG")
    assert payload["suggested_category_code"] == "GEBAEUDEVERSICHERUNG"
    assert payload["source_pages"] == [1, 2] and payload["is_einvoice"] is False
    assert "Rechnung RE-2026-0042" in payload["raw_text"] and "Gesamtbetrag 312,00 EUR" in payload["raw_text"]
    assert payload["processor_version"] == "invoice-model-v1"
    assert [(a["status"], a["error_code"], a["processor_version"]) for a in attempts] == [("succeeded", None, "invoice-model-v1")]


@pytest.mark.asyncio
async def test_the_proposal_changes_no_cost_entry_and_keeps_the_original_and_its_evidence(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    original = scan_pdf()
    doc = await upload(api, original)
    await process(org, Harness())
    document, _ = await state(api, doc)
    assert document["cost_entry_id"] is None and document["extraction_payload"]["gross_amount"] == "312.00"
    assert (await api.get(f"/api/invoice-documents/{doc['id']}/content")).content == original
    assert sql("SELECT count(*) FROM cost_entry WHERE organization_id=%s", str(org)) == [(0,)]
    revisions = sql("""SELECT r.processor_version, r.status, count(e.id) FROM extraction_revision r
        LEFT JOIN field_evidence e ON e.extraction_revision_id=r.id AND e.organization_id=r.organization_id
        WHERE r.organization_id=%s GROUP BY r.id""", str(org))
    assert revisions == [("invoice-model-v1", "succeeded", 7)]  # the seven fields the synthetic invoice states


@pytest.mark.asyncio
async def test_a_page_with_a_text_layer_is_not_read_by_the_model_and_needs_no_reading_model(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org, document_model=None)
    harness = Harness()
    doc = await upload(api, pdf_bytes())
    await process(org, harness)
    assert harness.reader.reads == []
    assert [call.model for call in harness.extractor.calls] == [EXTRACTION_MODEL]
    assert "Gesamtbetrag: 119,00 EUR" in harness.extractor.calls[0].text
    document, _ = await state(api, doc)
    assert document["processing_status"] == "succeeded"
    assert [(row.stage, row.pdf_page, row.model_name) for row in await ledger(org)] == [("extract_invoice", None, EXTRACTION_MODEL)]


# ---- structured invoices never reach a model ----------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("data,name,mime,expected", [
    (UBL, "xrechnung.xml", "application/xml", "XRECHNUNG_UBL"),
    (zugferd_pdf(), "zugferd.pdf", "application/pdf", "ZUGFERD"),
], ids=["xrechnung", "zugferd"])
async def test_a_structured_invoice_is_parsed_directly_and_makes_no_provider_call(worker_tenant, data, name, mime, expected):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    harness = Harness()
    doc = await upload(api, data, name, mime)
    await process(org, harness)
    assert harness.built == [] and harness.reader.reads == [] and harness.extractor.calls == []
    document, attempts = await state(api, doc)
    assert document["processing_status"] == "succeeded"
    assert document["extraction_payload"]["is_einvoice"] is True and document["extraction_payload"]["source_format"] == expected
    assert [(a["status"], a["processor_version"]) for a in attempts] == [("succeeded", "invoice-local-v1")]
    assert await ledger(org) == [] and await monthly(org) == 0


# ---- without the organization's opt-in nothing leaves the host ----------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("setup,expected", [
    (None, "ai_import_disabled"),
    ({"import_enabled": False}, "ai_import_disabled"),
    ({"confirm": False}, "ai_import_disabled"),
    ({"document_model": None}, "ai_document_model_missing"),
], ids=["no-settings", "switch-off", "key-unconfirmed", "no-reading-model"])
async def test_without_the_organizations_opt_in_no_provider_is_built_and_the_original_stays(worker_tenant, setup, expected):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    if setup is not None:
        ai(org, **setup)
    harness = Harness()
    original = scan_pdf()
    doc = await upload(api, original)
    await process(org, harness)
    assert harness.built == [] and harness.reader.reads == [] and harness.extractor.calls == []
    document, attempts = await state(api, doc)
    assert (document["processing_status"], document["processing_error_code"]) == ("unavailable", expected)
    assert no_proposal(document) and document["cost_entry_id"] is None
    # A refusal is a local decision: only a run of the models carries the model processor version.
    assert [(a["status"], a["error_code"], a["processor_version"]) for a in attempts] == [("unavailable", expected, "invoice-local-v1")]
    assert (await api.get(f"/api/invoice-documents/{doc['id']}/content")).content == original
    assert await ledger(org) == [] and await monthly(org) == 0


@pytest.mark.asyncio
async def test_an_unreadable_stored_key_ends_the_attempt_without_a_provider(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    sql("UPDATE organization_ai_settings SET encrypted_api_key='not-a-fernet-token' WHERE organization_id=%s", str(org))
    harness = Harness()
    doc = await upload(api, scan_pdf())
    await process(org, harness)
    document, _ = await state(api, doc)
    assert (document["processing_status"], document["processing_error_code"]) == ("unavailable", "ai_key_unreadable")
    assert harness.built == []


# ---- both stages are reserved, settled and counted ----------------------------------------------------------

class ObservingReader(FakeDocumentReadingProvider):
    """Records what the database holds for the attempt at the moment a page is read."""

    def __init__(self, org):
        super().__init__(usage=READING_USAGE)
        self.org, self.during = org, []

    async def read_page(self, page, *, model, max_output_tokens):
        self.during.append(sql("""SELECT c.stage, c.status, c.model_name, a.lease_until > now() + interval '170 seconds'
            FROM invoice_model_call c JOIN invoice_processing_attempt a ON a.organization_id=c.organization_id AND a.id=c.attempt_id
            WHERE c.organization_id=%s ORDER BY c.created_at""", str(self.org)))
        return await super().read_page(page, model=model, max_output_tokens=max_output_tokens)


@pytest.mark.asyncio
async def test_both_stages_are_reserved_before_the_call_settled_after_it_and_count_towards_the_monthly_total(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org, monthly_limit_usd=Decimal("5"))
    reader = ObservingReader(org)
    harness = Harness(reader=reader, extractor=FakeInvoiceExtractionProvider(usage=EXTRACTION_USAGE))
    doc = await upload(api, scan_pdf())
    await process(org, harness)
    # The page was reserved, under a lease that covers the request, before it was read.
    assert reader.during == [[("read_page", "RESERVED", READING_MODEL, True)]]
    rows = await ledger(org)
    assert [(row.stage, row.pdf_page, row.model_name, row.status) for row in rows] == [
        ("read_page", 1, READING_MODEL, "SETTLED"), ("extract_invoice", None, EXTRACTION_MODEL, "SETTLED")]
    assert [(row.input_tokens, row.output_tokens, row.cost_usd, row.usage_complete, row.error_code) for row in rows] == [
        (1000, 200, Decimal("0.00165"), True, None), (2000, 100, Decimal("0.00085"), True, None)]
    assert all(row.reserved_cost_usd >= row.cost_usd and row.settled_at is not None for row in rows)
    assert await monthly(org) == Decimal("0.0025")
    assert (await state(api, doc))[0]["processing_status"] == "succeeded"


@pytest.mark.asyncio
async def test_the_monthly_limit_is_checked_before_the_request_and_nothing_is_sent_over_it(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org, monthly_limit_usd=Decimal("0.01"))
    harness = Harness()
    doc = await upload(api, scan_pdf())
    await process(org, harness)
    assert harness.reader.reads == [] and harness.extractor.calls == []
    document, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("unavailable", "organization_ai_budget_exceeded")]
    assert no_proposal(document)
    assert await ledger(org) == [] and await monthly(org) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,consumed,complete", [
    (ProviderFailure("PROVIDER_TIMEOUT", False, "timed out", usage=ProviderUsage(300, 0, Decimal("0.000225"), 1)), (300, 0, Decimal("0.000225")), True),
    (ProviderFailure("OPENAI_IMAGE_INPUT_INVALID", False, "never left"), (0, 0, Decimal("0")), True),
    (ProviderFailure("PROVIDER_CONNECTION_ERROR", True, "usage unknown", usage_complete=False, attempt_count=1), None, False),
], ids=["usage-reported", "never-left", "usage-unknown"])
async def test_a_failed_request_keeps_its_usage_and_ends_without_a_proposal(worker_tenant, failure, consumed, complete):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    harness = Harness(reader=FakeDocumentReadingProvider(failures={("invoice", 1): failure}))
    original = scan_pdf()
    doc = await upload(api, original)
    await process(org, harness)
    assert harness.extractor.calls == []
    document, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"], a["processor_version"]) for a in attempts] == [("failed", "model_reading_failed", "invoice-model-v1")]
    assert no_proposal(document) and document["cost_entry_id"] is None
    assert (await api.get(f"/api/invoice-documents/{doc['id']}/content")).content == original
    (row,) = await ledger(org)
    assert (row.status, row.error_code, row.usage_complete) == ("SETTLED", failure.code, complete)
    if consumed is None:
        # The provider could not report what was used: the reservation stands in for it.
        consumed = (row.reserved_input_tokens, row.reserved_output_tokens, row.reserved_cost_usd)
        assert row.reserved_cost_usd > 0
    assert (row.input_tokens, row.output_tokens, row.cost_usd) == consumed
    assert await monthly(org) == row.cost_usd


@pytest.mark.asyncio
async def test_a_failed_extraction_after_reading_keeps_both_stages_usage(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    extractor = FakeInvoiceExtractionProvider(failure=ProviderFailure("PROVIDER_SCHEMA_FAILURE", False, "invalid", usage=EXTRACTION_USAGE))
    harness = Harness(reader=FakeDocumentReadingProvider(usage=READING_USAGE), extractor=extractor)
    doc = await upload(api, scan_pdf())
    await process(org, harness)
    _, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("failed", "model_extraction_failed")]
    rows = await ledger(org)
    assert [(row.stage, row.status, row.error_code) for row in rows] == [("read_page", "SETTLED", None), ("extract_invoice", "SETTLED", "PROVIDER_SCHEMA_FAILURE")]
    assert await monthly(org) == Decimal("0.0025")


@pytest.mark.asyncio
async def test_an_invoice_over_the_page_bound_is_refused_before_any_request(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    harness = Harness()
    doc = await upload(api, scan_pdf(INVOICE_MAX_PAGES + 1))
    await process(org, harness)
    assert harness.built == [] and harness.reader.reads == []
    document, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("needs_review", "invoice_page_limit")]
    assert no_proposal(document)


@pytest.mark.asyncio
async def test_an_attempt_stops_at_the_per_invoice_cost_bound(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    # Each page costs a fifth of the bound and the reservation of the next request no longer fits under it.
    expensive = ProviderUsage(40_000, 200, INVOICE_MAX_COST_USD / 5, 1)
    harness = Harness(reader=FakeDocumentReadingProvider(usage=expensive))
    doc = await upload(api, scan_pdf(INVOICE_MAX_PAGES))
    await process(org, harness)
    _, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("needs_review", "invoice_ai_limit")]
    assert harness.extractor.calls == []
    assert 0 < len(harness.reader.reads) < INVOICE_MAX_PAGES
    assert sum((row.cost_usd for row in await ledger(org)), Decimal(0)) <= INVOICE_MAX_COST_USD


# ---- the typed result ---------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_category_that_is_not_seeded_is_dropped_and_missing_amounts_need_review(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    extractor = FakeInvoiceExtractionProvider(output=synthetic_invoice_output(
        suggested_category_code=("MADE_UP_CODE", 0.9), gross_amount=(None, 0)))
    doc = await upload(api, scan_pdf())
    await process(org, Harness(extractor=extractor))
    document, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("needs_review", "missing_fields")]
    payload = document["extraction_payload"]
    assert payload["suggested_category_code"] is None and payload["gross_amount"] is None
    assert payload["field_confidences"]["suggested_category_code"] == 0 and payload["field_confidences"]["gross_amount"] == 0
    assert payload["field_confidences"]["invoice_date"] == 0.97 and 0 < payload["confidence"] < 1


# ---- organization isolation ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_another_organization_sees_nothing_of_the_ledger_and_its_usage(worker_tenant, seed_data):
    api, ids = worker_tenant
    org, other = ids["org_a_id"], seed_data["org_b_id"]
    ai(org, monthly_limit_usd=Decimal("5"))
    ai(other, monthly_limit_usd=Decimal("5"))
    harness = Harness(reader=FakeDocumentReadingProvider(usage=READING_USAGE), extractor=FakeInvoiceExtractionProvider(usage=EXTRACTION_USAGE))
    await upload(api, scan_pdf())
    await process(org, harness)
    assert len(await ledger(org)) == 2 and await monthly(org) == Decimal("0.0025")
    assert await ledger(other) == [] and await monthly(other) == 0
    attempt = sql("SELECT id FROM invoice_processing_attempt WHERE organization_id=%s", str(org))[0][0]
    # Even asked for by the other organization's context, the rows of this attempt stay invisible and unwritable;
    # and even the owner cannot hang a call of the other organization on this attempt (the composite foreign key).
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        sql("""INSERT INTO invoice_model_call(organization_id,attempt_id,stage,pdf_page,model_name,reserved_input_tokens,
            reserved_output_tokens,reserved_cost_usd) VALUES (%s,%s,'read_page',1,'m',1,1,0)""", str(other), str(attempt))
    async with get_worker_tenant_session(other) as db:
        assert (await db.scalars(select(InvoiceModelCall).where(InvoiceModelCall.attempt_id == attempt))).all() == []
        with pytest.raises(Exception):
            await db.execute(text("""INSERT INTO invoice_model_call(organization_id,attempt_id,stage,pdf_page,model_name,reserved_input_tokens,
                reserved_output_tokens,reserved_cost_usd) VALUES (:org,:attempt,'read_page',1,'m',1,1,0)"""), {"org": org, "attempt": attempt})
