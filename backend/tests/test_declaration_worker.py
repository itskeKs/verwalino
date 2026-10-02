"""Fenced declaration-import worker coverage."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from tests.declaration_queue_fixture import queue_fixture
from app.core.config import settings
from app.core.db import get_worker_tenant_session as get_tenant_session
from app.models.declaration_import import DeclarationExtractionRun, DeclarationImport
from app.models.organization import Organization
from app.repositories.declaration_import_repo import (
    DeclarationImportRepository,
    WorkerLease,
)
from app.workers.declaration_import import DeclarationImportWorker


@pytest.fixture(autouse=True)
def enable_declaration_imports(monkeypatch) -> None:
    monkeypatch.setattr(settings, "declaration_import_enabled", True)


def test_declaration_import_worker_module_is_available() -> None:
    assert DeclarationImportWorker is not None


@pytest.mark.parametrize(
    "updates",
    [
        {"declaration_worker_id": "  "},
        {"declaration_worker_poll_seconds": float("nan")},
        {"declaration_lease_seconds": 29},
        {"declaration_max_attempts": False},
    ],
)
def test_worker_rejects_invalid_settings_at_startup(updates) -> None:
    with pytest.raises(ValueError):
        DeclarationImportWorker(object(), configured=settings.model_copy(update=updates))


async def _create_import(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    status: str,
    next_attempt_at: datetime | None = None,
    lease_expires_at: datetime | None = None,
) -> DeclarationImport:
    async with get_tenant_session(org_id) as session:
        item = DeclarationImport(
            organization_id=org_id,
            mode="NEW_PROPERTY",
            target_property_id=None,
            applied_property_id=None,
            status="DRAFT",
            initiated_by=user_id,
            approved_by=None,
            approved_at=None,
            max_cost_usd=Decimal("1.00"),
            next_attempt_at=next_attempt_at,
            lease_owner="existing-worker" if lease_expires_at is not None else None,
            lease_token=uuid.uuid4() if lease_expires_at is not None else None,
            lease_generation=1 if lease_expires_at is not None else 0,
            lease_expires_at=lease_expires_at,
        )
        session.add(item)
        await session.flush()
        await queue_fixture(session, item, user_id, needs_document=True)
        item.status = status
        item.approved_by = user_id if status == "APPLYING" else None
        item.approved_at = datetime.now(timezone.utc) if status == "APPLYING" else None
        item.next_attempt_at = next_attempt_at
        item.lease_owner = "existing-worker" if lease_expires_at is not None else None
        item.lease_token = uuid.uuid4() if lease_expires_at is not None else None
        item.lease_generation = 1 if lease_expires_at is not None else 0
        item.lease_expires_at = lease_expires_at
        await session.flush()
        return item


@pytest.mark.asyncio
async def test_claim_selects_due_work_and_skips_future_or_active_leases(seed_data) -> None:
    now = datetime.now(timezone.utc)
    org_id = seed_data["org_a_id"]
    user_id = seed_data["user_a_admin_id"]
    queued = await _create_import(org_id, user_id, status="QUEUED")
    due_retry = await _create_import(
        org_id,
        user_id,
        status="RETRY_WAIT",
        next_attempt_at=now - timedelta(seconds=1),
    )
    await _create_import(
        org_id,
        user_id,
        status="RETRY_WAIT",
        next_attempt_at=now + timedelta(minutes=5),
    )
    await _create_import(
        org_id,
        user_id,
        status="QUEUED",
        lease_expires_at=now + timedelta(minutes=5),
    )
    async with get_tenant_session(org_id) as session:
        repo = DeclarationImportRepository(session, org_id)
        lease = await repo.claim_next("worker-a", now, 120)
    assert lease is not None and lease.import_id == queued.id
    async with get_tenant_session(org_id) as session:
        due_lease = await DeclarationImportRepository(session, org_id).claim_next(
            "worker-a", now, 120
        )
    assert due_lease is not None and due_lease.import_id == due_retry.id
    assert due_lease.lease_generation == 1


@pytest.mark.asyncio
async def test_claim_excludes_terminal_statuses(seed_data) -> None:
    now = datetime.now(timezone.utc)
    org_id = seed_data["org_a_id"]
    user_id = seed_data["user_a_admin_id"]
    for status in {"DRAFT", "NEEDS_REVIEW", "APPLYING", "FAILED", "CANCELLED"}:
        await _create_import(org_id, user_id, status=status)
    async with get_tenant_session(org_id) as session:
        assert await DeclarationImportRepository(session, org_id).claim_next("worker-a", now, 120) is None


@pytest.mark.asyncio
async def test_reclaim_closes_running_run_and_rejects_zombie_completion(seed_data) -> None:
    now = datetime.now(timezone.utc)
    org_id = seed_data["org_a_id"]
    user_id = seed_data["user_a_admin_id"]
    item = await _create_import(
        org_id,
        user_id,
        status="EXTRACTING",
        lease_expires_at=now - timedelta(seconds=1),
    )
    first = WorkerLease(
        org_id,
        item.id,
        4,
        uuid.uuid4(),
        now - timedelta(seconds=1),
    )
    async with get_tenant_session(org_id) as session:
        row = await DeclarationImportRepository(session, org_id).get_by_id_for_update(item.id)
        assert row is not None
        row.lease_generation = first.lease_generation
        row.lease_token = first.lease_token
        session.add(
            DeclarationExtractionRun(
                organization_id=org_id,
                import_id=item.id,
                status="RUNNING",
                provider="test",
                model_name="test",
                prompt_version="test",
                schema_version="test",
                harness_version="test",
            )
        )
        await session.flush()
    async with get_tenant_session(org_id) as session:
        repo = DeclarationImportRepository(session, org_id)
        second = await repo.claim_next("worker-b", now, 120)
    assert second is not None
    assert second.lease_generation == first.lease_generation + 1
    async with get_tenant_session(org_id) as session:
        repo = DeclarationImportRepository(session, org_id)
        assert not await repo.transition_with_lease(first, {"EXTRACTING"}, "FAILED", {})
        assert await repo.transition_with_lease(
            second,
            {"RETRY_WAIT"},
            "FAILED",
            {"lease_owner": None, "lease_token": None, "lease_expires_at": None},
        )
        run = await session.scalar(
            select(DeclarationExtractionRun).where(DeclarationExtractionRun.import_id == item.id)
        )
        assert run is not None
        assert run.status == "FAILED"
        assert run.error_code == "WORKER_LEASE_EXPIRED"


@pytest.mark.asyncio
async def test_heartbeat_renews_immediately_for_queued_and_due_retry(seed_data) -> None:
    now = datetime.now(timezone.utc)
    org_id = seed_data["org_a_id"]
    user_id = seed_data["user_a_admin_id"]
    queued = await _create_import(org_id, user_id, status="QUEUED")
    retry = await _create_import(
        org_id,
        user_id,
        status="RETRY_WAIT",
        next_attempt_at=now - timedelta(seconds=1),
    )
    async with get_tenant_session(org_id) as session:
        repo = DeclarationImportRepository(session, org_id)
        first = await repo.claim_next("worker-a", now, 120)
    assert first is not None and first.import_id == queued.id
    async with get_tenant_session(org_id) as session:
        repo = DeclarationImportRepository(session, org_id)
        renewed = await repo.heartbeat(first, now + timedelta(seconds=1), 120)
    assert renewed is not None and renewed.lease_expires_at > first.lease_expires_at
    async with get_tenant_session(org_id) as session:
        second = await DeclarationImportRepository(session, org_id).claim_next("worker-a", now, 120)
    assert second is not None and second.import_id == retry.id
    async with get_tenant_session(org_id) as session:
        renewed_retry = await DeclarationImportRepository(session, org_id).heartbeat(
            second, now + timedelta(seconds=1), 120
        )
    assert renewed_retry is not None and renewed_retry.lease_expires_at > second.lease_expires_at


@pytest.mark.asyncio
async def test_heartbeat_requires_current_org_token_generation_expiry_and_status(seed_data) -> None:
    now = datetime.now(timezone.utc)
    org_id = seed_data["org_a_id"]
    item = await _create_import(org_id, seed_data["user_a_admin_id"], status="QUEUED")
    async with get_tenant_session(org_id) as session:
        repo = DeclarationImportRepository(session, org_id)
        lease = await repo.claim_next("worker-a", now, 120)
    assert lease is not None and lease.import_id == item.id
    wrong_org = WorkerLease(
        uuid.uuid4(), lease.import_id, lease.lease_generation, lease.lease_token, lease.lease_expires_at
    )
    wrong_token = WorkerLease(
        org_id, lease.import_id, lease.lease_generation, uuid.uuid4(), lease.lease_expires_at
    )
    wrong_generation = WorkerLease(
        org_id, lease.import_id, lease.lease_generation + 1, lease.lease_token, lease.lease_expires_at
    )
    async with get_tenant_session(org_id) as session:
        repo = DeclarationImportRepository(session, org_id)
        assert await repo.heartbeat(wrong_org, now + timedelta(seconds=1), 120) is None
        assert await repo.heartbeat(wrong_token, now + timedelta(seconds=1), 120) is None
        assert await repo.heartbeat(wrong_generation, now + timedelta(seconds=1), 120) is None
        # A caller's future clock cannot expire a live database lease.
        assert await repo.heartbeat(lease, lease.lease_expires_at + timedelta(seconds=1), 120) is not None
        from sqlalchemy import text
        await session.execute(text("UPDATE declaration_import SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:job"), {'job':item.id})
        assert await repo.heartbeat(lease, now, 120) is None
        row = await repo.get_by_id_for_update(item.id)
        assert row is not None
        row.status = "FAILED"
        assert await session.flush() is None
        assert await repo.heartbeat(lease, now + timedelta(seconds=1), 120) is None


@pytest.mark.asyncio
async def test_concurrent_claims_never_receive_the_same_import(seed_data) -> None:
    now = datetime.now(timezone.utc)
    org_id = seed_data["org_a_id"]
    item = await _create_import(org_id, seed_data["user_a_admin_id"], status="QUEUED")

    async def claim(worker_id: str):
        async with get_tenant_session(org_id) as session:
            return await DeclarationImportRepository(session, org_id).claim_next(worker_id, now, 120)

    left, right = await asyncio.gather(claim("worker-a"), claim("worker-b"))
    leases = [lease for lease in (left, right) if lease is not None]
    assert len(leases) == 1
    assert leases[0].import_id == item.id


@pytest.mark.asyncio
async def test_worker_rotates_organization_start_after_a_successful_claim(seed_data) -> None:
    now = datetime.now(timezone.utc)
    await _create_import(seed_data["org_a_id"], seed_data["user_a_admin_id"], status="QUEUED")
    await _create_import(seed_data["org_b_id"], seed_data["user_b_admin_id"], status="QUEUED")
    worker = DeclarationImportWorker(object(), clock=lambda: now)
    first = await worker.claim_one()
    assert first is not None
    async with get_tenant_session(first.organization_id) as session:
        assert await DeclarationImportRepository(session, first.organization_id).transition_with_lease(
            first,
            {"QUEUED"},
            "FAILED",
            {"lease_owner": None, "lease_token": None, "lease_expires_at": None},
        )
    second = await worker.claim_one()
    assert second is not None
    assert second.organization_id != first.organization_id
    for lease in (first, second):
        async with get_tenant_session(lease.organization_id) as session:
            row = await DeclarationImportRepository(
                session, lease.organization_id
            ).get_by_id_for_update(lease.import_id)
            assert row is not None
            row.status = "FAILED"
            row.lease_owner = None
            row.lease_token = None
            row.lease_expires_at = None
            await session.flush()


@pytest.mark.asyncio
async def test_schema_guard_short_circuits_without_tenant_enumeration() -> None:
    class CatalogOnlySession:
        async def execute(self, _statement, _parameters=None):
            class Result:
                def scalars(self):
                    class Scalars:
                        def all(self):
                            return ["lease_token", "lease_generation"]

                    return Scalars()

            return Result()

    @asynccontextmanager
    async def unscoped():
        yield CatalogOnlySession()

    async def tenant_never_called(_org_id):
        raise AssertionError("tenant context must not open before schema is ready")
        yield  # pragma: no cover

    worker = DeclarationImportWorker(
        object(),
        unscoped_session_factory=unscoped,
        tenant_session_factory=tenant_never_called,
    )
    assert (await worker.run_once()).code == "SCHEMA_NOT_READY"


@pytest.mark.asyncio
async def test_disabled_worker_does_not_probe_schema_or_claim_work() -> None:
    @asynccontextmanager
    async def unscoped_never_called():
        raise AssertionError("disabled worker must not probe schema or claim work")
        yield  # pragma: no cover

    worker = DeclarationImportWorker(
        object(),
        configured=settings.model_copy(update={"declaration_import_enabled": False}),
        unscoped_session_factory=unscoped_never_called,
    )

    assert (await worker.run_once()).code == "DECLARATION_IMPORT_DISABLED"


@pytest.mark.asyncio
async def test_dispatcher_reads_only_organization_id_unscoped_and_claims_in_tenant_context(seed_data) -> None:
    org_ids = (seed_data["org_a_id"], seed_data["org_b_id"])
    await _create_import(org_ids[0], seed_data["user_a_admin_id"], status="QUEUED")
    tenant_contexts: list[uuid.UUID] = []

    class SpySession:
        async def execute(self, statement):
            selected = tuple(statement.selected_columns)
            assert selected == (Organization.id,)

            class Result:
                def scalars(self):
                    class Scalars:
                        def all(self):
                            return org_ids

                    return Scalars()

            return Result()

    @asynccontextmanager
    async def unscoped():
        yield SpySession()

    @asynccontextmanager
    async def tenant(org_id: uuid.UUID):
        tenant_contexts.append(org_id)
        async with get_tenant_session(org_id) as session:
            yield session

    lease = await DeclarationImportWorker(
        object(),
        unscoped_session_factory=unscoped,
        tenant_session_factory=tenant,
    ).claim_one()
    assert lease is not None
    assert tenant_contexts == [org_ids[0]]


@pytest.mark.asyncio
async def test_worker_retry_cap_uses_runs_not_provider_attempt_count(seed_data) -> None:
    now = datetime.now(timezone.utc)
    org_id = seed_data["org_a_id"]
    item = await _create_import(org_id, seed_data["user_a_admin_id"], status="QUEUED")
    async with get_tenant_session(org_id) as session:
        for _ in range(3):
            session.add(
                DeclarationExtractionRun(
                    organization_id=org_id,
                    import_id=item.id,
                    status="FAILED",
                    provider="test",
                    model_name="test",
                    prompt_version="test",
                    schema_version="test",
                    harness_version="test",
                )
            )
        row = await DeclarationImportRepository(session, org_id).get_by_id_for_update(item.id)
        assert row is not None
        row.attempt_count = 0

    async with get_tenant_session(org_id) as session:
        assert await DeclarationImportRepository(session, org_id).count_extraction_runs(item.id) == 3

    class NoPipeline:
        async def run(self, _lease):
            raise AssertionError("retry-capped import must not invoke the pipeline")

    worker = DeclarationImportWorker(NoPipeline(), clock=lambda: now)
    result = await worker.run_once()
    assert result.code == "WORKER_ATTEMPT_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_heartbeat_fence_loss_cancels_and_awaits_blocking_pipeline(seed_data) -> None:
    org_id = seed_data["org_a_id"]
    await _create_import(org_id, seed_data["user_a_admin_id"], status="QUEUED")
    cancelled = asyncio.Event()

    class BlockingPipeline:
        async def run(self, _lease):
            try:
                async with get_tenant_session(_lease.organization_id) as session:
                    row = await DeclarationImportRepository(
                        session, _lease.organization_id
                    ).get_by_id_for_update(_lease.import_id)
                    assert row is not None
                    row.lease_token = uuid.uuid4()
                    await session.flush()
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    async def immediate_sleep(_seconds: float) -> None:
        await asyncio.sleep(0)

    result = await DeclarationImportWorker(BlockingPipeline(), sleep=immediate_sleep).run_once()
    assert result.code == "LEASE_LOST"
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_heartbeat_sleep_exception_cancels_pipeline(seed_data) -> None:
    org_id = seed_data["org_a_id"]
    await _create_import(org_id, seed_data["user_a_admin_id"], status="QUEUED")
    cancelled = asyncio.Event()

    class BlockingPipeline:
        async def run(self, _lease):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    async def failing_sleep(_seconds: float) -> None:
        raise RuntimeError("timer failed")

    result = await DeclarationImportWorker(BlockingPipeline(), sleep=failing_sleep).run_once()
    assert result.code == "LEASE_LOST"
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_normal_pipeline_completion_cleans_up_heartbeat_task(seed_data) -> None:
    org_id = seed_data["org_a_id"]
    await _create_import(org_id, seed_data["user_a_admin_id"], status="QUEUED")
    heartbeat_cancelled = asyncio.Event()

    class SuccessfulPipeline:
        async def run(self, _lease):
            return None

    async def waiting_sleep(_seconds: float) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            heartbeat_cancelled.set()
            raise

    result = await DeclarationImportWorker(SuccessfulPipeline(), sleep=waiting_sleep).run_once()
    assert result.code == "PROCESSED"
    assert heartbeat_cancelled.is_set()


@pytest.mark.asyncio
async def test_pipeline_exception_cleans_up_heartbeat_task(seed_data) -> None:
    org_id = seed_data["org_a_id"]
    await _create_import(org_id, seed_data["user_a_admin_id"], status="QUEUED")
    heartbeat_cancelled = asyncio.Event()

    class FailingPipeline:
        async def run(self, _lease):
            raise RuntimeError("safe test failure")

    async def waiting_sleep(_seconds: float) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            heartbeat_cancelled.set()
            raise

    result = await DeclarationImportWorker(FailingPipeline(), sleep=waiting_sleep).run_once()
    assert result.code == "PIPELINE_ERROR"
    assert heartbeat_cancelled.is_set()


@pytest.mark.asyncio
async def test_simultaneous_pipeline_and_heartbeat_completion_prefers_pipeline() -> None:
    barrier = asyncio.Event()
    lease = WorkerLease(
        uuid.uuid4(), uuid.uuid4(), 1, uuid.uuid4(), datetime.now(timezone.utc)
    )

    class Pipeline:
        async def run(self, _lease):
            barrier.set()
            await asyncio.sleep(0)

    class SimultaneousWorker(DeclarationImportWorker):
        async def _heartbeat(self, _lease):
            await barrier.wait()
            return True

    result = await SimultaneousWorker(Pipeline())._run_claimed(lease)
    assert result.code == "PROCESSED"
