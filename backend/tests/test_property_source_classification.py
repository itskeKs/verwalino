"""Bound legacy originals need explicit current review, never inferred custody."""
import uuid
import pytest
from sqlalchemy import text
from app.core.db import get_tenant_session
from tests.test_property_source_admission import setup
from tests.test_property_source_binding import approve_grant,applied,bind
from tests.test_declaration_import_api import isolated_document_store,enable_declaration_imports  # noqa: F401

pytestmark=pytest.mark.asyncio


async def legacy_bound(api,seed):
    ids=await setup(api,seed,authorized=False)
    ids['review_grant']=await approve_grant(api,seed,ids)
    await applied(seed,ids)
    await bind(seed,ids)
    return ids


async def classification_body(api,ids,decision='ADMITTED'):
    response=await api.get('/api/property-source-classifications/pending/page')
    assert response.status_code==200,response.text
    source=next(item for item in response.json()['items'] if item['document_id']==str(ids['document']))
    return {'document_id':source['document_id'],'property_id':source['property_id'],
        'content_sha256':source['content_sha256'],'source_context_digest':source['source_context_digest'],
        'decision':decision,'reason':'Explicit review of current original audience'}


async def test_bound_own_original_review_is_atomic_immutable_and_replay_safe(client_a_admin,seed_data):
    ids=await legacy_bound(client_a_admin,seed_data)
    body=await classification_body(client_a_admin,ids)
    preview=await client_a_admin.get(f"/api/property-source-classifications/originals/{ids['document']}/content")
    assert preview.status_code==200 and preview.content==b'%PDF-1.4\n%%EOF'
    key={'Idempotency-Key':str(uuid.uuid4())}
    result=await client_a_admin.post('/api/property-source-classifications',headers=key,json=body)
    assert result.status_code==201,result.text
    replay=await client_a_admin.post('/api/property-source-classifications',headers=key,json=body)
    assert replay.json()==result.json()
    assert (await client_a_admin.post('/api/property-source-classifications',headers=key,json={**body,'decision':'QUARANTINED'})).status_code==409
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        assert await db.scalar(text('SELECT count(*) FROM property_source_classification WHERE document_id=:document'),ids)==1
        assert await db.scalar(text('SELECT count(*) FROM property_source_admission WHERE document_id=:document'),ids)==0
        assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:document AND action='property_document.classified'"),ids)==1
    assert (await client_a_admin.post(f"/api/property-data-domains/{ids['domain']}/grants/{ids['review_grant']}/revoke")).status_code==204
    assert (await client_a_admin.post('/api/property-source-classifications',headers=key,json=body)).status_code==404


async def test_administrator_role_without_source_grant_cannot_list_or_preview_bound_original(client_a_admin,client_a_clerk,client_b_admin,seed_data):
    ids=await setup(client_a_admin,seed_data,authorized=False)
    await applied(seed_data,ids)
    await bind(seed_data,ids)
    response=await client_a_admin.get('/api/property-source-classifications/pending/page')
    assert response.status_code==200
    assert all(item['document_id']!=str(ids['document']) for item in response.json()['items'])
    for api in (client_a_admin,client_a_clerk,client_b_admin):
        assert (await api.get(f"/api/property-source-classifications/originals/{ids['document']}/content")).status_code in (403,404)


async def test_unknown_custody_can_only_be_quarantined_without_original_access(client_a_admin,seed_data):
    from tests.test_scoped_account_statements import mutate
    ids=await legacy_bound(client_a_admin,seed_data)
    unknown=uuid.uuid4()
    await mutate(text("INSERT INTO property_document(id,organization_id,property_id,doc_type,title,original_filename,mime_type,content_byte_size,content_sha256,storage_uri,storage_backend,page_count,uploaded_by) "
        "SELECT :new,organization_id,property_id,doc_type,'Hidden original title','hidden-original.pdf',mime_type,content_byte_size,content_sha256,storage_uri,storage_backend,page_count,NULL FROM property_document WHERE id=:document"),
        {'new':unknown,'document':ids['document']})
    ids={**ids,'document':unknown}
    await mutate(text("CREATE POLICY test_classification_source ON property_document AS RESTRICTIVE FOR SELECT USING(property_source_classification_content_access(organization_id,id))"),{})
    try:
        body=await classification_body(client_a_admin,ids)
        response=await client_a_admin.get('/api/property-source-classifications/pending/page')
        row=next(item for item in response.json()['items'] if item['document_id']==str(unknown))
        assert row['source_uploaded_by'] is None and not row['can_admit'] and not row['can_preview']
        assert 'Hidden original title' not in response.text and 'hidden-original.pdf' not in response.text
        assert (await client_a_admin.get(f'/api/property-source-classifications/originals/{unknown}/content')).status_code==404
        assert (await client_a_admin.post('/api/property-source-classifications',headers={'Idempotency-Key':str(uuid.uuid4())},json=body)).status_code==404
        result=await client_a_admin.post('/api/property-source-classifications',headers={'Idempotency-Key':str(uuid.uuid4())},json={**body,'decision':'QUARANTINED'})
        assert result.status_code==201,result.text
        assert result.json()['decision']=='QUARANTINED'
    finally: await mutate(text('DROP POLICY test_classification_source ON property_document'),{})


