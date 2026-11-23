"""The inbox path of an acceptance instance: fixtures, the local stage, the model stage, the status and the cleanup (issue #862).

The command's refusals need no model, no network and no real acceptance instance: the instance is the sandbox of the invoice
command's tests (the same lock directories, scratch directory and live process, in the form ``scripts/acceptance-env.sh``
leaves them). The runs against the test database upload real originals through the API as a tester's browser does, run the
command in this process and read the result back through the product.
"""
import asyncio
import hashlib
import uuid

import psycopg2
import pytest

from app.core.intake_extraction import UNSTRUCTURED, extract_logical
from tests import offline_inbox_processing as inbox
from tests import offline_invoice_processing as invoice
from tests.conftest import MIGRATOR_SYNC_URL
from tests.intake_model_support import confirm_partition
from tests.invoice_model_support import no_invoice_model_data as no_invoice_model_data
from tests.test_financial_scope_boundary import scope_seed as scope_seed, worker_tenant as worker_tenant
from tests.test_invoice_source_custody import fixture
from tests.test_offline_invoice_processing import (
    _answer, _configure_as_the_instance_does, _listening, _use_store, instance as instance, lock_dir as lock_dir, stub_port as stub_port,
)
from tests.test_scope_payment_reconciliation import read_value
from tests.test_scoped_account_statements import client

ORGANIZATION_FORMS = [["local", "--organization", "Anybody"], ["process", "--organization", "Anybody"],
                      ["process", "--organization", "Anybody", "--fail-extraction"], ["status", "--organization", "Anybody"]]
FORMS = [["fixtures"], ["stub", "start"], ["stub", "stop"], *ORGANIZATION_FORMS, ["discard"]]


def _run(arguments, capsys):
    status = inbox.main(arguments)
    captured = capsys.readouterr()
    return status, captured.out, captured.err


async def _arun(arguments, capsys):
    """The command as a session runs it: it starts its own event loop, so not on the one of the test."""
    status = await asyncio.to_thread(inbox.main, arguments)
    captured = capsys.readouterr()
    return status, captured.out, captured.err


# ---- refusals: no model, no network ------------------------------------------------------------------------------------

@pytest.mark.no_db
@pytest.mark.parametrize("arguments", FORMS, ids=[" ".join(arguments) for arguments in FORMS])
def test_every_form_refuses_without_a_running_acceptance_instance(arguments, lock_dir, capsys):
    (lock_dir / "info").unlink()  # released the way `acceptance-env.sh stop` releases it
    lock_dir.rmdir()
    status, out, err = _run(arguments, capsys)
    assert status == 1 and out == ""
    assert "offline_inbox_processing: no acceptance instance is running" in err


@pytest.mark.no_db
@pytest.mark.parametrize("arguments", [ORGANIZATION_FORMS[0], ORGANIZATION_FORMS[1], ORGANIZATION_FORMS[3], ["discard"]], ids=["local", "process", "status", "discard"])
def test_the_database_forms_need_the_test_database_lock_of_this_instance(arguments, instance, capsys):
    (invoice.TESTDB_LOCK / "info").write_text("ffffffff 2026-11-23T10:00:00Z another-run\n")
    status, out, err = _run(arguments, capsys)
    assert status == 1 and out == "" and "not held by this acceptance instance" in err
    assert (invoice.TESTDB_LOCK / "info").exists()


@pytest.mark.parametrize("form", ["local", "process", "status"])
async def test_an_unknown_organization_is_refused(form, instance, capsys):
    for unknown in (f"No such organization {uuid.uuid4()}", str(uuid.uuid4())):
        status, out, err = await _arun([form, "--organization", unknown], capsys)
        assert status == 1 and out == "" and "organization" in err, err


@pytest.mark.no_db
def test_the_fixtures_are_refused_when_the_frontend_was_not_built(instance, capsys):
    (instance.scratch / "dist" / "assets").rmdir()
    status, out, err = _run(["fixtures"], capsys)
    assert status == 1 and "dist/assets" in err


async def test_an_organization_with_nothing_to_do_is_refused_by_every_form(instance, scope_seed, migrator_session, capsys):
    organization = str((await fixture(migrator_session, scope_seed))["org"])
    for form, message in (("local", "nothing is waiting"), ("process", "nothing is waiting"), ("status", "no inbox batch")):
        status, out, err = await _arun([form, "--organization", organization], capsys)
        assert status == 1 and out == "" and message in err, (form, err)


