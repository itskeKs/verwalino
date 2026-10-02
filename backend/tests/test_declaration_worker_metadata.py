"""Scheduling and cancellation must work before source rows become visible."""
import uuid
from datetime import datetime,timezone
import pytest
from sqlalchemy import text
from app.core.db import get_worker_tenant_session as get_tenant_session
from app.repositories.declaration_import_repo import DeclarationImportRepository, DeclarationPrincipalDenied
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_declaration_import_api import isolated_document_store, enable_declaration_imports  # noqa: F401
from tests.test_declaration_job_principal import new_draft, inspect_job, revoke
from tests.test_scoped_account_statements import client, mutate

pytestmark = pytest.mark.asyncio


async def source_policy(enabled):
    # Disposable DB only. Mimic the upcoming source RLS barrier without enabling
    # any production source policies. All tests restore this temporary policy.
    sql = ("CREATE POLICY test_worker_source_principal ON declaration_import AS RESTRICTIVE FOR SELECT USING (scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK'))"
           if enabled else 'DROP POLICY IF EXISTS test_worker_source_principal ON declaration_import')
    await mutate(text(sql),{})


async def queued(seed):
    ids = {'org':seed['org_a_id'],'actor':seed['user_a_admin_id']}
    job = await new_draft(ids)
    async with client(ids,'actor') as api:
        response = await api.post(f'/api/declaration-imports/{job}/start',headers={'Idempotency-Key':str(uuid.uuid4())})
        assert response.status_code == 200,response.text
    return ids,job


async def test_claim_resolves_principal_before_reading_source_row(scope_seed,migrator_session):
    await migrator_session.commit()
    ids,job = await queued(scope_seed)
    await source_policy(True)
    try:
        async with get_tenant_session(ids['org']) as session:
            assert await session.scalar(text('SELECT count(*) FROM declaration_import')) == 0
            lease = await DeclarationImportRepository(session,ids['org']).claim_next('metadata-test',datetime.now(timezone.utc),120)
            assert lease is not None and str(lease.import_id) == job
            assert await session.scalar(text("SELECT current_setting('app.current_user_id')")) == str(ids['actor'])
        async with get_tenant_session(ids['org']) as session:
            assert await DeclarationImportRepository(session,ids['org']).lock_current_lease(lease,{'QUEUED'}) is not None
    finally:
        await source_policy(False)


async def test_hidden_revoked_lease_still_commits_fenced_cancellation(scope_seed,migrator_session):
    await migrator_session.commit()
    ids,_ = await queued(scope_seed)
    async with get_tenant_session(ids['org']) as session:
        lease = await DeclarationImportRepository(session,ids['org']).claim_next('metadata-test',datetime.now(timezone.utc),120)
    await revoke(ids)
    await source_policy(True)
    try:
        async with get_tenant_session(ids['org']) as session:
            result = await DeclarationImportRepository(session,ids['org']).heartbeat(lease,datetime.now(timezone.utc),120)
            assert isinstance(result,DeclarationPrincipalDenied)
    finally:
        await source_policy(False)
    item,_,_,audits = await inspect_job(lease)
    assert item['status'] == 'CANCELLED' and audits == 1


async def test_scheduler_metadata_is_bounded_and_tenant_fenced(scope_seed,migrator_session,seed_data):
    await migrator_session.commit()
    ids,job = await queued(scope_seed)
    await queued(scope_seed)
    async with get_tenant_session(ids['org']) as session:
        rows = (await session.execute(text('SELECT * FROM next_declaration_queue_metadata(:org,clock_timestamp())'),{'org':ids['org']})).mappings().all()
        assert len(rows) == 1 and str(rows[0]['import_id']) == job
        assert set(rows[0]) == {'import_id','queue_request_id','lease_generation','lease_token'}
    async with get_tenant_session(seed_data['org_b_id']) as session:
        assert not (await session.execute(text('SELECT * FROM next_declaration_queue_metadata(:org,clock_timestamp())'),{'org':ids['org']})).all()
        assert not await session.scalar(text("SELECT lock_declaration_lease_metadata(:org,:job,NULL,0,:token,ARRAY['QUEUED'])"),{'org':ids['org'],'job':uuid.UUID(job),'token':uuid.uuid4()})


async def test_lease_expiring_during_row_lock_wait_is_rejected(scope_seed,migrator_session):
    import asyncio
    await migrator_session.commit()
    ids,_ = await queued(scope_seed)
    async with get_tenant_session(ids['org']) as session:
        lease = await DeclarationImportRepository(session,ids['org']).claim_next('expiry-test',datetime.now(timezone.utc),120)
    await mutate(text("UPDATE declaration_import SET lease_expires_at=clock_timestamp()+interval '1 second' WHERE id=:job"),{'job':lease.import_id})
    async def wait_for_lease():
        async with get_tenant_session(ids['org']) as session:
            await session.execute(text("SET LOCAL lock_timeout='5s'"))
            return await DeclarationImportRepository(session,ids['org']).lock_current_lease(lease,{'QUEUED'})
    async with get_tenant_session(ids['org']) as locker:
        await locker.execute(text('SELECT id FROM declaration_import WHERE id=:job FOR UPDATE'),{'job':lease.import_id})
        waiter = asyncio.create_task(wait_for_lease())
        # Observe a real row-lock wait before crossing the database deadline.
        from sqlalchemy.ext.asyncio import create_async_engine
        from tests.conftest import MIGRATOR_URL
        engine = create_async_engine(MIGRATOR_URL)
        try:
            async with engine.connect() as observer:
                for _ in range(100):
                    blocked = await observer.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE '%SELECT lock_declaration_lease_metadata%')"))
                    if blocked:
                        break
                    await asyncio.sleep(.01)
                assert blocked
            await locker.execute(text('SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM lease_expires_at-clock_timestamp()))+0.02) FROM declaration_import WHERE id=:job'),{'job':lease.import_id})
        finally:
            await engine.dispose()
    assert await asyncio.wait_for(waiter,2) is None


async def test_readiness_requires_metadata_helpers(scope_seed,migrator_session):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from app.workers.declaration_import import DeclarationImportWorker
    migration = importlib.import_module('migrations.versions.0046_declaration_worker_metadata')
    assert await DeclarationImportWorker(object()).schema_ready()
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as connection:
            nested = connection.begin_nested()
            try:
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
                    assert connection.scalar(text("SELECT to_regprocedure('next_declaration_queue_metadata(uuid,timestamptz)')")) is None
                    migration.upgrade()
                    assert connection.scalar(text("SELECT to_regprocedure('next_declaration_queue_metadata(uuid,timestamptz)')")) is not None
            finally:
                nested.rollback()
    finally:
        engine.dispose()
