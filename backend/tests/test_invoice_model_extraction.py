"""The invoice worker routes an unstructured PDF through the organization's two models (issue #500, ADR 0017).

Everything here runs the real queue-fenced worker against the test database with the in-process fake providers: no
provider call is made, and Tesseract is made unusable, because this path has none.
"""
import asyncio
import io
import json
import subprocess
import sys
import types
import uuid
from decimal import Decimal

import psycopg2
import pytest
from PIL import Image
from sqlalchemy import select, text

from app.core.db import get_worker_session_factory, get_worker_tenant_session
from app.core.invoice_pdf import inspect_invoice
from app.core.storage import LocalFsDocumentStore, set_store
from app.declarations.fake_provider import (
    FakeDocumentReadingProvider,
    FakeInvoiceExtractionProvider,
    synthetic_invoice_output,
)
from app.declarations.invoice_pipeline import INVOICE_MAX_COST_USD, INVOICE_MAX_PAGES, INVOICE_MAX_TEXT_CHARS, InvoiceProviders
from app.declarations.provider import ProviderFailure, ProviderUsage
from app.declarations.schemas import ReadingBlock
from app.models.invoice_model_call import InvoiceModelCall
from app.repositories.organization_ai_settings_repo import OrganizationAiSettingsRepository
from app.workers.invoice_extraction import process_once
from tests.ai_settings_support import configure_ai
from tests.conftest import MIGRATOR_SYNC_URL
from tests.invoice_model_support import no_invoice_model_data as no_invoice_model_data
from tests.test_invoice_extraction_provider import TRANSPORTS
from tests.test_invoice_extraction_worker import pdf_bytes, worker_tenant as worker_tenant, zugferd_pdf
from tests.test_m5_einvoice import CII_WITH_IBAN, PAYEE_IBAN, UBL_WITH_IBAN

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


async def usage(api, doc):
    response = await api.get(f"/api/invoice-documents/{doc['id']}/model-usage")
    assert response.status_code == 200, response.text
    return response.json()


class DuringRead(FakeDocumentReadingProvider):
    """Runs an action (sync or async) once, while the first page is being read: what happens to an invoice mid-run."""

    def __init__(self, action, **options):
        super().__init__(**options)
        self.action = action

    async def read_page(self, page, *, model, max_output_tokens):
        result = await super().read_page(page, model=model, max_output_tokens=max_output_tokens)
        if page.pdf_page == 1:
            done = self.action()
            if hasattr(done, "__await__"):
                await done
        return result


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
    assert (payload["vendor_iban"], payload["field_confidences"]["vendor_iban"]) == ("DE89370400440532013000", 0.9)
    assert payload["source_pages"] == [1, 2] and payload["is_einvoice"] is False
    assert "Rechnung RE-2026-0042" in payload["raw_text"] and "Gesamtbetrag 312,00 EUR" in payload["raw_text"]
    assert payload["processor_version"] == "invoice-model-v1"
    assert [(a["status"], a["error_code"], a["processor_version"]) for a in attempts] == [("succeeded", None, "invoice-model-v1")]


PRINTED_IBAN = "DE02 1203 0000 0000 2020 51"  # the insurer's account of the journey's bank statement, as an invoice prints it


