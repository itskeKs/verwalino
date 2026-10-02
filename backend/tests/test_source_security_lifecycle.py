"""Explicit lifecycle transitions preserve strict authority across replay and pause."""
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_access_disposition import retained_fixture,reviewed,assessment,acceptance_body
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value

pytestmark=pytest.mark.asyncio
PATH='/api/access-preparation/source-security'


async def status(api):
    response=await api.get(PATH)
    assert response.status_code==200,response.text
    return response.json()


async def accepted(api):
    report=await reviewed(api)
    state=await assessment(api,report)
    response=await api.post(f"/api/access-preparation/snapshots/{report['id']}/disposition/accept",
        json=acceptance_body(report,state),headers={'Idempotency-Key':str(uuid.uuid4())})
    assert response.status_code==201,response.text
    return response.json()


def transition_body(state,target,decision=None):
    return {'target_mode':target,'expected_mode':state['mode'],'expected_changed_at':state['changed_at'],
        'expected_transition_id':state['last_transition']['id'] if state['last_transition'] else None,
        'acceptance_id':decision['id'] if decision else None,
        'manifest_digest':decision['manifest_digest'] if decision else None,
        'assessment_digest':decision['assessment_digest'] if decision else None,
        'reason':'Explicit scoped lifecycle decision','acknowledged':True}


async def transition(api,body,key=None):
    return await api.post(PATH+'/transitions',json=body,headers={'Idempotency-Key':key or str(uuid.uuid4())})


async def test_activate_pause_and_replay_never_reenable_retained_sources(migrator_session,scope_seed):
    ids=await retained_fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        initial=await status(admin)
        assert initial['mode']=='PREPARATION' and initial['last_transition'] is None
        decision=await accepted(admin)
        body=transition_body(initial,'ENFORCED',decision)
        key=str(uuid.uuid4())
        activation=await transition(admin,body,key)
        assert activation.status_code==201,activation.text
        current=await status(admin)
        assert current['mode']=='ENFORCED' and current['last_transition']==activation.json()
        for who in ('actor','clerk'):
            assert await read_value('SELECT count(*) FROM invoice_document WHERE id=:document',ids,actor_key=who)==0
        paused=await transition(admin,transition_body(current,'PAUSED'))
        assert paused.status_code==201,paused.text
        assert (await transition(admin,body,key)).json()==activation.json()
        assert (await status(admin))['mode']=='PAUSED'
        stale=await transition(admin,transition_body(await status(admin),'ENFORCED',decision))
        assert stale.status_code==409,stale.text
        fresh=await accepted(admin)
        resumed=await transition(admin,transition_body(await status(admin),'ENFORCED',fresh))
        assert resumed.status_code==201,resumed.text
        assert (await status(admin))['mode']=='ENFORCED'
    assert await read_value('SELECT count(*) FROM scope_grant WHERE organization_id=:org',ids)==0
    assert await read_value("SELECT count(*) FROM audit_event WHERE organization_id=:org AND action='property_source.security_changed'",ids)==3


async def test_pause_and_status_work_without_budget_registrar(migrator_session,scope_seed,monkeypatch):
    ids=await retained_fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        decision=await accepted(admin)
        response=await transition(admin,transition_body(await status(admin),'ENFORCED',decision))
        assert response.status_code==201,response.text
        async def unavailable(*args,**kwargs):
            raise AssertionError('Pause must not contact budget registrar')
        monkeypatch.setattr('app.services.access_coverage_service.attest_runtime_budget',unavailable)
        response=await transition(admin,transition_body(await status(admin),'PAUSED'))
        assert response.status_code==201,response.text
        assert (await status(admin))['mode']=='PAUSED'


