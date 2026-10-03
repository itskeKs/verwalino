"""Reviewed visual-page partitions preserve provenance, concurrency and custody."""
import asyncio
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_logical_documents import clean_reference

pytestmark=pytest.mark.asyncio


async def candidates(api,ref):
    children=[]
    for start,end in [(1,3),(1,1),(2,3),(2,2),(3,3)]:
        response=await api.post('/api/logical-documents',json={'document_ref_id':ref['id'],'page_start':start,'page_end':end,'reason':'Candidate range'},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert response.status_code==201,response.text
        children.append(response.json())
    return children


def command(children,previous=None,key=None,reason='Reviewed all visual pages'):
    return dict(json={'logical_document_ids':[row['id'] for row in children],'reason':reason,'visual_pages_reviewed':True},
                headers={'Idempotency-Key':key or str(uuid.uuid4()),'If-Match':'"'+(previous or 'none')+'"'})


async def test_partition_is_exact_immutable_history_and_idempotent(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,_=await clean_reference(api,ids);whole,first,rest,_,_=await candidates(api,ref)
        url='/api/document-refs/'+ref['id']+'/partition'
        initial=await api.get(url);assert initial.status_code==200,initial.text
        assert initial.json()['current'] is None and initial.json()['can_review']
        body=command([rest,first]);created=await api.post(url,**body)
        assert created.status_code==201,created.text
        revision=created.json();assert revision['logical_document_ids']==[first['id'],rest['id']]
        assert revision['content_sha256']==ref['content_sha256'] and revision['verified_page_count']==3
        assert revision['revision_number']==1 and revision['previous_revision_id'] is None
        assert (await api.post(url,**body)).json()==revision
        changed=command([whole],key=body['headers']['Idempotency-Key'])
        assert (await api.post(url,**changed)).status_code==409
        assert (await api.post(url,**command([whole]))).status_code==409
        second=await api.post(url,**command([whole],previous=revision['id']));assert second.status_code==201,second.text
        assert second.json()['revision_number']==2
        assert (await api.post(url,**body)).json()==revision
        assert (await api.get(url)).json()['current']==second.json()
        assert (await api.get(url+'/'+revision['id'])).json()==revision
        assert all(row['review_state']=='unreviewed' for row in (await api.get('/api/logical-documents',params={'document_ref_id':ref['id']})).json()['items'])
    with pytest.raises(Exception,match='immutable|Immutable'):
        await mutate(text('UPDATE source_partition_revision SET reason=\'changed\' WHERE organization_id=:org'),ids)
    assert await read_value('SELECT count(*) FROM source_partition_member WHERE organization_id=:org',ids)==3
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==0


async def test_partition_rejects_gaps_overlap_duplicates_foreign_sources_and_unacknowledged(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,_=await clean_reference(api,ids);whole,first,rest,middle,last=await candidates(api,ref)
        foreign,_=await clean_reference(api,ids);others=await candidates(api,foreign)
        url='/api/document-refs/'+ref['id']+'/partition'
        for selected in ([],[first],[first,last],[whole,rest],[first,first,rest],[others[0]]):
            reply=await api.post(url,**command(selected));assert reply.status_code==422,reply.text
        body=command([whole]);body['json']['visual_pages_reviewed']=False
        assert (await api.post(url,**body)).status_code==422
        body=command([whole]);del body['headers']['If-Match']
        assert (await api.post(url,**body)).status_code==428
        body=command([whole]);body['headers']['If-Match']='*'
        assert (await api.post(url,**body)).status_code==422
        reply=await api.post(url,**command([last,first,middle]));assert reply.status_code==201,reply.text
    assert await read_value('SELECT count(*) FROM source_partition_revision WHERE organization_id=:org',ids)==1


async def test_partition_race_replay_and_access_revocation(migrator_session,scope_seed,store,client_b_admin):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        ref,_=await clean_reference(api,ids,scoped=True);whole,first,rest,_,_=await candidates(api,ref)
        url='/api/document-refs/'+ref['id']+'/partition';body=command([whole])
        replies=await asyncio.gather(api.post(url,**body),api.post(url,**body))
        assert [r.status_code for r in replies]==[201,201]
        prior=replies[0].json();assert replies[1].json()==prior
        replies=await asyncio.gather(api.post(url,**command([whole],previous=prior['id'])),api.post(url,**command([first,rest],previous=prior['id'])))
        assert sorted(r.status_code for r in replies)==[201,409]
        for denied in (admin,client_b_admin):
            assert (await denied.get(url)).status_code==404
            assert (await denied.get(url+'/'+prior['id'])).status_code==404
            assert (await denied.post(url,**body)).status_code==404
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND revoked_at IS NULL'),ids)
        assert (await api.get(url)).status_code==404
        assert (await api.post(url,**body)).status_code==404
    assert await read_value('SELECT count(*) FROM source_partition_revision WHERE organization_id=:org',ids)==2


async def test_xml_review_pause_and_raw_role_boundaries(migrator_session,scope_seed,store):
    import hashlib
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_worker_session_factory
    from app.core.intake_inspection import result
    from app.workers.workflow import process_once
    from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
    ids=await fixture(migrator_session,scope_seed);data=b'<invoice><total>12.00</total></invoice>'
    async with synthetic_enforcement(scope_seed),client(ids) as api:
        body={'items':[{'client_item_key':'xml','filename':'invoice.xml','mime_type':'application/xml',
            'content_sha256':hashlib.sha256(data).hexdigest(),'content_byte_size':len(data)}]}
        batch=(await api.post('/api/intake-batches',json=body,headers={'Idempotency-Key':str(uuid.uuid4())})).json()
        receipt=(await api.post('/api/intake-batches/'+batch['id']+'/items',files={'file':('invoice.xml',data,'application/xml')},data={'client_item_key':'xml'})).json()
        assert await process_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime))
        ref=(await api.get('/api/document-refs/page',params={'source_id':receipt['original_id']})).json()['items'][0]
        child=(await api.post('/api/logical-documents',json={'document_ref_id':ref['id'],'reason':'Entire XML'},headers={'Idempotency-Key':str(uuid.uuid4())})).json()
        url='/api/document-refs/'+ref['id']+'/partition';body=command([child])
        reply=await api.post(url,**body);assert reply.status_code==201,reply.text
        assert reply.json()['verified_page_count'] is None
        params={**ids,'revision':uuid.UUID(reply.json()['id'])}
        assert await read_value('SELECT count(*) FROM source_partition_revision WHERE id=:revision',params,actor_key='clerk')==1
        assert await read_value('SELECT count(*) FROM source_partition_revision WHERE id=:revision',params,actor_key='actor')==0
        for table in ('source_partition_revision','source_partition_member'):
            with pytest.raises(DBAPIError,match='permission denied'):
                await read_value(f'DELETE FROM {table} WHERE organization_id=:org RETURNING id',ids,actor_key='clerk')
        with pytest.raises(DBAPIError,match='permission denied'):
            await read_value('SELECT source_partition_result(:org,:revision)',params,actor_key='clerk')
        await change_mode(scope_seed,'PAUSED')
        status=await api.get(url);assert status.status_code==200 and not status.json()['can_review']
        assert (await api.post(url,**body)).status_code==404


async def test_partition_changes_coverage_and_rollback_preserves_history(migrator_session,scope_seed,store):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_access_coverage import capture
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        ref,_=await clean_reference(api,ids);whole,*_=await candidates(api,ref)
        before=await capture(admin)
        reply=await api.post('/api/document-refs/'+ref['id']+'/partition',**command([whole]));assert reply.status_code==201,reply.text
        assert (await capture(admin))['manifest_digest']!=before['manifest_digest']
        reply=await admin.post(f"/api/access-preparation/snapshots/{before['id']}/review",json={'manifest_digest':before['manifest_digest'],'acknowledged':True},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert reply.status_code==409,reply.text
    migration=importlib.import_module('migrations.versions.0100_source_partitions');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain reviewed'),db.begin_nested():migration.downgrade()
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE source_partition_revision CASCADE'))
                oid=db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))
                db.execute(text('TRUNCATE exception_case CASCADE'))
                db.execute(text('TRUNCATE exception_case CASCADE'))
                db.execute(text('TRUNCATE allocation_treatment_review CASCADE'))
                db.execute(text('TRUNCATE allocation_integrity_attestation CASCADE'))
                db.execute(text('TRUNCATE exception_completion CASCADE'))
                importlib.import_module('migrations.versions.0112_context_mappings').downgrade()
                importlib.import_module('migrations.versions.0111_context_candidate_source').downgrade()
                importlib.import_module('migrations.versions.0110_exception_completions').downgrade()
                importlib.import_module('migrations.versions.0109_allocation_adoptions').downgrade()
                importlib.import_module('migrations.versions.0108_allocation_treatments').downgrade()
                importlib.import_module('migrations.versions.0107_duplicate_exceptions').downgrade()
                db.execute(text('TRUNCATE duplicate_comparison CASCADE'))
                importlib.import_module('migrations.versions.0106_duplicate_comparisons').downgrade()
                db.execute(text('TRUNCATE allocation_proposal CASCADE'))
                importlib.import_module('migrations.versions.0105_allocation_proposals').downgrade()
                db.execute(text('TRUNCATE context_decision CASCADE'))
                importlib.import_module('migrations.versions.0104_context_decisions').downgrade()
                db.execute(text('TRUNCATE invoice_review_revision'))
                importlib.import_module('migrations.versions.0103_invoice_field_reviews').downgrade()
                importlib.import_module('migrations.versions.0102_exception_cases').downgrade()
                importlib.import_module('migrations.versions.0101_assignment_proposals').downgrade()
                migration.downgrade();migration.upgrade()
                assert db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))==oid
            finally:point.rollback()
        assert await read_value('SELECT count(*) FROM source_partition_revision WHERE organization_id=:org',ids)==1
    finally:engine.dispose()


