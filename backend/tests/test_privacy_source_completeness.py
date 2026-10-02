"""Hidden source-backed financial records must remain visible to retention gates."""
import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from app.core.db import get_tenant_session
from app.services.dsgvo_service import DsgvoService
from tests.test_m3_settlement import m3_seed, _extras_for_exit  # noqa: F401
from tests.test_m4_weg import m4_seed, _create_and_finalize_plan  # noqa: F401
from tests.test_financial_input_policy import draft,attest
from tests.test_financial_source_disclosure import bind_output,clear_disclosure_grants
from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
from tests.test_property_source_access import grant

pytestmark=pytest.mark.asyncio


async def retained_rental(api,seed,m3):
    run=await draft(api,m3,'0.00')
    assert (await attest(api,'settlement',run,{'advance_payments_total':'0.00','advance_payments_source':'MANUAL'})).status_code==201
    result=await api.post(f'/api/settlements/{run}/finalize',json=_extras_for_exit(m3))
    assert result.status_code==200,result.text
    await bind_output(seed,m3['property'],run,kind='settlement_run',scope_type='MIET')
    async with get_tenant_session(seed['org_a_id']) as db:
        person=await db.scalar(text('SELECT person_id FROM tenant_profile WHERE id=:tenant'),{'tenant':uuid.UUID(m3['tp_a'])})
    return person,run


