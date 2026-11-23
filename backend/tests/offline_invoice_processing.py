"""The invoice path of an acceptance instance, run by the session that launches a blind acceptance (issue #829).

An acceptance instance (scripts/acceptance-env.sh) runs only the API: no worker and no provider. This command stands in for
both on the invoice path, beside ``tests.offline_declaration_import`` for the declaration import, with the fakes of the browser
journey twin (the building-insurance invoice of docs/acceptance/data). Every form refuses, with a message and exit status 1,
when no acceptance instance of ``acceptance-env.sh`` is running; the database forms also when that run does not hold the
test database lock. Test tree only: no product switch, no configuration key.

    fixtures                        the sample documents into the instance's dist/assets, for the tester to fetch
    stub start | stub stop          the model list of an OpenAI-compatible provider on 127.0.0.1, for the settings page
    process --organization <name>   the invoice worker once over the queue of one organization, with the twin's fake models
            [--fail-extraction]       (the extraction model times out after the reading was paid); one line per attempt
    discard                         the model metering rows and model-read proposals of the run, before `acceptance-env.sh stop`

By hand, from the checkout of the PR's commit, with the test database settings:

    cd <repository>/backend && . ../scripts/test-env.sh && uv run --locked --extra dev --extra ops \\
      python -m tests.offline_invoice_processing <form>

The stored API key is neither decrypted for real nor used: the instance has its own random session secret, so the key its
settings page stored cannot be read with this process's settings. ``ai_settings_service._cipher`` is replaced by a stand-in
whose ``decrypt`` returns fixed bytes; everything else (the switch, the confirmation, the models, the budget, the claim and
the lease) is the product's own code, and the fake providers are handed to the worker where it would build the real ones.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import psycopg2
import reportlab
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import func, select

from app.core.config import settings
from app.core.db import get_worker_session_factory, get_worker_tenant_session
from app.core.declaration_test_database import require_disposable_database_settings
from app.core.storage import LocalFsDocumentStore, set_store
from app.declarations.fake_provider import FakeInvoiceExtractionProvider, InvoiceExtractionRecord, ReadingRecord
from app.declarations.invoice_pipeline import InvoiceProviders
from app.models.invoice_processing_attempt import InvoiceProcessingAttempt
from app.services import ai_settings_service
from app.workers.invoice_extraction import process_once
from tests.bdd.steps.browser_invoice import (
    EXTRACTION_MODEL, READING_MODEL, _extraction_failure, _insurance_output, _printed_invoice, _printed_lines, _structured,
)
from tests.bdd.steps.invoice_processing import EXTRACTION_USAGE
from tests.invoice_model_support import discard_invoice_model_data
from tests.offline_declaration_import import _organization_id, offline_worker_engine

# Fixed locations, the same as in scripts/acceptance-env.sh; the tests rewrite exactly these two and the port.
ACCEPTANCE_LOCK = Path("/tmp/hausverwaltung-acceptance.lock")
TESTDB_LOCK = Path("/tmp/verwalino-testdb.lock")
STUB_PORT = 18111
SCRATCH_NAME = "verwalino-acceptance."
STUB_RECORD, STUB_LOG = "offline-invoice-stub.json", "offline-invoice-stub.log"
STOP_WAIT_SECONDS = 10
MAX_ATTEMPTS = 25
BACKEND = Path(__file__).resolve().parents[1]

SCAN, SECOND_SCAN = "versicherung-2026.pdf", "versicherung-2026-b.pdf"
FIXTURE_NOTES = {
    SCAN: "scan, one page, no text layer",
    SECOND_SCAN: "the same scan with one pixel changed (a copy would be refused as a duplicate), for a second upload",
    "versicherung-xrechnung.xml": "XRechnung (UBL), read directly, no model",
    "versicherung-zugferd.pdf": "ZUGFeRD PDF with embedded CII, read directly, no model",
}


class Refusal(Exception):
    """A request this command will not carry out: the message says why and what to do; nothing was changed."""


# ---- the running acceptance instance ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Instance:
    """The running acceptance instance: its scratch directory holds the built frontend, the document store and our records."""

    scratch: Path


def _ps(field_name: str, pid: int) -> str:
    """One field of ``ps`` for a process, in the C locale: the form of the start time has to be the same at both ends."""
    result = subprocess.run(["ps", "-o", f"{field_name}=", "-p", str(pid)], env={**os.environ, "LC_ALL": "C"}, capture_output=True, text=True)
    return result.stdout.strip()


def process_identity(pid: int) -> tuple[str, str]:
    """The start time and command line of a process: what acceptance-env.sh records to recognise its app again."""
    return _ps("lstart", pid), _ps("command", pid)


def process_alive(pid: int) -> bool:
    state = _ps("stat", pid)
    return bool(state) and "Z" not in state


def _first_line(path: Path) -> str:
    try:
        return path.read_text().splitlines()[0]
    except (OSError, IndexError):
        return ""


def _record(lock: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = (lock / "instance").read_text().splitlines()
    except OSError:
        return values
    for line in lines:
        name, separator, value = line.partition("=")
        if separator:
            values[name] = value
    return values


def running_instance(*, database: bool = False) -> Instance:
    """The acceptance instance of ``scripts/acceptance-env.sh`` that is running, or the refusal why there is none.

    The checks of the script's own ``require_running_instance``: the lock is the script's, the record names a scratch
    directory it created, and the app is the live process it started. ``database`` also asks that the test database lock
    is this run's (the script takes both locks at ``start``, with the same line), so that the database forms run only while this
    run owns the database.
    """
    if not ACCEPTANCE_LOCK.is_dir():
        raise Refusal("no acceptance instance is running; start one with scripts/acceptance-env.sh start <commit> first")
    info = _first_line(ACCEPTANCE_LOCK / "info")
    if info.split()[2:3] != ["acceptance-env"]:
        raise Refusal(f"{ACCEPTANCE_LOCK} is not held by scripts/acceptance-env.sh; not touching it")
    record = _record(ACCEPTANCE_LOCK)
    if not record.get("scratch") or not record.get("pid", "").isdigit():
        raise Refusal("the acceptance instance is not fully started; wait for `start` or run `stop` and start again")
    scratch, pid = Path(record["scratch"]), int(record["pid"])
    if scratch.is_symlink() or not scratch.is_dir() or not scratch.name.startswith(SCRATCH_NAME) or not (scratch / ".acceptance-env").is_file():
        raise Refusal(f"{scratch} is not a scratch directory of acceptance-env.sh (name, directory, no link, marker); not touching it")
    if not process_alive(pid) or process_identity(pid) != (record.get("started"), record.get("command")):
        raise Refusal("the app of this acceptance instance is not running; run scripts/acceptance-env.sh stop and start again")
    if database and _first_line(TESTDB_LOCK / "info") != info:
        raise Refusal(f"{TESTDB_LOCK} is not held by this acceptance instance; nothing was touched")
    return Instance(scratch)


def _require_test_database() -> str:
    """The synchronous URL of the disposable test database, or a refusal: nothing else is ever touched."""
    try:
        return require_disposable_database_settings(settings)[2]
    except RuntimeError as error:
        raise Refusal(str(error)) from None


# ---- the sample documents -----------------------------------------------------------------------------------------------

def printed_image(lines: list[str]) -> Image.Image:
    """One A4 grey image that prints the lines, as a scanner delivers a page: a picture and no text layer."""
    image = Image.new("L", (595, 842), 255)
    drawing = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"), 18)  # has the umlauts the invoice prints
    for number, line in enumerate(lines):
        drawing.text((60, 100 + 34 * number), line, fill=0, font=font)
    return image


def scan_pdf(lines: list[str], variant: int = 0) -> bytes:
    """A one-page PDF that holds only the picture of the printed lines.

    ``variant`` 1 differs from 0 by one pixel: a second scan with the same bytes would be a duplicate of the first.
    """
    image = printed_image(lines)
    if variant:
        image.putpixel((2, 2), 0)
    out = io.BytesIO()
    image.save(out, "PDF", resolution=72)
    return out.getvalue()


def scan(variant: int = 0) -> bytes:
    """The scanned invoice: one A4 image that prints what the reading model is made to read, and no text layer."""
    return scan_pdf(_printed_lines(), variant)


def fixture_documents() -> dict[str, bytes]:
    documents = {SCAN: scan(0), SECOND_SCAN: scan(1)}
    for kind in ("xrechnung", "zugferd"):
        name, _mime, data = _structured(kind)
        documents[name] = data
    return documents


def write_documents(instance: Instance, documents: dict[str, bytes]) -> list[str]:
    """Write the documents into the instance's ``dist/assets``, where the app serves them at ``/assets/<name>``."""
    assets = instance.scratch / "dist" / "assets"
    if not assets.is_dir():
        raise Refusal(f"{assets} does not exist: the instance serves no frontend build, so nothing could fetch the documents")
    for name, data in documents.items():
        (assets / name).write_bytes(data)
    return list(documents)


