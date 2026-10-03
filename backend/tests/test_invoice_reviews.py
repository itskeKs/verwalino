"""Human field reviews preserve exact source basis without financial acceptance."""
import asyncio
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_assignment_proposals import prepared,command as proposal_command
from tests.test_exception_cases import setup_case

pytestmark=pytest.mark.asyncio


def command(proposal,previous=None,key=None,**changes):
    body={'assignment_proposal_id':proposal['id'],'seller_name':'Synthetic Utility GmbH','document_number':'R-2026-42',
        'document_kind':'invoice','currency':'EUR','invoice_date':'2026-10-01','service_period_from':'2026-09-01','service_period_to':'2026-09-30',
        'gross_amount':'119.00','vat_amount':None,'vat_rate':None,'reason':'Compared reviewed fields with the selected original',
        'fields_reviewed':True,**changes}
    return {'json':body,'headers':{'Idempotency-Key':key or str(uuid.uuid4()),'If-Match':'"'+(previous or 'none')+'"'}}


async def proposal(api,ids):
    ref,child,partition,extraction=await prepared(api,ids)
    reply=await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**proposal_command(partition,extraction,ids['scope']))
    assert reply.status_code==201,reply.text
    return ref,child,partition,extraction,reply.json()


async def test_field_review_exact_basis_corrections_replay_and_no_money(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,child,partition,extraction,p=await proposal(api,ids);url='/api/logical-documents/'+child['id']+'/invoice-reviews'
        body=command(p);reply=await api.post(url,**body);assert reply.status_code==201,reply.text
        first=reply.json();assert first['gross_amount']=='119.00' and first['vat_amount'] is None and first['vat_rate'] is None
        assert first['assignment_proposal_id']==p['id'] and first['extraction_revision_id']==extraction['id'] and first['partition_revision_id']==partition['id']
        assert first['content_sha256']==partition['content_sha256'] and first['revision_number']==1
        assert (await api.post(url,**body)).json()==first
        correction=command(p,previous=first['id'],document_number='C-2026-42',document_kind='credit_note',gross_amount='-119.00',vat_amount='-19.00',vat_rate='19.00')
        replies=await asyncio.gather(*[api.post(url,**{**correction,'headers':{**correction['headers'],'Idempotency-Key':str(uuid.uuid4())}}) for _ in range(2)])
        assert sorted(r.status_code for r in replies)==[201,409]
        second=next(r.json() for r in replies if r.status_code==201);assert second['previous_revision_id']==first['id'] and second['gross_amount']=='-119.00'
        assert (await api.post(url,**body)).json()==first
        history=(await api.get(url,params={'limit':1})).json();assert history['reviewable_assignment_id']==p['id']
        assert history['items'][0]['id']==second['id'] and history['next_before_revision']==2
        assert (await api.get(url,params={'before_revision':2})).json()['items'][0]['id']==first['id']
        assert (await api.get('/api/invoice-reviews/'+first['id'])).json()==first
        assert (await api.post(url,**command(p,key=body['headers']['Idempotency-Key'],gross_amount='120.00'))).status_code==409
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==0
    assert await read_value('SELECT count(*) FROM invoice_document WHERE organization_id=:org',ids)==0
    with pytest.raises(Exception,match='immutable|Immutable'):
        await mutate(text("UPDATE invoice_review_revision SET gross_amount=120 WHERE organization_id=:org"),ids)


async def test_field_review_rejects_unknown_or_inconsistent_values_and_preserves_blockers(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,child,partition,extraction,p,_=await setup_case(api,ids);url='/api/logical-documents/'+child['id']+'/invoice-reviews'
        for changes in ({'gross_amount':'119.001'},{'gross_amount':119.00},{'gross_amount':'NaN'},{'gross_amount':'1000000000000.00'},
                        {'gross_amount':'0.00'},{'document_kind':'invoice','gross_amount':'-119.00'},
                        {'document_kind':'credit_note','gross_amount':'119.00'},{'currency':'USD'},
                        {'vat_amount':'120.00'},{'vat_amount':'-19.00'},{'vat_rate':'100.01'},
                        {'service_period_from':None},{'service_period_to':'2026-08-31'},
                        {'invoice_date':'infinity'},{'fields_reviewed':False},{'fields_reviewed':1},{'seller_name':' '},{'document_number':'x'*101}):
            reply=await api.post(url,**command(p,**changes));assert reply.status_code==422,(changes,reply.text)
        reply=await api.post(url,**command(p,vat_amount='0.00'));assert reply.status_code==201,reply.text
        review=reply.json();assert review['vat_amount']=='0.00' and review['vat_rate'] is None
        assert review['assignment_state']=='blocked' and review['assignment_blockers']==['scope_hint_conflict']
        case=(await api.get('/api/exceptions')).json()['items'][0];assert case['resolution_state']=='unresolved'
        # Selecting a successor proposal invalidates a new review of the old proposal, but not its historical receipt.
        next_p=await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**proposal_command(partition,extraction,ids['other_scope'],previous=p['id']))
        assert next_p.status_code==201,next_p.text
        assert (await api.post(url,**command(p,previous=review['id']))).status_code==409
        assert (await api.post(url,**command(next_p.json(),previous=review['id']))).status_code==201


async def test_field_review_authority_replay_and_foreign_source(migrator_session,scope_seed,store,client_b_admin):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        _,child,_,_,p,_=await setup_case(api,ids);url='/api/logical-documents/'+child['id']+'/invoice-reviews'
        body=command(p);reply=await api.post(url,**body);assert reply.status_code==201,reply.text
        review=reply.json()
        _,other,_,_,_=await proposal(api,ids)
        assert (await api.post('/api/logical-documents/'+other['id']+'/invoice-reviews',**command(p))).status_code in (404,422)
        for denied in (admin,client_b_admin):
            assert (await denied.get(url)).status_code==404
            assert (await denied.get('/api/invoice-reviews/'+review['id'])).status_code==404
            assert (await denied.post(url,**body)).status_code==404
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:other_scope AND revoked_at IS NULL'),ids)
        assert (await api.get(url)).json()['items']==[]
        assert (await api.post(url,**body)).status_code==404
        assert (await api.get('/api/invoice-reviews/'+review['id'])).status_code==404


async def test_field_review_stale_partition_and_new_failed_extraction(migrator_session,scope_seed,store):
    from tests.test_source_partitions import command as partition_command
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,child,partition,extraction,p=await proposal(api,ids);url='/api/logical-documents/'+child['id']+'/invoice-reviews'
        body=command(p);first=(await api.post(url,**body)).json()
        partition_reply=await api.post('/api/document-refs/'+ref['id']+'/partition',**partition_command([child],previous=partition['id']))
        assert partition_reply.status_code==201,partition_reply.text
        assert (await api.post(url,**command(p,previous=first['id']))).status_code==409
        assert (await api.post(url,**body)).json()==first
        new_proposal=await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**proposal_command(partition_reply.json(),extraction,ids['scope'],previous=p['id']))
        assert new_proposal.status_code==201,new_proposal.text
        # Synthetic retained failed successor isolates freshness from worker scheduling.
        await mutate(text('''WITH a AS (
          INSERT INTO intake_extraction_attempt(organization_id,job_id,attempt_number,fence,finished_at,result)
          SELECT organization_id,id,2,gen_random_uuid(),clock_timestamp(),jsonb_build_object('status','failed') FROM intake_extraction_job
          WHERE organization_id=:org AND logical_document_id=:logical RETURNING *)
          INSERT INTO extraction_revision(organization_id,document_ref_id,intake_attempt_id,logical_document_id,content_sha256,processor_version,status,error_code)
          SELECT a.organization_id,l.document_ref_id,a.id,l.id,l.content_sha256,'intake-local-v1','failed','synthetic_history'
          FROM a JOIN logical_document l ON l.id=:logical'''),{**ids,'logical':uuid.UUID(child['id'])})
        assert (await api.post(url,**command(new_proposal.json(),previous=first['id']))).status_code==409
        assert (await api.post(url,**body)).json()==first


