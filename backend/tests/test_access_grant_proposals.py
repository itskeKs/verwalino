"""Explicit reviewed proposals apply exact grants atomically, never source activation."""
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import scope_seed, worker_tenant  # noqa: F401
from tests.test_access_coverage import fixture, capture
from tests.test_scoped_account_statements import client, mutate
from tests.test_scope_payment_reconciliation import read_value
from app.core.runtime_budget import attest_runtime_budget,runtime_budget_policy

pytestmark = pytest.mark.asyncio


async def reviewed(api):
    report = await capture(api)
    response = await api.post(f"/api/access-preparation/snapshots/{report['id']}/review",
        json={'manifest_digest':report['manifest_digest'],'acknowledged':True},headers={'Idempotency-Key':str(uuid.uuid4())})
    assert response.status_code == 201,response.text
    return report


def change(ids, **kwargs):
    return dict(domain_kind='financial',audience_id=str(ids['scope']),user_id=str(ids['clerk']),
        expected_grant_id=str(ids['grant']),capabilities=['read','export'],expires_at=None) | kwargs


async def propose(api, report, changes, key=None):
    return await api.post(f"/api/access-preparation/snapshots/{report['id']}/grant-proposals",
        json={'manifest_digest':report['manifest_digest'],'changes':changes},headers={'Idempotency-Key':key or str(uuid.uuid4())})


async def accept(api, proposal, key=None):
    return await api.post(f"/api/access-preparation/grant-proposals/{proposal['id']}/accept",
        json={'proposal_digest':proposal['proposal_digest'],'acknowledged':True},headers={'Idempotency-Key':key or str(uuid.uuid4())})


async def test_draft_has_no_authority_and_acceptance_replays_without_regrant(migrator_session,scope_seed):
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        report = await reviewed(admin)
        key = str(uuid.uuid4())
        response = await propose(admin,report,[change(ids)],key)
        assert response.status_code == 201,response.text
        proposal = response.json()
        assert (await propose(admin,report,[change(ids)],key)).json() == proposal
        assert await read_value("SELECT has_scope_capability(:org,:scope,'export')",ids,actor_key='clerk') is False
        assert (await propose(admin,report,[change(ids,capabilities=['read'])],key)).status_code == 409
        accept_key = str(uuid.uuid4())
        accepted = await accept(admin,proposal,accept_key)
        assert accepted.status_code == 201,accepted.text
        receipt = accepted.json()
        assert await read_value("SELECT has_scope_capability(:org,:scope,'export')",ids,actor_key='clerk') is True
        assert await read_value('SELECT revoked_at IS NOT NULL FROM scope_grant WHERE id=:grant',ids) is True
        new_id = receipt['results'][0]['grant_id']
        await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE id=:new'),{**ids,'new':uuid.UUID(new_id)})
        assert (await accept(admin,proposal,accept_key)).json() == receipt
        assert await read_value("SELECT has_scope_capability(:org,:scope,'export')",ids,actor_key='clerk') is False
        saved = await admin.get(f"/api/access-preparation/snapshots/{report['id']}/grant-proposal")
        assert saved.json()['acceptance'] == receipt
        assert await read_value('SELECT property_source_activation_ready(:org)',ids) is False


async def test_stale_acceptance_rolls_back_every_grant(migrator_session,scope_seed):
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        report = await reviewed(admin)
        response = await propose(admin,report,[change(ids),change(ids,audience_id=str(ids['other_scope']),expected_grant_id=None)])
        assert response.status_code == 201,response.text
        await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"),ids)
        assert (await accept(admin,response.json())).status_code == 409
        assert await read_value('SELECT revoked_at IS NULL FROM scope_grant WHERE id=:grant',ids)
        assert await read_value('SELECT count(*) FROM scope_grant WHERE scope_id=:other_scope',ids) == 0


