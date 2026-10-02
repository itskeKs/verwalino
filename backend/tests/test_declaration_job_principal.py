"""An explicit queue actor, not the draft creator, authorizes extraction work."""
import uuid
import pytest
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_scoped_account_statements import client
from tests.test_declaration_import_api import isolated_document_store, enable_declaration_imports  # noqa: F401

pytestmark = pytest.mark.asyncio


async def new_draft(ids):
    async with client(ids, 'actor') as api:
        response = await api.post('/api/declaration-imports', json={'mode': 'NEW_PROPERTY'})
        assert response.status_code == 201, response.text
        import_id = response.json()['id']
        uploaded = await api.post(f'/api/declaration-imports/{import_id}/documents',
            files={'file': ('source.pdf', b'%PDF-1.4\n%%EOF', 'application/pdf')},
            data={'document_role': 'DECLARATION', 'sequence_no': '1'})
        assert uploaded.status_code == 201, uploaded.text
        return import_id


async def test_queue_records_triggering_actor_and_replays_without_replacing_it(scope_seed, migrator_session):
    await migrator_session.commit()
    ids = {'org': scope_seed['org_a_id'], 'actor': scope_seed['user_a_admin_id'], 'clerk': scope_seed['user_a_clerk_id']}
    import_id = await new_draft(ids)
    key = str(uuid.uuid4())
    path = f'/api/declaration-imports/{import_id}/start'
    async with client(ids) as api:
        queued = await api.post(path, headers={'Idempotency-Key': key})
        assert queued.status_code == 200, queued.text
        assert queued.json()['queue_request_id']
        assert queued.json()['command_receipt_id'] == queued.json()['queue_request_id']
        replay = await api.post(path, headers={'Idempotency-Key': key})
        assert replay.json() == queued.json()
        assert (await api.post(path, headers={'Idempotency-Key': str(uuid.uuid4())})).status_code == 409

from datetime import datetime, timezone
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.db import get_tenant_session
from app.repositories.declaration_import_repo import DeclarationImportRepository, DeclarationPrincipalDenied
from tests.test_scoped_account_statements import mutate
from tests.test_declaration_pipeline import _seed_pipeline_import, _RecordingProvider, make_text_pdf
from app.declarations.pipeline import DeclarationPipeline


async def revoke(ids, change='role'):
    statement = ('UPDATE membership SET role=\'READ_ONLY\' WHERE organization_id=:org AND user_id=:actor'
                 if change == 'role' else 'UPDATE "user" SET is_active=false WHERE organization_id=:org AND id=:actor')
    await mutate(text(statement), ids)


async def inspect_job(lease,actor=None):
    async with get_tenant_session(lease.organization_id) as session:
        if actor is not None:
            await session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(actor)})
        item = (await session.execute(text('SELECT * FROM declaration_import WHERE id=:id'), {'id': lease.import_id})).mappings().one()
        candidates = await session.scalar(text('SELECT count(*) FROM declaration_candidate WHERE import_id=:id'), {'id': lease.import_id})
        runs = (await session.execute(text('SELECT status,error_code FROM declaration_extraction_run WHERE import_id=:id'), {'id': lease.import_id})).all()
        audits = await session.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:id AND action='declaration.permission_cancelled'"), {'id': lease.import_id})
        return item, candidates, runs, audits


@pytest.mark.parametrize('change', ['role', 'inactive'])
async def test_claim_cancels_revoked_queue_principal(scope_seed, migrator_session, change):
    await migrator_session.commit()
    ids = {'org': scope_seed['org_a_id'], 'actor': scope_seed['user_a_clerk_id'], 'clerk': scope_seed['user_a_clerk_id']}
    job = await new_draft(ids)
    async with client(ids) as api:
        queued = await api.post(f'/api/declaration-imports/{job}/start', headers={'Idempotency-Key': str(uuid.uuid4())})
        assert queued.status_code == 200
    await revoke(ids, change)
    async with get_tenant_session(ids['org']) as session:
        assert await DeclarationImportRepository(session, ids['org']).claim_next('principal-test', datetime.now(timezone.utc), 120) is None
    async with get_tenant_session(ids['org']) as session:
        row = (await session.execute(text('SELECT status,error_code,lease_token FROM declaration_import WHERE id=:id'), {'id': uuid.UUID(job)})).one()
        assert row == ('CANCELLED', 'JOB_PERMISSION_CHANGED', None)


