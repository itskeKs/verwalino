"""Correction impacts close only against current actual financial processing."""
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_allocation_adoptions import confirm_grant
from tests.test_allocation_correction_preview import prepared,preview_url
from tests.test_allocation_corrections import command

pytestmark=pytest.mark.asyncio


@pytest.mark.parametrize('case',['valid','draft','context','scope','unlinked','nonterminal','revoked'])
async def test_linked_plan_context_accepts_only_accessible_final_same_context_head(migrator_session,scope_seed,store,case):
    from tests.test_allocation_correction_preview import parent
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    original=await parent(ids,kind='economic_plan')
    successor=await parent(ids,kind='economic_plan')
    params={**ids,'original':original,'successor':successor}
    if case=='context':
        await mutate(text('UPDATE economic_plan SET monthly_due_day=15 WHERE id=:successor'),params)
    for target in [original,successor]:
        if case=='draft' and target==successor:continue
        await mutate(text("UPDATE economic_plan SET status='finalized',snapshot_payload='{}',snapshot_sha256=:hash,finalized_at=clock_timestamp(),finalized_by=:actor WHERE id=:target"),{**params,'target':target,'hash':'d'*64})
    if case!='unlinked':
        await mutate(text('UPDATE economic_plan SET superseded_by_id=:successor WHERE id=:original'),params)
    if case=='nonterminal':
        third=await parent(ids,kind='economic_plan')
        await mutate(text('UPDATE economic_plan SET superseded_by_id=:third WHERE id=:successor'),{**params,'third':third})
    if case=='revoked':
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'),ids)
    engine=create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as db:
            await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),{'org':str(ids['org']),'actor':str(ids['clerk'])})
            accepted=await db.scalar(text("""SELECT correction_context_matches(:org,
              jsonb_build_object('property_id',CAST(:property AS uuid),'economic_plan_id',CAST(:original AS uuid)),
              jsonb_build_object('property_id',CAST(:property AS uuid),'economic_plan_id',CAST(:successor AS uuid)),:scope)"""),{**params,'scope':ids['other_scope'] if case=='scope' else ids['scope']})
            assert accepted is (case=='valid')
    finally:await engine.dispose()


async def declare(api,target,values,source,reason='Review current source and full synthetic report inputs'):
    result=await api.post('/api/financial-input-coverage',json={'subject_kind':'settlement','subject_id':str(target),
      'values':values,'confirmed_complete':True,'reason':reason,'source_document_ids':[source]})
    assert result.status_code==201,result.text
    return result.json()


async def corrected_report(api,ids,finalized=False,extra_parent=False):
    old,allocation,review,_=await prepared(api,ids)
    await mutate(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,bank_account_id,origin,reason,reviewed_by) VALUES(:org,:property,:scope,:bank,'REVIEWED','Exact report account',:actor)"),ids)
    await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'),ids)
    await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:scope,:clerk,ARRAY['read','prepare','approve','confirm','export'],:actor,'Review report finalization')"),ids)
    created=await api.post('/api/weg-settlements/asset-reports',json={'property_id':str(ids['property']),
      'accounting_scope_id':str(ids['scope']),'period_start':'2026-01-01','period_end':'2026-12-31'})
    assert created.status_code==201,created.text
    target=created.json()['id'];url='/api/weg-settlements/asset-reports/'+target
    values={'reserve_opening_balance':'0.00','reserve_withdrawals_total':'0.00','reserve_contributions_total':'0.00',
      'bank_balances':[{'bank_account_id':str(ids['bank']),'label':'Synthetic account','account_type':'LAUFEND','balance':'0.00'}],
      'receivables':[],'payables':[]}
    await declare(api,target,values,old['source_document_id'])
    if extra_parent:
        from tests.test_allocation_correction_preview import parent,coverage
        ids['unproven']=await parent(ids,kind='economic_plan')
        await coverage(ids,ids['unproven'],kind='economic_plan',source=old['source_document_id'])
    if finalized:
        result=await api.post(url+'/finalize',json=values);assert result.status_code==200,result.text
    preview=await api.get(preview_url(old,review));assert preview.status_code==200 and preview.json()['can_review'],preview.text
    result=await api.post('/api/allocation-sets/'+old['id']+'/corrections',**command(old,review,preview.json()))
    assert result.status_code==201,result.text
    result=result.json();impact=next(i for i in result['receipt']['impacts'] if i['snapshot']['id']==target)
    return target,url,values,result,impact


