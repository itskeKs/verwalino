"""The invoice path of an acceptance instance: fixtures, the model-list stub, the offline worker and the cleanup (issue #829).

The command's refusals need no model, no network and no real acceptance instance: the instance is a sandbox of the same
shape as the one ``scripts/acceptance-env.sh`` records (lock directory, scratch directory, a live process). The run against
the test database queues real uploads through the API, runs the command in this process and reads the proposals back.
"""
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

import psycopg2
import pytest

from app.core.config import settings
from app.core.invoice_pdf import inspect_invoice
from app.core.storage import LocalFsDocumentStore, set_store
from tests import offline_invoice_processing as offline
from tests.ai_settings_support import configure_ai
from tests.conftest import MIGRATOR_SYNC_URL
from tests.invoice_model_support import no_invoice_model_data as no_invoice_model_data
from tests.test_invoice_extraction_worker import worker_tenant as worker_tenant

INFO = "0123abcd 2026-11-23T09:00:00Z acceptance-env"
SCAN, SECOND_SCAN, XRECHNUNG, ZUGFERD = "versicherung-2026.pdf", "versicherung-2026-b.pdf", "versicherung-xrechnung.xml", "versicherung-zugferd.pdf"


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """The two lock directories of a run, at the sandbox, in the form ``scripts/acceptance-env.sh start`` leaves them."""
    acceptance, testdb = tmp_path / "acceptance.lock", tmp_path / "testdb.lock"
    for directory in (acceptance, testdb):
        directory.mkdir()
        (directory / "info").write_text(INFO + "\n")
    monkeypatch.setattr(offline, "ACCEPTANCE_LOCK", acceptance)
    monkeypatch.setattr(offline, "TESTDB_LOCK", testdb)
    return acceptance


def _record(lock_dir, scratch, pid, started, command):
    (lock_dir / "instance").write_text(f"scratch={scratch}\npid={pid}\nstarted={started}\ncommand={command}\n")


@pytest.fixture
def instance(tmp_path, lock_dir):
    """A running instance: a scratch directory with the marker, the built frontend's assets and the document store, and
    a live app process (this one) that the record names by start time and command line."""
    scratch = tmp_path / "verwalino-acceptance.aBc123"
    (scratch / "dist" / "assets").mkdir(parents=True)
    (scratch / "documents").mkdir()
    (scratch / ".acceptance-env").touch()
    _record(lock_dir, scratch, os.getpid(), *offline.process_identity(os.getpid()))
    return offline.running_instance()


def _run(arguments, capsys):
    status = offline.main(arguments)
    captured = capsys.readouterr()
    return status, captured.out, captured.err


# ---- refusals: no model, no network ------------------------------------------------------------------------------------

COMMANDS = [["fixtures"], ["stub", "start"], ["stub", "stop"], ["process", "--organization", "Anybody"], ["process", "--organization", "Anybody", "--fail-extraction"], ["discard"]]


@pytest.mark.no_db
@pytest.mark.parametrize("arguments", COMMANDS, ids=[" ".join(arguments) for arguments in COMMANDS])
def test_every_command_refuses_without_a_running_acceptance_instance(arguments, lock_dir, capsys):
    (lock_dir / "info").unlink()  # released the way `acceptance-env.sh stop` releases it
    lock_dir.rmdir()
    status, out, err = _run(arguments, capsys)
    assert status == 1 and out == ""
    assert "no acceptance instance is running" in err


@pytest.mark.no_db
def test_a_lock_that_acceptance_env_does_not_hold_is_not_touched(lock_dir, capsys):
    (lock_dir / "info").write_text("0123abcd 2026-11-23T09:00:00Z some-other-tool\n")
    status, out, err = _run(["fixtures"], capsys)
    assert status == 1 and "not held by scripts/acceptance-env.sh" in err
    assert (lock_dir / "info").exists()


@pytest.mark.no_db
def test_an_instance_that_is_not_fully_started_is_refused(lock_dir, capsys):
    status, out, err = _run(["fixtures"], capsys)
    assert status == 1 and "not fully started" in err


@pytest.mark.no_db
def test_an_instance_whose_app_is_gone_or_replaced_is_refused(instance, lock_dir, capsys):
    started, command = offline.process_identity(os.getpid())
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    # The record names a process that has ended, ...
    _record(lock_dir, instance.scratch, finished.pid, started, command)
    status, out, err = _run(["fixtures"], capsys)
    assert status == 1 and "app of this acceptance instance is not running" in err
    # ... and then a live process that is not the one the script started (same number, other command line).
    _record(lock_dir, instance.scratch, os.getpid(), started, command + " --not-the-recorded-one")
    status, out, err = _run(["fixtures"], capsys)
    assert status == 1 and "app of this acceptance instance is not running" in err
    assert not any((instance.scratch / "dist" / "assets").iterdir())


