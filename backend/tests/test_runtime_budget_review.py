"""Only the trusted runtime can attest the configuration an administrator reviews."""
import uuid
from decimal import Decimal
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_access_coverage import fixture,capture
from tests.test_scoped_account_statements import client
from tests.test_scope_payment_reconciliation import read_value

pytestmark = pytest.mark.asyncio
PATH = '/api/access-preparation/runtime-budget'


async def test_runtime_budget_review_is_explicit_idempotent_and_snapshot_visible(migrator_session,scope_seed):
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        status = await admin.get(PATH)
        assert status.status_code == 200,status.text
        state = status.json()
        assert state['matches_reviewed_policy'] is False and state['review'] is None
        assert await read_value('SELECT count(*) FROM knowledge_budget_policy WHERE organization_id=:org',ids)==0
        key = str(uuid.uuid4())
        body = {'expected_review_id':None,'policy_digest':state['policy_digest'],'reason':'Reviewed configured limits','acknowledged':True}
        reviewed = await admin.post(PATH+'/review',json=body,headers={'Idempotency-Key':key})
        assert reviewed.status_code == 201,reviewed.text
        assert (await admin.post(PATH+'/review',json=body,headers={'Idempotency-Key':key})).json()==reviewed.json()
        assert (await admin.get(PATH)).json()['matches_reviewed_policy'] is True
        assert (await admin.post(PATH+'/review',json={**body,'reason':'Different decision'},headers={'Idempotency-Key':key})).status_code==409
        snapshot = await capture(admin)
        assert snapshot['checker_version']==3 and snapshot['activation_available'] is False
        rows = (await admin.get(f"/api/access-preparation/snapshots/{snapshot['id']}/items",params={'limit':200})).json()['items']
        budget = next(row for row in rows if row['kind']=='knowledge_budget_policy')
        assert budget['findings']==[]


async def test_changed_runtime_policy_requires_fresh_review_and_replay_never_restores_it(migrator_session,scope_seed,monkeypatch):
    from app.core.config import settings
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        state = (await admin.get(PATH)).json()
        body = {'expected_review_id':None,'policy_digest':state['policy_digest'],'reason':'First policy','acknowledged':True}
        key = str(uuid.uuid4())
        first = await admin.post(PATH+'/review',json=body,headers={'Idempotency-Key':key})
        assert first.status_code==201,first.text
        monkeypatch.setattr(settings,'declaration_qa_max_cost_usd',settings.declaration_qa_max_cost_usd+Decimal('0.01'))
        current = (await admin.get(PATH)).json()
        assert current['policy_digest']!=state['policy_digest'] and not current['matches_reviewed_policy']
        assert (await admin.post(PATH+'/review',json=body,headers={'Idempotency-Key':str(uuid.uuid4())})).status_code==409
        second = await admin.post(PATH+'/review',json={**body,'expected_review_id':first.json()['id'],'policy_digest':current['policy_digest']},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert second.status_code==201,second.text
        assert (await admin.post(PATH+'/review',json=body,headers={'Idempotency-Key':key})).json()==first.json()
        assert (await admin.get(PATH)).json()['review']['id']==second.json()['id']


async def test_untrusted_runtime_cannot_self_attest_or_approve_and_clerk_is_denied(migrator_session,scope_seed):
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids) as clerk:
        assert (await clerk.get(PATH)).status_code==403
    with pytest.raises(DBAPIError,match='permission denied'):
        await read_value("SELECT attest_runtime_budget(:org,pg_backend_pid(),pg_current_xact_id(),jsonb_build_object('enabled',false))",ids,actor_key='actor')
    with pytest.raises(DBAPIError,match='Trusted runtime policy'):
        await read_value("SELECT review_runtime_budget(:org,:request,NULL,:digest,'Forged policy')",{**ids,'request':uuid.uuid4(),'digest':'a'*64},actor_key='actor')


async def context(db, ids, actor='actor'):
    await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
                     {'org':str(ids['org']),'actor':str(ids[actor])})


