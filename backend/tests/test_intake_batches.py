"""Durable private intake: receiving bytes never means inspection succeeded."""
import hashlib
import uuid
import pytest
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client
from tests.test_scope_payment_reconciliation import read_value
from tests.test_invoice_extraction_worker import pdf_bytes

pytestmark=pytest.mark.asyncio


def manifest(data,key='document-1'):
    return {'items':[{'client_item_key':key,'filename':'invoice.pdf','mime_type':'application/pdf',
                     'content_sha256':hashlib.sha256(data).hexdigest(),'content_byte_size':len(data)}]}


async def create(api,data,**kwargs):
    body={**manifest(data),**kwargs}
    response=await api.post('/api/intake-batches',json=body,headers={'Idempotency-Key':str(uuid.uuid4())})
    assert response.status_code==201,response.text
    return response.json()


async def test_manifest_is_bounded_private_and_idempotent(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    data=pdf_bytes()
    async with client(ids) as api,client(ids,'actor') as admin:
        key=str(uuid.uuid4());body=manifest(data)
        first=await api.post('/api/intake-batches',json=body,headers={'Idempotency-Key':key})
        assert first.status_code==201,first.text
        batch=first.json()
        assert (await api.post('/api/intake-batches',json=body,headers={'Idempotency-Key':key})).json()==batch
        changed=manifest(data,'different')
        assert (await api.post('/api/intake-batches',json=changed,headers={'Idempotency-Key':key})).status_code==409
        state=(await api.get('/api/intake-batches/'+batch['id'])).json()
        assert state['counts']=={'waiting':1,'received':0,'processing':0,'review':0,'confirmed':0,'failed':0}
        assert state['items'][0]['client_item_key']=='document-1'
        assert state['items'][0]['status']=='waiting'
        assert (await admin.get('/api/intake-batches/'+batch['id'])).status_code==404
        for items in [[],body['items']*2,body['items']*101]:
            response=await api.post('/api/intake-batches',json={'items':items},headers={'Idempotency-Key':str(uuid.uuid4())})
            assert response.status_code==422,response.text


async def test_received_original_job_and_outbox_commit_once(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    data=pdf_bytes()
    async with client(ids) as api:
        batch=await create(api,data)
        endpoint='/api/intake-batches/'+batch['id']+'/items'
        response=await api.post(endpoint,files={'file':('invoice.pdf',data,'application/pdf')},data={'client_item_key':'document-1'})
        assert response.status_code==202,response.text
        receipt=response.json()
        assert receipt['status']=='received'
        replay=await api.post(endpoint,files={'file':('invoice.pdf',data,'application/pdf')},data={'client_item_key':'document-1'})
        assert replay.status_code==202 and replay.json()==receipt
        assert (await api.post(endpoint,files={'file':('invoice.pdf',b'wrong bytes','application/pdf')},data={'client_item_key':'document-1'})).status_code==422
        assert (await api.get('/api/document-refs/page',params={'source_id':receipt['original_id']})).json()['items']==[]
        state=(await api.get('/api/intake-batches/'+batch['id'])).json()
        assert state['counts']['received']==1 and state['counts']['review']==0
        assert state['items'][0]['original_id']==receipt['original_id']
        ids['original']=uuid.UUID(receipt['original_id'])
    assert await read_value('SELECT count(*) FROM workflow_job WHERE original_id=:original',ids)==1
    assert await read_value("SELECT count(*) FROM outbox_event WHERE resource_id=:original AND event_type='intake.received'",ids)==1


async def test_worker_inspection_releases_only_exact_private_reference(migrator_session,scope_seed,store):
    from app.core.db import get_worker_session_factory
    from app.core.intake_inspection import result
    from app.workers.workflow import process_once
    ids=await fixture(migrator_session,scope_seed)
    data=pdf_bytes(2)
    async with client(ids) as api,client(ids,'actor') as admin:
        batch=await create(api,data)
        receipt=(await api.post('/api/intake-batches/'+batch['id']+'/items',files={'file':('invoice.pdf',data,'application/pdf')},data={'client_item_key':'document-1'})).json()
        def clean(content,mime):
            assert content==data
            return result(content,'clean',mime=mime,pages=2)
        assert await process_once(get_worker_session_factory(),ids['org'],inspector=clean)
        rows=(await api.get('/api/document-refs/page',params={'source_id':receipt['original_id']})).json()['items']
        assert len(rows)==1 and rows[0]['source_kind']=='intake_original'
        assert (await admin.get('/api/document-refs/'+rows[0]['id'])).status_code==404
        assert (await api.get('/api/intake-batches/'+batch['id'])).json()['counts']['review']==1
        assert not await process_once(get_worker_session_factory(),ids['org'],inspector=clean)


async def receive(api,data,**kwargs):
    batch=await create(api,data,**kwargs)
    response=await api.post('/api/intake-batches/'+batch['id']+'/items',files={'file':('invoice.pdf',data,'application/pdf')},data={'client_item_key':'document-1'})
    assert response.status_code==202,response.text
    return batch,response.json()


async def work_sql(ids,sql,**params):
    from sqlalchemy import text
    from app.core.db import get_worker_session_factory,set_tenant_context
    async with get_worker_session_factory()() as db,db.begin():
        await set_tenant_context(db,ids['org'])
        return await db.scalar(text(sql),{'org':ids['org'],**params})


async def test_blocked_inspection_retries_same_original_with_bounded_replay(migrator_session,scope_seed,store):
    from app.core.db import get_worker_session_factory
    from app.core.intake_inspection import result
    from app.workers.workflow import process_once
    ids=await fixture(migrator_session,scope_seed);data=pdf_bytes()
    async with client(ids) as api:
        batch,receipt=await receive(api,data)
        for attempt in range(1,4):
            assert await process_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'blocked','scanner_unavailable',mime=mime))
            state=(await api.get('/api/intake-batches/'+batch['id'])).json()
            assert state['counts']['failed']==1 and state['items'][0]['error_code']=='scanner_unavailable'
            body={'job_id':receipt['job_id'],'expected_fence':state['items'][0]['fence'],'reason':'Scanner availability repaired'}
            key=str(uuid.uuid4())
            retried=await api.post('/api/intake-batches/retry',json=body,headers={'Idempotency-Key':key})
            assert retried.status_code==(202 if attempt<3 else 409),retried.text
            if attempt<3:
                assert (await api.post('/api/intake-batches/retry',json=body,headers={'Idempotency-Key':key})).json()==retried.json()
        assert not await process_once(get_worker_session_factory(),ids['org'])
        assert (await api.get('/api/document-refs/page',params={'source_id':receipt['original_id']})).json()['items']==[]
    assert await read_value('SELECT count(*) FROM job_attempt WHERE job_id=:job',{**ids,'job':uuid.UUID(receipt['job_id'])})==3
    assert await read_value("SELECT count(*) FROM outbox_event WHERE resource_id=:original AND event_type='intake.received'",{**ids,'original':uuid.UUID(receipt['original_id'])})==1