@pytest.mark.no_db
def test_a_scratch_directory_without_the_marker_is_refused(instance, lock_dir, capsys):
    (instance.scratch / ".acceptance-env").unlink()
    status, out, err = _run(["fixtures"], capsys)
    assert status == 1 and "not a scratch directory of acceptance-env.sh" in err
    assert not any((instance.scratch / "dist" / "assets").iterdir())


@pytest.mark.no_db
@pytest.mark.parametrize("arguments", [["process", "--organization", "Anybody"], ["discard"]], ids=["process", "discard"])
def test_the_database_commands_need_the_test_database_lock_of_this_instance(arguments, instance, capsys):
    (offline.TESTDB_LOCK / "info").write_text("ffffffff 2026-11-23T10:00:00Z another-run\n")
    status, out, err = _run(arguments, capsys)
    assert status == 1 and "not held by this acceptance instance" in err
    assert (offline.TESTDB_LOCK / "info").exists()


def test_an_unknown_organization_is_refused(instance, capsys):
    for unknown in (f"No such organization {uuid.uuid4()}", str(uuid.uuid4())):
        status, out, err = _run(["process", "--organization", unknown], capsys)
        assert status == 1 and out == "" and "organization" in err, err


async def test_an_organization_without_ai_setup_ends_each_scan_with_the_products_outcome_and_no_model_is_asked(
        instance, worker_tenant, no_invoice_model_data, monkeypatch, capsys):
    client, seed = worker_tenant
    organization = seed["org_a_id"]
    asked = []  # a provider the command built: the product builds none while the organization may not send a scan anywhere
    reader, extractor = offline._printed_invoice, offline.FakeInvoiceExtractionProvider
    monkeypatch.setattr(offline, "_printed_invoice", lambda: asked.append("reader") or reader())
    monkeypatch.setattr(offline, "FakeInvoiceExtractionProvider", lambda *a, **kw: asked.append("extractor") or extractor(*a, **kw))

    async def processed(name, variant, state):
        _use_store(instance)
        scan = await _upload(client, name, "application/pdf", offline.scan(variant))
        status = await asyncio.to_thread(offline.main, ["process", "--organization", str(organization)])
        out = capsys.readouterr().out
        # The record line shows the product's outcome and that no model was asked; the original stays.
        assert status == 0, f"{state}: {out}"
        assert [line for line in out.splitlines() if line.startswith("invoice ")] == [
            f"invoice {scan} attempt 1: unavailable (ai_import_disabled); no model asked"], f"{state}: {out}"
        invoice = await _invoice(client, scan)
        assert (invoice["processing_status"], invoice["processing_error_code"]) == ("unavailable", "ai_import_disabled"), state
        _use_store(instance)
        assert (await client.get(f"/api/invoice-documents/{scan}/content")).content.startswith(b"%PDF"), state
        assert asked == [] and _model_rows() == (0, 0), state

    await processed(SCAN, 0, "no AI settings at all")
    configure_ai(organization, import_enabled=False, qa_enabled=False)
    await processed(SECOND_SCAN, 1, "a saved key with the switch off")
    # The third state, the switch on without a key, cannot be reached: the settings page refuses to switch the import on
    # before a key is saved, so there is no organization in it to process.
    refused = await client.put(f"/api/organizations/{organization}/ai-settings", json={"model": "gpt-5.6-luna", "import_enabled": True, "api_key": None})
    assert refused.status_code == 422 and "API-Schlüssel" in refused.json()["detail"]


async def test_nothing_waiting_is_refused(worker_tenant, no_invoice_model_data):
    _client, seed = worker_tenant
    configure_ai(seed["org_a_id"])
    with pytest.raises(offline.Refusal, match="nothing is waiting"):
        await offline.process_waiting(seed["org_a_id"])


async def test_an_installation_that_switched_invoice_processing_off_is_refused(worker_tenant, monkeypatch):
    _client, seed = worker_tenant
    configure_ai(seed["org_a_id"])
    monkeypatch.setattr(settings, "invoice_extraction_enabled", False)
    with pytest.raises(offline.Refusal, match="HV_INVOICE_EXTRACTION_ENABLED"):
        await offline.process_waiting(seed["org_a_id"])


# ---- fixtures ----------------------------------------------------------------------------------------------------------

