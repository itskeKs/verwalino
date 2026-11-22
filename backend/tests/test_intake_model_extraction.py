"""The inbox reads and extracts an unstructured original through the organization's two models (issue #501, ADR 0017).

Everything here runs the real queue-fenced worker against the test database with the in-process fake providers: no provider
call is made, and Tesseract is made unusable, because this path has none. Each logical document of an original is read
from its own selected pages only, every request is reserved and settled in the shared ledger, and the opt-in is the
organization's own. An original of more than one page is sent only after a person has decided how it divides (a
confirmed source partition lists the document); until then nothing is requested from a provider.
"""
import hashlib
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.db import get_worker_session_factory, get_worker_tenant_session
from app.core.extraction_evidence import FIELD_TYPES
from app.core.intake_extraction import extract_logical
from app.core.intake_inspection import result
from app.declarations.fake_provider import (
    FakeDocumentReadingProvider,
    FakeInvoiceExtractionProvider,
    synthetic_invoice_output,
)
from app.declarations.provider import PROVIDER_CONNECTION_ERROR, ProviderFailure, ProviderUsage
from app.declarations.schemas import ReadingBlock
from app.repositories.organization_ai_settings_repo import OrganizationAiSettingsRepository
from app.workers.outbox import process_once as deliver_once
from app.workers.workflow import process_once as inspect_once
from tests.intake_model_support import (
    EXTRACTION_MODEL,
    READING_MODEL,
    Harness,
    confirm_partition,
    document_pdf,
    enable_models,
    extract_all,
    ledger,
    picture,
    tesseract_unusable as tesseract_unusable,
)
from tests.test_financial_scope_boundary import scope_seed as scope_seed, worker_tenant as worker_tenant
from tests.test_intake_batches import create, manifest, work_sql
from tests.test_invoice_extraction_worker import zugferd_pdf
from tests.test_invoice_source_custody import fixture, foreign_fixture, store as store
from tests.test_m5_einvoice import PAYEE_IBAN, UBL_WITH_IBAN
from tests.test_scope_payment_reconciliation import read_value
from tests.test_scoped_account_statements import client, mutate

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("tesseract_unusable")]

# A page with a text layer of its own (more than 40 non-whitespace characters) and the page of a scan.
FIRST = ["Mustermann Versicherungen AG", "Rechnungsnummer RE-2026-0042", "Gebaeudeversicherung 2026, Gesamtbetrag 312,00 EUR"]
SECOND = ["Mustermann Versicherungen AG, Seite 2", "Zahlbar bis 28.02.2026 auf das angegebene Konto"]


async def receive_original(api, ids, data, *, pages, mime="application/pdf", filename="invoice.pdf"):
    """Receive an original, inspect it clean and deliver it: its reference. The original's own logical document exists."""
    item = {**manifest(data)["items"][0], "filename": filename, "mime_type": mime}
    batch = await create(api, data, items=[item])
    uploaded = await api.post("/api/intake-batches/" + batch["id"] + "/items",
                              files={"file": (filename, data, mime)}, data={"client_item_key": "document-1"})
    assert uploaded.status_code == 202, uploaded.text
    factory = get_worker_session_factory()
    assert await inspect_once(factory, ids["org"], inspector=lambda content, kind: result(content, "clean", mime=kind, pages=pages))
    assert await deliver_once(factory, ids["org"])
    ref = (await api.get("/api/document-refs/page", params={"source_id": uploaded.json()["original_id"]})).json()["items"][0]
    return {**ref, "batch_id": batch["id"]}


async def documents(api, ref):
    return {(item["page_start"], item["page_end"]): item["id"]
            for item in (await api.get("/api/logical-documents", params={"document_ref_id": ref["id"]})).json()["items"]}


