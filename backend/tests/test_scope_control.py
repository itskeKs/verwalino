"""Rollback fences classified effects without widening or freezing legacy access."""
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tests.test_financial_scope_boundary import worker_tenant, scope_seed, financial_fixture  # noqa: F401
from tests.test_scoped_account_statements import client, mutate
from tests.test_scope_payment_reconciliation import read_value

pytestmark = pytest.mark.asyncio
PATH = '/api/accounting-scopes/control'


async def change(ids, mode='paused', previous=None, key=None, reason='Reviewed scoped rollback'):
    async with client(ids, 'actor') as api:
        return await api.post(PATH, json={'mode': mode, 'expected_event_id': previous, 'reason': reason},
            headers={'Idempotency-Key': key or str(uuid.uuid4())})


async def test_pause_resume_preserves_grants_and_replay_is_not_current_state(migrator_session, scope_seed):
    ids = await financial_fixture(migrator_session, scope_seed)
    await migrator_session.commit()
    async with client(ids, 'actor') as api:
        initial = await api.get(PATH)
        assert initial.status_code == 200, initial.text
        assert initial.json() == {'mode': 'compatibility', 'event': None}
    key = str(uuid.uuid4())
    paused = await change(ids, key=key)
    assert paused.status_code == 201, paused.text
    event = paused.json()
    assert (await change(ids, key=key)).json() == event
    assert (await change(ids, key=key, reason='Changed command')).status_code == 409
    assert (await change(ids, mode='compatibility')).status_code == 409
    assert not await read_value("SELECT has_scope_capability(:org,:scope,'prepare')", ids, actor_key='clerk')
    assert await read_value("SELECT has_scope_capability(:org,:scope,'read')", ids, actor_key='clerk')
    with pytest.raises(DBAPIError, match='scope capability'):
        await read_value("UPDATE cost_entry SET document_number='Paused bypass' WHERE id=:cost RETURNING id", ids, actor_key='clerk')
    assert await read_value('SELECT count(*) FROM cost_entry WHERE id=:cost', ids, actor_key='clerk') == 1
    assert await read_value('SELECT count(*) FROM cost_entry WHERE id=:cost', ids, actor_key='actor') == 0
    resumed = await change(ids, mode='compatibility', previous=event['id'])
    assert resumed.status_code == 201, resumed.text
    assert (await change(ids, key=key)).json() == event
    async with client(ids, 'actor') as api:
        assert (await api.get(PATH)).json()['event']['id'] == resumed.json()['id']
    assert await read_value("SELECT has_scope_capability(:org,:scope,'prepare')", ids, actor_key='clerk')
    await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE id=:grant'), ids)
    assert not await read_value("SELECT has_scope_capability(:org,:scope,'read')", ids, actor_key='clerk')


async def test_live_admin_tenant_isolation_and_immutable_history(migrator_session, scope_seed, client_b_admin):
    ids = await financial_fixture(migrator_session, scope_seed)
    await migrator_session.commit()
    event = (await change(ids)).json()
    ids['event'] = uuid.UUID(event['id'])
    async with client(ids) as clerk:
        assert (await clerk.get(PATH)).status_code == 403
        assert (await clerk.post(PATH, json={'mode': 'compatibility', 'expected_event_id': event['id'], 'reason': 'Bypass'}, headers={'Idempotency-Key': str(uuid.uuid4())})).status_code == 403
    assert (await client_b_admin.get(PATH)).json() == {'mode': 'compatibility', 'event': None}
    for actor_key, count in [('actor', 1), ('clerk', 0)]:
        assert await read_value('SELECT count(*) FROM scope_control_event WHERE id=:event', ids, actor_key=actor_key) == count
        assert await read_value("SELECT count(*) FROM audit_event WHERE entity_type='scope_control_event' AND entity_id=:event", ids, actor_key=actor_key) == count
    with pytest.raises(DBAPIError):
        await read_value("INSERT INTO scope_control_event(organization_id,actor_user_id,request_key,mode,reason) VALUES (:org,:actor,:key,'paused','Forged') RETURNING id", {**ids, 'key': uuid.uuid4()}, actor_key='actor')
    with pytest.raises(DBAPIError):
        await read_value("UPDATE scope_control_event SET mode='compatibility' WHERE id=:event RETURNING id", ids)
    with pytest.raises(DBAPIError):
        await read_value('DELETE FROM scope_control_event WHERE id=:event RETURNING id', ids)
    with pytest.raises(DBAPIError, match='not accessible'):
        await read_value("SELECT change_scoped_effect_control(:org,:key,'compatibility',:event,'Bypass')", {**ids, 'key': uuid.uuid4()}, actor_key='clerk')
    # API uses live membership rather than the still valid session's role claim.
    await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:actor"), ids)
    assert (await change(ids, mode='compatibility', previous=event['id'])).status_code == 403