def write_fixtures(instance: Instance) -> list[str]:
    return write_documents(instance, fixture_documents())


# ---- the model-list stub ------------------------------------------------------------------------------------------------

_LAUNCHER = """
import subprocess, sys
log, cwd, *command = sys.argv[1:]
with open(log, "ab") as output:
    child = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True)
print(child.pid)
"""


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _answers(port: int) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/v1/models", timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def _stub_record(instance: Instance) -> dict | None:
    try:
        return json.loads((instance.scratch / STUB_RECORD).read_text())
    except (OSError, ValueError):
        return None


def _is_the_stub(record: dict) -> bool:
    return process_alive(record["pid"]) and process_identity(record["pid"]) == (record["started"], record["command"])


def start_stub(instance: Instance) -> int:
    """Start the model-list stub detached, check that it answers and record the process, so that `stop` can verify it."""
    record_path = instance.scratch / STUB_RECORD
    existing = _stub_record(instance)
    if existing is not None and _is_the_stub(existing):
        raise Refusal(f"the model-list stub is already running (PID {existing['pid']}); run `stub stop` first")
    if _listening(STUB_PORT):
        raise Refusal(f"port {STUB_PORT} on 127.0.0.1 is already in use; nothing was started")
    record_path.unlink(missing_ok=True)
    command = [sys.executable, "-m", "tests.offline_model_list_stub", "--port", str(STUB_PORT), "--lock", str(ACCEPTANCE_LOCK),
               "--scratch", str(instance.scratch), "--model", READING_MODEL, "--model", EXTRACTION_MODEL]
    # Detached into its own session through a short-lived launcher (as acceptance-env.sh starts the app): the stub is
    # neither this process's child nor in its process group, so closing the caller's shell does not take it along.
    launched = subprocess.run([sys.executable, "-I", "-c", _LAUNCHER, str(instance.scratch / STUB_LOG), str(BACKEND), *command],
                              env={"PATH": os.environ.get("PATH", "")}, capture_output=True, text=True)
    if launched.returncode != 0 or not launched.stdout.strip().isdigit():
        raise Refusal(f"the model-list stub could not be launched: {launched.stderr.strip()[-300:]}")
    pid = int(launched.stdout.strip())
    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while not _answers(STUB_PORT):
        if not process_alive(pid) or time.monotonic() > deadline:
            _terminate(pid)
            raise Refusal(f"the model-list stub did not answer on port {STUB_PORT}; see {instance.scratch / STUB_LOG}")
        time.sleep(0.1)
    started, command_line = process_identity(pid)
    record_path.write_text(json.dumps({"pid": pid, "port": STUB_PORT, "started": started, "command": command_line}))
    return pid