# ---- fixtures ----------------------------------------------------------------------------------------------------------

@pytest.mark.no_db
def test_the_fixtures_are_the_inbox_samples_and_the_page_script_hands_them_to_the_inbox(instance, capsys):
    status, out, err = _run(["fixtures"], capsys)
    assert status == 0, err
    names = [inbox.SCAN, inbox.SECOND_SCAN, inbox.FOUR_PAGES, inbox.XRECHNUNG]
    assets = instance.scratch / "dist" / "assets"
    assert sorted(path.name for path in assets.iterdir()) == sorted(names)
    assert all(f"/assets/{name}" in out for name in names)
    # The page script: defines the hand-over for the form of /posteingang, and the call for each sample is printed after it.
    assert "async function handIn(" in out and 'aria-label="Neuer Eingang"' in out and "DataTransfer" in out and "Stapel einreichen" in out
    assert all(f'await handIn("{name}")' in out for name in names)

    def read(name, mime, pages):
        return extract_logical((assets / name).read_bytes(), mime, None if pages is None else 1, pages, pages)

    # A scan: one page and no text layer of its own, so that the reading model has to read it; the second one is no duplicate.
    scans = [(assets / name).read_bytes() for name in (inbox.SCAN, inbox.SECOND_SCAN)]
    assert scans[0] != scans[1]
    for name in (inbox.SCAN, inbox.SECOND_SCAN):
        answer = read(name, "application/pdf", 1)
        assert answer["status"] == UNSTRUCTURED and [bool(page["text"].strip()) for page in answer["pages"]] == [False]
    # The four-page upload: a text layer on pages 1 and 2, pictures on pages 3 and 4.
    answer = read(inbox.FOUR_PAGES, "application/pdf", 4)
    assert answer["status"] == UNSTRUCTURED and [bool(page["text"].strip()) for page in answer["pages"]] == [True, True, False, False]
    # The e-invoice is read directly, without a model.
    answer = read(inbox.XRECHNUNG, "application/xml", None)
    assert answer["status"] != UNSTRUCTURED and answer["payload"]["gross_amount"] == "119.00"


# ---- the model-list stub (the invoice command's) ----------------------------------------------------------------------

@pytest.mark.no_db
def test_the_stub_is_the_invoice_commands_and_is_started_and_stopped_from_here(instance, stub_port, capsys):
    status, out, err = _run(["stub", "start"], capsys)
    assert status == 0, err
    assert f"http://127.0.0.1:{stub_port}/v1" in out
    code, body = _answer(stub_port)
    assert code == 200 and sorted(item["id"] for item in body["data"]) == ["gpt-5.4-mini", "gpt-5.6-luna"]
    status, out, err = _run(["stub", "stop"], capsys)
    assert status == 0, err
    assert not _listening(stub_port)


# ---- the stages against the test database -----------------------------------------------------------------------------

def _mime(name):
    return "application/xml" if name.endswith(".xml") else "application/pdf"


async def _submit(api, instance, *names, tampered=None):
    """The tester's „Stapel einreichen“: one batch with the named sample documents, through the API; the original ids by name."""
    documents = {**inbox.fixture_documents(), **(tampered or {})}
    items = [{"client_item_key": f"document-{number}", "filename": name, "mime_type": _mime(name),
              "content_sha256": hashlib.sha256(documents[name]).hexdigest(), "content_byte_size": len(documents[name])}
             for number, name in enumerate(names, 1)]
    created = await api.post("/api/intake-batches", json={"items": items}, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert created.status_code == 201, created.text
    originals = {}
    for number, name in enumerate(names, 1):
        _use_store(instance)
        uploaded = await api.post(f"/api/intake-batches/{created.json()['id']}/items", data={"client_item_key": f"document-{number}"},
                                  files={"file": (name, documents[name], _mime(name))})
        assert uploaded.status_code == 202, uploaded.text
        originals[name] = uploaded.json()["original_id"]
    return originals


async def _documents(api, original):
    """The reference of an original and its logical documents by page range."""
    ref = (await api.get("/api/document-refs/page", params={"source_id": original})).json()["items"][0]
    items = (await api.get("/api/logical-documents", params={"document_ref_id": ref["id"]})).json()["items"]
    return ref, {(item["page_start"], item["page_end"]): item["id"] for item in items}


def _model_rows():
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT (SELECT count(*) FROM invoice_model_call),"
                       " (SELECT count(*) FROM extraction_revision WHERE processor_version IN ('invoice-model-v1', 'intake-model-v1')),"
                       " (SELECT count(*) FROM field_evidence WHERE method = 'reading_model')")
        return cursor.fetchone()