async def test_pause_preserves_export_grant_and_resume_does_not_restore_revoked_rights(migrator_session, scope_seed):
    ids = await financial_fixture(migrator_session, scope_seed)
    await migrator_session.execute(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE id=:grant'), ids)
    await migrator_session.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:scope,:clerk,ARRAY['read','prepare','confirm','approve','send','export'],:actor,'All exact capabilities')"), ids)
    await migrator_session.commit()
    original = await read_value('SELECT to_jsonb(c) FROM cost_entry c WHERE id=:cost', ids)
    event = (await change(ids)).json()
    for capability in ('read', 'prepare', 'confirm', 'approve', 'send', 'export'):
        assert await read_value('SELECT has_scope_capability(:org,:scope,:cap)', {**ids, 'cap': capability}, actor_key='clerk') == (capability in ('read','export'))
    assert not await read_value("SELECT has_scope_capability(:org,:scope,'export')", ids, actor_key='actor')
    await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND user_id=:clerk AND revoked_at IS NULL'), ids)
    assert (await change(ids, mode='compatibility', previous=event['id'])).status_code == 201
    assert not await read_value("SELECT has_scope_capability(:org,:scope,'prepare')", ids, actor_key='clerk')
    assert await read_value('SELECT to_jsonb(c) FROM cost_entry c WHERE id=:cost', ids) == original


@pytest.mark.parametrize('phase', ['before_claim', 'before_finish'])
@pytest.mark.parametrize('bound', [True, False])
async def test_pause_fences_scoped_invoice_jobs_but_retains_unclassified_compatibility(migrator_session, worker_tenant, phase, bound):
    from app.core.db import get_session_factory, set_tenant_context
    from app.services.invoice_processing_service import InvoiceProcessingService
    _, seed = worker_tenant
    ids = await financial_fixture(migrator_session, {**seed, 'user_a_clerk_id': seed['user_a_admin_id']})
    if not bound:
        old = ids['document']
        ids.update(document=uuid.uuid4(), legacy_cost=uuid.uuid4())
        await migrator_session.execute(text("""INSERT INTO cost_entry(id,organization_id,property_id,cost_category_id,
          cost_category_plan_version,invoice_date,service_period_from,service_period_to,gross_amount,tenant_apportionable,status,source)
          SELECT :legacy_cost,:org,:other_property,cost_category_id,cost_category_plan_version,invoice_date,service_period_from,service_period_to,
            gross_amount,tenant_apportionable,status,source FROM cost_entry WHERE id=:cost"""), ids)
        await migrator_session.execute(text("""INSERT INTO invoice_document(id,organization_id,cost_entry_id,storage_uri,storage_backend,mime_type,
          content_byte_size,content_sha256,version,chain_root_id,status)
          SELECT :document,:org,:legacy_cost,storage_uri,storage_backend,mime_type,content_byte_size,content_sha256,1,:document,'active'
          FROM invoice_document WHERE id=:old"""), {**ids, 'old': old})
    ids['attempt'] = uuid.uuid4()
    await migrator_session.execute(text("INSERT INTO invoice_processing_attempt(id,organization_id,document_id,attempt_no,status,initiated_by,requested_scope_id) VALUES(:attempt,:org,:document,1,'queued',:actor,:requested)"), {**ids, 'requested': ids['scope'] if bound else None})
    await migrator_session.execute(text("UPDATE invoice_document SET processing_status='queued' WHERE id=:document"), ids)
    await migrator_session.commit()
    claim = None
    if phase == 'before_finish':
        async with get_session_factory()() as session, session.begin():
            await set_tenant_context(session, ids['org'])
            claim = await InvoiceProcessingService(session, ids['org']).claim()
            assert claim is not None
    assert (await change(ids)).status_code == 201
    async with get_session_factory()() as session, session.begin():
        await set_tenant_context(session, ids['org'])
        service = InvoiceProcessingService(session, ids['org'])
        if phase == 'before_claim':
            claim = await service.claim()
            assert (claim is None) == bound
        else:
            assert await service.finish(claim, {'status': 'succeeded', 'payload': {'gross_amount': '999.00'}}) == (not bound)
    status = await read_value('SELECT status FROM invoice_processing_attempt WHERE id=:attempt', ids)
    assert status == ('canceled' if bound else 'running' if phase == 'before_claim' else 'succeeded')
    if bound:
        assert await read_value('SELECT extraction_payload FROM invoice_document WHERE id=:document', ids) is None
    else:
        assert await read_value("UPDATE cost_entry SET document_number='Legacy retained' WHERE id=:legacy_cost RETURNING id", ids, actor_key='actor') == ids['legacy_cost']


