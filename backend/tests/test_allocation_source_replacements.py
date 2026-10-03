"""Changed sources retain exact money, permanent claims and one financial chain."""
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_allocation_adoptions import ready,reviewed,confirm_grant,command as adopt_command
from tests.test_allocation_corrections import command,next_review
from tests.test_allocation_correction_preview import parent,coverage,preview_url as correction_preview_url

pytestmark=pytest.mark.asyncio


def preview_url(old,review):
    return '/api/allocation-sets/'+old['id']+'/replacement-preview?treatment_review_id='+review['id']


def replacement_url(old):
    return '/api/allocation-sets/'+old['id']+'/source-replacements'


async def prepared(api,ids,*,changed_amount=False):
    child,a,t,p,url=await ready(api,ids)
    reply=await api.post(url,**adopt_command(t,p));assert reply.status_code==201,reply.text
    old=reply.json()
    new_child,new_a,new_t=await replacement_review(api,ids,changed_amount=changed_amount)
    return child,old,new_child,new_a,new_t


async def replacement_review(api,ids,*,changed_amount=False):
    if not changed_amount:return await reviewed(api,ids)
    from tests.test_assignment_proposals import prepared as source,command as assignment
    from tests.test_invoice_reviews import command as invoice
    from tests.test_context_decisions import command as context
    from tests.test_allocation_proposals import command as allocation
    from tests.test_allocation_treatments import command as treatment
    _,child,partition,extraction=await source(api,ids)
    reply=await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**assignment(partition,extraction,ids['scope']));assert reply.status_code==201,reply.text
    proposal=reply.json()
    reply=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**invoice(proposal,gross_amount='149.00'));assert reply.status_code==201,reply.text
    r=reply.json();p=(await api.get('/api/assignment-proposals/'+proposal['id']+'/context-preview')).json()
    reply=await api.post('/api/logical-documents/'+child['id']+'/context-decisions',**context(proposal,r,p));assert reply.status_code==201,reply.text
    category=await read_value("SELECT id FROM cost_category WHERE is_active AND plan_version=1 AND NOT reserve_relevant AND owner_billing_relevant_default AND cost_group='UMLAGEFAEHIG' ORDER BY id LIMIT 1",ids)
    rows=[{'cost_category_id':str(category),'cost_category_plan_version':1,'description':'Reviewed revised invoice part','gross_amount':amount,'vat_amount':None} for amount in ['49.00','100.00']]
    reply=await api.post('/api/logical-documents/'+child['id']+'/allocation-proposals',**allocation(reply.json(),rows));assert reply.status_code==201,reply.text
    a=reply.json();p=(await api.get('/api/allocation-proposals/'+a['id']+'/treatment-preview')).json()
    reply=await api.post('/api/logical-documents/'+child['id']+'/allocation-treatments',**treatment(a,p));assert reply.status_code==201,reply.text
    return child,a,reply.json()


