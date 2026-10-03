"""Whole-set correction is atomic, retained and replayable without duplicate money."""
import uuid
import pytest
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client
from tests.test_scope_payment_reconciliation import read_value
from tests.test_allocation_adoptions import confirm_grant
from tests.test_allocation_correction_preview import prepared,preview_url,parent,coverage

pytestmark=pytest.mark.asyncio


def command(old,review,preview,key=None):
    return {'headers':{'Idempotency-Key':key or str(uuid.uuid4()),'If-Match':'"'+old['id']+'"'},'json':{
      'treatment_review_id':review['id'],'preview_digest':preview['preview_digest'],'reason':'Confirm complete reviewed correction and open downstream obligations',
      'costs_confirmed':True,'whole_original_disclosure_reviewed':True,'downstream_followups_reviewed':True}}


@pytest.mark.parametrize('credit',[False,True])
async def test_correction_replaces_all_members_and_source_once(migrator_session,scope_seed,store,credit):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        old,allocation,review,_=await prepared(api,ids,credit)
        run=await parent(ids)
        plan=await parent(ids,kind='economic_plan');await coverage(ids,plan,kind='economic_plan',source=old['source_document_id'])
        preview=await api.get(preview_url(old,review));assert preview.status_code==200,preview.text
        assert preview.json()['can_review'],preview.text
        body=command(old,review,preview.json());url='/api/allocation-sets/'+old['id']+'/corrections'
        reply=await api.post(url,**body);assert reply.status_code==201,reply.text
        result=reply.json();new=result['allocation'];receipt=result['receipt']
        assert len(new['costs'])==3 and new['id']!=old['id']
        assert receipt['predecessor_id']==old['id'] and receipt['successor_id']==new['id']
        assert {item['snapshot']['id'] for item in receipt['impacts']}=={str(run),str(plan)}
        assert (await api.post(url,**body)).json()==result
        assert (await api.get('/api/allocation-corrections/'+receipt['id'])).json()==result
        assert (await api.get('/api/logical-documents/'+allocation['logical_document_id']+'/allocation-adoptions')).json()['item']==new
        for member in old['costs']:
            cost=(await api.get('/api/cost-entries/'+member['cost_entry_id'])).json()
            assert cost['superseded_by_id']==new['primary_cost_id'] and cost['gross_amount']==member['gross_amount']
        prior_source=(await api.get('/api/invoice-documents/'+old['source_document_id'])).json()
        source=(await api.get('/api/invoice-documents/'+new['source_document_id'])).json()
        assert prior_source['status']=='superseded' and source['status']=='active'
        assert source['supersedes_id']==prior_source['id'] and source['chain_root_id']==prior_source['id'] and source['version']==2
        assert source['content_sha256']==prior_source['content_sha256'] and source['retention_until']>=prior_source['retention_until']
        assert not (await api.get(preview_url(old,review))).json()['can_review']
        assert not (await api.get('/api/allocation-treatments/'+review['id']+'/adoption-preview')).json()['can_adopt']
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org AND superseded_by_id IS NULL',ids)==3
    assert str(await read_value('SELECT sum(gross_amount) FROM cost_entry WHERE organization_id=:org AND superseded_by_id IS NULL',ids))==('-119.00' if credit else '119.00')
    assert await read_value('SELECT count(*) FROM invoice_document WHERE organization_id=:org AND status=\'active\'',ids)==1


async def next_review(api,allocation,review):
    from tests.test_allocation_proposals import command as allocation_command
    from tests.test_allocation_treatments import command as treatment_command
    rows=[{'cost_category_id':allocation['lines'][0]['cost_category_id'],'cost_category_plan_version':1,
      'description':'Another complete reviewed correction','gross_amount':amount,'vat_amount':None} for amount in ['59.00','60.00']]
    reply=await api.post('/api/logical-documents/'+allocation['logical_document_id']+'/allocation-proposals',
      **allocation_command({'id':allocation['context_decision_id']},rows,previous=allocation['id']))
    assert reply.status_code==201,reply.text
    allocation=reply.json();preview=(await api.get('/api/allocation-proposals/'+allocation['id']+'/treatment-preview')).json()
    reply=await api.post('/api/logical-documents/'+allocation['logical_document_id']+'/allocation-treatments',**treatment_command(allocation,preview,previous=review['id']))
    assert reply.status_code==201,reply.text
    return allocation,reply.json()


