"""What the inbox's model path records where it found a value, what it cost, and what it leaves behind when it does not
finish (issue #501, ADR 0017).

The real queue-fenced worker against the test database with the in-process fake providers, read through the API a user
meets: the evidence of a proposal, the usage of a logical document over all its attempts, and the state a document is in
when the models were not asked, were asked in part, or failed. Whatever happens, the original stays, nothing is applied,
what was requested is recorded and visible, and the established retry works within its three attempts.
"""
import uuid
from contextlib import closing
from dataclasses import replace
from decimal import Decimal

import psycopg2
import pytest
from sqlalchemy import text

from app.core.db import get_worker_tenant_session
from app.core.intake_extraction import extract_logical
from app.declarations.fake_provider import FakeInvoiceExtractionProvider, synthetic_invoice_output
from app.declarations.provider import PROVIDER_CONNECTION_ERROR, ProviderFailure, ProviderUsage
from app.declarations.schemas import ReadingBlock
from app.repositories.organization_ai_settings_repo import OrganizationAiSettingsRepository
from app.services.ai_settings_service import (
    AI_PROVIDER_CONFIRMATION_VERSION,
    INBOX_AI_CONFIRMATION_MISSING,
    INVOICE_AI_CONFIRMATION_MISSING,
    effective_ai_settings,
    invoice_import_blocker,
)
from tests.conftest import MIGRATOR_SYNC_URL
from tests.intake_model_support import (
    EXTRACTION_MODEL,
    READING_MODEL,
    Harness,
    confirm_partition,
    document_pdf,
    enable_models,
    extract_all,
    ledger,
    tesseract_unusable as tesseract_unusable,
)
from tests.test_financial_scope_boundary import scope_seed as scope_seed, worker_tenant as worker_tenant
from tests.test_intake_batches import work_sql
from tests.test_intake_model_extraction import FIRST, SECOND, Reader, documents, job_state, receive_original, split
from tests.test_invoice_source_custody import fixture, foreign_fixture, store as store
from tests.test_scope_payment_reconciliation import read_value
from tests.test_scoped_account_statements import client, mutate

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("tesseract_unusable")]

OUTAGE = ProviderFailure(PROVIDER_CONNECTION_ERROR, True, "synthetic outage", usage=ProviderUsage(40, 0, Decimal("0.00001"), 1))
UNREPORTED = ProviderFailure(PROVIDER_CONNECTION_ERROR, True, "synthetic outage without a usage report", usage_complete=False)


class PerDocument(FakeInvoiceExtractionProvider):
    """Answers each logical document with its own scripted output (the fake answers every request the same)."""

    def __init__(self, outputs, **options):
        super().__init__(**options)
        self._outputs = outputs

    async def extract_invoice(self, documents, **options):
        answer = await super().extract_invoice(documents, **options)
        output = self._outputs.get(documents[0].document_key)
        return answer if output is None else replace(answer, output=output)


def stated_evidence(ids, document):
    """The evidence rows of the proposal of one logical document: field -> (status, page, quotation, method)."""
    return read_value("""SELECT COALESCE(jsonb_object_agg(field_name, jsonb_build_array(locator_status, page_number, quotation, method)), '{}'::jsonb)
        FROM field_evidence WHERE organization_id=:org AND extraction_revision_id=(
          SELECT id FROM extraction_revision WHERE organization_id=:org AND logical_document_id=:document)""",
                      {**ids, "document": uuid.UUID(document)})


UNLOCATED = ["unlocated", None, None, "unlocated"]


# ---- where the model found a value ------------------------------------------------------------------------------------