async def test_three_domains_are_explicit_and_atomic(migrator_session,scope_seed):
    ids = await fixture(migrator_session,scope_seed)
    ids['person'] = uuid.uuid4()
    await mutate(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES(:person,:org,'Test','Contact','','','')"),ids)
    async with client(ids,'actor') as admin:
        async def domain(path,body):
            response = await admin.post(path,json=body,headers={'Idempotency-Key':str(uuid.uuid4())})
            assert response.status_code == 201,response.text
            return response.json()['id']
        property_domain = await domain('/api/property-data-domains',{'property_id':str(ids['property']),'label':'Building team','reason':'Reviewed team assignment'})
        person_domain = await domain('/api/person-data-domains',{'person_id':str(ids['person']),'reason_code':'TEAM_ACCESS'})
        report = await reviewed(admin)
        changes = [change(ids,capabilities=[]),change(ids,domain_kind='property',audience_id=property_domain,expected_grant_id=None),
                   change(ids,domain_kind='person',audience_id=person_domain,expected_grant_id=None)]
        proposed = await propose(admin,report,changes)
        assert proposed.status_code == 201,proposed.text
        assert await read_value('SELECT count(*) FROM property_data_grant WHERE organization_id=:org',ids) == 0
        accepted = await accept(admin,proposed.json())
        assert accepted.status_code == 201,accepted.text
        assert len(accepted.json()['results']) == 3
        assert await read_value("SELECT has_scope_capability(:org,:scope,'read')",ids,actor_key='clerk') is False
        for function,value in [('has_property_data_capability',property_domain),('has_person_data_capability',person_domain)]:
            assert await read_value(f"SELECT {function}(:org,:domain,'export')",{**ids,'domain':uuid.UUID(value)},actor_key='clerk') is True
        assert await read_value('SELECT count(*) FROM access_grant_acceptance WHERE organization_id=:org',ids) == 1
        assert await read_value("SELECT count(*) FROM audit_event WHERE entity_id=:snapshot AND action='access.grants_accepted'",{**ids,'snapshot':uuid.UUID(report['id'])},actor_key='actor') == 1
        assert await read_value("SELECT count(*) FROM audit_event WHERE entity_id=:snapshot AND action='access.grants_accepted'",{**ids,'snapshot':uuid.UUID(report['id'])},actor_key='clerk') == 0


async def test_proposals_require_review_exact_predecessor_and_supported_role(migrator_session,scope_seed):
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        report = await capture(admin)
        assert (await propose(admin,report,[change(ids)])).status_code == 409
        report = await reviewed(admin)
        assert (await propose(admin,report,[change(ids,expected_grant_id=None)])).status_code == 409
        assert (await propose(admin,report,[change(ids,audience_id=str(uuid.uuid4()),expected_grant_id=None)])).status_code == 404
        assert (await propose(admin,report,[change(ids),change(ids)])).status_code == 422
        assert (await propose(admin,report,[change(ids,domain_kind='person',capabilities=['read','approve'])])).status_code == 422
        assert (await propose(admin,report,[change(ids,expires_at='2020-01-01T00:00:00Z')])).status_code == 422
        await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"),ids)
        report = await reviewed(admin)
        assert (await propose(admin,report,[change(ids)])).status_code == 422
        response = await propose(admin,report,[change(ids,capabilities=['read'])])
        assert response.status_code == 201,response.text


async def test_private_receipts_and_current_principal_checks(migrator_session,scope_seed,client_b_admin,monkeypatch):
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_worker_session_factory,set_tenant_context
    from app.api import access_preparation
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        report = await reviewed(admin)
        proposal = (await propose(admin,report,[change(ids)])).json()
        assert (await accept(client_b_admin,proposal)).status_code == 404
        assert (await client_b_admin.get(f"/api/access-preparation/snapshots/{report['id']}/grant-proposal")).status_code == 404
        async with client(ids) as clerk:
            assert (await accept(clerk,proposal)).status_code == 403
        for table in ('access_grant_proposal','access_grant_acceptance'):
            with pytest.raises(DBAPIError,match='permission denied'):
                await read_value(f'SELECT count(*) FROM {table}',ids,actor_key='actor')
        async with get_worker_session_factory()() as db,db.begin():
            await set_tenant_context(db,ids['org'])
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['actor'])})
            with pytest.raises(DBAPIError,match='Current coverage administrator'):
                await db.execute(text('SELECT access_grant_proposal_for_snapshot(:org,:snapshot)'),{**ids,'snapshot':uuid.UUID(report['id'])})
        original = access_preparation.require_live_principal
        async def revoked(db,auth,**kwargs):
            await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:actor"),ids)
            return await original(db,auth,**kwargs)
        monkeypatch.setattr(access_preparation,'require_live_principal',revoked)
        assert (await accept(admin,proposal)).status_code == 403
        assert await read_value('SELECT revoked_at IS NULL FROM scope_grant WHERE id=:grant',ids)


async def test_expiry_invalidates_proposal_without_partial_effects(migrator_session,scope_seed):
    from datetime import datetime,timedelta,timezone
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        report = await reviewed(admin)
        expiry = datetime.now(timezone.utc)+timedelta(seconds=2)
        response = await propose(admin,report,[change(ids,expires_at=expiry.isoformat())])
        assert response.status_code == 201,response.text
        await read_value('SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM CAST(:expiry AS timestamptz)-clock_timestamp()))+.02)',{'expiry':expiry})
        assert (await accept(admin,response.json())).status_code == 422
        assert await read_value('SELECT revoked_at IS NULL FROM scope_grant WHERE id=:grant',ids)


