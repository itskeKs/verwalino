"""The declaration worker also claims waiting invoices in its loop (issue #814).

The loop is driven with fake declaration and invoice steps (and an idle fake of the inbox's extraction stage, which has its
own tests in ``test_inbox_model_stage_process.py``), so what is proved is the schedule: both kinds of work take
turns, a failure of one neither ends the loop nor holds back the other, a shutdown ends it and nothing is claimed
where the installation does not process invoices. The real invoice step against the database is covered with the
invoice worker's own scenarios (``test_invoice_model_extraction.py``).
"""
import asyncio
import logging
import signal
import uuid

import pytest

from app.core.config import settings
from app.workers import declaration_import, intake_extraction, invoice_extraction
from app.workers.declaration_import import DeclarationImportWorker, WorkerResult

pytestmark = pytest.mark.no_db

A, B, C = (uuid.UUID(int=number) for number in (1, 2, 3))
SENTINEL = "Synthetic invoice text: payee DE00 0000 0000 0000 0000 00"


class Loop:
    """A worker whose declaration step and invoice step are fakes, stopped after a number of rounds."""

    def __init__(self, monkeypatch, *, rounds, declaration=None, invoice=None, organizations=(A, B, C), enabled=True):
        self.events = []
        self.inbox_turns = []
        self.sleeps = 0
        self.rounds = rounds
        self.organizations = organizations
        self.declaration = declaration or self.processed
        self.invoice = invoice or self.always
        monkeypatch.setattr(invoice_extraction, "process_once", self.process_once)
        monkeypatch.setattr(intake_extraction, "process_once", self.inbox_once)
        monkeypatch.setattr(declaration_import, "get_worker_session_factory", lambda: "worker session factory")
        self.worker = DeclarationImportWorker(
            configured=settings.model_copy(update={"invoice_extraction_enabled": enabled}), sleep=self.sleep)
        self.worker.run_once = self.run_once
        self.worker._organization_ids = self.organization_ids
        self.worker._cleanup_signup_requests = self.cleanup

    async def processed(self):
        return WorkerResult("PROCESSED", uuid.UUID(int=99))

    async def always(self, organization):
        return True

    async def run_once(self):
        self.events.append("declaration")
        return await self.declaration()

    async def organization_ids(self):
        self.events.append("organizations")
        return self.organizations

    async def cleanup(self):
        return None

    async def process_once(self, factory, organization, **options):
        assert factory == "worker session factory"
        self.events.append(("invoice", organization))
        return await self.invoice(organization)

    async def inbox_once(self, factory, organization, **options):
        """The inbox's stage in the loop: idle here, so that the schedule of the invoices is what these tests look at."""
        self.inbox_turns.append(organization)
        return False

    async def sleep(self, _seconds):
        self.sleeps += 1
        if self.sleeps >= self.rounds:
            raise asyncio.CancelledError()

    async def run(self):
        with pytest.raises(asyncio.CancelledError):
            await self.worker.run_forever()
        return [event for event in self.events if event != "organizations"]


@pytest.mark.asyncio
async def test_every_round_gives_the_declaration_import_and_one_invoice_a_turn_and_organizations_take_turns(monkeypatch):
    loop = Loop(monkeypatch, rounds=3)
    assert await loop.run() == ["declaration", ("invoice", A), "declaration", ("invoice", B), "declaration", ("invoice", C)]


@pytest.mark.asyncio
async def test_neither_kind_of_work_starves_the_other_when_both_always_have_work(monkeypatch):
    loop = Loop(monkeypatch, rounds=5, organizations=(A,))
    events = await loop.run()
    assert events == ["declaration", ("invoice", A)] * 5


@pytest.mark.asyncio
async def test_an_idle_round_asks_every_organization_once_and_the_next_round_starts_after_the_one_that_had_work(monkeypatch):
    async def only_b(organization):
        return organization == B

    loop = Loop(monkeypatch, rounds=2, invoice=only_b)
    assert await loop.run() == ["declaration", ("invoice", A), ("invoice", B), "declaration", ("invoice", C), ("invoice", A), ("invoice", B)]