def _lines(out, prefix):
    return [line for line in out.splitlines() if line.startswith(prefix)]


async def _proposal(organization, document):
    return await read_value("SELECT payload FROM extraction_revision WHERE organization_id=:org AND logical_document_id=:document",
                            {"org": uuid.UUID(organization), "document": uuid.UUID(document)})


async def test_an_organization_without_ai_setup_ends_a_scan_with_the_products_outcome_and_no_model_is_asked(
        instance, scope_seed, migrator_session, no_invoice_model_data, capsys):
    ids = await fixture(migrator_session, scope_seed)
    organization = str(ids["org"])
    async with client(ids) as api:
        original = (await _submit(api, instance, inbox.SCAN))[inbox.SCAN]
        status, out, err = await _arun(["local", "--organization", organization], capsys)
        assert status == 0, err
        status, out, err = await _arun(["process", "--organization", organization], capsys)
        assert status == 0, err
        _ref, documents = await _documents(api, original)
        (scan,) = documents.values()
        assert _lines(out, "document ") == [f"document {scan} (pages 1-1) attempt 1: unavailable (ai_import_disabled); no model asked"], out
        assert _model_rows() == (0, 0, 0)


async def test_the_local_stage_stands_in_for_the_scanner_only(instance, scope_seed, migrator_session, no_invoice_model_data, capsys):
    ids = await fixture(migrator_session, scope_seed)
    organization = str(ids["org"])
    async with client(ids) as api:
        # Not a PDF, whatever its name says: the stand-in answers for the malware scanner, not for the inspection's own checks.
        await _submit(api, instance, inbox.SCAN, inbox.FOUR_PAGES, tampered={inbox.SCAN: b"this is not a pdf"})
        status, out, err = await _arun(["local", "--organization", organization], capsys)
        assert status == 0, err
        assert "inspection: 2 originals" in out and "delivery: 2 originals" in out, out
        status, out, err = await _arun(["status", "--organization", organization], capsys)
        assert status == 0, err
        assert f"{inbox.SCAN}: inspection rejected (mime_mismatch)" in out and f"{inbox.FOUR_PAGES}: inspection clean" in out, out


