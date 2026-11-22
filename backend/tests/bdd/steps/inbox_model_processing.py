"""An inbox document through the worker with in-process fake providers, observed through the HTTP API (issue #501).

Only worker ticks run in-process; every upload, question and command is an HTTP request. The sample documents are
synthetic: a building-insurance invoice of 312,00 EUR dated 15 January 2026, and a water bill of 99,00 EUR. The fake
providers answer what a model would read off them, with the page and passage each value stands on.
"""
import hashlib
import uuid
from dataclasses import replace
from decimal import Decimal

import pytest
from pytest_bdd import given, parsers, then, when

from app.core.intake_extraction import extract_logical
from app.core.intake_inspection import result
from app.declarations.fake_provider import FakeDocumentReadingProvider, FakeInvoiceExtractionProvider, synthetic_invoice_output
from app.declarations.provider import ProviderFailure, ProviderUsage
from app.declarations.schemas import ReadingBlock
from tests.ai_settings_support import configure_ai
from tests.bdd.steps.browser_setup import create
from tests.bdd.steps.inbox import get
from tests.bdd.steps.invoice_processing import EXTRACTION_USAGE, READING_USAGE, _organization, _settings
from tests.intake_model_support import Harness, document_pdf, inbox_adapters
from tests.test_m5_einvoice import UBL_WITH_IBAN

INSURANCE = ["Mustermann Versicherungen AG", "Rechnungsnummer RE-2026-0042", "Gebaeudeversicherung 2026, Gesamtbetrag 312,00 EUR"]
WATER_BILL = ["Stadtwerke Test GmbH, Abrechnung Wasser", "Gesamtbetrag 99,00 EUR"]


class Reader(FakeDocumentReadingProvider):
    """Reads each page as the sample says, whichever document asks for it."""

    def __init__(self, by_page, **options):
        super().__init__(**options)
        self._by_page = by_page

    async def read_page(self, page, *, model, max_output_tokens):
        self._transcripts[(page.document_key, page.pdf_page)] = [ReadingBlock(kind="TEXT", text=line) for line in self._by_page[page.pdf_page]]
        return await super().read_page(page, model=model, max_output_tokens=max_output_tokens)


class Extractor(FakeInvoiceExtractionProvider):
    """Answers a document by the first page it was given: the sample's own proposal for that stretch of pages."""

    def __init__(self, by_first_page, **options):
        super().__init__(**options)
        self._by_first_page = by_first_page

    async def extract_invoice(self, documents, **options):
        answer = await super().extract_invoice(documents, **options)
        output = self._by_first_page.get(min(page.pdf_page for page in documents[0].pages))
        return answer if output is None else replace(answer, output=output)


INSURANCE_PROPOSAL = synthetic_invoice_output(
    vendor_name=("Mustermann Versicherungen AG", 0.95, 1, "Mustermann Versicherungen AG"),
    document_number=("RE-2026-0042", 0.9, 1, "Rechnungsnummer RE-2026-0042"),
    gross_amount=("312.00", 0.98, 1, "Gesamtbetrag 312,00 EUR"),
    invoice_date=("2026-01-15", 0.97, 2, "Rechnungsdatum 15.01.2026"))
WATER_PROPOSAL = synthetic_invoice_output(
    vendor_name=("Stadtwerke Test GmbH", 0.9, 3, "Stadtwerke Test GmbH, Abrechnung Wasser"),
    gross_amount=("99.00", 0.9, 4, "Gesamtbetrag 99,00 EUR"),
    invoice_date=("2026-02-03", 0.9), document_number=("W-77", 0.9))


def _insurance_scan_pages():
    return document_pdf([INSURANCE, None]), {2: ["Rechnungsdatum 15.01.2026"]}


def _upload(world, data, filename, mime="application/pdf"):
    batch = create(world, "/api/intake-batches", {"items": [{
        "client_item_key": "document", "filename": filename, "mime_type": mime,
        "content_sha256": hashlib.sha256(data).hexdigest(), "content_byte_size": len(data)}]}, keyed=True)
    response = world.act("POST", f"/api/intake-batches/{batch['id']}/items", data={"client_item_key": "document"}, files={"file": (filename, data, mime)})
    assert response.status_code == 202, world.describe_response()
    world.data.update(batch=batch, original=response.json(), original_bytes=data)


