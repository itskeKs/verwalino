"""Derived pair evidence joins the shared workflow without resolving it."""
import uuid
import pytest
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client
from tests.test_duplicate_comparisons import reviewed,command,comparisons
from tests.test_invoice_reviews import command as review_command

pytestmark=pytest.mark.asyncio


async def test_duplicate_case_discovery_comparison_and_reopened_attention(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        *_,a=await reviewed(api,ids)
        assert (await api.get('/api/exceptions')).json()['items']==[]
        _,child,_,_,p,b=await reviewed(api,ids)
        cases=(await api.get('/api/exceptions')).json()['items'];assert len(cases)==1
        case=cases[0];assert case['cause']=='duplicate_candidate' and case['duplicate_dependency_count']==1 and case['resolution_state']=='unresolved'
        url='/api/exceptions/'+case['id'];deps=(await api.get(url+'/duplicate-dependencies')).json()['items'];assert len(deps)==1
        assert {deps[0]['anchor_review_id'],deps[0]['reference_review_id']}=={a['id'],b['id']}
        body=command(b);reply=await api.post(comparisons(a),**body);assert reply.status_code==201,reply.text
        events=(await api.get(url+'/events')).json()['items'];assert [e['action'] for e in events]==['duplicate_comparison_recorded','duplicate_evidence_added']
        assert events[0]['duplicate_comparison_id']==reply.json()['id'] and events[0]['actor_id'] is None
        assert (await api.post(comparisons(a),**body)).json()==reply.json()
        assert len((await api.get(url+'/events')).json()['items'])==2
        current=(await api.get(url)).json()
        snooze=await api.post(url+'/events',json={'action':'snooze','reason':'Waiting for supplier evidence','snoozed_until':'2099-01-01T12:00:00Z'},headers={'Idempotency-Key':str(uuid.uuid4()),'If-Match':'"'+current['revision_id']+'"'})
        assert snooze.status_code==201,snooze.text
        revision=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**review_command(p,previous=b['id'],reason='Review confirmed again'))
        assert revision.status_code==201,revision.text
        current=(await api.get(url)).json();assert current['workflow_state']=='open' and current['duplicate_dependency_count']==2 and current['resolution_state']=='unresolved'


async def test_duplicate_exception_overlapping_cases_idempotent_discovery_and_reverse_observations(migrator_session,scope_seed,store):
    from sqlalchemy import text
    from tests.test_scoped_account_statements import mutate
    from tests.test_scope_payment_reconciliation import read_value
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        reviews=[]
        for _ in range(3):
            *_,review=await reviewed(api,ids);reviews.append(review)
        cases=(await api.get('/api/exceptions')).json()['items']
        assert len(cases)==2 and sorted(c['duplicate_dependency_count'] for c in cases)==[1,2]
        assert await read_value('SELECT count(*) FROM duplicate_exception_dependency WHERE organization_id=:org',ids)==3
        # Repeated opposite-direction discovery is idempotent, and does not reopen attention.
        for r in reviews+list(reversed(reviews)):
            await mutate(text('SELECT record_duplicate_exception_dependencies(:org,:review)'),{**ids,'review':uuid.UUID(r['id'])})
        assert await read_value('SELECT count(*) FROM exception_case_event WHERE organization_id=:org',ids)==3
        a,b=reviews[:2];first=await api.post(comparisons(a),**command(b));assert first.status_code==201,first.text
        reverse=await api.post(comparisons(b),**command(a,previous=first.json()['id']));assert reverse.status_code==201,reverse.text
        assert await read_value("SELECT count(*) FROM exception_case_event WHERE organization_id=:org AND action='duplicate_comparison_recorded'",ids)==2
        for r in (first.json(),reverse.json()):
            await mutate(text('SELECT record_duplicate_comparison_event(:org,:comparison,false)'),{**ids,'comparison':uuid.UUID(r['id'])})
        assert await read_value('SELECT count(*) FROM exception_case_event WHERE organization_id=:org',ids)==5