def _answer(iban_as_read: str | None) -> dict:
    """What a model answers for the synthetic invoice: its nine fields and the IBAN exactly as it read it."""
    answer = json.loads(synthetic_invoice_output(vendor_name=("Musterversicherung AG", 0.95)).model_dump_json())
    answer["vendor_iban"] = {"value": iban_as_read, "confidence": 0.9 if iban_as_read else 0}
    return answer


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_name", sorted(TRANSPORTS))
async def test_the_vendor_iban_of_a_scan_ends_normalised_in_the_stored_proposal_through_every_transport(worker_tenant, transport_name):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    reader = FakeDocumentReadingProvider(transcripts=transcript(1, f"Musterversicherung AG Rechnung RE-2026-0042 Gesamtbetrag 312,00 EUR IBAN {PRINTED_IBAN}"))
    transport = TRANSPORTS[transport_name](transport_name)
    # The model reads the IBAN in lower case as it stands on the page; the transport parses and validates its answer.
    harness = Harness(reader=reader, extractor=transport.build([_answer(PRINTED_IBAN.lower())]))
    doc = await upload(api, scan_pdf())
    await process(org, harness)
    assert transport.count() == 1 and PRINTED_IBAN in transport.seen()["text"]
    document, _ = await state(api, doc)
    assert document["processing_status"] == "succeeded", document["processing_error_code"]
    payload = document["extraction_payload"]
    assert payload["vendor_iban"] == "DE02120300000000202051" and payload["field_confidences"]["vendor_iban"] == 0.9
    # The IBAN is shown for review and weighs nothing: the overall certainty is that of the nine fields.
    assert payload["confidence"] == 0.8405
    assert payload["gross_amount"] == "312.00" and document["cost_entry_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_name", sorted(TRANSPORTS))
async def test_a_wrong_vendor_iban_is_stored_as_not_stated_and_the_proposal_still_succeeds(worker_tenant, transport_name):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    transport = TRANSPORTS[transport_name](transport_name)
    harness = Harness(extractor=transport.build([_answer("DE02 1203 0000 0000 2020 52")]))
    doc = await upload(api, scan_pdf())
    await process(org, harness)
    assert transport.count() == 1, "a wrong IBAN is no reason for a second, paid request"
    document, _ = await state(api, doc)
    assert document["processing_status"] == "succeeded"
    payload = document["extraction_payload"]
    assert payload["vendor_iban"] is None and payload["field_confidences"]["vendor_iban"] == 0
    assert payload["gross_amount"] == "312.00" and payload["vendor_name"] == "Musterversicherung AG"


