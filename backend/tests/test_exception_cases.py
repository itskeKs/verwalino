"""Shared intake conflict evidence and advisory workflow never resolve money."""
import asyncio
import uuid
from datetime import datetime,timedelta,timezone
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_assignment_proposals import prepared,command as proposal_command

pytestmark=pytest.mark.asyncio


async def setup_case(api,ids):
    await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:other_scope,:clerk,ARRAY['read','prepare'],:actor,'Second scope for conflict review') ON CONFLICT DO NOTHING"),ids)
    ref,child,partition,extraction=await prepared(api,ids,scoped=True)
    body=proposal_command(partition,extraction,ids['other_scope'])
    response=await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**body)
    assert response.status_code==201,response.text
    return ref,child,partition,extraction,response.json(),body


def event_command(revision,action='claim',key=None,until=None,reason='Review work recorded'):
    return dict(json={'action':action,'reason':reason,'snoozed_until':until},headers={'Idempotency-Key':key or str(uuid.uuid4()),'If-Match':'"'+revision+'"'})


async def test_blocked_proposals_share_one_case_and_preserve_dependency_history(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,child,partition,extraction,proposal,body=await setup_case(api,ids)
        response=await api.get('/api/exceptions');assert response.status_code==200,response.text
        case=response.json()['items'][0];assert case['cause']=='scope_hint_conflict' and case['resolution_state']=='unresolved'
        assert case['workflow_state']=='open' and case['revision_number']==1
        assert (await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**body)).json()==proposal
        assert (await api.get('/api/exceptions/'+case['id'])).json()['revision_number']==1
        second=await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**proposal_command(partition,extraction,ids['other_scope'],previous=proposal['id']))
        assert second.status_code==201,second.text
        assert len((await api.get('/api/exceptions')).json()['items'])==1
        detail=(await api.get('/api/exceptions/'+case['id'])).json()
        assert detail['revision_number']==2 and detail['dependency_count']==2
        events=(await api.get('/api/exceptions/'+case['id']+'/events')).json()['items']
        assert len(events)==2 and all(event['action']=='evidence_added' and event['actor_id'] is None for event in events)
        dependencies=(await api.get('/api/exceptions/'+case['id']+'/dependencies')).json()['items']
        assert {row['assignment_proposal_id'] for row in dependencies}=={proposal['id'],second.json()['id']}
        assert {row['proposal_revision_number'] for row in dependencies}=={1,2}
        assert all(row['proposal_reason']==proposal['reason'] for row in dependencies)
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==0


async def test_advisory_claim_snooze_new_evidence_and_historical_replay(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,child,partition,extraction,proposal,_=await setup_case(api,ids)
        case=(await api.get('/api/exceptions')).json()['items'][0];url='/api/exceptions/'+case['id']
        body=event_command(case['revision_id']);reply=await api.post(url+'/events',**body);assert reply.status_code==201,reply.text
        claimed=reply.json();assert claimed['claimant_id']==str(ids['clerk']) and claimed['revision_number']==2
        assert (await api.post(url+'/events',**body)).json()==claimed
        assert (await api.post(url+'/events',**event_command(case['revision_id'],'reopen'))).status_code==409
        until=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        sleep=event_command(claimed['id'],'snooze',until=until,reason='Awaiting source comparison')
        reply=await api.post(url+'/events',**sleep);assert reply.status_code==201,reply.text
        snoozed=reply.json();assert snoozed['workflow_state']=='snoozed'
        detail=(await api.get(url)).json();assert detail['workflow_state']=='snoozed' and detail['resolution_state']=='unresolved'
        assert len((await api.get('/api/exceptions',params={'workflow_state':'snoozed'})).json()['items'])==1
        assert (await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**proposal_command(partition,extraction,ids['other_scope'],previous=proposal['id']))).status_code==201
        detail=(await api.get(url)).json();assert detail['workflow_state']=='open' and detail['revision_number']==4
        assert (await api.post(url+'/events',**sleep)).json()==snoozed
        assert (await api.get(url)).json()['workflow_state']=='open'
        assert (await api.post(url+'/events',**event_command(detail['revision_id'],'resolve'))).status_code==422
        changed=event_command(claimed['id'],'snooze',key=sleep['headers']['Idempotency-Key'],until=until,reason='Changed replay')
        assert (await api.post(url+'/events',**changed)).status_code==409