@pytest.mark.parametrize('field',['content_sha256','source_context_digest','property_id'])
async def test_classification_rejects_changed_context(client_a_admin,seed_data,field):
    ids=await legacy_bound(client_a_admin,seed_data)
    body=await classification_body(client_a_admin,ids)
    body[field]=str(uuid.uuid4()) if field=='property_id' else '0'*64
    response=await client_a_admin.post('/api/property-source-classifications',headers={'Idempotency-Key':str(uuid.uuid4())},json=body)
    assert response.status_code==409,response.text


async def test_classification_serializes_identical_retries_and_retains_link_context(client_a_admin,seed_data):
    import asyncio
    from sqlalchemy.exc import DBAPIError
    ids=await legacy_bound(client_a_admin,seed_data)
    body=await classification_body(client_a_admin,ids)
    key={'Idempotency-Key':str(uuid.uuid4())}
    responses=await asyncio.wait_for(asyncio.gather(*[client_a_admin.post('/api/property-source-classifications',headers=key,json=body) for _ in range(2)]),5)
    assert [r.status_code for r in responses]==[201,201]
    assert responses[0].json()==responses[1].json()
    with pytest.raises(DBAPIError,match='Classified source links are immutable'):
        async with get_tenant_session(seed_data['org_a_id']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
            await db.execute(text('DELETE FROM declaration_import_document WHERE document_id=:document'),ids)
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:document AND action='property_document.classified'"),ids)==1


async def test_classification_pages_are_bounded_and_cursor_bound(client_a_admin,client_b_admin,seed_data):
    first=await legacy_bound(client_a_admin,seed_data)
    second=await legacy_bound(client_a_admin,seed_data)
    response=await client_a_admin.get('/api/property-source-classifications/pending/page?limit=1')
    assert response.status_code==200 and len(response.json()['items'])==1 and response.json()['next_cursor']
    cursor=response.json()['next_cursor']
    page2=await client_a_admin.get('/api/property-source-classifications/pending/page',params={'limit':1,'cursor':cursor})
    assert page2.status_code==200 and page2.json()['items'][0]['id']!=response.json()['items'][0]['id']
    assert (await client_b_admin.get('/api/property-source-classifications/pending/page',params={'cursor':cursor})).status_code==422
    assert (await client_a_admin.get('/api/property-source-classifications/pending/page',params={'cursor':cursor,'q':'changed'})).status_code==422
    assert (await client_a_admin.get('/api/property-source-classifications/pending/page?limit=201')).status_code==422
    assert first['document']!=second['document']


@pytest.mark.parametrize('field,value',[('document_role','ANNEX'),('sequence_no',2)])
async def test_classified_link_role_and_order_are_immutable(client_a_admin,seed_data,field,value):
    from sqlalchemy.exc import DBAPIError
    ids=await legacy_bound(client_a_admin,seed_data)
    body=await classification_body(client_a_admin,ids)
    key={'Idempotency-Key':str(uuid.uuid4())}
    receipt=await client_a_admin.post('/api/property-source-classifications',headers=key,json=body)
    assert receipt.status_code==201
    with pytest.raises(DBAPIError,match='Classified source links are immutable'):
        async with get_tenant_session(seed_data['org_a_id']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
            await db.execute(text(f'UPDATE declaration_import_document SET {field}=:value WHERE document_id=:document'),{'value':value,'document':ids['document']})
    assert (await client_a_admin.post('/api/property-source-classifications',headers=key,json=body)).json()==receipt.json()


async def test_changed_link_role_invalidates_pending_review_context(client_a_admin,seed_data):
    ids=await legacy_bound(client_a_admin,seed_data)
    body=await classification_body(client_a_admin,ids)
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        await db.execute(text("UPDATE declaration_import_document SET document_role='ANNEX' WHERE document_id=:document"),ids)
    response=await client_a_admin.post('/api/property-source-classifications',headers={'Idempotency-Key':str(uuid.uuid4())},json=body)
    assert response.status_code==409,response.text


async def test_known_uploader_mixed_links_cannot_preview_or_admit(client_a_admin,seed_data,monkeypatch):
    from app.core.storage import get_store
    ids=await legacy_bound(client_a_admin,seed_data)
    other=await client_a_admin.post('/api/declaration-imports',json={'mode':'NEW_PROPERTY'})
    assert other.status_code==201
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        await db.execute(text("INSERT INTO declaration_import_document(organization_id,import_id,document_id,sequence_no,document_role) VALUES(:org,:job,:document,1,'ANNEX')"),
            {'org':seed_data['org_a_id'],'job':uuid.UUID(other.json()['id']),'document':ids['document']})
    def forbidden(*args,**kwargs): raise AssertionError('Mixed source preview must not access storage')
    monkeypatch.setattr(get_store(),'get',forbidden)
    response=await client_a_admin.get('/api/property-source-classifications/pending/page')
    row=next(item for item in response.json()['items'] if item['document_id']==str(ids['document']))
    assert row['linked_import_count']==2 and not row['can_admit'] and not row['can_preview']
    assert 'source.pdf' not in response.text
    assert (await client_a_admin.get(f"/api/property-source-classifications/originals/{ids['document']}/content")).status_code==404
    body=await classification_body(client_a_admin,ids)
    assert (await client_a_admin.post('/api/property-source-classifications',headers={'Idempotency-Key':str(uuid.uuid4())},json=body)).status_code==404
    result=await client_a_admin.post('/api/property-source-classifications',headers={'Idempotency-Key':str(uuid.uuid4())},json={**body,'decision':'QUARANTINED'})
    assert result.status_code==201,result.text


async def test_classification_late_failure_rolls_back_receipt_and_audit(client_a_admin,seed_data,monkeypatch):
    from app.services.property_source_classification_service import PropertySourceClassificationService
    ids=await legacy_bound(client_a_admin,seed_data)
    body=await classification_body(client_a_admin,ids)
    original=PropertySourceClassificationService.classify
    async def fail(self,*args,**kwargs):
        await original(self,*args,**kwargs)
        raise RuntimeError('forced-classification-rollback')
    monkeypatch.setattr(PropertySourceClassificationService,'classify',fail)
    with pytest.raises(RuntimeError,match='forced-classification-rollback'):
        await client_a_admin.post('/api/property-source-classifications',headers={'Idempotency-Key':str(uuid.uuid4())},json=body)
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        assert await db.scalar(text('SELECT count(*) FROM property_source_classification WHERE document_id=:document'),ids)==0
        assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:document AND action='property_document.classified'"),ids)==0


@pytest.mark.parametrize('operation',['DELETE','UPDATE'])
async def test_link_mutation_waits_for_acl_before_locking_source_rows(client_a_admin,seed_data,operation):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_URL
    ids=await legacy_bound(client_a_admin,seed_data)
    body=await classification_body(client_a_admin,ids)
    assert (await client_a_admin.post('/api/property-source-classifications',headers={'Idempotency-Key':str(uuid.uuid4())},json=body)).status_code==201
    marker='classification-lock-'+uuid.uuid4().hex
    async def change():
        with pytest.raises(DBAPIError,match='Classified source links are immutable'):
            async with get_tenant_session(seed_data['org_a_id']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
                command='DELETE FROM declaration_import_document' if operation=='DELETE' else "UPDATE declaration_import_document SET document_role='ANNEX'"
                await db.execute(text(command+' WHERE document_id=:document /* '+marker+' */'),ids)
    engine=create_async_engine(MIGRATOR_URL)
    task=None
    try:
        async with get_tenant_session(seed_data['org_a_id']) as locker:
            await locker.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),{'key':'scope-acl:'+str(seed_data['org_a_id'])})
            task=asyncio.create_task(change())
            async with engine.connect() as observer:
                blocked=False
                for _ in range(300):
                    await observer.execute(text('SELECT pg_stat_clear_snapshot()'))
                    blocked=await observer.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE :query)"),{'query':'%'+marker+'%'})
                    if blocked: break
                    await asyncio.sleep(.01)
                assert blocked and not task.done()
            # If the statement took either row before its ACL wait, NOWAIT fails.
            await locker.execute(text('SELECT id FROM declaration_import WHERE id=:job FOR UPDATE NOWAIT'),ids)
            await locker.execute(text('SELECT id FROM declaration_import_document WHERE document_id=:document FOR UPDATE NOWAIT'),ids)
        await asyncio.wait_for(task,5)
    finally:
        await engine.dispose()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)


