"""Pair evidence is audience-scoped and never a financial clearance."""
import asyncio
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_invoice_reviews import proposal,command as review_command

pytestmark=pytest.mark.asyncio

async def reviewed(api,ids,**changes):
    ref,child,partition,extraction,p=await proposal(api,ids)
    reply=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**review_command(p,**changes))
    assert reply.status_code==201,reply.text
    return ref,child,partition,extraction,p,reply.json()


def command(reference,previous=None,key=None,**changes):
    return {'json':{'reference_review_id':reference['id'],'verdict':'same_document','reason':'Compared both exact sources','comparison_reviewed':True,**changes},
      'headers':{'Idempotency-Key':key or str(uuid.uuid4()),'If-Match':'"'+(previous or 'none')+'"'}}


def candidates(review):return '/api/invoice-reviews/'+review['id']+'/duplicate-candidates'
def comparisons(review):return '/api/invoice-reviews/'+review['id']+'/duplicate-comparisons'


async def test_duplicate_matching_pair_chain_replay_and_no_money(migrator_session,scope_seed,store,monkeypatch):
    from tests.test_invoice_extraction_worker import pdf_bytes
    data=pdf_bytes();monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:data)
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        *_,a=await reviewed(api,ids)
        *_,b=await reviewed(api,ids)
        reply=await api.get(candidates(a));assert reply.status_code==200,reply.text
        item=reply.json()['items'][0]
        assert item['can_compare'] is True
        assert item['review_id']==b['id'] and item['evidence']['match_reasons']==['same_bytes_and_range','same_reviewed_identity']
        assert item['evidence']['field_disagreements']==[] and item['latest_comparison'] is None
        body=command(b);reply=await api.post(comparisons(a),**body);assert reply.status_code==201,reply.text
        first=reply.json();assert first['financial_state']=='evidence_only'
        assert (await api.post(comparisons(a),**body)).json()==first
        assert (await api.post(comparisons(a),**command(b,previous=first['id'],verdict='distinct_documents'))).status_code==422
        reverse=command(a,previous=first['id'])
        replies=await asyncio.gather(*[api.post(comparisons(b),**{**reverse,'headers':{**reverse['headers'],'Idempotency-Key':str(uuid.uuid4())}}) for _ in range(2)])
        assert sorted(r.status_code for r in replies)==[201,409]
        second=next(r.json() for r in replies if r.status_code==201)
        assert second['subject_review_id']==b['id'] and second['reference_review_id']==a['id'] and second['previous_revision_id']==first['id']
        assert (await api.get(candidates(a))).json()['items'][0]['latest_comparison']['id']==second['id']
        assert (await api.get('/api/duplicate-comparisons/'+first['id'])).json()==first
        assert (await api.post(comparisons(a),**command(a))).status_code==422
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==0
    with pytest.raises(Exception,match='immutable|Immutable'):
        await mutate(text("UPDATE duplicate_comparison SET verdict='distinct_documents' WHERE organization_id=:org"),ids)


async def test_duplicate_changed_bytes_identity_paging_and_stale_reviews(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,child,_,_,p,a=await reviewed(api,ids)
        *_,b=await reviewed(api,ids,seller_name='  SYNTHETIC   Utility GmbH  ')
        *_,c=await reviewed(api,ids)
        page=(await api.get(candidates(a),params={'limit':1})).json();assert len(page['items'])==1 and page['next_cursor']
        next_page=(await api.get(candidates(a),params={'limit':1,'cursor':page['next_cursor']})).json()
        assert {page['items'][0]['review_id'],next_page['items'][0]['review_id']}=={b['id'],c['id']}
        assert (await api.get(candidates(b),params={'cursor':page['next_cursor']})).status_code==422
        body=command(b);first=await api.post(comparisons(a),**body);assert first.status_code==201,first.text
        new_review=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**review_command(p,previous=a['id'],document_number='Changed'))
        assert new_review.status_code==201,new_review.text
        assert (await api.post(comparisons(a),**command(b,previous=first.json()['id']))).status_code==409
        assert (await api.get(candidates(a))).status_code==409
        assert (await api.post(comparisons(a),**body)).json()==first.json()


