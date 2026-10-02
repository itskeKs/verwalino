"""Present-day reviewed disclosure is separate from actual attachment proof."""
import uuid
import pytest
from sqlalchemy import text
from app.core.db import get_tenant_session
from tests.test_property_source_admission import setup
from tests.test_property_source_access import access
from tests.test_declaration_import_api import isolated_document_store,enable_declaration_imports  # noqa: F401

pytestmark=pytest.mark.asyncio


async def legacy_source(api,seed):
    ids=await setup(api,seed,authorized=False)
    grant=await api.post(f"/api/property-data-domains/{ids['domain']}/grants",
        headers={'Idempotency-Key':str(uuid.uuid4())},json={'user_id':str(seed['user_a_admin_id']),
            'capabilities':['read','prepare','approve'],'reason':'Review current source disclosure'})
    assert grant.status_code==201,grant.text
    ids['review_grant']=grant.json()['id']
    return ids


async def test_explicit_current_review_admits_legacy_original_without_fabricating_attachment_history(client_a_admin,seed_data):
    ids=await legacy_source(client_a_admin,seed_data)
    assert not await access(seed_data,'property_document',ids['document'],actor=seed_data['user_a_clerk_id'])
    listed=await client_a_admin.get('/api/property-source-admission-reviews/pending/page')
    assert listed.status_code==200,listed.text
    source=next(row for row in listed.json()['items'] if row['document_id']==str(ids['document']))
    body={'document_id':str(ids['document']),'import_id':str(ids['job']),
          'draft_revision':source['draft_revision'],'content_sha256':source['content_sha256'],
          'reason':'Original and target reviewed for current team access'}
    key={'Idempotency-Key':str(uuid.uuid4())}
    admitted=await client_a_admin.post('/api/property-source-admission-reviews',headers=key,json=body)
    assert admitted.status_code==201,admitted.text
    assert await access(seed_data,'property_document',ids['document'],actor=seed_data['user_a_clerk_id'])
    replay=await client_a_admin.post('/api/property-source-admission-reviews',headers=key,json=body)
    assert replay.status_code==201 and replay.json()==admitted.json()
    changed=await client_a_admin.post('/api/property-source-admission-reviews',headers=key,json={**body,'reason':'Different review'})
    assert changed.status_code==409
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        assert await db.scalar(text('SELECT count(*) FROM property_source_admission WHERE document_id=:document'),ids)==0
        assert await db.scalar(text('SELECT count(*) FROM property_source_admission_review WHERE document_id=:document'),ids)==1
        assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:document AND action='property_document.admission_reviewed'"),ids)==1
    revoked=await client_a_admin.post(f"/api/property-data-domains/{ids['domain']}/grants/{ids['review_grant']}/revoke")
    assert revoked.status_code==204
    assert await access(seed_data,'property_document',ids['document'],actor=seed_data['user_a_clerk_id'])
    assert (await client_a_admin.post('/api/property-source-admission-reviews',headers=key,json=body)).status_code==404


async def pending_body(api,ids):
    response=await api.get('/api/property-source-admission-reviews/pending/page')
    assert response.status_code==200,response.text
    source=next(row for row in response.json()['items'] if row['document_id']==str(ids['document']))
    return {'document_id':source['document_id'],'import_id':source['import_id'],'draft_revision':source['draft_revision'],
            'content_sha256':source['content_sha256'],'reason':'Reviewed current disclosure'}


async def test_only_owner_with_exact_current_rights_can_read_original_or_review(client_a_admin,client_a_clerk,client_b_admin,seed_data,monkeypatch):
    from app.core.storage import get_store
    ids=await legacy_source(client_a_admin,seed_data)
    body=await pending_body(client_a_admin,ids)
    content=await client_a_admin.get(f"/api/property-source-admission-reviews/originals/{ids['document']}/content")
    assert content.status_code==200 and content.content==b'%PDF-1.4\n%%EOF'
    def forbidden(*args,**kwargs): raise AssertionError('Denied review must not read original storage')
    monkeypatch.setattr(get_store(),'get',forbidden)
    for api in [client_a_clerk,client_b_admin]:
        assert (await api.get(f"/api/property-source-admission-reviews/originals/{ids['document']}/content")).status_code==404
        assert (await api.post('/api/property-source-admission-reviews',headers={'Idempotency-Key':str(uuid.uuid4())},json=body)).status_code==404
    # Explicit destination approval still cannot substitute for source ownership.
    assert (await client_a_admin.post(f"/api/property-data-domains/{ids['domain']}/grants/{ids['reader_grant']}/revoke")).status_code==204
    granted=await client_a_admin.post(f"/api/property-data-domains/{ids['domain']}/grants",headers={'Idempotency-Key':str(uuid.uuid4())},
        json={'user_id':str(seed_data['user_a_clerk_id']),'capabilities':['read','prepare','approve'],'reason':'Not the source owner'})
    assert granted.status_code==201
    assert (await client_a_clerk.post('/api/property-source-admission-reviews',headers={'Idempotency-Key':str(uuid.uuid4())},json=body)).status_code==404