@pytest.mark.no_db
def test_the_fixtures_are_the_twins_documents_and_a_second_scan_that_is_no_duplicate(instance, capsys):
    status, out, err = _run(["fixtures"], capsys)
    assert status == 0, err
    assets = instance.scratch / "dist" / "assets"
    assert sorted(path.name for path in assets.iterdir()) == sorted([SCAN, SECOND_SCAN, XRECHNUNG, ZUGFERD])
    assert all(f"/assets/{name}" in out for name in (SCAN, SECOND_SCAN, XRECHNUNG, ZUGFERD))
    scans = [(assets / name).read_bytes() for name in (SCAN, SECOND_SCAN)]
    assert scans[0] != scans[1]
    for data in scans:
        inspected = inspect_invoice(data, "application/pdf")
        # A scan: one page and no text layer of its own, so that the reading model has to read it.
        assert inspected["status"] == "unstructured" and inspected["page_count"] == 1 and not inspected["pages"][0]["text"].strip()
    assert inspect_invoice((assets / XRECHNUNG).read_bytes(), "application/xml")["payload"]["gross_amount"] == "312.00"
    assert inspect_invoice((assets / ZUGFERD).read_bytes(), "application/pdf")["payload"]["gross_amount"] == "312.00"


@pytest.mark.no_db
def test_the_fixtures_are_refused_when_the_frontend_was_not_built(instance, capsys):
    (instance.scratch / "dist" / "assets").rmdir()
    status, out, err = _run(["fixtures"], capsys)
    assert status == 1 and "dist/assets" in err


# ---- the model-list stub -----------------------------------------------------------------------------------------------

def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _answer(port, path="/v1/models"):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
        return response.status, json.loads(response.read())


def _listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _stop_if_alive(instance):
    """Whatever a failing test left: the stub of its record, ended by the record's PID."""
    record = instance.scratch / offline.STUB_RECORD
    if record.exists():
        pid = json.loads(record.read_text())["pid"]
        if offline.process_alive(pid):
            os.killpg(pid, signal.SIGKILL)


@pytest.fixture
def stub_port(instance, monkeypatch):
    port = _free_port()
    monkeypatch.setattr(offline, "STUB_PORT", port)
    yield port
    _stop_if_alive(instance)


@pytest.mark.no_db
def test_the_stub_lists_the_two_models_on_127_0_0_1_and_stops_by_its_record(instance, stub_port, capsys):
    status, out, err = _run(["stub", "start"], capsys)
    assert status == 0, err
    assert f"http://127.0.0.1:{stub_port}/v1" in out
    code, body = _answer(stub_port)
    assert code == 200 and sorted(item["id"] for item in body["data"]) == ["gpt-5.4-mini", "gpt-5.6-luna"]
    pid = json.loads((instance.scratch / offline.STUB_RECORD).read_text())["pid"]
    assert offline.process_alive(pid)
    # A second start is refused while the first one runs; nothing else listens.
    status, out, err = _run(["stub", "start"], capsys)
    assert status == 1 and "already running" in err
    status, out, err = _run(["stub", "stop"], capsys)
    assert status == 0, err
    assert not offline.process_alive(pid) and not _listening(stub_port)
    assert not (instance.scratch / offline.STUB_RECORD).exists()


@pytest.mark.no_db
def test_the_stub_is_refused_on_a_port_that_is_in_use(instance, stub_port, capsys):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", stub_port))
        taken.listen()
        status, out, err = _run(["stub", "start"], capsys)
    assert status == 1 and f"port {stub_port}" in err and not (instance.scratch / offline.STUB_RECORD).exists()


@pytest.mark.no_db
def test_stop_signals_only_the_process_it_started(instance, stub_port, capsys):
    status, out, err = _run(["stub", "start"], capsys)
    assert status == 0, err
    record = instance.scratch / offline.STUB_RECORD
    recorded = json.loads(record.read_text())
    # The number now belongs to another process: this one, which stop must not signal.
    record.write_text(json.dumps({**recorded, "pid": os.getpid()}))
    status, out, err = _run(["stub", "stop"], capsys)
    assert status == 1 and "is not the stub this command started" in err
    assert _listening(stub_port)  # the real stub was neither signalled nor replaced
    record.write_text(json.dumps(recorded))
    assert _run(["stub", "stop"], capsys)[0] == 0 and not _listening(stub_port)


@pytest.mark.no_db
def test_stop_without_a_recorded_stub_says_so(instance, capsys):
    status, out, err = _run(["stub", "stop"], capsys)
    assert status == 1 and "no model-list stub is recorded" in err


@pytest.mark.no_db
def test_the_stub_ends_by_itself_when_the_instance_is_gone(instance, stub_port, lock_dir, capsys):
    assert _run(["stub", "start"], capsys)[0] == 0
    pid = json.loads((instance.scratch / offline.STUB_RECORD).read_text())["pid"]
    (lock_dir / "instance").unlink()  # what `acceptance-env.sh stop` does to the record of the run
    deadline = time.monotonic() + 10
    while offline.process_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not offline.process_alive(pid) and not _listening(stub_port)


# ---- the worker against the test database ------------------------------------------------------------------------------

def _use_store(instance):
    set_store(LocalFsDocumentStore(str(instance.scratch / "documents")))