async def test_duplicate_audience_intersection_and_kind_conflict(migrator_session,scope_seed,store,monkeypatch,client_b_admin):
    from tests.test_invoice_extraction_worker import pdf_bytes
    data=pdf_bytes();monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:data)
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        *_,a=await reviewed(api,ids)
        *_,b=await reviewed(api,ids,document_kind='credit_note',gross_amount='-119.00')
        item=(await api.get(candidates(a))).json()['items'][0]
        assert item['evidence']['kind_conflict'] is True
        assert 'document_kind' in item['evidence']['field_disagreements']
        assert (await api.post(comparisons(a),**command(b))).status_code==422
        for denied in (admin,client_b_admin):
            assert (await denied.get(candidates(a))).status_code==404
            assert (await denied.post(comparisons(a),**command(b))).status_code==404


async def source_reviews(api,ids,data,pages,ranges,**changes):
    from app.core.db import get_worker_session_factory
    from app.core.intake_inspection import result
    from app.core.intake_extraction import extract_logical
    from app.workers.workflow import process_once as inspect_once
    from app.workers.outbox import process_once as deliver_once
    from app.workers.intake_extraction import process_once
    from tests.test_intake_batches import receive
    from tests.test_source_partitions import command as partition_command
    from tests.test_assignment_proposals import command as proposal_command
    if pages is None:
        from tests.test_intake_batches import manifest
        body=manifest(data);body['items'][0].update(filename='invoice.xml',mime_type='application/xml')
        reply=await api.post('/api/intake-batches',json=body,headers={'Idempotency-Key':str(uuid.uuid4())})
        assert reply.status_code==201,reply.text
        reply=await api.post('/api/intake-batches/'+reply.json()['id']+'/items',files={'file':('invoice.xml',data,'application/xml')},data={'client_item_key':'document-1'})
        assert reply.status_code==202,reply.text
        receipt=reply.json()
    else:
        _,receipt=await receive(api,data)
    assert await inspect_once(get_worker_session_factory(),ids['org'],inspector=lambda content,mime:result(content,'clean',mime=mime,pages=pages))
    while await deliver_once(get_worker_session_factory(),ids['org']):pass
    ref=(await api.get('/api/document-refs/page',params={'source_id':receipt['original_id']})).json()['items'][0]
    if ranges is None:
        children=(await api.get('/api/logical-documents',params={'document_ref_id':ref['id']})).json()['items']
    else:
        children=[]
        for low,high in ranges:
            reply=await api.post('/api/logical-documents',json={'document_ref_id':ref['id'],'page_start':low,'page_end':high,'reason':'Separate exact pages'},headers={'Idempotency-Key':str(uuid.uuid4())})
            assert reply.status_code==201,reply.text
            children.append(reply.json())
        while await deliver_once(get_worker_session_factory(),ids['org']):pass
    while await process_once(get_worker_session_factory(),ids['org'],processor=extract_logical):pass
    reply=await api.post('/api/document-refs/'+ref['id']+'/partition',**partition_command(children))
    assert reply.status_code==201,reply.text
    partition=reply.json();reviews=[]
    for child in children:
        extraction=(await api.get('/api/document-refs/'+ref['id']+'/extractions',params={'logical_document_id':child['id']})).json()['items'][0]
        reply=await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**proposal_command(partition,extraction,ids['scope']))
        assert reply.status_code==201,reply.text
        reply=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**review_command(reply.json(),**changes))
        assert reply.status_code==201,reply.text
        reviews.append(reply.json())
    return reviews


