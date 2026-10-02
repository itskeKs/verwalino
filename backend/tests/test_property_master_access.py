"""Canonical property/unit authority and atomic new-property audiences."""
import uuid
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.db import get_tenant_session
from tests.test_financial_scope_boundary import scope_seed, worker_tenant  # noqa: F401
from tests.test_scoped_account_statements import client
from tests.test_declaration_import_api import isolated_document_store, enable_declaration_imports  # noqa: F401
from tests.test_property_source_access import grant
from tests.test_property_source_enforcement import synthetic_enforcement, change_mode

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def committed_actor(scope_seed, migrator_session):
    await migrator_session.commit()


def principals(seed):
    return {'org':seed['org_a_id'],'actor':seed['user_a_admin_id'],'clerk':seed['user_a_clerk_id']}


def audience(seed):
    return {'label':'Reviewed building data','reason':'Explicit creation audience',
            'grants':[{'user_id':str(seed['user_a_admin_id']), 'capabilities':['read','prepare','approve','export'],
                       'reason':'Responsible publisher'}]}


async def existing(api):
    prop = await api.post('/api/properties', json={'name':'Private property','management_type':'WEG'})
    assert prop.status_code == 201, prop.text
    unit = await api.post('/api/units',json={'property_id':prop.json()['id'],'number':'Private unit','usage_type':'WOHNUNG'})
    assert unit.status_code == 201, unit.text
    return prop.json()['id'], unit.json()['id']


async def test_master_detail_lists_and_raw_rows_require_source_read(scope_seed):
    ids = principals(scope_seed)
    async with client(ids,'actor') as api:
        prop, unit = await existing(api)
        async with synthetic_enforcement(scope_seed):
            for kind, subject in [('properties',prop),('units',unit)]:
                assert (await api.get(f'/api/{kind}/{subject}')).status_code == 404
                assert (await api.get(f'/api/{kind}')).json() == []
                assert (await api.get(f'/api/{kind}/page')).json()['items'] == []
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(ids['actor'])})
                assert await db.scalar(text('SELECT count(*) FROM property')) == 0
                assert await db.scalar(text('SELECT count(*) FROM unit')) == 0
            # Administration can configure a known target ID without receiving
            # its source fields or inheriting read authority.
            await grant(api,scope_seed,prop,caps=['read'])
            assert (await api.get(f'/api/properties/{prop}')).status_code == 200