async def test_empty_organization_bootstraps_only_explicit_audience(migrator_session,scope_seed):
    from tests.test_property_master_access import audience
    await migrator_session.commit()
    ids={'org':scope_seed['org_a_id'],'actor':scope_seed['user_a_admin_id'],'clerk':scope_seed['user_a_clerk_id']}
    async with client(ids,'actor') as admin:
        decision=await accepted(admin)
        response=await transition(admin,transition_body(await status(admin),'ENFORCED',decision))
        assert response.status_code==201,response.text
        body={'name':'Explicit first property','management_type':'WEG'}
        assert (await admin.post('/api/properties',json=body)).status_code==422
        response=await admin.post('/api/properties',json={**body,'data_audience':audience(scope_seed)},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert response.status_code==201,response.text
        property_id=response.json()['id']
        assert (await admin.get(f'/api/properties/{property_id}')).status_code==200
        async with client(ids) as clerk:
            assert (await clerk.get(f'/api/properties/{property_id}')).status_code==404
        assert (await transition(admin,transition_body(await status(admin),'PAUSED'))).status_code==201
        assert (await admin.get(f'/api/properties/{property_id}')).status_code==200
        assert (await admin.put(f'/api/properties/{property_id}',json={'name':'Paused mutation'})).status_code==404


@pytest.mark.parametrize('change',['source','budget','active_work'])
async def test_changed_evidence_cannot_activate(migrator_session,scope_seed,monkeypatch,change):
    from decimal import Decimal
    from app.core.config import settings
    ids=await retained_fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        decision=await accepted(admin)
        if change=='source':
            await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"),ids)
        elif change=='budget':
            monkeypatch.setattr(settings,'declaration_qa_max_cost_usd',settings.declaration_qa_max_cost_usd+Decimal('0.01'))
        else:
            await mutate(text("UPDATE invoice_document SET processing_status='queued' WHERE id=:document"),ids)
        response=await transition(admin,transition_body(await status(admin),'ENFORCED',decision))
        assert response.status_code==409,response.text
        assert (await status(admin))['mode']=='PREPARATION'
    assert await read_value('SELECT count(*) FROM source_security_transition WHERE organization_id=:org',ids)==0
    assert await read_value('SELECT count(*) FROM source_security_authorization WHERE organization_id=:org',ids)==0


@pytest.mark.parametrize('old_proof',[False,True])
async def test_raw_activation_requires_current_connection_proof(migrator_session,scope_seed,old_proof):
    import json
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_tenant_session
    from app.core.runtime_budget import attest_runtime_budget,runtime_budget_policy
    ids=await retained_fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        decision=await accepted(admin)
        body=transition_body(await status(admin),'ENFORCED',decision)
    if old_proof:
        async with get_tenant_session(ids['org']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['actor'])})
            await attest_runtime_budget(db,ids['org'],ids['actor'],runtime_budget_policy())
    with pytest.raises(DBAPIError,match='Trusted current runtime required'):
        async with get_tenant_session(ids['org']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['actor'])})
            await db.execute(text('SELECT transition_source_security(:org,:request,CAST(:payload AS jsonb))'),
                {'org':ids['org'],'request':uuid.uuid4(),'payload':json.dumps(body)})
    assert not await read_value('SELECT property_source_activation_ready(:org)',ids)


@pytest.mark.parametrize('same_key',[False,True])
async def test_concurrent_transition_has_one_effect(migrator_session,scope_seed,same_key):
    import asyncio
    ids=await retained_fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        decision=await accepted(admin)
        body=transition_body(await status(admin),'ENFORCED',decision)
        key=str(uuid.uuid4())
        responses=await asyncio.gather(transition(admin,body,key),transition(admin,body,key if same_key else None))
        assert sorted(r.status_code for r in responses)==([201,201] if same_key else [201,409])
        if same_key: assert responses[0].json()==responses[1].json()
    assert await read_value('SELECT count(*) FROM source_security_transition WHERE organization_id=:org',ids)==1
    assert await read_value("SELECT count(*) FROM audit_event WHERE organization_id=:org AND action='property_source.security_changed'",ids)==1