def _process(world, monkeypatch, *, pages, reader, extractor):
    """Run the worker ticks of the inbox until it is idle: inspection, delivery of the documents, extraction."""
    from app.workers import intake_extraction, workflow
    from app.workers.workflow_runner import WorkflowRunner

    harness = Harness(reader=reader, extractor=extractor)
    world.data["harness"] = harness
    if "inspect_once" not in world.data:
        world.data["inspect_once"], world.data["extract_once"] = workflow.process_once, intake_extraction.process_once
        inspect_once, extract_once = world.data["inspect_once"], world.data["extract_once"]

        async def inspect(factory, org):
            return await inspect_once(factory, org, inspector=lambda data, mime: result(data, "clean", mime=mime, pages=pages))

        async def extract(factory, org, **options):
            return await extract_once(factory, org, processor=extract_logical, **options)

        monkeypatch.setattr(workflow, "process_once", inspect)
        monkeypatch.setattr(intake_extraction, "process_once", extract)

    organization = world.data.setdefault("organization_id", _organization(world))

    async def organizations():
        return (organization,)

    runner = WorkflowRunner(organizations, inbox_adapters(("intake_inspection", "outbox", "intake_extraction"), harness))
    for _ in range(20):
        tick = world.run(runner.run_once())
        if tick.code == "IDLE":
            return
        assert tick.code == "PROCESSED", tick
    pytest.fail("The inbox did not become idle")


def _documents(world):
    ref, = get(world, "/api/document-refs/page", source_id=world.data["original"]["original_id"])["items"]
    world.data["ref"] = ref
    return {(item["page_start"], item["page_end"]): item for item in get(world, "/api/logical-documents", document_ref_id=ref["id"])["items"]}


def _sole_document(world):
    (document,) = _documents(world).values()
    return document


def _revision(world, document):
    """The newest proposal of one logical document, with the evidence rows the review screen reads."""
    ref = world.data["ref"]
    newest = get(world, f"/api/document-refs/{ref['id']}/extractions", logical_document_id=document["id"])["items"][0]
    return get(world, f"/api/extraction-revisions/{newest['id']}")


def _confirm(world, *documents):
    """The reviewer confirms how the original divides: these documents, each page in exactly one of them."""
    ref = world.data["ref"]
    current = get(world, f"/api/document-refs/{ref['id']}/partition")["current"]
    response = world.act("POST", f"/api/document-refs/{ref['id']}/partition", headers={
        "Idempotency-Key": str(uuid.uuid4()), "If-Match": '"' + (current["id"] if current else "none") + '"'},
        json={"logical_document_ids": [document["id"] for document in documents], "reason": "Checked against the original", "visual_pages_reviewed": True})
    assert response.status_code == 201, world.describe_response()


def _run_insurance_scan(world, monkeypatch, *, extractor=None, reader_usage=READING_USAGE):
    data, transcripts = _insurance_scan_pages()
    _upload(world, data, "versicherung.pdf")
    world.data["sample"] = (data, transcripts)
    # A multipage upload waits until it is confirmed as one document (or split): the inbox looks at it, then the reviewer decides.
    _process(world, monkeypatch, pages=2, reader=Reader(transcripts, usage=reader_usage), extractor=Extractor({}))
    _confirm_whole(world)
    _process(world, monkeypatch, pages=2, reader=Reader(transcripts, usage=reader_usage),
             extractor=extractor or Extractor({1: INSURANCE_PROPOSAL}, usage=EXTRACTION_USAGE))
    world.data["document"] = _sole_document(world)


def _confirm_whole(world):
    (whole,) = _documents(world).values()
    _confirm(world, whole)


@given("the organization has enabled the AI import for the inbox with its own key and both models")
def inbox_ai_enabled(world):
    world.data["organization_id"] = _organization(world)
    _settings(world)


@given("the organization has enabled the AI import for the inbox with a monthly limit of one cent")
def inbox_ai_with_limit(world):
    world.data["organization_id"] = _organization(world)
    _settings(world, monthly_limit_usd="0.01")


@given("the organization enabled the AI import and confirmed the provider with the text that names invoices but not inbox documents")
def inbox_ai_confirmed_earlier(world):
    world.data["organization_id"] = _organization(world)
    # What an organization holds that confirmed the text of version 2: its invoices are covered, the inbox is not.
    configure_ai(world.data["organization_id"], text_version="2", import_enabled=True)
    settings = world.act("GET", f"/api/organizations/{world.data['organization_id']}/ai-settings").json()
    assert (settings["invoice_confirmation_required"], settings["inbox_confirmation_required"]) == (False, True)