async def test_a_field_is_located_where_the_model_found_it_only_when_that_verifies_against_the_documents_own_pages(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([FIRST, SECOND, None, None]), pages=4)
        first, second = await split(api, ref, 1, 2), await split(api, ref, 3, 4)
        whole = (await documents(api, ref))[(1, 4)]
        await confirm_partition(api, ref, first, second)
        reader = Reader(transcripts={
            (second, 3): [ReadingBlock(kind="TEXT", text="Stadtwerke Test GmbH, Abrechnung Wasser, Seite 3")],
            (second, 4): [ReadingBlock(kind="TEXT", text="Gesamtbetrag  99,00 EUR"), ReadingBlock(kind="TEXT", text="Seite 4")]})
        extractor = PerDocument({
            first: synthetic_invoice_output(
                vendor_name=("Mustermann Versicherungen AG", 0.95, 1, "Mustermann Versicherungen AG"),
                document_number=("RE-2026-0042", 0.9, 1, "Rechnungsnummer RE-2026-0042"),
                gross_amount=("312.00", 0.98, 1, "Gesamtbetrag 312,00 EUR"),
                # Page 3 is a page of the other document of this original: never a location of this one.
                invoice_date=("2026-01-15", 0.97, 3, "Stadtwerke Test GmbH, Abrechnung Wasser, Seite 3"),
                # A page of this document, but the passage is not on it.
                service_period_from=("2026-01-01", 0.9, 2, "Leistungszeitraum ab 01.01.2026"),
                # No page at all, and a page that is none of the original's.
                service_period_to=("2026-12-31", 0.9, None, "Mustermann Versicherungen AG"),
                suggested_category_code=("GEBAEUDEVERSICHERUNG", 0.8, 9, "Gebaeudeversicherung 2026")),
            second: synthetic_invoice_output(
                vendor_name=("Stadtwerke Test GmbH", 0.9, 3, "Stadtwerke Test GmbH, Abrechnung Wasser"),
                # The page read as two lines with two spaces: whitespace is compared alike, the passage is kept as the page has it.
                gross_amount=("99.00", 0.9, 4, "Gesamtbetrag 99,00 EUR Seite 4"),
                document_number=("RE-2026-0042", 0.9, 1, "Rechnungsnummer RE-2026-0042"))})
        assert await extract_all(ids["org"], Harness(reader=reader, extractor=extractor), processor=extract_logical) == 3

        assert await stated_evidence(ids, first) == {
            "vendor_name": ["page_quote", 1, "Mustermann Versicherungen AG", "text_layer"],
            "document_number": ["page_quote", 1, "Rechnungsnummer RE-2026-0042", "text_layer"],
            "gross_amount": ["page_quote", 1, "Gesamtbetrag 312,00 EUR", "text_layer"],
            "invoice_date": UNLOCATED, "service_period_from": UNLOCATED, "service_period_to": UNLOCATED,
            "suggested_category_code": UNLOCATED}
        assert await stated_evidence(ids, second) == {
            "vendor_name": ["page_quote", 3, "Stadtwerke Test GmbH, Abrechnung Wasser", "reading_model"],
            "gross_amount": ["page_quote", 4, "Gesamtbetrag  99,00 EUR\nSeite 4", "reading_model"],
            "document_number": UNLOCATED, "invoice_date": UNLOCATED, "service_period_from": UNLOCATED,
            "service_period_to": UNLOCATED, "suggested_category_code": UNLOCATED}
        assert (await job_state(ids, whole))["status"] == "awaiting_split", "the unsplit whole is left out of the division and is not read"
        assert await stated_evidence(ids, whole) == {}

        # What the review screen reads: the original's page numbers, the passage and how the page was read.
        listed = (await api.get("/api/document-refs/" + ref["id"] + "/extractions")).json()["items"]
        by_document = {item["logical_document_id"]: item["id"] for item in listed}
        shown = (await api.get("/api/extraction-revisions/" + by_document[second])).json()
        located = {row["field_name"]: (row["locator_status"], row["page_number"], row["quotation"], row["method"]) for row in shown["fields"]
                   if row["locator_status"] == "page_quote"}
        assert located == {"vendor_name": ("page_quote", 3, "Stadtwerke Test GmbH, Abrechnung Wasser", "reading_model"),
                           "gross_amount": ("page_quote", 4, "Gesamtbetrag  99,00 EUR\nSeite 4", "reading_model")}
        assert shown["payload"]["gross_amount"] == "99.00", "the value is kept whether or not it is located"