async def split(api, ref, start, end):
    created = await api.post("/api/logical-documents", json={"document_ref_id": ref["id"], "page_start": start, "page_end": end,
                                                              "reason": "Synthetic split"}, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def revision(ids, document):
    """The recorded proposal of one logical document: its header, proposal and the evidence it holds."""
    params = {**ids, "document": uuid.UUID(document)}
    header = await read_value("""SELECT to_jsonb(r)-'payload' FROM extraction_revision r
        WHERE r.organization_id=:org AND r.logical_document_id=:document""", params)
    payload = await read_value("SELECT payload FROM extraction_revision WHERE organization_id=:org AND logical_document_id=:document", params)
    evidence = await read_value("""SELECT COALESCE(jsonb_object_agg(field_name,locator_status),'{}'::jsonb) FROM field_evidence
        WHERE organization_id=:org AND extraction_revision_id=(SELECT id FROM extraction_revision WHERE organization_id=:org AND logical_document_id=:document)""", params)
    pages = await read_value("""SELECT a.result->'pages' FROM intake_extraction_attempt a JOIN extraction_revision r
        ON r.organization_id=a.organization_id AND r.intake_attempt_id=a.id WHERE r.organization_id=:org AND r.logical_document_id=:document""", params)
    return header, payload, evidence, pages


def job_state(ids, document):
    return read_value("SELECT jsonb_build_object('status',status,'error_code',error_code,'attempts',attempt_number) FROM intake_extraction_job WHERE organization_id=:org AND logical_document_id=:document",
                      {**ids, "document": uuid.UUID(document)})


class Reader(FakeDocumentReadingProvider):
    """A fake reading stage that keeps the images it was sent and may act after a page was read."""

    def __init__(self, *args, after=None, **options):
        super().__init__(*args, **options)
        self.images = []
        self._after = after

    async def read_page(self, page, *, model, max_output_tokens):
        self.images.append(page)
        answer = await super().read_page(page, model=model, max_output_tokens=max_output_tokens)
        if self._after is not None:
            await self._after(len(self.images))
        return answer


async def processing(api, document):
    reply = (await api.get("/api/logical-documents/" + document + "/processing")).json()
    return reply["status"], reply["error_code"], reply["can_retry"]


def nothing_was_sent(harness):
    return harness.built == [] and harness.reader.reads == [] and harness.extractor.calls == []


async def test_a_multipage_upload_waits_and_nothing_is_requested_before_a_person_has_decided_how_it_divides(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([FIRST, SECOND, None, None]), pages=4)
        whole = (await documents(api, ref))[(1, 4)]
        first, second = await split(api, ref, 1, 2), await split(api, ref, 3, 4)
        harness = Harness()
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 3, "every document of the original was looked at once"
        assert nothing_was_sent(harness), "no provider is even constructed, the whole upload is not sent and neither is a part of it"
        assert await ledger(ids["org"]) == []
        for document in (whole, first, second):
            assert await processing(api, document) == ("awaiting_split", "awaiting_split", False), "waiting is no failure: no retry to press"
            assert (await revision(ids, document))[1] is None
            assert (await job_state(ids, document))["attempts"] == 1
        item = (await api.get("/api/intake-batches/" + ref["batch_id"])).json()["items"][0]
        assert (item["status"], item["stage"], item["error_code"]) == ("review", "split", "awaiting_split")
        assert item["logical_counts"] == {"total": 3, "processing": 0, "review": 0, "failed": 0}
        fence = (await api.get("/api/logical-documents/" + whole + "/processing")).json()["fence"]
        again = await api.post("/api/logical-documents/" + whole + "/retry-extraction", json={"expected_fence": fence, "reason": "Try again"},
                               headers={"Idempotency-Key": str(uuid.uuid4())})
        assert again.status_code == 409, "a waiting document cannot be pushed through by retrying it"
        # Waiting again does not send anything either, and uses no further attempt.
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 0 and nothing_was_sent(harness)
        assert (await job_state(ids, whole))["attempts"] == 1


async def test_after_the_split_is_confirmed_each_document_is_read_from_its_own_pages_and_the_unsplit_whole_is_never_sent(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    data = document_pdf([FIRST, SECOND, None, None])
    async with client(ids) as api:
        ref = await receive_original(api, ids, data, pages=4)
        first, second = await split(api, ref, 1, 2), await split(api, ref, 3, 4)
        whole = (await documents(api, ref))[(1, 4)]
        reader = Reader(transcripts={
            (second, 3): [ReadingBlock(kind="TEXT", text="Stadtwerke Test GmbH, Abrechnung Wasser, Seite 3")],
            (second, 4): [ReadingBlock(kind="TEXT", text="Gesamtbetrag 99,00 EUR, Seite 4")]})
        harness = Harness(reader=reader, extractor=FakeInvoiceExtractionProvider(synthetic_invoice_output(gross_amount=("99.00", 0.9))))
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 3
        assert nothing_was_sent(harness)

        partition = await confirm_partition(api, ref, first, second)
        assert partition["logical_document_ids"] == [first, second]
        # What waited for the decision is queued again, the whole is left out of it and says so.
        assert [await processing(api, document) for document in (first, second, whole)] == [
            ("queued", None, False), ("queued", None, False), ("awaiting_split", "not_in_split", False)]
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 2

        reads = {}
        for record in reader.reads:
            assert record.model == READING_MODEL
            reads.setdefault(record.document_key, []).append(record.pdf_page)
        asked = {call.document_key: call for call in harness.extractor.calls}
        assert reads == {second: [3, 4]}, "each page is read once, only for its own document, and the first document needs no reading"
        assert {key: call.pages for key, call in asked.items()} == {first: (1, 2), second: (3, 4)}, "the unsplit whole is never sent"
        assert all(call.model == EXTRACTION_MODEL for call in harness.extractor.calls)
        # Never an unrelated page: what each document's extraction request carries is its own text.
        assert "RE-2026-0042" in asked[first].text and "Seite 3" not in asked[first].text and "Seite 4" not in asked[first].text
        assert "Seite 3" in asked[second].text and "Seite 4" in asked[second].text
        assert "RE-2026-0042" not in asked[second].text and "Seite 2" not in asked[second].text

        for document, numbers, methods in ((first, [1, 2], ["text_layer", "text_layer"]), (second, [3, 4], ["reading_model", "reading_model"])):
            header, payload, evidence, pages = await revision(ids, document)
            assert header["processor_version"] == "intake-model-v1" and header["status"] == "succeeded"
            assert header["content_sha256"] == hashlib.sha256(data).hexdigest(), "the hash binding of the original is kept"
            assert payload["source_pages"] == numbers, "the original's page numbers"
            assert [(page["number"], page["method"]) for page in pages] == list(zip(numbers, methods))
            assert payload["raw_text"] == "\n".join(page["text"] for page in pages)
            assert set(evidence) == {key for key in FIELD_TYPES if payload[key] is not None} and "gross_amount" in evidence
            assert set(evidence.values()) == {"unlocated"}, "a model proposes a value, no locator proves it"
            assert payload["vendor_iban"] == "DE89370400440532013000" and payload["is_einvoice"] is False
        assert (await revision(ids, second))[1]["gross_amount"] == "99.00"
        assert await read_value("SELECT count(*) FROM cost_entry WHERE organization_id=:org", ids) == 0, "a proposal books nothing"
        listed = (await api.get("/api/document-refs/" + ref["id"] + "/extractions")).json()["items"]
        assert {item["processor_version"] for item in listed} == {"intake-model-v1"} and len(listed) == 2
        for document in (first, second):
            assert (await job_state(ids, document))["status"] == "succeeded"
        assert await processing(api, whole) == ("awaiting_split", "not_in_split", False) and (await revision(ids, whole))[1] is None
        assert [row.document_key for row in harness.extractor.calls] == [first, second] and len(await ledger(ids["org"])) == 4
        # The original is processed from where the user sees it: the batch is ready for review, the whole does not hold it back.
        item = (await api.get("/api/intake-batches/" + ref["batch_id"])).json()["items"][0]
        assert (item["status"], item["stage"], item["error_code"]) == ("review", "review", None)


async def test_an_original_confirmed_as_one_document_is_sent_whole_and_only_then(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([FIRST, SECOND, None, None]), pages=4)
        (whole,) = (await documents(api, ref)).values()
        harness = Harness()
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 1 and nothing_was_sent(harness)
        assert await processing(api, whole) == ("awaiting_split", "awaiting_split", False)
        await confirm_partition(api, ref, whole)
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 1
        assert await processing(api, whole) == ("succeeded", None, False)
    assert [call.pages for call in harness.extractor.calls] == [(1, 2, 3, 4)] and [read.pdf_page for read in harness.reader.reads] == [3, 4]
    assert (await job_state(ids, whole))["attempts"] == 2, "the wait used an attempt"


async def test_a_single_page_upload_is_processed_at_once_without_a_split_decision(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([None]), pages=1)
        (document,) = (await documents(api, ref)).values()
        harness = Harness()
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 1
        assert await processing(api, document) == ("succeeded", None, False)
        assert (await api.get("/api/document-refs/" + ref["id"] + "/partition")).json()["current"] is None, "nobody was asked"
    assert [read.pdf_page for read in harness.reader.reads] == [1] and [call.pages for call in harness.extractor.calls] == [(1,)]
    assert (await job_state(ids, document))["attempts"] == 1


async def test_a_structured_multipage_e_invoice_needs_neither_a_model_nor_a_split_decision(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, zugferd_pdf(pages=3), pages=3)
        (document,) = (await documents(api, ref)).values()
        harness = Harness()
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 1
        assert await processing(api, document) == ("succeeded", None, False)
        assert (await api.get("/api/document-refs/" + ref["id"] + "/partition")).json()["current"] is None
    assert nothing_was_sent(harness) and await ledger(ids["org"]) == []
    header, payload, _, _ = await revision(ids, document)
    assert (header["processor_version"], payload["source_format"], payload["source_pages"]) == ("intake-local-v1", "ZUGFERD", [1, 2, 3])


async def test_a_partition_that_leaves_the_document_out_during_a_run_stops_further_spending(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([None, None]), pages=2)
        (whole,) = (await documents(api, ref)).values()
        await confirm_partition(api, ref, whole)

        async def divided(count):
            # While the first page is being read, the user divides the original into two documents: the whole is left out.
            a, b = await split(api, ref, 1, 1), await split(api, ref, 2, 2)
            await confirm_partition(api, ref, a, b)

        reader = Reader(after=divided)
        harness = Harness(reader=reader)
        process = await extract_all(ids["org"], harness, processor=extract_logical)
        assert process >= 1
        assert len([read for read in reader.reads if read.document_key == whole]) == 1, "the second page of the whole is not read"
        assert harness.extractor.calls == [] or all(call.document_key != whole for call in harness.extractor.calls)
        assert [row.pdf_page for row in await ledger(ids["org"]) if row.stage == "read_page"][:1] == [1]
        assert await processing(api, whole) == ("failed", "lease_lost", True)
        assert (await revision(ids, whole))[1] is None


async def test_the_decision_of_one_organization_changes_nothing_for_the_waiting_document_of_another(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    foreign = await foreign_fixture(migrator_session)
    enable_models(ids["org"])
    enable_models(foreign["org"])
    async with client(ids) as api, client(foreign) as other:
        own_ref = await receive_original(api, ids, document_pdf([FIRST, SECOND]), pages=2)
        foreign_ref = await receive_original(other, foreign, document_pdf([FIRST, SECOND]), pages=2)
        (own,), (theirs,) = (await documents(api, own_ref)).values(), (await documents(other, foreign_ref)).values()
        assert await extract_all(ids["org"], Harness(), processor=extract_logical) == 1
        assert await extract_all(foreign["org"], Harness(), processor=extract_logical) == 1
        await confirm_partition(api, own_ref, own)
        assert await processing(api, own) == ("queued", None, False)
        assert await processing(other, theirs) == ("awaiting_split", "awaiting_split", False), "the other organization is still waiting"
        assert (await job_state(foreign, theirs))["status"] == "awaiting_split"


@pytest.mark.parametrize("fmt,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg")], ids=["png", "jpeg"])
async def test_a_png_or_jpeg_original_is_read_by_the_reading_model_as_one_page(migrator_session, scope_seed, store, fmt, mime):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    data = picture(fmt)
    async with client(ids) as api:
        ref = await receive_original(api, ids, data, pages=1, mime=mime, filename="voucher." + fmt.lower())
        (document,) = (await documents(api, ref)).values()
        reader = Reader(transcripts={(document, 1): [ReadingBlock(kind="TEXT", text="Gesamtbetrag 312,00 EUR, Rechnungsnummer RE-2026-0042")]})
        harness = Harness(reader=reader)
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 1
    (image,) = reader.images
    assert (image.document_key, image.pdf_page, image.media_type) == (document, 1, "image/png")
    assert image.data.startswith(b"\x89PNG\r\n\x1a\n"), "the transports take PNG only"
    (call,) = harness.extractor.calls
    assert call.pages == (1,) and "RE-2026-0042" in call.text
    header, payload, evidence, pages = await revision(ids, document)
    assert (header["processor_version"], header["status"], payload["source_pages"]) == ("intake-model-v1", "succeeded", [1])
    assert [(page["number"], page["method"]) for page in pages] == [(1, "reading_model")]
    assert [(row.stage, row.pdf_page, row.model_name, row.status) for row in await ledger(ids["org"])] == [
        ("read_page", 1, READING_MODEL, "SETTLED"), ("extract_invoice", None, EXTRACTION_MODEL, "SETTLED")]


async def test_a_pdf_with_a_text_layer_needs_no_reading_model(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"], document_model=None)
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([FIRST, SECOND]), pages=2)
        (document,) = (await documents(api, ref)).values()
        await confirm_partition(api, ref, document)
        harness = Harness()
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 1
    assert harness.reader.reads == [] and [call.pages for call in harness.extractor.calls] == [(1, 2)]
    assert (await job_state(ids, document))["status"] == "succeeded"
    assert [(row.stage, row.status) for row in await ledger(ids["org"])] == [("extract_invoice", "SETTLED")]


async def test_a_structured_original_is_parsed_locally_and_makes_no_provider_call(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    harness = Harness()
    async with client(ids) as api:
        xml_ref = await receive_original(api, ids, UBL_WITH_IBAN, pages=None, mime="application/xml", filename="invoice.xml")
        (xml_document,) = (await documents(api, xml_ref)).values()
        pdf_ref = await receive_original(api, ids, zugferd_pdf(), pages=1)
        (pdf_document,) = (await documents(api, pdf_ref)).values()
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 2
    assert harness.built == [] and harness.reader.reads == [] and harness.extractor.calls == [], "no provider is even constructed"
    assert await ledger(ids["org"]) == []
    header, payload, evidence, _ = await revision(ids, xml_document)
    assert (header["processor_version"], header["status"], payload["source_format"], payload["vendor_iban"]) == ("intake-local-v1", "succeeded", "XRECHNUNG_UBL", PAYEE_IBAN)
    assert "xml_path" in set(evidence.values())
    header, payload, evidence, pages = await revision(ids, pdf_document)
    assert (header["processor_version"], header["status"], payload["source_format"], payload["source_pages"]) == ("intake-local-v1", "succeeded", "ZUGFERD", [1])
    assert set(evidence.values()) == {"unlocated"} and [page["method"] for page in pages] == ["text_layer"]


@pytest.mark.parametrize("setup,code", [
    (None, "ai_import_disabled"),
    ({"import_enabled": False}, "ai_import_disabled"),
    ({"text_version": "1"}, "ai_inbox_confirmation_missing"),
    ({"text_version": "2"}, "ai_inbox_confirmation_missing"),
    ({"document_model": None}, "ai_document_model_missing"),
    ({"key": "unreadable"}, "ai_key_unreadable"),
], ids=["no-settings", "switch-off", "confirmation-without-invoices", "confirmation-without-the-inbox", "no-reading-model", "unreadable-key"])
async def test_without_the_opt_in_nothing_is_sent_and_the_document_is_retained_for_the_manual_workflow(migrator_session, scope_seed, store, setup, code):
    ids = await fixture(migrator_session, scope_seed)
    if setup is not None:
        enable_models(ids["org"], **{key: value for key, value in setup.items() if key != "key"})
        if setup.get("key") == "unreadable":
            await mutate(text("UPDATE organization_ai_settings SET encrypted_api_key='not-a-token' WHERE organization_id=:org"), ids)
    data = document_pdf([None])
    async with client(ids) as api:
        ref = await receive_original(api, ids, data, pages=1)
        (document,) = (await documents(api, ref)).values()
        harness = Harness()
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 1
        status = (await api.get("/api/logical-documents/" + document + "/processing")).json()
        history = (await api.get("/api/document-refs/" + ref["id"] + "/extractions")).json()["items"]
    assert harness.built == [] and harness.reader.reads == [] and harness.extractor.calls == []
    assert await ledger(ids["org"]) == []
    assert (status["status"], status["error_code"], status["can_retry"]) == ("unavailable", code, True), "the established retry workflow stays open"
    assert [(row["status"], row["error_code"], row["processor_version"], row["logical_document_id"]) for row in history] == [("unavailable", code, "intake-local-v1", document)]
    assert await read_value("SELECT count(*) FROM intake_original WHERE organization_id=:org AND content_sha256=:hash",
                            {**ids, "hash": hashlib.sha256(data).hexdigest()}) == 1, "the original is retained"
    assert (await revision(ids, document))[1] is None, "no proposal exists"


@pytest.mark.parametrize("failing,code,calls", [("reader", "model_reading_failed", 1), ("extractor", "model_extraction_failed", 3)], ids=["reading", "extraction"])
async def test_a_provider_failure_retains_the_document_keeps_its_usage_and_sends_nothing_more(migrator_session, scope_seed, store, failing, code, calls):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    failure = ProviderFailure(PROVIDER_CONNECTION_ERROR, True, "synthetic outage", usage=ProviderUsage(40, 0, Decimal("0.00001"), 1))
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([None, None]), pages=2)
        (document,) = (await documents(api, ref)).values()
        await confirm_partition(api, ref, document)
        reader = Reader(failures={(document, 1): failure}) if failing == "reader" else Reader()
        extractor = FakeInvoiceExtractionProvider(failure=failure) if failing == "extractor" else FakeInvoiceExtractionProvider()
        assert await extract_all(ids["org"], Harness(reader=reader, extractor=extractor), processor=extract_logical) == 1
        status = (await api.get("/api/logical-documents/" + document + "/processing")).json()
    assert (status["status"], status["error_code"], status["can_retry"]) == ("failed", code, True)
    assert len(await ledger(ids["org"])) == calls and len(reader.reads) == (1 if failing == "reader" else 2)
    assert all(row.status == "SETTLED" for row in await ledger(ids["org"])), "a failed request keeps its usage"
    failed = (await ledger(ids["org"]))[-1]
    assert (failed.error_code, failed.input_tokens, failed.cost_usd) == (PROVIDER_CONNECTION_ERROR, 40, Decimal("0.00001"))
    assert (await revision(ids, document))[1] is None


async def test_a_request_that_never_left_the_host_is_settled_at_zero_ends_unreachable_and_is_not_on_the_usage_card(migrator_session, scope_seed, store):
    """The inbox path follows the invoice path (#833): a request the transport states as not sent consumed nothing."""
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    failure = ProviderFailure(PROVIDER_CONNECTION_ERROR, True, "synthetic: the connection was refused", not_sent=True)
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([None]), pages=1)
        (document,) = (await documents(api, ref)).values()
        reader = Reader(failures={(document, 1): failure})
        assert await extract_all(ids["org"], Harness(reader=reader), processor=extract_logical) == 1
        assert await processing(api, document) == ("failed", "provider_unreachable", True), "the same reason as the invoice path"
        (row,) = await ledger(ids["org"])
        assert (row.status, row.usage_complete, row.error_code) == ("SETTLED", True, "PROVIDER_REQUEST_NOT_SENT")
        assert (row.input_tokens, row.output_tokens, row.cost_usd) == (0, 0, Decimal("0"))
        async with get_worker_tenant_session(ids["org"]) as db:
            assert await OrganizationAiSettingsRepository(db, ids["org"]).monthly_usage() == Decimal("0")
        card = await api.get("/api/logical-documents/" + document + "/model-usage")
        assert card.status_code == 200 and card.json() == {"stages": [], "total": None}, "nothing was requested, so there is nothing to list"
        # A request that did leave the host and failed stays on the card at what it consumed.
        again = Reader(failures={(document, 1): ProviderFailure(PROVIDER_CONNECTION_ERROR, True, "synthetic outage", usage=ProviderUsage(40, 0, Decimal("0.00001"), 1))})
        fence = (await api.get("/api/logical-documents/" + document + "/processing")).json()["fence"]
        retried = await api.post("/api/logical-documents/" + document + "/retry-extraction", json={"expected_fence": fence, "reason": "Again"},
                                 headers={"Idempotency-Key": str(uuid.uuid4())})
        assert retried.status_code == 202, retried.text
        assert await extract_all(ids["org"], Harness(reader=again), processor=extract_logical) == 1
        (stage,) = (await api.get("/api/logical-documents/" + document + "/model-usage")).json()["stages"]
        assert (stage["stage"], stage["calls"]) == ("READING", 1)


