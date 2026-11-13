"""Fenced declaration-import worker coverage."""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import psycopg2
from fastapi import HTTPException
from sqlalchemy import func, select

from tests.conftest import MIGRATOR_SYNC_URL
from tests.declaration_queue_fixture import queue_fixture
from tests.test_error_tracking import offline_tracker as offline_tracker
from tests.test_property_source_enforcement import synthetic_enforcement
from app.core.config import settings
from app.core.db import get_worker_tenant_session as get_tenant_session
from app.core.db import get_worker_unscoped_session
from app.models.declaration_import import DeclarationExtractionRun, DeclarationImport
from app.models.organization import Organization
from app.services.ai_settings_service import _cipher, effective_ai_settings
from app.repositories.declaration_import_repo import (
    DeclarationImportRepository,
    WorkerLease,
)
from app.workers.declaration_import import DeclarationImportWorker


@pytest.fixture
def seed_data(seed_data, monkeypatch):
    """Keep each worker scenario out of retained imports from other fixtures."""
    isolated = {key: uuid.uuid4() for key in seed_data}
    with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection:
        with connection, connection.cursor() as cursor:
            for org in ('a', 'b'):
                org_key = f'org_{org}_id'
                cursor.execute('INSERT INTO organization (id,name) VALUES (%s,%s)',
                               (str(isolated[org_key]), f'Worker {org} {isolated[org_key]}'))
                roles = ('admin', 'clerk', 'readonly') if org == 'a' else ('admin',)
                for role in roles:
                    user_key = f'user_{org}_{role}_id'
                    cursor.execute(
                        'INSERT INTO "user" (id,organization_id,email,password_hash,display_name) '
                        'SELECT %s,%s,%s,password_hash,display_name FROM "user" WHERE id=%s',
                        (str(isolated[user_key]), str(isolated[org_key]),
                         f'{isolated[user_key]}@worker.example', str(seed_data[user_key])),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError('Worker namespace requires the original seed user')
                    cursor.execute(
                        'INSERT INTO membership (id,organization_id,user_id,role) '
                        'SELECT %s,%s,%s,role FROM membership WHERE organization_id=%s AND user_id=%s',
                        (str(uuid.uuid4()), str(isolated[org_key]), str(isolated[user_key]),
                         str(seed_data[org_key]), str(seed_data[user_key])),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError('Worker namespace requires the original seed membership')
                mandate_key = f'mandate_{org}_id'
                cursor.execute(
                    'INSERT INTO management_mandate (id,organization_id,mandate_type,label,start_date) '
                    'SELECT %s,%s,mandate_type,label,start_date FROM management_mandate WHERE id=%s',
                    (str(isolated[mandate_key]), str(isolated[org_key]), str(seed_data[mandate_key])),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError('Worker namespace requires the original seed mandate')

    original_directory = DeclarationImportWorker._organization_ids
    namespace = {isolated['org_a_id'], isolated['org_b_id']}

    async def organization_ids(worker):
        # Preserve the real unscoped query, including the dispatcher spy assertions.
        found = await original_directory(worker)
        return tuple(org_id for org_id in found if org_id in namespace)

    monkeypatch.setattr(DeclarationImportWorker, '_organization_ids', organization_ids)
    return isolated


@pytest.fixture(autouse=True)
def enable_declaration_imports(configured_ai) -> None:
    pass


def test_declaration_import_worker_module_is_available() -> None:
    assert DeclarationImportWorker is not None


async def test_worker_reads_changed_key_model_and_switch_at_the_next_job(seed_data, configured_ai):
    org, actor = seed_data['org_a_id'], seed_data['user_a_admin_id']
    seen = []
    class Pipeline:
        async def run(self, lease):
            seen.append((worker._active_ai.model, worker._active_ai.api_key.get_secret_value()))
            async with get_tenant_session(org) as db:
                assert await DeclarationImportRepository(db, org).transition_with_lease(lease, {'QUEUED'}, 'FAILED', {
                    'current_stage':'failed', 'error_code':'SYNTHETIC', 'error_message':'SYNTHETIC',
                    'lease_owner':None, 'lease_token':None, 'lease_expires_at':None,
                })
    worker = DeclarationImportWorker(Pipeline())
    await _create_import(org, actor, status='QUEUED')
    await worker.run_once(organization_id=org)
    configured_ai(org, model='future-model', api_key='synthetic-replaced-key')
    await _create_import(org, actor, status='QUEUED')
    await worker.run_once(organization_id=org)
    configured_ai(org, import_enabled=False)
    assert (await worker.run_once(organization_id=org)).code == 'DECLARATION_IMPORT_DISABLED'
    assert [model for model, key in seen] == ['gpt-5.6-luna', 'future-model']
    assert [key for model, key in seen] == ['synthetic-test-key', 'synthetic-replaced-key']


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
async def test_worker_skips_organization_with_undecryptable_key_and_keeps_rotating(
    seed_data, configured_ai, caplog
) -> None:
    now = datetime.now(timezone.utc)
    first_org, second_org = sorted((seed_data["org_a_id"], seed_data["org_b_id"]))
    first_actor = (
        seed_data["user_a_admin_id"]
        if first_org == seed_data["org_a_id"]
        else seed_data["user_b_admin_id"]
    )
    second_actor = (
        seed_data["user_b_admin_id"]
        if second_org == seed_data["org_b_id"]
        else seed_data["user_a_admin_id"]
    )
    # Stored under another tenant's cipher, as after a SESSION_SECRET rotation.
    ciphertext = _cipher(uuid.uuid4()).encrypt(b"stale-secret-key").decode()
    with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection:
        with connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE organization_ai_settings SET encrypted_api_key=%s "
                "WHERE organization_id=%s",
                (ciphertext, str(first_org)),
            )
    async with get_tenant_session(first_org) as session:
        with pytest.raises(HTTPException) as excinfo:
            await effective_ai_settings(session, first_org)
    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == (
        "Der gespeicherte KI-Schlüssel kann nicht entschlüsselt werden. "
        "Bitte hinterlegen Sie ihn erneut in den Einstellungen."
    )
    queued = await _create_import(second_org, second_actor, status="QUEUED")
    worker = DeclarationImportWorker(object(), clock=lambda: now)
    with caplog.at_level(logging.WARNING, logger="app.workers.declaration_import"):
        second_lease = await worker.claim_one()
    assert second_lease is not None
    assert second_lease.organization_id == second_org
    assert second_lease.import_id == queued.id
    warnings = [record for record in caplog.records if record.name == "app.workers.declaration_import"]
    assert len(warnings) == 1
    assert warnings[0].getMessage() == (
        f"declaration worker result=AI_KEY_UNREADABLE organization_id={first_org}"
    )
    for record in caplog.records:
        assert ciphertext not in record.getMessage()
        assert "stale-secret-key" not in record.getMessage()
        assert record.exc_info is record.exc_text is None
    # Re-entering the key under the current secret claims that organization again.
    configured_ai(first_org)
    first_item = await _create_import(first_org, first_actor, status="QUEUED")
    first_lease = await worker.claim_one()
    assert first_lease is not None
    assert first_lease.organization_id == first_org
    assert first_lease.import_id == first_item.id
    for lease in (second_lease, first_lease):
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
async def test_disabled_worker_does_not_probe_schema_or_claim_work(seed_data, configured_ai) -> None:
    configured_ai(seed_data["org_a_id"], import_enabled=False)
    @asynccontextmanager
    async def unscoped_never_called():
        raise AssertionError("disabled worker must not probe schema or claim work")
        yield  # pragma: no cover

    worker = DeclarationImportWorker(
        object(),
        unscoped_session_factory=unscoped_never_called,
    )

    assert (await worker.run_once(organization_id=seed_data["org_a_id"])).code == "DECLARATION_IMPORT_DISABLED"


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
async def test_unexpected_provider_construction_error_counts_a_safe_failed_attempt(
    seed_data, monkeypatch, caplog
) -> None:
    sentinel = "Synthetic document fragment: owner account DE123456789"

    def fail_provider(_effective):
        raise ModuleNotFoundError(sentinel)

    monkeypatch.setattr("app.declarations.provider_runtime.create_extraction_provider", fail_provider)
    org_id = seed_data["org_a_id"]
    item = await _create_import(org_id, seed_data["user_a_admin_id"], status="QUEUED")
    now = datetime.now(timezone.utc)
    worker = DeclarationImportWorker(clock=lambda: now)
    async with get_tenant_session(org_id) as session:
        db_before = await session.scalar(select(func.clock_timestamp()))
    with caplog.at_level(logging.WARNING, logger="app.workers.declaration_import"):
        async with synthetic_enforcement(seed_data):
            result = await worker.run_once(organization_id=org_id)

    assert result.code == "WORKER_UNEXPECTED_ERROR"
    assert result.import_id == item.id
    async with get_tenant_session(org_id) as session:
        db_after = await session.scalar(select(func.clock_timestamp()))
        repo = DeclarationImportRepository(session, org_id)
        row = await repo.get_by_id_for_update(item.id)
        assert row is not None
        assert row.status == "RETRY_WAIT"
        assert row.current_stage == "retry_wait"
        assert row.error_code == row.error_message == "WORKER_UNEXPECTED_ERROR"
        # Enforced worker transitions use the database clock for retry deadlines.
        assert db_before + timedelta(minutes=5) <= row.next_attempt_at <= db_after + timedelta(minutes=5)
        assert row.lease_owner is row.lease_token is row.lease_expires_at is None
        assert row.attempt_count == 0
        assert await repo.count_extraction_runs(item.id) == 1
        run = await session.scalar(
            select(DeclarationExtractionRun).where(DeclarationExtractionRun.import_id == item.id)
        )
        assert run is not None
        assert run.status == "FAILED"
        assert run.error_code == "WORKER_UNEXPECTED_ERROR"
        assert run.completed_at == now
        assert run.input_tokens == run.output_tokens == run.estimated_cost_usd == 0
        assert worker._active_ai.provider == "openai"
        assert run.provider == "OpenAiDeclarationProvider"
        assert run.model_name == worker._active_ai.model
        assert run.prompt_version == settings.declaration_prompt_version
        assert run.schema_version == settings.declaration_schema_version
        assert run.harness_version == settings.declaration_harness_version
    warnings = [record for record in caplog.records if record.name == "app.workers.declaration_import"]
    assert len(warnings) == 1
    record = warnings[0]
    assert record.getMessage() == (
        f"declaration worker result=WORKER_UNEXPECTED_ERROR import_id={item.id} "
        f"error_type=ModuleNotFoundError location={__name__}:fail_provider"
    )
    for record in caplog.records:
        assert sentinel not in record.getMessage()
        assert sentinel not in str(record.args)
        assert record.exc_info is record.exc_text is None


@pytest.mark.asyncio
async def test_repeated_unexpected_worker_errors_reach_the_existing_attempt_limit(
    seed_data, monkeypatch
) -> None:
    def fail_provider(_effective):
        raise ModuleNotFoundError("Synthetic document content")

    monkeypatch.setattr("app.declarations.provider_runtime.create_extraction_provider", fail_provider)
    org_id = seed_data["org_a_id"]
    item = await _create_import(org_id, seed_data["user_a_admin_id"], status="QUEUED")
    configured = settings.model_copy(update={"declaration_max_attempts": 3})
    worker = DeclarationImportWorker(configured=configured)
    for attempt in range(1, configured.declaration_max_attempts + 1):
        result = await worker.run_once(organization_id=org_id)
        assert result.code == "WORKER_UNEXPECTED_ERROR"
        async with get_tenant_session(org_id) as session:
            repo = DeclarationImportRepository(session, org_id)
            assert await repo.count_extraction_runs(item.id) == attempt
            row = await repo.get_by_id_for_update(item.id)
            assert row is not None
            row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    result = await worker.run_once(organization_id=org_id)
    assert result.code == "WORKER_ATTEMPT_LIMIT_REACHED"
    assert result.import_id == item.id
    async with get_tenant_session(org_id) as session:
        repo = DeclarationImportRepository(session, org_id)
        row = await repo.get_by_id_for_update(item.id)
        assert row is not None
        assert row.status == "FAILED"
        assert row.error_code == row.error_message == "WORKER_ATTEMPT_LIMIT_REACHED"
        assert row.next_attempt_at is None
        assert row.lease_owner is row.lease_token is row.lease_expires_at is None
        assert await repo.count_extraction_runs(item.id) == configured.declaration_max_attempts


@pytest.mark.asyncio
async def test_run_forever_logs_only_safe_error_type_and_location(monkeypatch, caplog) -> None:
    sentinel = "Synthetic document fragment: owner account DE987654321"

    async def fail_schema_ready():
        raise RuntimeError(sentinel)

    async def stop_sleep(_seconds):
        raise asyncio.CancelledError()

    worker = DeclarationImportWorker(sleep=stop_sleep)
    monkeypatch.setattr(worker, "schema_ready", fail_schema_ready)
    with caplog.at_level(logging.WARNING, logger="app.workers.declaration_import"):
        with pytest.raises(asyncio.CancelledError):
            await worker.run_forever()
    warnings = [record for record in caplog.records if record.name == "app.workers.declaration_import"]
    assert len(warnings) == 1
    record = warnings[0]
    assert record.getMessage() == (
        "declaration worker result=UNEXPECTED "
        f"error_type=RuntimeError location={__name__}:fail_schema_ready"
    )
    for record in caplog.records:
        assert sentinel not in record.getMessage()
        assert sentinel not in str(record.args)
        assert record.exc_info is record.exc_text is None


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
async def test_failing_declaration_import_is_one_tracker_event_with_the_import_id(seed_data, offline_tracker) -> None:
    events, tracker = offline_tracker
    item = await _create_import(seed_data["org_a_id"], seed_data["user_a_admin_id"], status="QUEUED")
    sentinel = "Synthetic document fragment: tenant account DE111222333"

    class FailingPipeline:
        async def run(self, _lease):
            raise RuntimeError(sentinel)

    async def waiting_sleep(_seconds: float) -> None:
        await asyncio.Event().wait()

    result = await DeclarationImportWorker(FailingPipeline(), sleep=waiting_sleep).run_once()
    tracker.flush()
    assert result.code == "PIPELINE_ERROR"
    assert len(events) == 1
    assert events[0]["tags"] == {"worker": "declaration_import", "job_id": str(item.id), "request_id": str(item.id)}
    assert sentinel not in json.dumps(events[0])


@pytest.mark.asyncio
async def test_pipeline_error_logs_only_safe_error_type_and_location(seed_data, caplog) -> None:
    org_id = seed_data["org_a_id"]
    item = await _create_import(org_id, seed_data["user_a_admin_id"], status="QUEUED")
    sentinel = "Synthetic document fragment: tenant account DE111222333"

    class FailingPipeline:
        async def run(self, _lease):
            raise RuntimeError(sentinel)

    async def waiting_sleep(_seconds: float) -> None:
        await asyncio.Event().wait()

    with caplog.at_level(logging.WARNING, logger="app.workers.declaration_import"):
        result = await DeclarationImportWorker(FailingPipeline(), sleep=waiting_sleep).run_once()
    assert result.code == "PIPELINE_ERROR"
    warnings = [record for record in caplog.records if record.name == "app.workers.declaration_import"]
    assert len(warnings) == 1
    assert warnings[0].getMessage() == (
        f"declaration worker result=PIPELINE_ERROR import_id={item.id} "
        f"error_type=RuntimeError location={__name__}:run"
    )
    for record in caplog.records:
        assert sentinel not in record.getMessage()
        assert sentinel not in str(record.args)
        assert record.exc_info is record.exc_text is None


async def _make_retry_due(org_id, import_id) -> None:
    async with get_tenant_session(org_id) as session:
        row = await DeclarationImportRepository(session, org_id).get_by_id_for_update(import_id)
        assert row is not None
        row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)


async def _enter_preflight(lease, *, start_run: bool) -> None:
    async with get_tenant_session(lease.organization_id) as session:
        repo = DeclarationImportRepository(session, lease.organization_id)
        assert await repo.transition_with_lease(
            lease, {"QUEUED", "RETRY_WAIT"}, "PREFLIGHT", {"current_stage": "preflight"}
        )
        if start_run:
            session.add(DeclarationExtractionRun(
                organization_id=lease.organization_id, import_id=lease.import_id, status="RUNNING",
                provider="test", model_name="test", prompt_version="test",
                schema_version="test", harness_version="test",
            ))
            await session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("entered_preflight", [False, True])
async def test_pipeline_error_before_a_run_counts_attempts_up_to_the_limit(
    seed_data, entered_preflight
) -> None:
    org_id = seed_data["org_a_id"]
    item = await _create_import(org_id, seed_data["user_a_admin_id"], status="QUEUED")

    class FailingPipeline:
        async def run(self, lease):
            if entered_preflight:
                await _enter_preflight(lease, start_run=False)
            raise RuntimeError("pipeline failure handling failed too")

    async def waiting_sleep(_seconds: float) -> None:
        await asyncio.Event().wait()

    configured = settings.model_copy(update={"declaration_max_attempts": 2})
    worker = DeclarationImportWorker(FailingPipeline(), configured=configured, sleep=waiting_sleep)
    for attempt in range(1, configured.declaration_max_attempts + 1):
        result = await worker.run_once(organization_id=org_id)
        assert result.code == "PIPELINE_ERROR"
        async with get_tenant_session(org_id) as session:
            repo = DeclarationImportRepository(session, org_id)
            assert await repo.count_extraction_runs(item.id) == attempt
            row = await repo.get_by_id_for_update(item.id)
            assert row is not None
            assert row.status == "RETRY_WAIT"
            assert row.lease_token is None
        await _make_retry_due(org_id, item.id)
    result = await worker.run_once(organization_id=org_id)
    assert result.code == "WORKER_ATTEMPT_LIMIT_REACHED"
    async with get_tenant_session(org_id) as session:
        row = await DeclarationImportRepository(session, org_id).get_by_id_for_update(item.id)
        assert row is not None
        assert row.status == "FAILED"


@pytest.mark.asyncio
async def test_pipeline_error_after_its_run_started_is_not_counted_twice(seed_data) -> None:
    org_id = seed_data["org_a_id"]
    item = await _create_import(org_id, seed_data["user_a_admin_id"], status="QUEUED")

    class FailingAfterRunPipeline:
        async def run(self, lease):
            await _enter_preflight(lease, start_run=True)
            raise RuntimeError("pipeline failure handling failed too")

    async def waiting_sleep(_seconds: float) -> None:
        await asyncio.Event().wait()

    result = await DeclarationImportWorker(FailingAfterRunPipeline(), sleep=waiting_sleep).run_once(
        organization_id=org_id
    )
    assert result.code == "PIPELINE_ERROR"
    async with get_tenant_session(org_id) as session:
        repo = DeclarationImportRepository(session, org_id)
        assert await repo.count_extraction_runs(item.id) == 1
        row = await repo.get_by_id_for_update(item.id)
        assert row is not None
        assert row.status == "PREFLIGHT"


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


def _insert_expired_signup_request(email: str) -> None:
    with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection, connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO signup_request "
            "(email_normalized,organization_name,admin_name,password_hash,token_hash,expires_at) "
            "VALUES (%s,'Synthetic org','Synthetic name','synthetic hash',"
            "sha256(convert_to(%s,'UTF8')),clock_timestamp()-interval '8 days')",
            (email, email))