async def test_a_passage_on_the_page_that_does_not_carry_the_value_locates_nothing_through_the_worker(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([FIRST, SECOND]), pages=2)
        (document,) = (await documents(api, ref)).values()
        await confirm_partition(api, ref, document)
        extractor = PerDocument({document: synthetic_invoice_output(
            # Real places on page 1 that do not state the values (the three examples of the review of PR #832) ...
            gross_amount=("9999.00", 0.9, 1, "Gesamtbetrag"), document_number=("XYZ-999", 0.9, 1, "Rechnungsnummer"),
            vendor_name=("Mustermann Versicherungen AG", 0.95, 1, "AG"),
            # ... one that states a date in another spelling than the stored one, and one that states another date.
            invoice_date=("2026-01-15", 0.9, 2, "Zahlbar bis 28.02.2026 auf das angegebene Konto"),
            service_period_to=("2026-02-28", 0.9, 2, "Zahlbar bis 28.02.2026 auf das angegebene Konto"),
            vat_amount=("312.00", 0.9, 1, "Gebaeudeversicherung 2026, Gesamtbetrag 312,00 EUR"))})
        assert await extract_all(ids["org"], Harness(extractor=extractor), processor=extract_logical) == 1
        assert await stated_evidence(ids, document) == {
            "gross_amount": UNLOCATED, "document_number": UNLOCATED, "vendor_name": UNLOCATED, "invoice_date": UNLOCATED,
            "service_period_to": ["page_quote", 2, "Zahlbar bis 28.02.2026 auf das angegebene Konto", "text_layer"],
            "vat_amount": ["page_quote", 1, "Gebaeudeversicherung 2026, Gesamtbetrag 312,00 EUR", "text_layer"],
            "service_period_from": UNLOCATED, "suggested_category_code": UNLOCATED}
        assert await read_value("SELECT payload->>'gross_amount' FROM extraction_revision WHERE organization_id=:org AND logical_document_id=:document",
                                {**ids, "document": uuid.UUID(document)}) == "9999.00", "the value is kept whether or not it is located"


async def test_the_assignment_hints_of_a_page_the_reading_model_read_carry_the_originals_page_number_and_how_it_was_read(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([FIRST, None]), pages=2)
        child = (await api.get("/api/logical-documents", params={"document_ref_id": ref["id"]})).json()["items"][0]
        confirmed = await confirm_partition(api, ref, child["id"])
        reader = Reader(transcripts={(child["id"], 2): [ReadingBlock(kind="TEXT", text="Objekt-ID: " + str(ids["property"])),
                                                       ReadingBlock(kind="TEXT", text="Zählernummer: M-123")]})
        assert await extract_all(ids["org"], Harness(reader=reader), processor=extract_logical) == 1
        await mutate(text("INSERT INTO meter(organization_id,property_id,meter_number,meter_type) VALUES(:org,:property,'M-123','STROM')"), ids)
        newest = (await api.get("/api/document-refs/" + ref["id"] + "/extractions", params={"logical_document_id": child["id"]})).json()["items"][0]
        found = await api.get("/api/logical-documents/" + child["id"] + "/assignment-candidates",
                              params={"partition_revision_id": confirmed["id"], "extraction_revision_id": newest["id"]})
        assert found.status_code == 200, found.text
    (row,) = found.json()["items"]
    assert row["scope_id"] == str(ids["scope"]) and row["requires_manual_review"]
    assert [(e["kind"], e["page_number"], e["method"], e["quotation"]) for e in row["evidence"]] == [
        ("property_id", 2, "reading_model", "Objekt-ID: " + str(ids["property"])), ("meter_number", 2, "reading_model", "Zählernummer: M-123")]
    assert await read_value("SELECT count(*) FROM assignment_proposal WHERE organization_id=:org", ids) == 0, "a hint proposes nothing"