@pytest.mark.parametrize('phase', ['load', 'bytes', 'provider', 'convert', 'publish'])
async def test_pipeline_durably_cancels_after_revocation(scope_seed, migrator_session, phase):
    await migrator_session.commit()
    seed = {**scope_seed, 'user_a_admin_id': scope_seed['user_a_clerk_id']}
    ids = {'org': seed['org_a_id'], 'actor': seed['user_a_admin_id']}
    target = None
    if phase == 'convert':
        async with client(ids, 'actor') as api:
            created = await api.post('/api/properties', json={'name':'Synthetic existing target', 'management_type':'WEG'})
            assert created.status_code == 201
        target = uuid.UUID(created.json()['id'])
    lease, key, store = await _seed_pipeline_import(seed, make_text_pdf('Synthetic declaration'),
        mode='EXISTING_PROPERTY' if target else 'NEW_PROPERTY', target_property_id=target)
    reads = []
    original_get = store.get
    def observed_get(path):
        reads.append(path)
        return original_get(path)
    store.get = observed_get

    class Provider(_RecordingProvider):
        async def inventory(self, *args, **kwargs):
            result = await super().inventory(*args, **kwargs)
            if phase == 'provider':
                await revoke(ids)
            return result

    class Pipeline(DeclarationPipeline):
        async def _preflight_documents(self, *args):
            if phase == 'bytes':
                await revoke(ids)
            return await super()._preflight_documents(*args)
        async def _convert(self, *args):
            if phase == 'convert':
                await revoke(ids)
            return await super()._convert(*args)
        async def _publish(self, *args):
            if phase == 'publish':
                await revoke(ids)
            return await super()._publish(*args)

    provider = Provider(key)
    if phase == 'load':
        await revoke(ids)
    with pytest.raises(DeclarationPrincipalDenied):
        await Pipeline(provider, store=store).run(lease)
    item, candidates, runs, audits = await inspect_job(lease)
    assert item['status'] == 'CANCELLED' and item['error_code'] == 'JOB_PERMISSION_CHANGED'
    assert item['lease_token'] is None and item['lease_generation'] == lease.lease_generation + 1
    assert candidates == 0 and audits == 1
    assert all(row == ('FAILED', 'JOB_PERMISSION_CHANGED') for row in runs)
    if phase in {'load', 'bytes'}:
        assert reads == [] and provider.calls == []
    if phase == 'provider':
        assert provider.calls == ['inventory'] and item['attempt_count'] >= 1
        assert item['input_tokens'] > 0  # persisted conservative reservation survives cancellation
    assert original_get(next(iter(store._data)))  # original remains intact