async def test_tenant_admin_and_changed_payload_are_checked_before_replay(migrator_session,scope_seed,client_b_admin):
    ids=await retained_fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        decision=await accepted(admin)
        body=transition_body(await status(admin),'ENFORCED',decision)
        key=str(uuid.uuid4())
        assert (await transition(client_b_admin,body,key)).status_code==404
        async with client(ids) as clerk:
            assert (await clerk.get(PATH)).status_code==403
            assert (await transition(clerk,body,key)).status_code==403
        assert (await transition(admin,body,key)).status_code==201
        assert (await transition(admin,{**body,'reason':'Different request'},key)).status_code==409
        await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:actor"),ids)
        assert (await transition(admin,body,key)).status_code in (403,404)


async def test_replay_survives_registrar_outage_and_aba_evidence_is_rejected(migrator_session,scope_seed,monkeypatch):
    ids=await retained_fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        decision=await accepted(admin)
        first_body=transition_body(await status(admin),'ENFORCED',decision)
        first_key=str(uuid.uuid4())
        first=await transition(admin,first_body,first_key)
        assert first.status_code==201,first.text
        assert (await transition(admin,transition_body(await status(admin),'PAUSED'))).status_code==201
        old_paused_state=await status(admin)
        paused_decision=await accepted(admin)
        resume_body=transition_body(old_paused_state,'ENFORCED',paused_decision)
        assert (await transition(admin,resume_body)).status_code==201
        assert (await transition(admin,transition_body(await status(admin),'PAUSED'))).status_code==201
        # Same mode/actor/reason reproduces the old structural manifest; timestamp
        # freshness still rejects the previous PAUSED snapshot and prior request.
        assert (await transition(admin,resume_body)).status_code==409
        response=await transition(admin,transition_body(await status(admin),'ENFORCED',paused_decision))
        assert response.status_code==409,response.text
        async def unavailable(*args,**kwargs):
            raise AssertionError('Historical replay must not contact registrar')
        monkeypatch.setattr('app.services.access_coverage_service.attest_runtime_budget',unavailable)
        assert (await transition(admin,first_body,first_key)).json()==first.json()
        assert (await status(admin))['mode']=='PAUSED'


async def test_audit_failure_rolls_back_mode_authorization_and_receipt(migrator_session,scope_seed):
    from sqlalchemy.exc import DBAPIError
    ids=await retained_fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        decision=await accepted(admin)
        body=transition_body(await status(admin),'ENFORCED',decision)
        await mutate(text("""CREATE FUNCTION test_reject_transition_audit() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN IF NEW.action='property_source.security_changed' THEN RAISE EXCEPTION 'Injected transition failure'; END IF; RETURN NEW; END $$"""),{})
        await mutate(text('CREATE TRIGGER zz_test_reject_transition_audit BEFORE INSERT ON audit_event FOR EACH ROW EXECUTE FUNCTION test_reject_transition_audit()'),{})
        try:
            with pytest.raises(DBAPIError,match='Injected transition failure'):
                await transition(admin,body)
            assert (await status(admin))['mode']=='PREPARATION'
            for table in ('source_security_transition','source_security_authorization'):
                assert await read_value(f'SELECT count(*) FROM {table} WHERE organization_id=:org',ids)==0
        finally:
            await mutate(text('DROP TRIGGER zz_test_reject_transition_audit ON audit_event'),{})
            await mutate(text('DROP FUNCTION test_reject_transition_audit()'),{})


