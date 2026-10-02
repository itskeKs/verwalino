"""HTTP authentication snapshots cannot outlive a declaration principal change."""
import uuid
import pytest
from sqlalchemy import text
from app.core import tenant
from app.core.storage import get_store
from tests.test_financial_scope_boundary import scope_seed, worker_tenant  # noqa: F401
from tests.test_scoped_account_statements import client, mutate
from tests.test_declaration_import_api import isolated_document_store, enable_declaration_imports  # noqa: F401

pytestmark = pytest.mark.asyncio


async def draft(ids):
    async with client(ids) as api:
        created = await api.post('/api/declaration-imports', json={'mode':'NEW_PROPERTY'})
        assert created.status_code == 201
        job = created.json()['id']
        uploaded = await api.post(f'/api/declaration-imports/{job}/documents',
            files={'file':('source.pdf',b'%PDF-1.4\n%%EOF','application/pdf')},
            data={'document_role':'DECLARATION','sequence_no':'1'})
        assert uploaded.status_code == 201
        return job, uploaded.json()['id']


@pytest.mark.parametrize('operation', ['create','get','list','upload','metadata','content','start','retry','cancel','candidates','review','bulk','preview','approve'])
async def test_role_change_after_auth_snapshot_denies_before_request_effects(scope_seed, migrator_session, monkeypatch, operation):
    await migrator_session.commit()
    ids = {'org':scope_seed['org_a_id'], 'clerk':scope_seed['user_a_clerk_id']}
    job, document = await draft(ids)
    original_validate = tenant.validate_session_principal
    async def stale_auth(*args, **kwargs):
        role = await original_validate(*args, **kwargs)
        await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"), ids)
        return role
    monkeypatch.setattr(tenant, 'validate_session_principal', stale_auth)
    def forbidden(*args, **kwargs):
        raise AssertionError('Original storage must not be accessed after role revocation')
    monkeypatch.setattr(get_store(), 'get', forbidden)
    monkeypatch.setattr(get_store(), 'put', forbidden)
    endpoint = f'/api/declaration-imports/{job}'
    key = {'Idempotency-Key':str(uuid.uuid4())}
    requests = {
        'create':('POST','/api/declaration-imports',{'json':{'mode':'NEW_PROPERTY'}}),
        'get':('GET',endpoint,{}),
        'list':('GET',endpoint+'/documents',{}),
        'upload':('POST',endpoint+'/documents',{'files':{'file':('second.pdf',b'%PDF-1.4','application/pdf')},'data':{'document_role':'ANNEX','sequence_no':'2'}}),
        'metadata':('PATCH',endpoint+f'/documents/{document}',{'json':{'title':'Changed'}}),
        'content':('GET',endpoint+f'/documents/{document}/content',{}),
        'start':('POST',endpoint+'/start',{'headers':key}),
        'retry':('POST',endpoint+'/retry',{'headers':key}),
        'cancel':('POST',endpoint+'/cancel',{}),
        'candidates':('GET',endpoint+'/candidates',{}),
        'review':('PATCH',endpoint+f'/candidates/{uuid.uuid4()}',{'json':{'draft_revision':1,'decision':'ACCEPTED'}}),
        'bulk':('POST',endpoint+'/candidates/bulk-review',{'json':{'draft_revision':1,'decision':'ACCEPTED','candidate_ids':[str(uuid.uuid4())]}}),
        'preview':('GET',endpoint+'/approval-preview',{}),
        'approve':('POST',endpoint+'/approve',{'json':{'draft_revision':1,'idempotency_key':str(uuid.uuid4())}}),
    }
    method, path, kwargs = requests[operation]
    async with client(ids) as api:
        response = await api.request(method,path,**kwargs)
        assert response.status_code == 403, response.text


@pytest.mark.parametrize('endpoint', ['metadata','content'])
async def test_deactivation_after_auth_snapshot_denies_approved_source_before_storage(scope_seed, migrator_session, monkeypatch, endpoint):
    await migrator_session.commit()
    ids = {'org':scope_seed['org_a_id'],'clerk':scope_seed['user_a_clerk_id']}
    _, document = await draft(ids)
    async with client(ids) as api:
        property_response = await api.post('/api/properties',json={'name':'Approved source fixture','management_type':'WEG'})
        assert property_response.status_code == 201
    property_id = property_response.json()['id']
    await mutate(text('UPDATE property_document SET property_id=:property WHERE id=:document'),
                 {'property':uuid.UUID(property_id),'document':uuid.UUID(document)})
    # Approved original compatibility access deliberately includes active read-only members.
    await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"),ids)
    path = f'/api/properties/{property_id}/documents'
    if endpoint == 'content':
        path += f'/{document}/content'
    async with client(ids) as api:
        assert (await api.get(path)).status_code == 200
    original_validate = tenant.validate_session_principal
    async def deactivate_after_auth(*args, **kwargs):
        role = await original_validate(*args, **kwargs)
        await mutate(text('UPDATE "user" SET is_active=false WHERE organization_id=:org AND id=:clerk'),ids)
        return role
    monkeypatch.setattr(tenant,'validate_session_principal',deactivate_after_auth)
    def forbidden(*args, **kwargs):
        raise AssertionError('Original bytes must not be read by a deactivated actor')
    monkeypatch.setattr(get_store(),'get',forbidden)
    async with client(ids) as api:
        response = await api.get(path)
        assert response.status_code == 401, response.text


async def test_source_request_orders_revocation_after_its_transaction(scope_seed, migrator_session, monkeypatch):
    import asyncio
    from app.core import live_principal
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    await migrator_session.commit()
    ids = {'org':scope_seed['org_a_id'],'clerk':scope_seed['user_a_clerk_id']}
    job, document = await draft(ids)
    locked, release, revoking = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_guard = live_principal.require_live_principal
    async def held_guard(*args, **kwargs):
        await original_guard(*args, **kwargs)
        locked.set()
        await release.wait()
    monkeypatch.setattr(live_principal,'require_live_principal',held_guard)
    engine = create_async_engine(MIGRATOR_URL,connect_args={'server_settings':{'application_name':'hv-live-request-revocation'}})
    async def revoke():
        async with engine.begin() as connection:
            await connection.execute(text("SET LOCAL lock_timeout='5s'"))
            revoking.set()
            await connection.execute(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"),ids)
    try:
        async with client(ids) as api:
            request = asyncio.create_task(api.get(f'/api/declaration-imports/{job}/documents/{document}/content'))
            await asyncio.wait_for(locked.wait(),2)
            mutation = asyncio.create_task(revoke())
            await asyncio.wait_for(revoking.wait(),2)
            observer = create_async_engine(MIGRATOR_URL)
            try:
                async def wait_for_lock():
                    async with observer.connect() as connection:
                        for _ in range(100):
                            waiting = await connection.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='hv-live-request-revocation' AND wait_event='advisory')"))
                            if waiting:
                                return
                            await asyncio.sleep(.01)
                        raise AssertionError('Revocation must wait on the held request ACL lock')
                await asyncio.wait_for(wait_for_lock(),2)
            finally:
                await observer.dispose()
            assert not mutation.done()
            release.set()
            assert (await asyncio.wait_for(request,2)).status_code == 200
            await asyncio.wait_for(mutation,2)
            assert (await api.get(f'/api/declaration-imports/{job}/documents/{document}/content')).status_code == 403
    finally:
        release.set()
        await engine.dispose()