def _signup_request_count(email: str) -> int:
    with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection, connection, connection.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM public.signup_request WHERE email_normalized=%s', (email,))
        return cursor.fetchone()[0]


@pytest.mark.asyncio
async def test_worker_cycle_deletes_expired_signup_request_without_signup_traffic(monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, 'deployment_mode', 'hosted')
    _insert_expired_signup_request('worker-stale@example.test')

    async def stop_after_first_cycle(_seconds: float) -> None:
        raise asyncio.CancelledError()

    worker = DeclarationImportWorker(sleep=stop_after_first_cycle)
    with caplog.at_level(logging.INFO, logger='app.workers.declaration_import'):
        with pytest.raises(asyncio.CancelledError):
            await worker.run_forever()
    assert _signup_request_count('worker-stale@example.test') == 0
    lines = [record for record in caplog.records if 'result=SIGNUP_CLEANUP ' in record.getMessage()]
    assert len(lines) == 1
    assert 'removed=' in lines[0].getMessage()
    assert 'worker-stale@example.test' not in lines[0].getMessage()


@pytest.mark.asyncio
async def test_worker_signup_cleanup_runs_at_most_once_per_interval(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'deployment_mode', 'hosted')
    base = datetime.now(timezone.utc)
    ticks = iter([0, 30, 60])
    worker = DeclarationImportWorker(clock=lambda: base + timedelta(seconds=next(ticks)))
    _insert_expired_signup_request('interval-first@example.test')
    await worker._cleanup_signup_requests()
    assert _signup_request_count('interval-first@example.test') == 0
    _insert_expired_signup_request('interval-second@example.test')
    await worker._cleanup_signup_requests()
    assert _signup_request_count('interval-second@example.test') == 1
    await worker._cleanup_signup_requests()
    assert _signup_request_count('interval-second@example.test') == 0


