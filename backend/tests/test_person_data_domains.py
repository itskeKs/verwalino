"""Explicit contact grants prepare an audience without inheriting other rights."""
import uuid
import pytest
from sqlalchemy import text
from app.core.db import get_tenant_session
from tests.test_derived_source_access import actor_session

pytestmark=pytest.mark.asyncio


async def domain_fixture(api):
    person=await api.post('/api/persons',json={'first_name':'Synthetic','last_name':'Contact audience'})
    assert person.status_code==201,person.text
    body={'person_id':person.json()['id'],'reason_code':'INITIAL_SETUP'}
    key={'Idempotency-Key':str(uuid.uuid4())}
    response=await api.post('/api/person-data-domains',json=body,headers=key)
    assert response.status_code==201,response.text
    return body,key,response.json()


async def capability(seed,domain,actor,cap):
    async with get_tenant_session(seed['org_a_id']) as db:
        await actor_session(db,actor)
        return await db.scalar(text('SELECT has_person_data_capability(:org,:domain,:cap)'),
            {'org':seed['org_a_id'],'domain':uuid.UUID(domain),'cap':cap})


async def test_contact_domain_replay_never_implies_an_administrator_data_grant(client_a_admin,seed_data):
    body,key,domain=await domain_fixture(client_a_admin)
    assert domain['enforcement_status']=='PREPARATION'
    replay=await client_a_admin.post('/api/person-data-domains',json=body,headers=key)
    assert replay.status_code==201 and replay.json()['id']==domain['id']
    assert (await client_a_admin.post('/api/person-data-domains',json={**body,'reason_code':'REPLACEMENT'},headers=key)).status_code==409
    assert not await capability(seed_data,domain['id'],seed_data['user_a_admin_id'],'read')


async def test_contact_grant_revoke_and_replay_recheck_the_exact_audience(client_a_admin,client_a_clerk,client_b_admin,seed_data):
    _,_,domain=await domain_fixture(client_a_admin)
    path=f"/api/person-data-domains/{domain['id']}"
    assert (await client_a_clerk.get(path)).status_code==404
    command={'user_id':str(seed_data['user_a_clerk_id']),'capabilities':['read','prepare','export'],'reason_code':'TEAM_ACCESS'}
    key={'Idempotency-Key':str(uuid.uuid4())}
    result=await client_a_admin.post(path+'/grants',json=command,headers=key)
    assert result.status_code==201,result.text
    grant=result.json()['id']
    assert (await client_a_admin.post(path+'/grants',json=command,headers=key)).json()['id']==grant
    assert (await client_a_clerk.get(path)).status_code==200
    assert (await client_b_admin.get(path)).status_code==404
    assert (await client_a_clerk.get(path+'/grants/page')).status_code==403
    assert await capability(seed_data,domain['id'],seed_data['user_a_clerk_id'],'prepare')
    assert not await capability(seed_data,domain['id'],seed_data['user_a_admin_id'],'read')
    assert (await client_a_admin.post(path+f'/grants/{grant}/revoke')).status_code==204
    replay=await client_a_admin.post(path+'/grants',json=command,headers=key)
    assert replay.status_code==201 and replay.json()['revoked_at'] is not None
    assert not await capability(seed_data,domain['id'],seed_data['user_a_clerk_id'],'read')
    assert (await client_a_clerk.get(path)).status_code==404


async def test_property_grants_do_not_create_contact_rights(client_a_admin,seed_data):
    from tests.test_property_data_domains import domain_fixture as property_domain
    _,_,domain=await domain_fixture(client_a_admin)
    _,property_domain_id=await property_domain(client_a_admin)
    result=await client_a_admin.post(f'/api/property-data-domains/{property_domain_id}/grants',headers={'Idempotency-Key':str(uuid.uuid4())},
        json={'user_id':str(seed_data['user_a_clerk_id']),'capabilities':['read','prepare','export'],'reason':'Property rights only'})
    assert result.status_code==201,result.text
    assert not await capability(seed_data,domain['id'],seed_data['user_a_clerk_id'],'read')


@pytest.mark.parametrize('change',['missing_read','financial_capability','past_expiry','foreign_user'])
async def test_invalid_contact_grants_are_rejected(client_a_admin,seed_data,change):
    _,_,domain=await domain_fixture(client_a_admin)
    body={'user_id':str(seed_data['user_a_clerk_id']),'capabilities':['read'],'reason_code':'TEAM_ACCESS'}
    if change=='missing_read': body['capabilities']=['prepare']
    if change=='financial_capability': body['capabilities']=['read','approve']
    if change=='past_expiry': body['expires_at']='2000-01-01T00:00:00Z'
    if change=='foreign_user': body['user_id']=str(seed_data['user_b_admin_id'])
    response=await client_a_admin.post(f"/api/person-data-domains/{domain['id']}/grants",headers={'Idempotency-Key':str(uuid.uuid4())},json=body)
    assert response.status_code==(404 if change=='foreign_user' else 422),response.text


