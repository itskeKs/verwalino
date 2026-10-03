"""Correction preview is complete, private, versioned evidence without money effects."""
import json
import uuid
from datetime import date
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_allocation_adoptions import ready,confirm_grant,command as adopt_command
from tests.test_allocation_proposals import command as allocation_command
from tests.test_allocation_treatments import command as treatment_command

pytestmark=pytest.mark.asyncio


async def prepared(api,ids,credit=False):
    child,allocation,review,preview,url=await ready(api,ids,credit=credit)
    result=await api.post(url,**adopt_command(review,preview));assert result.status_code==201,result.text
    old=result.json()
    rows=[{'cost_category_id':allocation['lines'][0]['cost_category_id'],'cost_category_plan_version':1,
      'description':'Corrected part '+str(index),'gross_amount':('-' if credit else '')+amount,'vat_amount':None}
      for index,amount in enumerate(['19.00','40.00','60.00'])]
    result=await api.post('/api/logical-documents/'+child['id']+'/allocation-proposals',
      **allocation_command({'id':allocation['context_decision_id']},rows,previous=allocation['id']))
    assert result.status_code==201,result.text
    replacement=result.json();preview=(await api.get('/api/allocation-proposals/'+replacement['id']+'/treatment-preview')).json()
    result=await api.post('/api/logical-documents/'+child['id']+'/allocation-treatments',**treatment_command(replacement,preview,previous=review['id']))
    assert result.status_code==201,result.text
    return old,replacement,result.json(),review


def preview_url(old,review):
    return '/api/allocation-sets/'+old['id']+'/correction-preview?treatment_review_id='+review['id']


async def parent(ids,*,kind='settlement_run',run_type='WEG_JAHRESABRECHNUNG',scope='scope',snapshot=None,start='2026-01-01',end='2026-12-31'):
    target=uuid.uuid4();start=date.fromisoformat(start);end=date.fromisoformat(end)
    if kind=='settlement_run':
        await mutate(text("""INSERT INTO settlement_run(id,organization_id,property_id,run_type,period_start,period_end,status,engine_version,advance_payments_source,snapshot_payload)
          VALUES(:target,:org,:property,:type,:start,:end,'draft','fixture','MANUAL',CAST(:snapshot AS jsonb))"""),
          {**ids,'target':target,'type':run_type,'start':start,'end':end,'snapshot':json.dumps(snapshot) if snapshot is not None else None})
    else:
        await mutate(text("""INSERT INTO economic_plan(id,organization_id,property_id,plan_year,period_start,period_end,status,engine_version)
          VALUES(:target,:org,:property,2026,:start,:end,'draft','fixture')"""),{**ids,'target':target,'start':start,'end':end})
    if scope:
        await mutate(text('INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,'+kind+"_id,origin,reviewed_by,reason) VALUES(:org,:property,:scope,:target,'REVIEWED',:actor,'Preview fixture')"),{**ids,'scope':ids[scope],'target':target})
    return target


async def coverage(ids,target,*,kind='settlement_run',source=None,revision=1):
    await mutate(text('INSERT INTO coverage_declaration(organization_id,'+kind+"_id,property_id,period_start,period_end,component,revision,state,values,source_document_ids,source_revision,reason,attestor_id) VALUES(:org,:target,:property,'2026-01-01','2026-12-31',:component,:revision,'known','{}',CAST(:sources AS jsonb),:digest,'Preview evidence',:actor)"),
      {**ids,'target':target,'component':'budget_inputs' if kind=='economic_plan' else 'WEG_JAHRESABRECHNUNG','revision':revision,'sources':json.dumps([source] if source else []),'digest':'a'*64})