@pytest.mark.asyncio
async def test_an_invoice_that_states_no_iban_proposes_none(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    harness = Harness(extractor=FakeInvoiceExtractionProvider(output=synthetic_invoice_output(vendor_iban=(None, 0))))
    doc = await upload(api, scan_pdf())
    await process(org, harness)
    document, _ = await state(api, doc)
    assert document["processing_status"] == "succeeded" and document["extraction_payload"]["vendor_iban"] is None


@pytest.mark.asyncio
async def test_the_database_refuses_a_proposal_whose_vendor_iban_is_not_shaped_like_one(worker_tenant, monkeypatch):
    """The boundary check of the worker comes first; this is the second line, in the function that records the revision."""
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    monkeypatch.setattr("app.core.extraction_evidence.valid_invoice_outcome", lambda result: True)

    def processor(data, mime):
        result = inspect_invoice(data, mime)
        result["payload"]["vendor_iban"] = "kein IBAN"
        return result
    await upload(api, UBL_WITH_IBAN, "xrechnung.xml", "application/xml")
    with pytest.raises(Exception, match="Invalid extraction proposal schema"):
        await process_once(get_worker_session_factory(), org, providers=Harness(), processor=processor)


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
    (UBL_WITH_IBAN, "xrechnung.xml", "application/xml", "XRECHNUNG_UBL"),
    (zugferd_pdf(CII_WITH_IBAN), "zugferd.pdf", "application/pdf", "ZUGFERD"),
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
    # The payee's IBAN comes from the payment data, without any model; its certainty is that of the other structured fields.
    assert document["extraction_payload"]["vendor_iban"] == PAYEE_IBAN and document["extraction_payload"]["field_confidences"]["vendor_iban"] == 0.99
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
async def test_a_confirmation_from_before_invoices_were_covered_sends_no_invoice_until_it_is_renewed(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org, text_version="1")
    harness = Harness()
    original = scan_pdf()
    doc = await upload(api, original)
    await process(org, harness)
    assert harness.built == [] and harness.reader.reads == [] and harness.extractor.calls == []
    document, attempts = await state(api, doc)
    assert (document["processing_status"], document["processing_error_code"]) == ("unavailable", "ai_invoice_confirmation_missing")
    assert [(a["status"], a["error_code"], a["processor_version"]) for a in attempts] == [("unavailable", "ai_invoice_confirmation_missing", "invoice-local-v1")]
    assert no_proposal(document) and (await api.get(f"/api/invoice-documents/{doc['id']}/content")).content == original
    assert await ledger(org) == [] and await monthly(org) == 0
    # The organization confirms the current text; the invoice is then read when it is started again.
    sql("""INSERT INTO organization_ai_confirmation(organization_id,confirmed_by_user_id,provider,base_url,text_version)
        VALUES (%s,%s,'openai',NULL,'2')""", str(org), str(ids["user_a_admin_id"]))
    assert (await api.post(f"/api/invoice-documents/{doc['id']}/retry")).status_code == 200
    await process(org, harness)
    assert (await state(api, doc))[0]["processing_status"] == "succeeded" and len(harness.reader.reads) == 1


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


# ---- what the invoice shows: the usage of both stages, failed and cancelled attempts included ----------------

@pytest.mark.asyncio
async def test_the_invoice_shows_the_usage_of_both_stages_and_nothing_before_a_call(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    doc = await upload(api, scan_pdf())
    assert await usage(api, doc) == {"stages": [], "total": None}
    await process(org, Harness(reader=FakeDocumentReadingProvider(usage=READING_USAGE), extractor=FakeInvoiceExtractionProvider(usage=EXTRACTION_USAGE)))
    shown = await usage(api, doc)
    reading, extraction = shown["stages"]
    assert (reading["stage"], reading["model"], reading["calls"], reading["usage_status"]) == ("READING", READING_MODEL, 1, "REPORTED")
    assert (reading["reported_input_tokens"], reading["reported_output_tokens"], Decimal(reading["estimated_cost_usd"])) == (1000, 200, Decimal("0.00165"))
    assert (extraction["stage"], extraction["model"], extraction["usage_status"]) == ("EXTRACTION", EXTRACTION_MODEL, "REPORTED")
    assert (extraction["input_tokens"], extraction["output_tokens"], Decimal(extraction["estimated_cost_usd"])) == (2000, 100, Decimal("0.00085"))
    assert (shown["total"]["input_tokens"], shown["total"]["output_tokens"], Decimal(shown["total"]["estimated_cost_usd"])) == (3000, 300, Decimal("0.0025"))
    assert await monthly(org) == Decimal("0.0025")


@pytest.mark.asyncio
async def test_usage_the_provider_could_not_report_is_shown_as_its_reservation_and_not_as_zero(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    failure = ProviderFailure("PROVIDER_CONNECTION_ERROR", True, "usage unknown", usage_complete=False, attempt_count=1)
    doc = await upload(api, scan_pdf())
    await process(org, Harness(reader=FakeDocumentReadingProvider(failures={("invoice", 1): failure})))
    (stage,) = (await usage(api, doc))["stages"]
    (row,) = await ledger(org)
    assert (stage["stage"], stage["usage_status"], stage["reported_input_tokens"], stage["reported_output_tokens"]) == ("READING", "RESERVED", None, None)
    assert (stage["input_tokens"], stage["output_tokens"]) == (row.reserved_input_tokens, row.reserved_output_tokens)
    assert row.reserved_cost_usd > 0
    assert Decimal(stage["estimated_cost_usd"]) == row.reserved_cost_usd.quantize(Decimal("0.000001"))
    assert await monthly(org) == row.reserved_cost_usd


@pytest.mark.asyncio
async def test_a_model_without_a_price_has_usage_but_no_cost(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org, provider="openai_compatible", base_url="https://compatible.example/v1", model="local-extraction-model", document_model="local-vision-model")
    doc = await upload(api, scan_pdf())
    harness = Harness(reader=FakeDocumentReadingProvider(usage=ProviderUsage(1000, 200, Decimal("0"), 1)),
                      extractor=FakeInvoiceExtractionProvider(usage=ProviderUsage(2000, 100, Decimal("0"), 1)))
    await process(org, harness)
    shown = await usage(api, doc)
    assert [(stage["stage"], stage["usage_status"], stage["estimated_cost_usd"]) for stage in shown["stages"]] == [("READING", "REPORTED", None), ("EXTRACTION", "REPORTED", None)]
    assert (shown["total"]["input_tokens"], shown["total"]["output_tokens"], shown["total"]["estimated_cost_usd"]) == (3000, 300, None)


@pytest.mark.asyncio
async def test_only_the_invoices_own_organization_can_read_its_usage(worker_tenant, seed_data, client_b_admin):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    doc = await upload(api, scan_pdf())
    await process(org, Harness())
    assert (await usage(api, doc))["total"] is not None
    assert (await client_b_admin.get(f"/api/invoice-documents/{doc['id']}/model-usage")).status_code == 404
    assert (await client_b_admin.get(f"/api/invoice-documents/{uuid.uuid4()}/model-usage")).status_code == 404


# ---- when something goes wrong: no proposal, the original stays, what was used is recorded and visible -------

def lower_monthly_limit(org, limit):
    return lambda: sql("UPDATE organization_ai_settings SET monthly_limit_usd=%s WHERE organization_id=%s", limit, str(org))


@pytest.mark.asyncio
async def test_a_monthly_limit_reached_between_the_stages_stops_the_extraction_and_keeps_the_reading(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org, monthly_limit_usd=Decimal("5"))
    # The reading consumes three-quarters of a cent. The administrator lowers the limit to one cent while the page is
    # being read: the extraction request, reserved at 0.4 cent on top of what is used, no longer fits.
    reader = DuringRead(lower_monthly_limit(org, Decimal("0.01")), usage=ProviderUsage(1000, 1500, Decimal("0.0075"), 1))
    harness = Harness(reader=reader)
    original = scan_pdf()
    doc = await upload(api, original)
    await process(org, harness)
    assert [read.pdf_page for read in reader.reads] == [1] and harness.extractor.calls == []
    document, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("unavailable", "organization_ai_budget_exceeded")]
    assert (document["processing_status"], document["processing_error_code"]) == ("unavailable", "organization_ai_budget_exceeded")
    assert no_proposal(document) and document["cost_entry_id"] is None
    assert (await api.get(f"/api/invoice-documents/{doc['id']}/content")).content == original
    assert [(row.stage, row.status) for row in await ledger(org)] == [("read_page", "SETTLED")]
    assert await monthly(org) == Decimal("0.0075")
    shown = await usage(api, doc)
    assert [(stage["stage"], stage["usage_status"]) for stage in shown["stages"]] == [("READING", "REPORTED")]
    assert Decimal(shown["total"]["estimated_cost_usd"]) == Decimal("0.0075")


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["reading", "extraction"])
async def test_a_provider_failure_in_either_stage_leaves_no_proposal_and_shows_what_was_used(worker_tenant, stage):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    failure = ProviderFailure("PROVIDER_TIMEOUT", False, "timed out", usage=ProviderUsage(300, 0, Decimal("0.000225"), 1))
    if stage == "reading":
        harness = Harness(reader=FakeDocumentReadingProvider(failures={("invoice", 1): failure}))
        expected_code, expected_cost = "model_reading_failed", Decimal("0.000225")
    else:
        harness = Harness(reader=FakeDocumentReadingProvider(usage=READING_USAGE), extractor=FakeInvoiceExtractionProvider(failure=failure))
        expected_code, expected_cost = "model_extraction_failed", Decimal("0.00165") + Decimal("0.000225")
    original = scan_pdf()
    doc = await upload(api, original)
    await process(org, harness)
    document, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("failed", expected_code)]
    assert (document["processing_status"], document["processing_error_code"]) == ("failed", expected_code)
    assert no_proposal(document) and document["cost_entry_id"] is None
    assert (await api.get(f"/api/invoice-documents/{doc['id']}/content")).content == original
    assert Decimal((await usage(api, doc))["total"]["estimated_cost_usd"]) == expected_cost == await monthly(org)