async def test_role_and_deactivation_recheck(client_a_admin,seed_data):
    from tests.test_scoped_account_statements import mutate
    _,_,result_domain = await domain_fixture(client_a_admin)
    domain = result_domain["id"]
    actor = seed_data['user_a_clerk_id']
    command = {'user_id':str(actor),'capabilities':['read','prepare','export'],'reason_code':'TEAM_ACCESS'}
    assert (await client_a_admin.post(f'/api/person-data-domains/{domain}/grants',json=command,headers={'Idempotency-Key':str(uuid.uuid4())})).status_code == 201
    await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE user_id=:actor"),{'actor':actor})
    try:
        assert await capability(seed_data,domain,actor,'read')
        assert await capability(seed_data,domain,actor,'export')
        assert not await capability(seed_data,domain,actor,'prepare')
        assert not await capability(seed_data,domain,actor,'approve')
        assert not await capability(seed_data,domain,actor,'send')
        await mutate(text('UPDATE "user" SET is_active=false WHERE id=:actor'),{'actor':actor})
        assert not await capability(seed_data,domain,actor,'read')
    finally:
        await mutate(text('UPDATE "user" SET is_active=true WHERE id=:actor'),{'actor':actor})
        await mutate(text("UPDATE membership SET role='CLERK' WHERE user_id=:actor"),{'actor':actor})


async def test_same_key_concurrency_and_wrong_domain_revocation(client_a_admin,seed_data):
    import asyncio
    _,_,result_domain = await domain_fixture(client_a_admin)
    domain = result_domain["id"]
    _,_,other_domain = await domain_fixture(client_a_admin)
    other = other_domain["id"]
    path = f'/api/person-data-domains/{domain}/grants'
    key = {'Idempotency-Key':str(uuid.uuid4())}
    body = {'user_id':str(seed_data['user_a_clerk_id']),'capabilities':['read'],'reason_code':'TEAM_ACCESS'}
    a,b = await asyncio.gather(client_a_admin.post(path,json=body,headers=key),client_a_admin.post(path,json=body,headers=key))
    assert a.status_code == b.status_code == 201
    assert a.json()['id'] == b.json()['id']
    assert (await client_a_admin.post(f"/api/person-data-domains/{other}/grants/{a.json()['id']}/revoke")).status_code == 404


async def test_raw_database_evidence_and_actor_fences(client_a_admin,seed_data):
    from sqlalchemy.exc import DBAPIError
    _,_,result_domain = await domain_fixture(client_a_admin)
    domain = result_domain["id"]
    path = f'/api/person-data-domains/{domain}/grants'
    result = await client_a_admin.post(path,json={'user_id':str(seed_data['user_a_clerk_id']),'capabilities':['read'],'reason_code':'TEAM_ACCESS'},headers={'Idempotency-Key':str(uuid.uuid4())})
    grant = uuid.UUID(result.json()['id'])
    async with get_tenant_session(seed_data['org_a_id']) as session:
        await session.execute(text("SELECT set_config('app.current_user_id', :actor, true)"),{'actor':str(seed_data['user_a_admin_id'])})
        for statement in ["UPDATE person_data_domain SET label='Changed' WHERE id=:domain", 'DELETE FROM person_data_domain WHERE id=:domain',
                          "UPDATE person_data_grant SET reason_code='REPLACEMENT' WHERE id=:grant",'DELETE FROM person_data_grant WHERE id=:grant',
                          'UPDATE person_data_grant SET revoked_at=now(),revoked_by=:other WHERE id=:grant']:
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    await session.execute(text(statement),{'domain':uuid.UUID(domain),'grant':grant,'other':seed_data['user_a_clerk_id']})
        audits = (await session.execute(text("SELECT action,actor_user_id FROM audit_event WHERE entity_id IN (:domain,:grant)"),{'domain':uuid.UUID(domain),'grant':grant})).all()
        assert {row.action for row in audits} == {'person_data.domain_prepared','person_data.grant_prepared'}
        assert all(row.actor_user_id == seed_data['user_a_admin_id'] for row in audits)
    async with get_tenant_session(seed_data['org_b_id']) as session:
        await session.execute(text("SELECT set_config('app.current_user_id', :actor, true)"),{'actor':str(seed_data['user_b_admin_id'])})
        assert not await session.scalar(text("SELECT has_person_data_capability(:org,:domain,'read')"),{'org':seed_data['org_a_id'],'domain':uuid.UUID(domain)})
        assert not await session.scalar(text('SELECT count(*) FROM person_data_domain WHERE id=:domain'),{'domain':uuid.UUID(domain)})