@pytest.mark.parametrize('changed_amount',[False,True])
async def test_replacement_consumes_new_source_and_retains_old_claim(migrator_session,scope_seed,store,changed_amount):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        child,old,new_child,a,t=await prepared(api,ids,changed_amount=changed_amount)
        run=await parent(ids);await coverage(ids,run,source=old['source_document_id'])
        candidates=await api.get('/api/allocation-treatments/'+t['id']+'/replacement-candidates');assert candidates.status_code==200,candidates.text
        assert [v['id'] for v in candidates.json()['items']]==[old['id']]
        preview=await api.get(preview_url(old,t));assert preview.status_code==200,preview.text
        p=preview.json();assert p['can_review'],p
        assert p['old']['gross_amount']=='119.00' and p['proposed']['gross_amount']==('149.00' if changed_amount else '119.00')
        body=command(old,t,p);reply=await api.post(replacement_url(old),**body);assert reply.status_code==201,reply.text
        result=reply.json();new=result['allocation'];assert new['transition_kind']=='source_replacement'
        assert result['receipt']['transition_kind']=='source_replacement'
        assert (await api.post(replacement_url(old),**body)).json()==result
        for logical in [child['id'],new_child['id']]:assert (await api.get('/api/logical-documents/'+logical+'/allocation-adoptions')).json()['item']==new
        prior=(await api.get('/api/invoice-documents/'+old['source_document_id'])).json()
        source=(await api.get('/api/invoice-documents/'+new['source_document_id'])).json()
        assert source['content_sha256']!=prior['content_sha256'] and source['supersedes_id']==prior['id']
        assert source['version']==2 and source['chain_root_id']==prior['id'] and source['retention_until']>=prior['retention_until']
        assert prior['status']=='superseded' and source['status']=='active'
        assert (await api.get('/api/invoice-documents/'+prior['id']+'/content')).status_code==200
        assert (await api.get('/api/invoice-documents/'+source['id']+'/content')).status_code==200
        assert not (await api.get(preview_url(old,t))).json()['can_review']
        assert not (await api.get('/api/allocation-treatments/'+t['id']+'/adoption-preview')).json()['can_adopt']
        assert result['receipt']['impacts'][0]['snapshot']['effects']['source_evidence_review']
    assert await read_value('SELECT count(*) FROM allocation_source_claim WHERE organization_id=:org',ids)==2
    assert await read_value('SELECT count(DISTINCT claim_id) FROM allocation_source_member WHERE organization_id=:org',ids)==2
    assert str(await read_value('SELECT sum(gross_amount) FROM cost_entry WHERE organization_id=:org AND superseded_by_id IS NULL',ids))==('149.00' if changed_amount else '119.00')


async def test_replacement_then_same_source_correction_then_replacement_replays(migrator_session,scope_seed,store,monkeypatch):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        child,old,new_child,a,t=await prepared(api,ids)
        run=await parent(ids);await coverage(ids,run,source=old['source_document_id'])
        body=command(old,t,(await api.get(preview_url(old,t))).json())
        reply=await api.post(replacement_url(old),**body);assert reply.status_code==201,reply.text
        first=reply.json();a,t=await next_review(api,a,t);new=first['allocation']
        p=(await api.get(correction_preview_url(new,t))).json();assert p['can_review'],p
        correction_body=command(new,t,p);correction_url='/api/allocation-sets/'+new['id']+'/corrections'
        reply=await api.post(correction_url,**correction_body);assert reply.status_code==201,reply.text
        second=reply.json();third_child,_,third_t=await reviewed(api,ids)
        p=(await api.get(preview_url(second['allocation'],third_t))).json();assert p['can_review'],p
        third_body=command(second['allocation'],third_t,p);third_url=replacement_url(second['allocation'])
        reply=await api.post(third_url,**third_body);assert reply.status_code==201,reply.text
        third=reply.json()
        monkeypatch.setattr('app.services.allocation_adoption_service.verify_document_integrity_and_size',lambda *args:False)
        for url,body,result in [(replacement_url(old),body,first),(correction_url,correction_body,second),(third_url,third_body,third)]:
            assert (await api.post(url,**body)).json()==result
            assert (await api.get('/api/allocation-corrections/'+result['receipt']['id'])).json()==result
        for logical in [child['id'],new_child['id'],third_child['id']]:
            assert (await api.get('/api/logical-documents/'+logical+'/allocation-adoptions')).json()['item']==third['allocation']
        assert third['receipt']['impacts'][0]['snapshot']['effects']['source_evidence_review']
    assert await read_value('SELECT count(*) FROM allocation_source_claim WHERE organization_id=:org',ids)==3
    assert await read_value('SELECT count(*) FROM allocation_source_member WHERE organization_id=:org',ids)==4
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org AND superseded_by_id IS NULL',ids)==2