async def test_proposal_migration_retains_evidence_and_roundtrips_empty(migrator_session,scope_seed):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        report = await reviewed(admin)
        proposal = (await propose(admin,report,[change(ids)])).json()
    with pytest.raises(DBAPIError,match='immutable'):
        await mutate(text("UPDATE access_grant_proposal SET changes='[]' WHERE id=:proposal"),{'proposal':uuid.UUID(proposal['id'])})
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            migration = importlib.import_module('migrations.versions.0084_access_grant_proposals')
            with pytest.raises(DBAPIError,match='Retain access grant proposals'):
                with db.begin_nested(): migration.downgrade()
            point = db.begin_nested()
            try:
                db.execute(text('TRUNCATE access_grant_acceptance,access_grant_proposal CASCADE'))
                migration.downgrade();migration.upgrade()
                assert not db.scalar(text('SELECT property_source_activation_ready(:org)'),ids)
            finally: point.rollback()
    finally: engine.dispose()


async def test_concurrent_acceptance_has_one_receipt_and_direct_invalid_commands_fail(migrator_session,scope_seed):
    import asyncio,json
    from sqlalchemy.exc import DBAPIError
    from app.services.access_coverage_service import AccessCoverageService
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        report = await reviewed(admin)
        proposal = (await propose(admin,report,[change(ids)])).json()
        key = str(uuid.uuid4())
        responses = await asyncio.gather(accept(admin,proposal,key),accept(admin,proposal,key))
        assert [r.status_code for r in responses] == [201,201]
        assert responses[0].json() == responses[1].json()
        assert await read_value('SELECT count(*) FROM access_grant_acceptance WHERE organization_id=:org',ids) == 1
        current = await reviewed(admin)
        for invalid in [change(ids,capabilities=['read','superuser']),change(ids,capabilities=[] ,expected_grant_id=None)]:
            with pytest.raises(DBAPIError,match='Invalid capabilities'):
                await attested_value('SELECT propose_access_grants(:org,:snapshot,:request,:digest,CAST(:changes AS jsonb),CAST(:policy AS jsonb))',
                    {**ids,'snapshot':uuid.UUID(current['id']),'request':uuid.uuid4(),'digest':current['manifest_digest'],
                     'changes':json.dumps([invalid]),'policy':AccessCoverageService(None,None).policy()},actor_key='actor')


async def test_failure_during_second_write_rolls_back_first_replacement(migrator_session,scope_seed):
    from sqlalchemy.exc import DBAPIError
    from app.services.access_coverage_service import AccessCoverageService
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        report = await reviewed(admin)
        proposal = (await propose(admin,report,[change(ids),change(ids,audience_id=str(ids['other_scope']),expected_grant_id=None)])).json()
    # Install only on the guarded disposable database and remove in finally.
    from app.core.db import get_session_factory,set_tenant_context
    await mutate(text('''CREATE FUNCTION test_reject_second_proposal_grant() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF EXISTS(SELECT 1 FROM public.scope_grant WHERE organization_id=NEW.organization_id AND reason='Explicitly accepted access proposal')
          THEN RAISE EXCEPTION 'Injected second grant failure'; END IF; RETURN NEW; END $$;'''),{})
    await mutate(text('CREATE TRIGGER zz_test_reject_second_proposal_grant BEFORE INSERT ON scope_grant FOR EACH ROW EXECUTE FUNCTION test_reject_second_proposal_grant()'),{})
    try:
        async with get_session_factory()() as db,db.begin():
            await set_tenant_context(db,ids['org'])
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['actor'])})
            await attest_runtime_budget(db,ids['org'],ids['actor'],runtime_budget_policy())
            with pytest.raises(DBAPIError,match='Injected second grant failure'):
                async with db.begin_nested():
                    await db.execute(text('SELECT accept_access_grants(:org,:proposal,:request,:digest,CAST(:policy AS jsonb))'),
                        {**ids,'proposal':uuid.UUID(proposal['id']),'request':uuid.uuid4(),'digest':proposal['proposal_digest'],'policy':AccessCoverageService(None,None).policy()})
        assert await read_value('SELECT revoked_at IS NULL FROM scope_grant WHERE id=:grant',ids)
        assert await read_value('SELECT count(*) FROM scope_grant WHERE organization_id=:org',ids) == 1
        assert await read_value('SELECT count(*) FROM access_grant_acceptance WHERE organization_id=:org',ids) == 0
    finally:
        await mutate(text('DROP TRIGGER zz_test_reject_second_proposal_grant ON scope_grant'),{})
        await mutate(text('DROP FUNCTION test_reject_second_proposal_grant()'),{})