async def test_both_stages_reserve_settle_and_count_towards_the_monthly_total(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    reading = ProviderUsage(1000, 200, Decimal("0.00165"), 1)
    extraction = ProviderUsage(2000, 100, Decimal("0.00085"), 1)
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([None, FIRST]), pages=2)
        (document,) = (await documents(api, ref)).values()
        await confirm_partition(api, ref, document)
        seen = []

        async def watch(_):
            # While the first request is out, its reservation is already durable and counts towards the month.
            rows = await ledger(ids["org"])
            async with get_worker_tenant_session(ids["org"]) as db:
                seen.append((len(rows), rows[0].status, await OrganizationAiSettingsRepository(db, ids["org"]).monthly_usage()))

        harness = Harness(reader=Reader(usage=reading, after=watch), extractor=FakeInvoiceExtractionProvider(usage=extraction))
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 1
        rows = await ledger(ids["org"])
        usage = (await api.get("/api/logical-documents/" + document + "/processing")).json()
    assert [(row.stage, row.pdf_page, row.status, row.usage_complete) for row in rows] == [("read_page", 1, "SETTLED", True), ("extract_invoice", None, "SETTLED", True)]
    assert all(row.intake_attempt_id is not None and row.attempt_id is None for row in rows), "the rows belong to the inbox attempt"
    assert [(row.input_tokens, row.output_tokens, row.cost_usd) for row in rows] == [(1000, 200, Decimal("0.00165")), (2000, 100, Decimal("0.00085"))]
    assert all(row.reserved_cost_usd > row.cost_usd for row in rows), "a reservation is the worst case, the settlement what was used"
    assert seen[0][:2] == (1, "RESERVED") and seen[0][2] >= rows[0].reserved_cost_usd
    async with get_worker_tenant_session(ids["org"]) as db:
        assert await OrganizationAiSettingsRepository(db, ids["org"]).monthly_usage() == Decimal("0.00250"), "both stages count"
    assert usage["status"] == "succeeded"
    # The usage of the document is readable for those who may read it, over all its attempts.
    listed = await read_value("SELECT intake_model_usage(:org,:document)", {**ids, "document": uuid.UUID(document)}, actor_key="clerk")
    assert [(row["stage"], row["model_name"], row["status"], row["cost_usd"]) for row in listed] == [
        ("read_page", READING_MODEL, "SETTLED", "0.0016500000"), ("extract_invoice", EXTRACTION_MODEL, "SETTLED", "0.0008500000")]