async def test_private_evidence_and_guarded_migration_roundtrip(migrator_session,scope_seed):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids=await retained_fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        decision=await accepted(admin)
        assert (await transition(admin,transition_body(await status(admin),'ENFORCED',decision))).status_code==201
    with pytest.raises(DBAPIError,match='immutable'):
        await mutate(text("UPDATE source_security_transition SET reason='Rewrite' WHERE organization_id=:org"),ids)
    migration=importlib.import_module('migrations.versions.0091_source_security_lifecycle')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            for role in ('app_rw','app_worker','app_review'):
                for table in ('source_security_transition','source_security_authorization'):
                    assert not db.scalar(text('SELECT has_table_privilege(:role,:table,\'SELECT,INSERT,UPDATE,DELETE\')'),{'role':role,'table':table})
                assert not db.scalar(text("SELECT has_function_privilege(:role,'property_source_activation_ready(uuid)','EXECUTE')"),{'role':role})
            with pytest.raises(DBAPIError,match='Retain source lifecycle evidence'):
                with db.begin_nested(): migration.downgrade()
            point=db.begin_nested()
            try:
                before=db.scalar(text("SELECT pg_get_functiondef('property_source_activation_ready(uuid)'::regprocedure)"))
                db.execute(text('TRUNCATE source_security_transition,source_security_authorization,property_source_security'))
                migration.downgrade()
                assert not db.scalar(text('SELECT property_source_activation_ready(:org)'),ids)
                migration.upgrade()
                assert db.scalar(text("SELECT pg_get_functiondef('property_source_activation_ready(uuid)'::regprocedure)"))==before
            finally: point.rollback()
    finally: engine.dispose()


async def test_canonical_replay_across_timezones_and_equivalent_offsets(migrator_session,scope_seed):
    import json
    from datetime import datetime,timezone,timedelta
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_tenant_session
    ids=await retained_fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        decision=await accepted(admin)
        assert (await transition(admin,transition_body(await status(admin),'ENFORCED',decision))).status_code==201
        body=transition_body(await status(admin),'PAUSED')
        key=str(uuid.uuid4())
        original=await transition(admin,body,key)
        assert original.status_code==201,original.text
    instant=datetime.fromisoformat(body['expected_changed_at'].replace('Z','+00:00'))
    for zone,offset in [('Pacific/Honolulu',-10),('Asia/Tokyo',9)]:
        async with get_tenant_session(ids['org']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('TimeZone',:zone,true)"),{'actor':str(ids['actor']),'zone':zone})
            values={'org':ids['org'],'request':uuid.UUID(key),'payload':json.dumps({**body,
                'expected_changed_at':instant.astimezone(timezone(timedelta(hours=offset))).isoformat()})}
            replay=await db.scalar(text('SELECT transition_source_security(:org,:request,CAST(:payload AS jsonb))'),values)
            assert replay['id']==original.json()['id']
            for invalid in ('now','today','infinity','2026-10-02T10:00:00'):
                with pytest.raises(DBAPIError,match='absolute with timezone'):
                    async with db.begin_nested():
                        await db.execute(text('SELECT transition_source_security(:org,:request,CAST(:payload AS jsonb))'),
                            {**values,'payload':json.dumps({**body,'expected_changed_at':invalid})})
    assert await read_value('SELECT count(*) FROM source_security_transition WHERE organization_id=:org',ids)==2


async def test_observed_unlogged_state_still_conflicts_with_last_receipt(migrator_session,scope_seed):
    ids=await retained_fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        decision=await accepted(admin)
        assert (await transition(admin,transition_body(await status(admin),'ENFORCED',decision))).status_code==201
        assert (await transition(admin,transition_body(await status(admin),'PAUSED'))).status_code==201
        from sqlalchemy.ext.asyncio import create_async_engine
        from tests.conftest import MIGRATOR_URL
        engine=create_async_engine(MIGRATOR_URL)
        try:
            async with engine.begin() as db:
                await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
                    {'org':str(ids['org']),'actor':str(ids['actor'])})
                await db.execute(text("UPDATE property_source_security SET reason='Unlogged trusted mutation' WHERE organization_id=:org"),ids)
        finally: await engine.dispose()
        observed=await status(admin)
        assert observed['changed_at']!=observed['last_transition']['changed_at']
        fresh=await accepted(admin)
        result=await transition(admin,transition_body(observed,'ENFORCED',fresh))
        assert result.status_code==409,result.text
        assert (await status(admin))['mode']=='PAUSED'