async def test_duplicate_exception_all_source_access_and_prepare_before_replay(migrator_session,scope_seed,store,client_b_admin):
    from sqlalchemy import text
    from tests.test_scoped_account_statements import mutate
    from tests.test_assignment_proposals import prepared,command as proposal_command
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        *_,a=await reviewed(api,ids)
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:other_scope,:clerk,ARRAY['read','prepare'],:actor,'Source custody scope')"),ids)
        ref,child,partition,extraction=await prepared(api,{**ids,'scope':ids['other_scope']},scoped=True)
        p=await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**proposal_command(partition,extraction,ids['scope']))
        assert p.status_code==201,p.text
        b=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**review_command(p.json()));assert b.status_code==201,b.text
        case=next(c for c in (await api.get('/api/exceptions')).json()['items'] if c['cause']=='duplicate_candidate');url='/api/exceptions/'+case['id']
        body={'json':{'action':'claim','reason':'Reviewing both sources','snoozed_until':None},'headers':{'Idempotency-Key':str(uuid.uuid4()),'If-Match':'"'+case['revision_id']+'"'}}
        first=await api.post(url+'/events',**body);assert first.status_code==201,first.text
        for denied in (admin,client_b_admin):
            assert (await denied.get(url)).status_code==404
            assert (await denied.get(url+'/duplicate-dependencies')).status_code==404
        # Keep reference read but revoke prepare on its original intake scope only.
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:other_scope AND revoked_at IS NULL'),ids)
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:other_scope,:clerk,ARRAY['read'],:actor,'Read-only source')"),ids)
        assert (await api.get(url)).json()['can_work'] is False
        assert (await api.post(url+'/events',**body)).status_code==404
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:other_scope AND revoked_at IS NULL'),ids)
        assert (await api.get(url)).status_code==404
        assert (await api.get(url+'/events')).status_code==404
        assert (await api.get(url+'/duplicate-dependencies')).status_code==404
        assert case['id'] not in str((await api.get('/api/exceptions')).json())


async def test_duplicate_exception_no_resolution_on_corrected_nonmatching_review(migrator_session,scope_seed,store):
    from tests.test_duplicate_comparisons import source_reviews
    from tests.test_invoice_extraction_worker import pdf_bytes
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        data=pdf_bytes();a,=await source_reviews(api,ids,data,1,None)
        b,=await source_reviews(api,ids,data+b'\n% other bytes\n',1,None)
        case=(await api.get('/api/exceptions')).json()['items'][0];url='/api/exceptions/'+case['id']
        corrected=await api.post('/api/logical-documents/'+b['logical_document_id']+'/invoice-reviews',**review_command({'id':b['assignment_proposal_id']},previous=b['id'],document_number='Different invoice'))
        assert corrected.status_code==201,corrected.text
        retained=(await api.get(url)).json();assert retained['duplicate_dependency_count']==1 and retained['resolution_state']=='unresolved'
        assert (await api.get('/api/invoice-reviews/'+a['id']+'/duplicate-candidates')).json()['items']==[]
        assert len((await api.get(url+'/duplicate-dependencies')).json()['items'])==1


async def test_duplicate_exception_private_role_restrictions_and_observation_rollback(migrator_session,scope_seed,store):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_worker_session_factory,get_review_session_factory,set_tenant_context
    from tests.test_scoped_account_statements import mutate
    from tests.test_scope_payment_reconciliation import read_value
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        *_,a=await reviewed(api,ids);*_,b=await reviewed(api,ids)
        for factory in (get_worker_session_factory(),get_review_session_factory()):
            with pytest.raises(DBAPIError,match='permission denied'):
                async with factory() as db,db.begin():
                    await set_tenant_context(db,ids['org'])
                    await db.execute(text('SELECT record_duplicate_exception_dependencies(:org,:review)'),{**ids,'review':uuid.UUID(a['id'])})
        with pytest.raises(DBAPIError,match='permission denied'):
            await read_value('SELECT record_duplicate_exception_dependencies(:org,CAST(:review AS uuid))',{**ids,'review':a['id']},actor_key='clerk')
        original=await read_value("SELECT pg_get_functiondef('record_duplicate_comparison_event(uuid,uuid,boolean)'::regprocedure)",ids)
        try:
            await mutate(text("CREATE OR REPLACE FUNCTION record_duplicate_comparison_event(org uuid,target uuid,required boolean) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$ BEGIN RAISE EXCEPTION 'Injected observation failure' USING ERRCODE='23514'; END $$"),ids)
            assert (await api.post(comparisons(a),**command(b))).status_code==422
            assert await read_value('SELECT count(*) FROM duplicate_comparison WHERE organization_id=:org',ids)==0
            assert await read_value('SELECT count(*) FROM exception_case_event WHERE organization_id=:org',ids)==1
        finally:await mutate(text(original),ids)
        assert (await api.post(comparisons(a),**command(b))).status_code==201


