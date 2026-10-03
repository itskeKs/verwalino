"""Case completion retains physical identity while acknowledging changed sources."""
import pytest
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client
from tests.test_scope_payment_reconciliation import read_value
from tests.test_allocation_adoptions import confirm_grant,reviewed
from tests.test_allocation_source_replacements import prepared,preview_url,replacement_url,replacement_review
from tests.test_allocation_corrections import command as replace_command,next_review
from tests.test_exception_completions import command,completion_preview,adopted_context

pytestmark=pytest.mark.asyncio


@pytest.mark.parametrize('changed_amount',[False,True])
async def test_replaced_context_and_duplicate_cases_have_exact_typed_outcomes(migrator_session,scope_seed,store,changed_amount):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids);await confirm_grant({**ids,'scope':ids['other_scope']})
    async with client(ids) as api:
        child,a,t,old,context_case=await adopted_context(api,ids)
        new_child,a,t=await replacement_review(api,ids,changed_amount=changed_amount)
        p=(await api.get(preview_url(old,t))).json();assert p['can_review'],p
        reply=await api.post(replacement_url(old),**replace_command(old,t,p));assert reply.status_code==201,reply.text
        new=reply.json()['allocation']
        cases=(await api.get('/api/exceptions')).json()['items'];assert cases
        for case in cases:
            preview=await completion_preview(api,case);assert preview['can_complete'],preview
            assert len(preview['members'])==2
            replaced=next(m for m in preview['members'] if m['logical_document_id']==child['id'])
            assert replaced['disposition']=='replaced_document' and replaced['allocation_set_id']==old['id']
            assert replaced['replacement']['allocation_head_id']==new['id'] and replaced['replacement']['source_document_id']==new['source_document_id']
            assert preview['comparisons'][0]['verdict']=='confirmed_replacement'
            body=command(preview);endpoint='/api/exceptions/'+case['id']+'/completions'
            result=await api.post(endpoint,**body);assert result.status_code==201,result.text
            assert (await api.post(endpoint,**body)).json()==result.json()
            assert (await api.get('/api/exceptions/'+case['id'])).json()['resolution_state']=='resolved'
            member=next(m for m in result.json()['members'] if m['logical_document_id']==child['id'])
            assert member['replacement']['allocation_head_id']==new['id']
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org AND superseded_by_id IS NULL',ids)==2


async def test_completion_tracks_new_heads_but_keeps_historical_receipt(migrator_session,scope_seed,store):
    from tests.test_allocation_correction_preview import preview_url as correct_preview
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        old_child,old,_,a,t=await prepared(api,ids)
        response=await api.post(replacement_url(old),**replace_command(old,t,(await api.get(preview_url(old,t))).json()));assert response.status_code==201,response.text
        new=response.json()['allocation'];case=(await api.get('/api/exceptions')).json()['items'][0];url='/api/exceptions/'+case['id']
        body=command(await completion_preview(api,case));first=await api.post(url+'/completions',**body);assert first.status_code==201,first.text
        assert (await api.get(url)).json()['resolution_state']=='resolved'
        a,t=await next_review(api,a,t)
        response=await api.post('/api/allocation-sets/'+new['id']+'/corrections',**replace_command(new,t,(await api.get(correct_preview(new,t))).json()));assert response.status_code==201,response.text
        new=response.json()['allocation'];detail=(await api.get(url)).json();assert detail['completion_changed'] and detail['resolution_state']=='unresolved'
        assert (await api.post(url+'/completions',**body)).json()==first.json()
        preview=await completion_preview(api,case);assert preview['can_complete'],preview
        response=await api.post(url+'/completions',**command(preview));assert response.status_code==201,response.text
        _,_,t=await replacement_review(api,ids,changed_amount=True)
        response=await api.post(replacement_url(new),**replace_command(new,t,(await api.get(preview_url(new,t))).json()));assert response.status_code==201,response.text
        latest=response.json()['allocation'];preview=await completion_preview(api,case);assert preview['can_complete'] and len(preview['members'])==3,preview
        assert (await api.get(url)).json()['completion_changed']
        replaced=[m for m in preview['members'] if m['disposition']=='replaced_document'];assert len(replaced)==2
        assert all(m['replacement']['allocation_head_id']==latest['id'] for m in replaced)
        response=await api.post(url+'/completions',**command(preview));assert response.status_code==201,response.text
        assert (await api.get(url)).json()['resolution_state']=='resolved'
        assert (await api.post(url+'/completions',**body)).json()==first.json()


async def test_new_old_source_review_or_unrelated_source_never_waived(migrator_session,scope_seed,store):
    from tests.test_invoice_reviews import command as fields
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        old_child,old,_,_,t=await prepared(api,ids)
        response=await api.post(replacement_url(old),**replace_command(old,t,(await api.get(preview_url(old,t))).json()));assert response.status_code==201,response.text
        case=(await api.get('/api/exceptions')).json()['items'][0];p=await completion_preview(api,case);assert p['can_complete']
        stale=command(p)
        old_review=(await api.get('/api/logical-documents/'+old_child['id']+'/invoice-reviews')).json()['items'][0]
        reply=await api.post('/api/logical-documents/'+old_child['id']+'/invoice-reviews',**fields({'id':old_review['assignment_proposal_id']},previous=old_review['id'],reason='Later unadopted interpretation'))
        assert reply.status_code==201,reply.text
        assert not (await completion_preview(api,case))['can_complete']
        assert (await api.post('/api/exceptions/'+case['id']+'/completions',**stale)).status_code==409
        await reviewed(api,ids)
        assert not (await completion_preview(api,case))['can_complete']