async def test_attestation_is_bound_to_connection_transaction_and_actor(migrator_session,scope_seed):
    from app.core.db import get_engine,get_tenant_session
    from app.core.runtime_budget import attest_runtime_budget,runtime_budget_policy
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.test_scoped_account_statements import mutate
    ids = await fixture(migrator_session,scope_seed)
    await mutate(text("UPDATE membership SET role='OWNER_ADMIN' WHERE organization_id=:org AND user_id=:clerk"),ids)
    async with get_engine().connect() as connection, AsyncSession(bind=connection) as db:
        async with db.begin():
            await context(db,ids)
            pid = await db.scalar(text('SELECT pg_backend_pid()'))
            await attest_runtime_budget(db,ids['org'],ids['actor'],runtime_budget_policy())
            assert await db.scalar(text('SELECT runtime_budget_status(:org)'),ids)
            async with get_tenant_session(ids['org']) as other:
                await context(other,ids)
                assert await other.scalar(text('SELECT pg_backend_pid()')) != pid
                with pytest.raises(DBAPIError,match='Trusted runtime policy required'):
                    async with other.begin_nested():
                        await other.scalar(text('SELECT runtime_budget_status(:org)'),ids)
            await context(db,ids,'clerk')
            with pytest.raises(DBAPIError,match='Trusted runtime policy required'):
                async with db.begin_nested():
                    await db.scalar(text('SELECT runtime_budget_status(:org)'),ids)
            await context(db,ids)
            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.scalar(text('SELECT runtime_budget_status(:other)'),{'other':uuid.uuid4()})
        async with db.begin():
            await context(db,ids)
            assert await db.scalar(text('SELECT pg_backend_pid()')) == pid
            with pytest.raises(DBAPIError,match='Trusted runtime policy required'):
                async with db.begin_nested():
                    await db.scalar(text('SELECT runtime_budget_status(:org)'),ids)


async def test_equivalent_policy_has_identical_digest_but_request_cannot_change(migrator_session,scope_seed):
    import json
    from app.core.db import get_tenant_session
    from app.core.runtime_budget import attest_runtime_budget,runtime_budget_policy
    ids = await fixture(migrator_session,scope_seed)
    policy = json.loads(runtime_budget_policy())
    async with get_tenant_session(ids['org']) as db:
        await context(db,ids)
        first = await attest_runtime_budget(db,ids['org'],ids['actor'],json.dumps({**policy,'max_cost_usd':'0.0500'}))
        same = await attest_runtime_budget(db,ids['org'],ids['actor'],json.dumps({**policy,'max_cost_usd':'0.05'}))
        assert same==first
        with pytest.raises(DBAPIError,match='changed within request'):
            await attest_runtime_budget(db,ids['org'],ids['actor'],json.dumps({**policy,'max_cost_usd':'0.06'}))


@pytest.mark.parametrize('same_key',[False,True])
async def test_concurrent_reviews_have_one_effect(migrator_session,scope_seed,same_key):
    import asyncio
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        state = (await admin.get(PATH)).json()
        body = {'expected_review_id':None,'policy_digest':state['policy_digest'],'reason':'Concurrent review','acknowledged':True}
        first,second = str(uuid.uuid4()),str(uuid.uuid4())
        responses = await asyncio.gather(*(admin.post(PATH+'/review',json=body,headers={'Idempotency-Key':key})
            for key in (first,first if same_key else second)))
        assert sorted(r.status_code for r in responses)==([201,201] if same_key else [201,409])
        if same_key: assert responses[0].json()==responses[1].json()
    assert await read_value('SELECT count(*) FROM runtime_budget_review WHERE organization_id=:org',ids)==1
    assert await read_value("SELECT count(*) FROM audit_event WHERE organization_id=:org AND action='knowledge.runtime_budget_reviewed'",ids)==1


async def test_failed_audit_rolls_back_review_and_budget(migrator_session,scope_seed):
    from app.core.db import get_tenant_session
    from app.core.runtime_budget import attest_runtime_budget,runtime_budget_policy
    from tests.test_scoped_account_statements import mutate
    ids = await fixture(migrator_session,scope_seed)
    await mutate(text("""CREATE FUNCTION test_reject_budget_review() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN IF NEW.action='knowledge.runtime_budget_reviewed' THEN RAISE EXCEPTION 'Injected budget audit failure'; END IF; RETURN NEW; END $$"""),{})
    await mutate(text('CREATE TRIGGER zz_test_reject_budget_review BEFORE INSERT ON audit_event FOR EACH ROW EXECUTE FUNCTION test_reject_budget_review()'),{})
    try:
        with pytest.raises(DBAPIError,match='Injected budget audit failure'):
            async with get_tenant_session(ids['org']) as db:
                await context(db,ids)
                proof = await attest_runtime_budget(db,ids['org'],ids['actor'],runtime_budget_policy())
                await db.scalar(text("SELECT review_runtime_budget(:org,:request,NULL,:digest,'Atomic review')"),
                    {**ids,'request':uuid.uuid4(),'digest':proof['policy_digest']})
        assert await read_value('SELECT count(*) FROM knowledge_budget_policy WHERE organization_id=:org',ids)==0
        assert await read_value('SELECT count(*) FROM runtime_budget_review WHERE organization_id=:org',ids)==0
        assert await read_value('SELECT count(*) FROM runtime_budget_attestation WHERE organization_id=:org',ids)==1
    finally:
        await mutate(text('DROP TRIGGER zz_test_reject_budget_review ON audit_event'),{})
        await mutate(text('DROP FUNCTION test_reject_budget_review()'),{})