# ---- what a logical document has cost ---------------------------------------------------------------------------------

def usage_of(api, document):
    return api.get("/api/logical-documents/" + document + "/model-usage")


async def test_the_usage_of_a_document_is_visible_over_all_attempts_also_a_failed_one_and_equals_what_the_month_counts(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    reading = ProviderUsage(1000, 200, Decimal("0.00165"), 1)
    extraction = ProviderUsage(2000, 100, Decimal("0.00085"), 1)
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([None]), pages=1)
        (document,) = (await documents(api, ref)).values()
        assert (await usage_of(api, document)).json() == {"stages": [], "total": None}, "nothing requested is no usage, not a zero"
        harness = Harness(reader=Reader(usage=reading), extractor=FakeInvoiceExtractionProvider(failure=ProviderFailure(
            PROVIDER_CONNECTION_ERROR, True, "synthetic outage", usage=ProviderUsage(40, 0, Decimal("0.00001"), 1))))
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 1
        first = (await usage_of(api, document)).json()
        assert [(stage["stage"], stage["model"], stage["calls"], stage["usage_status"]) for stage in first["stages"]] == [
            ("READING", READING_MODEL, 1, "REPORTED"), ("EXTRACTION", EXTRACTION_MODEL, 1, "REPORTED")]
        assert Decimal(first["total"]["estimated_cost_usd"]) == Decimal("0.00166"), "a failed request keeps what it consumed"

        status = (await api.get("/api/logical-documents/" + document + "/processing")).json()
        assert (status["status"], status["error_code"], status["can_retry"]) == ("failed", "model_extraction_failed", True)
        retried = await api.post("/api/logical-documents/" + document + "/retry-extraction", headers={"Idempotency-Key": str(uuid.uuid4())},
                                 json={"expected_fence": status["fence"], "reason": "The provider is back"})
        assert retried.status_code == 202, retried.text
        harness.extractor._failure, harness.extractor._usage = None, extraction
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 1

        both = (await usage_of(api, document)).json()
        assert [(stage["stage"], stage["calls"], Decimal(stage["estimated_cost_usd"])) for stage in both["stages"]] == [
            ("READING", 2, Decimal("0.0033")), ("EXTRACTION", 2, Decimal("0.00086"))], "a retry is metered again, both attempts count"
        assert (both["total"]["input_tokens"], both["total"]["output_tokens"], Decimal(both["total"]["estimated_cost_usd"])) == (4040, 500, Decimal("0.00416"))
        async with get_worker_tenant_session(ids["org"]) as db:
            assert await OrganizationAiSettingsRepository(db, ids["org"]).monthly_usage() == Decimal("0.00416"), "the ledger the month counts"
        assert (await api.get("/api/logical-documents/" + document + "/processing")).json()["status"] == "succeeded"


async def test_usage_that_was_not_reported_is_shown_as_the_reservation_it_is_and_never_as_a_known_zero(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([FIRST]), pages=1)
        (document,) = (await documents(api, ref)).values()
        assert await extract_all(ids["org"], Harness(extractor=FakeInvoiceExtractionProvider(failure=UNREPORTED)), processor=extract_logical) == 1
        rows = await ledger(ids["org"])
        body = (await usage_of(api, document)).json()
    (call,) = rows
    assert (call.status, call.usage_complete) == ("SETTLED", False) and call.cost_usd == call.reserved_cost_usd > 0
    (stage,) = body["stages"]
    assert (stage["stage"], stage["usage_status"], stage["reported_input_tokens"], stage["reported_output_tokens"]) == ("EXTRACTION", "RESERVED", None, None)
    assert Decimal(stage["estimated_cost_usd"]) == call.reserved_cost_usd.quantize(Decimal("0.000001")) > 0
    assert stage["input_tokens"] == call.reserved_input_tokens