def _signal(pid: int, number: int) -> None:
    try:
        # The stub leads its own session, so its group is its own; anything else gets the plain signal.
        if os.getpgid(pid) == pid:
            os.killpg(pid, number)
        else:
            os.kill(pid, number)
    except ProcessLookupError:
        pass


def _wait_until_gone(pid: int) -> bool:
    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while process_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    return not process_alive(pid)


def _terminate(pid: int, identity: tuple[str, str] | None = None) -> None:
    """End the process: TERM, and KILL when it stays, the second time only if it is still the same process."""
    if not process_alive(pid):
        return
    identity = identity or process_identity(pid)
    _signal(pid, 15)
    if _wait_until_gone(pid):
        return
    if process_alive(pid) and process_identity(pid) == identity:
        _signal(pid, 9)
    if not _wait_until_gone(pid):
        raise Refusal(f"the process {pid} did not stop")


def stop_stub(instance: Instance) -> str:
    """Stop the stub this command recorded, after checking that the PID is still that process; nothing else is signalled."""
    record = _stub_record(instance)
    if record is None:
        raise Refusal("no model-list stub is recorded for this instance; nothing was stopped")
    record_path = instance.scratch / STUB_RECORD
    pid = record["pid"]
    if not process_alive(pid):
        record_path.unlink(missing_ok=True)
        message = f"The model-list stub (PID {pid}) was already gone."
    elif process_identity(pid) != (record["started"], record["command"]):
        raise Refusal(f"PID {pid} is not the stub this command started; nothing was signalled")
    else:
        _terminate(pid, (record["started"], record["command"]))
        record_path.unlink(missing_ok=True)
        message = f"Stopped the model-list stub (PID {pid})."
    if _listening(record["port"]):
        raise Refusal(f"port {record['port']} on 127.0.0.1 is still in use by another process")
    return f"{message} Port {record['port']} is free."