async def test_an_exhausted_monthly_budget_stops_before_the_next_request_and_retains_the_document(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([None, None]), pages=2)
        (document,) = (await documents(api, ref)).values()
        await confirm_partition(api, ref, document)

        async def limit_reached(count):
            enable_models(ids["org"], monthly_limit_usd=Decimal("0.0000001"))

        reader = Reader(after=limit_reached)
        harness = Harness(reader=reader)
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 1
        status = (await api.get("/api/logical-documents/" + document + "/processing")).json()
    assert (status["status"], status["error_code"], status["can_retry"]) == ("unavailable", "organization_ai_budget_exceeded", True)
    assert len(reader.reads) == 1 and harness.extractor.calls == [], "the request that would pass the limit is never sent"
    assert [(row.stage, row.status) for row in await ledger(ids["org"])] == [("read_page", "SETTLED")], "what was requested stays in the ledger"


async def test_withdrawing_the_opt_in_during_a_run_stops_it_before_the_next_request(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([None, None]), pages=2)
        (document,) = (await documents(api, ref)).values()
        await confirm_partition(api, ref, document)

        async def withdrawn(count):
            enable_models(ids["org"], import_enabled=False)

        reader = Reader(after=withdrawn)
        assert await extract_all(ids["org"], Harness(reader=reader), processor=extract_logical) == 1
        status = (await api.get("/api/logical-documents/" + document + "/processing")).json()
    assert (status["status"], status["error_code"]) == ("unavailable", "ai_import_disabled") and len(reader.reads) == 1