async def test_duplicate_extents_xml_and_changed_bytes_dispositions(migrator_session,scope_seed,store):
    from tests.test_invoice_extraction_worker import pdf_bytes
    from tests.test_m5_einvoice import UBL
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        a,b=await source_reviews(api,ids,pdf_bytes(2),2,[(1,1),(2,2)])
        item=(await api.get(candidates(a))).json()['items'][0]
        assert item['evidence']['match_reasons']==['same_reviewed_identity']
        assert (await api.post(comparisons(a),**command(b,verdict='distinct_documents'))).status_code==201
        # Different exact bytes but the same reviewed identity are only a suggestion.
        data=pdf_bytes();c,=await source_reviews(api,ids,data,1,None)
        d,=await source_reviews(api,ids,data+b'\n%changed bytes\n',1,None)
        item=next(x for x in (await api.get(candidates(c))).json()['items'] if x['review_id']==d['id'])
        assert item['evidence']['match_reasons']==['same_reviewed_identity']
        assert (await api.post(comparisons(c),**command(d,verdict='replacement_requested'))).status_code==201
        e,=await source_reviews(api,ids,UBL,None,None)
        f,=await source_reviews(api,ids,UBL,None,None,vat_amount='19.00')
        item=next(x for x in (await api.get(candidates(e))).json()['items'] if x['review_id']==f['id'])
        assert item['evidence']['subject']['page_start'] is None
        assert 'same_bytes_and_range' in item['evidence']['match_reasons'] and item['evidence']['field_disagreements']==['vat_amount']
        same=await api.post(comparisons(e),**command(f));assert same.status_code==201,same.text
        assert same.json()['evidence']['field_disagreements']==['vat_amount']
        assert (await api.post(comparisons(e),**command(f,previous=same.json()['id'],verdict='replacement_requested'))).status_code==422


async def test_duplicate_hidden_heads_and_current_authority_replay(migrator_session,scope_seed,store):
    from tests.test_assignment_proposals import command as proposal_command
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        *_,a=await reviewed(api,ids)
        _,child,partition,extraction,p,b=await reviewed(api,ids)
        body=command(b);first=await api.post(comparisons(a),**body);assert first.status_code==201,first.text
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:other_scope,:clerk,ARRAY['read','prepare'],:actor,'Synthetic next context'),(:org,:scope,:actor,ARRAY['read','prepare'],:actor,'Synthetic separate uploader')"),ids)
        *_,hidden=await reviewed(admin,ids)
        assert hidden['id'] not in str((await api.get(candidates(a))).json())
        next_p=await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**proposal_command(partition,extraction,ids['other_scope'],previous=p['id']))
        assert next_p.status_code==201,next_p.text
        successor=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**review_command(next_p.json(),previous=b['id']))
        assert successor.status_code==201,successor.text
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:other_scope AND revoked_at IS NULL'),ids)
        # Head selected before scope/access filtering: old visible b never returns as candidate.
        assert (await api.get(candidates(a))).json()['items']==[]
        assert (await api.get(candidates(b))).status_code==404
        assert (await api.post(comparisons(a),**command(b,previous=first.json()['id']))).status_code==404
        assert (await api.post(comparisons(a),**body)).json()==first.json()
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND revoked_at IS NULL'),ids)
        assert (await api.post(comparisons(a),**body)).status_code==404
        assert (await api.get('/api/duplicate-comparisons/'+first.json()['id'])).status_code==404


async def test_duplicate_identity_preserves_number_case_and_explicit_values(migrator_session,scope_seed,store):
    from tests.test_invoice_extraction_worker import pdf_bytes
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        data=pdf_bytes();a,=await source_reviews(api,ids,data,1,None)
        b,=await source_reviews(api,ids,data+b'\n% other\n',1,None,document_number='r-2026-42')
        assert (await api.get(candidates(a))).json()['items']==[]
        assert (await api.post(comparisons(a),**command(b))).status_code==422
        for values in ({'comparison_reviewed':False},{'comparison_reviewed':1},{'reason':' '},{'verdict':'clear'},{'match_reasons':['same_bytes_and_range']}):
            assert (await api.post(comparisons(a),**command(b,**values))).status_code==422
        assert (await api.get(candidates(a),params={'limit':51})).status_code==422
        assert (await api.get(candidates(a),params={'cursor':'tampered'})).status_code==422


