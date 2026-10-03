"""Completion is an exact acknowledged outcome, with separately current validity."""
import uuid
import pytest
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import fixture,store
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_allocation_adoptions import confirm_grant,ready,command as adoption_command
from tests.test_exception_cases import event_command

pytestmark=pytest.mark.asyncio


def command(preview,key=None):
    return {'headers':{'Idempotency-Key':key or str(uuid.uuid4()),'If-Match':'"'+preview['expected_event_id']+'"'},
      'json':{'preview_digest':preview['preview_digest'],'reason':'Checked every retained case dependency against the confirmed outcome','completion_reviewed':True}}


async def adopted_context(api,ids):
    child,allocation,review,preview,url=await ready(api,ids,source_scope=ids['other_scope'])
    result=await api.post(url,**adoption_command(review,preview));assert result.status_code==201,result.text
    cases=(await api.get('/api/exceptions')).json()['items']
    assert len(cases)==1 and cases[0]['cause']=='scope_hint_conflict'
    return child,allocation,review,result.json(),cases[0]


async def completion_preview(api,case):
    response=await api.get('/api/exceptions/'+case['id']+'/completion-preview')
    assert response.status_code==200,response.text
    return response.json()


async def test_completion_exact_context_outcome_and_advisory_inheritance(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids);await confirm_grant({**ids,'scope':ids['other_scope']})
    async with client(ids) as api:
        child,a,r,adopted,case=await adopted_context(api,ids);url='/api/exceptions/'+case['id']
        preview=await completion_preview(api,case);assert preview['can_complete']
        assert [m['allocation_set_id'] for m in preview['members']]==[adopted['id']]
        body=command(preview);response=await api.post(url+'/completions',**body);assert response.status_code==201,response.text
        receipt=response.json();assert (await api.post(url+'/completions',**body)).json()==receipt
        detail=(await api.get(url)).json();assert detail['resolution_state']=='resolved' and detail['workflow_state']=='completed'
        assert detail['completion_id']==receipt['id'] and not detail['completion_changed']
        assert not (await api.get('/api/exceptions',params={'workflow_state':'open'})).json()['items']
        assert (await api.get('/api/exceptions',params={'workflow_state':'completed'})).json()['items'][0]['id']==case['id']
        snooze=event_command(detail['revision_id'],'snooze')
        from datetime import datetime,timedelta,timezone
        snooze['json']['snoozed_until']=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        assert (await api.post(url+'/events',**snooze)).status_code==409
        for verb in ('claim','release'):
            response=await api.post(url+'/events',**event_command(detail['revision_id'],verb));assert response.status_code==201,response.text
            detail=(await api.get(url)).json();assert detail['resolution_state']=='resolved' and detail['completion_id']==receipt['id']
        response=await api.post(url+'/events',**event_command(detail['revision_id'],'reopen'));assert response.status_code==201,response.text
        detail=(await api.get(url)).json();assert detail['resolution_state']=='unresolved' and detail['workflow_state']=='open'
        assert (await api.post(url+'/completions',**body)).json()==receipt
        assert (await api.get(url)).json()['resolution_state']=='unresolved'
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==2


async def test_completion_raw_receipt_invalidates_without_case_event_and_retry_is_historical(migrator_session,scope_seed,store,monkeypatch):
    from tests.test_invoice_extraction_worker import pdf_bytes
    from tests.test_intake_batches import receive
    from sqlalchemy import text
    data=pdf_bytes();monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:data)
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids);await confirm_grant({**ids,'scope':ids['other_scope']})
    async with client(ids) as api:
        _,a,_,_,case=await adopted_context(api,ids);url='/api/exceptions/'+case['id']
        body=command(await completion_preview(api,case));first=await api.post(url+'/completions',**body);assert first.status_code==201,first.text
        before=(await api.get(url)).json()
        await mutate(text("UPDATE property SET name=name||' Renamed' WHERE organization_id=:org AND id=:property"),ids)
        assert (await api.get(url)).json()['resolution_state']=='resolved'
        stale_command=command(await completion_preview(api,case))
        await receive(api,data)
        assert (await api.post(url+'/completions',**stale_command)).status_code==409
        after=(await api.get(url)).json();assert after['revision_id']==before['revision_id']
        assert after['completion_changed'] and after['resolution_state']=='unresolved' and after['workflow_state']=='open'
        assert not (await completion_preview(api,case))['can_complete']
        assert (await api.post(url+'/completions',**body)).json()==first.json()
        assert (await api.get(url)).json()['completion_changed']