async def test_a_lost_fence_stops_further_spending_and_records_nothing(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([None, None, None]), pages=3)
        (document,) = (await documents(api, ref)).values()
        await confirm_partition(api, ref, document)
        taken = []

        async def reclaimed(count):
            # The lease ran out and another worker claimed the job: this run no longer holds the fence.
            await mutate(text("UPDATE intake_extraction_job SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE organization_id=:org"), ids)
            taken.append(await work_sql(ids, "SELECT claim_intake_extraction(:org)"))

        reader = Reader(after=reclaimed)
        harness = Harness(reader=reader)
        assert not await extract_all(ids["org"], harness, processor=extract_logical), "the stale run completes nothing"
    assert len(reader.reads) == 1 and harness.extractor.calls == [], "no second page is read, nothing is extracted"
    assert [(row.stage, row.pdf_page, row.status) for row in await ledger(ids["org"])] == [("read_page", 1, "SETTLED")]
    assert taken[0] is not None
    assert await read_value("SELECT count(*) FROM extraction_revision WHERE organization_id=:org", ids) == 0
    assert (await job_state(ids, document))["status"] == "running", "the live claim is the new worker's"


async def test_a_second_organization_sees_nothing_of_the_usage_or_the_proposal(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    foreign = await foreign_fixture(migrator_session)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([None]), pages=1)
        (document,) = (await documents(api, ref)).values()
        assert await extract_all(ids["org"], Harness(), processor=extract_logical) == 1
    assert len(await ledger(ids["org"])) == 2
    assert await ledger(foreign["org"]) == []
    own = await read_value("SELECT jsonb_array_length(intake_model_usage(:org,:document))", {**ids, "document": uuid.UUID(document)}, actor_key="clerk")
    assert own == 2
    # The foreign organization names the other's document: nothing is found, no matter whose context it works in.
    assert await read_value("SELECT intake_model_usage(:org,:document)", {"org": foreign["org"], "clerk": foreign["clerk"], "document": uuid.UUID(document)}, actor_key="clerk") is None
    assert await read_value("SELECT count(*) FROM extraction_revision WHERE organization_id=:org", foreign) == 0