@pytest.mark.parametrize('credit',[False,True])
async def test_preview_compares_whole_sets_without_money_or_publication(migrator_session,scope_seed,store,credit):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        old,allocation,review,previous=await prepared(api,ids,credit)
        unchanged=await api.get(preview_url(old,previous));assert unchanged.status_code==200 and not unchanged.json()['can_review']
        result=await api.get(preview_url(old,review));assert result.status_code==200,result.text
        value=result.json();assert value['can_review'] and value['financial_state']=='preview_only'
        assert len(value['old']['costs'])==2 and len(value['proposed']['costs'])==3
        assert value['old']['gross_amount']==value['proposed']['gross_amount']==('-119.00' if credit else '119.00')
        assert value['impacts']==[] and len(value['preview_digest'])==64
        assert (await api.get(preview_url(old,review))).json()==value
        # Ordinary adoption still refuses another charge of this exact source.
        assert not (await api.get('/api/allocation-treatments/'+review['id']+'/adoption-preview')).json()['can_adopt']
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org AND superseded_by_id IS NULL',ids)==2
    assert await read_value('SELECT count(*) FROM invoice_document WHERE organization_id=:org',ids)==1
    assert await read_value('SELECT count(*) FROM allocation_integrity_attestation WHERE organization_id=:org',ids)==1