@pytest.mark.parametrize('change',['hash','revision','import','reason'])
async def test_stale_or_missing_review_context_has_no_effect(client_a_admin,seed_data,change):
    ids=await legacy_source(client_a_admin,seed_data)
    body=await pending_body(client_a_admin,ids)
    if change=='hash': body['content_sha256']='0'*64
    if change=='revision': body['draft_revision']+=1
    if change=='import': body['import_id']=str(uuid.uuid4())
    if change=='reason': body['reason']='  \n '
    response=await client_a_admin.post('/api/property-source-admission-reviews',headers={'Idempotency-Key':str(uuid.uuid4())},json=body)
    assert response.status_code==(422 if change=='reason' else 409),response.text
    assert not await access(seed_data,'property_document',ids['document'],actor=seed_data['user_a_clerk_id'])


async def test_partial_review_keeps_other_sources_closed_and_replays_during_queued_work(client_a_admin,seed_data):
    ids=await setup(client_a_admin,seed_data,authorized=False)
    extra=await client_a_admin.post(f"/api/declaration-imports/{ids['job']}/documents",files={'file':('annex.pdf',b'%PDF-1.4\n%%EOF','application/pdf')},
        data={'document_role':'ANNEX','sequence_no':'2'})
    assert extra.status_code==201,extra.text
    grant=await client_a_admin.post(f"/api/property-data-domains/{ids['domain']}/grants",headers={'Idempotency-Key':str(uuid.uuid4())},
        json={'user_id':str(seed_data['user_a_admin_id']),'capabilities':['read','prepare','approve'],'reason':'Review both originals individually'})
    assert grant.status_code==201
    body=await pending_body(client_a_admin,ids)
    other_body=await pending_body(client_a_admin,{**ids,'document':uuid.UUID(extra.json()['id'])})
    key={'Idempotency-Key':str(uuid.uuid4())}
    accepted=await client_a_admin.post('/api/property-source-admission-reviews',headers=key,json=body)
    assert accepted.status_code==201,accepted.text
    assert not await access(seed_data,'declaration_import',ids['job'],actor=seed_data['user_a_clerk_id'])
    assert (await client_a_admin.post('/api/property-source-admission-reviews',headers=key,json=body)).json()==accepted.json()
    started=await client_a_admin.post(f"/api/declaration-imports/{ids['job']}/start",headers={'Idempotency-Key':str(uuid.uuid4())})
    assert started.status_code==200,started.text
    assert (await client_a_admin.post('/api/property-source-admission-reviews',headers=key,json=body)).status_code==201
    denied=await client_a_admin.post('/api/property-source-admission-reviews',headers={'Idempotency-Key':str(uuid.uuid4())},json=other_body)
    assert denied.status_code==409,denied.text


async def test_concurrent_same_key_review_creates_one_proof_and_audit(client_a_admin,seed_data):
    import asyncio
    ids=await legacy_source(client_a_admin,seed_data)
    body=await pending_body(client_a_admin,ids)
    key={'Idempotency-Key':str(uuid.uuid4())}
    responses=await asyncio.wait_for(asyncio.gather(*[client_a_admin.post('/api/property-source-admission-reviews',headers=key,json=body) for _ in range(2)]),5)
    assert [r.status_code for r in responses]==[201,201]
    assert responses[0].json()==responses[1].json()
    different=await client_a_admin.post('/api/property-source-admission-reviews',headers={'Idempotency-Key':str(uuid.uuid4())},json=body)
    assert different.status_code==409
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        assert await db.scalar(text('SELECT count(*) FROM property_source_admission_review WHERE document_id=:document'),ids)==1
        assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:document AND action='property_document.admission_reviewed'"),ids)==1