async def test_field_review_roles_sql_validation_pause_and_transaction_rollback(migrator_session,scope_seed,store):
    import json
    from app.core.db import get_tenant_session,get_worker_session_factory,get_review_session_factory,set_tenant_context
    from sqlalchemy.exc import DBAPIError
    from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
    ids=await fixture(migrator_session,scope_seed)
    async with synthetic_enforcement(scope_seed),client(ids) as api:
        _,child,_,_,p,_=await setup_case(api,ids);url='/api/logical-documents/'+child['id']+'/invoice-reviews'
        body=command(p)
        fields={k:v for k,v in body['json'].items() if k not in {'assignment_proposal_id','reason','fields_reviewed'}}
        params={**ids,'logical':uuid.UUID(child['id']),'proposal':uuid.UUID(p['id']),'request':uuid.uuid4(),'fields':json.dumps(fields)}
        sql="SELECT create_invoice_review(:org,:logical,:proposal,NULL,:request,CAST(:fields AS jsonb),'Explicit human field review',true)"
        for factory in (get_worker_session_factory(),get_review_session_factory()):
            with pytest.raises(DBAPIError,match='Interactive runtime required|permission denied'):
                async with factory() as db,db.begin():
                    await set_tenant_context(db,ids['org'])
                    await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                    await db.execute(text(sql),params)
        for changes in ({'gross_amount':'119.001'},{'gross_amount':'NaN'},{'invoice_date':'today'},{'service_period_from':'01/02/2026'},{'service_period_to':'infinity'}):
            with pytest.raises(DBAPIError,match='exact cents|ISO dates'):
                async with get_tenant_session(ids['org']) as db:
                    await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                    await db.execute(text(sql),{**params,'fields':json.dumps({**fields,**changes})})
        with pytest.raises(DBAPIError,match='inaccessible'):
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('app.current_scope_id',:scope,true)"),{'actor':str(ids['clerk']),'scope':str(ids['scope'])})
                await db.execute(text(sql),params)
        with pytest.raises(RuntimeError,match='simulated crash'):
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                assert await db.scalar(text(sql),params)
                raise RuntimeError('simulated crash')
        assert await read_value('SELECT count(*) FROM invoice_review_revision WHERE organization_id=:org',ids)==0
        # ISO dates and canonical decimal strings keep the same identity across session DateStyle settings.
        receipts=[]
        for style in ('ISO, MDY','German, DMY'):
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('DateStyle',:style,true)"),{'actor':str(ids['clerk']),'style':style})
                receipts.append(await db.scalar(text(sql),params))
        assert receipts[0]==receipts[1]
        with pytest.raises(DBAPIError,match='permission denied'):
            await read_value('DELETE FROM invoice_review_revision WHERE organization_id=:org RETURNING id',ids,actor_key='clerk')
        await change_mode(scope_seed,'PAUSED')
        paused=(await api.get(url)).json();assert len(paused['items'])==1 and paused['reviewable_assignment_id'] is None
        with pytest.raises(DBAPIError,match='inaccessible'):
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                await db.execute(text(sql),params)
        await change_mode(scope_seed,'ENFORCED')
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND revoked_at IS NULL'),ids)
        assert (await api.get(url)).status_code==404
        assert (await api.post(url,**body)).status_code==404