async def test_preview_distinguishes_consumption_possible_calculation_and_coverage(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        old,_,review,_=await prepared(api,ids)
        exact=await parent(ids,snapshot={'run_type':'WEG_JAHRESABRECHNUNG','inputs':{'cost_positions':[{'cost_entry_id':old['costs'][0]['cost_entry_id']}]}})
        empty=await parent(ids)
        plan=await parent(ids,kind='economic_plan');await coverage(ids,plan,kind='economic_plan')
        unrelated=await parent(ids,start='2027-01-01',end='2027-12-31')
        result=await api.get(preview_url(old,review));assert result.status_code==200,result.text
        value=result.json();assert value['can_review'],value
        effects={row['id']:row['effects'] for row in value['impacts']}
        assert effects[str(exact)]==dict(exact_cost_consumption=True,potential_calculation_change=True,coverage_renewal=False,source_evidence_review=False)
        assert effects[str(empty)]['potential_calculation_change'] and not effects[str(empty)]['exact_cost_consumption']
        assert effects[str(plan)]['coverage_renewal'] and not effects[str(plan)]['potential_calculation_change']
        assert str(unrelated) not in effects
        before=value['preview_digest']
        await coverage(ids,plan,kind='economic_plan',source=old['source_document_id'],revision=2)
        value=(await api.get(preview_url(old,review))).json();assert value['preview_digest']!=before
        assert next(row for row in value['impacts'] if row['id']==str(plan))['effects']['source_evidence_review']
        before=value['preview_digest']
        await mutate(text('UPDATE settlement_run SET advance_payments_total=10 WHERE organization_id=:org AND id=:target'),{**ids,'target':empty})
        assert (await api.get(preview_url(old,review))).json()['preview_digest']!=before


@pytest.mark.parametrize('problem',['unclassified','foreign_scope_reference','unknown_snapshot','null_position','invalid_uuid','malformed_container','oversized','overflow'])
async def test_preview_blocks_complete_result_for_unreviewable_dependencies(migrator_session,scope_seed,store,problem):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        old,_,review,_=await prepared(api,ids)
        await parent(ids)
        if problem=='unclassified':bad=await parent(ids,scope=None)
        elif problem=='foreign_scope_reference':
            bad=await parent(ids,run_type='BK_MIETER',scope='other_scope',snapshot={'run_type':'BK_MIETER','computation':[{'cost_entry_id':old['costs'][0]['cost_entry_id']}]})
        elif problem=='unknown_snapshot':bad=await parent(ids,snapshot={'run_type':'WEG_JAHRESABRECHNUNG','unknown_costs':[]})
        elif problem=='null_position':bad=await parent(ids,snapshot={'run_type':'WEG_JAHRESABRECHNUNG','inputs':{'cost_positions':[None]}})
        elif problem=='invalid_uuid':bad=await parent(ids,snapshot={'run_type':'WEG_JAHRESABRECHNUNG','computation':[{'cost_entry_id':'broken'}]})
        elif problem=='malformed_container':bad=await parent(ids,snapshot={'run_type':'WEG_JAHRESABRECHNUNG','inputs':{'cost_positions':{}},'computation':[]})
        elif problem=='oversized':bad=await parent(ids,snapshot={'run_type':'WEG_JAHRESABRECHNUNG','computation':[],'padding':'x'*2097152})
        else:
            for _ in range(100):bad=await parent(ids)
        result=await api.get(preview_url(old,review));assert result.status_code==200,result.text
        value=result.json();assert value=={'can_review':False,'preview_digest':None,'blocker':'Complete current correction basis and accessible dependencies required','impacts':[]}
        assert str(bad) not in result.text


async def test_preview_requires_live_source_and_confirmation_authority(migrator_session,scope_seed,store,client_b_admin):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api,client(ids,'actor') as other:
        old,_,review,_=await prepared(api,ids);url=preview_url(old,review)
        assert (await api.get(url)).json()['can_review']
        assert (await other.get(url)).status_code==404
        assert (await client_b_admin.get(url)).status_code==404
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'),ids)
        assert (await api.get(url)).status_code==404


async def test_preview_retains_old_coverage_reference_before_overflow_filter(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        old,_,review,_=await prepared(api,ids)
        bad=await parent(ids,run_type='BK_MIETER',scope='other_scope',start='2027-01-01',end='2027-12-31')
        await mutate(text("""INSERT INTO coverage_declaration(organization_id,settlement_run_id,property_id,period_start,period_end,component,revision,state,values,source_document_ids,source_revision,reason,attestor_id)
          SELECT :org,:target,:property,'2027-01-01','2027-12-31','BK_MIETER',n,'known','{}',
            CASE WHEN n=1 THEN jsonb_build_array(CAST(:source AS text)) ELSE '[]'::jsonb END,:digest,'Retained history',:actor
          FROM generate_series(1,1002) n"""),{**ids,'target':bad,'source':old['source_document_id'],'digest':'a'*64})
        result=await api.get(preview_url(old,review));assert result.status_code==200,result.text
        assert not result.json()['can_review'] and result.json()['impacts']==[]
        assert str(bad) not in result.text


async def test_preview_helpers_bound_before_traversal_and_migration_roundtrip(migrator_session,scope_seed):
    await migrator_session.commit()
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL
    engine=create_engine(MIGRATOR_SYNC_URL)
    signatures=['allocation_correction_preview(uuid,uuid,uuid)','allocation_correction_impacts(uuid,uuid,uuid)','correction_duplicate_snapshot(uuid,uuid)','correction_snapshot_cost_ids(jsonb)']
    migration=importlib.import_module('migrations.versions.0113_allocation_correction_preview')
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            point=db.begin_nested()
            db.execute(text('TRUNCATE allocation_integrity_attestation CASCADE'))
            db.execute(text('TRUNCATE financial_computation_attestation CASCADE'))
            db.execute(text('TRUNCATE allocation_correction_context,allocation_correction_completion'))
            importlib.import_module('migrations.versions.0116_correction_followup_completions').downgrade()
            importlib.import_module('migrations.versions.0115_financial_computation_evidence').downgrade()
            importlib.import_module('migrations.versions.0114_allocation_corrections').downgrade()
            before={f:db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':f}) for f in signatures}
            ordinary=db.scalar(text("SELECT pg_get_functiondef('adoption_duplicate_snapshot(uuid,uuid)'::regprocedure)"))
            migration.downgrade();migration.upgrade()
            assert before=={f:db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':f}) for f in signatures}
            assert ordinary==db.scalar(text("SELECT pg_get_functiondef('adoption_duplicate_snapshot(uuid,uuid)'::regprocedure)"))
            importlib.import_module('migrations.versions.0114_allocation_corrections').upgrade()
            importlib.import_module('migrations.versions.0115_financial_computation_evidence').upgrade()
            importlib.import_module('migrations.versions.0116_correction_followup_completions').upgrade()
            for signature in signatures[1:]:
                assert not db.scalar(text("SELECT has_function_privilege('app_rw',:signature,'EXECUTE')"),{'signature':signature})
            for payload in ({'padding':'x'*2097152,'computation':[{'cost_entry_id':'invalid'}]}, {'inputs':{'cost_positions':[None]}}, {'result':{'lines':{}}}, {'result':{'owners':[{'cost_lines':False}]}}, {'result':{'owners':[None]}}):
                with pytest.raises(DBAPIError,match='Complete correction|Malformed retained'),db.begin_nested():
                    db.execute(text('SELECT correction_snapshot_cost_ids(CAST(:payload AS jsonb))'),{'payload':json.dumps(payload)})
            assert db.scalar(text("SELECT correction_snapshot_cost_ids(CAST(:payload AS jsonb))"),{'payload':json.dumps({'computation':[{'cost_entry_id':'AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA'}]})})==['aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa']
            point.rollback()
    finally:engine.dispose()


async def test_preview_retains_final_snapshot_and_observes_finalization(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        old,_,review,_=await prepared(api,ids)
        snapshot={'run_type':'WEG_JAHRESABRECHNUNG','computation':[{'cost_entry_id':old['costs'][0]['cost_entry_id']}],
          'result':{'owners':[{'cost_lines':[{'cost_entry_id':old['costs'][1]['cost_entry_id']}]}]}}
        run=await parent(ids,snapshot=snapshot)
        first=(await api.get(preview_url(old,review))).json();assert first['can_review']
        await mutate(text("UPDATE settlement_run SET status='finalized',snapshot_sha256=:hash,finalized_at=clock_timestamp(),finalized_by=:actor WHERE organization_id=:org AND id=:target"),{**ids,'target':run,'hash':'b'*64})
        result=await api.get(preview_url(old,review));assert result.status_code==200,result.text
        value=result.json();assert value['can_review'] and value['preview_digest']!=first['preview_digest']
        impact=value['impacts'][0];assert impact['status']=='finalized' and impact['effects']['exact_cost_consumption']
        assert impact['follow_up']=='review_finalized_successor'
        assert await read_value('SELECT snapshot_payload FROM settlement_run WHERE organization_id=:org AND id=:target',{**ids,'target':run})==snapshot


async def test_preview_accepts_actual_plan_snapshot_and_retained_source_evidence(migrator_session,scope_seed,store):
    from decimal import Decimal
    from app.engine.types import WirtschaftsplanInput,PropertyInput,PlanResult
    from app.engine.snapshot import build_plan_snapshot_payload
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        old,_,review,_=await prepared(api,ids)
        plan=await parent(ids,kind='economic_plan')
        inp=WirtschaftsplanInput(2026,date(2026,1,1),date(2026,12,31),PropertyInput(ids['property'],'Synthetic property','WEG'),(),(),())
        snapshot=build_plan_snapshot_payload(inp,PlanResult((),Decimal(0),Decimal(0),Decimal(0),Decimal(0),()))
        assert snapshot['document_type']=='WIRTSCHAFTSPLAN' and 'run_type' not in snapshot
        await mutate(text('UPDATE economic_plan SET snapshot_payload=CAST(:snapshot AS jsonb) WHERE organization_id=:org AND id=:target'),{**ids,'target':plan,'snapshot':json.dumps(snapshot)})
        await coverage(ids,plan,kind='economic_plan',source=old['source_document_id'])
        result=await api.get(preview_url(old,review));assert result.status_code==200,result.text
        value=result.json();assert value['can_review'],value
        assert value['impacts'][0]['effects']==dict(exact_cost_consumption=False,potential_calculation_change=False,coverage_renewal=True,source_evidence_review=True)
        await mutate(text("UPDATE economic_plan SET status='finalized',snapshot_sha256=:hash,finalized_at=clock_timestamp(),finalized_by=:actor WHERE organization_id=:org AND id=:target"),{**ids,'target':plan,'hash':'c'*64})
        after=(await api.get(preview_url(old,review))).json();assert after['can_review'] and after['preview_digest']!=value['preview_digest']
        assert not after['impacts'][0]['effects']['coverage_renewal'] and after['impacts'][0]['effects']['source_evidence_review']
        assert await read_value('SELECT snapshot_payload FROM economic_plan WHERE organization_id=:org AND id=:target',{**ids,'target':plan})==snapshot