@pytest.mark.asyncio
async def test_a_replaced_original_cancels_the_attempt_after_the_request_it_made_and_applies_nothing(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    replaced = []

    async def replace_original():
        response = await api.post(f"/api/invoice-documents/{doc['id']}/version", files={"file": ("neu.pdf", scan_pdf(), "application/pdf")})
        assert response.status_code == 201, response.text
        replaced.append(response.json())

    harness = Harness(reader=DuringRead(replace_original, usage=READING_USAGE))
    original = scan_pdf()
    doc = await upload(api, original)
    await process(org, harness)
    assert harness.extractor.calls == []
    document, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("canceled", "source_no_longer_editable")]
    assert (document["processing_status"], document["processing_error_code"], document["status"]) == ("canceled", "source_no_longer_editable", "superseded")
    assert no_proposal(document)
    assert (await api.get(f"/api/invoice-documents/{doc['id']}/content")).content == original
    assert [row.status for row in await ledger(org)] == ["SETTLED"]
    assert Decimal((await usage(api, doc))["total"]["estimated_cost_usd"]) == Decimal("0.00165") == await monthly(org)
    # The replacement is a new original with its own processing; the cancelled one cannot be started again.
    assert (await api.get(f"/api/invoice-documents/{replaced[0]['id']}")).json()["processing_status"] == "queued"
    assert (await api.post(f"/api/invoice-documents/{doc['id']}/retry")).status_code == 409


