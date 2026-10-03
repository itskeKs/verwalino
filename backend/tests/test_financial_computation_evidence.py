"""Only actual engine writes receive private, transaction-bound computation proof."""
import uuid
import pytest
from tests.test_m3_settlement import m3_seed,_extras_for_exit  # noqa: F401
from tests.test_m4_weg import m4_seed,_create_and_finalize_plan  # noqa: F401
from tests.financial_coverage_helpers import attest_fixture,weg_values
from tests.test_financial_input_policy import draft
from tests.test_scope_payment_reconciliation import read_value

pytestmark=pytest.mark.asyncio


async def prepared_financial(api,seed,kind):
    if kind=='rental':
        target=await draft(api,seed,'1200.00');body=_extras_for_exit(seed)
        await attest_fixture(api,'settlement',target,{'advance_payments_total':'1200.00','advance_payments_source':'MANUAL'})
        return target,'/api/settlements/'+target,body,'settlement_run'
    endpoint={'plan':'/api/economic-plans','weg':'/api/weg-settlements','asset':'/api/weg-settlements/asset-reports'}[kind]
    data={'property_id':seed['property'],'period_start':'2025-01-01','period_end':'2025-12-31'}
    if kind=='plan':
        data['plan_year']=2025;body={'planned_costs':[],'reserve_contribution_total':'0.00'}
    elif kind=='weg':
        data['economic_plan_id']=await _create_and_finalize_plan(api,seed);body=weg_values(seed)
    else:
        body={'reserve_opening_balance':'0.00','reserve_withdrawals_total':'0.00','reserve_contributions_total':'0.00',
          'bank_balances':[{'bank_account_id':seed[key],'label':'Synthetic bank','account_type':account_type,'balance':'0.00'}
            for key,account_type in [('bank_giro','LAUFEND'),('bank_ruecklage','RUECKLAGE')]],'receivables':[],'payables':[]}
    response=await api.post(endpoint,json=data);assert response.status_code==201,response.text
    target=response.json()['id'];await attest_fixture(api,'economic_plan' if kind=='plan' else 'settlement',target,body)
    return target,endpoint+'/'+target,body,'economic_plan' if kind=='plan' else 'settlement_run'


@pytest.mark.parametrize('kind',['rental','plan','weg','asset'])
async def test_actual_compute_and_finalize_have_exact_distinct_receipts(client_a_admin,m3_seed,m4_seed,kind):
    seed=m3_seed if kind=='rental' else m4_seed
    target,url,body,table=await prepared_financial(client_a_admin,seed,kind)
    params={'org':seed['org_a_id'],'target':uuid.UUID(target),'kind':table}
    if kind=='rental':
        response=await client_a_admin.post(url+'/preview',json=body);assert response.status_code==200,response.text
        assert await read_value('SELECT count(*) FROM financial_computation_receipt WHERE organization_id=:org AND settlement_run_id=:target',params)==0
    for operation in ['compute','finalize']:
        response=await client_a_admin.post(url+'/'+operation,json=body);assert response.status_code==200,response.text
        receipt=await read_value('SELECT state FROM financial_computation_receipt WHERE organization_id=:org AND '+table+'_id=:target ORDER BY receipt_number DESC LIMIT 1',params)
        current=await read_value('SELECT financial_computation_state(:org,:kind,:target)',params)
        assert receipt==current and receipt['status']==('draft' if operation=='compute' else 'finalized')
        assert all(len(receipt[key])==64 for key in ['parent_digest','lines_digest','coverage_digest','source_digest'])
    assert await read_value('SELECT count(*) FROM financial_computation_receipt WHERE organization_id=:org AND '+table+'_id=:target',params)==2