async def test_replacement_rechecks_authority_stale_preview_and_integrity(migrator_session,scope_seed,store,monkeypatch,client_b_admin):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api,client(ids,'actor') as other:
        _,old,_,a,t=await prepared(api,ids)
        p=(await api.get(preview_url(old,t))).json();body=command(old,t,p)
        for field in ['costs_confirmed','whole_original_disclosure_reviewed','downstream_followups_reviewed']:
            assert (await api.post(replacement_url(old),**{**body,'json':{**body['json'],field:False}})).status_code==422
        for foreign in [other,client_b_admin]:
            assert (await foreign.get(preview_url(old,t))).status_code==404
            assert (await foreign.post(replacement_url(old),**body)).status_code==404
            assert (await foreign.get('/api/allocation-treatments/'+t['id']+'/replacement-candidates')).status_code==404
        stale={**body,'json':{**body['json'],'preview_digest':'a'*64}}
        assert (await api.post(replacement_url(old),**stale)).status_code==409
        monkeypatch.setattr('app.services.allocation_adoption_service.verify_document_integrity_and_size',lambda *args:False)
        assert (await api.post(replacement_url(old),**body)).status_code==409
        assert await read_value('SELECT count(*) FROM allocation_source_claim WHERE organization_id=:org',ids)==1
        monkeypatch.undo()
        await parent(ids)
        assert (await api.post(replacement_url(old),**body)).status_code==409
        p=(await api.get(preview_url(old,t))).json();body=command(old,t,p)
        result=await api.post(replacement_url(old),**body);assert result.status_code==201,result.text
        changed={**body,'json':{**body['json'],'reason':'Altered command'}}
        assert (await api.post(replacement_url(old),**changed)).status_code==409
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'),ids)
        assert (await api.post(replacement_url(old),**body)).status_code==404
        assert (await api.get('/api/allocation-corrections/'+result.json()['receipt']['id'])).status_code==404


@pytest.mark.parametrize('stage',['claim','receipt','source','audit'])
async def test_replacement_is_atomic_and_exact_concurrent_retry(migrator_session,scope_seed,store,stage):
    import asyncio
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        _,old,_,_,t=await prepared(api,ids);await parent(ids)
        body=command(old,t,(await api.get(preview_url(old,t))).json())
        table,condition={'claim':('allocation_source_claim','true'),'receipt':('allocation_correction','true'),
          'source':('invoice_document','NEW.version=2'),'audit':('audit_event',"NEW.action='document.allocation_source_replaced'")}[stage]
        await mutate(text("CREATE FUNCTION synthetic_replacement_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF "+condition+" THEN RAISE EXCEPTION 'Synthetic replacement rollback' USING ERRCODE='23514';END IF;RETURN NEW;END $$"),{})
        await mutate(text('CREATE TRIGGER synthetic_replacement_failure BEFORE INSERT ON '+table+' FOR EACH ROW EXECUTE FUNCTION synthetic_replacement_failure()'),{})
        try:
            result=await api.post(replacement_url(old),**body);assert result.status_code==422,result.text
            for table_name,count in [('cost_entry',2),('allocation_set',1),('invoice_document',1),('allocation_source_claim',1),('allocation_source_member',1),('allocation_correction',0),('allocation_correction_impact',0)]:
                assert await read_value('SELECT count(*) FROM '+table_name+' WHERE organization_id=:org',ids)==count
            assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org AND superseded_by_id IS NULL',ids)==2
        finally:
            await mutate(text('DROP TRIGGER synthetic_replacement_failure ON '+table),{})
            await mutate(text('DROP FUNCTION synthetic_replacement_failure()'),{})
        replies=await asyncio.gather(*[api.post(replacement_url(old),**body) for _ in range(2)])
        assert [r.status_code for r in replies]==[201,201],[r.text for r in replies]
        assert replies[0].json()==replies[1].json()
    assert await read_value('SELECT count(*) FROM allocation_source_claim WHERE organization_id=:org',ids)==2


async def test_replacement_exempts_only_retained_reviews_not_new_old_source_edits(migrator_session,scope_seed,store):
    from tests.test_invoice_reviews import command as invoice_command
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        old_child,old,_,_,t=await prepared(api,ids)
        assert (await api.get(preview_url(old,t))).json()['can_review']
        review=(await api.get('/api/logical-documents/'+old_child['id']+'/invoice-reviews')).json()['items'][0]
        newer=await api.post('/api/logical-documents/'+old_child['id']+'/invoice-reviews',**invoice_command({'id':review['assignment_proposal_id']},previous=review['id'],reason='Unadopted new source interpretation'))
        assert newer.status_code==201,newer.text
        p=(await api.get(preview_url(old,t))).json();assert not p['can_review'] and p['impacts']==[]
        assert newer.json()['id'] not in str(p)


