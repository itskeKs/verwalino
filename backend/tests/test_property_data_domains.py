"""Explicit shared-data grants never inherit financial entitlements."""
import uuid
import pytest
from sqlalchemy import text
from app.core.db import get_tenant_session

pytestmark = pytest.mark.asyncio


async def test_domain_creation_is_idempotent_and_does_not_implicitly_grant(client_a_admin, seed_data):
    prop = await client_a_admin.post('/api/properties',json={'name':'Shared data fixture','management_type':'WEG'})
    assert prop.status_code == 201
    body = {'property_id':prop.json()['id'],'label':'Shared building data','reason':'Reviewed shared source audience'}
    key = {'Idempotency-Key':str(uuid.uuid4())}
    response = await client_a_admin.post('/api/property-data-domains',json=body,headers=key)
    assert response.status_code == 201, response.text
    assert response.json()['enforcement_status'] == 'PREPARATION'
    replay = await client_a_admin.post('/api/property-data-domains',json=body,headers=key)
    assert replay.json()['id'] == response.json()['id']
    changed = await client_a_admin.post('/api/property-data-domains',json={**body,'label':'Different'},headers=key)
    assert changed.status_code == 409
    async with get_tenant_session(seed_data['org_a_id']) as session:
        await session.execute(text("SELECT set_config('app.current_user_id', :actor, true)"),{'actor':str(seed_data['user_a_admin_id'])})
        assert not await session.scalar(text("SELECT has_property_data_capability(:org,:domain,'read')"), {'org':seed_data['org_a_id'],'domain':uuid.UUID(response.json()['id'])})


async def domain_fixture(api):
    response = await api.post('/api/properties',json={'name':'Shared property '+str(uuid.uuid4())[:8],'management_type':'WEG'})
    assert response.status_code == 201,response.text
    prop = response.json()['id']
    response = await api.post('/api/property-data-domains',headers={'Idempotency-Key':str(uuid.uuid4())},
        json={'property_id':prop,'label':'Reviewed shared data','reason':'Explicit shared building audience'})
    assert response.status_code == 201,response.text
    return prop,response.json()['id']


async def capability(seed,domain,actor,cap):
    async with get_tenant_session(seed['org_a_id']) as session:
        await session.execute(text("SELECT set_config('app.current_user_id', :actor, true)"),{'actor':str(actor)})
        return await session.scalar(text('SELECT has_property_data_capability(:org,:domain,:cap)'),
            {'org':seed['org_a_id'],'domain':uuid.UUID(domain),'cap':cap})


async def test_explicit_grants_replay_without_restoring_revocation(client_a_admin,client_a_clerk,client_b_admin,seed_data):
    _,domain = await domain_fixture(client_a_admin)
    path = f'/api/property-data-domains/{domain}'
    assert (await client_a_clerk.get(path)).status_code == 404
    command = {'user_id':str(seed_data['user_a_clerk_id']),'capabilities':['read','prepare','export'], 'reason':'Shared sources reviewed'}
    key = {'Idempotency-Key':str(uuid.uuid4())}
    first = await client_a_admin.post(path+'/grants',json=command,headers=key)
    assert first.status_code == 201,first.text
    grant = first.json()['id']
    assert (await client_a_admin.post(path+'/grants',json=command,headers=key)).json()['id'] == grant
    assert (await client_a_admin.post(path+'/grants',json={**command,'reason':'Changed'},headers=key)).status_code == 409
    assert (await client_a_clerk.get(path)).status_code == 200
    assert (await client_a_clerk.get(path+'/grants/page')).status_code == 403
    assert (await client_b_admin.get(path)).status_code == 404
    assert await capability(seed_data,domain,seed_data['user_a_clerk_id'],'prepare')
    assert not await capability(seed_data,domain,seed_data['user_a_admin_id'],'read')
    for _ in range(2):
        assert (await client_a_admin.post(path+f'/grants/{grant}/revoke')).status_code == 204
    replay = await client_a_admin.post(path+'/grants',json=command,headers=key)
    assert replay.status_code == 201 and replay.json()['revoked_at'] is not None
    assert not await capability(seed_data,domain,seed_data['user_a_clerk_id'],'read')
    assert (await client_a_clerk.get(path)).status_code == 404
    replacement = await client_a_admin.post(path+'/grants',json=command,headers={'Idempotency-Key':str(uuid.uuid4())})
    assert replacement.status_code == 201 and replacement.json()['id'] != grant
    history = await client_a_admin.get(path+'/grants/page')
    assert len(history.json()['items']) == 2