async def test_field_review_coverage_source_constraints_and_guarded_downgrade(migrator_session,scope_seed,store):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_access_coverage import capture
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        _,child,_,_,p=await proposal(api,ids);before=await capture(admin)
        result=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**command(p));assert result.status_code==201,result.text
        review=result.json();assert (await capture(admin))['manifest_digest']!=before['manifest_digest']
        _,_,foreign_partition,foreign_extraction,_=await proposal(api,ids)
    migration=importlib.import_module('migrations.versions.0103_invoice_field_reviews');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain invoice field review'),db.begin_nested():migration.downgrade()
            for field,value in [('partition_revision_id',foreign_partition['id']),('extraction_revision_id',foreign_extraction['id']),('content_sha256','b'*64),('scope_id',str(ids['other_scope']))]:
                with pytest.raises(DBAPIError,match='foreign key'),db.begin_nested():
                    db.execute(text('''INSERT INTO invoice_review_revision SELECT (jsonb_populate_record(NULL::invoice_review_revision,
                      to_jsonb(r)||jsonb_build_object('id',gen_random_uuid(),'request_key',gen_random_uuid(),'previous_revision_id',r.id,'revision_number',2,CAST(:field AS text),CAST(:value AS text)))).*
                      FROM invoice_review_revision r WHERE r.id=:review'''),{'review':uuid.UUID(review['id']),'field':field,'value':value})
            point=db.begin_nested()
            try:
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
                db.execute(text('TRUNCATE exception_case CASCADE'))
                importlib.import_module('migrations.versions.0107_duplicate_exceptions').downgrade()
                db.execute(text('TRUNCATE duplicate_comparison CASCADE'))
                importlib.import_module('migrations.versions.0106_duplicate_comparisons').downgrade()
                db.execute(text('TRUNCATE allocation_proposal CASCADE'))
                importlib.import_module('migrations.versions.0105_allocation_proposals').downgrade()
                db.execute(text('TRUNCATE context_decision CASCADE'))
                importlib.import_module('migrations.versions.0104_context_decisions').downgrade()
                db.execute(text('TRUNCATE invoice_review_revision'))
                oid=db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))
                migration.downgrade();migration.upgrade()
                assert db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))==oid
                assert db.scalar(text('SELECT count(*) FROM invoice_review_revision'))==0
            finally:point.rollback()
        assert await read_value('SELECT count(*) FROM invoice_review_revision WHERE organization_id=:org',ids)==1
    finally:engine.dispose()


async def test_field_review_hidden_successor_denies_new_write_but_retains_authorized_receipt(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,child,partition,extraction,p=await proposal(api,ids);url='/api/logical-documents/'+child['id']+'/invoice-reviews'
        body=command(p);reply=await api.post(url,**body);assert reply.status_code==201,reply.text
        review=reply.json()
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:other_scope,:clerk,ARRAY['read','prepare'],:actor,'Synthetic next context')"),ids)
        assert (await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**proposal_command(partition,extraction,ids['other_scope'],previous=p['id']))).status_code==201
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:other_scope AND revoked_at IS NULL'),ids)
        assert (await api.post(url,**command(p,previous=review['id']))).status_code==404
        assert (await api.post(url,**body)).json()==review