@pytest.mark.asyncio
async def test_a_failing_invoice_ends_neither_the_loop_nor_the_other_organizations_and_logs_no_invoice_text(monkeypatch, caplog):
    async def first_fails(organization):
        if organization == A:
            raise RuntimeError(SENTINEL)
        return True

    loop = Loop(monkeypatch, rounds=2, invoice=first_fails)
    with caplog.at_level(logging.WARNING, logger="app.workers.declaration_import"):
        events = await loop.run()
    # The failing organization is skipped within the round, the next one is served, and the declaration step goes on.
    assert events == ["declaration", ("invoice", A), ("invoice", B), "declaration", ("invoice", C)]
    (line,) = [record.getMessage() for record in caplog.records]
    assert line.startswith(f"declaration worker result=INVOICE_ERROR organization_id={A} error_type=RuntimeError location=")
    assert SENTINEL not in line and all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
async def test_a_failing_declaration_round_does_not_hold_back_the_invoices(monkeypatch, caplog):
    async def fails():
        raise RuntimeError(SENTINEL)

    loop = Loop(monkeypatch, rounds=2, declaration=fails, organizations=(A,))
    with caplog.at_level(logging.WARNING, logger="app.workers.declaration_import"):
        events = await loop.run()
    assert events == ["declaration", ("invoice", A), "declaration", ("invoice", A)]
    assert [record.getMessage().split(" error_type")[0] for record in caplog.records] == ["declaration worker result=UNEXPECTED"] * 2
    assert all(SENTINEL not in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_a_failure_finding_the_organizations_ends_the_invoice_turn_but_not_the_loop(monkeypatch, caplog):
    loop = Loop(monkeypatch, rounds=2)

    async def unavailable():
        raise RuntimeError(SENTINEL)

    loop.worker._organization_ids = unavailable
    with caplog.at_level(logging.WARNING, logger="app.workers.declaration_import"):
        events = await loop.run()
    assert events == ["declaration", "declaration"]
    # Each round the invoice turn and the inbox turn look for the organizations and say so, each for itself.
    assert [record.getMessage().split(" error_type")[0] for record in caplog.records] == [
        "declaration worker result=INVOICE_DISCOVERY_FAILED", "declaration worker result=INBOX_DISCOVERY_FAILED"] * 2
    assert all(SENTINEL not in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_without_a_ready_schema_no_invoice_is_claimed(monkeypatch):
    async def not_ready():
        return WorkerResult("SCHEMA_NOT_READY")

    loop = Loop(monkeypatch, rounds=2, declaration=not_ready)
    assert await loop.run() == ["declaration", "declaration"]


@pytest.mark.asyncio
async def test_with_the_operators_switch_off_the_loop_claims_no_invoice_and_the_invoice_turn_does_not_even_look_for_organizations(monkeypatch):
    loop = Loop(monkeypatch, rounds=3, enabled=False)
    assert await loop.worker._process_one_invoice() is False
    assert loop.events == [], "the invoice turn looked for nothing"
    assert await loop.run() == ["declaration"] * 3
    # The inbox's extraction stage does not depend on the invoice switch: it still takes its turn in every round.
    assert loop.inbox_turns == [A, B, C] * 3


@pytest.mark.asyncio
async def test_a_shutdown_while_an_invoice_is_processed_ends_the_loop_and_is_no_failure(monkeypatch, caplog):
    started, never = asyncio.Event(), asyncio.Event()

    async def hangs(organization):
        started.set()
        await never.wait()

    loop = Loop(monkeypatch, rounds=1000, invoice=hangs)
    with caplog.at_level(logging.WARNING, logger="app.workers.declaration_import"):
        task = asyncio.create_task(loop.worker.run_forever())
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert caplog.records == []


@pytest.mark.asyncio
async def test_a_sigterm_cancels_the_loop_so_that_an_invoice_request_in_flight_is_settled_by_its_own_shutdown_path(monkeypatch):
    """The invoice path settles a request that is out when its task is cancelled (WORKER_CANCELLED); the declaration
    worker, which now runs it, must turn SIGTERM into that cancellation as the invoice worker does."""
    started = asyncio.Event()
    handlers = {}

    async def forever(_self):
        started.set()
        await asyncio.Event().wait()

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", lambda number, callback, *args: handlers.setdefault(number, (callback, args)))
    monkeypatch.setattr(loop, "remove_signal_handler", lambda number: handlers.pop(number, None) is not None)
    monkeypatch.setattr(DeclarationImportWorker, "run_forever", forever)
    task = asyncio.create_task(declaration_import._main())
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert signal.SIGTERM in handlers
        callback, args = handlers[signal.SIGTERM]
        callback(*args)
        await asyncio.wait_for(task, 5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