async def test_read_is_not_prepare_and_pause_keeps_master_reads(scope_seed):
    ids = principals(scope_seed)
    async with client(ids,'actor') as api:
        prop, unit = await existing(api)
        domain, receipt = await grant(api,scope_seed,prop,caps=['read'])
        async with synthetic_enforcement(scope_seed):
            assert (await api.get(f'/api/properties/{prop}')).status_code == 200
            assert (await api.get(f'/api/units/{unit}')).status_code == 200
            assert (await api.put(f'/api/properties/{prop}',json={'name':'Forbidden'})).status_code == 404
            assert (await api.put(f'/api/units/{unit}',json={'number':'Forbidden'})).status_code == 404
            assert (await api.post('/api/units',json={'property_id':prop,'number':'Forbidden','usage_type':'WOHNUNG'})).status_code == 404
            assert (await api.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code == 204
            grant_response=await api.post(f'/api/property-data-domains/{domain}/grants',headers={'Idempotency-Key':str(uuid.uuid4())},
                json={'user_id':str(ids['actor']),'capabilities':['read','prepare'],'reason':'Edit current master data'})
            assert grant_response.status_code == 201
            assert (await api.put(f'/api/properties/{prop}',json={'name':'Authorized'})).status_code == 200
            assert (await api.put(f'/api/units/{unit}',json={'number':'Authorized'})).status_code == 200
            created=await api.post('/api/units',json={'property_id':prop,'number':'Second','usage_type':'WOHNUNG'})
            assert created.status_code == 201, created.text
            await change_mode(scope_seed,'PAUSED')
            assert (await api.get(f'/api/units/{unit}')).status_code == 200
            assert (await api.put(f'/api/units/{unit}',json={'number':'Paused'})).status_code == 404


async def test_explicit_new_audience_is_atomic_and_replay_checks_current_access(scope_seed):
    ids=principals(scope_seed)
    async with client(ids,'actor') as api, synthetic_enforcement(scope_seed):
        body={'name':'New reviewed property','management_type':'WEG'}
        assert (await api.post('/api/properties',json=body)).status_code == 422
        body['data_audience']=audience(scope_seed)
        headers={'Idempotency-Key':str(uuid.uuid4())}
        response=await api.post('/api/properties',headers=headers,json=body)
        assert response.status_code == 201, response.text
        prop=response.json()['id']
        retry=await api.post('/api/properties',headers=headers,json=body)
        assert retry.status_code == 201 and retry.json()['id']==prop
        assert (await api.post('/api/properties',headers=headers,json={**body,'name':'Changed'})).status_code == 409
        domains=(await api.get('/api/property-data-domains/page',params={'property_id':prop})).json()['items']
        assert len(domains)==1
        grants=(await api.get(f"/api/property-data-domains/{domains[0]['id']}/grants/page")).json()['items']
        assert len(grants)==1
        assert (await api.post(f"/api/property-data-domains/{domains[0]['id']}/grants/{grants[0]['id']}/revoke")).status_code == 204
        assert (await api.post('/api/properties',headers=headers,json=body)).status_code == 404
        async with client(ids) as clerk:
            assert (await clerk.post('/api/properties',headers={'Idempotency-Key':str(uuid.uuid4())},json=body)).status_code == 403


async def test_raw_master_insert_and_reparent_cannot_choose_other_audience(scope_seed):
    ids=principals(scope_seed)
    async with client(ids,'actor') as api:
        prop, unit=await existing(api)
        other,_=await existing(api)
        await grant(api,scope_seed,prop,caps=['read','prepare'])
        async with synthetic_enforcement(scope_seed),get_tenant_session(ids['org']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['actor'])})
            for statement,values in [
                ("INSERT INTO property(organization_id,name,street,zip_code,city,management_type) VALUES (:org,'Unauthorized','','','','WEG')",ids),
                ('UPDATE unit SET property_id=:other WHERE id=:unit',{'other':uuid.UUID(other),'unit':uuid.UUID(unit)}),
                ("INSERT INTO unit(organization_id,property_id,number,usage_type) VALUES (:org,:other,'Unauthorized','WOHNUNG')",{**ids,'other':uuid.UUID(other)}),
            ]:
                with pytest.raises(DBAPIError):
                    async with db.begin_nested(): await db.execute(text(statement),values)


async def test_creation_failure_rolls_back_audience_and_parallel_retry_is_one_result(scope_seed, monkeypatch):
    import asyncio
    from app.repositories.property_repo import PropertyRepository
    ids=principals(scope_seed)
    body={'name':'Atomic creation','management_type':'WEG','data_audience':audience(scope_seed)}
    headers={'Idempotency-Key':str(uuid.uuid4())}
    async with client(ids,'actor') as api, synthetic_enforcement(scope_seed):
        async def fail(repo, entity):
            raise RuntimeError('Synthetic parent insert failure')
        with monkeypatch.context() as patch:
            patch.setattr(PropertyRepository,'create',fail)
            with pytest.raises(RuntimeError,match='Synthetic parent insert failure'):
                await api.post('/api/properties',headers=headers,json=body)
        async with get_tenant_session(ids['org']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(ids['actor'])})
            for table in ['property_data_domain','property_data_grant','property_creation_receipt']:
                assert await db.scalar(text(f'SELECT count(*) FROM {table}')) == 0
        responses=await asyncio.wait_for(asyncio.gather(*[
            api.post('/api/properties',headers=headers,json=body) for _ in range(2)]),5)
        assert [r.status_code for r in responses]==[201,201]
        assert responses[0].json()['id']==responses[1].json()['id']
        async with get_tenant_session(ids['org']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(ids['actor'])})
            for table in ['property','property_data_domain','property_data_grant','property_creation_receipt']:
                assert await db.scalar(text(f'SELECT count(*) FROM {table}')) == 1
            for sql in ["UPDATE property_creation_receipt SET request_digest=repeat('0',64)", 'DELETE FROM property_creation_receipt']:
                with pytest.raises(DBAPIError):
                    async with db.begin_nested(): await db.execute(text(sql))
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(ids['clerk'])})
            assert await db.scalar(text('SELECT count(*) FROM property_creation_receipt'))==0
            assert not await db.scalar(text('SELECT property_data_setup_target_exists(:org,:property)'),
                {'org':ids['org'],'property':uuid.UUID(responses[0].json()['id'])})


