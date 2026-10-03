"""Logical page references retain the original's custody and verified bounds."""
import uuid
import pytest
from sqlalchemy import text
from app.core.db import get_worker_session_factory
from app.core.intake_inspection import result
from app.workers.workflow import process_once
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_invoice_extraction_worker import pdf_bytes
from tests.test_intake_batches import receive

pytestmark=pytest.mark.asyncio


async def clean_reference(api,ids,*,scoped=False):
    data=pdf_bytes(3)
    _,receipt=await receive(api,data,**({'scope_hint_id':str(ids['scope'])} if scoped else {}))
    assert await process_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=3))
    ref=(await api.get('/api/document-refs/page',params={'source_id':receipt['original_id']})).json()['items'][0]
    return ref,receipt


async def test_logical_pages_are_private_bounded_immutable_and_idempotent(migrator_session,scope_seed,store,client_b_admin):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        ref,receipt=await clean_reference(api,ids)
        body={'document_ref_id':ref['id'],'page_start':2,'page_end':3,'reason':'Possible invoice and attachment, completeness remains unreviewed'}
        key=str(uuid.uuid4());headers={'Idempotency-Key':key}
        created=await api.post('/api/logical-documents',json=body,headers=headers)
        assert created.status_code==201,created.text
        child=created.json();assert child['review_state']=='unreviewed'
        assert child['content_sha256']==ref['content_sha256']
        assert child['page_start']==2 and child['page_end']==3 and child['verified_page_count']==3
        assert 'storage_uri' not in child
        assert (await api.post('/api/logical-documents',json=body,headers=headers)).json()==child
        assert (await api.post('/api/logical-documents',json={**body,'page_end':2},headers=headers)).status_code==409
        assert (await api.get('/api/logical-documents/'+child['id'])).json()==child
        for denied in (admin,client_b_admin):
            assert (await denied.get('/api/logical-documents/'+child['id'])).status_code==404
            assert (await denied.get('/api/logical-documents',params={'document_ref_id':ref['id']})).status_code==404
            assert (await denied.post('/api/logical-documents',json=body,headers={'Idempotency-Key':str(uuid.uuid4())})).status_code==404
        listed=await api.get('/api/logical-documents',params={'document_ref_id':ref['id']})
        assert listed.json()['items']==[child]
        for start,end in [(0,2),(3,2),(1,4),(None,2),(None,None)]:
            bad=await api.post('/api/logical-documents',json={**body,'page_start':start,'page_end':end},headers={'Idempotency-Key':str(uuid.uuid4())})
            assert bad.status_code==422,bad.text
    params={**ids,'child':uuid.UUID(child['id']),'original':uuid.UUID(receipt['original_id'])}
    assert await read_value('SELECT count(*) FROM intake_original WHERE id=:original',params)==1
    assert await read_value("SELECT count(*) FROM outbox_event WHERE effect_key=:effect",{**ids,'effect':'logical:'+child['id']})==1
    with pytest.raises(Exception,match='immutable|Immutable'):
        await mutate(text("UPDATE logical_document SET page_end=2 WHERE id=:child"),params)


async def test_logical_pages_require_live_clean_custody_and_prepare_rights(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,receipt=await receive(api,pdf_bytes(3),scope_hint_id=str(ids['scope']))
        ref=await read_value('SELECT id FROM document_ref WHERE intake_original_id=:original',{**ids,'original':uuid.UUID(receipt['original_id'])})
        body={'document_ref_id':str(ref),'page_start':1,'page_end':3,'reason':'Whole source for review'}
        async def create():
            return await api.post('/api/logical-documents',json=body,headers={'Idempotency-Key':str(uuid.uuid4())})
        assert (await create()).status_code==404
        assert await process_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=3))
        created=await create();assert created.status_code==201,created.text
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND revoked_at IS NULL'),ids)
        assert (await api.get('/api/logical-documents/'+created.json()['id'])).status_code==404
        assert (await create()).status_code==404


async def test_xml_has_no_invented_pages_and_pausing_retains_readable_children(migrator_session,scope_seed,store):
    import hashlib
    from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
    ids=await fixture(migrator_session,scope_seed);data=b'<invoice><total>12.00</total></invoice>'
    async with synthetic_enforcement(scope_seed),client(ids) as api:
        body={'items':[{'client_item_key':'xml','filename':'invoice.xml','mime_type':'application/xml',
                       'content_sha256':hashlib.sha256(data).hexdigest(),'content_byte_size':len(data)}]}
        batch=(await api.post('/api/intake-batches',json=body,headers={'Idempotency-Key':str(uuid.uuid4())})).json()
        receipt=(await api.post('/api/intake-batches/'+batch['id']+'/items',files={'file':('invoice.xml',data,'application/xml')},data={'client_item_key':'xml'})).json()
        assert await process_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime))
        ref=(await api.get('/api/document-refs/page',params={'source_id':receipt['original_id']})).json()['items'][0]
        body={'document_ref_id':ref['id'],'reason':'Preserve the entire structured original'};headers={'Idempotency-Key':str(uuid.uuid4())}
        bad=await api.post('/api/logical-documents',json={**body,'page_start':1,'page_end':1},headers=headers)
        assert bad.status_code==422,bad.text
        created=await api.post('/api/logical-documents',json=body,headers=headers)
        assert created.status_code==201,created.text
        assert created.json()['verified_page_count'] is None and created.json()['page_start'] is None
        await change_mode(scope_seed,'PAUSED')
        assert (await api.get('/api/logical-documents/'+created.json()['id'])).status_code==200
        assert (await api.post('/api/logical-documents',json=body,headers=headers)).status_code==404