async def test_partition_command_rollback_and_composite_source_foreign_keys(migrator_session,scope_seed,store):
    from app.core.db import get_tenant_session
    from sqlalchemy.exc import DBAPIError
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,_=await clean_reference(api,ids);whole,*_=await candidates(api,ref)
        foreign,_=await clean_reference(api,ids);other,*_=await candidates(api,foreign)
        params={**ids,'ref':uuid.UUID(ref['id']),'request':uuid.uuid4(),'child':uuid.UUID(whole['id'])}
        with pytest.raises(RuntimeError,match='simulated crash'):
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                assert await db.scalar(text("SELECT review_source_partition(:org,:ref,:request,NULL,ARRAY[CAST(:child AS uuid)],'Reviewed',true)"),params)
                raise RuntimeError('simulated crash')
        assert await read_value('SELECT count(*) FROM source_partition_revision WHERE organization_id=:org',ids)==0
        created=await api.post('/api/document-refs/'+ref['id']+'/partition',**command([whole]));assert created.status_code==201,created.text
    with pytest.raises(DBAPIError,match='foreign key'):
        await mutate(text('''INSERT INTO source_partition_member(organization_id,document_ref_id,partition_revision_id,logical_document_id)
          VALUES(:org,:ref,:revision,:other)'''),{**params,'revision':uuid.UUID(created.json()['id']),'other':uuid.UUID(other['id'])})