@when("the reviewer uploads a two-page building-insurance invoice whose second page is a scan, confirms it as one document and the inbox processes it")
def upload_and_process(world, monkeypatch):
    _run_insurance_scan(world, monkeypatch)


@when("the reviewer uploads a two-page building-insurance invoice whose second page is a scan whose extraction fails at the provider, confirms it as one document and the inbox processes it")
def upload_and_fail(world, monkeypatch):
    failure = ProviderFailure("PROVIDER_TIMEOUT", False, "timed out", usage=ProviderUsage(300, 0, Decimal("0.000225"), 1))
    _run_insurance_scan(world, monkeypatch, extractor=Extractor({1: INSURANCE_PROPOSAL}, failure=failure))


@when("the reviewer uploads a four-page original with an insurance invoice on pages 1 and 2 and a water bill on pages 3 and 4")
def upload_four_pages(world):
    data = document_pdf([INSURANCE, ["Zahlbar bis 28.02.2026 auf das angegebene Konto"], None, None])
    _upload(world, data, "unterlagen.pdf")
    world.data["sample"] = (data, {3: [WATER_BILL[0]], 4: [WATER_BILL[1]]})


@when("the inbox processes the upload")
def process_upload(world, monkeypatch):
    world.data["organization_id"] = _organization(world)
    _, transcripts = world.data["sample"]
    _process(world, monkeypatch, pages=4, reader=Reader(transcripts, usage=READING_USAGE), extractor=Extractor({1: INSURANCE_PROPOSAL, 3: WATER_PROPOSAL}, usage=EXTRACTION_USAGE))
    world.data["whole"] = _documents(world)[(1, 4)]


@when("the inbox processes the documents")
def process_documents(world, monkeypatch):
    """The worker ticks after the reviewer has divided the original (on the source page, in the browser twin)."""
    _, transcripts = world.data["sample"]
    _process(world, monkeypatch, pages=4, reader=Reader(transcripts, usage=READING_USAGE),
             extractor=Extractor({1: INSURANCE_PROPOSAL, 3: WATER_PROPOSAL}, usage=EXTRACTION_USAGE))
    world.data["documents"] = _documents(world)


@when(parsers.parse("the reviewer marks pages {a:d} to {b:d} and pages {c:d} to {d:d} as documents, confirms the division and the inbox processes them"))
def split_and_process(world, monkeypatch, a, b, c, d):
    _, transcripts = world.data["sample"]
    ref = world.data["ref"]
    for first, last in ((a, b), (c, d)):
        response = world.act("POST", "/api/logical-documents", headers={"Idempotency-Key": str(uuid.uuid4())},
                             json={"document_ref_id": ref["id"], "page_start": first, "page_end": last, "reason": "Two vouchers in one scan"})
        assert response.status_code == 201, world.describe_response()
    documents = _documents(world)
    _confirm(world, documents[(a, b)], documents[(c, d)])
    _process(world, monkeypatch, pages=4, reader=Reader(transcripts, usage=READING_USAGE),
             extractor=Extractor({1: INSURANCE_PROPOSAL, 3: WATER_PROPOSAL}, usage=EXTRACTION_USAGE))
    world.data["documents"] = _documents(world)


@when("the reviewer uploads a one-page building-insurance invoice and the inbox processes it")
def upload_one_page(world, monkeypatch):
    data = document_pdf([INSURANCE])
    _upload(world, data, "einzelseite.pdf")
    world.data["sample"] = (data, {})
    _process(world, monkeypatch, pages=1, reader=Reader({}), extractor=Extractor({1: INSURANCE_PROPOSAL}, usage=EXTRACTION_USAGE))
    world.data["document"] = _sole_document(world)


@when("the reviewer confirms the whole original as one document and the inbox processes it")
def confirm_whole_and_process(world, monkeypatch):
    _confirm_whole(world)
    _, transcripts = world.data["sample"]
    _process(world, monkeypatch, pages=4, reader=Reader(transcripts, usage=READING_USAGE), extractor=Extractor({1: INSURANCE_PROPOSAL}, usage=EXTRACTION_USAGE))
    world.data["document"] = world.data["whole"]


@when("the reviewer uploads a structured e-invoice and the inbox processes it")
def upload_structured(world, monkeypatch):
    _upload(world, UBL_WITH_IBAN, "e-rechnung.xml", "application/xml")
    _process(world, monkeypatch, pages=None, reader=Reader({}), extractor=Extractor({}))
    world.data["document"] = _sole_document(world)