async def test_case_authority_and_concurrent_writes_revalidate_current_version(migrator_session,scope_seed,store,client_b_admin):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        await setup_case(api,ids)
        case=(await api.get('/api/exceptions')).json()['items'][0];url='/api/exceptions/'+case['id']
        replies=await asyncio.gather(*[api.post(url+'/events',**event_command(case['revision_id'])) for _ in range(2)])
        assert sorted(reply.status_code for reply in replies)==[201,409]
        for denied in (admin,client_b_admin):
            assert (await denied.get('/api/exceptions')).json()['items']==[]
            for path in ('','/events','/dependencies'):assert (await denied.get(url+path)).status_code==404
            assert (await denied.post(url+'/events',**event_command(case['revision_id']))).status_code==404
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:other_scope AND revoked_at IS NULL'),ids)
        assert (await api.get('/api/exceptions')).json()['items']==[]
        assert (await api.get(url)).status_code==404
        assert (await api.post(url+'/events',**event_command(case['revision_id']))).status_code==404


async def test_exception_role_pause_rollback_and_current_authority_on_replay(migrator_session,scope_seed,store):
    from app.core.db import get_tenant_session,get_worker_session_factory,get_review_session_factory,set_tenant_context
    from sqlalchemy.exc import DBAPIError
    from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
    ids=await fixture(migrator_session,scope_seed)
    async with synthetic_enforcement(scope_seed),client(ids) as api:
        await setup_case(api,ids)
        case=(await api.get('/api/exceptions')).json()['items'][0];url='/api/exceptions/'+case['id']
        params={**ids,'target':uuid.UUID(case['id']),'expected':uuid.UUID(case['revision_id']),'request':uuid.uuid4()}
        sql="SELECT append_exception_event(:org,:target,:expected,:request,'claim','Explicit review work',NULL)"
        for factory in (get_worker_session_factory(),get_review_session_factory()):
            with pytest.raises(DBAPIError,match='Interactive runtime required|permission denied'):
                async with factory() as db,db.begin():
                    await set_tenant_context(db,ids['org'])
                    await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                    await db.execute(text(sql),params)
        with pytest.raises(DBAPIError,match='inaccessible'):
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('app.current_scope_id',:scope,true)"),{'actor':str(ids['clerk']),'scope':str(ids['scope'])})
                await db.execute(text(sql),params)
        with pytest.raises(RuntimeError,match='simulated crash'):
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                assert await db.scalar(text(sql),params)
                raise RuntimeError('simulated crash')
        assert (await api.get(url)).json()['revision_number']==1
        body=event_command(case['revision_id']);assert (await api.post(url+'/events',**body)).status_code==201
        for table in ('exception_case','exception_dependency','exception_case_event'):
            with pytest.raises(DBAPIError,match='permission denied'):
                await read_value(f'DELETE FROM {table} WHERE organization_id=:org RETURNING id',ids,actor_key='clerk')
            with pytest.raises(DBAPIError,match='immutable|Immutable'):
                await mutate(text(f'DELETE FROM {table} WHERE organization_id=:org'),ids)
        with pytest.raises(DBAPIError,match='permission denied'):
            await read_value('SELECT record_assignment_exceptions(:org,:target)',params,actor_key='clerk')
        await change_mode(scope_seed,'PAUSED')
        assert (await api.get(url)).json()['can_work'] is False
        assert (await api.post(url+'/events',**body)).status_code==404
        await change_mode(scope_seed,'ENFORCED')
        assert (await api.post(url+'/events',**body)).status_code==201
        # A readable target does not substitute for the now-revoked original scope.
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND revoked_at IS NULL'),ids)
        assert (await api.get('/api/exceptions')).json()['items']==[]
        assert (await api.post(url+'/events',**body)).status_code==404