async def test_correction_historical_retry_after_successor_and_current_head(migrator_session,scope_seed,store,monkeypatch):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        old,a,t,_=await prepared(api,ids)
        body=command(old,t,(await api.get(preview_url(old,t))).json());url='/api/allocation-sets/'+old['id']+'/corrections'
        first=await api.post(url,**body);assert first.status_code==201,first.text
        first=first.json();a,t=await next_review(api,a,t)
        new=first['allocation'];preview=(await api.get(preview_url(new,t))).json();assert preview['can_review'],preview
        second=await api.post('/api/allocation-sets/'+new['id']+'/corrections',**command(new,t,preview));assert second.status_code==201,second.text
        second=second.json()
        monkeypatch.setattr('app.services.allocation_adoption_service.verify_document_integrity_and_size',lambda *args:False)
        assert (await api.post(url,**body)).json()==first
        assert (await api.get('/api/allocation-corrections/'+first['receipt']['id'])).json()==first
        assert (await api.get('/api/logical-documents/'+a['logical_document_id']+'/allocation-adoptions')).json()['item']==second['allocation']
        altered={**body,'json':{**body['json'],'reason':'Changed historical command'}}
        assert (await api.post(url,**altered)).status_code==409
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org AND superseded_by_id IS NULL',ids)==2
    assert await read_value('SELECT count(*) FROM allocation_correction WHERE organization_id=:org',ids)==2


@pytest.mark.parametrize('table,operation,condition',[
    ('cost_entry','INSERT','NEW.gross_amount=40'),('allocation_correction','INSERT','true'),
    ('allocation_correction_impact','INSERT','true'),('cost_entry','UPDATE','NEW.superseded_by_id IS NOT NULL'),
    ('invoice_document','UPDATE',"NEW.status='superseded'"),('invoice_document','INSERT','NEW.version=2'),
    ('audit_event','INSERT',"NEW.action='document.allocation_corrected'")])
async def test_correction_rolls_back_every_stage_and_concurrent_exact_retry(migrator_session,scope_seed,store,table,operation,condition):
    import asyncio
    from sqlalchemy import text
    from tests.test_scoped_account_statements import mutate
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        old,_,review,_=await prepared(api,ids);await parent(ids)
        preview=(await api.get(preview_url(old,review))).json();body=command(old,review,preview)
        url='/api/allocation-sets/'+old['id']+'/corrections'
        await mutate(text("CREATE FUNCTION synthetic_correction_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF "+condition+" THEN RAISE EXCEPTION 'Synthetic correction rollback' USING ERRCODE='23514';END IF;RETURN NEW;END $$"),{})
        await mutate(text('CREATE TRIGGER synthetic_correction_failure BEFORE '+operation+' ON '+table+' FOR EACH ROW EXECUTE FUNCTION synthetic_correction_failure()'),{})
        try:
            result=await api.post(url,**body);assert result.status_code==422,result.text
            assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org AND superseded_by_id IS NULL',ids)==2
            assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==2
            assert await read_value('SELECT count(*) FROM invoice_document WHERE organization_id=:org AND status=\'active\'',ids)==1
            assert await read_value('SELECT count(*) FROM allocation_correction WHERE organization_id=:org',ids)==0
            assert await read_value('SELECT count(*) FROM allocation_correction_impact WHERE organization_id=:org',ids)==0
        finally:
            await mutate(text('DROP TRIGGER synthetic_correction_failure ON '+table),{})
            await mutate(text('DROP FUNCTION synthetic_correction_failure()'),{})
        replies=await asyncio.gather(api.post(url,**body),api.post(url,**body))
        assert [r.status_code for r in replies]==[201,201],[r.text for r in replies]
        assert replies[0].json()==replies[1].json()
        assert (await api.post(url,**command(old,review,preview))).status_code==409