@pytest.mark.parametrize('same_bytes',[True,False])
async def test_completion_three_document_component_requires_every_exact_outcome(migrator_session,scope_seed,store,monkeypatch,same_bytes):
    from tests.test_invoice_extraction_worker import pdf_bytes
    from tests.test_duplicate_comparisons import command as compare_command,comparisons
    if same_bytes:
        data=pdf_bytes();monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:data)
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        items=[await ready(api,ids) for _ in range(3)]
        fields=[(await api.get('/api/invoice-reviews/'+item[1]['invoice_review_id'])).json() for item in items]
        for i in range(3):
            for j in range(i+1,3):
                result=await api.post(comparisons(fields[i]),**compare_command(fields[j],verdict='same_document' if same_bytes else 'distinct_documents'))
                assert result.status_code==201,result.text
        cases=(await api.get('/api/exceptions')).json()['items'];assert cases
        case=cases[0];url='/api/exceptions/'+case['id']
        assert not (await completion_preview(api,case))['can_complete']
        for i,item in enumerate(items[:1] if same_bytes else items):
            child,allocation,review,_,adopt_url=item
            preview=(await api.get('/api/allocation-treatments/'+review['id']+'/adoption-preview')).json()
            result=await api.post(adopt_url,**adoption_command(review,preview));assert result.status_code==201,result.text
            if not same_bytes and i<2:assert not (await completion_preview(api,case))['can_complete']
        preview=await completion_preview(api,case);assert preview['can_complete'] and len(preview['members'])==3
        assert len({m['allocation_set_id'] for m in preview['members']})==(1 if same_bytes else 3)
        assert sum(m['disposition']=='covered_copy' for m in preview['members'])==(2 if same_bytes else 0)
        result=await api.post(url+'/completions',**command(preview));assert result.status_code==201,result.text
        assert (await api.get(url)).json()['resolution_state']=='resolved'
        assert len(result.json()['members'])==3
        assert len(preview['comparisons'])==3 and result.json()['comparisons']==preview['comparisons']
        assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==(2 if same_bytes else 6)


async def test_completion_retains_and_disposes_superseded_pair_dependencies(migrator_session,scope_seed,store):
    from tests.test_duplicate_comparisons import command as compare_command,comparisons
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        a=await ready(api,ids);b=await ready(api,ids,field_revisions=2)
        fields=[(await api.get('/api/invoice-reviews/'+item[1]['invoice_review_id'])).json() for item in (a,b)]
        compared=await api.post(comparisons(fields[0]),**compare_command(fields[1],verdict='distinct_documents'));assert compared.status_code==201,compared.text
        cases=(await api.get('/api/exceptions')).json()['items'];assert len(cases)==1
        case=cases[0];assert case['duplicate_dependency_count']==2
        for child,allocation,review,_,url in (a,b):
            preview=(await api.get('/api/allocation-treatments/'+review['id']+'/adoption-preview')).json()
            adopted=await api.post(url,**adoption_command(review,preview));assert adopted.status_code==201,adopted.text
        preview=await completion_preview(api,case);assert preview['can_complete']
        assert {d['disposition'] for d in preview['dispositions']}=={'fulfilled_by_current_comparison','superseded_by_current_comparison'}
        response=await api.post('/api/exceptions/'+case['id']+'/completions',**command(preview));assert response.status_code==201,response.text
        assert len(response.json()['dispositions'])==2