async def test_background_roles_cannot_review_even_with_restored_actor(migrator_session,scope_seed,store):
    from app.core.db import get_worker_session_factory,get_review_session_factory,set_tenant_context
    from sqlalchemy.exc import DBAPIError
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,_=await clean_reference(api,ids);whole,*_=await candidates(api,ref)
    for factory in (get_worker_session_factory(),get_review_session_factory()):
        with pytest.raises(DBAPIError,match='Interactive runtime required|permission denied'):
            async with factory() as db,db.begin():
                await set_tenant_context(db,ids['org'])
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                await db.execute(text("SELECT review_source_partition(:org,:ref,:request,NULL,ARRAY[CAST(:child AS uuid)],'Restored actor',true)"),
                    {**ids,'ref':uuid.UUID(ref['id']),'request':uuid.uuid4(),'child':uuid.UUID(whole['id'])})
    assert await read_value('SELECT count(*) FROM source_partition_revision WHERE organization_id=:org',ids)==0


async def test_partition_view_reports_live_original_export_separately_from_review(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,receipt=await clean_reference(api,ids,scoped=True)
        url='/api/document-refs/'+ref['id']+'/partition'
        status=(await api.get(url)).json()
        assert status['can_review'] and not status['can_export']
        assert (await api.get('/api/intake-batches/originals/'+receipt['original_id']+'/content')).status_code==404
        grant=uuid.uuid4()
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE id=:grant'),ids)
        await mutate(text("INSERT INTO scope_grant(id,organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:grant,:org,:scope,:clerk,ARRAY['read','prepare','export'],:actor,'Reviewed original access')"),{**ids,'grant':grant})
        assert (await api.get(url)).json()['can_export']
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE id=:grant'),{**ids,'grant':grant})
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:scope,:clerk,ARRAY['read','prepare'],:actor,'Restore review only')"),ids)
        status=(await api.get(url)).json()
        assert status['can_review'] and not status['can_export']