async def test_the_inbox_is_processed_stage_by_stage_the_division_is_awaited_and_the_cleanup_leaves_no_model_row(
        instance, scope_seed, migrator_session, no_invoice_model_data, monkeypatch, capsys):
    ids = await fixture(migrator_session, scope_seed)
    organization = str(ids["org"])
    _configure_as_the_instance_does(ids["org"], monkeypatch)
    async with client(ids) as api:
        originals = await _submit(api, instance, inbox.SCAN, inbox.XRECHNUNG, inbox.FOUR_PAGES)
        status, out, err = await _arun(["local", "--organization", organization], capsys)
        assert status == 0, err
        assert "inspection: 3 originals" in out and "delivery: 3 originals" in out, out

        # The model stage: the scan is read and extracted, the e-invoice is read directly, and the four-page upload waits.
        status, out, err = await _arun(["process", "--organization", organization], capsys)
        assert status == 0, err
        scan_ref, scan_documents = await _documents(api, originals[inbox.SCAN])
        xml_ref, xml_documents = await _documents(api, originals[inbox.XRECHNUNG])
        ref, documents = await _documents(api, originals[inbox.FOUR_PAGES])
        (scan,), (xml,), whole = scan_documents.values(), xml_documents.values(), documents[(1, 4)]
        assert _lines(out, "document ") == [
            f"document {scan} (pages 1-1) attempt 1: succeeded; reading gpt-5.4-mini (page 1); extraction gpt-5.6-luna (pages 1)",
            f"document {xml} (whole document) attempt 1: succeeded; no model asked",
            f"document {whole} (pages 1-4) attempt 1: awaiting_split; no model asked"], out
        proposal = await _proposal(organization, scan)
        assert (proposal["vendor_name"], proposal["document_number"], proposal["invoice_date"], proposal["gross_amount"]) == (
            "Mustermann Versicherungen AG", "RE-2026-0042", "2026-01-15", "312.00")
        assert (await _proposal(organization, xml))["gross_amount"] == "119.00"
        assert _model_rows()[0] == 2, "only the scan was sent: one reading and one extraction request"
        status, out, err = await _arun(["process", "--organization", organization], capsys)
        assert status == 1 and "nothing is waiting" in err, "the four-page upload is parked until somebody divides it"

        # The tester divides the four-page upload into its two documents and confirms the division; the stage runs again.
        first = (await api.post("/api/logical-documents", json={"document_ref_id": ref["id"], "page_start": 1, "page_end": 2, "reason": "Two vouchers"},
                                headers={"Idempotency-Key": str(uuid.uuid4())})).json()["id"]
        second = (await api.post("/api/logical-documents", json={"document_ref_id": ref["id"], "page_start": 3, "page_end": 4, "reason": "Two vouchers"},
                                 headers={"Idempotency-Key": str(uuid.uuid4())})).json()["id"]
        await confirm_partition(api, ref, first, second)
        status, out, err = await _arun(["process", "--organization", organization], capsys)
        assert status == 0, err
        assert _lines(out, "document ") == [
            f"document {first} (pages 1-2) attempt 1: succeeded; extraction gpt-5.6-luna (pages 1,2)",
            f"document {second} (pages 3-4) attempt 1: succeeded; reading gpt-5.4-mini (page 3), gpt-5.4-mini (page 4); "
            "extraction gpt-5.6-luna (pages 3,4)"], out
        text_pages, scanned_pages = await _proposal(organization, first), await _proposal(organization, second)
        assert (text_pages["vendor_name"], text_pages["gross_amount"]) == ("Mustermann Versicherungen AG", "312.00")
        assert (scanned_pages["vendor_name"], scanned_pages["gross_amount"]) == ("Stadtwerke Test GmbH", "99.00")

        # The status lists the batch and every document with its stage.
        status, out, err = await _arun(["status", "--organization", organization], capsys)
        assert status == 0, err
        assert f"{inbox.FOUR_PAGES}: inspection clean" in out and f"document {whole} (pages 1-4): extraction awaiting_split" in out, out
        assert f"document {second} (pages 3-4): extraction succeeded" in out, out

        # The cleanup the launching session runs before `acceptance-env.sh stop`.
        assert all(_model_rows()), "the run left metering rows, model-read proposals and the passages they were read from"
        status, out, err = await _arun(["discard"], capsys)
        assert status == 0, err
        assert _model_rows() == (0, 0, 0)


async def test_a_failing_extraction_hits_the_oldest_waiting_scan_and_keeps_the_original(
        instance, scope_seed, migrator_session, no_invoice_model_data, monkeypatch, capsys):
    ids = await fixture(migrator_session, scope_seed)
    organization = str(ids["org"])
    _configure_as_the_instance_does(ids["org"], monkeypatch)
    async with client(ids) as api:
        first = (await _submit(api, instance, inbox.SCAN))[inbox.SCAN]
        assert (await _arun(["local", "--organization", organization], capsys))[0] == 0
        assert (await _arun(["process", "--organization", organization], capsys))[0] == 0
        # The first scan is done; the second is the oldest waiting one when the extraction is made to fail.
        second = (await _submit(api, instance, inbox.SECOND_SCAN))[inbox.SECOND_SCAN]
        assert (await _arun(["local", "--organization", organization], capsys))[0] == 0
        status, out, err = await _arun(["process", "--organization", organization, "--fail-extraction"], capsys)
        assert status == 0, err
        (_ref, first_documents), (_ref, second_documents) = await _documents(api, first), await _documents(api, second)
        (done,), (failed,) = first_documents.values(), second_documents.values()
        (line,) = _lines(out, "document ")
        assert line.startswith(f"document {failed} (pages 1-1) attempt 1: failed (") and "reading gpt-5.4-mini (page 1)" in line and "extraction gpt-5.6-luna" in line, out
        assert (await api.get(f"/api/logical-documents/{done}/processing")).json()["status"] == "succeeded"
        processing = (await api.get(f"/api/logical-documents/{failed}/processing")).json()
        assert processing["status"] == "failed" and processing["can_retry"] is True
        # The attempt is recorded as failed and proposes nothing.
        assert await read_value("SELECT jsonb_agg(jsonb_build_object('status', status, 'payload', payload)) FROM extraction_revision"
                                " WHERE organization_id=:org AND logical_document_id=:document",
                                {"org": ids["org"], "document": uuid.UUID(failed)}) == [{"status": "failed", "payload": None}]
        assert _model_rows()[0] > 0
        assert (await _arun(["discard"], capsys))[0] == 0 and _model_rows() == (0, 0, 0)