async def test_heartbeat_cancel_retry_and_old_receipts_cannot_replace_new_principal(scope_seed, migrator_session):
    await migrator_session.commit()
    ids = {'org': scope_seed['org_a_id'], 'actor': scope_seed['user_a_admin_id'], 'clerk': scope_seed['user_a_clerk_id']}
    job = await new_draft(ids)
    key = str(uuid.uuid4())
    async with client(ids) as api:
        queued = await api.post(f'/api/declaration-imports/{job}/start', headers={'Idempotency-Key': key})
        request = queued.json()['queue_request_id']
    async with get_tenant_session(ids['org']) as session:
        lease = await DeclarationImportRepository(session, ids['org']).claim_next('principal-test', datetime.now(timezone.utc), 120)
        actor = await session.scalar(text("SELECT current_setting('app.current_user_id')"))
        assert actor == str(ids['clerk'])
    await revoke({**ids, 'actor': ids['clerk']})
    async with get_tenant_session(ids['org']) as session:
        assert isinstance(await DeclarationImportRepository(session, ids['org']).heartbeat(lease, datetime.now(timezone.utc), 120), DeclarationPrincipalDenied)
    item, _, _, audits = await inspect_job(lease)
    assert item['status'] == 'CANCELLED' and audits == 1
    async with client(ids, 'actor') as api:
        retried = await api.post(f'/api/declaration-imports/{job}/retry', headers={'Idempotency-Key': str(uuid.uuid4())})
        assert retried.status_code == 200, retried.text
        next_request = retried.json()['queue_request_id']
        assert next_request != request
    # A stale cancellation cannot cancel a newer command, even with a revoked actor.
    async with get_tenant_session(ids['org']) as session:
        assert not await DeclarationImportRepository(session, ids['org']).cancel_unauthorized(lease)
    await mutate(text("UPDATE membership SET role='CLERK' WHERE organization_id=:org AND user_id=:clerk"), ids)
    async with client(ids) as api:
        replay = await api.post(f'/api/declaration-imports/{job}/start', headers={'Idempotency-Key': key})
        assert replay.status_code == 200
        assert replay.json()['command_receipt_id'] == request
        assert replay.json()['queue_request_id'] == next_request
    async with get_tenant_session(ids['org']) as session:
        receipt = (await session.execute(text('SELECT actor_user_id,previous_request_id FROM declaration_queue_request WHERE id=:request'), {'request': uuid.UUID(next_request)})).one()
        assert receipt == (ids['actor'], uuid.UUID(request))
        creator = await session.scalar(text('SELECT initiated_by FROM declaration_import WHERE id=:job'), {'job': uuid.UUID(job)})
        assert creator == ids['actor']
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(text('UPDATE declaration_queue_request SET actor_user_id=:actor WHERE id=:request'), {'actor': ids['clerk'], 'request': uuid.UUID(next_request)})
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(text('UPDATE declaration_import SET initiated_by=:actor WHERE id=:job'), {'actor': ids['clerk'], 'job': uuid.UUID(job)})


import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def close_synthetic_queue(scope_seed):
    yield
    # The global dispatcher must not pick up another test's intentionally queued work.
    await mutate(text("UPDATE declaration_import SET status='CANCELLED',lease_token=NULL,lease_owner=NULL,lease_expires_at=NULL WHERE organization_id=:org AND status IN ('QUEUED','RETRY_WAIT','PREFLIGHT','EXTRACTING','RECONCILING')"), {'org':scope_seed['org_a_id']})


async def test_unknown_legacy_queue_is_quarantined_without_inferred_creator(scope_seed, migrator_session):
    await migrator_session.commit()
    ids = {'org':scope_seed['org_a_id'], 'actor':scope_seed['user_a_admin_id']}
    job = await new_draft(ids)
    await mutate(text("UPDATE declaration_import SET status='QUEUED' WHERE id=:job"), {'job':uuid.UUID(job)})
    async with get_tenant_session(ids['org']) as session:
        repo = DeclarationImportRepository(session, ids['org'])
        assert await repo.claim_next('legacy-test', datetime.now(timezone.utc), 120) is None
    async with client(ids, 'actor') as api:
        cancelled = await api.get(f'/api/declaration-imports/{job}')
        assert cancelled.json()['error_code'] == 'JOB_PRINCIPAL_UNVERIFIED'
        assert cancelled.json()['queue_request_id'] is None
        retry = await api.post(f'/api/declaration-imports/{job}/retry', headers={'Idempotency-Key':str(uuid.uuid4())})
        assert retry.status_code == 200 and retry.json()['queue_request_id']


async def test_concurrent_commands_have_one_receipt_and_reject_other_actor(scope_seed, migrator_session):
    import asyncio
    await migrator_session.commit()
    ids = {'org':scope_seed['org_a_id'], 'actor':scope_seed['user_a_admin_id'], 'clerk':scope_seed['user_a_clerk_id']}
    job = await new_draft(ids)
    key = str(uuid.uuid4())
    path = f'/api/declaration-imports/{job}/start'
    async with client(ids) as api:
        missing = await api.post(path)
        assert missing.status_code == 422
        first, second = await asyncio.gather(api.post(path, headers={'Idempotency-Key':key}),api.post(path, headers={'Idempotency-Key':key}))
        assert first.status_code == second.status_code == 200
        assert first.json()['queue_request_id'] == second.json()['queue_request_id']
    async with client(ids, 'actor') as api:
        assert (await api.post(path,headers={'Idempotency-Key':key})).status_code == 409
    async with get_tenant_session(ids['org']) as session:
        assert await session.scalar(text('SELECT count(*) FROM declaration_queue_request WHERE import_id=:job'),{'job':uuid.UUID(job)}) == 1