async def test_completion_atomic_event_failure_concurrent_retry_and_private_evidence(migrator_session,scope_seed,store):
    import asyncio
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids);await confirm_grant({**ids,'scope':ids['other_scope']})
    async with client(ids) as api:
        _,_,_,_,case=await adopted_context(api,ids);url='/api/exceptions/'+case['id']
        preview=await completion_preview(api,case);body=command(preview)
        await mutate(text("CREATE FUNCTION synthetic_completion_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.action='completion_recorded' THEN RAISE EXCEPTION 'Synthetic completion failure' USING ERRCODE='23514';END IF;RETURN NEW;END $$"),{})
        await mutate(text('CREATE TRIGGER synthetic_completion_failure BEFORE INSERT ON exception_case_event FOR EACH ROW EXECUTE FUNCTION synthetic_completion_failure()'),{})
        try:
            result=await api.post(url+'/completions',**body);assert result.status_code==422,result.text
            assert await read_value('SELECT count(*) FROM exception_completion WHERE organization_id=:org',ids)==0
            assert await read_value('SELECT count(*) FROM exception_completion_member WHERE organization_id=:org',ids)==0
            assert (await api.get(url)).json()['revision_id']==preview['expected_event_id']
        finally:
            await mutate(text('DROP TRIGGER synthetic_completion_failure ON exception_case_event'),{})
            await mutate(text('DROP FUNCTION synthetic_completion_failure()'),{})
        replies=await asyncio.gather(api.post(url+'/completions',**body),api.post(url+'/completions',**body))
        assert [r.status_code for r in replies]==[201,201],[r.text for r in replies]
        assert replies[0].json()==replies[1].json()
        assert (await api.post(url+'/completions',**command(preview))).status_code==409
        body['json']['reason']='Different completion request'
        assert (await api.post(url+'/completions',**body)).status_code==409
        for sql in ('SELECT basis FROM exception_completion WHERE organization_id=:org','SELECT request_digest FROM exception_completion WHERE organization_id=:org','SELECT evaluate_exception_completion(:org,:case)'):
            with pytest.raises(DBAPIError,match='permission denied'):
                await read_value(sql,{**ids,'case':uuid.UUID(case['id'])},actor_key='clerk')
        for table in ('exception_completion','exception_completion_member'):
            with pytest.raises(DBAPIError,match='immutable|Immutable'):
                await mutate(text('DELETE FROM '+table+' WHERE organization_id=:org'),ids)
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==2


async def test_completion_authority_before_replay_roles_pause_and_new_head(migrator_session,scope_seed,store,client_b_admin):
    from app.core.db import get_worker_session_factory,get_review_session_factory,set_tenant_context
    from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
    from tests.test_invoice_reviews import command as field_command
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids);await confirm_grant({**ids,'scope':ids['other_scope']})
    async with client(ids) as api,client(ids,'actor') as other:
        child,a,_,_,case=await adopted_context(api,ids);url='/api/exceptions/'+case['id']
        preview=await completion_preview(api,case);body=command(preview)
        params={**ids,'case':uuid.UUID(case['id']),'expected':uuid.UUID(preview['expected_event_id']),'request':uuid.uuid4(),'digest':preview['preview_digest']}
        for factory in (get_worker_session_factory(),get_review_session_factory()):
            with pytest.raises(DBAPIError,match='Interactive runtime required|permission denied'):
                async with factory() as db,db.begin():
                    await set_tenant_context(db,ids['org'])
                    await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                    await db.execute(text("SELECT create_exception_completion(:org,:case,:expected,:request,:digest,'Reviewed all outcomes',true)"),params)
        first=await api.post(url+'/completions',**body);assert first.status_code==201,first.text
        receipt_url=url+'/completions/'+first.json()['id']
        for denied in (other,client_b_admin):
            assert (await denied.get(receipt_url)).status_code==404
            assert (await denied.get(url+'/completion-preview')).status_code==404
            assert (await denied.post(url+'/completions',**body)).status_code==404
        async with synthetic_enforcement(scope_seed):
            await change_mode(scope_seed,'PAUSED')
            assert (await api.post(url+'/completions',**body)).status_code==404
            await change_mode(scope_seed,'ENFORCED')
        field=(await api.get('/api/invoice-reviews/'+a['invoice_review_id'])).json()
        changed=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**field_command({'id':field['assignment_proposal_id']},previous=field['id'],document_number='Changed after confirmed outcome'))
        assert changed.status_code==201,changed.text
        detail=(await api.get(url)).json();assert detail['completion_changed'] and detail['resolution_state']=='unresolved'
        assert (await api.post(url+'/completions',**body)).json()==first.json()
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:other_scope AND user_id=:clerk AND revoked_at IS NULL'),ids)
        assert (await api.post(url+'/completions',**body)).status_code==404
        assert (await api.get(receipt_url)).status_code==404