async def test_logical_creation_is_atomic_and_raw_writes_cannot_forge_inspection(migrator_session,scope_seed,store):
    import asyncio
    from sqlalchemy.exc import DBAPIError
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,_=await clean_reference(api,ids)
        body={'document_ref_id':ref['id'],'page_start':1,'page_end':3,'reason':'Whole source'};headers={'Idempotency-Key':str(uuid.uuid4())}
        replies=await asyncio.gather(*[api.post('/api/logical-documents',json=body,headers=headers) for _ in range(2)])
        assert all(reply.status_code==201 for reply in replies),[r.text for r in replies]
        assert replies[0].json()==replies[1].json()
        child=replies[0].json();params={**ids,'child':uuid.UUID(child['id'])}
        with pytest.raises(DBAPIError,match='permission denied'):
            await read_value('DELETE FROM logical_document WHERE id=:child RETURNING id',params,actor_key='clerk')
        assert await read_value('SELECT count(*) FROM logical_document WHERE id=:child',params,actor_key='actor')==0
        statement='''INSERT INTO logical_document(organization_id,document_ref_id,verified_attempt_id,content_sha256,
          page_start,page_end,verified_page_count,created_by,request_key,request_digest,reason)
          SELECT organization_id,document_ref_id,verified_attempt_id,:digest,1,2,:pages,created_by,gen_random_uuid(),'forged','Forged source'
          FROM logical_document WHERE id=:child'''
        for digest,pages in [('0'*64,3),(child['content_sha256'],4)]:
            with pytest.raises(DBAPIError,match='exact clean inspection|verified page bounds'):
                await mutate(text(statement),{**params,'digest':digest,'pages':pages})


async def test_logical_migration_preserves_evidence_and_function_identity(migrator_session,scope_seed,store):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,_=await clean_reference(api,ids)
        created=await api.post('/api/logical-documents',json={'document_ref_id':ref['id'],'page_start':1,'page_end':3,'reason':'Retained source'},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert created.status_code==201,created.text
    migration=importlib.import_module('migrations.versions.0095_logical_documents');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain logical'),db.begin_nested():
                migration.downgrade()
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE logical_document,field_evidence,extraction_revision,outbox_event CASCADE'))
                oid=db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))
                db.execute(text('TRUNCATE source_partition_revision CASCADE'))
                db.execute(text('TRUNCATE exception_case CASCADE'))
                db.execute(text('TRUNCATE exception_case CASCADE'))
                db.execute(text('TRUNCATE allocation_treatment_review CASCADE'))
                db.execute(text('TRUNCATE allocation_integrity_attestation CASCADE'))
                db.execute(text('TRUNCATE exception_completion CASCADE'))
                db.execute(text('TRUNCATE financial_computation_attestation CASCADE'))
                db.execute(text('TRUNCATE allocation_correction_context,allocation_correction_completion'))
                importlib.import_module('migrations.versions.0122_allocation_source_replacements').downgrade()
                importlib.import_module('migrations.versions.0121_visual_allocation_sources').downgrade()
                db.execute(text('TRUNCATE visual_disclosure_attestation CASCADE'))
                db.execute(text('TRUNCATE allocation_source_claim CASCADE'))
                db.execute(text('TRUNCATE visual_extract_attestation CASCADE'))
                importlib.import_module('migrations.versions.0120_allocation_source_aliases').downgrade()
                importlib.import_module('migrations.versions.0119_allocation_source_claims').downgrade()
                importlib.import_module('migrations.versions.0118_visual_disclosures').downgrade()
                importlib.import_module('migrations.versions.0117_visual_extract_artifacts').downgrade()
                importlib.import_module('migrations.versions.0116_correction_followup_completions').downgrade()
                importlib.import_module('migrations.versions.0115_financial_computation_evidence').downgrade()
                importlib.import_module('migrations.versions.0114_allocation_corrections').downgrade()
                importlib.import_module('migrations.versions.0113_allocation_correction_preview').downgrade()
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
                importlib.import_module('migrations.versions.0100_source_partitions').downgrade()
                importlib.import_module('migrations.versions.0099_intake_extraction_status').downgrade()
                importlib.import_module('migrations.versions.0098_intake_extraction_jobs').downgrade()
                importlib.import_module('migrations.versions.0097_internal_outbox_delivery').downgrade()
                importlib.import_module('migrations.versions.0096_extraction_evidence').downgrade()
                migration.downgrade();migration.upgrade()
                assert db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))==oid
            finally:
                point.rollback()
        assert await read_value('SELECT count(*) FROM logical_document WHERE id=:child',{**ids,'child':uuid.UUID(created.json()['id'])})==1
    finally:
        engine.dispose()


async def test_new_child_invalidates_prior_access_coverage_without_new_original_audience(migrator_session,scope_seed,store):
    from tests.test_access_coverage import capture
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        ref,receipt=await clean_reference(api,ids)
        before=await capture(admin)
        created=await api.post('/api/logical-documents',json={'document_ref_id':ref['id'],'page_start':1,'page_end':2,'reason':'Candidate child'},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert created.status_code==201,created.text
        after=await capture(admin)
        assert after['manifest_digest']!=before['manifest_digest']
        response=await admin.post(f"/api/access-preparation/snapshots/{before['id']}/review",json={'manifest_digest':before['manifest_digest'],'acknowledged':True},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert response.status_code==409,response.text
        for report in (before,after):
            items=(await admin.get(f"/api/access-preparation/snapshots/{report['id']}/items",params={'limit':200})).json()['items']
            source=next(item for item in items if item['resource_id']==receipt['original_id'])
            assert source['references']['strict_audience']==[{'user_id':str(ids['clerk']),'capabilities':['read','export']}]