async def test_stale_worker_cannot_complete_reclaimed_generation(migrator_session,scope_seed,store):
    import json
    from sqlalchemy import text
    from app.core.intake_inspection import result
    from tests.test_scoped_account_statements import mutate
    ids=await fixture(migrator_session,scope_seed);data=pdf_bytes()
    async with client(ids) as api:
        batch,receipt=await receive(api,data)
        first=await work_sql(ids,'SELECT claim_intake_inspection(:org)')
        await mutate(text("UPDATE workflow_job SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:job"),{'job':uuid.UUID(first['job_id'])})
        second=await work_sql(ids,'SELECT claim_intake_inspection(:org)')
        assert first['fence']!=second['fence']
        inspection=json.dumps(result(data,'clean',mime='application/pdf',pages=1))
        for claim,expected in [(first,False),(second,True),(second,False)]:
            assert await work_sql(ids,'SELECT complete_intake_inspection(:org,:job,:token,CAST(:inspection AS jsonb))',job=uuid.UUID(claim['job_id']),token=uuid.UUID(claim['fence']),inspection=inspection) is expected
        state=(await api.get('/api/intake-batches/'+batch['id'])).json()
        assert state['counts']['review']==1


async def test_scope_revocation_cancels_work_and_hides_status(migrator_session,scope_seed,store):
    from sqlalchemy import text
    from app.core.db import get_worker_session_factory
    from app.workers.workflow import process_once
    from tests.test_scoped_account_statements import mutate
    ids=await fixture(migrator_session,scope_seed);data=pdf_bytes()
    async with client(ids) as api,client(ids,'actor') as admin:
        assert (await admin.post('/api/intake-batches',json={**manifest(data),'scope_hint_id':str(ids['scope'])},headers={'Idempotency-Key':str(uuid.uuid4())})).status_code==404
        batch,receipt=await receive(api,data,scope_hint_id=str(ids['scope']))
        claim=await work_sql(ids,'SELECT claim_intake_inspection(:org)')
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND revoked_at IS NULL'),ids)
        assert await work_sql(ids,'SELECT intake_worker_context(:org,:job,:token)',job=uuid.UUID(claim['job_id']),token=uuid.UUID(claim['fence'])) is None
        assert (await api.get('/api/intake-batches/'+batch['id'])).status_code==404
        retry=await api.post('/api/intake-batches/retry',json={'job_id':receipt['job_id'],'expected_fence':claim['fence'],'reason':'Denied retry'},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert retry.status_code==404
    assert await read_value('SELECT status FROM workflow_job WHERE id=:job',{**ids,'job':uuid.UUID(receipt['job_id'])})=='cancelled'
    assert not await process_once(get_worker_session_factory(),ids['org'],inspector=lambda *args:pytest.fail('Revoked source reached scanner'))


async def test_raw_runtime_cannot_forge_custody_or_clean_inspection(migrator_session,scope_seed,store):
    import json
    from sqlalchemy.exc import DBAPIError
    ids=await fixture(migrator_session,scope_seed);data=pdf_bytes()
    async with client(ids) as api:
        batch=await create(api,data)
        state=(await api.get('/api/intake-batches/'+batch['id'])).json();item=state['items'][0]
    digest=hashlib.sha256(data).hexdigest();original=uuid.uuid4()
    payload={'id':str(original),'organization_id':str(ids['org']),'item_id':item['id'],'uploaded_by':str(ids['clerk']),
        'content_sha256':digest,'content_byte_size':len(data),'mime_type':'application/pdf',
        'storage_uri':f"{ids['org']}/{digest[:2]}/{digest}.bin",'storage_backend':'LOCAL_FS','storage_version':None}
    params={**ids,'batch':uuid.UUID(batch['id']),'original':original,'payload':json.dumps(payload)}
    for statement,match in [
        ("SELECT receive_intake_original(:org,:batch,'document-1',CAST(:payload AS jsonb))",'Trusted fresh'),
        ('SELECT admit_intake_upload(:org,:original,CAST(:payload AS jsonb))','permission denied'),
        ('SELECT claim_intake_inspection(:org)','permission denied'),
        ("UPDATE workflow_job SET status='clean' RETURNING id",'permission denied'),
        ('DELETE FROM intake_original RETURNING id','permission denied')]:
        with pytest.raises(DBAPIError,match=match):
            await read_value(statement,params,actor_key='clerk')


async def test_intake_invalidates_coverage_and_blocks_active_work(migrator_session,scope_seed,store):
    from tests.test_source_security_lifecycle import accepted,status,transition,transition_body
    from tests.test_access_disposition import reviewed,assessment
    from app.core.db import get_worker_session_factory
    from app.core.intake_inspection import result
    from app.workers.workflow import process_once
    await migrator_session.commit()
    ids={'org':scope_seed['org_a_id'],'actor':scope_seed['user_a_admin_id'],'clerk':scope_seed['user_a_clerk_id']}
    async with client(ids,'actor') as admin,client(ids) as api:
        decision=await accepted(admin)
        batch,receipt=await receive(api,pdf_bytes())
        denied=await transition(admin,transition_body(await status(admin),'ENFORCED',decision))
        assert denied.status_code==409,denied.text
        current=await reviewed(admin)
        active=await assessment(admin,current)
        assert active['active_work_count']==1 and not active['acceptance_available']
        assert await process_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'blocked','scanner_unavailable',mime=mime))
        decision=await accepted(admin)
        assert (await transition(admin,transition_body(await status(admin),'ENFORCED',decision))).status_code==201
        assert (await api.get('/api/document-refs/page',params={'source_id':receipt['original_id']})).json()['items']==[]


