"""Continue immutable financial corrections with a reviewed linked-plan successor."""
import uuid
import pytest
from sqlalchemy import text
from tests.test_m4_weg import m4_seed,_create_and_finalize_plan  # noqa: F401
from tests.test_financial_computation_evidence import prepared_financial
from tests.financial_coverage_helpers import attest_fixture
from tests.test_scope_payment_reconciliation import read_value
from tests.test_scoped_account_statements import mutate
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import fixture,store  # noqa: F401

pytestmark=pytest.mark.asyncio


async def plan_successor(api,original,finalize=True):
    response=await api.post('/api/economic-plans/'+original+'/correction')
    assert response.status_code==201,response.text
    successor=response.json()['id']
    if finalize:
        values={'planned_costs':[],'reserve_contribution_total':'0.00'}
        await attest_fixture(api,'economic_plan',successor,values)
        response=await api.post('/api/economic-plans/'+successor+'/finalize',json=values)
        assert response.status_code==200,response.text
    return successor


async def test_reviewed_plan_successor_invalidates_draft_and_retry_preserves_later_compute(client_a_admin,m4_seed):
    api=client_a_admin
    target,url,values,_=await prepared_financial(api,m4_seed,'weg')
    computed=await api.post(url+'/compute',json=values);assert computed.status_code==200,computed.text
    original=computed.json()['economic_plan_id']
    successor=await plan_successor(api,original)
    preview=await api.get(url+'/economic-plan-successor');assert preview.status_code==200,preview.text
    assert preview.json()['current_plan_id']==original and preview.json()['successor_id']==successor
    body={'expected_plan_id':original,'successor_id':successor,'confirmed':True,'reason':'Reviewed corrected plan obligations'}
    refused=await api.patch(url+'/economic-plan',json={**body,'confirmed':False});assert refused.status_code==422
    applied=await api.patch(url+'/economic-plan',json=body);assert applied.status_code==200,applied.text
    assert applied.json()['economic_plan_id']==successor and applied.json()['lines']==[] and applied.json()['total_cost'] is None
    stale=await api.post(url+'/compute',json=values);assert stale.status_code==422,stale.text
    await attest_fixture(api,'settlement',target,values)
    computed=await api.post(url+'/compute',json=values);assert computed.status_code==200,computed.text
    repeat=await api.patch(url+'/economic-plan',json=body);assert repeat.status_code==200,repeat.text
    assert repeat.json()==computed.json()
    params={'org':m4_seed['org_a_id'],'target':uuid.UUID(target)}
    assert await read_value("SELECT count(*) FROM audit_event WHERE organization_id=:org AND entity_id=:target AND action='settlement.plan_successor_selected'",params)==1


async def test_finalized_weg_correction_keeps_original_hash_and_uses_new_plan(client_a_admin,m4_seed):
    api=client_a_admin;target,url,values,_=await prepared_financial(api,m4_seed,'weg')
    original=await api.post(url+'/finalize',json=values);assert original.status_code==200,original.text
    original=original.json();successor=await plan_successor(api,original['economic_plan_id'])
    replacement=await api.post('/api/settlements/'+target+'/correction');assert replacement.status_code==201,replacement.text
    replacement=replacement.json()['id'];new_url='/api/weg-settlements/'+replacement
    result=await api.patch(new_url+'/economic-plan',json={'expected_plan_id':original['economic_plan_id'],'successor_id':successor,'confirmed':True,'reason':'Finalize corrected obligations'})
    assert result.status_code==200,result.text
    await attest_fixture(api,'settlement',replacement,values)
    result=await api.post(new_url+'/finalize',json=values);assert result.status_code==200,result.text
    retained=(await api.get(url)).json()
    assert retained['snapshot_sha256']==original['snapshot_sha256'] and retained['lines']==original['lines']
    assert retained['superseded_by_id']==replacement
    assert (await api.post('/api/settlements/'+target+'/correction')).status_code==409


@pytest.mark.parametrize('case',['draft_plan','changed_context','unrelated','nonterminal','cycle','finalized_run','wrong_family'])
async def test_plan_selection_rejects_invalid_successors(client_a_admin,m4_seed,case):
    api=client_a_admin;target,url,values,_=await prepared_financial(api,m4_seed,'asset' if case=='wrong_family' else 'weg')
    original=(await api.get('/api/weg-settlements/'+target)).json()['economic_plan_id']
    if case=='wrong_family':
        original=await _create_and_finalize_plan(api,m4_seed)
        url='/api/weg-settlements/'+target
    if case=='finalized_run':
        result=await api.post(url+'/finalize',json=values);assert result.status_code==200,result.text
    successor=await plan_successor(api,original,finalize=case not in ('draft_plan','changed_context'))
    params={'org':m4_seed['org_a_id'],'original':uuid.UUID(original),'successor':uuid.UUID(successor)}
    if case=='changed_context':
        await mutate(text('UPDATE economic_plan SET monthly_due_day=17 WHERE id=:successor'),params)
        plan_values={'planned_costs':[],'reserve_contribution_total':'0.00'}
        await attest_fixture(api,'economic_plan',successor,plan_values)
        result=await api.post('/api/economic-plans/'+successor+'/finalize',json=plan_values);assert result.status_code==200,result.text
    elif case=='unrelated':successor=await _create_and_finalize_plan(api,m4_seed)
    elif case=='nonterminal':await plan_successor(api,successor)
    elif case=='cycle':await mutate(text('UPDATE economic_plan SET superseded_by_id=:original WHERE id=:successor'),params)
    response=await api.patch(url+'/economic-plan',json={'expected_plan_id':original,'successor_id':successor,'confirmed':True,'reason':'Invalid fixture selection'})
    assert response.status_code==(404 if case=='wrong_family' else 409),response.text
    retained=(await api.get('/api/weg-settlements/'+target)).json()
    assert retained['economic_plan_id']==(None if case=='wrong_family' else original)