async def test_the_usage_is_not_readable_by_another_organization_or_by_a_user_without_access_to_the_document(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    foreign = await foreign_fixture(migrator_session)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([None]), pages=1)
        (document,) = (await documents(api, ref)).values()
        assert await extract_all(ids["org"], Harness(), processor=extract_logical) == 1
        assert len((await usage_of(api, document)).json()["stages"]) == 2
    async with client(foreign) as other:
        assert (await usage_of(other, document)).status_code == 404, "another organization finds no such document"
    async with client(ids, "actor") as stranger:
        assert (await usage_of(stranger, document)).status_code == 404, "no read access to the original"
        assert (await usage_of(stranger, str(uuid.uuid4()))).status_code == 404
    async with client(ids) as api:
        assert (await usage_of(api, str(uuid.uuid4()))).status_code == 404
    assert len(await ledger(foreign["org"])) == 0


# ---- what is left behind when the models are not asked, asked in part, or fail --------------------------------------

def budget(limit):
    return lambda org: enable_models(org, monthly_limit_usd=limit)


def replace_model(org):
    enable_models(org, model="gpt-5.6-sol")


def withdraw(org):
    enable_models(org, import_enabled=False)


def confirm_inbox_text(org):
    """The organization confirms the current text (it names inbox documents) for its destination, as a new record."""
    with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as db, db, db.cursor() as cursor:
        cursor.execute("""INSERT INTO organization_ai_confirmation(organization_id,confirmed_by_user_id,provider,base_url,text_version)
            SELECT s.organization_id,(SELECT id FROM "user" WHERE organization_id=s.organization_id ORDER BY created_at LIMIT 1),s.provider,s.base_url,%s
            FROM organization_ai_settings s WHERE s.organization_id=%s""", (AI_PROVIDER_CONFIRMATION_VERSION, str(org)))


def reset_models(org):
    enable_models(org)


# scenario: what the organization has at the start, what happens while the first request is out, which fake fails, the
# scanned pages of the document, the state it ends in, the requests that were made and how the cause is removed
SCENARIOS = {
    "limit-before-the-first-request": dict(start={"monthly_limit_usd": Decimal("0.0000001")}, state=("unavailable", "organization_ai_budget_exceeded", True), stages=[]),
    "limit-reached-between-the-stages": dict(during=budget(Decimal("0.0000001")), scans=1, state=("unavailable", "organization_ai_budget_exceeded", True), stages=["read_page"]),
    "limit-reached-between-two-pages": dict(during=budget(Decimal("0.0000001")), state=("unavailable", "organization_ai_budget_exceeded", True), stages=["read_page"]),
    "the-reading-model-fails": dict(reader=OUTAGE, state=("failed", "model_reading_failed", True), stages=["read_page"]),
    "the-extraction-model-fails": dict(extractor=OUTAGE, scans=1, state=("failed", "model_extraction_failed", True), stages=["read_page", "extract_invoice"]),
    "the-extraction-model-fails-without-a-usage-report": dict(extractor=UNREPORTED, scans=1, state=("failed", "model_extraction_failed", True), stages=["read_page", "extract_invoice"]),
    "the-model-is-replaced-during-the-run": dict(during=replace_model, state=("unavailable", "ai_settings_changed", True), stages=["read_page"]),
    "the-opt-in-is-withdrawn-during-the-run": dict(during=withdraw, state=("unavailable", "ai_import_disabled", True), stages=["read_page"]),
    "the-confirmation-names-no-inbox-documents": dict(start={"text_version": "2"}, state=("unavailable", "ai_inbox_confirmation_missing", True), stages=[], fix=confirm_inbox_text),
}


async def state_of(api, document):
    status = (await api.get("/api/logical-documents/" + document + "/processing")).json()
    return status, (status["status"], status["error_code"], status["can_retry"])