async def test_duplicate_exception_backfill_constraints_coverage_and_downgrade(migrator_session,scope_seed,store):
    import importlib
    from sqlalchemy import create_engine,text
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_access_coverage import capture
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        *_,a=await reviewed(api,ids);before=await capture(admin)
        *_,b=await reviewed(api,ids)
        assert (await capture(admin))['manifest_digest']!=before['manifest_digest']
        result=await api.post(comparisons(a),**command(b));assert result.status_code==201,result.text
        comparison=result.json()
        case=(await api.get('/api/exceptions')).json()['items'][0]
        dep=(await api.get('/api/exceptions/'+case['id']+'/duplicate-dependencies')).json()['items'][0]
        assert dep['latest_exact_comparison']['id']==comparison['id']
    migration=importlib.import_module('migrations.versions.0107_duplicate_exceptions');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain duplicate exception'),db.begin_nested():migration.downgrade()
            with pytest.raises(DBAPIError,match='immutable|Immutable'),db.begin_nested():
                db.execute(text("UPDATE duplicate_exception_dependency SET evidence='{}'::jsonb WHERE id=:dep"),{'dep':uuid.UUID(dep['id'])})
            for field in ('anchor_review_id','reference_review_id','case_id','scope_id','anchor_extraction_id'):
                with pytest.raises(DBAPIError,match='provenance|foreign key'),db.begin_nested():
                    db.execute(text('''INSERT INTO duplicate_exception_dependency SELECT (jsonb_populate_record(NULL::duplicate_exception_dependency,to_jsonb(d)||
                      jsonb_build_object('id',gen_random_uuid(),CAST(:field AS text),gen_random_uuid()))).* FROM duplicate_exception_dependency d WHERE d.id=:dep'''),
                      {'dep':uuid.UUID(dep['id']),'field':field})
            with pytest.raises(DBAPIError,match='provenance|foreign key'),db.begin_nested():
                db.execute(text('''INSERT INTO exception_case_event SELECT (jsonb_populate_record(NULL::exception_case_event,to_jsonb(e)||
                  jsonb_build_object('id',gen_random_uuid(),'previous_event_id',e.id,'revision_number',e.revision_number+1,'duplicate_comparison_id',gen_random_uuid()))).*
                  FROM exception_case_event e WHERE e.duplicate_comparison_id=:comparison'''),{'comparison':uuid.UUID(comparison['id'])})
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE allocation_treatment_review CASCADE'))
                db.execute(text('TRUNCATE allocation_integrity_attestation CASCADE'))
                db.execute(text('TRUNCATE exception_completion CASCADE'))
                db.execute(text('TRUNCATE financial_computation_attestation CASCADE'))
                db.execute(text('TRUNCATE allocation_correction_context,allocation_correction_completion'))
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
                db.execute(text('TRUNCATE exception_case CASCADE'))
                migration.downgrade()
                oid=db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))
                migration.upgrade()
                assert db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))==oid
                assert db.scalar(text('SELECT count(*) FROM duplicate_exception_dependency WHERE organization_id=:org'),ids)==1
                assert db.scalar(text('SELECT count(*) FROM exception_case_event WHERE organization_id=:org'),ids)==2
                for r in (a,b):db.execute(text('SELECT record_duplicate_exception_dependencies(:org,:r)'),{**ids,'r':uuid.UUID(r['id'])})
                db.execute(text('SELECT record_duplicate_comparison_event(:org,:comparison,false)'),{**ids,'comparison':uuid.UUID(comparison['id'])})
                assert db.scalar(text('SELECT count(*) FROM exception_case_event WHERE organization_id=:org'),ids)==2
            finally:point.rollback()
    finally:engine.dispose()


async def test_duplicate_exception_simultaneous_reviews_and_review_transaction_rollback(migrator_session,scope_seed,store):
    import asyncio,json
    from sqlalchemy import text
    from app.core.db import get_tenant_session
    from tests.test_scope_payment_reconciliation import read_value
    from tests.test_invoice_reviews import proposal
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        *_,a=await reviewed(api,ids)
        _,child,_,_,p=await proposal(api,ids)
        body=review_command(p);fields={k:v for k,v in body['json'].items() if k not in {'assignment_proposal_id','reason','fields_reviewed'}}
        with pytest.raises(RuntimeError,match='simulated crash'):
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                await db.execute(text("SELECT create_invoice_review(:org,:logical,:proposal,NULL,:request,CAST(:fields AS jsonb),'Exact pair reviewed',true)"),
                  {**ids,'logical':uuid.UUID(child['id']),'proposal':uuid.UUID(p['id']),'request':uuid.uuid4(),'fields':json.dumps(fields)})
                raise RuntimeError('simulated crash')
        assert await read_value('SELECT count(*) FROM exception_case WHERE organization_id=:org',ids)==0
        b_reply=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**body);assert b_reply.status_code==201,b_reply.text
        b=b_reply.json()
        replies=await asyncio.gather(
          api.post('/api/logical-documents/'+a['logical_document_id']+'/invoice-reviews',**review_command({'id':a['assignment_proposal_id']},previous=a['id'])),
          api.post('/api/logical-documents/'+b['logical_document_id']+'/invoice-reviews',**review_command(p,previous=b['id'])))
        assert [r.status_code for r in replies]==[201,201]
        # Two serialized transitions: original pair, first successor pair, final successor pair.
        case=(await api.get('/api/exceptions')).json()['items'][0]
        events=(await api.get('/api/exceptions/'+case['id']+'/events')).json()['items']
        assert case['duplicate_dependency_count']==3 and [e['revision_number'] for e in events]==[3,2,1]
        assert events[0]['previous_event_id']==events[1]['id'] and events[1]['previous_event_id']==events[2]['id']