async def test_shared_data_and_financial_grants_are_independent(client_a_admin,seed_data):
    prop,domain = await domain_fixture(client_a_admin)
    sphere = await client_a_admin.post('/api/accounting-scopes',headers={'Idempotency-Key':str(uuid.uuid4())},json={
        'property_id':prop,'scope_type':'WEG','label':'Financial sphere','valid_from':'2025-01-01','whole_property':True})
    assert sphere.status_code == 201,sphere.text
    scope_id = sphere.json()['id']
    command = {'user_id':str(seed_data['user_a_clerk_id']),'capabilities':['read','prepare'],'reason':'Explicit finance grant'}
    result = await client_a_admin.post(f'/api/accounting-scopes/{scope_id}/grants',headers={'Idempotency-Key':str(uuid.uuid4())},json=command)
    assert result.status_code == 201,result.text
    assert not await capability(seed_data,domain,seed_data['user_a_clerk_id'],'read')
    command['user_id'] = str(seed_data['user_a_admin_id'])
    result = await client_a_admin.post(f'/api/property-data-domains/{domain}/grants',headers={'Idempotency-Key':str(uuid.uuid4())},json=command)
    assert result.status_code == 201,result.text
    async with get_tenant_session(seed_data['org_a_id']) as session:
        await session.execute(text("SELECT set_config('app.current_user_id', :actor, true)"),{'actor':str(seed_data['user_a_admin_id'])})
        assert not await session.scalar(text("SELECT has_scope_capability(:org,:scope,'read')"),{'org':seed_data['org_a_id'],'scope':uuid.UUID(scope_id)})
    assert await capability(seed_data,domain,seed_data['user_a_admin_id'],'read')


async def test_role_and_deactivation_recheck(client_a_admin,seed_data):
    from tests.test_scoped_account_statements import mutate
    _,domain = await domain_fixture(client_a_admin)
    actor = seed_data['user_a_clerk_id']
    command = {'user_id':str(actor),'capabilities':['read','prepare','approve','export'],'reason':'Live role test'}
    assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants',json=command,headers={'Idempotency-Key':str(uuid.uuid4())})).status_code == 201
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


@pytest.mark.parametrize('change', ['missing_read','financial_capability','past_expiry','foreign_user'])
async def test_invalid_grants_rejected(client_a_admin,seed_data,change):
    _,domain = await domain_fixture(client_a_admin)
    body = {'user_id':str(seed_data['user_a_clerk_id']),'capabilities':['read'],'reason':'Reviewed'}
    if change == 'missing_read': body['capabilities'] = ['prepare']
    if change == 'financial_capability': body['capabilities'] = ['read','send']
    if change == 'past_expiry': body['expires_at'] = '2000-01-01T00:00:00Z'
    if change == 'foreign_user': body['user_id'] = str(seed_data['user_b_admin_id'])
    result = await client_a_admin.post(f'/api/property-data-domains/{domain}/grants',json=body,headers={'Idempotency-Key':str(uuid.uuid4())})
    assert result.status_code == (404 if change == 'foreign_user' else 422),result.text


async def test_same_key_concurrency_and_wrong_domain_revocation(client_a_admin,seed_data):
    import asyncio
    _,domain = await domain_fixture(client_a_admin)
    _,other = await domain_fixture(client_a_admin)
    path = f'/api/property-data-domains/{domain}/grants'
    key = {'Idempotency-Key':str(uuid.uuid4())}
    body = {'user_id':str(seed_data['user_a_clerk_id']),'capabilities':['read'],'reason':'Concurrent grant'}
    a,b = await asyncio.gather(client_a_admin.post(path,json=body,headers=key),client_a_admin.post(path,json=body,headers=key))
    assert a.status_code == b.status_code == 201
    assert a.json()['id'] == b.json()['id']
    assert (await client_a_admin.post(f"/api/property-data-domains/{other}/grants/{a.json()['id']}/revoke")).status_code == 404


async def test_raw_database_evidence_and_actor_fences(client_a_admin,seed_data):
    from sqlalchemy.exc import DBAPIError
    prop,domain = await domain_fixture(client_a_admin)
    path = f'/api/property-data-domains/{domain}/grants'
    result = await client_a_admin.post(path,json={'user_id':str(seed_data['user_a_clerk_id']),'capabilities':['read'],'reason':'Immutable evidence'},headers={'Idempotency-Key':str(uuid.uuid4())})
    grant = uuid.UUID(result.json()['id'])
    async with get_tenant_session(seed_data['org_a_id']) as session:
        await session.execute(text("SELECT set_config('app.current_user_id', :actor, true)"),{'actor':str(seed_data['user_a_admin_id'])})
        for statement in ["UPDATE property_data_domain SET label='Changed' WHERE id=:domain", 'DELETE FROM property_data_domain WHERE id=:domain',
                          "UPDATE property_data_grant SET reason='Changed' WHERE id=:grant",'DELETE FROM property_data_grant WHERE id=:grant',
                          'UPDATE property_data_grant SET revoked_at=now(),revoked_by=:other WHERE id=:grant']:
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    await session.execute(text(statement),{'domain':uuid.UUID(domain),'grant':grant,'other':seed_data['user_a_clerk_id']})
        audits = (await session.execute(text("SELECT action,actor_user_id FROM audit_event WHERE entity_id IN (:domain,:grant)"),{'domain':uuid.UUID(domain),'grant':grant})).all()
        assert {row.action for row in audits} == {'property_data.domain_prepared','property_data.grant_prepared'}
        assert all(row.actor_user_id == seed_data['user_a_admin_id'] for row in audits)
    async with get_tenant_session(seed_data['org_b_id']) as session:
        await session.execute(text("SELECT set_config('app.current_user_id', :actor, true)"),{'actor':str(seed_data['user_b_admin_id'])})
        assert not await session.scalar(text("SELECT has_property_data_capability(:org,:domain,'read')"),{'org':seed_data['org_a_id'],'domain':uuid.UUID(domain)})
        assert not await session.scalar(text('SELECT count(*) FROM property_data_domain WHERE id=:domain'),{'domain':uuid.UUID(domain)})