async def test_tenant_fences_and_migration_refuse_loss_of_queue_evidence(scope_seed, migrator_session, seed_data):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    await migrator_session.commit()
    ids = {'org':scope_seed['org_a_id'], 'actor':scope_seed['user_a_admin_id']}
    job = await new_draft(ids)
    async with client(ids, 'actor') as api:
        queued = await api.post(f'/api/declaration-imports/{job}/start',headers={'Idempotency-Key':str(uuid.uuid4())})
        request = uuid.UUID(queued.json()['queue_request_id'])
    async with get_tenant_session(seed_data['org_b_id']) as session:
        assert await session.scalar(text('SELECT count(*) FROM declaration_queue_request WHERE id=:request'),{'request':request}) == 0
        assert await session.scalar(text('SELECT declaration_queue_actor(:org,:job,:request)'),{'org':ids['org'],'job':uuid.UUID(job),'request':request}) is None
        assert not await session.scalar(text('SELECT cancel_unauthorized_declaration(:org,:job,:request,1,NULL)'),{'org':ids['org'],'job':uuid.UUID(job),'request':request})
    migration = importlib.import_module('migrations.versions.0043_declaration_queue_principals')
    def verify(connection):
        with pytest.raises(DBAPIError, match='Retain populated'):
            with connection.begin_nested(), Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
        transaction = connection.begin_nested()
        try:
            connection.execute(text('TRUNCATE declaration_queue_request CASCADE'))
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                migration.upgrade()
            assert connection.scalar(text("SELECT to_regclass('public.declaration_queue_request')"))
        finally:
            transaction.rollback()
    from sqlalchemy import create_engine
    from tests.conftest import MIGRATOR_SYNC_URL
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as connection:
            verify(connection)
    finally:
        engine.dispose()


async def test_worker_reports_permission_denial_instead_of_processed(scope_seed, migrator_session):
    from app.workers.declaration_import import DeclarationImportWorker
    await migrator_session.commit()
    lease, key, store = await _seed_pipeline_import(scope_seed, make_text_pdf('Synthetic denied source'))
    await revoke({'org':lease.organization_id, 'actor':scope_seed['user_a_admin_id']})
    # _run_claimed observes pipeline-origin denial; no provider is called.
    result = await DeclarationImportWorker(DeclarationPipeline(_RecordingProvider(key),store=store))._run_claimed(lease)
    assert result.code == 'JOB_PERMISSION_CHANGED'
    item, _, _, audits = await inspect_job(lease)
    assert item['status'] == 'CANCELLED' and audits == 1


async def test_worker_reports_heartbeat_permission_cancellation(scope_seed, migrator_session):
    import asyncio
    from app.workers.declaration_import import DeclarationImportWorker
    await migrator_session.commit()
    lease, _, _ = await _seed_pipeline_import(scope_seed, make_text_pdf('Heartbeat source'))
    started, cancelled = asyncio.Event(), asyncio.Event()
    class WaitingPipeline:
        async def run(self, _lease):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
    async def revoke_before_heartbeat(_delay):
        await started.wait()
        await revoke({'org':lease.organization_id, 'actor':scope_seed['user_a_admin_id']})
    result = await DeclarationImportWorker(WaitingPipeline(), sleep=revoke_before_heartbeat)._run_claimed(lease)
    assert result.code == 'JOB_PERMISSION_CHANGED' and cancelled.is_set()
    item, _, _, audits = await inspect_job(lease)
    assert item['status'] == 'CANCELLED' and audits == 1