@pytest.mark.asyncio
async def test_worker_signup_cleanup_is_skipped_in_selfhost_mode(monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, 'deployment_mode', 'selfhost')
    _insert_expired_signup_request('selfhost-stale@example.test')
    await DeclarationImportWorker()._cleanup_signup_requests()
    assert _signup_request_count('selfhost-stale@example.test') == 1
    assert not [record for record in caplog.records if 'SIGNUP_CLEANUP' in record.getMessage()]


@pytest.mark.asyncio
async def test_worker_signup_cleanup_sets_bounded_statement_timeouts(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'deployment_mode', 'hosted')
    statements: list[str] = []
    original_factory = get_worker_unscoped_session

    @asynccontextmanager
    async def recording_factory():
        async with original_factory() as session:
            original_execute = session.execute

            async def execute(*args, **kwargs):
                statements.append(str(args[0]))
                return await original_execute(*args, **kwargs)

            session.execute = execute
            yield session

    worker = DeclarationImportWorker(unscoped_session_factory=recording_factory)
    await worker._cleanup_signup_requests()
    issued = '\n'.join(statements)
    assert "SET LOCAL lock_timeout='100ms'" in issued
    assert "SET LOCAL statement_timeout='1s'" in issued


@pytest.mark.asyncio
async def test_worker_signup_cleanup_failure_logs_bounded_line_and_recovers_next_cycle(
    monkeypatch, caplog,
) -> None:
    monkeypatch.setattr(settings, 'deployment_mode', 'hosted')
    _insert_expired_signup_request('cleanup-locked@example.test')
    clock_now = datetime.now(timezone.utc)

    def clock() -> datetime:
        return clock_now

    with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection:
        with connection.cursor() as cursor:
            cursor.execute('LOCK TABLE public.signup_request IN ACCESS EXCLUSIVE MODE')
            cycles = 0

            async def advance_and_stop_after_three_cycles(_seconds: float) -> None:
                nonlocal clock_now, cycles
                clock_now += timedelta(seconds=45)
                cycles += 1
                if cycles >= 3:
                    raise asyncio.CancelledError()

            worker = DeclarationImportWorker(sleep=advance_and_stop_after_three_cycles, clock=clock)
            with caplog.at_level(logging.INFO, logger='app.workers.declaration_import'):
                with pytest.raises(asyncio.CancelledError):
                    await worker.run_forever()
            failures = [record for record in caplog.records
                        if 'result=SIGNUP_CLEANUP_FAILED' in record.getMessage()]
            # The first cycle fails; the second cycle is inside the cadence
            # interval and must not re-attempt; the third cycle retries after
            # the interval and fails again.
            assert len(failures) == 2
            for record in failures:
                assert 'cleanup-locked@example.test' not in record.getMessage()
                assert 'error_type=' in record.getMessage()
            idles = [record for record in caplog.records if 'result=IDLE' in record.getMessage()]
            assert len(idles) == 3
    assert _signup_request_count('cleanup-locked@example.test') == 1
    await DeclarationImportWorker()._cleanup_signup_requests()
    assert _signup_request_count('cleanup-locked@example.test') == 0