# ---- the worker --------------------------------------------------------------------------------------------------------

class _StandInCipher:
    """What ``ai_settings_service._cipher`` returns here. The key the instance stored is bound to the instance's own random
    session secret and cannot be read with this process's settings; the fake providers never use a key."""

    def decrypt(self, _token: bytes) -> bytes:
        return b"offline-stand-in-key"


def models_asked(reads: list[ReadingRecord], calls: list[InvoiceExtractionRecord]) -> str:
    """What the fake models were asked, as the record of an attempt prints it (shared with the inbox command)."""
    stages = []
    if reads:
        stages.append("reading " + ", ".join(f"{read.model} (page {read.pdf_page})" for read in reads))
    if calls:
        stages.append("extraction " + ", ".join(f"{call.model} (pages {','.join(map(str, call.pages))})" for call in calls))
    return "; ".join(stages) or "no model asked"


@dataclass
class AttemptRecord:
    """One attempt the worker finished and what the fake models were asked meanwhile."""

    invoice_id: uuid.UUID
    attempt_no: int
    status: str
    error_code: str | None
    reads: list[ReadingRecord] = field(default_factory=list)
    calls: list[InvoiceExtractionRecord] = field(default_factory=list)

    def line(self) -> str:
        outcome = self.status + (f" ({self.error_code})" if self.error_code else "")
        return f"invoice {self.invoice_id} attempt {self.attempt_no}: {outcome}; {models_asked(self.reads, self.calls)}"


async def _waiting(organization_id: uuid.UUID) -> int:
    async with get_worker_tenant_session(organization_id) as session:
        return await session.scalar(select(func.count()).select_from(InvoiceProcessingAttempt)
                                    .where(InvoiceProcessingAttempt.organization_id == organization_id)
                                    .where(InvoiceProcessingAttempt.status.in_(("queued", "running"))))


async def _finished(organization_id: uuid.UUID) -> dict[uuid.UUID, InvoiceProcessingAttempt]:
    async with get_worker_tenant_session(organization_id) as session:
        rows = await session.scalars(select(InvoiceProcessingAttempt)
                                     .where(InvoiceProcessingAttempt.organization_id == organization_id)
                                     .where(InvoiceProcessingAttempt.completed_at.is_not(None))
                                     .order_by(InvoiceProcessingAttempt.completed_at, InvoiceProcessingAttempt.created_at))
        return {row.id: row for row in rows}


async def process_waiting(organization_id: uuid.UUID, *, fail_extraction: bool = False) -> list[AttemptRecord]:
    """Run the invoice worker over the organization's queue, oldest waiting attempt first, until nothing is claimed.

    The worker is the real ``process_once``; only the providers it would build are the twin's fakes, one pair per attempt,
    so that the record of each attempt shows which model was asked for which page. With ``fail_extraction`` the extraction
    model times out after the reading was paid, on every scan this run processes.
    """
    if not settings.invoice_extraction_enabled:
        raise Refusal("invoice processing is switched off in this process (HV_INVOICE_EXTRACTION_ENABLED=false), so the worker would "
                      "claim nothing; start the command without that setting")
    if not await _waiting(organization_id):
        raise Refusal("nothing is waiting: no invoice of this organization is queued (has the tester uploaded one, or restarted a failed one?)")
    built: list[tuple] = []

    def providers(effective):
        reader = _printed_invoice()
        extractor = FakeInvoiceExtractionProvider(failure=_extraction_failure()) if fail_extraction else \
            FakeInvoiceExtractionProvider(output=_insurance_output(), usage=EXTRACTION_USAGE)
        built.append((reader, extractor))
        return InvoiceProviders(reader=reader if effective.document_model else None, extractor=extractor)

    records: list[AttemptRecord] = []
    with patch.object(ai_settings_service, "_cipher", lambda _organization_id: _StandInCipher()):
        for _ in range(MAX_ATTEMPTS):
            before, used = await _finished(organization_id), len(built)
            if not await process_once(get_worker_session_factory(), organization_id, providers=providers):
                break
            new = [row for identifier, row in (await _finished(organization_id)).items() if identifier not in before]
            for row in new:
                records.append(AttemptRecord(row.document_id, row.attempt_no, row.status, row.error_code))
            # The models of this attempt: the providers built while it ran (none for a structured invoice).
            if new and len(built) > used:
                records[-1].reads, records[-1].calls = built[-1][0].reads, built[-1][1].calls
    return records