async def test_duplicate_roles_pause_rollback_and_stale_partition(migrator_session,scope_seed,store):
    from app.core.db import get_tenant_session,get_worker_session_factory,get_review_session_factory,set_tenant_context
    from sqlalchemy.exc import DBAPIError
    from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
    from tests.test_source_partitions import command as partition_command
    ids=await fixture(migrator_session,scope_seed)
    async with synthetic_enforcement(scope_seed),client(ids) as api:
        *_,a=await reviewed(api,ids)
        ref,child,partition,_,_,b=await reviewed(api,ids)
        params={**ids,'a':uuid.UUID(a['id']),'b':uuid.UUID(b['id']),'request':uuid.uuid4()}
        sql="SELECT create_duplicate_comparison(:org,:a,:b,NULL,:request,'same_document','Explicit pair review',true)"
        for factory in (get_worker_session_factory(),get_review_session_factory()):
            with pytest.raises(DBAPIError,match='Interactive runtime required|permission denied'):
                async with factory() as db,db.begin():
                    await set_tenant_context(db,ids['org'])
                    await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                    await db.execute(text(sql),params)
        with pytest.raises(RuntimeError,match='simulated crash'):
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                assert await db.scalar(text(sql),params)
                raise RuntimeError('simulated crash')
        assert await read_value('SELECT count(*) FROM duplicate_comparison WHERE organization_id=:org',ids)==0
        body=command(b);reply=await api.post(comparisons(a),**body);assert reply.status_code==201,reply.text
        await change_mode(scope_seed,'PAUSED')
        assert (await api.get(candidates(a))).json()['items'][0]['can_compare'] is False
        assert (await api.post(comparisons(a),**body)).status_code==404
        await change_mode(scope_seed,'ENFORCED')
        reply=await api.post('/api/document-refs/'+ref['id']+'/partition',**partition_command([child],previous=partition['id']))
        assert reply.status_code==201,reply.text
        assert (await api.post(comparisons(a),**command(b))).status_code==409
        assert (await api.post(comparisons(a),**body)).status_code==201
        with pytest.raises(DBAPIError,match='permission denied'):
            await read_value('DELETE FROM duplicate_comparison WHERE organization_id=:org RETURNING id',ids,actor_key='clerk')


async def test_duplicate_coverage_provenance_and_guarded_downgrade(migrator_session,scope_seed,store):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_access_coverage import capture
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        *_,a=await reviewed(api,ids);*_,b=await reviewed(api,ids);before=await capture(admin)
        result=await api.post(comparisons(a),**command(b));assert result.status_code==201,result.text
        first=result.json();assert (await capture(admin))['manifest_digest']!=before['manifest_digest']
        history=await api.get(comparisons(b),params={'reference_review_id':a['id'],'limit':1})
        assert history.json()['items']==[first]
    migration=importlib.import_module('migrations.versions.0106_duplicate_comparisons');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain duplicate comparison'),db.begin_nested():migration.downgrade()
            columns=db.scalars(text("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='duplicate_comparison' AND is_generated='NEVER' ORDER BY ordinal_position")).all()
            fields=','.join(columns);select=','.join('r.'+c for c in columns)
            for field in ('subject_review_id','reference_review_id','scope_id','subject_document_ref_id','reference_logical_id'):
                with pytest.raises(DBAPIError,match='foreign key'),db.begin_nested():
                    db.execute(text(f'''INSERT INTO duplicate_comparison({fields}) SELECT {select} FROM duplicate_comparison a,
                      LATERAL jsonb_populate_record(NULL::duplicate_comparison,to_jsonb(a)||jsonb_build_object('id',gen_random_uuid(),'request_key',gen_random_uuid(),
                        'previous_revision_id',a.id,'revision_number',2,CAST(:field AS text),gen_random_uuid())) r WHERE a.id=:first'''),
                      {'field':field,'first':uuid.UUID(first['id'])})
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE exception_case CASCADE'))
                db.execute(text('TRUNCATE allocation_treatment_review CASCADE'))
                db.execute(text('TRUNCATE allocation_integrity_attestation CASCADE'))
                importlib.import_module('migrations.versions.0109_allocation_adoptions').downgrade()
                importlib.import_module('migrations.versions.0108_allocation_treatments').downgrade()
                importlib.import_module('migrations.versions.0107_duplicate_exceptions').downgrade()
                db.execute(text('TRUNCATE duplicate_comparison'))
                oid=db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))
                migration.downgrade();migration.upgrade()
                assert db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))==oid
            finally:point.rollback()
    finally:engine.dispose()