async def test_receipt_digest_and_current_viewer_revocation(client_a_admin,client_a_clerk,client_b_admin,seed_data):
    import json
    ids=await legacy_source(client_a_admin,seed_data)
    body=await pending_body(client_a_admin,ids)
    response=await client_a_admin.post('/api/property-source-admission-reviews',headers={'Idempotency-Key':str(uuid.uuid4())},json=body)
    assert response.status_code==201,response.text
    url='/api/property-source-admission-reviews/'+response.json()['id']
    assert (await client_a_clerk.get(url)).json()==response.json()
    assert (await client_b_admin.get(url)).status_code==404
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        assert await db.scalar(text("SELECT request_digest=encode(sha256(convert_to(CAST(:body AS jsonb)::text,'UTF8')),'hex') "
            'FROM property_source_admission_review WHERE document_id=:document'),{**ids,'body':json.dumps(body)})
    assert (await client_a_admin.post(f"/api/property-data-domains/{ids['domain']}/grants/{ids['reader_grant']}/revoke")).status_code==204
    assert (await client_a_clerk.get(url)).status_code==404
    assert not await access(seed_data,'property_document',ids['document'],actor=seed_data['user_a_clerk_id'])


@pytest.mark.parametrize('changed',['reviewer','grant','revision','hash'])
async def test_direct_sql_cannot_forge_review_context(client_a_admin,seed_data,changed):
    from sqlalchemy.exc import DBAPIError
    ids=await legacy_source(client_a_admin,seed_data)
    body=await pending_body(client_a_admin,ids)
    params={**ids,**body,'org':seed_data['org_a_id'],'reviewer':seed_data['user_a_admin_id'],
            'grant':uuid.UUID(ids['review_grant']),'key':uuid.uuid4()}
    params['document_id']=uuid.UUID(body['document_id']);params['import_id']=uuid.UUID(body['import_id'])
    if changed=='reviewer': params['reviewer']=seed_data['user_a_clerk_id']
    if changed=='grant': params['grant']=uuid.uuid4()
    if changed=='revision': params['draft_revision']+=1
    if changed=='hash': params['content_sha256']='0'*64
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        with pytest.raises(DBAPIError,match='current authority|context is stale'):
            async with db.begin_nested():
                await db.execute(text('INSERT INTO property_source_admission_review '
                    '(organization_id,import_id,document_id,property_id,domain_id,grant_id,reviewed_by,draft_revision,content_sha256,reason,request_key) '
                    'VALUES (:org,:import_id,:document_id,:property,:domain,:grant,:reviewer,:draft_revision,:content_sha256,:reason,:key)'),params)
        assert await db.scalar(text('SELECT count(*) FROM property_source_admission_review WHERE document_id=:document'),ids)==0
        assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:document AND action='property_document.admission_reviewed'"),ids)==0


async def test_review_and_audit_rollback_together(client_a_admin,seed_data):
    from app.core.tenant import AuthContext
    from app.schemas.property_source_admission_review import SourceAdmissionReviewCreate
    from app.services.property_source_admission_review_service import PropertySourceAdmissionReviewService
    ids=await legacy_source(client_a_admin,seed_data)
    body=SourceAdmissionReviewCreate(**await pending_body(client_a_admin,ids))
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        auth=AuthContext(user_id=seed_data['user_a_admin_id'],organization_id=seed_data['org_a_id'],role='OWNER_ADMIN')
        nested=await db.begin_nested()
        await PropertySourceAdmissionReviewService(db,auth).review(body,uuid.uuid4())
        assert await db.scalar(text('SELECT count(*) FROM property_source_admission_review WHERE document_id=:document'),ids)==1
        await nested.rollback()
        assert await db.scalar(text('SELECT count(*) FROM property_source_admission_review WHERE document_id=:document'),ids)==0
        assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:document AND action='property_document.admission_reviewed'"),ids)==0