async def test_direct_expiry_is_absolute_and_timezone_independent(migrator_session,scope_seed):
    import json
    from datetime import datetime,timedelta,timezone
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_session_factory,set_tenant_context
    from app.services.access_coverage_service import AccessCoverageService
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        report = await reviewed(admin)
    params={**ids,'snapshot':uuid.UUID(report['id']),'request':uuid.uuid4(),'digest':report['manifest_digest'],
            'policy':AccessCoverageService(None,None).policy()}
    sql='SELECT propose_access_grants(:org,:snapshot,:request,:digest,CAST(:changes AS jsonb),CAST(:policy AS jsonb))'
    for expiry in ['tomorrow','2099-12-31T12:00:00','2099-12-31 12:00:00+01:00']:
        with pytest.raises(DBAPIError,match='absolute with timezone'):
            await attested_value(sql,{**params,'changes':json.dumps([change(ids,expires_at=expiry)])},actor_key='actor')
    expiry=datetime.now(timezone.utc)+timedelta(days=30)
    async with get_session_factory()() as db,db.begin():
        await set_tenant_context(db,ids['org'])
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('TimeZone','Pacific/Honolulu',true)"),{'actor':str(ids['actor'])})
        await attest_runtime_budget(db,ids['org'],ids['actor'],runtime_budget_policy())
        proposal=await db.scalar(text(sql),{**params,'changes':json.dumps([change(ids,expires_at=expiry.isoformat())])})
    assert proposal['changes'][0]['expires_at'].endswith('Z')
    async with get_session_factory()() as db,db.begin():
        await set_tenant_context(db,ids['org'])
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('TimeZone','Asia/Tokyo',true)"),{'actor':str(ids['actor'])})
        await attest_runtime_budget(db,ids['org'],ids['actor'],runtime_budget_policy())
        receipt=await db.scalar(text('SELECT accept_access_grants(:org,:proposal,:request,:digest,CAST(:policy AS jsonb))'),
            {**params,'proposal':uuid.UUID(proposal['id']),'request':uuid.uuid4(),'digest':proposal['proposal_digest']})
    actual=await read_value('SELECT expires_at FROM scope_grant WHERE id=:new',{'new':uuid.UUID(receipt['results'][0]['grant_id'])})
    assert actual == expiry


async def test_departed_member_grants_remain_visible_and_revocable(migrator_session,scope_seed):
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        # Financial grants use a membership FK; property/contact grants retain user identity.
        response=await admin.post('/api/property-data-domains',json={'property_id':str(ids['property']),'label':'Team','reason':'Reviewed'},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert response.status_code==201,response.text
        domain=response.json()['id']
        grant=await admin.post(f'/api/property-data-domains/{domain}/grants',json={'user_id':str(ids['actor']),'capabilities':['read'],'reason':'Team'},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert grant.status_code==201,grant.text
        # Keep administrator to operate; use a new user with only a retained data grant.
        departed=uuid.uuid4()
        await mutate(text('INSERT INTO "user"(id,organization_id,email,password_hash,display_name) VALUES(:departed,:org,:email,\'fixture\',\'Departed\')'),{**ids,'departed':departed,'email':str(departed)+'@scope-test.invalid'})
        await mutate(text("INSERT INTO membership(organization_id,user_id,role) VALUES(:org,:departed,'CLERK')"),{**ids,'departed':departed})
        grant=await admin.post(f'/api/property-data-domains/{domain}/grants',json={'user_id':str(departed),'capabilities':['read'],'reason':'Team'},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert grant.status_code==201,grant.text
        await mutate(text('DELETE FROM membership WHERE organization_id=:org AND user_id=:departed'),{**ids,'departed':departed})
        report=await reviewed(admin)
        rows=(await admin.get(f"/api/access-preparation/snapshots/{report['id']}/items",params={'limit':200})).json()['items']
        item=next(r for r in rows if r['kind']=='property' and r['resource_id']==str(ids['property']))
        retained=next(g for g in item['audience'] if g['user_id']==str(departed))
        assert retained['effective_capabilities']==[] and retained['role'] is None
        proposal=await propose(admin,report,[change(ids,domain_kind='property',audience_id=domain,user_id=str(departed),expected_grant_id=grant.json()['id'],capabilities=[])])
        assert proposal.status_code==201,proposal.text
        assert (await accept(admin,proposal.json())).status_code==201
        assert await read_value('SELECT revoked_at IS NOT NULL FROM property_data_grant WHERE id=:grant',{'grant':uuid.UUID(grant.json()['id'])})


async def attested_value(sql,params,actor_key):
    from app.core.db import get_tenant_session
    async with get_tenant_session(params['org']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(params[actor_key])})
        await attest_runtime_budget(db,params['org'],params[actor_key],params['policy'])
        return await db.scalar(text(sql),params)