@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_whatever_stops_the_models_the_document_is_retained_nothing_is_applied_usage_is_visible_and_a_retry_works(migrator_session, scope_seed, store, name):
    scenario = SCENARIOS[name]
    scans = scenario.get("scans", 2)
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"], **scenario.get("start", {}))
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([None] * scans), pages=scans)
        (document,) = (await documents(api, ref)).values()
        await confirm_partition(api, ref, document)
        during = scenario.get("during")

        async def acting(_):
            during(ids["org"])

        reader = Reader(after=acting if during else None, failures={(document, 1): scenario["reader"]} if "reader" in scenario else None)
        harness = Harness(reader=reader, extractor=FakeInvoiceExtractionProvider(failure=scenario.get("extractor")))
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 1

        status, ended = await state_of(api, document)
        assert ended == scenario["state"], "an honest status with the structured reason, and the established retry open"
        stages = scenario["stages"]
        assert len(reader.reads) == stages.count("read_page") and len(harness.extractor.calls) == stages.count("extract_invoice"), "nothing more is requested"
        # What was requested is recorded, and what a user reads is what the ledger holds.
        rows = await ledger(ids["org"])
        assert [row.stage for row in rows] == stages and all(row.status == "SETTLED" for row in rows)
        body = (await usage_of(api, document)).json()
        assert [stage["calls"] for stage in body["stages"]] == [1] * len(rows)
        if rows:
            assert Decimal(body["total"]["estimated_cost_usd"]) == sum((row.cost_usd for row in rows), Decimal("0")).quantize(Decimal("0.000001")) > 0
        else:
            assert body == {"stages": [], "total": None}, "nothing was requested: no usage, never 0,00 USD"
        # The document is retained and nothing is applied.
        assert await read_value("SELECT count(*) FROM intake_original WHERE organization_id=:org", ids) == 1
        assert (await api.get("/api/document-refs/" + ref["id"] + "/extractions")).json()["items"][0]["status"] == ended[0]
        assert await read_value("SELECT count(*) FROM extraction_revision WHERE organization_id=:org AND payload IS NOT NULL", ids) == 0, "no proposal"
        for table in ("cost_entry", "assignment_proposal", "invoice_review_revision", "allocation_proposal"):
            assert await read_value(f"SELECT count(*) FROM {table} WHERE organization_id=:org", ids) == 0, table

        # Once the cause is gone the established retry runs the document again and meters it again.
        enable_models(ids["org"])
        scenario.get("fix", reset_models)(ids["org"])
        retried = await api.post("/api/logical-documents/" + document + "/retry-extraction", headers={"Idempotency-Key": str(uuid.uuid4())},
                                 json={"expected_fence": status["fence"], "reason": "Cause removed"})
        assert retried.status_code == 202, retried.text
        assert await extract_all(ids["org"], Harness(), processor=extract_logical) == 1
        second, ended = await state_of(api, document)
        assert (ended, second["attempt_number"]) == (("succeeded", None, False), 2)
        assert len(await ledger(ids["org"])) == len(rows) + scans + 1, "the second attempt reads every page and extracts"
        again = (await usage_of(api, document)).json()
        assert [(stage["stage"], stage["calls"]) for stage in again["stages"]] == [
            ("READING", stages.count("read_page") + scans), ("EXTRACTION", stages.count("extract_invoice") + 1)]
        assert await read_value("SELECT count(*) FROM extraction_revision WHERE organization_id=:org AND payload IS NOT NULL", ids) == 1


async def test_three_failed_attempts_end_the_retry_and_every_attempt_stays_metered(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([FIRST]), pages=1)
        (document,) = (await documents(api, ref)).values()
        harness = Harness(extractor=FakeInvoiceExtractionProvider(failure=OUTAGE))
        for attempt in (1, 2, 3):
            assert await extract_all(ids["org"], harness, processor=extract_logical) == 1
            status, ended = await state_of(api, document)
            assert (ended, status["attempt_number"]) == (("failed", "model_extraction_failed", attempt < 3), attempt)
            if attempt < 3:
                retried = await api.post("/api/logical-documents/" + document + "/retry-extraction", headers={"Idempotency-Key": str(uuid.uuid4())},
                                         json={"expected_fence": status["fence"], "reason": f"Attempt {attempt + 1}"})
                assert retried.status_code == 202, retried.text
        refused = await api.post("/api/logical-documents/" + document + "/retry-extraction", headers={"Idempotency-Key": str(uuid.uuid4())},
                                 json={"expected_fence": status["fence"], "reason": "A fourth attempt"})
        assert refused.status_code == 409, "the attempt limit holds for the paid path as for the local one"
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 0, "nothing is queued"
        assert len(harness.extractor.calls) == 3 and len(await ledger(ids["org"])) == 3
        body = (await usage_of(api, document)).json()
        assert [(stage["stage"], stage["calls"]) for stage in body["stages"]] == [("EXTRACTION", 3)]
        assert Decimal(body["total"]["estimated_cost_usd"]) == Decimal("0.00003"), "every attempt's usage is kept"
        assert await read_value("SELECT count(*) FROM intake_original WHERE organization_id=:org", ids) == 1