async def test_completion_hidden_raw_source_changes_only_generic_current_status(migrator_session,scope_seed,store,monkeypatch):
    from tests.test_invoice_extraction_worker import pdf_bytes
    from tests.test_intake_batches import receive
    data=pdf_bytes();monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:data)
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids);await confirm_grant({**ids,'scope':ids['other_scope']})
    async with client(ids) as api,client(ids,'actor') as hidden:
        _,_,_,_,case=await adopted_context(api,ids);url='/api/exceptions/'+case['id']
        body=command(await completion_preview(api,case));first=await api.post(url+'/completions',**body);assert first.status_code==201,first.text
        _,receipt=await receive(hidden,data)
        reply=await api.get(url);assert reply.status_code==200 and reply.json()['completion_changed']
        preview=await api.get(url+'/completion-preview');assert preview.status_code==200 and not preview.json()['can_complete']
        assert receipt['original_id'] not in reply.text+preview.text
        assert (await api.post(url+'/completions',**body)).json()==first.json()


async def test_completion_hidden_transitive_member_outside_retained_case_dependencies(migrator_session,scope_seed,store):
    from sqlalchemy import text
    from tests.test_duplicate_comparisons import reviewed as fields_only
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids);await confirm_grant(ids,'actor')
    # Pin only the generated fixture identifier; all source/review provenance is
    # created by the real runtime. The hidden source is the canonical low end.
    hidden_id=uuid.UUID('00000000-0000-4000-8000-000000000001')
    async with client(ids,'actor') as hidden,client(ids) as api:
        await mutate(text("ALTER TABLE logical_document ALTER COLUMN id SET DEFAULT '00000000-0000-4000-8000-000000000001'::uuid"),{})
        try:
            *_,hidden_review=await fields_only(hidden,{**ids,'clerk':ids['actor']})
        finally:await mutate(text('ALTER TABLE logical_document ALTER COLUMN id SET DEFAULT gen_random_uuid()'),{})
        *_,a=await fields_only(api,ids);*_,b=await fields_only(api,ids)
        cases=(await api.get('/api/exceptions')).json()['items'];assert len(cases)==1
        case=cases[0];assert case['logical_document_id']!=str(hidden_id)
        deps=(await api.get('/api/exceptions/'+case['id']+'/duplicate-dependencies')).json()['items']
        assert len(deps)==1 and {deps[0]['anchor_review_id'],deps[0]['reference_review_id']}=={a['id'],b['id']}
        preview=await completion_preview(api,case)
        assert not preview['can_complete'] and preview['preview_digest'] is None
        assert preview['members']==preview['comparisons']==preview['dispositions']==[]
        assert hidden_review['id'] not in str(preview) and str(hidden_id) not in str(preview)


