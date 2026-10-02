"""Source authority is independent of financial authority and input visibility."""
import uuid
import pytest
import pytest_asyncio
from sqlalchemy import text
from app.core.db import get_tenant_session
from tests.test_property_source_access import grant
from tests.test_property_source_enforcement import synthetic_enforcement, change_mode
from tests.test_financial_input_policy import draft, attest
from tests.test_m3_settlement import m3_seed, _extras_for_exit  # noqa: F401
from tests.test_m4_weg import m4_seed  # noqa: F401
from tests.test_scoped_account_statements import mutate

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def clear_synthetic_source_grants():
    yield
    # The financial seed properties are shared by the module. Only this guarded
    # disposable harness can discard immutable test configuration between cases.
    await mutate(text('TRUNCATE property_data_domain CASCADE'), {})


async def test_rental_calculations_require_current_source_authority(client_a_admin, seed_data, m3_seed):
    run = await draft(client_a_admin, m3_seed, '0.00')
    values = {'advance_payments_total':'0.00','advance_payments_source':'MANUAL'}
    assert (await attest(client_a_admin, 'settlement', run, values)).status_code == 201
    async with synthetic_enforcement(seed_data):
        for action in ['compute','preview','finalize']:
            response = await client_a_admin.post(f'/api/settlements/{run}/{action}', json=_extras_for_exit(m3_seed))
            assert response.status_code == 404, response.text
        assert (await attest(client_a_admin, 'settlement', run, values)).status_code == 404
        domain, receipt = await grant(client_a_admin, seed_data, m3_seed['property'], caps=['read'])
        response = await client_a_admin.post(f'/api/settlements/{run}/compute', json=_extras_for_exit(m3_seed))
        assert response.status_code == 200, response.text
        await change_mode(seed_data, 'PAUSED')
        assert (await client_a_admin.post(f'/api/settlements/{run}/finalize', json=_extras_for_exit(m3_seed))).status_code == 404
        await change_mode(seed_data, 'ENFORCED')
        assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code == 204
        assert (await client_a_admin.post(f'/api/settlements/{run}/finalize', json=_extras_for_exit(m3_seed))).status_code == 404


async def test_hidden_unproven_source_cannot_produce_plan_or_coverage(client_a_admin, seed_data, m4_seed):
    created = await client_a_admin.post('/api/economic-plans', json={'plan_year':2025,'property_id':m4_seed['property'],
        'period_start':'2025-01-01','period_end':'2025-12-31'})
    assert created.status_code == 201, created.text
    plan = created.json()['id']
    values = {'planned_costs':[],'reserve_contribution_total':'0.00'}
    assert (await attest(client_a_admin, 'economic_plan', plan, values)).status_code == 201
    await grant(client_a_admin, seed_data, m4_seed['property'], caps=['read'])
    document = uuid.uuid4()
    await mutate(text("INSERT INTO property_document(id,organization_id,property_id,doc_type,title,original_filename,mime_type,content_byte_size,content_sha256,storage_uri,storage_backend,page_count) "
        "VALUES(:document,:org,:property,'ANNEX','Unknown original','unknown.pdf','application/pdf',1,:hash,'synthetic/unknown','TEST',1)"),
        {'document':document,'org':seed_data['org_a_id'],'property':uuid.UUID(m4_seed['property']),'hash':'b'*64})
    await mutate(text("CREATE POLICY test_financial_hidden_original ON property_document AS RESTRICTIVE FOR SELECT USING(property_source_permitted(organization_id,'property_document',id,'read'))"), {})
    try:
        async with synthetic_enforcement(seed_data):
            async with get_tenant_session(seed_data['org_a_id']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(seed_data['user_a_admin_id'])})
                assert await db.scalar(text('SELECT count(*) FROM property_document WHERE id=:document'), {'document':document}) == 0
            for action in ['compute','finalize']:
                response = await client_a_admin.post(f'/api/economic-plans/{plan}/{action}', json=values)
                assert response.status_code == 404, response.text
            assert (await attest(client_a_admin, 'economic_plan', plan, values)).status_code == 404
            async with get_tenant_session(seed_data['org_a_id']) as db:
                assert await db.scalar(text('SELECT snapshot_payload FROM economic_plan WHERE id=:plan'), {'plan':uuid.UUID(plan)}) is None
                assert await db.scalar(text('SELECT count(*) FROM coverage_declaration WHERE economic_plan_id=:plan'), {'plan':uuid.UUID(plan)}) == 1
    finally:
        await mutate(text('DROP POLICY test_financial_hidden_original ON property_document'), {})
        await mutate(text('DELETE FROM property_document WHERE id=:document'), {'document':document})