async def test_parallel_upload_and_signed_continuation_preserve_manifest(migrator_session,scope_seed,store,client_b_admin):
    import asyncio
    ids=await fixture(migrator_session,scope_seed);data=pdf_bytes()
    body={'items':[manifest(data,str(i))['items'][0] for i in range(100)]}
    async with client(ids) as api:
        batch=(await api.post('/api/intake-batches',json=body,headers={'Idempotency-Key':str(uuid.uuid4())})).json()
        first=(await api.get('/api/intake-batches/'+batch['id'],params={'limit':50})).json()
        second=(await api.get('/api/intake-batches/'+batch['id'],params={'limit':50,'cursor':first['next_cursor']})).json()
        assert len(first['items'])==len(second['items'])==50 and second['next_cursor'] is None
        assert len({item['id'] for item in first['items']+second['items']})==100
        assert (await client_b_admin.get('/api/intake-batches/'+batch['id'],params={'cursor':first['next_cursor']})).status_code==422
        endpoint='/api/intake-batches/'+batch['id']+'/items'
        responses=await asyncio.gather(*[api.post(endpoint,files={'file':('invoice.pdf',data,'application/pdf')},data={'client_item_key':'0'}) for _ in range(2)])
        assert all(r.status_code==202 for r in responses),[r.text for r in responses]
        assert responses[0].json()==responses[1].json()
        assert (await api.get('/api/intake-batches/'+batch['id'])).json()['counts']['received']==1