@when("the administrator confirms the provider again and the reviewer asks for the processing once more")
def confirm_and_retry(world, monkeypatch):
    org = world.data["organization_id"]
    current = world.act("GET", f"/api/organizations/{org}/ai-settings").json()
    # The settings are saved as the settings page saves them: every choice again, with the tick, without the key.
    saved = world.act("PUT", f"/api/organizations/{org}/ai-settings", json={
        "model": current["model"], "document_model": current["document_model"], "import_enabled": current["import_enabled"], "confirmation": True})
    assert saved.status_code == 200 and saved.json()["inbox_confirmation_required"] is False, world.describe_response()
    _retry(world, monkeypatch, Extractor({1: INSURANCE_PROPOSAL}, usage=EXTRACTION_USAGE))


@when("the reviewer asks for the processing once more after the provider works again")
def retry_after_failure(world, monkeypatch):
    _retry(world, monkeypatch, Extractor({1: INSURANCE_PROPOSAL}, usage=EXTRACTION_USAGE))


def _retry(world, monkeypatch, extractor):
    document = world.data["document"]
    status = get(world, f"/api/logical-documents/{document['id']}/processing")
    assert status["can_retry"] is True, status
    response = world.act("POST", f"/api/logical-documents/{document['id']}/retry-extraction", headers={"Idempotency-Key": str(uuid.uuid4())},
                         json={"expected_fence": status["fence"], "reason": "The cause is gone"})
    assert response.status_code == 202, world.describe_response()
    _, transcripts = world.data["sample"]
    _process(world, monkeypatch, pages=2, reader=Reader(transcripts, usage=READING_USAGE), extractor=extractor)


@when("Verwaltung Beta asks for the usage of that inbox document")
def beta_asks(world):
    world.send(world.administrator_of("Verwaltung Beta"), "GET", f"/api/logical-documents/{world.data['document']['id']}/model-usage")


@then(parsers.parse('the inbox document proposes 312,00 EUR dated 15 January 2026 from "{vendor}"'))
def proposes_insurance(world, vendor):
    revision = _revision(world, world.data["document"])
    assert revision["status"] == "succeeded", revision
    fields = revision["payload"]
    assert (fields["gross_amount"], fields["invoice_date"], fields["vendor_name"]) == ("312.00", "2026-01-15", vendor)


def _evidence(world, name):
    revision = _revision(world, world.data["document"])
    return next(field for field in revision["fields"] if field["field_name"] == name)


@then(parsers.parse('the amount is shown as found on page {page:d} in "{passage}"'))
def amount_found(world, page, passage):
    field = _evidence(world, "gross_amount")
    assert (field["locator_status"], field["page_number"], field["quotation"], field["method"]) == ("page_quote", page, passage, "text_layer")


@then(parsers.parse('the invoice date is shown as found on page {page:d} in "{passage}", read by the reading model'))
def date_found(world, page, passage):
    field = _evidence(world, "invoice_date")
    assert (field["locator_status"], field["page_number"], field["quotation"], field["method"]) == ("page_quote", page, passage, "reading_model")


@then("no cost was created from the inbox document")
def nothing_posted(world):
    assert get(world, "/api/cost-entries") == []


def _usage(world):
    response = world.look(world.actor, f"/api/logical-documents/{world.data['document']['id']}/model-usage")
    assert response.status_code == 200, response.text
    return response.json()


@then("the inbox document shows the usage of the reading and of the extraction model")
def both_stages(world):
    usage = _usage(world)
    assert [(s["stage"], s["model"], s["usage_status"]) for s in usage["stages"]] == [("READING", "gpt-5.4-mini", "REPORTED"), ("EXTRACTION", "gpt-5.6-luna", "REPORTED")]
    assert Decimal(usage["total"]["estimated_cost_usd"]) == Decimal("0.0025")


@then("the inbox document shows the usage of the reading and of the failed extraction")
def reading_and_failed_extraction(world):
    usage = _usage(world)
    assert [s["stage"] for s in usage["stages"]] == ["READING", "EXTRACTION"]
    assert Decimal(usage["total"]["estimated_cost_usd"]) == Decimal("0.00165") + Decimal("0.000225")