async def test_classification_migration_retains_receipts_and_roundtrips_empty(client_a_admin,seed_data):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids=await legacy_bound(client_a_admin,seed_data)
    assert (await client_a_admin.post('/api/property-source-classifications',headers={'Idempotency-Key':str(uuid.uuid4())},json=await classification_body(client_a_admin,ids))).status_code==201
    migration=importlib.import_module('migrations.versions.0056_property_source_classification')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain populated source classification'):
                with db.begin_nested(): migration.downgrade()
            nested=db.begin_nested()
            try:
                db.execute(text('TRUNCATE property_source_classification'))
                migration.downgrade()
                assert db.scalar(text("SELECT to_regclass('property_source_classification')")) is None
                migration.upgrade()
            finally: nested.rollback()
            assert db.scalar(text('SELECT count(*) FROM property_source_classification WHERE document_id=:document'),ids)==1
    finally: engine.dispose()


@pytest.mark.parametrize('table',['property_document','declaration_import','declaration_import_document','property_data_domain','property_data_grant'])
async def test_busy_classification_dependency_refuses_nowait(seed_data,table):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0056_property_source_classification')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        async with get_tenant_session(seed_data['org_a_id']) as reader:
            await reader.execute(text(f'SELECT id FROM {table} LIMIT 1'))
            with pytest.raises(DBAPIError) as caught:
                with engine.begin() as db:
                    db.execute(text("SET LOCAL lock_timeout='1s'"))
                    with Operations.context(MigrationContext.configure(db)): migration.upgrade()
            assert caught.value.orig.pgcode=='55P03' and 'could not obtain lock' in str(caught.value.orig)
    finally: engine.dispose()


