"""Which process runs the inbox's model stage (issue #501, owner decision of 2026-11-22).

In every stack the declaration worker, the process that holds the organization's session secret and the route to its
provider, runs the extraction stage of the inbox (``core/invoice_claimant.py``); the inbox worker keeps inspection,
delivery and the rest, has no session secret and no route to a provider, and never reaches that stage: a document cannot
end with ``ai_key_unreadable`` because the process that looked at it could not decrypt the organization's key.
"""
import asyncio
import logging
import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.core.invoice_claimant import runs_inbox_extraction
from app.declarations.fake_provider import FakeDocumentReadingProvider
from app.workers import declaration_import, intake_extraction
from app.workers.declaration_import import DeclarationImportWorker, WorkerResult
from app.workers.workflow_runner import INBOX_STAGES, STAGES, WorkflowRunner, build_adapters
from tests.intake_model_support import Harness, document_pdf, enable_models, ledger
from tests.test_financial_scope_boundary import scope_seed as scope_seed, worker_tenant as worker_tenant
from tests.test_intake_model_extraction import FIRST, documents, job_state, processing, receive_original, revision
from tests.test_invoice_source_custody import fixture, store as store
from tests.test_scope_payment_reconciliation import read_value
from tests.test_scoped_account_statements import client, mutate
from tests.test_m5_einvoice import UBL_WITH_IBAN


# ---- the rule ----------------------------------------------------------------------------------------------------

@pytest.mark.no_db
@pytest.mark.parametrize("kind, expected", [("declaration_import", True), ("workflow_runner", False), ("invoice_extraction", False)])
def test_only_the_declaration_worker_runs_the_inbox_stage(kind, expected):
    assert runs_inbox_extraction(kind) is expected


@pytest.mark.no_db
def test_the_inbox_worker_cannot_be_told_to_run_the_stage_and_does_not_load_its_code():
    """The runner has no such stage, by default or by ``--stage``; its process never imports the model stage."""
    assert "intake_extraction" not in STAGES and "intake_extraction" not in INBOX_STAGES
    assert INBOX_STAGES == ("intake_inspection", "outbox")
    with pytest.raises(ValueError):
        build_adapters(["intake_extraction"], session_factory=object())
    from app.workers import workflow_runner
    with pytest.raises(SystemExit):
        asyncio.run(workflow_runner.main(["--healthcheck", "--stage", "intake_extraction"]))
    code = ("import sys, app.workers.workflow_runner as runner; runner.build_adapters(session_factory=object());"
            "print(sorted(name for name in ('app.workers.intake_extraction', 'app.declarations.intake_pipeline', 'app.declarations.openai_provider') if name in sys.modules))")
    environment = {**os.environ, "HV_DATABASE_URL_WORKER": "postgresql+asyncpg://app_worker:unused@127.0.0.1:1/unused"}
    environment.pop("HV_SESSION_SECRET", None)
    answer = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=environment, timeout=120)
    assert answer.returncode == 0, answer.stderr
    assert answer.stdout.strip() == "[]", "the inbox worker's process loads neither the stage nor the pipeline nor a provider transport"


# ---- the processes -----------------------------------------------------------------------------------------------

def loop_worker(org, harness=None, *, rounds=1):
    """The declaration worker of any stack for ``rounds`` rounds of its loop over one organization (fake providers)."""
    calls = []

    async def sleep(_seconds):
        calls.append(1)
        if len(calls) >= rounds:
            raise asyncio.CancelledError()

    async def only_this_organization():
        return (org,)

    worker = DeclarationImportWorker(configured=settings.model_copy(update={"invoice_extraction_enabled": False}), sleep=sleep, intake_providers=harness)
    worker._organization_ids = only_this_organization
    return worker