@then("the inbox document shows the usage of both attempts")
def both_attempts(world):
    usage = _usage(world)
    assert [(s["stage"], s["calls"]) for s in usage["stages"]] == [("READING", 2), ("EXTRACTION", 2)]
    assert Decimal(usage["total"]["estimated_cost_usd"]) == Decimal("0.00165") + Decimal("0.000225") + Decimal("0.00165") + Decimal("0.00085")


@then("the inbox document shows no usage")
def no_usage(world):
    assert _usage(world) == {"stages": [], "total": None}


@then("nothing of the inbox document was sent to a provider")
def nothing_sent(world):
    harness = world.data["harness"]
    assert harness.reader.reads == [] and harness.extractor.calls == []


def _proposes_nothing(world, status, code):
    document = world.data["document"]
    state = get(world, f"/api/logical-documents/{document['id']}/processing")
    assert (state["status"], state["error_code"]) == (status, code), state
    assert _revision(world, document)["payload"] is None


@then("the inbox document says that the organization has to confirm again and proposes nothing")
def confirm_again(world):
    _proposes_nothing(world, "unavailable", "ai_inbox_confirmation_missing")


@then("the inbox document says that the processing failed and proposes nothing")
def processing_failed(world):
    _proposes_nothing(world, "failed", "model_extraction_failed")


@then("the inbox document says that the monthly budget is exhausted and proposes nothing")
def budget_exhausted(world):
    _proposes_nothing(world, "unavailable", "organization_ai_budget_exceeded")


@then("the original of the inbox document can still be downloaded")
def original_downloadable(world):
    response = world.look(world.actor, f"/api/intake-batches/originals/{world.data['original']['original_id']}/content")
    assert response.status_code == 200 and response.content == world.data["original_bytes"]


@then("the usage of the inbox document is not found")
def usage_not_found(world):
    assert world.response.status_code == 404, world.describe_response()


@then("the inbox document proposes the values of the e-invoice")
def proposes_einvoice(world):
    revision = _revision(world, world.data["document"])
    assert (revision["status"], revision["processor_version"], revision["payload"]["source_format"]) == ("succeeded", "intake-local-v1", "XRECHNUNG_UBL")


@then(parsers.parse('the inbox document of pages {a:d} to {b:d} proposes {amount} EUR from "{vendor}"'))
def split_proposal(world, a, b, amount, vendor):
    fields = _revision(world, world.data["documents"][(a, b)])["payload"]
    assert (fields["gross_amount"], fields["vendor_name"]) == (amount.replace(",", "."), vendor)


@then("the upload says that it waits for the division and nobody has proposed anything")
def waits_for_the_split(world):
    item = get(world, f"/api/intake-batches/{world.data['batch']['id']}")["items"][0]
    assert (item["status"], item["stage"], item["error_code"]) == ("review", "split", "awaiting_split"), item
    whole = world.data["whole"]
    state = get(world, f"/api/logical-documents/{whole['id']}/processing")
    assert (state["status"], state["error_code"], state["can_retry"]) == ("awaiting_split", "awaiting_split", False), state
    assert _revision_count(world) == 0


def _revision_count(world):
    ref = world.data["ref"]
    return len(get(world, f"/api/document-refs/{ref['id']}/extractions")["items"])


@then("nobody was asked how the original divides")
def nobody_asked(world):
    assert get(world, f"/api/document-refs/{world.data['ref']['id']}/partition")["current"] is None


@then("the unsplit whole was never sent and says that it is not part of the division")
def whole_not_sent(world):
    harness, whole = world.data["harness"], world.data["whole"]
    assert whole["id"] not in {call.document_key for call in harness.extractor.calls} | {read.document_key for read in harness.reader.reads}
    state = get(world, f"/api/logical-documents/{whole['id']}/processing")
    assert (state["status"], state["error_code"]) == ("awaiting_split", "not_in_split"), state


@then("the whole original was sent once, with all four pages, as one document")
def whole_sent(world):
    harness = world.data["harness"]
    assert [call.pages for call in harness.extractor.calls] == [(1, 2, 3, 4)]
    assert sorted(read.pdf_page for read in harness.reader.reads) == [3, 4]


@then("neither document was asked about a page of the other")
def own_pages_only(world):
    documents, harness = world.data["documents"], world.data["harness"]
    first, second = documents[(1, 2)]["id"], documents[(3, 4)]["id"]
    assert {call.document_key: call.pages for call in harness.extractor.calls} == {first: (1, 2), second: (3, 4)}
    assert {(read.document_key, read.pdf_page) for read in harness.reader.reads} == {(second, 3), (second, 4)}