async def test_pending_raw_old_source_invalidates_completion_without_money(migrator_session,scope_seed,store):
    from tests.test_intake_batches import receive
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        _,old,_,_,t=await prepared(api,ids)
        data=(await api.get('/api/invoice-documents/'+old['source_document_id']+'/content')).content
        reply=await api.post(replacement_url(old),**replace_command(old,t,(await api.get(preview_url(old,t))).json()));assert reply.status_code==201,reply.text
        case=(await api.get('/api/exceptions')).json()['items'][0];url='/api/exceptions/'+case['id']
        body=command(await completion_preview(api,case));receipt=await api.post(url+'/completions',**body);assert receipt.status_code==201,receipt.text
        await receive(api,data)
        assert not (await completion_preview(api,case))['can_complete']
        assert (await api.get(url)).json()['completion_changed']
        assert (await api.post(url+'/completions',**body)).json()==receipt.json()
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org AND superseded_by_id IS NULL',ids)==2


async def test_replacement_completion_live_audience_and_atomic_seal(migrator_session,scope_seed,store,client_b_admin):
    from sqlalchemy import text
    from tests.test_scoped_account_statements import mutate
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        _,old,_,_,t=await prepared(api,ids)
        reply=await api.post(replacement_url(old),**replace_command(old,t,(await api.get(preview_url(old,t))).json()));assert reply.status_code==201,reply.text
        case=(await api.get('/api/exceptions')).json()['items'][0];url='/api/exceptions/'+case['id'];p=await completion_preview(api,case);body=command(p)
        assert (await client_b_admin.get(url+'/completion-preview')).status_code==404
        await mutate(text("CREATE FUNCTION synthetic_bad_replacement_outcome() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.disposition='replaced_document' THEN NEW.replacement_head_id:=NEW.allocation_set_id;END IF;RETURN NEW;END $$"),{})
        await mutate(text('CREATE TRIGGER synthetic_bad_replacement_outcome BEFORE INSERT ON exception_completion_member FOR EACH ROW EXECUTE FUNCTION synthetic_bad_replacement_outcome()'),{})
        try:
            reply=await api.post(url+'/completions',**body);assert reply.status_code==422,reply.text
            assert await read_value('SELECT count(*) FROM exception_completion WHERE organization_id=:org',ids)==0
            assert (await api.get(url)).json()['revision_id']==p['expected_event_id']
        finally:
            await mutate(text('DROP TRIGGER synthetic_bad_replacement_outcome ON exception_completion_member'),{})
            await mutate(text('DROP FUNCTION synthetic_bad_replacement_outcome()'),{})
        reply=await api.post(url+'/completions',**body);assert reply.status_code==201,reply.text
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'),ids)
        assert (await api.get(url+'/completion-preview')).status_code==404
        assert (await api.post(url+'/completions',**body)).status_code==404


async def test_ordinary_duplicate_basis_survives_upgrade_and_empty_roundtrip(migrator_session,scope_seed,store):
    import importlib
    from sqlalchemy import create_engine,text
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_scoped_account_statements import mutate
    from tests.test_duplicate_comparisons import command as compare,comparisons
    from tests.test_allocation_adoptions import command as adopt
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    # Isolate this historical roundtrip before creating its own ordinary receipt.
    await mutate(text('TRUNCATE exception_completion CASCADE'),{})
    async with client(ids) as api:
        old_child,old,new_child,a,t=await prepared(api,ids)
        r=(await api.get('/api/invoice-reviews/'+a['invoice_review_id'])).json()
        old_r=(await api.get('/api/logical-documents/'+old_child['id']+'/invoice-reviews')).json()['items'][0]
        response=await api.post(comparisons(r),**compare(old_r,verdict='distinct_documents'));assert response.status_code==201,response.text
        p=(await api.get('/api/allocation-treatments/'+t['id']+'/adoption-preview')).json();assert p['can_adopt'],p
        response=await api.post('/api/logical-documents/'+new_child['id']+'/allocation-adoptions',**adopt(t,p));assert response.status_code==201,response.text
        case=(await api.get('/api/exceptions')).json()['items'][0];p=await completion_preview(api,case);assert p['can_complete'],p
        response=await api.post('/api/exceptions/'+case['id']+'/completions',**command(p));assert response.status_code==201,response.text
        receipt=response.json()
    migration=importlib.import_module('migrations.versions.0123_replacement_exception_outcomes');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),{'org':str(ids['org']),'actor':str(ids['clerk'])})
            before={s:db.scalar(text('SELECT pg_get_functiondef(CAST(:s AS regprocedure))'),{'s':s}) for s in migration.ADAPTED}
            current=text('SELECT exception_completion_current(:org,CAST(:receipt AS uuid))');params={**ids,'receipt':receipt['id']}
            assert db.scalar(current,params)
            migration.downgrade();assert db.scalar(current,params)
            migration.upgrade();assert db.scalar(current,params)
            assert before=={s:db.scalar(text('SELECT pg_get_functiondef(CAST(:s AS regprocedure))'),{'s':s}) for s in before}
            for signature in migration.ADDED+['pre_replaced_outcome_'+s for s in migration.ADAPTED]:
                for role in ['app_rw','app_worker','app_review']:
                    assert not db.scalar(text('SELECT has_function_privilege(:role,:signature,\'execute\')'),{'role':role,'signature':signature})
    finally:engine.dispose()