async def test_draft_followup_requires_current_compute_and_explicit_source_review(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        target,url,values,correction,impact=await corrected_report(api,ids)
        status_url='/api/allocation-correction-impacts/'+impact['id']+'/follow-up'
        result=await api.get(status_url);assert result.status_code==200,result.text
        assert result.json()['status']=='open' and not result.json()['ready']
        options=await api.get('/api/financial-input-coverage/source-options',params={'subject_kind':'settlement','subject_id':target})
        assert options.status_code==200,options.text
        assert [item['id'] for item in options.json()['items']]==[correction['allocation']['source_document_id']]
        await declare(api,target,values,correction['allocation']['source_document_id'])
        computed=await api.post(url+'/compute',json=values);assert computed.status_code==200,computed.text
        result=await api.get(status_url);assert result.status_code==200,result.text
        status=result.json();assert status['ready'] and status['source_review_required'],status
        body={'preview_digest':status['preview_digest'],'reason':'Current report inputs and corrected source reviewed','source_reviewed':False,'confirmed':True}
        endpoint='/api/allocation-correction-impacts/'+impact['id']+'/completions'
        headers={'Idempotency-Key':str(uuid.uuid4())}
        refused=await api.post(endpoint,json=body,headers=headers);assert refused.status_code==422,refused.text
        body['source_reviewed']=True
        saved=await api.post(endpoint,json=body,headers=headers);assert saved.status_code==201,saved.text
        assert (await api.get(status_url)).json()['status']=='completed'
        assert (await api.post(endpoint,json=body,headers=headers)).json()==saved.json()
        await declare(api,target,values,correction['allocation']['source_document_id'],reason='Renewed review after completion')
        assert (await api.get(status_url)).json()['status']=='changed'
        assert (await api.post(endpoint,json=body,headers=headers)).json()==saved.json()
        assert await read_value('SELECT count(*) FROM allocation_correction_completion WHERE organization_id=:org',ids)==1


@pytest.mark.parametrize('initially_finalized',[True,False])
async def test_followup_requires_finalized_successor_and_preserved_context(migrator_session,scope_seed,store,initially_finalized):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        target,url,values,correction,impact=await corrected_report(api,ids,finalized=initially_finalized)
        if not initially_finalized:
            await declare(api,target,values,correction['allocation']['source_document_id'])
            result=await api.post(url+'/finalize',json=values);assert result.status_code==200,result.text
        status_url='/api/allocation-correction-impacts/'+impact['id']+'/follow-up'
        result=await api.post('/api/settlements/'+target+'/correction');assert result.status_code==201,result.text
        replacement=result.json()['id'];new_url='/api/weg-settlements/asset-reports/'+replacement
        status=await api.get(status_url);assert status.status_code==200,status.text
        assert not status.json()['ready']
        await declare(api,replacement,values,correction['allocation']['source_document_id'])
        result=await api.post(new_url+'/compute',json=values);assert result.status_code==200,result.text
        assert not (await api.get(status_url)).json()['ready']
        result=await api.post(new_url+'/finalize',json=values);assert result.status_code==200,result.text
        status=(await api.get(status_url)).json();assert status['ready'] and status['target_id']==replacement,status
        saved=await api.post('/api/allocation-correction-impacts/'+impact['id']+'/completions',
          headers={'Idempotency-Key':str(uuid.uuid4())},json={'preview_digest':status['preview_digest'],'reason':'Final successor verified',
            'source_reviewed':True,'confirmed':True})
        assert saved.status_code==201,saved.text
        assert (await api.get(status_url)).json()['status']=='completed'


@pytest.mark.parametrize('changed',['output','context','source','old_source_coverage'])
async def test_changed_or_wrong_evidence_cannot_complete_followup(migrator_session,scope_seed,store,changed):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        target,url,values,correction,impact=await corrected_report(api,ids)
        source=correction['allocation']['source_document_id']
        await declare(api,target,values,source)
        result=await api.post(url+'/compute',json=values);assert result.status_code==200,result.text
        status_url='/api/allocation-correction-impacts/'+impact['id']+'/follow-up'
        before=(await api.get(status_url)).json();assert before['ready'],before
        params={**ids,'target':uuid.UUID(target)}
        if changed=='output':
            await mutate(text("UPDATE settlement_run SET snapshot_payload=jsonb_set(snapshot_payload,'{forged}','true') WHERE organization_id=:org AND id=:target"),params)
        elif changed=='context':
            await mutate(text("UPDATE settlement_run SET period_end='2027-01-01' WHERE organization_id=:org AND id=:target"),params)
        elif changed=='source':
            await mutate(text("UPDATE property SET name=name||' changed' WHERE organization_id=:org AND id=:property"),ids)
        else:
            original=await read_value('SELECT source_document_id FROM allocation_set WHERE organization_id=:org AND id=:old',{**ids,'old':uuid.UUID(correction['receipt']['predecessor_id'])})
            await declare(api,target,values,str(original))
            result=await api.post(url+'/compute',json=values);assert result.status_code==200,result.text
        after=await api.get(status_url);assert after.status_code==200,after.text
        assert not after.json()['ready'],after.text
        result=await api.post('/api/allocation-correction-impacts/'+impact['id']+'/completions',headers={'Idempotency-Key':str(uuid.uuid4())},
          json={'preview_digest':before['preview_digest'],'reason':'Stale review','source_reviewed':True,'confirmed':True})
        assert result.status_code==409,result.text
        assert await read_value('SELECT count(*) FROM allocation_correction_completion WHERE organization_id=:org',ids)==0


async def test_followup_exact_concurrent_retry_and_live_grant_recheck(migrator_session,scope_seed,store,client_b_admin):
    import asyncio
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        target,url,values,correction,impact=await corrected_report(api,ids)
        await declare(api,target,values,correction['allocation']['source_document_id'])
        result=await api.post(url+'/compute',json=values);assert result.status_code==200,result.text
        status_url='/api/allocation-correction-impacts/'+impact['id']+'/follow-up'
        status=(await api.get(status_url)).json();assert status['ready'],status
        endpoint='/api/allocation-correction-impacts/'+impact['id']+'/completions'
        body={'preview_digest':status['preview_digest'],'reason':'Concurrent exact review','source_reviewed':True,'confirmed':True}
        headers={'Idempotency-Key':str(uuid.uuid4())}
        first,second=await asyncio.gather(api.post(endpoint,json=body,headers=headers),api.post(endpoint,json=body,headers=headers))
        assert first.status_code==second.status_code==201,(first.text,second.text)
        assert first.json()==second.json()
        assert (await api.post(endpoint,json={**body,'reason':'Conflicting reuse'},headers=headers)).status_code==409
        assert (await client_b_admin.get(status_url)).status_code==404
        assert (await client_b_admin.get('/api/financial-input-coverage/source-options',params={'subject_kind':'settlement','subject_id':target})).status_code==404
        assert (await client_b_admin.post(endpoint,json=body,headers=headers)).status_code==404
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'),ids)
        assert (await api.get(status_url)).status_code==404
        assert (await api.post(endpoint,json=body,headers=headers)).status_code==404
    assert await read_value('SELECT count(*) FROM allocation_correction_completion WHERE organization_id=:org',ids)==1


async def test_followup_context_backfill_two_orgs_without_tenant_guc_and_guarded_roundtrip(migrator_session,scope_seed,seed_data,store):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_scope_grants import scope_fixture
    # This historical backfill fixture starts before visual/replacement finance.
    # Isolate it from retained allocations created by earlier test modules.
    await mutate(text('TRUNCATE allocation_set CASCADE'),{})
    first=await scope_fixture(migrator_session,scope_seed)
    second_seed={**seed_data,'org_a_id':seed_data['org_b_id'],'user_a_admin_id':seed_data['user_b_admin_id'],'user_a_clerk_id':seed_data['user_b_admin_id']}
    second=await scope_fixture(migrator_session,second_seed)
    await migrator_session.commit()
    retained=[];unproven=[]
    for ids in [first,second]:
        await confirm_grant(ids)
        async with client(ids) as api:
            target,_,_,correction,impact=await corrected_report(api,ids,extra_parent=True)
            retained.append(uuid.UUID(impact['id']))
            unproven.extend(uuid.UUID(i['id']) for i in correction['receipt']['impacts'] if i['snapshot']['id']==str(ids['unproven']))
        await mutate(text("UPDATE economic_plan SET period_end='2027-01-01' WHERE organization_id=:org AND id=:unproven"),ids)
    engine=create_engine(MIGRATOR_SYNC_URL);migration=importlib.import_module('migrations.versions.0116_correction_followup_completions')
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain correction follow-up'),db.begin_nested():
                importlib.import_module('migrations.versions.0123_replacement_exception_outcomes').downgrade()
                importlib.import_module('migrations.versions.0122_allocation_source_replacements').downgrade()
                importlib.import_module('migrations.versions.0121_visual_allocation_sources').downgrade()
                db.execute(text('TRUNCATE visual_disclosure_attestation CASCADE'))
                db.execute(text('TRUNCATE allocation_source_claim CASCADE'))
                db.execute(text('TRUNCATE visual_extract_attestation CASCADE'))
                importlib.import_module('migrations.versions.0120_allocation_source_aliases').downgrade()
                importlib.import_module('migrations.versions.0119_allocation_source_claims').downgrade()
                importlib.import_module('migrations.versions.0118_visual_disclosures').downgrade()
                importlib.import_module('migrations.versions.0117_visual_extract_artifacts').downgrade()
                migration.downgrade()
            with pytest.raises(DBAPIError,match='immutable|Immutable'),db.begin_nested():
                db.execute(text('DELETE FROM allocation_correction_context WHERE impact_id=:id'),{'id':retained[0]})
            definition=db.scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
            point=db.begin_nested()
            try:
                db.execute(text("SELECT set_config('app.current_org_id','',true)"))
                db.execute(text('TRUNCATE allocation_correction_context,allocation_correction_completion'))
                importlib.import_module('migrations.versions.0123_replacement_exception_outcomes').downgrade()
                importlib.import_module('migrations.versions.0122_allocation_source_replacements').downgrade()
                importlib.import_module('migrations.versions.0121_visual_allocation_sources').downgrade()
                db.execute(text('TRUNCATE visual_disclosure_attestation CASCADE'))
                db.execute(text('TRUNCATE allocation_source_claim CASCADE'))
                db.execute(text('TRUNCATE visual_extract_attestation CASCADE'))
                importlib.import_module('migrations.versions.0120_allocation_source_aliases').downgrade()
                importlib.import_module('migrations.versions.0119_allocation_source_claims').downgrade()
                importlib.import_module('migrations.versions.0118_visual_disclosures').downgrade()
                importlib.import_module('migrations.versions.0117_visual_extract_artifacts').downgrade()
                migration.downgrade();migration.upgrade()
                importlib.import_module('migrations.versions.0117_visual_extract_artifacts').upgrade()
                importlib.import_module('migrations.versions.0118_visual_disclosures').upgrade()
                importlib.import_module('migrations.versions.0119_allocation_source_claims').upgrade()
                importlib.import_module('migrations.versions.0120_allocation_source_aliases').upgrade()
                importlib.import_module('migrations.versions.0121_visual_allocation_sources').upgrade()
                importlib.import_module('migrations.versions.0122_allocation_source_replacements').upgrade()
                importlib.import_module('migrations.versions.0123_replacement_exception_outcomes').upgrade()
                assert definition==db.scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
                assert db.scalar(text('SELECT count(*) FROM allocation_correction_context WHERE impact_id=ANY(:ids)'),{'ids':retained})==2
                assert db.scalar(text('SELECT count(*) FROM allocation_correction_context WHERE impact_id=ANY(:ids)'),{'ids':unproven})==0
                for role in ('app_rw','app_worker','app_review'):
                    assert not db.scalar(text("SELECT has_function_privilege(:role,'correction_followup_basis(uuid,uuid,boolean)','EXECUTE')"),{'role':role})
            finally:point.rollback()
    finally:engine.dispose()


async def test_followup_runtime_roles_and_rollback_after_completion(migrator_session,scope_seed,store,monkeypatch):
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_session_factory,get_worker_session_factory,get_review_session_factory,set_tenant_context
    from app.services.allocation_adoption_service import AllocationAdoptionService
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        target,url,values,correction,impact=await corrected_report(api,ids)
        await declare(api,target,values,correction['allocation']['source_document_id'])
        result=await api.post(url+'/compute',json=values);assert result.status_code==200,result.text
        status=(await api.get('/api/allocation-correction-impacts/'+impact['id']+'/follow-up')).json();assert status['ready'],status
        params={**ids,'impact':uuid.UUID(impact['id'])}
        for factory in [get_session_factory(),get_worker_session_factory(),get_review_session_factory()]:
            async with factory() as db,db.begin():
                await set_tenant_context(db,ids['org'])
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                statements=['SELECT * FROM allocation_correction_context','SELECT * FROM allocation_correction_completion',
                  'SELECT correction_followup_basis(:org,:impact,false)']
                if factory is not get_session_factory():statements.append('SELECT correction_followup_status(:org,:impact)')
                for statement in statements:
                    with pytest.raises(DBAPIError):
                        async with db.begin_nested():await db.execute(text(statement),params)
        original=AllocationAdoptionService.call
        async def fail_after_record(self,sql,**params):
            result=await original(self,sql,**params)
            if sql.startswith('SELECT complete_correction_followup('):raise RuntimeError('Synthetic failure after receipt')
            return result
        monkeypatch.setattr(AllocationAdoptionService,'call',fail_after_record)
        with pytest.raises(RuntimeError,match='Synthetic failure after receipt'):
            await api.post('/api/allocation-correction-impacts/'+impact['id']+'/completions',headers={'Idempotency-Key':str(uuid.uuid4())},
              json={'preview_digest':status['preview_digest'],'reason':'Rollback entire completion','source_reviewed':True,'confirmed':True})
    assert await read_value('SELECT count(*) FROM allocation_correction_completion WHERE organization_id=:org',ids)==0


@pytest.mark.parametrize('chain',['cycle','over_bound'])
async def test_incomplete_successor_chains_never_complete(migrator_session,scope_seed,store,chain):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        target,_,_,_,impact=await corrected_report(api,ids,finalized=True)
        params={**ids,'target':uuid.UUID(target)}
        if chain=='cycle':
            await mutate(text('UPDATE settlement_run SET superseded_by_id=id WHERE organization_id=:org AND id=:target'),params)
        else:
            # Typed same-context finalized fixture successors exceed the
            # supported traversal bound before a terminal receipt is trusted.
            successors=[uuid.uuid4() for _ in range(100)]
            from sqlalchemy.ext.asyncio import create_async_engine
            from tests.conftest import MIGRATOR_URL
            engine=create_async_engine(MIGRATOR_URL)
            try:
                async with engine.begin() as db:
                    for index,successor in enumerate(reversed(successors)):
                        next_id=successors[len(successors)-index] if index else None
                        await db.execute(text("""INSERT INTO settlement_run SELECT (jsonb_populate_record(NULL::settlement_run,to_jsonb(r)||
                          jsonb_build_object('id',CAST(:successor AS uuid),'superseded_by_id',CAST(:next AS uuid)))).*
                          FROM settlement_run r WHERE organization_id=:org AND id=:target"""),{**params,'successor':successor,'next':next_id})
                        await db.execute(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,settlement_run_id,origin,reviewed_by,reason) VALUES(:org,:property,:scope,:successor,'REVIEWED',:actor,'Bounded chain fixture')"),{**params,'successor':successor})
                    await db.execute(text('UPDATE settlement_run SET superseded_by_id=:next WHERE organization_id=:org AND id=:target'),{**params,'next':successors[0]})
            finally:await engine.dispose()
        result=await api.get('/api/allocation-correction-impacts/'+impact['id']+'/follow-up')
        assert result.status_code==422,result.text
    assert await read_value('SELECT count(*) FROM allocation_correction_completion WHERE organization_id=:org',ids)==0


async def test_new_allocation_head_changes_validity_without_rewriting_historical_completion(migrator_session,scope_seed,store):
    from tests.test_allocation_corrections import next_review
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        target,url,values,correction,impact=await corrected_report(api,ids)
        await declare(api,target,values,correction['allocation']['source_document_id'])
        result=await api.post(url+'/compute',json=values);assert result.status_code==200,result.text
        status_url='/api/allocation-correction-impacts/'+impact['id']+'/follow-up'
        status=(await api.get(status_url)).json();assert status['ready'],status
        endpoint='/api/allocation-correction-impacts/'+impact['id']+'/completions'
        body={'preview_digest':status['preview_digest'],'reason':'First exact completion','source_reviewed':True,'confirmed':True}
        headers={'Idempotency-Key':str(uuid.uuid4())}
        saved=await api.post(endpoint,json=body,headers=headers);assert saved.status_code==201,saved.text
        current=correction['allocation'];params={**ids,'set':uuid.UUID(current['id'])}
        allocation_id=await read_value('SELECT allocation_proposal_id FROM allocation_set WHERE organization_id=:org AND id=:set',params)
        review_id=await read_value('SELECT treatment_review_id FROM allocation_set WHERE organization_id=:org AND id=:set',params)
        logical=await read_value('SELECT logical_document_id FROM allocation_set WHERE organization_id=:org AND id=:set',params)
        allocations=(await api.get('/api/logical-documents/'+str(logical)+'/allocation-proposals')).json()['items']
        reviews=(await api.get('/api/logical-documents/'+str(logical)+'/allocation-treatments')).json()['items']
        allocation,review=await next_review(api,next(a for a in allocations if a['id']==str(allocation_id)),next(r for r in reviews if r['id']==str(review_id)))
        preview=(await api.get(preview_url(current,review))).json();assert preview['can_review'],preview
        newer=await api.post('/api/allocation-sets/'+current['id']+'/corrections',**command(current,review,preview));assert newer.status_code==201,newer.text
        assert (await api.get(status_url)).json()['status']=='changed'
        assert (await api.post(endpoint,json=body,headers=headers)).json()==saved.json()
        await declare(api,target,values,newer.json()['allocation']['source_document_id'])
        result=await api.post(url+'/compute',json=values);assert result.status_code==200,result.text
        status=(await api.get(status_url)).json();assert status['ready'] and status['status']=='changed',status
        second=await api.post(endpoint,json={**body,'preview_digest':status['preview_digest'],'reason':'Current later head reviewed'},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert second.status_code==201,second.text
        status=(await api.get(status_url)).json();assert status['status']=='completed' and len(status['history'])==2,status