async def test_duplicate_exception_hidden_added_dependency_keeps_visible_pair_command_private(migrator_session,scope_seed,store,monkeypatch):
    """Privileged retained evidence arriving after revocation must not widen the pair command's audience."""
    from sqlalchemy import text
    from tests.test_scoped_account_statements import mutate
    from tests.test_scope_payment_reconciliation import read_value
    from tests.test_assignment_proposals import prepared,command as proposal_command
    from tests.test_invoice_extraction_worker import pdf_bytes
    data=pdf_bytes();serial=iter(range(3))
    monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:data+f'\n% unique receipt {next(serial)}\n'.encode())
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        sources=[]
        for number in range(3):
            response=await admin.post('/api/properties',json={'name':f'Private source {number}','management_type':'WEG'})
            assert response.status_code==201,response.text
            response=await admin.post('/api/accounting-scopes',json={'property_id':response.json()['id'],'scope_type':'WEG','label':f'Private intake {number}','valid_from':'2025-01-01','whole_property':True,'units':[]},headers={'Idempotency-Key':str(uuid.uuid4())})
            assert response.status_code==201,response.text
            hint=response.json()['id']
            await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:hint,:clerk,ARRAY['read','prepare'],:actor,'Independent source custody')"),{**ids,'hint':uuid.UUID(hint)})
            ref,child,partition,extraction=await prepared(api,{**ids,'scope':uuid.UUID(hint)},scoped=True)
            response=await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**proposal_command(partition,extraction,ids['scope']))
            assert response.status_code==201,response.text
            sources.append((child,response.json(),hint))
        # Known logical ordering ensures A/B and A/C share the same anchor case.
        sources.sort(key=lambda row:row[0]['id']);reviews=[]
        for number,(child,p,_) in enumerate(sources):
            response=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**review_command(p,document_number='R-2026-42' if number<2 else 'Unrelated initially'))
            assert response.status_code==201,response.text
            reviews.append(response.json())
        a,b,c=reviews;case=next(x for x in (await api.get('/api/exceptions')).json()['items'] if x['cause']=='duplicate_candidate');url='/api/exceptions/'+case['id']
        snooze=await api.post(url+'/events',json={'action':'snooze','reason':'Waiting for evidence','snoozed_until':'2099-01-01T12:00:00Z'},headers={'Idempotency-Key':str(uuid.uuid4()),'If-Match':'"'+case['revision_id']+'"'})
        assert snooze.status_code==201,snooze.text
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:hint AND revoked_at IS NULL'),{**ids,'hint':uuid.UUID(sources[2][2])})
        assert (await api.get(url)).status_code==200
        # Model a privileged retained successor, with exact existing provenance; no runtime bypass is added.
        await mutate(text('''INSERT INTO invoice_review_revision SELECT (jsonb_populate_record(NULL::invoice_review_revision,to_jsonb(r)||
          jsonb_build_object('id',gen_random_uuid(),'previous_revision_id',r.id,'revision_number',2,'request_key',gen_random_uuid(),'document_number','R-2026-42'))).*
          FROM invoice_review_revision r WHERE r.id=:review'''),{**ids,'review':uuid.UUID(c['id'])})
        assert (await api.get(url)).status_code==404
        for suffix in ('/events','/dependencies','/duplicate-dependencies'):assert (await api.get(url+suffix)).status_code==404
        assert case['id'] not in str((await api.get('/api/exceptions')).json())
        reply=await api.post(comparisons(a),**command(b));assert reply.status_code==201,reply.text
        assert case['id'] not in reply.text and c['id'] not in reply.text and sources[2][0]['id'] not in reply.text
        assert await read_value("SELECT count(*) FROM exception_case_event WHERE organization_id=:org AND case_id=:case AND action='duplicate_comparison_recorded'",{**ids,'case':uuid.UUID(case['id'])})==1
        assert await read_value("SELECT workflow_state FROM exception_case_event WHERE organization_id=:org AND case_id=:case ORDER BY revision_number DESC LIMIT 1",{**ids,'case':uuid.UUID(case['id'])})=='open'