@pytest.mark.parametrize('decision',['ADMITTED','QUARANTINED'])
async def test_paused_source_effects_reject_new_classification(client_a_admin,seed_data,decision):
    from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
    ids=await legacy_bound(client_a_admin,seed_data)
    body=await classification_body(client_a_admin,ids,decision)
    async with synthetic_enforcement(seed_data):
        await change_mode(seed_data,'PAUSED')
        response=await client_a_admin.post('/api/property-source-classifications',headers={'Idempotency-Key':str(uuid.uuid4())},json=body)
        assert response.status_code==404,response.text


async def test_raw_classification_canonical_fields_and_live_reviewer_fence(client_a_admin,seed_data):
    import json
    from sqlalchemy.exc import DBAPIError
    from tests.test_scoped_account_statements import mutate
    ids=await legacy_bound(client_a_admin,seed_data)
    body=await classification_body(client_a_admin,ids)
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        context=(await db.execute(text('SELECT * FROM property_source_classification_context(:org,:document,false)'),{'org':seed_data['org_a_id'],'document':ids['document']})).mappings().one()
    params={'org':seed_data['org_a_id'],'document':ids['document'],'property':ids['property'],'domain':context.domain_id,'grant':context.grant_id,
        'hash':context.content_sha256,'digest':context.source_context_digest,'actor':seed_data['user_a_clerk_id'],'key':uuid.uuid4()}
    command=text("INSERT INTO property_source_classification(organization_id,document_id,property_id,domain_id,grant_id,reviewed_by,decision,content_sha256,source_context_digest,reason,request_key,request_digest,created_at) "
        "VALUES(:org,:document,:property,:domain,:grant,:actor,'ADMITTED',:hash,:digest,'  Current source review  ',:key,'forged','2001-01-01') RETURNING id,request_digest,created_at>clock_timestamp()-interval '5 seconds' AS canonical_time")
    with pytest.raises(DBAPIError) as caught:
        async with get_tenant_session(seed_data['org_a_id']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(params['actor'])})
            await db.execute(command,params)
    assert caught.value.orig.sqlstate=='42501'
    params['actor']=seed_data['user_a_admin_id']
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(params['actor'])})
        receipt=(await db.execute(command,params)).mappings().one()
        expected=await db.scalar(text("SELECT encode(sha256(convert_to(CAST(:body AS jsonb)::text,'UTF8')),'hex')"),{'body':json.dumps({**body,'reason':'Current source review'})})
        assert receipt.canonical_time and receipt.request_digest==expected
    with pytest.raises(DBAPIError,match='immutable'):
        await mutate(text("UPDATE property_source_classification SET decision='QUARANTINED' WHERE id=:id"),{'id':receipt.id})
    with pytest.raises(DBAPIError,match='immutable'):
        await mutate(text('DELETE FROM property_source_classification WHERE id=:id'),{'id':receipt.id})