async def test_concurrent_commands_have_one_successor_and_parallel_retry_one_receipt(migrator_session, scope_seed):
    import asyncio
    ids = await financial_fixture(migrator_session, scope_seed)
    await migrator_session.commit()
    key = str(uuid.uuid4())
    first, replay = await asyncio.gather(change(ids, key=key), change(ids, key=key))
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    previous = first.json()['id']
    left, right = await asyncio.gather(change(ids, mode='compatibility', previous=previous), change(ids, mode='compatibility', previous=previous))
    assert sorted([left.status_code, right.status_code]) == [201,409]
    assert await read_value('SELECT count(*) FROM scope_control_event WHERE organization_id=:org', ids) == 2


async def test_pause_waits_for_inflight_effect_and_preserves_capability_function_oid(migrator_session, scope_seed):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import APP_RW_URL
    ids = await financial_fixture(migrator_session, scope_seed)
    await migrator_session.commit()
    engine = create_async_engine(APP_RW_URL)
    task = None
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true)"), {'org': str(ids['org']), 'user': str(ids['clerk'])})
            assert await connection.scalar(text("SELECT has_scope_capability(:org,:scope,'prepare')"), ids)
            task = asyncio.create_task(change(ids))
            # Observe the actual lock wait, rather than assuming a scheduling delay.
            async with asyncio.timeout(5):
                while not await read_value("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted)", ids):
                    await asyncio.sleep(.02)
            assert not task.done()
            await connection.execute(text("UPDATE cost_entry SET document_number='Before pause' WHERE id=:cost"), ids)
        assert (await task).status_code == 201
        assert not await read_value("SELECT has_scope_capability(:org,:scope,'prepare')", ids, actor_key='clerk')
        # Existing dependency OIDs must point to the gated function, not a renamed baseline.
        assert await read_value("SELECT count(*) FROM pg_depend WHERE refobjid='public.has_scope_capability(uuid,uuid,text)'::regprocedure AND classid='pg_policy'::regclass", ids) > 0
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


async def test_migration_preserves_policy_oid_and_refuses_to_erase_recorded_control(migrator_session, scope_seed):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = await financial_fixture(migrator_session, scope_seed)
    await migrator_session.commit()
    assert (await change(ids)).status_code == 201
    spec = importlib.util.spec_from_file_location('scope_control_migration', Path(__file__).parents[1] / 'migrations/versions/0041_scoped_effect_control.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            oid = connection.scalar(text("SELECT 'public.has_scope_capability(uuid,uuid,text)'::regprocedure::oid"))
            with pytest.raises(DBAPIError, match='Preserve scoped rollback history'):
                with connection.begin_nested(), Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
            # Rehearse empty downgrade/upgrade transactionally on disposable data;
            # rollback restores the synthetic evidence and keeps Alembic consistent.
            connection.execute(text('TRUNCATE scope_control_event'))
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                assert 'scoped_effects_enabled' not in connection.scalar(text("SELECT pg_get_functiondef('public.has_scope_capability(uuid,uuid,text)'::regprocedure)"))
                migration.upgrade()
            assert connection.scalar(text("SELECT 'public.has_scope_capability(uuid,uuid,text)'::regprocedure::oid")) == oid
            transaction.rollback()
        assert await read_value('SELECT count(*) FROM scope_control_event WHERE organization_id=:org', ids) == 1
    finally:
        engine.dispose()