@pytest.mark.parametrize('family', ['plan','weg','assets'])
async def test_all_weg_compute_and_finalize_paths_require_complete_sources(client_a_admin, seed_data, m4_seed, family):
    from tests.test_m4_weg import _create_and_finalize_plan
    from tests.financial_coverage_helpers import weg_values
    values = {'planned_costs':[],'reserve_contribution_total':'0.00'}
    endpoint = '/api/economic-plans'
    payload = {'property_id':m4_seed['property'],'period_start':'2025-01-01','period_end':'2025-12-31'}
    kind = 'economic_plan'
    if family == 'plan': payload['plan_year'] = 2025
    elif family == 'weg':
        endpoint = '/api/weg-settlements'
        payload['economic_plan_id'] = await _create_and_finalize_plan(client_a_admin, m4_seed)
        values = weg_values(m4_seed)
        kind = 'settlement'
    else:
        endpoint = '/api/weg-settlements/asset-reports'
        values = {'reserve_opening_balance':'0.00','reserve_withdrawals_total':'0.00','reserve_contributions_total':'0.00',
            'receivables':[],'payables':[], 'bank_balances':[
                {'bank_account_id':m4_seed['bank_giro'],'label':'Giro','account_type':'LAUFEND','balance':'0.00'},
                {'bank_account_id':m4_seed['bank_ruecklage'],'label':'Reserve','account_type':'RUECKLAGE','balance':'0.00'}]}
        kind = 'settlement'
    created = await client_a_admin.post(endpoint, json=payload)
    assert created.status_code == 201, created.text
    subject = created.json()['id']
    assert (await attest(client_a_admin, kind, subject, values)).status_code == 201
    async with synthetic_enforcement(seed_data):
        for action in ['compute','finalize']:
            response = await client_a_admin.post(f'{endpoint}/{subject}/{action}', json=values)
            assert response.status_code == 404, response.text
        await grant(client_a_admin, seed_data, m4_seed['property'], caps=['read'])
        response = await client_a_admin.post(f'{endpoint}/{subject}/finalize', json=values)
        assert response.status_code == 200, response.text


async def test_direct_sql_cannot_write_calculation_outputs_without_source_rights(client_a_admin, seed_data, m3_seed, m4_seed):
    from sqlalchemy.exc import DBAPIError
    rental = await draft(client_a_admin, m3_seed, '0.00')
    values = {'advance_payments_total':'0.00','advance_payments_source':'MANUAL'}
    assert (await attest(client_a_admin, 'settlement', rental, values)).status_code == 201
    assert (await client_a_admin.post(f'/api/settlements/{rental}/compute', json=_extras_for_exit(m3_seed))).status_code == 200
    result = await client_a_admin.post('/api/economic-plans', json={'property_id':m4_seed['property'],'plan_year':2025,
        'period_start':'2025-01-01','period_end':'2025-12-31'})
    plan = result.json()['id']
    budget = {'planned_costs':[],'reserve_contribution_total':'0.00'}
    assert (await attest(client_a_admin, 'economic_plan', plan, budget)).status_code == 201
    assert (await client_a_admin.post(f'/api/economic-plans/{plan}/compute', json=budget)).status_code == 200
    other = await client_a_admin.post('/api/properties', json={'name':'Different source audience','management_type':'WEG'})
    other_id = uuid.UUID(other.json()['id'])
    await grant(client_a_admin, seed_data, other_id, caps=['read'])
    async with synthetic_enforcement(seed_data), get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(seed_data['user_a_admin_id'])})
        for table, key, value in [('settlement_run','id',rental),('economic_plan','id',plan),
                                  ('settlement_line','settlement_run_id',rental),('economic_plan_line','economic_plan_id',plan)]:
            assert await db.scalar(text(f'SELECT count(*) FROM {table} WHERE {key}=:id'), {'id':uuid.UUID(value)}) > 0
            field = "snapshot_payload='{}'::jsonb" if table.endswith('_run') or table=='economic_plan' else "label='Forged calculation'"
            with pytest.raises(DBAPIError, match='Complete financial source authority required'):
                async with db.begin_nested():
                    await db.execute(text(f'UPDATE {table} SET {field} WHERE {key}=:id'), {'id':uuid.UUID(value)})
            if table.endswith('_line'):
                with pytest.raises(DBAPIError, match='Complete financial source authority required'):
                    async with db.begin_nested():
                        await db.execute(text(f'DELETE FROM {table} WHERE {key}=:id'), {'id':uuid.UUID(value)})
                with pytest.raises(DBAPIError, match='Complete financial source authority required'):
                    async with db.begin_nested():
                        await db.execute(text(f"INSERT INTO {table} SELECT (jsonb_populate_record(NULL::{table},to_jsonb(r)||jsonb_build_object('id',gen_random_uuid()))).* FROM {table} r WHERE {key}=:id LIMIT 1"), {'id':uuid.UUID(value)})
        with pytest.raises(DBAPIError, match='Complete financial source authority required'):
            async with db.begin_nested():
                await db.execute(text("INSERT INTO coverage_declaration SELECT (jsonb_populate_record(NULL::coverage_declaration,to_jsonb(c)||jsonb_build_object('id',gen_random_uuid(),'revision',revision+1))).* FROM coverage_declaration c WHERE economic_plan_id=:id LIMIT 1"), {'id':uuid.UUID(plan)})
        for parent, foreign_key, value in [('settlement_run','settlement_run_id',rental),('economic_plan','economic_plan_id',plan)]:
            with pytest.raises(DBAPIError, match='Coverage property differs from financial subject'):
                async with db.begin_nested():
                    await db.execute(text(f"INSERT INTO coverage_declaration SELECT (jsonb_populate_record(NULL::coverage_declaration,to_jsonb(c)||jsonb_build_object('id',gen_random_uuid(),'revision',revision+1,'property_id',CAST(:other AS uuid)))).* FROM coverage_declaration c WHERE {foreign_key}=:id LIMIT 1"), {'id':uuid.UUID(value),'other':other_id})
            with pytest.raises(DBAPIError, match='Complete financial source authority required'):
                async with db.begin_nested():
                    await db.execute(text(f'UPDATE {parent} SET property_id=:other WHERE id=:id'), {'id':uuid.UUID(value),'other':other_id})