async def test_pause_and_role_changes_stop_new_work(migrator_session,scope_seed,store,monkeypatch):
    from sqlalchemy import text
    from tests.test_scoped_account_statements import mutate
    from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
    ids=await fixture(migrator_session,scope_seed);data=pdf_bytes()
    async with synthetic_enforcement(scope_seed),client(ids) as api:
        batch,receipt=await receive(api,data)
        await change_mode(scope_seed,'PAUSED')
        monkeypatch.setattr(store,'put',lambda *a,**kw:pytest.fail('Paused intake reached storage'))
        assert (await api.post('/api/intake-batches',json=manifest(data),headers={'Idempotency-Key':str(uuid.uuid4())})).status_code==404
        assert await work_sql(ids,'SELECT claim_intake_inspection(:org)') is None
        assert (await api.get('/api/intake-batches/'+batch['id'])).json()['items'][0]['error_code']=='initiator_permission_changed'
        await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"),ids)
        assert (await api.post('/api/intake-batches',json=manifest(data),headers={'Idempotency-Key':str(uuid.uuid4())})).status_code==403
        await mutate(text('UPDATE "user" SET is_active=false WHERE id=:clerk'),ids)
        assert (await api.get('/api/intake-batches/'+batch['id'])).status_code==401


async def test_intake_migration_refuses_data_and_preserves_function_identity(migrator_session,scope_seed,store):
    import importlib
    from sqlalchemy import create_engine,text
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids=await fixture(migrator_session,scope_seed);data=pdf_bytes()
    async with client(ids) as api:
        batch,receipt=await receive(api,data)
    migration=importlib.import_module('migrations.versions.0093_intake_quarantine')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain intake'),db.begin_nested():
                migration.downgrade()
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE intake_batch,intake_upload_admission CASCADE'))
                oid=db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))
                importlib.import_module('migrations.versions.0094_intake_inbox_status').downgrade()
                migration.downgrade()
                assert db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))==oid
                migration.upgrade()
                assert db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))==oid
            finally:
                point.rollback()
        assert await read_value('SELECT count(*) FROM intake_original WHERE id=:original',{**ids,'original':uuid.UUID(receipt['original_id'])})==1
    finally:
        engine.dispose()


async def test_original_export_requires_clean_status_and_live_export_scope(migrator_session,scope_seed,store,monkeypatch,client_b_admin):
    from sqlalchemy import text
    from app.core.db import get_worker_session_factory
    from app.core.intake_inspection import result
    from app.workers.workflow import process_once
    from tests.test_scoped_account_statements import mutate
    ids=await fixture(migrator_session,scope_seed);data=pdf_bytes()
    async with client(ids) as api,client(ids,'actor') as admin:
        batch,receipt=await receive(api,data,scope_hint_id=str(ids['scope']))
        path='/api/intake-batches/originals/'+receipt['original_id']+'/content'
        assert (await api.get(path)).status_code==404
        assert await process_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=1))
        assert (await api.get(path)).status_code==404
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND revoked_at IS NULL'),ids)
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:scope,:clerk,ARRAY['read','prepare','export'],:actor,'Explicit intake export')"),ids)
        response=await api.get(path)
        assert response.status_code==200 and response.content==data
        assert response.headers['content-disposition'].startswith('attachment;')
        assert (await admin.get(path)).status_code==404
        assert (await client_b_admin.get(path)).status_code==404
        monkeypatch.setattr(store,'iter_chunks',lambda *a,**kw:iter([b'changed bytes']))
        assert (await api.get(path)).status_code==409