@pytest.mark.asyncio
async def test_a_user_who_loses_permission_mid_run_cancels_the_attempt_before_the_next_request(worker_tenant):
    api, ids = worker_tenant
    org, user = ids["org_a_id"], ids["user_a_admin_id"]
    ai(org)
    reader = DuringRead(lambda: sql("UPDATE membership SET role='READ_ONLY' WHERE user_id=%s", str(user)), usage=READING_USAGE)
    harness = Harness(reader=reader)
    doc = await upload(api, scan_pdf(2))
    await process(org, harness)
    # The second page and the extraction were never requested.
    assert [read.pdf_page for read in reader.reads] == [1] and harness.extractor.calls == []
    document, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("canceled", "scope_permission_changed")]
    assert (document["processing_status"], document["processing_error_code"]) == ("canceled", "scope_permission_changed")
    assert no_proposal(document)
    assert Decimal((await usage(api, doc))["total"]["estimated_cost_usd"]) == Decimal("0.00165") == await monthly(org)


def expire_lease(org):
    return lambda: sql("UPDATE invoice_processing_attempt SET lease_until=now()-interval '1 second' WHERE organization_id=%s AND status='running'", str(org))


@pytest.mark.asyncio
async def test_a_lost_lease_applies_nothing_and_every_further_attempt_is_metered_up_to_the_cap(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    reader = DuringRead(expire_lease(org), usage=READING_USAGE)
    harness = Harness(reader=reader)
    original = scan_pdf(2)
    doc = await upload(api, original)
    factory = get_worker_session_factory()
    # Each attempt reads its first page, finds its lease gone before the second request and stops: nothing is
    # applied. The expired claim is taken over by the next attempt, three at most; the fourth claim only ends it.
    assert [await process_once(factory, org, providers=harness) for _ in range(4)] == [True, True, True, False]
    assert [read.pdf_page for read in reader.reads] == [1, 1, 1] and harness.extractor.calls == []
    document, attempts = await state(api, doc)
    assert [(a["attempt_no"], a["status"], a["error_code"]) for a in attempts] == [
        (3, "timeout", "worker_lease_expired"), (2, "timeout", "worker_lease_expired"), (1, "timeout", "worker_lease_expired")]
    assert (document["processing_status"], document["processing_error_code"]) == ("failed", "retry_budget_exhausted")
    assert no_proposal(document)
    assert (await api.get(f"/api/invoice-documents/{doc['id']}/content")).content == original
    assert [row.status for row in await ledger(org)] == ["SETTLED"] * 3
    assert Decimal((await usage(api, doc))["total"]["estimated_cost_usd"]) == Decimal("0.00495") == await monthly(org)


@pytest.mark.asyncio
async def test_a_retry_after_a_failure_is_metered_again_and_the_retries_are_capped(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    failure = ProviderFailure("PROVIDER_TIMEOUT", False, "timed out", usage=ProviderUsage(300, 0, Decimal("0.000225"), 1))
    failing = Harness(reader=FakeDocumentReadingProvider(failures={("invoice", 1): failure}))
    doc = await upload(api, scan_pdf())
    await process(org, failing)
    # Asking again queues the invoice and sends nothing: only the worker reaches a provider, and it meters.
    built = len(failing.built)
    retry = await api.post(f"/api/invoice-documents/{doc['id']}/retry")
    assert (retry.status_code, retry.json()["processing_status"]) == (200, "queued")
    assert len(failing.built) == built and len(failing.reader.reads) == 1 and await ledger(org) != []
    # The retry succeeds: its reading and extraction are reserved and settled like the first attempt's, as their own rows.
    working = Harness(reader=FakeDocumentReadingProvider(usage=READING_USAGE), extractor=FakeInvoiceExtractionProvider(usage=EXTRACTION_USAGE))
    await process(org, working)
    document, attempts = await state(api, doc)
    assert [(a["attempt_no"], a["status"]) for a in attempts] == [(2, "succeeded"), (1, "failed")]
    assert document["processing_status"] == "succeeded" and document["extraction_payload"]["gross_amount"] == "312.00"
    rows = await ledger(org)
    assert [(row.stage, row.status, row.error_code) for row in rows] == [
        ("read_page", "SETTLED", "PROVIDER_TIMEOUT"), ("read_page", "SETTLED", None), ("extract_invoice", "SETTLED", None)]
    assert len({row.attempt_id for row in rows}) == 2
    assert Decimal((await usage(api, doc))["total"]["estimated_cost_usd"]) == Decimal("0.000225") + Decimal("0.0025") == await monthly(org)
    # A document that has used its three attempts cannot be started again, and asking costs nothing.
    second = await upload(api, scan_pdf(), name="second.pdf")
    answers = []
    for _ in range(3):
        await process(org, failing)
        answers.append((await api.post(f"/api/invoice-documents/{second['id']}/retry")).status_code)
    assert answers == [200, 200, 409]
    spent = len(await ledger(org))
    assert (await api.post(f"/api/invoice-documents/{second['id']}/retry")).status_code == 409
    assert await process_once(get_worker_session_factory(), org, providers=failing) is False
    assert len(await ledger(org)) == spent
    assert [a["status"] for a in (await state(api, second))[1]] == ["failed"] * 3


# ---- the opt-in is read again before every request ---------------------------------------------------------

def withdraw(org, how):
    """What an organization can do to its consent while an invoice is being read, as the database holds it."""
    if how == "switch":
        return lambda: sql("UPDATE organization_ai_settings SET import_enabled=false WHERE organization_id=%s", str(org))
    if how == "confirmation":
        # A newer confirmation of the text that does not name invoices (the one an installation without invoice
        # processing records) is the organization's latest word for this destination.
        return lambda: sql("""INSERT INTO organization_ai_confirmation(organization_id,confirmed_by_user_id,provider,base_url,text_version)
            SELECT organization_id, confirmed_by_user_id, provider, base_url, '1' FROM organization_ai_confirmation
            WHERE organization_id=%s LIMIT 1""", str(org))
    if how == "reading-model":
        return lambda: sql("UPDATE organization_ai_settings SET document_model=NULL WHERE organization_id=%s", str(org))
    return lambda: sql("UPDATE organization_ai_settings SET encrypted_api_key='not-a-fernet-token' WHERE organization_id=%s", str(org))


@pytest.mark.asyncio
@pytest.mark.parametrize("how,code", [
    ("switch", "ai_import_disabled"), ("confirmation", "ai_invoice_confirmation_missing"),
    ("reading-model", "ai_document_model_missing"), ("key", "ai_key_unreadable"),
])
async def test_an_opt_in_withdrawn_during_the_run_stops_it_before_the_next_request(worker_tenant, how, code):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    reader = DuringRead(withdraw(org, how), usage=READING_USAGE)
    harness = Harness(reader=reader)
    original = scan_pdf(3)
    doc = await upload(api, original)
    await process(org, harness)
    # The page that was out when the consent went is paid and recorded; no further page and no extraction follows.
    assert [read.pdf_page for read in reader.reads] == [1] and harness.extractor.calls == []
    document, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("unavailable", code)]
    assert (document["processing_status"], document["processing_error_code"]) == ("unavailable", code)
    assert no_proposal(document) and document["cost_entry_id"] is None
    assert (await api.get(f"/api/invoice-documents/{doc['id']}/content")).content == original
    assert [(row.stage, row.pdf_page, row.status) for row in await ledger(org)] == [("read_page", 1, "SETTLED")]
    assert Decimal((await usage(api, doc))["total"]["estimated_cost_usd"]) == Decimal("0.00165") == await monthly(org)


@pytest.mark.asyncio
async def test_a_switch_turned_off_while_the_last_page_is_read_stops_before_the_extraction_request(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    harness = Harness(reader=DuringRead(withdraw(org, "switch"), usage=READING_USAGE))
    doc = await upload(api, scan_pdf())
    await process(org, harness)
    assert len(harness.reader.reads) == 1 and harness.extractor.calls == []
    document, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("unavailable", "ai_import_disabled")]
    assert no_proposal(document) and [row.stage for row in await ledger(org)] == ["read_page"]


# ---- the destination and the models are read again before every request ------------------------------------

# What an administrator can replace while an invoice is being read: the settings a run starts with and what they become,
# each saved as the settings screen would hold it (the key and the confirmation of the destination included), so that
# the organization still allows everything, only not to the destination and models the run was built from.
REPLACEMENTS = {
    "provider": ({}, {"provider": "anthropic"}),
    "base-url": ({"provider": "openai_compatible", "base_url": "https://llm-a.example.test/v1"}, {"base_url": "https://llm-b.example.test/v1"}),
    "key": ({}, {"api_key": "another-synthetic-key"}),
    "extraction-model": ({}, {"model": "gpt-5.6-sol"}),
    "reading-model": ({}, {"document_model": "gpt-5.4"}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("what", list(REPLACEMENTS))
async def test_a_destination_or_model_replaced_during_the_run_stops_it_before_the_next_request(worker_tenant, what):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    started, replacement = REPLACEMENTS[what]
    ai(org, **started)
    reader = DuringRead(lambda: ai(org, **replacement), usage=READING_USAGE)
    harness = Harness(reader=reader)
    original = scan_pdf(3)
    doc = await upload(api, original)
    await process(org, harness)
    # The page that was out when the settings were replaced is paid and recorded; no further page, no extraction request,
    # and the replacement destination never received one either.
    assert [read.pdf_page for read in reader.reads] == [1] and harness.extractor.calls == []
    assert len(harness.built) == 1
    document, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("unavailable", "ai_settings_changed")]
    assert (document["processing_status"], document["processing_error_code"]) == ("unavailable", "ai_settings_changed")
    assert no_proposal(document) and document["cost_entry_id"] is None
    assert (await api.get(f"/api/invoice-documents/{doc['id']}/content")).content == original
    assert [(row.stage, row.pdf_page, row.status) for row in await ledger(org)] == [("read_page", 1, "SETTLED")]
    assert Decimal((await usage(api, doc))["total"]["estimated_cost_usd"]) == Decimal("0.00165") == await monthly(org)


@pytest.mark.asyncio
async def test_saving_the_same_settings_again_during_the_run_does_not_stop_it(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    # The key is encrypted again on every save (a different token for the same key), and the run goes on.
    reader = DuringRead(lambda: ai(org), usage=READING_USAGE)
    harness = Harness(reader=reader, extractor=FakeInvoiceExtractionProvider(usage=EXTRACTION_USAGE))
    doc = await upload(api, scan_pdf(2))
    await process(org, harness)
    assert [read.pdf_page for read in reader.reads] == [1, 2] and len(harness.extractor.calls) == 1
    document, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("succeeded", None)]
    assert document["processing_status"] == "succeeded" and document["extraction_payload"]["gross_amount"] == "312.00"
    assert [row.stage for row in await ledger(org)] == ["read_page", "read_page", "extract_invoice"]


# ---- the text bound stops the reading ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_text_bound_stops_the_reading_before_the_next_page_is_read_and_paid(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    page = "x" * (INVOICE_MAX_TEXT_CHARS // 2 + 1000)
    reader = FakeDocumentReadingProvider(transcripts={key: blocks for number in (1, 2, 3) for key, blocks in transcript(number, page).items()}, usage=READING_USAGE)
    harness = Harness(reader=reader)
    doc = await upload(api, scan_pdf(3))
    await process(org, harness)
    # The second page took the total over the bound; the third was never requested, and nothing was extracted.
    assert [read.pdf_page for read in reader.reads] == [1, 2] and harness.extractor.calls == []
    document, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("needs_review", "invoice_text_limit")]
    assert no_proposal(document)
    assert [(row.stage, row.pdf_page, row.status) for row in await ledger(org)] == [("read_page", 1, "SETTLED"), ("read_page", 2, "SETTLED")]
    assert Decimal((await usage(api, doc))["total"]["estimated_cost_usd"]) == Decimal("0.0033") == await monthly(org)


# ---- a shutdown during a request ---------------------------------------------------------------------------

class HangingReader(FakeDocumentReadingProvider):
    """A request that never answers: the worker is told to stop while it waits."""

    def __init__(self):
        super().__init__(usage=READING_USAGE)
        self.out = asyncio.Event()

    async def read_page(self, page, *, model, max_output_tokens):
        self.out.set()
        await asyncio.sleep(3600)


@pytest.mark.asyncio
async def test_a_shutdown_during_a_request_settles_it_at_its_reservation_and_leaves_the_attempt_to_be_reclaimed(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    reader = HangingReader()
    doc = await upload(api, scan_pdf(2))
    task = asyncio.create_task(process_once(get_worker_session_factory(), org, providers=Harness(reader=reader)))
    await asyncio.wait_for(reader.out.wait(), 60)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Nothing stays RESERVED: the request that was out is booked at its reservation, as any usage that is unknown.
    (row,) = await ledger(org)
    assert (row.status, row.usage_complete, row.error_code) == ("SETTLED", False, "WORKER_CANCELLED") and row.settled_at is not None
    assert (row.input_tokens, row.output_tokens, row.cost_usd) == (row.reserved_input_tokens, row.reserved_output_tokens, row.reserved_cost_usd)
    assert row.reserved_cost_usd > 0 and await monthly(org) == row.reserved_cost_usd
    (stage,) = (await usage(api, doc))["stages"]
    assert stage["usage_status"] == "RESERVED" and stage["reported_input_tokens"] is None
    # The attempt keeps its lease; once it has expired the next claim reads the invoice again, as for any crash.
    assert (await state(api, doc))[1][0]["status"] == "running"
    expire_lease(org)()
    await process(org, Harness(reader=FakeDocumentReadingProvider(usage=READING_USAGE), extractor=FakeInvoiceExtractionProvider(usage=EXTRACTION_USAGE)))
    document, attempts = await state(api, doc)
    assert document["processing_status"] == "succeeded" and [a["attempt_no"] for a in attempts] == [2, 1]