async def _upload(client, name, mime, data):
    answer = await client.post("/api/invoice-documents/upload", files={"file": (name, data, mime)})
    assert answer.status_code == 201, answer.text
    return answer.json()["id"]


async def _invoice(client, invoice_id):
    return (await client.get(f"/api/invoice-documents/{invoice_id}")).json()


def _model_rows():
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT (SELECT count(*) FROM invoice_model_call),"
                       " (SELECT count(*) FROM extraction_revision WHERE processor_version = 'invoice-model-v1')")
        return cursor.fetchone()


def _configure_as_the_instance_does(organization, monkeypatch):
    """The key as an instance with its own session secret stored it: this process's settings cannot decrypt it."""
    with monkeypatch.context() as other_instance:
        other_instance.setattr(settings, "session_secret", "the-random-secret-of-another-instance")
        configure_ai(organization)


async def test_the_command_processes_the_queue_with_the_twins_providers_and_the_cleanup_leaves_no_model_row(
        instance, worker_tenant, no_invoice_model_data, monkeypatch, capsys):
    client, seed = worker_tenant
    organization = seed["org_a_id"]
    _configure_as_the_instance_does(organization, monkeypatch)
    _use_store(instance)
    documents = offline.fixture_documents()
    scan = await _upload(client, SCAN, "application/pdf", documents[SCAN])
    xrechnung = await _upload(client, XRECHNUNG, "application/xml", documents[XRECHNUNG])

    status = await asyncio.to_thread(offline.main, ["process", "--organization", str(organization)])
    out = capsys.readouterr().out
    assert status == 0, out
    lines = [line for line in out.splitlines() if line.startswith("invoice ")]
    assert len(lines) == 2
    # One line per attempt: the invoice, what each stage was asked and the outcome (the oldest waiting attempt first).
    assert lines[0].startswith(f"invoice {scan} attempt 1: succeeded") and "reading gpt-5.4-mini (page 1)" in lines[0] and "extraction gpt-5.6-luna" in lines[0]
    assert lines[1].startswith(f"invoice {xrechnung} attempt 1: succeeded") and "no model asked" in lines[1]
    proposal = (await _invoice(client, scan))["extraction_payload"]
    assert (proposal["vendor_name"], proposal["document_number"], proposal["invoice_date"], proposal["gross_amount"], proposal["vendor_iban"]) == (
        "Musterversicherung AG", "RE-2026-0042", "2026-01-15", "312.00", "DE02120300000000202051")
    structured = (await _invoice(client, xrechnung))["extraction_payload"]
    assert (structured["vendor_name"], structured["gross_amount"], structured["vendor_iban"]) == ("Musterversicherung AG", "312.00", "DE02120300000000202051")
    assert _model_rows()[0] > 0
    assert await asyncio.to_thread(offline.main, ["process", "--organization", str(organization)]) == 1  # nothing waits any more
    assert "nothing is waiting" in capsys.readouterr().err

    # The cleanup the launching session runs before `acceptance-env.sh stop`.
    assert offline.main(["discard"]) == 0
    assert _model_rows() == (0, 0)


async def test_a_failing_extraction_hits_the_oldest_waiting_scan_and_keeps_the_original(
        instance, worker_tenant, no_invoice_model_data, monkeypatch, capsys):
    client, seed = worker_tenant
    organization = str(seed["org_a_id"])
    _configure_as_the_instance_does(seed["org_a_id"], monkeypatch)
    documents = offline.fixture_documents()
    _use_store(instance)
    first = await _upload(client, SCAN, "application/pdf", documents[SCAN])
    assert await asyncio.to_thread(offline.main, ["process", "--organization", organization]) == 0
    capsys.readouterr()
    # The first scan is done; the second is the oldest waiting one when the extraction is made to fail. The command
    # leaves no store of its own behind, so this process sets its own again for the upload.
    _use_store(instance)
    second = await _upload(client, SECOND_SCAN, "application/pdf", documents[SECOND_SCAN])
    assert await asyncio.to_thread(offline.main, ["process", "--organization", organization, "--fail-extraction"]) == 0
    out = capsys.readouterr().out
    (line,) = [line for line in out.splitlines() if line.startswith("invoice ")]
    assert line.startswith(f"invoice {second} attempt 1: failed (model_extraction_failed)") and "reading gpt-5.4-mini (page 1)" in line and "extraction gpt-5.6-luna" in line
    assert (await _invoice(client, first))["processing_status"] == "succeeded"
    failed = await _invoice(client, second)
    assert (failed["processing_status"], failed["processing_error_code"]) == ("failed", "model_extraction_failed")
    _use_store(instance)
    assert (await client.get(f"/api/invoice-documents/{second}/content")).content.startswith(b"%PDF")
    assert offline.main(["discard"]) == 0 and _model_rows() == (0, 0)