async def test_replacement_does_not_waive_unrelated_duplicate_or_original_kind(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        _,old,_,_,t=await prepared(api,ids)
        _,_,credit=await reviewed(api,ids,credit=True)
        assert not (await api.get(preview_url(old,credit))).json()['can_review']
        _,_,unrelated=await reviewed(api,ids)
        assert not (await api.get(preview_url(old,t))).json()['can_review']
        assert not (await api.get(preview_url(old,unrelated))).json()['can_review']
        # Ordinary correction cannot be used to smuggle a changed source.
        body=command(old,t,{'preview_digest':'a'*64})
        assert (await api.post('/api/allocation-sets/'+old['id']+'/corrections',**body)).status_code==404


async def test_replacement_migration_roundtrip_and_sealed_private_helpers(migrator_session,scope_seed,store):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    migration=importlib.import_module('migrations.versions.0122_allocation_source_replacements')
    async with client(ids) as api:
        _,old,_,_,t=await prepared(api,ids)
        result=await api.post(replacement_url(old),**command(old,t,(await api.get(preview_url(old,t))).json()));assert result.status_code==201,result.text
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            before={s:db.scalar(text('SELECT pg_get_functiondef(CAST(:s AS regprocedure))'),{'s':s}) for s in migration.ADAPTED}
            with pytest.raises(DBAPIError,match='Retain changed-source'),db.begin_nested():migration.downgrade()
            for role in ['app_rw','app_worker','app_review']:
                for signature in migration.ADDED[:3]+['pre_replacement_'+s for s in migration.ADAPTED]:
                    assert not db.scalar(text('SELECT has_function_privilege(:role,:s,\'execute\')'),{'role':role,'s':signature})
            for table in ['allocation_set','allocation_correction','allocation_source_claim','allocation_source_member']:
                with pytest.raises(DBAPIError,match='immutable|Immutable|only|append'),db.begin_nested():
                    db.execute(text('DELETE FROM '+table+' WHERE organization_id=:org'),ids)
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE allocation_set CASCADE'))
                migration.downgrade();migration.upgrade()
                assert before=={s:db.scalar(text('SELECT pg_get_functiondef(CAST(:s AS regprocedure))'),{'s':s}) for s in before}
            finally:point.rollback()
    finally:engine.dispose()


async def test_old_followup_completes_only_against_latest_replacement_and_sources_stay_consumed(migrator_session,scope_seed,store,monkeypatch):
    from tests.test_correction_followups import corrected_report,declare
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        target,url,values,first,impact=await corrected_report(api,ids)
        old=first['allocation'];old_data=(await api.get('/api/invoice-documents/'+old['source_document_id']+'/content')).content
        for _ in range(2):
            _,_,t=await reviewed(api,ids)
            p=(await api.get(preview_url(old,t))).json();assert p['can_review'],p
            reply=await api.post(replacement_url(old),**command(old,t,p));assert reply.status_code==201,reply.text
            old=reply.json()['allocation']
        status_url='/api/allocation-correction-impacts/'+impact['id']+'/follow-up'
        status=(await api.get(status_url)).json();assert not status['ready'] and status['status']=='open'
        await declare(api,target,values,old['source_document_id'])
        computed=await api.post(url+'/compute',json=values);assert computed.status_code==200,computed.text
        status=(await api.get(status_url)).json();assert status['ready'] and status['source_document_id']==old['source_document_id'],status
        reply=await api.post('/api/allocation-correction-impacts/'+impact['id']+'/completions',headers={'Idempotency-Key':str(uuid.uuid4())},json={
          'preview_digest':status['preview_digest'],'reason':'Original-era obligation checked against current replacement source', 'source_reviewed':True,'confirmed':True})
        assert reply.status_code==201,reply.text
        assert reply.json()['allocation_head_id']==old['id']
        monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:old_data)
        _,_,r,p,_=await ready(api,ids,document_number='NEW-NUMBER-CANNOT-REUSE-OLD-BYTES')
        assert not p['can_adopt']
        assert not (await api.get(preview_url(old,r))).json()['can_review']