@pytest.mark.asyncio
async def test_the_inbox_worker_leaves_an_unstructured_document_alone_and_the_declaration_worker_reads_it(migrator_session, scope_seed, store, monkeypatch):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([FIRST]), pages=1)
        (document,) = (await documents(api, ref)).values()

        # The inbox worker as it runs: no session secret, so a model stage in it could only end `ai_key_unreadable`. It
        # is not given the chance: nothing of the stage or of the settings is reached, however often it looks.
        def forbidden(*_args, **_kwargs):
            raise AssertionError("the inbox worker reached the model stage")

        with monkeypatch.context() as patched:
            patched.setattr(intake_extraction, "process_once", forbidden)
            patched.setattr(intake_extraction, "model_outcome", forbidden)
            patched.setattr("app.services.ai_settings_service.effective_ai_settings", forbidden)
            patched.setattr("app.declarations.invoice_pipeline.create_invoice_providers", forbidden)
            patched.setattr(settings, "session_secret", "an-installation-without-the-secret-of-the-key")

            async def organizations():
                return (ids["org"],)

            runner = WorkflowRunner(organizations, build_adapters())
            for _ in range(4):
                assert (await runner.run_once()).code == "IDLE"
        assert await processing(api, document) == ("queued", None, False)
        assert (await job_state(ids, document))["attempts"] == 0 and (await revision(ids, document))[1] is None
        assert await read_value("SELECT count(*) FROM intake_extraction_attempt WHERE organization_id=:org", ids) == 0

        # The declaration worker, which has the secret and the route, runs it in its loop.
        harness = Harness()
        with pytest.raises(asyncio.CancelledError):
            await loop_worker(ids["org"], harness).run_forever()
        assert await processing(api, document) == ("succeeded", None, False)
        assert [call.pages for call in harness.extractor.calls] == [(1,)]
        assert (await revision(ids, document))[0]["processor_version"] == "intake-model-v1"


@pytest.mark.asyncio
async def test_the_declaration_worker_parses_a_structured_document_locally_and_sends_nothing(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, UBL_WITH_IBAN, pages=None, mime="application/xml", filename="invoice.xml")
        (document,) = (await documents(api, ref)).values()
        harness = Harness()
        with pytest.raises(asyncio.CancelledError):
            await loop_worker(ids["org"], harness).run_forever()
        assert await processing(api, document) == ("succeeded", None, False)
    assert harness.built == [] and await ledger(ids["org"]) == []


class HangingReader(FakeDocumentReadingProvider):
    """A request that never answers: the worker is told to stop while it waits."""

    def __init__(self):
        super().__init__()
        self.out = asyncio.Event()

    async def read_page(self, page, *, model, max_output_tokens):
        self.out.set()
        await asyncio.sleep(3600)


@pytest.mark.asyncio
async def test_a_shutdown_during_an_inbox_request_ends_the_loop_settles_the_request_and_leaves_the_claim_to_its_lease(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([None]), pages=1)
        (document,) = (await documents(api, ref)).values()
        reader = HangingReader()
        task = asyncio.create_task(loop_worker(ids["org"], Harness(reader=reader), rounds=5).run_forever())
        await asyncio.wait_for(reader.out.wait(), 60)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Nothing stays RESERVED, and the claim is the fenced one that a later claim recovers once its lease has run out.
        (row,) = await ledger(ids["org"])
        assert (row.status, row.usage_complete, row.error_code) == ("SETTLED", False, "WORKER_CANCELLED")
        assert (await job_state(ids, document))["status"] == "running"
        await mutate(text("UPDATE intake_extraction_job SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE organization_id=:org"), ids)
        harness = Harness()
        with pytest.raises(asyncio.CancelledError):
            await loop_worker(ids["org"], harness).run_forever()
        assert await processing(api, document) == ("succeeded", None, False)
        assert (await job_state(ids, document))["attempts"] == 2


# ---- the turn in the loop ------------------------------------------------------------------------------------------

class Turns:
    """Records which kind of work got a turn, in order, with a configurable result per organization."""

    def __init__(self):
        self.calls = []
        self.results = {}

    def process(self, name):
        async def process_once(_factory, org, **_options):
            self.calls.append((name, org))
            result = self.results.get((name, org), False)
            if isinstance(result, BaseException):
                raise result
            return result

        return process_once