async def test_database_retains_review_and_prevents_wrong_destination_or_second_link(client_a_admin,seed_data):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids=await legacy_source(client_a_admin,seed_data)
    body=await pending_body(client_a_admin,ids)
    assert (await client_a_admin.post('/api/property-source-admission-reviews',headers={'Idempotency-Key':str(uuid.uuid4())},json=body)).status_code==201
    other=await setup(client_a_admin,seed_data,authorized=False)
    migration=importlib.import_module('migrations.versions.0052_property_source_admission_review')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as connection,Operations.context(MigrationContext.configure(connection)):
            connection.execute(text("SELECT set_config('app.current_org_id',:org,true)"),{'org':str(seed_data['org_a_id'])})
            assert connection.scalar(text('SELECT property_source_binding_proven(:org,:document,:property)'),{**ids,'org':seed_data['org_a_id']})
            assert not connection.scalar(text("SELECT has_function_privilege('app_rw','property_source_binding_proven(uuid,uuid,uuid)','EXECUTE')"))
            connection.execute(text("SELECT set_config('app.current_org_id','',true)"))
            assert connection.scalar(text('SELECT property_source_binding_proven(:org,:document,:property)'),{**ids,'org':seed_data['org_a_id']}) is False
            for statement,pattern in [
                ('UPDATE property_source_admission_review SET reason=reason WHERE document_id=:document','review is immutable'),
                ('DELETE FROM property_source_admission_review WHERE document_id=:document','review is immutable'),
                ('UPDATE property_document SET property_id=:wrong WHERE id=:document','differs from its reviewed property'),
                ("INSERT INTO declaration_import_document(id,organization_id,import_id,document_id,sequence_no,document_role) VALUES(gen_random_uuid(),:org,:other,:document,2,'ANNEX')",'cannot enter another import')]:
                with pytest.raises(DBAPIError,match=pattern):
                    with connection.begin_nested(): connection.execute(text(statement),{**ids,'org':seed_data['org_a_id'],'wrong':other['property'],'other':other['job']})
            with pytest.raises(DBAPIError,match='Retain populated'):
                with connection.begin_nested(): migration.downgrade()
            nested=connection.begin_nested()
            try:
                connection.execute(text('TRUNCATE property_source_admission_review'))
                migration.downgrade()
                assert connection.scalar(text("SELECT to_regclass('property_source_admission_review')")) is None
                migration.upgrade()
            finally: nested.rollback()
            assert connection.scalar(text('SELECT count(*) FROM property_source_admission_review WHERE document_id=:document'),ids)==1
    finally: engine.dispose()


@pytest.mark.parametrize('table',['declaration_import','declaration_import_document','property_document','property_data_domain','property_data_grant'])
async def test_busy_review_dependency_refuses_migration_immediately(seed_data,table):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0052_property_source_admission_review')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        async with get_tenant_session(seed_data['org_a_id']) as reader:
            await reader.execute(text(f'SELECT id FROM {table} LIMIT 1'))
            with pytest.raises(DBAPIError) as caught:
                with engine.begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout='1s'"))
                    with Operations.context(MigrationContext.configure(connection)): migration.upgrade()
            assert caught.value.orig.pgcode=='55P03'
            assert 'could not obtain lock' in str(caught.value.orig)
    finally: engine.dispose()


async def test_pending_page_rechecks_work_started_after_initial_query(client_a_admin,seed_data,monkeypatch):
    from app.services.property_source_admission_review_service import PropertySourceAdmissionReviewService
    ids=await legacy_source(client_a_admin,seed_data)
    original=PropertySourceAdmissionReviewService._context
    async def start_between_queries(self,document_id,lock=False):
        context=await original(self,document_id,lock)
        if str(document_id)==str(ids['document']):
            await self.session.execute(text("UPDATE declaration_import SET status='QUEUED' WHERE id=:job"),ids)
        return context
    monkeypatch.setattr(PropertySourceAdmissionReviewService,'_context',start_between_queries)
    response=await client_a_admin.get('/api/property-source-admission-reviews/pending/page')
    assert response.status_code==200,response.text
    assert str(ids['document']) not in {row['document_id'] for row in response.json()['items']}


async def test_second_link_waits_for_review_then_rechecks_proof(client_a_admin,seed_data):
    import asyncio
    from sqlalchemy.exc import DBAPIError
    from app.core.tenant import AuthContext
    from app.schemas.property_source_admission_review import SourceAdmissionReviewCreate
    from app.services.property_source_admission_review_service import PropertySourceAdmissionReviewService
    ids=await legacy_source(client_a_admin,seed_data)
    other=await setup(client_a_admin,seed_data,authorized=False)
    body=SourceAdmissionReviewCreate(**await pending_body(client_a_admin,ids))
    started=asyncio.Event()
    async def competing_link():
        async with get_tenant_session(seed_data['org_a_id']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
            started.set()
            with pytest.raises(DBAPIError,match='cannot enter another import'):
                async with db.begin_nested():
                    await db.execute(text("INSERT INTO declaration_import_document(id,organization_id,import_id,document_id,sequence_no,document_role) "
                        "VALUES(gen_random_uuid(),:org,:other,:document,2,'ANNEX')"),{**ids,'org':seed_data['org_a_id'],'other':other['job']})
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        auth=AuthContext(user_id=seed_data['user_a_admin_id'],organization_id=seed_data['org_a_id'],role='OWNER_ADMIN')
        await PropertySourceAdmissionReviewService(db,auth).review(body,uuid.uuid4())
        task=asyncio.create_task(competing_link())
        await asyncio.wait_for(started.wait(),2)
        await asyncio.sleep(.05)
        assert not task.done(), 'Second link must wait for the document row held by review'
    await asyncio.wait_for(task,3)