@pytest.mark.parametrize('changed',['snapshot','line','source','coverage'])
async def test_changed_persisted_state_after_trusted_attestation_is_not_consumed(client_a_admin,m4_seed,monkeypatch,changed):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession
    target,url,body,table=await prepared_financial(client_a_admin,m4_seed,'plan')
    params={'org':m4_seed['org_a_id'],'target':uuid.UUID(target)}
    scalar=AsyncSession.scalar;injected=False
    async def alter(self,statement,*args,**kwargs):
        nonlocal injected
        if str(statement).startswith('SELECT record_financial_computation(') and not injected:
            injected=True
            sql={
              'snapshot':"UPDATE economic_plan SET snapshot_payload=jsonb_set(snapshot_payload,'{unexpected}', 'true') WHERE organization_id=:org AND id=:target",
              'line':"UPDATE economic_plan_line SET planned_amount=planned_amount+1 WHERE organization_id=:org AND economic_plan_id=:target",
              'source':"UPDATE property SET name=name||' Changed' WHERE organization_id=:org AND id=(SELECT property_id FROM economic_plan WHERE organization_id=:org AND id=:target)",
              'coverage':"INSERT INTO coverage_declaration(organization_id,economic_plan_id,property_id,period_start,period_end,component,revision,state,values,source_document_ids,source_revision,reason,attestor_id) SELECT organization_id,economic_plan_id,property_id,period_start,period_end,component,revision+1,state,values,source_document_ids,source_revision,reason,attestor_id FROM coverage_declaration WHERE organization_id=:org AND economic_plan_id=:target ORDER BY revision DESC LIMIT 1",
            }[changed]
            await self.execute(text(sql),params)
        return await scalar(self,statement,*args,**kwargs)
    monkeypatch.setattr(AsyncSession,'scalar',alter)
    response=await client_a_admin.post(url+'/compute',json=body)
    assert response.status_code in (409,422),response.text
    assert injected
    assert await read_value('SELECT count(*) FROM financial_computation_receipt WHERE organization_id=:org AND economic_plan_id=:target',params)==0
    assert await read_value('SELECT snapshot_payload FROM economic_plan WHERE organization_id=:org AND id=:target',params) is None


async def test_deferred_seal_rolls_back_changes_after_consumption(client_a_admin,m4_seed,monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    import app.services.economic_plan_service as service
    original=service.record_computation
    async def rewrite(session,org,actor,subject,operation,basis):
        receipt=await original(session,org,actor,subject,operation,basis)
        await session.execute(text("UPDATE economic_plan SET snapshot_payload=jsonb_set(snapshot_payload,'{forged}', 'true') WHERE organization_id=:org AND id=:target"),{'org':org,'target':subject.id})
        return receipt
    target,url,body,_=await prepared_financial(client_a_admin,m4_seed,'plan')
    monkeypatch.setattr(service,'record_computation',rewrite)
    with pytest.raises(DBAPIError,match='Computation changed before commit'):
        await client_a_admin.post(url+'/compute',json=body)
    params={'org':m4_seed['org_a_id'],'target':uuid.UUID(target)}
    assert await read_value('SELECT count(*) FROM financial_computation_receipt WHERE organization_id=:org AND economic_plan_id=:target',params)==0
    assert await read_value('SELECT snapshot_payload FROM economic_plan WHERE organization_id=:org AND id=:target',params) is None


async def test_private_proof_requires_exact_role_actor_and_transaction(client_a_admin,m4_seed):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_session_factory,get_worker_session_factory,get_review_session_factory,set_tenant_context
    target,url,body,_=await prepared_financial(client_a_admin,m4_seed,'plan')
    result=await client_a_admin.post(url+'/compute',json=body);assert result.status_code==200,result.text
    params={'org':m4_seed['org_a_id'],'target':uuid.UUID(target)}
    proof=await read_value('SELECT attestation_id FROM financial_computation_receipt WHERE organization_id=:org AND economic_plan_id=:target',params)
    async def principal(db):
        await set_tenant_context(db,params['org'])
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(m4_seed['user_a_admin_id'])})
    for factory in [get_worker_session_factory(),get_review_session_factory()]:
        with pytest.raises(DBAPIError,match='Interactive computation|permission denied'):
            async with factory() as db,db.begin():
                await principal(db)
                await db.execute(text("SELECT prepare_financial_computation(:org,'economic_plan',:target,'compute')"),params)
    async with get_session_factory()() as db,db.begin():
        await principal(db)
        for statement,args in [
            ('SELECT * FROM financial_computation_receipt',params),
            ('SELECT * FROM financial_computation_attestation',params),
            ("SELECT attest_financial_computation(:org,'economic_plan',:target,'compute','{}',pg_backend_pid(),pg_current_xact_id()::text)",params),
            ('SELECT record_financial_computation(:org,:proof)',{**params,'proof':proof}),
            ('SELECT record_financial_computation(:org,:proof)',{**params,'proof':uuid.uuid4()}),
        ]:
            with pytest.raises(DBAPIError):
                async with db.begin_nested():await db.execute(text(statement),args)
    assert await read_value('SELECT count(*) FROM financial_computation_receipt WHERE organization_id=:org AND economic_plan_id=:target',params)==1