async def test_correction_requires_current_impact_acknowledgments_bytes_and_live_authority(migrator_session,scope_seed,store,monkeypatch,client_b_admin):
    from sqlalchemy import text
    from tests.test_scoped_account_statements import mutate
    import app.services.allocation_adoption_service as service
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api,client(ids,'actor') as other:
        old,_,review,_=await prepared(api,ids);preview=(await api.get(preview_url(old,review))).json()
        body=command(old,review,preview);url='/api/allocation-sets/'+old['id']+'/corrections'
        for ack in ('costs_confirmed','whole_original_disclosure_reviewed','downstream_followups_reviewed'):
            assert (await api.post(url,**{**body,'json':{**body['json'],ack:False}})).status_code==422
        assert (await other.post(url,**body)).status_code==404
        assert (await client_b_admin.post(url,**body)).status_code==404
        await parent(ids)
        assert (await api.post(url,**body)).status_code==409
        body=command(old,review,(await api.get(preview_url(old,review))).json())
        verify=service.verify_document_integrity_and_size;monkeypatch.setattr(service,'verify_document_integrity_and_size',lambda *args:False)
        assert (await api.post(url,**body)).status_code==409
        assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==2
        monkeypatch.setattr(service,'verify_document_integrity_and_size',verify)
        result=await api.post(url,**body);assert result.status_code==201,result.text
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND user_id=:clerk AND scope_id=:scope AND revoked_at IS NULL'),ids)
        assert (await api.post(url,**body)).status_code==404
        assert (await api.get('/api/allocation-corrections/'+result.json()['receipt']['id'])).status_code==404