async def test_more_pages_than_one_voucher_ends_for_review_before_any_request(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([FIRST] * 11), pages=11)
        (document,) = (await documents(api, ref)).values()
        await confirm_partition(api, ref, document)
        harness = Harness()
        assert await extract_all(ids["org"], harness, processor=extract_logical) == 1
        status = (await api.get("/api/logical-documents/" + document + "/processing")).json()
    assert (status["status"], status["error_code"], status["can_retry"]) == ("needs_review", "invoice_page_limit", False)
    assert harness.built == [] and await ledger(ids["org"]) == []


async def test_the_real_child_process_hands_the_selected_pages_over_and_the_worker_records_the_proposal(migrator_session, scope_seed, store):
    """No injected processor: the isolated child reads the text layers and the worker carries on with the models."""
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([FIRST, SECOND]), pages=2)
        first, second = await split(api, ref, 1, 1), await split(api, ref, 2, 2)
        await confirm_partition(api, ref, first, second)
        harness = Harness()
        assert await extract_all(ids["org"], harness) == 3, "the whole is looked at, found to be left out, and parked"
    assert {call.pages for call in harness.extractor.calls} == {(1,), (2,)}
    header, payload, _, pages = await revision(ids, first)
    assert (header["processor_version"], payload["source_pages"], [page["text"] for page in pages]) == ("intake-model-v1", [1], ["\n".join(FIRST)])