async def test_contact_aba_cannot_change_names_during_trusted_computation(client_a_admin,m4_seed,monkeypatch):
    import asyncio
    from sqlalchemy import text
    from app.core.db import get_session_factory,set_tenant_context
    import app.services.economic_plan_service as service
    target,url,body,_=await prepared_financial(client_a_admin,m4_seed,'plan')
    params={'org':m4_seed['org_a_id'],'target':uuid.UUID(target),'owner':uuid.UUID(m4_seed['owner_a'])}
    person=await read_value('SELECT person_id FROM owner_profile WHERE organization_id=:org AND id=:owner',params)
    original_name=await read_value('SELECT first_name FROM person WHERE organization_id=:org AND id=:person',{**params,'person':person})
    names_loaded=asyncio.Event();release=asyncio.Event();writer_started=asyncio.Event();writer_pid=None
    original=service.resolve_wirtschaftsplan_values
    async def pause(*args,**kwargs):
        value=await original(*args,**kwargs);names_loaded.set();await asyncio.wait_for(release.wait(),5);return value
    monkeypatch.setattr(service,'resolve_wirtschaftsplan_values',pause)
    async def edit_twice():
        nonlocal writer_pid
        for name in ('Synthetic intervening identity',original_name):
            async with get_session_factory()() as db,db.begin():
                await set_tenant_context(db,params['org'])
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(m4_seed['user_a_admin_id'])})
                writer_pid=await db.scalar(text('SELECT pg_backend_pid()'));writer_started.set()
                # Raw app_rw can preserve updated_at. Row serialization must
                # prevent even this otherwise hash-identical A->B->A sequence.
                await db.execute(text('UPDATE person SET first_name=:name WHERE organization_id=:org AND id=:person'),{**params,'person':person,'name':name})
    computation=asyncio.create_task(client_a_admin.post(url+'/finalize',json=body))
    await asyncio.wait_for(names_loaded.wait(),5);editor=asyncio.create_task(edit_twice())
    try:
        await asyncio.wait_for(writer_started.wait(),5)
        for _ in range(100):
            blocked=await read_value("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid",{'pid':writer_pid})
            if blocked:break
            await asyncio.sleep(.01)
        assert blocked and not editor.done()
    finally:release.set()
    response=await asyncio.wait_for(computation,5);assert response.status_code==200,response.text
    await asyncio.wait_for(editor,5)
    receipt=await read_value('SELECT state FROM financial_computation_receipt WHERE organization_id=:org AND economic_plan_id=:target',params)
    assert receipt==await read_value("SELECT financial_computation_state(:org,'economic_plan',:target)",params)
    payload=await read_value('SELECT snapshot_payload FROM economic_plan WHERE organization_id=:org AND id=:target',params)
    assert 'Synthetic intervening identity' not in str(payload)