@pytest.mark.parametrize('operation',['status','request_deletion','anonymize','export_person_data'])
async def test_hidden_retained_rental_cannot_be_ignored_by_privacy(client_a_admin,seed_data,m3_seed,operation):
    try:
        person,run=await retained_rental(client_a_admin,seed_data,m3_seed)
        async with synthetic_enforcement(seed_data),get_tenant_session(seed_data['org_a_id']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
            assert await db.scalar(text('SELECT count(*) FROM settlement_run WHERE id=:id'),{'id':uuid.UUID(run)})==0
            with pytest.raises(HTTPException) as denied:
                await getattr(DsgvoService(db,seed_data['org_a_id'],seed_data['user_a_admin_id']),operation)(person)
            assert denied.value.status_code==409
            assert await db.scalar(text('SELECT anonymized_at FROM person WHERE id=:id'),{'id':person}) is None
    finally: await clear_disclosure_grants()


async def test_retention_read_is_distinct_from_export_and_survives_pause(client_a_admin,seed_data,m3_seed):
    try:
        person,_=await retained_rental(client_a_admin,seed_data,m3_seed)
        domain,receipt=await grant(client_a_admin,seed_data,m3_seed['property'],caps=['read'])
        async with synthetic_enforcement(seed_data):
            status=await client_a_admin.get(f'/api/dsgvo/persons/{person}/status')
            assert status.status_code==200 and status.json()['is_bound'],status.text
            assert (await client_a_admin.get(f'/api/dsgvo/persons/{person}/export')).status_code==409
            assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code==204
            response=await client_a_admin.post(f'/api/property-data-domains/{domain}/grants',headers={'Idempotency-Key':str(uuid.uuid4())},
                json={'user_id':str(seed_data['user_a_admin_id']),'capabilities':['read','export'],'reason':'Review complete retention sources'})
            assert response.status_code==201,response.text
            await change_mode(seed_data,'PAUSED')
            exported=await client_a_admin.get(f'/api/dsgvo/persons/{person}/export')
            assert exported.status_code==200 and exported.json()['retention_bindings'],exported.text
    finally: await clear_disclosure_grants()


async def test_hidden_retained_plan_blocks_privacy_even_with_financial_export(client_a_admin,seed_data,m4_seed):
    try:
        plan=await _create_and_finalize_plan(client_a_admin,m4_seed)
        await bind_output(seed_data,m4_seed['property'],plan)
        async with synthetic_enforcement(seed_data):
            response=await client_a_admin.get(f"/api/dsgvo/persons/{m4_seed['person_a']}/status")
            assert response.status_code==409,response.text
            response=await client_a_admin.get(f"/api/dsgvo/persons/{m4_seed['person_a']}/export")
            assert response.status_code==409,response.text
    finally: await clear_disclosure_grants()


async def test_expiry_during_retention_discovery_cannot_turn_hidden_output_into_erasure(client_a_admin,seed_data,m3_seed,monkeypatch):
    from datetime import datetime,timedelta,timezone
    from app.repositories.dsgvo_repo import DsgvoRepository
    try:
        person,_=await retained_rental(client_a_admin,seed_data,m3_seed)
        domain,receipt=await grant(client_a_admin,seed_data,m3_seed['property'],caps=['read'])
        assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code==204
        granted=await client_a_admin.post(f'/api/property-data-domains/{domain}/grants',headers={'Idempotency-Key':str(uuid.uuid4())},
            json={'user_id':str(seed_data['user_a_admin_id']),'capabilities':['read'],'reason':'Short retention review',
                  'expires_at':(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat()})
        assert granted.status_code==201,granted.text
        original=DsgvoRepository.finalized_bk_runs_for_tenants
        async def expire_before_rows(repo,tenant_ids):
            await repo._session.execute(text('SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM expires_at-clock_timestamp()))+0.02) FROM property_data_grant WHERE id=:grant'),
                {'grant':uuid.UUID(granted.json()['id'])})
            return await original(repo,tenant_ids)
        monkeypatch.setattr(DsgvoRepository,'finalized_bk_runs_for_tenants',expire_before_rows)
        async with synthetic_enforcement(seed_data),get_tenant_session(seed_data['org_a_id']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
            with pytest.raises(HTTPException) as denied:
                await DsgvoService(db,seed_data['org_a_id'],seed_data['user_a_admin_id']).anonymize(person)
            assert denied.value.status_code==409
            assert await db.scalar(text('SELECT anonymized_at FROM person WHERE id=:person'),{'person':person}) is None
    finally: await clear_disclosure_grants()


@pytest.mark.parametrize('action',['status','request-deletion'])
async def test_privacy_rechecks_administrator_in_the_data_transaction(client_a_admin,seed_data,action):
    from app.main import app
    from app.core.tenant import AuthContext,get_current_user
    from tests.test_scoped_account_statements import mutate
    response=await client_a_admin.post('/api/persons',json={'first_name':'Privacy','last_name':'Principal'})
    assert response.status_code==201,response.text
    person=response.json()['id']
    ids={'org':seed_data['org_a_id'],'actor':seed_data['user_a_admin_id']}
    app.dependency_overrides[get_current_user]=lambda:AuthContext(ids['actor'],ids['org'],'OWNER_ADMIN')
    try:
        await mutate(text("UPDATE membership SET role='CLERK' WHERE organization_id=:org AND user_id=:actor"),ids)
        request=client_a_admin.get if action=='status' else client_a_admin.post
        assert (await request(f'/api/dsgvo/persons/{person}/{action}')).status_code==403
    finally:
        app.dependency_overrides.pop(get_current_user,None)
        await mutate(text("UPDATE membership SET role='OWNER_ADMIN' WHERE organization_id=:org AND user_id=:actor"),ids)


async def test_privacy_source_migration_is_private_and_retains_strict_state(seed_data):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0064_privacy_source_completeness')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        async with synthetic_enforcement(seed_data):
            with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError,match='Retain strict privacy'):
                    with db.begin_nested(): migration.downgrade()
                savepoint=db.begin_nested()
                try:
                    db.execute(text('TRUNCATE property_source_security'))
                    migration.downgrade()
                    migration.upgrade()
                    for name in ['privacy_source_context_complete','privacy_context_before_source']:
                        assert not db.scalar(text("SELECT has_function_privilege('app_rw',:signature,'EXECUTE')"),{'signature':name+'(uuid,uuid,text)'})
                    assert 'row_security=off' in db.scalar(text("SELECT proconfig FROM pg_proc WHERE oid='privacy_financial_context_complete(uuid,uuid,text)'::regprocedure"))
                finally: savepoint.rollback()
    finally: engine.dispose()
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        assert not await db.scalar(text("SELECT privacy_financial_context_complete(:org,:person,'read')"),{'org':seed_data['org_b_id'],'person':uuid.uuid4()})
        assert not await db.scalar(text("SELECT privacy_financial_context_complete(:org,:person,'unknown')"),{'org':seed_data['org_a_id'],'person':uuid.uuid4()})


@pytest.mark.parametrize('table',['property_source_security','person','tenant_profile','owner_profile',
    'settlement_run','settlement_line','economic_plan','economic_plan_line'])
async def test_privacy_source_migration_refuses_busy_dependencies(seed_data,table):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0064_privacy_source_completeness')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as reader:
            reader.execute(text(f'SELECT 1 FROM {table} LIMIT 1'))
            with pytest.raises(DBAPIError) as denied:
                with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                    db.execute(text("SET LOCAL lock_timeout='1s'"))
                    migration.upgrade()
            assert denied.value.orig.pgcode=='55P03'
    finally: engine.dispose()