async def test_financial_source_migration_refuses_strict_rollback_and_roundtrips(seed_data):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration = importlib.import_module('migrations.versions.0059_financial_source_input')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        async with synthetic_enforcement(seed_data):
            with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError, match='Retain strict financial source checks'):
                    with db.begin_nested(): migration.downgrade()
                savepoint = db.begin_nested()
                try:
                    db.execute(text('TRUNCATE property_source_security'))
                    migration.downgrade()
                    assert db.scalar(text("SELECT to_regprocedure('property_financial_source_access(uuid,uuid)')")) is None
                    migration.upgrade()
                    assert 'row_security=off' in db.scalar(text("SELECT proconfig FROM pg_proc WHERE oid='property_financial_source_access(uuid,uuid)'::regprocedure"))
                    assert not db.scalar(text("SELECT has_function_privilege('app_rw','guard_financial_source_input()','EXECUTE')"))
                finally: savepoint.rollback()
    finally: engine.dispose()


@pytest.mark.parametrize('table', ['property','unit','property_source_security','settlement_run','settlement_line','economic_plan','economic_plan_line','coverage_declaration'])
async def test_financial_source_migration_requires_quiescent_dependencies(seed_data, table):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration = importlib.import_module('migrations.versions.0059_financial_source_input')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as reader:
            reader.execute(text(f'SELECT 1 FROM {table} LIMIT 1'))
            with pytest.raises(DBAPIError) as caught:
                with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
                    db.execute(text("SET LOCAL lock_timeout='1s'"))
                    migration.upgrade()
            assert caught.value.orig.pgcode == '55P03'
    finally: engine.dispose()


async def test_financial_output_writer_waits_for_acl_before_parent_row(client_a_admin, seed_data, m4_seed):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_URL
    created = await client_a_admin.post('/api/economic-plans', json={'plan_year':2025,'property_id':m4_seed['property'],
        'period_start':'2025-01-01','period_end':'2025-12-31'})
    plan = uuid.UUID(created.json()['id'])
    marker = 'financial-source-wait-'+uuid.uuid4().hex
    async def write():
        with pytest.raises(DBAPIError, match='Complete financial source authority required'):
            async with get_tenant_session(seed_data['org_a_id']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(seed_data['user_a_admin_id'])})
                await db.execute(text("UPDATE economic_plan SET snapshot_payload='{}'::jsonb WHERE id=:plan /* "+marker+' */'), {'plan':plan})
    engine = create_async_engine(MIGRATOR_URL)
    task = None
    try:
        async with synthetic_enforcement(seed_data):
            async with get_tenant_session(seed_data['org_a_id']) as locker:
                await locker.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {'key':'scope-acl:'+str(seed_data['org_a_id'])})
                task = asyncio.create_task(write())
                async with engine.connect() as observer:
                    blocked = False
                    for _ in range(300):
                        await observer.execute(text('SELECT pg_stat_clear_snapshot()'))
                        blocked = await observer.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE :query)"), {'query':'%'+marker+'%'})
                        if blocked: break
                        await asyncio.sleep(.01)
                    assert blocked and not task.done()
                await locker.execute(text('SELECT id FROM economic_plan WHERE id=:plan FOR UPDATE NOWAIT'), {'plan':plan})
            await asyncio.wait_for(task, 5)
    finally:
        await engine.dispose()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