async def test_newer_other_parent_type_cannot_suppress_deferred_seal(client_a_admin,m3_seed,m4_seed,monkeypatch):
    from decimal import Decimal
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from tests.test_scoped_account_statements import mutate
    import app.services.settlement_service as rental
    from app.services.economic_plan_service import EconomicPlanService,PlanInputs
    target,url,body,_=await prepared_financial(client_a_admin,m3_seed,'rental')
    params={'org':m3_seed['org_a_id'],'target':uuid.UUID(target),'property':uuid.UUID(m4_seed['property'])}
    await mutate(text("INSERT INTO economic_plan(id,organization_id,property_id,plan_year,period_start,period_end,status,engine_version) VALUES(:target,:org,:property,2025,'2025-01-01','2025-12-31','draft','m4.1.0')"),params)
    await attest_fixture(client_a_admin,'economic_plan',target,{'planned_costs':[],'reserve_contribution_total':'0.00'})
    original=rental.record_computation
    async def second_parent(session,org,actor,subject,operation,basis):
        receipt=await original(session,org,actor,subject,operation,basis)
        await session.execute(text("UPDATE settlement_run SET snapshot_payload=jsonb_set(snapshot_payload,'{forged}', 'true') WHERE organization_id=:org AND id=:target"),params)
        await EconomicPlanService(session,org,actor).compute(subject.id,PlanInputs(planned_costs=[],reserve_contribution_total=Decimal('0')))
        return receipt
    monkeypatch.setattr(rental,'record_computation',second_parent)
    with pytest.raises(DBAPIError,match='Computation changed before commit'):
        await client_a_admin.post(url+'/compute',json=body)
    assert await read_value('SELECT count(*) FROM financial_computation_receipt WHERE organization_id=:org AND COALESCE(settlement_run_id,economic_plan_id)=:target',params)==0


@pytest.mark.parametrize('changed',['output','source'])
async def test_forcing_constraints_early_cannot_bypass_later_mutation_seal(client_a_admin,m4_seed,monkeypatch,changed):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    import app.services.economic_plan_service as service
    original=service.record_computation
    async def rewrite(session,org,actor,subject,operation,basis):
        receipt=await original(session,org,actor,subject,operation,basis)
        await session.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        sql=("UPDATE economic_plan SET snapshot_payload=jsonb_set(snapshot_payload,'{forged}', 'true') WHERE organization_id=:org AND id=:target" if changed=='output'
            else "UPDATE property SET name=name||' Changed' WHERE organization_id=:org AND id=:property")
        await session.execute(text(sql),{'org':org,'target':subject.id,'property':subject.property_id})
        return receipt
    target,url,body,_=await prepared_financial(client_a_admin,m4_seed,'plan')
    monkeypatch.setattr(service,'record_computation',rewrite)
    with pytest.raises(DBAPIError,match='Computation changed before commit'):
        await client_a_admin.post(url+'/compute',json=body)
    params={'org':m4_seed['org_a_id'],'target':uuid.UUID(target)}
    assert await read_value('SELECT count(*) FROM financial_computation_receipt WHERE organization_id=:org AND economic_plan_id=:target',params)==0


@pytest.mark.parametrize("changed",["actor","tenant","parent","operation","pid","transaction","expired"])
async def test_mismatched_or_expired_trusted_proof_is_not_consumed(client_a_admin,m4_seed,monkeypatch,changed):
    import json
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.test_scoped_account_statements import mutate
    target,url,body,_=await prepared_financial(client_a_admin,m4_seed,"plan")
    params={"org":m4_seed["org_a_id"],"target":uuid.UUID(target)}
    original=AsyncSession.scalar;injected=False
    async def substitute(self,statement,*args,**kwargs):
        nonlocal injected
        if str(statement).startswith("SELECT attest_financial_computation("):
            injected=True;values=dict(args[0]);state=json.loads(values["state"])
            if changed=="actor":
                await self.execute(text("SELECT set_config(\u0027app.current_user_id\u0027,:actor,true)"),{"actor":str(m4_seed["user_a_clerk_id"])})
            elif changed=="tenant":values["org"]=m4_seed["org_b_id"]
            elif changed=="parent":values["target"]=uuid.uuid4();state["subject_id"]=str(values["target"])
            elif changed=="operation":values["operation"]="finalize";state["status"]="finalized"
            elif changed=="pid":values["pid"]+=1
            elif changed=="transaction":values["transaction"]="1"
            values["state"]=json.dumps(state)
            args=(values,*args[1:])
        return await original(self,statement,*args,**kwargs)
    monkeypatch.setattr(AsyncSession,"scalar",substitute)
    if changed=="expired":
        await mutate(text("ALTER TABLE financial_computation_attestation ALTER COLUMN expires_at SET DEFAULT clock_timestamp()-interval \u00271 second\u0027"),{})
    try:
        response=await client_a_admin.post(url+"/compute",json=body)
        assert response.status_code in (404,409,422),response.text
        assert injected
        assert await read_value("SELECT count(*) FROM financial_computation_receipt WHERE organization_id=:org AND economic_plan_id=:target",params)==0
        assert await read_value("SELECT snapshot_payload FROM economic_plan WHERE organization_id=:org AND id=:target",params) is None
    finally:
        if changed=="expired":
            await mutate(text("ALTER TABLE financial_computation_attestation ALTER COLUMN expires_at SET DEFAULT clock_timestamp()+interval \u00272 minutes\u0027"),{})