async def test_grant_expiry_uses_database_clock(client_a_admin,seed_data):
    from datetime import datetime,timedelta,timezone
    _,domain = await domain_fixture(client_a_admin)
    actor = seed_data['user_a_readonly_id']
    result = await client_a_admin.post(f'/api/property-data-domains/{domain}/grants',headers={'Idempotency-Key':str(uuid.uuid4())},json={
        'user_id':str(actor),'capabilities':['read','export'],'reason':'Short lived review',
        'expires_at':(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()})
    assert result.status_code == 201,result.text
    assert await capability(seed_data,domain,actor,'read')
    async with get_tenant_session(seed_data['org_a_id']) as session:
        await session.execute(text("SELECT set_config('app.current_user_id', :actor, true)"),{'actor':str(actor)})
        await session.execute(text('SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM expires_at-clock_timestamp()))+0.02) FROM property_data_grant WHERE domain_id=:domain'), {'domain':uuid.UUID(domain)})
        assert not await session.scalar(text("SELECT has_property_data_capability(:org,:domain,'read')"),{'org':seed_data['org_a_id'],'domain':uuid.UUID(domain)})


async def test_stale_admin_snapshot_cannot_prepare_grant(client_a_admin,seed_data,monkeypatch):
    from app.core import tenant
    from tests.test_scoped_account_statements import mutate
    _,domain = await domain_fixture(client_a_admin)
    original = tenant.validate_session_principal
    actor = seed_data['user_a_admin_id']
    async def stale(*args, **kwargs):
        role = await original(*args,**kwargs)
        await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE user_id=:actor"),{'actor':actor})
        return role
    monkeypatch.setattr(tenant,'validate_session_principal',stale)
    try:
        result = await client_a_admin.post(f'/api/property-data-domains/{domain}/grants',headers={'Idempotency-Key':str(uuid.uuid4())},json={
            'user_id':str(seed_data['user_a_clerk_id']),'capabilities':['read'],'reason':'Stale admin'})
        assert result.status_code == 403,result.text
    finally:
        await mutate(text("UPDATE membership SET role='OWNER_ADMIN' WHERE user_id=:actor"),{'actor':actor})


async def test_domain_migration_refuses_evidence_loss_and_roundtrips_empty_configuration(client_a_admin):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL
    await domain_fixture(client_a_admin)
    migration = importlib.import_module('migrations.versions.0045_property_data_domains')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as connection:
            with pytest.raises(DBAPIError,match='Retain populated'):
                with connection.begin_nested(),Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
            transaction = connection.begin_nested()
            try:
                connection.execute(text('TRUNCATE property_data_grant,property_data_domain CASCADE'))
                with Operations.context(MigrationContext.configure(connection)):
                    # Remove later typed FK boundaries inside this rollback-only
                    # historical rehearsal; retain every committed receipt.
                    connection.execute(text('TRUNCATE property_source_admission_review'))
                    connection.execute(text('TRUNCATE property_source_classification'))
                    importlib.import_module('migrations.versions.0056_property_source_classification').downgrade()
                    importlib.import_module('migrations.versions.0052_property_source_admission_review').downgrade()
                    importlib.import_module('migrations.versions.0051_declaration_publication_audience').downgrade()
                    importlib.import_module('migrations.versions.0050_property_source_admission').downgrade()
                    migration.downgrade()
                    migration.upgrade()
                assert connection.scalar(text("SELECT to_regclass('public.property_data_domain')"))
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize('route',['page','detail'])
@pytest.mark.parametrize('change',['demotion','deactivation'])
async def test_stale_read_principal_cannot_disclose_prepared_domain(client_a_admin,seed_data,monkeypatch,route,change):
    from app.core import tenant
    from tests.test_scoped_account_statements import mutate
    _,domain = await domain_fixture(client_a_admin)
    original = tenant.validate_session_principal
    actor = seed_data['user_a_admin_id']
    async def stale(*args, **kwargs):
        role = await original(*args,**kwargs)
        statement = "UPDATE membership SET role='READ_ONLY' WHERE user_id=:actor" if change == 'demotion' else 'UPDATE "user" SET is_active=false WHERE id=:actor'
        await mutate(text(statement),{'actor':actor})
        return role
    monkeypatch.setattr(tenant,'validate_session_principal',stale)
    try:
        result = await client_a_admin.get('/api/property-data-domains/'+('page' if route == 'page' else domain))
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