async def test_completion_component_cap_fails_closed_before_pair_evaluation(migrator_session,scope_seed,store):
    from tests.test_duplicate_comparisons import reviewed as fields_only
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        for _ in range(101):await fields_only(api,ids)
        case=(await api.get('/api/exceptions',params={'limit':1})).json()['items'][0]
        preview=await completion_preview(api,case)
        assert not preview['can_complete'] and preview['preview_digest'] is None
        assert preview['members']==preview['comparisons']==preview['dispositions']==[]
        assert await read_value('SELECT count(*) FROM exception_completion WHERE organization_id=:org',ids)==0


async def test_completion_sealed_membership_coverage_and_guarded_roundtrip(migrator_session,scope_seed,store):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine,text
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids);await confirm_grant({**ids,'scope':ids['other_scope']})
    async with client(ids) as api:
        _,_,_,_,case=await adopted_context(api,ids)
        result=await api.post('/api/exceptions/'+case['id']+'/completions',**command(await completion_preview(api,case)))
        assert result.status_code==201,result.text
    migration=importlib.import_module('migrations.versions.0110_exception_completions');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain completion'),db.begin_nested():migration.downgrade()
            # A copied parent cannot survive without its exact typed event and all members.
            with pytest.raises(DBAPIError,match='Complete immutable outcome receipt required'),db.begin_nested():
                db.execute(text("""INSERT INTO exception_completion SELECT (jsonb_populate_record(NULL::exception_completion,to_jsonb(c)||
                  jsonb_build_object('id',gen_random_uuid(),'request_key',gen_random_uuid()))).* FROM exception_completion c WHERE organization_id=:org"""),ids)
                db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            coverage=db.scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
            assert "'exception_completion'" in coverage and "'exception_completion_member'" in coverage
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE exception_completion CASCADE'))
                db.execute(text('TRUNCATE mapping_version CASCADE'))
                db.execute(text('TRUNCATE allocation_integrity_attestation CASCADE'))
                db.execute(text('TRUNCATE financial_computation_attestation CASCADE'))
                db.execute(text('TRUNCATE allocation_correction_context,allocation_correction_completion'))
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
                functions=['access_coverage_manifest(uuid,jsonb)','exception_case_access(uuid,uuid)','exception_case_work_access(uuid,uuid)','append_exception_event(uuid,uuid,uuid,uuid,text,text,timestamptz)']
                before={f:db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':f}) for f in functions}
                migration.downgrade();migration.upgrade()
                after={f:db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':f}) for f in functions}
                assert before==after and db.scalar(text('SELECT count(*) FROM exception_completion'))==0
            finally:point.rollback()
    finally:engine.dispose()


async def test_completion_races_new_raw_receipt_without_leaving_false_resolution(migrator_session,scope_seed,store,monkeypatch):
    import asyncio
    from tests.test_invoice_extraction_worker import pdf_bytes
    from tests.test_intake_batches import create
    data=pdf_bytes();monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:data)
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids);await confirm_grant({**ids,'scope':ids['other_scope']})
    async with client(ids) as api:
        _,_,_,_,case=await adopted_context(api,ids);url='/api/exceptions/'+case['id']
        body=command(await completion_preview(api,case));batch=await create(api,data)
        completion,receipt=await asyncio.gather(api.post(url+'/completions',**body),api.post('/api/intake-batches/'+batch['id']+'/items',
          files={'file':('copy.pdf',data,'application/pdf')},data={'client_item_key':'document-1'}))
        assert receipt.status_code==202,receipt.text
        assert completion.status_code in (201,409),completion.text
        detail=(await api.get(url)).json();assert detail['resolution_state']=='unresolved' and detail['workflow_state']=='open'
        assert not (await completion_preview(api,case))['can_complete']
        if completion.status_code==201:
            assert detail['completion_changed']
            assert (await api.post(url+'/completions',**body)).json()==completion.json()
        assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==2