async def test_plan_selection_concurrent_replay_tenant_denial_and_rollback(client_a_admin,client_b_admin,m4_seed,monkeypatch):
    import asyncio
    from app.repositories.audit_repo import AuditRepository
    api=client_a_admin;target,url,values,_=await prepared_financial(api,m4_seed,'weg')
    computed=await api.post(url+'/compute',json=values);assert computed.status_code==200,computed.text
    original=computed.json()['economic_plan_id'];successor=await plan_successor(api,original)
    body={'expected_plan_id':original,'successor_id':successor,'confirmed':True,'reason':'Concurrent explicit plan review'}
    assert (await client_b_admin.get(url+'/economic-plan-successor')).status_code==404
    assert (await client_b_admin.patch(url+'/economic-plan',json=body)).status_code==404
    audit_create=AuditRepository.create
    async def fail_after_audit(self,event):
        result=await audit_create(self,event)
        if event.action=='settlement.plan_successor_selected':raise RuntimeError('Synthetic transition rollback')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(AuditRepository,'create',fail_after_audit)
        with pytest.raises(RuntimeError,match='Synthetic transition rollback'):
            await api.patch(url+'/economic-plan',json=body)
    assert (await api.get(url)).json()==computed.json()
    first,second=await asyncio.gather(api.patch(url+'/economic-plan',json=body),api.patch(url+'/economic-plan',json=body))
    assert first.status_code==second.status_code==200,(first.text,second.text)
    assert first.json()==second.json()
    assert await read_value("SELECT count(*) FROM audit_event WHERE organization_id=:org AND entity_id=:target AND action='settlement.plan_successor_selected'",{'org':m4_seed['org_a_id'],'target':uuid.UUID(target)})==1


async def test_scoped_plan_selection_rechecks_live_authority(migrator_session,scope_seed,store):
    from tests.test_allocation_correction_preview import parent
    from tests.test_allocation_adoptions import confirm_grant
    from tests.test_scoped_account_statements import client
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    await mutate(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,bank_account_id,origin,reason,reviewed_by) VALUES(:org,:property,:scope,:bank,'REVIEWED','Exact account',:actor)"),ids)
    original=await parent(ids,kind='economic_plan');successor=await parent(ids,kind='economic_plan')
    target=await parent(ids)
    params={**ids,'original':original,'successor':successor,'target':target}
    await mutate(text("UPDATE economic_plan SET status='finalized',snapshot_payload='{}',snapshot_sha256=:hash,finalized_at=clock_timestamp(),finalized_by=:actor WHERE id IN (:original,:successor)"),{**params,'hash':'c'*64})
    await mutate(text('UPDATE settlement_run SET economic_plan_id=:original WHERE id=:target'),params)
    await mutate(text('UPDATE economic_plan SET superseded_by_id=:successor WHERE id=:original'),params)
    url='/api/weg-settlements/'+str(target)
    body={'expected_plan_id':str(original),'successor_id':str(successor),'confirmed':True,'reason':'Reviewed scoped plan'}
    async with client(ids) as api:
        preview=await api.get(url+'/economic-plan-successor');assert preview.status_code==200,preview.text
        applied=await api.patch(url+'/economic-plan',json=body);assert applied.status_code==200,applied.text
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'),ids)
        assert (await api.get(url+'/economic-plan-successor')).status_code==404
        assert (await api.patch(url+'/economic-plan',json=body)).status_code==404


async def test_plan_chain_bound_and_inaccessible_middle_hop_fail_closed(client_a_admin,m4_seed,monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    from app.repositories.economic_plan_repo import EconomicPlanRepository
    api=client_a_admin;target,url,_,_=await prepared_financial(api,m4_seed,'weg')
    original=(await api.get(url)).json()['economic_plan_id']
    middle=await plan_successor(api,original);await plan_successor(api,middle)
    original_get=EconomicPlanRepository.get_by_id
    async def hidden_middle(self,target):
        return None if str(target)==middle else await original_get(self,target)
    with monkeypatch.context() as patch:
        patch.setattr(EconomicPlanRepository,'get_by_id',hidden_middle)
        assert (await api.get(url+'/economic-plan-successor')).status_code==404
    # Privileged malformed legacy fixture: 101 frozen hops, no unbounded walk.
    ids=[uuid.uuid4() for _ in range(100)]
    engine=create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as db:
            for index in range(99,-1,-1):
                await db.execute(text("""INSERT INTO economic_plan SELECT (jsonb_populate_record(NULL::economic_plan,to_jsonb(p)||
                  jsonb_build_object('id',CAST(:id AS uuid),'superseded_by_id',CAST(:next AS uuid)))).*
                  FROM economic_plan p WHERE id=:original"""),{'id':ids[index],'next':ids[index+1] if index<99 else None,'original':uuid.UUID(original)})
            # Replace the terminal pointer, preserving the finalized fixture rows.
            await db.execute(text('UPDATE economic_plan SET superseded_by_id=:first WHERE id=:original'),{'first':ids[0],'original':uuid.UUID(original)})
    finally:await engine.dispose()
    response=await api.get(url+'/economic-plan-successor');assert response.status_code==409,response.text