async def test_exception_actions_time_validation_pagination_and_new_evidence_race(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,child,partition,extraction,proposal,_=await setup_case(api,ids)
        case=(await api.get('/api/exceptions')).json()['items'][0];url='/api/exceptions/'+case['id']
        assert (await api.post(url+'/events',**event_command(case['revision_id'],'release'))).status_code==409
        for action,until in [('snooze',None),('snooze','2030-01-01T12:00:00'),('snooze','2020-01-01T12:00:00Z'),('claim','2030-01-01T12:00:00Z')]:
            assert (await api.post(url+'/events',**event_command(case['revision_id'],action,until=until))).status_code==422
        missing=event_command(case['revision_id']);missing['headers'].pop('If-Match')
        assert (await api.post(url+'/events',**missing)).status_code==428
        claim=(await api.post(url+'/events',**event_command(case['revision_id']))).json()
        release=(await api.post(url+'/events',**event_command(claim['id'],'release'))).json();assert release['claimant_id'] is None
        until=datetime.now(timezone.utc)+timedelta(days=1)
        sleep=event_command(release['id'],'snooze',until=until.isoformat())
        snoozed=(await api.post(url+'/events',**sleep)).json()
        equivalent=event_command(release['id'],'snooze',key=sleep['headers']['Idempotency-Key'],until=until.astimezone(timezone(timedelta(hours=2))).isoformat())
        assert (await api.post(url+'/events',**equivalent)).json()==snoozed
        reopened=(await api.post(url+'/events',**event_command(snoozed['id'],'reopen'))).json();assert reopened['snoozed_until'] is None
        # Either command order preserves new evidence and cannot leave an old-version snooze effective.
        race=await asyncio.gather(api.post(url+'/events',**event_command(reopened['id'],'snooze',until=until.isoformat())),
            api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**proposal_command(partition,extraction,ids['other_scope'],previous=proposal['id'])))
        assert race[0].status_code in (201,409) and race[1].status_code==201
        assert (await api.get(url)).json()['workflow_state']=='open'
        first=(await api.get(url+'/events',params={'limit':2})).json();assert len(first['items'])==2
        second=(await api.get(url+'/events',params={'limit':2,'before_revision':first['next_before_revision']})).json()
        assert not ({r['id'] for r in first['items']}&{r['id'] for r in second['items']})
        deps=(await api.get(url+'/dependencies',params={'limit':1})).json();assert len(deps['items'])==1 and deps['next_before_revision']
        older=(await api.get(url+'/dependencies',params={'limit':1,'before_revision':deps['next_before_revision']})).json()
        assert older['items'][0]['assignment_proposal_id']==proposal['id']
        # A second source produces a second case and a cursor bound to actor and filter.
        await setup_case(api,ids)
        first=(await api.get('/api/exceptions',params={'limit':1})).json();assert first['next_cursor']
        second=(await api.get('/api/exceptions',params={'limit':1,'cursor':first['next_cursor']})).json()
        assert len(second['items'])==1 and second['items'][0]['id']!=first['items'][0]['id']
        assert (await api.get('/api/exceptions',params={'workflow_state':'snoozed','cursor':first['next_cursor']})).status_code==422
        async with client(ids,'actor') as admin:
            assert (await admin.get('/api/exceptions',params={'cursor':first['next_cursor']})).status_code==422