async def test_retry_can_recover_without_restarting_completed_work(migrator_session,scope_seed,store):
    from app.core.db import get_worker_session_factory
    from app.core.intake_inspection import result
    from app.workers.workflow import process_once
    ids=await fixture(migrator_session,scope_seed);data=pdf_bytes()
    async with client(ids) as api:
        batch,receipt=await receive(api,data)
        assert await process_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'blocked','scanner_timeout',mime=mime))
        state=(await api.get('/api/intake-batches/'+batch['id'])).json()
        body={'job_id':receipt['job_id'],'expected_fence':state['items'][0]['fence'],'reason':'Retry after transient scanner timeout'}
        key=str(uuid.uuid4())
        retried=await api.post('/api/intake-batches/retry',json=body,headers={'Idempotency-Key':key})
        assert retried.status_code==202,retried.text
        assert await process_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=1))
        assert (await api.post('/api/intake-batches/retry',json=body,headers={'Idempotency-Key':key})).json()==retried.json()
        assert (await api.get('/api/intake-batches/'+batch['id'])).json()['counts']['review']==1
        assert (await api.get('/api/intake-batches/originals/'+receipt['original_id']+'/content')).content==data
        assert not await process_once(get_worker_session_factory(),ids['org'])


@pytest.mark.parametrize('scoped,exportable',[(False,True),(True,False),(True,True)])
async def test_coverage_projects_actual_intake_read_and_export(migrator_session,scope_seed,store,scoped,exportable):
    from sqlalchemy import text
    from tests.test_scoped_account_statements import mutate
    from tests.test_access_coverage import capture
    from app.core.db import get_worker_session_factory
    from app.core.intake_inspection import result
    from app.workers.workflow import process_once
    ids=await fixture(migrator_session,scope_seed);data=pdf_bytes()
    if scoped and exportable:
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND revoked_at IS NULL'),ids)
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:scope,:clerk,ARRAY['read','prepare','export'],:actor,'Reviewed intake export')"),ids)
    async with client(ids) as api,client(ids,'actor') as admin:
        batch,receipt=await receive(api,data,**({'scope_hint_id':str(ids['scope'])} if scoped else {}))
        assert await process_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=1))
        report=await capture(admin)
        entries=(await admin.get(f"/api/access-preparation/snapshots/{report['id']}/items",params={'limit':200})).json()['items']
        source=next(item for item in entries if item['resource_id']==receipt['original_id'])
        assert source['references']['strict_audience']==[{'user_id':str(ids['clerk']),'capabilities':['read','export'] if exportable else ['read']}]
        assert (await api.get('/api/intake-batches/originals/'+receipt['original_id']+'/content')).status_code==(200 if exportable else 404)


async def test_inbox_headers_and_actions_use_current_private_authority(migrator_session,scope_seed,store,client_b_admin):
    from app.core.db import get_worker_session_factory
    from app.core.intake_inspection import result
    from app.workers.workflow import process_once
    ids=await fixture(migrator_session,scope_seed);data=pdf_bytes()
    async with client(ids) as api,client(ids,'actor') as admin:
        first=await create(api,data)
        second,receipt=await receive(api,data)
        response=await api.get('/api/intake-batches',params={'limit':1})
        assert response.status_code==200,response.text
        first_page=response.json();assert first_page['items'][0]['id']==second['id']
        next_page=(await api.get('/api/intake-batches',params={'limit':1,'cursor':first_page['next_cursor']})).json()
        assert next_page['items'][0]['id']==first['id'] and next_page['next_cursor'] is None
        assert (await admin.get('/api/intake-batches')).json()['items']==[]
        assert (await client_b_admin.get('/api/intake-batches',params={'cursor':first_page['next_cursor']})).status_code==422
        waiting=(await api.get('/api/intake-batches/'+first['id'])).json()['items'][0]
        assert waiting['can_upload'] and not waiting['can_export'] and not waiting['can_retry']
        assert await process_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'blocked','scanner_unavailable',mime=mime))
        blocked=(await api.get('/api/intake-batches/'+second['id'])).json()['items'][0]
        assert blocked['can_retry'] and not blocked['can_upload'] and not blocked['can_export']