@pytest.mark.parametrize('failure',[RuntimeError('unconfigured'),OSError('connection refused')])
async def test_runtime_unavailable_and_invalid_confirmation_cannot_approve(migrator_session,scope_seed,monkeypatch,failure):
    from contextlib import asynccontextmanager
    from app.core import runtime_budget
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        state = (await admin.get(PATH)).json()
        body = {'policy_digest':state['policy_digest'],'reason':'Review','acknowledged':False}
        assert (await admin.post(PATH+'/review',json=body,headers={'Idempotency-Key':str(uuid.uuid4())})).status_code==422
        @asynccontextmanager
        async def unavailable(org):
            raise failure
            yield
        monkeypatch.setattr(runtime_budget,'get_review_tenant_session',unavailable)
        response = await admin.get(PATH)
        assert response.status_code==503
    assert await read_value('SELECT count(*) FROM runtime_budget_review WHERE organization_id=:org',ids)==0


async def test_registrar_progresses_while_revocation_waits(migrator_session,scope_seed):
    import asyncio
    from app.core.db import get_tenant_session
    from app.core.runtime_budget import attest_runtime_budget,runtime_budget_policy
    ids = await fixture(migrator_session,scope_seed)
    async def queued_writer():
        async with get_tenant_session(ids['org']) as writer:
            await writer.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),{'key':f"scope-acl:{ids['org']}"})
    async with get_tenant_session(ids['org']) as db:
        await context(db,ids)
        await db.execute(text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key,0))"),{'key':f"scope-acl:{ids['org']}"})
        pid = await db.scalar(text('SELECT pg_backend_pid()'))
        task = asyncio.create_task(queued_writer())
        try:
            for _ in range(200):
                if await read_value("SELECT count(*) FROM pg_stat_activity WHERE wait_event='advisory' AND :pid=ANY(pg_blocking_pids(pid))",{**ids,'pid':pid}):
                    break
                await asyncio.sleep(.01)
            else: pytest.fail('Writer never reached authorization lock')
            proof = await asyncio.wait_for(attest_runtime_budget(db,ids['org'],ids['actor'],runtime_budget_policy()),2)
            assert proof['policy_digest']
        finally:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)


async def test_budget_migration_retains_evidence_and_preserves_historical_reports(migrator_session,scope_seed):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        await admin.get(PATH)
    migration = importlib.import_module('migrations.versions.0088_runtime_budget_review')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain runtime budget evidence'):
                with db.begin_nested(): migration.downgrade()
            point = db.begin_nested()
            try:
                from tests.conftest import clear_disposable_source_context
                clear_disposable_source_context(db.connection.cursor())
                from pathlib import Path
                from alembic.script import ScriptDirectory
                versions = ScriptDirectory(str(Path(__file__).resolve().parents[1] / 'migrations'))
                for revision in versions.iterate_revisions('head', '0088'):
                    revision.module.downgrade()
                migration.downgrade()
                for version in (1,2):
                    db.execute(text("INSERT INTO access_coverage_snapshot(organization_id,generated_by,request_key,request_digest,manifest_digest,manifest,item_count,finding_count,checker_version) VALUES(:org,:actor,:request,repeat('a',64),repeat('b',64),jsonb_build_object('checker_version',:version,'items','[]'::jsonb),0,0,:version)"),
                        {**ids,'request':uuid.uuid4(),'version':version})
                before = db.scalar(text('SELECT jsonb_agg(to_jsonb(s) ORDER BY id)::text FROM access_coverage_snapshot s'))
                migration.upgrade()
                assert db.scalar(text('SELECT jsonb_agg(to_jsonb(s) ORDER BY id)::text FROM access_coverage_snapshot s'))==before
                for role in ('app_rw','app_worker','app_review'):
                    for table in ('runtime_budget_review','runtime_budget_attestation'):
                        assert not db.scalar(text("SELECT has_table_privilege(:role,:table,'SELECT,INSERT,UPDATE,DELETE')"),{'role':role,'table':table})
                migration.downgrade()
                assert db.scalar(text('SELECT jsonb_agg(to_jsonb(s) ORDER BY id)::text FROM access_coverage_snapshot s'))==before
            finally: point.rollback()
    finally: engine.dispose()


async def test_budget_review_stales_prior_snapshot_and_replay_requires_current_admin(migrator_session,scope_seed):
    from tests.test_scoped_account_statements import mutate
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        before = await capture(admin)
        state = (await admin.get(PATH)).json()
        key = str(uuid.uuid4())
        body = {'policy_digest':state['policy_digest'],'reason':'New budget evidence','acknowledged':True}
        assert (await admin.post(PATH+'/review',json=body,headers={'Idempotency-Key':key})).status_code==201
        stale = await admin.post(f"/api/access-preparation/snapshots/{before['id']}/review",json={
            'manifest_digest':before['manifest_digest'],'acknowledged':True},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert stale.status_code==409
        await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:actor"),ids)
        assert (await admin.post(PATH+'/review',json=body,headers={'Idempotency-Key':key})).status_code==403