async def test_grant_expiry_uses_database_clock(client_a_admin,seed_data):
    from datetime import datetime,timedelta,timezone
    _,_,result_domain = await domain_fixture(client_a_admin)
    domain = result_domain["id"]
    actor = seed_data['user_a_readonly_id']
    result = await client_a_admin.post(f'/api/person-data-domains/{domain}/grants',headers={'Idempotency-Key':str(uuid.uuid4())},json={
        'user_id':str(actor),'capabilities':['read','export'],'reason_code':'TEAM_ACCESS',
        'expires_at':(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()})
    assert result.status_code == 201,result.text
    assert await capability(seed_data,domain,actor,'read')
    async with get_tenant_session(seed_data['org_a_id']) as session:
        await session.execute(text("SELECT set_config('app.current_user_id', :actor, true)"),{'actor':str(actor)})
        await session.execute(text('SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM expires_at-clock_timestamp()))+0.02) FROM person_data_grant WHERE domain_id=:domain'), {'domain':uuid.UUID(domain)})
        assert not await session.scalar(text("SELECT has_person_data_capability(:org,:domain,'read')"),{'org':seed_data['org_a_id'],'domain':uuid.UUID(domain)})


async def test_stale_admin_snapshot_cannot_prepare_grant(client_a_admin,seed_data,monkeypatch):
    from app.core import tenant
    from tests.test_scoped_account_statements import mutate
    _,_,result_domain = await domain_fixture(client_a_admin)
    domain = result_domain["id"]
    original = tenant.validate_session_principal
    actor = seed_data['user_a_admin_id']
    async def stale(*args, **kwargs):
        role = await original(*args,**kwargs)
        await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE user_id=:actor"),{'actor':actor})
        return role
    monkeypatch.setattr(tenant,'validate_session_principal',stale)
    try:
        result = await client_a_admin.post(f'/api/person-data-domains/{domain}/grants',headers={'Idempotency-Key':str(uuid.uuid4())},json={
            'user_id':str(seed_data['user_a_clerk_id']),'capabilities':['read'],'reason_code':'TEAM_ACCESS'})
        assert result.status_code == 403,result.text
    finally:
        await mutate(text("UPDATE membership SET role='OWNER_ADMIN' WHERE user_id=:actor"),{'actor':actor})


@pytest.mark.parametrize('extra',[{'label':'Private contact name'},{'reason':'Private bank account'},{'reason_code':'arbitrary private text'}])
async def test_contact_metadata_cannot_carry_free_text(client_a_admin,extra):
    body,_,domain=await domain_fixture(client_a_admin)
    assert domain['label']=='Contact '+body['person_id']
    result=await client_a_admin.post('/api/person-data-domains',json={**body,**extra},headers={'Idempotency-Key':str(uuid.uuid4())})
    assert result.status_code==422,result.text


async def test_contact_configuration_audit_is_admin_only_in_strict_mode(client_a_admin,seed_data):
    from tests.test_property_source_enforcement import synthetic_enforcement
    _,_,domain=await domain_fixture(client_a_admin)
    async with synthetic_enforcement(seed_data):
        for actor,expected in [('user_a_admin_id',1),('user_a_clerk_id',0)]:
            async with get_tenant_session(seed_data['org_a_id']) as db:
                await actor_session(db,seed_data[actor])
                rows=(await db.execute(text('SELECT payload_diff FROM audit_event WHERE entity_id=:id'),{'id':uuid.UUID(domain['id'])})).scalars().all()
                assert len(rows)==expected
                if rows: assert rows==[{'reason_code':'INITIAL_SETUP'}]


async def test_contact_migration_preserves_evidence_and_cannot_enable_source_access(client_a_admin):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL
    await domain_fixture(client_a_admin)
    migration=importlib.import_module('migrations.versions.0068_person_data_domains')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as connection:
            with pytest.raises(DBAPIError,match='Retain contact'):
                with connection.begin_nested(),Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
            transaction=connection.begin_nested()
            try:
                connection.execute(text('TRUNCATE person_data_grant,person_data_domain'))
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
                    migration.upgrade()
                assert connection.scalar(text("SELECT to_regclass('public.person_data_domain')"))
                assert not connection.scalar(text('SELECT property_source_activation_ready(gen_random_uuid())'))
            finally: transaction.rollback()
    finally: engine.dispose()


@pytest.mark.parametrize('table',['person','"user"','membership','audit_event','property_source_security'])
async def test_contact_migration_refuses_busy_dependencies(seed_data,table):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0068_person_data_domains')
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


@pytest.mark.parametrize('route',['page','detail'])
@pytest.mark.parametrize('change',['demotion','deactivation'])
async def test_stale_read_principal_cannot_disclose_prepared_domain(client_a_admin,seed_data,monkeypatch,route,change):
    from app.core import tenant
    from tests.test_scoped_account_statements import mutate
    _,_,prepared = await domain_fixture(client_a_admin)
    domain = prepared["id"]
    original = tenant.validate_session_principal
    actor = seed_data['user_a_admin_id']
    async def stale(*args, **kwargs):
        role = await original(*args,**kwargs)
        statement = "UPDATE membership SET role='READ_ONLY' WHERE user_id=:actor" if change == 'demotion' else 'UPDATE "user" SET is_active=false WHERE id=:actor'
        await mutate(text(statement),{'actor':actor})
        return role
    monkeypatch.setattr(tenant,'validate_session_principal',stale)
    try:
        result = await client_a_admin.get('/api/person-data-domains/'+('page' if route == 'page' else domain))
        if change == 'deactivation':
            assert result.status_code == 401,result.text
        elif route == 'detail':
            assert result.status_code == 404,result.text
        else:
            assert result.status_code == 200,result.text
            assert domain not in {item['id'] for item in result.json()['items']}
    finally:
        await mutate(text('UPDATE "user" SET is_active=true WHERE id=:actor'),{'actor':actor})
        await mutate(text("UPDATE membership SET role='OWNER_ADMIN' WHERE user_id=:actor"),{'actor':actor})


async def test_contact_pages_follow_exact_grants_and_reject_cursor_reuse(client_a_admin,client_a_clerk,seed_data):
    domains=[]
    for _ in range(3):
        _,_,domain=await domain_fixture(client_a_admin)
        domains.append(domain)
        result=await client_a_admin.post(f"/api/person-data-domains/{domain['id']}/grants",headers={'Idempotency-Key':str(uuid.uuid4())},json={
            'user_id':str(seed_data['user_a_clerk_id']),'capabilities':['read'],'reason_code':'TEAM_ACCESS'})
        assert result.status_code==201
    first=await client_a_clerk.get('/api/person-data-domains/page?limit=1')
    assert first.status_code==200 and len(first.json()['items'])==1
    cursor=first.json()['next_cursor']
    second=await client_a_clerk.get('/api/person-data-domains/page',params={'limit':1,'cursor':cursor})
    assert second.status_code==200
    assert second.json()['items'][0]['id']!=first.json()['items'][0]['id']
    changed=await client_a_clerk.get('/api/person-data-domains/page',params={'limit':1,'cursor':cursor,'person_id':domains[0]['person_id']})
    assert changed.status_code==422,changed.text


@pytest.mark.parametrize('change',['forged_actor','foreign_person','invalid_reason','client_label'])
async def test_raw_contact_setup_rejects_forged_metadata(client_a_admin,seed_data,change):
    from sqlalchemy.exc import DBAPIError
    person=await client_a_admin.post('/api/persons',json={'last_name':'Raw metadata target'})
    person_id=uuid.UUID(person.json()['id'])
    org=seed_data['org_a_id']
    params={'person':person_id,'org':org,'actor':seed_data['user_a_admin_id'],'reason':'INITIAL_SETUP'}
    if change=='forged_actor': params['actor']=seed_data['user_a_clerk_id']
    if change=='foreign_person': params['person']=uuid.uuid4()
    if change=='invalid_reason': params['reason']='Private details'
    columns='organization_id,person_id,created_by,request_key,reason_code'
    values=':org,:person,:actor,gen_random_uuid(),:reason'
    if change=='client_label': columns+=',label';values+=",'Private name'"
    async with get_tenant_session(org) as db:
        await actor_session(db,seed_data['user_a_admin_id'])
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text(f'INSERT INTO person_data_domain({columns}) VALUES({values})'),params)
        assert not await db.scalar(text('SELECT count(*) FROM person_data_domain WHERE person_id=:id'),{'id':person_id})