async def test_master_migration_retains_receipts_and_roundtrips(scope_seed):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0061_property_master_access')
    ids=principals(scope_seed)
    async with client(ids,'actor') as api:
        response=await api.post('/api/properties',headers={'Idempotency-Key':str(uuid.uuid4())},
            json={'name':'Retained creation','management_type':'WEG','data_audience':audience(scope_seed)})
        assert response.status_code==201,response.text
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        async with synthetic_enforcement(scope_seed):
            with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError,match='Retain strict property master'):
                    with db.begin_nested(): migration.downgrade()
                savepoint=db.begin_nested()
                try:
                    db.execute(text('TRUNCATE property_source_security'))
                    with pytest.raises(DBAPIError,match='Retain property creation receipts'):
                        with db.begin_nested(): migration.downgrade()
                    db.execute(text('TRUNCATE property_creation_receipt'))
                    migration.downgrade()
                    assert db.scalar(text("SELECT to_regprocedure('property_master_access(uuid,uuid,text)')")) is None
                    migration.upgrade()
                    assert db.scalar(text("SELECT count(*) FROM pg_policy WHERE polname LIKE 'property_master_%' AND NOT polpermissive"))==8
                    assert db.scalar(text("SELECT condeferred FROM pg_constraint WHERE conname=:name"),{'name':migration.DOMAIN_FK})
                    assert 'row_security=off' in db.scalar(text("SELECT proconfig FROM pg_proc WHERE oid='property_master_access(uuid,uuid,text)'::regprocedure"))
                finally: savepoint.rollback()
                assert db.scalar(text('SELECT count(*) FROM property_creation_receipt WHERE organization_id=:org'),ids)==1
    finally: engine.dispose()


@pytest.mark.parametrize('table',['property','unit','property_data_domain','property_data_grant','property_source_security'])
async def test_master_migration_refuses_busy_dependencies(scope_seed,table):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0061_property_master_access')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as reader:
            reader.execute(text(f'SELECT 1 FROM {table} LIMIT 1'))
            with pytest.raises(DBAPIError) as caught:
                with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                    db.execute(text("SET LOCAL lock_timeout='1s'"))
                    migration.upgrade()
            assert caught.value.orig.pgcode=='55P03'
    finally: engine.dispose()


@pytest.mark.parametrize('classification',[None,'QUARANTINED','ADMITTED'])
async def test_master_fields_cannot_disclose_unproven_or_quarantined_sources(scope_seed,classification):
    from tests.test_property_source_classification import legacy_bound
    from tests.test_bound_source_access import classify
    ids=principals(scope_seed)
    async with client(ids,'actor') as api:
        source=await legacy_bound(api,scope_seed)
        prop=str(source['property'])
        created=await api.post('/api/units',json={'property_id':prop,'number':'Copied fact','usage_type':'WOHNUNG'})
        assert created.status_code==201,created.text
        unit=created.json()['id']
        if classification: await classify(api,source,classification)
        async with synthetic_enforcement(scope_seed):
            expected=200 if classification=='ADMITTED' else 404
            for path in [f'/api/properties/{prop}',f'/api/units/{unit}']:
                assert (await api.get(path)).status_code==expected
            assert (await api.put(f'/api/properties/{prop}',json={'name':'Edit copied facts'})).status_code==expected
            assert (await api.put(f'/api/units/{unit}',json={'number':'Edit copied facts'})).status_code==expected
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['actor'])})
                for table in ['property','unit']:
                    assert await db.scalar(text(f'SELECT count(*) FROM {table}')) == (1 if classification=='ADMITTED' else 0)