async def test_correction_guards_runtime_writes_and_retains_evidence_on_downgrade(migrator_session,scope_seed,store):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine,text
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_session_factory,set_tenant_context
    from tests.conftest import MIGRATOR_SYNC_URL
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        old,_,review,_=await prepared(api,ids);run=await parent(ids)
        result=await api.post('/api/allocation-sets/'+old['id']+'/corrections',**command(old,review,(await api.get(preview_url(old,review))).json()))
        assert result.status_code==201,result.text
        result=result.json();new=result['allocation']
    params={**ids,'cost':uuid.UUID(new['primary_cost_id']),'other_cost':uuid.UUID(old['primary_cost_id']),'source':uuid.UUID(new['source_document_id']),'run':run}
    async with get_session_factory()() as db,db.begin():
        await set_tenant_context(db,ids['org']);await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
        for statement in [
            'UPDATE cost_entry SET gross_amount=1 WHERE organization_id=:org AND id=:cost',
            'UPDATE cost_entry SET superseded_by_id=:other_cost WHERE organization_id=:org AND id=:cost',
            "UPDATE invoice_document SET status='superseded',superseded_at=clock_timestamp() WHERE organization_id=:org AND id=:source",
            'DELETE FROM cost_entry WHERE organization_id=:org AND id=:cost',
            'SELECT * FROM allocation_correction WHERE organization_id=:org',
            'SELECT * FROM allocation_correction_impact WHERE organization_id=:org',
            'DELETE FROM settlement_run WHERE organization_id=:org AND id=:run',
        ]:
            with pytest.raises(DBAPIError):
                async with db.begin_nested():await db.execute(text(statement),params)
    migration=importlib.import_module('migrations.versions.0114_allocation_corrections');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain allocation correction'),db.begin_nested():migration.downgrade()
            for table in ('allocation_correction','allocation_correction_impact'):
                with pytest.raises(DBAPIError,match='immutable|Immutable'),db.begin_nested():db.execute(text('DELETE FROM '+table+' WHERE organization_id=:org'),ids)
            functions=['allocation_adoption_result(uuid,uuid)','evaluate_exception_completion(uuid,uuid)','mapping_source_basis(uuid,uuid)',
              'protect_adopted_cost()','protect_adopted_invoice()','adoption_bridge_admission(public.invoice_document)','access_coverage_manifest(uuid,jsonb)']
            before={f:db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':f}) for f in functions}
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE allocation_integrity_attestation CASCADE'))
                computation=importlib.import_module('migrations.versions.0115_financial_computation_evidence')
                db.execute(text('TRUNCATE financial_computation_attestation CASCADE'))
                followup=importlib.import_module('migrations.versions.0116_correction_followup_completions')
                db.execute(text('TRUNCATE allocation_correction_context,allocation_correction_completion'))
                followup.downgrade()
                computation.downgrade()
                migration.downgrade();migration.upgrade()
                computation.upgrade()
                followup.upgrade()
                assert before=={f:db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':f}) for f in functions}
            finally:point.rollback()
    finally:engine.dispose()


async def test_later_correction_retains_ancestor_costs_sources_and_live_parent_access(migrator_session,scope_seed,store):
    from sqlalchemy import text
    from tests.test_scoped_account_statements import mutate
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        old,a,t,_=await prepared(api,ids)
        plan=await parent(ids,kind='economic_plan')
        await coverage(ids,plan,kind='economic_plan',source=old['source_document_id'])
        await mutate(text("UPDATE economic_plan SET status='finalized',snapshot_payload='{\"document_type\":\"WIRTSCHAFTSPLAN\",\"result\":{\"owners\":[]}}',snapshot_sha256=:hash,finalized_at=clock_timestamp(),finalized_by=:actor WHERE organization_id=:org AND id=:target"),{**ids,'target':plan,'hash':'c'*64})
        run=await parent(ids,start='2027-01-01',end='2027-12-31',
            snapshot={'run_type':'WEG_JAHRESABRECHNUNG','computation':[{'cost_entry_id':old['costs'][0]['cost_entry_id']}]})
        await coverage(ids,run,source=old['source_document_id'])
        body=command(old,t,(await api.get(preview_url(old,t))).json())
        first=await api.post('/api/allocation-sets/'+old['id']+'/corrections',**body)
        assert first.status_code==201,first.text
        first=first.json();new=first['allocation'];a,t=await next_review(api,a,t)
        preview=(await api.get(preview_url(new,t))).json();assert preview['can_review'],preview
        impacts={i['id']:i for i in preview['impacts']}
        assert impacts[str(plan)]['status']=='finalized' and impacts[str(plan)]['effects']['source_evidence_review']
        assert impacts[str(run)]['effects']['exact_cost_consumption'] and impacts[str(run)]['effects']['source_evidence_review']
        pending=command(new,t,preview)
        # A newly inaccessible owner affects retained financial disclosure, while
        # the correction's property/scope/private-source authority stays intact.
        from tests.test_property_source_access import grant
        from tests.test_property_source_enforcement import synthetic_enforcement
        async with client(ids,'actor') as admin:
            await grant(admin,scope_seed,ids['property'],actor=ids['clerk'])
        people={**ids,'person':uuid.uuid4(),'owner':uuid.uuid4(),'target':plan}
        await mutate(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES(:person,:org,'Retained','Owner','','','')"),people)
        await mutate(text("INSERT INTO owner_profile(id,organization_id,person_id,notes) VALUES(:owner,:org,:person,'')"),people)
        await mutate(text("INSERT INTO economic_plan_line(organization_id,economic_plan_id,line_type,owner_profile_id,label,planned_amount,monthly_amount,sort_order) VALUES(:org,:target,'OWNER_ADVANCE',:owner,'Retained owner',120,10,1)"),people)
        async with synthetic_enforcement(scope_seed):
            blocked=await api.get(preview_url(new,t));assert blocked.status_code==200,blocked.text
            assert not blocked.json()['can_review'] and blocked.json()['impacts']==[] and str(plan) not in blocked.text
            rejected=await api.post('/api/allocation-sets/'+new['id']+'/corrections',**pending)
            assert rejected.status_code==409,rejected.text
            assert (await api.get('/api/allocation-corrections/'+first['receipt']['id'])).status_code==404
            assert (await api.post('/api/allocation-sets/'+old['id']+'/corrections',**body)).status_code==404
        assert await read_value('SELECT count(*) FROM allocation_correction WHERE organization_id=:org',ids)==1


async def test_correction_runtime_roles_and_transaction_bound_integrity_receipt(migrator_session,scope_seed,store):
    from sqlalchemy import text
    from app.core.db import get_session_factory,get_worker_session_factory,get_review_session_factory,set_tenant_context
    from sqlalchemy.exc import DBAPIError
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        old,_,review,_=await prepared(api,ids)
        preview=(await api.get(preview_url(old,review))).json()
    params={**ids,'predecessor':uuid.UUID(old['id']),'target':uuid.UUID(review['id']),'request':uuid.uuid4(),'preview':preview['preview_digest'],'proof':uuid.uuid4()}
    arguments=":org,:predecessor,:target,:predecessor,:request,:preview,'Explicit source and cost review',true,true,true"
    async def principal(db):
        await set_tenant_context(db,ids['org'])
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
    for factory in (get_worker_session_factory(),get_review_session_factory()):
        with pytest.raises(DBAPIError,match='Interactive runtime required|permission denied'):
            async with factory() as db,db.begin():
                await principal(db)
                await db.execute(text('SELECT prepare_allocation_correction('+arguments+')'),params)
    async with get_session_factory()() as db,db.begin():
        await principal(db)
        admission=await db.scalar(text('SELECT prepare_allocation_correction('+arguments+')'),params)
        with pytest.raises(DBAPIError,match='integrity attestation'):
            async with db.begin_nested():await db.execute(text('SELECT correct_allocation('+arguments+',:proof)'),params)
        proof_params={**ids,'original':uuid.UUID(admission['original_id']),'request':params['request'],'digest':admission['request_digest'],
          'pid':admission['backend_pid']+1,'transaction':admission['transaction_id'],'hash':admission['content_sha256'],'size':admission['content_byte_size']}
        sql='SELECT attest_allocation_integrity(:org,:original,:request,:digest,:pid,:transaction,:hash,:size)'
        with pytest.raises(DBAPIError,match='permission denied'):
            async with db.begin_nested():await db.execute(text(sql),proof_params)
        async with get_review_session_factory()() as reviewer,reviewer.begin():
            await principal(reviewer)
            proof=await reviewer.scalar(text(sql),proof_params)
        with pytest.raises(DBAPIError,match='integrity attestation'):
            async with db.begin_nested():await db.execute(text('SELECT correct_allocation('+arguments+',:proof)'),{**params,'proof':proof})
    assert await read_value('SELECT count(*) FROM allocation_correction WHERE organization_id=:org',ids)==0
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org AND superseded_by_id IS NULL',ids)==2



async def test_changed_amount_period_and_competing_corrections_are_complete(migrator_session,scope_seed,store):
    import asyncio
    from tests.test_invoice_reviews import command as field_command
    from tests.test_context_decisions import command as context_command
    from tests.test_allocation_proposals import command as allocation_command
    from tests.test_allocation_treatments import command as treatment_command
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        old,a,t,_=await prepared(api,ids);logical=a['logical_document_id']
        fields=(await api.get('/api/logical-documents/'+logical+'/invoice-reviews')).json()['items'][0]
        proposal={'id':fields['assignment_proposal_id']}
        response=await api.post('/api/logical-documents/'+logical+'/invoice-reviews',**field_command(proposal,previous=fields['id'],gross_amount='150.00',service_period_from='2027-01-01',service_period_to='2027-01-31'))
        assert response.status_code==201,response.text
        fields=response.json();preview=(await api.get('/api/assignment-proposals/'+proposal['id']+'/context-preview')).json()
        response=await api.post('/api/logical-documents/'+logical+'/context-decisions',**context_command(proposal,fields,preview,previous=a['context_decision_id']))
        assert response.status_code==201,response.text
        rows=[{'cost_category_id':a['lines'][0]['cost_category_id'],'cost_category_plan_version':1,'description':'Changed whole invoice','gross_amount':'150.00','vat_amount':None}]
        response=await api.post('/api/logical-documents/'+logical+'/allocation-proposals',**allocation_command(response.json(),rows,previous=a['id']))
        assert response.status_code==201,response.text
        a=response.json();preview=(await api.get('/api/allocation-proposals/'+a['id']+'/treatment-preview')).json()
        response=await api.post('/api/logical-documents/'+logical+'/allocation-treatments',**treatment_command(a,preview,previous=t['id']))
        assert response.status_code==201,response.text
        t=response.json();old_period=await parent(ids);new_period=await parent(ids,start='2027-01-01',end='2027-12-31')
        preview=(await api.get(preview_url(old,t))).json();assert preview['can_review'],preview
        assert preview['old']['gross_amount']=='119.00' and preview['proposed']['gross_amount']=='150.00'
        assert {i['id'] for i in preview['impacts']}=={str(old_period),str(new_period)}
        assert all(i['effects']['potential_calculation_change'] for i in preview['impacts'])
        assert str(await read_value('SELECT sum(gross_amount) FROM cost_entry WHERE organization_id=:org AND superseded_by_id IS NULL',ids))=='119.00'
        url='/api/allocation-sets/'+old['id']+'/corrections'
        bodies=[command(old,t,preview),command(old,t,preview)]
        responses=await asyncio.gather(*[api.post(url,**body) for body in bodies])
        assert sorted(r.status_code for r in responses)==[201,409],[r.text for r in responses]
        winner=next(r.json() for r in responses if r.status_code==201)
        assert len(winner['allocation']['costs'])==1
        current=(await api.get('/api/logical-documents/'+logical+'/allocation-adoptions')).json()
        assert current['item']==winner['allocation'] and current['correction']==winner
    assert str(await read_value('SELECT sum(gross_amount) FROM cost_entry WHERE organization_id=:org AND superseded_by_id IS NULL',ids))=='150.00'
    assert await read_value('SELECT count(*) FROM allocation_correction WHERE organization_id=:org',ids)==1


async def test_readable_current_allocation_does_not_require_private_correction_confirmation_rights(migrator_session,scope_seed,store):
    from sqlalchemy import text
    from tests.test_scoped_account_statements import mutate
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        old,a,t,_=await prepared(api,ids)
        reply=await api.post('/api/allocation-sets/'+old['id']+'/corrections',**command(old,t,(await api.get(preview_url(old,t))).json()))
        assert reply.status_code==201,reply.text
        saved=reply.json()
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'),ids)
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:scope,:clerk,ARRAY['read'],:actor,'Read-only retained allocation')"),ids)
        response=await api.get('/api/logical-documents/'+a['logical_document_id']+'/allocation-adoptions')
        assert response.status_code==200,response.text
        assert response.json()=={'item':saved['allocation'],'correction':None}
        assert (await api.get('/api/allocation-corrections/'+saved['receipt']['id'])).status_code==404


async def test_correction_invalidates_old_exception_completion_and_selects_current_outcome(migrator_session,scope_seed,store):
    from tests.test_exception_completions import adopted_context,completion_preview,command as complete_command
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids);await confirm_grant({**ids,'scope':ids['other_scope']})
    async with client(ids) as api:
        _,a,t,old,case=await adopted_context(api,ids)
        url='/api/exceptions/'+case['id'];request=complete_command(await completion_preview(api,case))
        completed=await api.post(url+'/completions',**request);assert completed.status_code==201,completed.text
        a,t=await next_review(api,a,t)
        preview=(await api.get(preview_url(old,t))).json();assert preview['can_review'],preview
        response=await api.post('/api/allocation-sets/'+old['id']+'/corrections',**command(old,t,preview));assert response.status_code==201,response.text
        new=response.json()['allocation']
        current=await completion_preview(api,case)
        assert current['can_complete'] and [m['allocation_set_id'] for m in current['members']]==[new['id']]
        detail=(await api.get(url)).json();assert detail['completion_changed'] and detail['resolution_state']=='unresolved'
        assert (await api.post(url+'/completions',**request)).json()==completed.json()
        assert (await api.get(url)).json()['resolution_state']=='unresolved'