def turn_worker(monkeypatch, organizations, turns, *, rounds=1, invoices=True):
    from app.workers import invoice_extraction

    rounds_done = []

    async def sleep(_seconds):
        rounds_done.append(1)
        if len(rounds_done) >= rounds:
            raise asyncio.CancelledError()

    async def run_once(*_args, **_kwargs):
        turns.calls.append(("declaration", None))
        return WorkerResult("IDLE")

    async def nothing(*_args, **_kwargs):
        return None

    async def discovered():
        return tuple(organizations)

    worker = DeclarationImportWorker(configured=settings.model_copy(update={"invoice_extraction_enabled": invoices}), sleep=sleep)
    worker._organization_ids = discovered
    monkeypatch.setattr(worker, "run_once", run_once)
    monkeypatch.setattr(worker, "_cleanup_signup_requests", nothing)
    monkeypatch.setattr(declaration_import, "get_worker_session_factory", lambda: "factory")
    monkeypatch.setattr(invoice_extraction, "process_once", turns.process("invoice"))
    monkeypatch.setattr(intake_extraction, "process_once", turns.process("inbox"))
    return worker


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_each_round_gives_the_declaration_the_invoice_and_the_inbox_a_turn_in_that_order(monkeypatch):
    org = uuid.uuid4()
    turns = Turns()
    worker = turn_worker(monkeypatch, [org], turns, rounds=2)
    with pytest.raises(asyncio.CancelledError):
        await worker.run_forever()
    assert turns.calls == [("declaration", None), ("invoice", org), ("inbox", org)] * 2


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_a_backlog_of_one_kind_does_not_starve_another(monkeypatch):
    org = uuid.uuid4()
    turns = Turns()
    turns.results = {("inbox", org): True, ("invoice", org): True}
    worker = turn_worker(monkeypatch, [org], turns, rounds=3)
    with pytest.raises(asyncio.CancelledError):
        await worker.run_forever()
    assert [name for name, _ in turns.calls].count("declaration") == 3
    assert [name for name, _ in turns.calls].count("invoice") == 3 and [name for name, _ in turns.calls].count("inbox") == 3


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_the_inbox_turn_rotates_over_the_organizations_so_that_a_backlog_does_not_hold_the_rest_back(monkeypatch):
    first, second, third = (uuid.uuid4() for _ in range(3))
    turns = Turns()
    turns.results = {("inbox", first): True, ("inbox", second): True, ("inbox", third): True}
    worker = turn_worker(monkeypatch, [first, second, third], turns, invoices=False)
    for _ in range(4):
        assert await worker._process_one_inbox_extraction() is True
    assert [org for name, org in turns.calls if name == "inbox"] == [first, second, third, first]


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_a_failure_of_the_inbox_turn_for_one_organization_is_logged_and_neither_stops_the_others_nor_the_loop(monkeypatch, caplog):
    broken, healthy = uuid.uuid4(), uuid.uuid4()
    turns = Turns()
    turns.results = {("inbox", broken): RuntimeError("synthetic failure with a secret-looking value sk-test"), ("inbox", healthy): True,
                     ("invoice", broken): True}
    worker = turn_worker(monkeypatch, [broken, healthy], turns, rounds=2)
    with caplog.at_level(logging.INFO, logger=declaration_import.logger.name), pytest.raises(asyncio.CancelledError):
        await worker.run_forever()
    assert [call for call in turns.calls if call[0] == "inbox"] == [("inbox", broken), ("inbox", healthy)] * 2
    assert [call for call in turns.calls if call[0] == "declaration"] == [("declaration", None)] * 2, "the loop went on"
    assert any(name == "invoice" for name, _ in turns.calls), "the invoice turn is not held back by the failing inbox turn"
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "result=INBOX_ERROR" in messages and "error_type=RuntimeError" in messages and f"organization_id={broken}" in messages
    assert "result=INBOX_PROCESSED" in messages and "sk-test" not in messages and "synthetic failure" not in messages


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_a_failing_invoice_turn_does_not_hold_back_the_inbox_turn(monkeypatch):
    org = uuid.uuid4()
    turns = Turns()
    turns.results = {("invoice", org): RuntimeError("boom"), ("inbox", org): True}
    worker = turn_worker(monkeypatch, [org], turns)
    with pytest.raises(asyncio.CancelledError):
        await worker.run_forever()
    assert turns.calls == [("declaration", None), ("invoice", org), ("inbox", org)]


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_a_failed_discovery_ends_only_the_turn(monkeypatch, caplog):
    turns = Turns()
    worker = turn_worker(monkeypatch, [], turns)

    async def broken():
        raise RuntimeError("the database is gone")

    worker._organization_ids = broken
    with caplog.at_level(logging.INFO, logger=declaration_import.logger.name):
        assert await worker._process_one_inbox_extraction() is False
    assert "result=INBOX_DISCOVERY_FAILED" in caplog.text and "the database is gone" not in caplog.text


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_a_cancellation_is_not_a_failure_and_passes_through_the_turn(monkeypatch):
    org = uuid.uuid4()
    turns = Turns()
    turns.results = {("inbox", org): asyncio.CancelledError()}
    worker = turn_worker(monkeypatch, [org], turns)
    with pytest.raises(asyncio.CancelledError):
        await worker._process_one_inbox_extraction()


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_the_inbox_turn_does_not_depend_on_the_invoice_switch(monkeypatch):
    """Production's declaration worker has the invoice switch off (its stack has an invoice worker) and still runs the inbox."""
    org = uuid.uuid4()
    turns = Turns()
    turns.results = {("inbox", org): True}
    worker = turn_worker(monkeypatch, [org], turns, invoices=False)
    assert await worker._process_one_invoice() is False
    assert await worker._process_one_inbox_extraction() is True
    assert turns.calls == [("inbox", org)]