async def _process_with_worker_engine(organization_id: uuid.UUID, fail_extraction: bool) -> tuple[list[AttemptRecord], int]:
    async with offline_worker_engine():
        records = await process_waiting(organization_id, fail_extraction=fail_extraction)
        return records, await _waiting(organization_id)


def _organization(name_or_id: str) -> uuid.UUID:
    """The organization by name or id; an unknown one is refused (the id of the declaration command is not looked up there)."""
    try:
        organization_id = _organization_id(name_or_id)
    except SystemExit as unknown:
        raise Refusal(str(unknown.code)) from None
    with psycopg2.connect(os.environ["HV_DATABASE_URL_SYNC"]) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM public.organization WHERE id = %s", (str(organization_id),))
        if cursor.fetchone()[0] != 1:
            raise Refusal(f"no organization has the id {organization_id}")
    return organization_id


# ---- the cleanup --------------------------------------------------------------------------------------------------------

def discard(sync_url: str) -> int:
    """Discard the model metering rows and model-read proposals of the test database; return how many calls were metered.

    The run holds the test database lock (checked by ``running_instance(database=True)``), so no test process is on it; the
    rows are the ones that make the migration downgrades of the backend suite refuse (0197, 0198).
    """
    with closing(psycopg2.connect(sync_url)) as connection, connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM public.invoice_model_call")
        metered = cursor.fetchone()[0]
        discard_invoice_model_data(cursor.execute)
    return metered


# ---- the command --------------------------------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tests.offline_invoice_processing", description=__doc__.split("\n\n")[0])
    forms = parser.add_subparsers(dest="form", required=True)
    forms.add_parser("fixtures", help="write the sample documents into the instance's dist/assets")
    stub = forms.add_parser("stub", help="start or stop the model-list stub on 127.0.0.1")
    stub.add_argument("action", choices=("start", "stop"))
    process = forms.add_parser("process", help="run the invoice worker once over the queue of one organization")
    process.add_argument("--organization", required=True, help="name or id of the organization")
    process.add_argument("--fail-extraction", action="store_true", help="the extraction model times out after the reading was paid")
    forms.add_parser("discard", help="discard the model metering rows and model-read proposals of the test database")
    return parser


def _run(arguments: argparse.Namespace) -> int:
    if arguments.form == "fixtures":
        instance = running_instance()
        written = write_fixtures(instance)
        print(f"Wrote {len(written)} documents into {instance.scratch / 'dist' / 'assets'}; the tester fetches them from the instance:")
        print("\n".join(f"  /assets/{name}  {FIXTURE_NOTES.get(name, '')}".rstrip() for name in written))
        return 0
    if arguments.form == "stub":
        instance = running_instance()
        if arguments.action == "start":
            pid = start_stub(instance)
            print(f"Model-list stub running (PID {pid}). Provider „OpenAI-kompatible API“, base URL http://127.0.0.1:{STUB_PORT}/v1, any key; "
                  f"it lists {READING_MODEL} and {EXTRACTION_MODEL}. It ends by itself when the instance is stopped.")
        else:
            print(stop_stub(instance))
        return 0
    instance = running_instance(database=True)
    sync_url = _require_test_database()
    if arguments.form == "discard":
        print(f"Discarded the model metering rows ({discard(sync_url)} calls) and model-read proposals of the test database.")
        return 0
    organization_id = _organization(arguments.organization)
    set_store(LocalFsDocumentStore(str(instance.scratch / "documents")))
    try:
        records, waiting = asyncio.run(_process_with_worker_engine(organization_id, arguments.fail_extraction))
    finally:
        set_store(None)
    print("\n".join(record.line() for record in records) or "no attempt was claimed")
    if waiting:
        print(f"still waiting: {waiting}")
    return 0 if records else 1


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        return _run(arguments)
    except Refusal as refusal:
        print(f"offline_invoice_processing: {refusal}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