async def test_exception_backfill_constraints_coverage_and_downgrade_guard(migrator_session,scope_seed,store):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_access_coverage import capture
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        _,_,_,_,proposal,_=await setup_case(api,ids)
        case=(await api.get('/api/exceptions')).json()['items'][0]
        before=await capture(admin)
        assert (await api.post('/api/exceptions/'+case['id']+'/events',**event_command(case['revision_id']))).status_code==201
        assert (await capture(admin))['manifest_digest']!=before['manifest_digest']
    migration=importlib.import_module('migrations.versions.0102_exception_cases');engine=create_engine(MIGRATOR_SYNC_URL)
    params={**ids,'case':uuid.UUID(case['id']),'proposal':uuid.UUID(proposal['id']),'extraction':uuid.UUID(proposal['extraction_revision_id'])}
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain exception'),db.begin_nested():migration.downgrade()
            # Cause and case/source/scope identity are independent database checks.
            for cause,scope in [('unsupported_scope_type','other_scope'),('scope_hint_conflict','scope')]:
                with pytest.raises(DBAPIError,match='cause missing|foreign key'),db.begin_nested():
                    db.execute(text('TRUNCATE exception_dependency CASCADE'))
                    db.execute(text(f"INSERT INTO exception_dependency(organization_id,case_id,scope_id,cause,extraction_revision_id,assignment_proposal_id) VALUES(:org,:case,:{scope},:cause,:extraction,:proposal)"),{**params,'cause':cause})
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE exception_case CASCADE'))
                oid=db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))
                migration.downgrade();migration.upgrade()
                assert db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))==oid
                assert db.scalar(text('SELECT count(*) FROM exception_case WHERE organization_id=:org'),ids)==1
                assert db.scalar(text("SELECT count(*) FROM exception_case_event WHERE organization_id=:org AND action='evidence_added' AND actor_id IS NULL AND claimant_id IS NULL"),ids)==1
                assert db.scalar(text('SELECT count(*) FROM exception_case_event WHERE organization_id=:org'),ids)==1
                db.execute(text('SELECT record_assignment_exceptions(:org,:proposal)'),params)
                assert db.scalar(text('SELECT count(*) FROM exception_case_event WHERE organization_id=:org'),ids)==1
            finally:point.rollback()
        assert await read_value('SELECT count(*) FROM exception_case_event WHERE organization_id=:org',ids)==2
    finally:engine.dispose()


async def test_exception_snooze_expiry_preserves_receipt_and_proposal_rollback_is_atomic(migrator_session,scope_seed,store):
    from app.core.db import get_tenant_session
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,child,partition,extraction,proposal,_=await setup_case(api,ids)
        case=(await api.get('/api/exceptions')).json()['items'][0];url='/api/exceptions/'+case['id']
        params={**ids,'logical':uuid.UUID(child['id']),'partition':uuid.UUID(partition['id']),'extraction':uuid.UUID(extraction['id']),
            'prior':uuid.UUID(proposal['id']),'request':uuid.uuid4()}
        with pytest.raises(RuntimeError,match='simulated crash'):
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                assert await db.scalar(text("SELECT create_assignment_proposal(:org,:logical,:partition,:extraction,:other_scope,:prior,:request,'New blocked evidence')"),params)
                assert await db.scalar(text('SELECT count(*) FROM exception_dependency WHERE organization_id=:org'),ids)==2
                raise RuntimeError('simulated crash')
        assert (await api.get(url)).json()['revision_number']==1
        assert await read_value('SELECT count(*) FROM assignment_proposal WHERE organization_id=:org',ids)==1
        body=event_command(case['revision_id'],'snooze',until=(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat())
        reply=await api.post(url+'/events',**body);assert reply.status_code==201,reply.text
        receipt=reply.json()
        await asyncio.sleep(2.1)
        current=(await api.get(url)).json();assert current['workflow_state']=='open' and current['revision_id']==receipt['id']
        assert current['resolution_state']=='unresolved'
        assert (await api.post(url+'/events',**body)).json()==receipt
        assert (await api.get('/api/exceptions',params={'workflow_state':'snoozed'})).json()['items']==[]