async def test_a_lost_fence_leaves_the_usage_already_incurred_recorded_and_visible(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([None, None, None]), pages=3)
        (document,) = (await documents(api, ref)).values()
        await confirm_partition(api, ref, document)

        async def reclaimed(_):
            await mutate(text("UPDATE intake_extraction_job SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE organization_id=:org"), ids)
            await work_sql(ids, "SELECT claim_intake_extraction(:org)")

        reader = Reader(after=reclaimed, usage=ProviderUsage(1000, 200, Decimal("0.00165"), 1))
        assert not await extract_all(ids["org"], Harness(reader=reader), processor=extract_logical)
        body = (await usage_of(api, document)).json()
        assert [(stage["stage"], stage["calls"], Decimal(stage["estimated_cost_usd"])) for stage in body["stages"]] == [("READING", 1, Decimal("0.00165"))]
        assert await read_value("SELECT count(*) FROM extraction_revision WHERE organization_id=:org", ids) == 0, "the stale run recorded no proposal"
        assert await read_value("SELECT count(*) FROM cost_entry WHERE organization_id=:org", ids) == 0
        assert await read_value("SELECT count(*) FROM intake_original WHERE organization_id=:org", ids) == 1


async def test_an_organization_that_confirmed_the_earlier_text_keeps_invoices_and_has_its_inbox_documents_held_back(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"], text_version="2")
    async with get_worker_tenant_session(ids["org"]) as db:
        effective = await effective_ai_settings(db, ids["org"])
    assert invoice_import_blocker(effective, needs_reading=False) is None, "the invoices of version 2 are covered"
    assert invoice_import_blocker(effective, needs_reading=False, inbox=True) == INBOX_AI_CONFIRMATION_MISSING
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([FIRST]), pages=1)
        (document,) = (await documents(api, ref)).values()
        harness = Harness()
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 1
        status, ended = await state_of(api, document)
    assert ended == ("unavailable", "ai_inbox_confirmation_missing", True)
    assert harness.built == [] and await ledger(ids["org"]) == [], "no provider is constructed and nothing is sent"


async def test_the_text_of_an_installation_without_invoices_names_the_inbox_so_its_documents_go_to_the_models(migrator_session, scope_seed, store):
    """Version 4 names declarations and the inbox but no invoices: what an installation that processes no invoices
    shows and records. Its inbox documents are read by the organization's models; its invoices would not be."""
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"], text_version="4")
    async with get_worker_tenant_session(ids["org"]) as db:
        effective = await effective_ai_settings(db, ids["org"])
    assert invoice_import_blocker(effective, needs_reading=False, inbox=True) is None
    assert invoice_import_blocker(effective, needs_reading=False) == INVOICE_AI_CONFIRMATION_MISSING
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([None, FIRST]), pages=2)
        (document,) = (await documents(api, ref)).values()
        await confirm_partition(api, ref, document)
        harness = Harness(reader=Reader(), extractor=FakeInvoiceExtractionProvider())
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 1
        status, _ = await state_of(api, document)
    assert status["status"] == "succeeded"
    assert [row.stage for row in await ledger(ids["org"])] == ["read_page", "extract_invoice"]