async def test_computation_evidence_is_immutable_manifested_and_roundtrips(client_a_admin,m4_seed):
    import importlib
    from sqlalchemy import create_engine,text
    from sqlalchemy.exc import DBAPIError
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_access_coverage import capture
    target,url,body,_=await prepared_financial(client_a_admin,m4_seed,"plan")
    before=await capture(client_a_admin)
    response=await client_a_admin.post(url+"/compute",json=body);assert response.status_code==200,response.text
    assert (await capture(client_a_admin))["manifest_digest"]!=before["manifest_digest"]
    migration=importlib.import_module("migrations.versions.0115_financial_computation_evidence")
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            for table in ("financial_computation_attestation","financial_computation_receipt"):
                with pytest.raises(DBAPIError,match="immutable|Immutable"),db.begin_nested():
                    db.execute(text("DELETE FROM "+table+" WHERE organization_id=:org"),{"org":m4_seed["org_a_id"]})
            followup=importlib.import_module('migrations.versions.0116_correction_followup_completions')
            with pytest.raises(DBAPIError,match="Retain trusted"),db.begin_nested():
                db.execute(text('TRUNCATE allocation_correction_context,allocation_correction_completion'))
                followup.downgrade()
                migration.downgrade()
            signature="access_coverage_manifest(uuid,jsonb)"
            definition=db.scalar(text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),{"signature":signature})
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE allocation_correction_context,allocation_correction_completion'))
                followup.downgrade()
                db.execute(text("TRUNCATE financial_computation_attestation CASCADE"))
                migration.downgrade();migration.upgrade()
                followup.upgrade()
                assert definition==db.scalar(text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),{"signature":signature})
                for role in ("app_rw","app_worker","app_review"):
                    assert not db.scalar(text("SELECT has_function_privilege(:role,\u0027financial_computation_state(uuid,text,uuid)\u0027,\u0027EXECUTE\u0027)"),{"role":role})
            finally:point.rollback()
    finally:engine.dispose()


async def test_busy_contact_source_refuses_computation_without_waiting(client_a_admin,m4_seed):
    import asyncio
    from sqlalchemy import text
    from app.core.db import get_session_factory,set_tenant_context
    target,url,body,_=await prepared_financial(client_a_admin,m4_seed,"plan")
    params={"org":m4_seed["org_a_id"],"owner":uuid.UUID(m4_seed["owner_a"]),"target":uuid.UUID(target)}
    async with get_session_factory()() as db,db.begin():
        await set_tenant_context(db,params["org"])
        await db.execute(text("SELECT set_config(\u0027app.current_user_id\u0027,:actor,true)"),{"actor":str(m4_seed["user_a_admin_id"])})
        await db.execute(text("SELECT id FROM person WHERE organization_id=:org AND id=(SELECT person_id FROM owner_profile WHERE organization_id=:org AND id=:owner) FOR UPDATE"),params)
        response=await asyncio.wait_for(client_a_admin.post(url+"/compute",json=body),3)
        assert response.status_code==409,response.text
    assert await read_value("SELECT count(*) FROM financial_computation_receipt WHERE organization_id=:org AND economic_plan_id=:target",params)==0
