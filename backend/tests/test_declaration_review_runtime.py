"""Ordinary runtime SQL cannot impersonate the trusted semantic review runtime."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.db import get_tenant_session
from tests.test_derived_source_access import committed_scope,actor_session  # noqa: F401
from tests.test_financial_scope_boundary import scope_seed,worker_tenant  # noqa: F401
from tests.test_declaration_import_api import isolated_document_store,enable_declaration_imports  # noqa: F401
from tests.test_declaration_review_commands import review_seed,COMMAND,command_params
from tests.test_property_source_enforcement import synthetic_enforcement
from tests.test_scoped_account_statements import mutate

pytestmark=pytest.mark.asyncio


async def test_ordinary_runtime_cannot_supply_unvalidated_correction(committed_scope):
    seed=committed_scope;params=await review_seed(seed)
    async with synthetic_enforcement(seed),get_tenant_session(params['org']) as db:
        await actor_session(db,params['actor'])
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text(COMMAND),command_params(params,decision='CORRECTED',correction='{}'))


async def test_ordinary_runtime_cannot_mark_managed_issue_resolved(committed_scope):
    seed=committed_scope;params=await review_seed(seed)
    await mutate(text("INSERT INTO declaration_candidate_issue(organization_id,import_id,candidate_id,code,severity,message) VALUES(:org,:job,:candidate,'CONFLICTING_ALTERNATIVES','BLOCKER','Synthetic')"),params)
    async with synthetic_enforcement(seed),get_tenant_session(params['org']) as db:
        await actor_session(db,params['actor'])
        await db.execute(text("SELECT set_config('app.review_validated','true',true)"))
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text("UPDATE declaration_candidate_issue SET resolution_status='RESOLVED',resolved_by=:actor,resolved_at=clock_timestamp() WHERE import_id=:job"),params)


ISSUE_COMMAND='SELECT apply_declaration_issue_revalidation(:org,:job,:revision,CAST(:effects AS jsonb))'


async def managed_issue(params):
    import uuid
    issue=uuid.uuid4()
    await mutate(text("INSERT INTO declaration_candidate_issue(id,organization_id,import_id,candidate_id,code,severity,message) VALUES(:issue,:org,:job,:candidate,'CONFLICTING_ALTERNATIVES','BLOCKER','Synthetic')"),{**params,'issue':issue})
    return issue


@pytest.mark.parametrize('runtime',['web','worker'])
async def test_review_role_cannot_be_assumed_or_commands_called_by_other_runtime(committed_scope,runtime):
    from app.core.db import get_worker_tenant_session
    seed=committed_scope;params=await review_seed(seed)
    factory=get_tenant_session if runtime=='web' else get_worker_tenant_session
    async with synthetic_enforcement(seed),factory(params['org']) as db:
        await actor_session(db,params['actor'])
        for sql in ['SET LOCAL ROLE app_review','SET SESSION AUTHORIZATION app_review',COMMAND,ISSUE_COMMAND]:
            with pytest.raises(DBAPIError):
                async with db.begin_nested(): await db.execute(text(sql),{**command_params(params),'effects':'[]'})


async def test_review_role_has_only_graph_reads_and_narrow_commands(committed_scope):
    from app.core.db import get_review_tenant_session
    seed=committed_scope;params=await review_seed(seed)
    async with synthetic_enforcement(seed),get_review_tenant_session(params['org']) as db:
        await actor_session(db,params['actor'])
        for table in ['declaration_candidate','declaration_import','declaration_candidate_issue','person','payment','audit_event','declaration_review_context']:
            for privilege in ['INSERT','UPDATE','DELETE','TRUNCATE']:
                assert not await db.scalar(text('SELECT has_table_privilege(current_user,:table,:privilege)'),{'table':table,'privilege':privilege})
        for table in ['person','payment','audit_event','declaration_review_context']:
            assert not await db.scalar(text("SELECT has_table_privilege(current_user,:table,'SELECT')"),{'table':table})
        for role in ['app_rw','app_worker','app_migrator']:
            assert not await db.scalar(text("SELECT pg_has_role(current_user,:role,'MEMBER')"),{'role':role})
        for signature in ['claim_declaration_work(uuid,text,integer)',
                          'apply_declaration_review_before_context(uuid,uuid,integer,uuid[],text,jsonb,text,boolean)']:
            assert not await db.scalar(text("SELECT has_function_privilege(current_user,:signature,'EXECUTE')"),{'signature':signature})


async def test_issue_revalidation_requires_same_transaction_actor_import_and_revision(committed_scope):
    import json
    from app.core.db import get_review_tenant_session
    seed=committed_scope;params=await review_seed(seed);issue=await managed_issue(params)
    other=await review_seed(seed)
    async with synthetic_enforcement(seed):
        async with get_review_tenant_session(params['org']) as db:
            await actor_session(db,params['actor'])
            effects=json.dumps([{'id':str(issue),'resolution_status':'RESOLVED'}])
            with pytest.raises(DBAPIError,match='Current review transaction context required'):
                async with db.begin_nested():
                    await db.execute(text(ISSUE_COMMAND),{**params,'revision':1,'effects':effects})
            assert await db.scalar(text(COMMAND),command_params(params))==2
            for values in [{**params,'revision':1},{**other,'revision':2}]:
                with pytest.raises(DBAPIError,match='Current review transaction context required'):
                    async with db.begin_nested(): await db.execute(text(ISSUE_COMMAND),{**values,'effects':effects})
            await db.execute(text(ISSUE_COMMAND),{**params,'revision':2,'effects':effects})
            assert await db.scalar(text('SELECT resolution_status FROM declaration_candidate_issue WHERE id=:issue'),{'issue':issue})=='RESOLVED'
        async with get_review_tenant_session(params['org']) as db:
            await actor_session(db,params['actor'])
            await db.execute(text("SELECT set_config('app.review_validated','true',true)"))
            with pytest.raises(DBAPIError,match='Current review transaction context required'):
                async with db.begin_nested():
                    await db.execute(text(ISSUE_COMMAND),{**params,'revision':2,'effects':effects})


@pytest.mark.parametrize('effect',[
    {'resolution_status':'RESOLVED','severity':'INFO'},
    {'resolution_status':'IGNORED'},
    {'resolution_status':'RESOLVED','resolved_by':'forged'},
    {'resolution_status':'RESOLVED','message':'Forged'},
])
async def test_issue_command_cannot_rewrite_source_or_resolution_identity(committed_scope,effect):
    import json
    from app.core.db import get_review_tenant_session
    seed=committed_scope;params=await review_seed(seed);issue=await managed_issue(params)
    async with synthetic_enforcement(seed),get_review_tenant_session(params['org']) as db:
        await actor_session(db,params['actor'])
        assert await db.scalar(text(COMMAND),command_params(params))==2
        with pytest.raises(DBAPIError,match='INVALID_ISSUE_EFFECTS'):
            async with db.begin_nested():
                await db.execute(text(ISSUE_COMMAND),{**params,'revision':2,'effects':json.dumps([{'id':str(issue),**effect}])})
        assert (await db.execute(text('SELECT severity,message,resolution_status FROM declaration_candidate_issue WHERE id=:issue'),{'issue':issue})).one()==('BLOCKER','Synthetic','OPEN')


async def test_review_validator_failure_after_decision_rolls_back_all_effects(committed_scope,monkeypatch):
    from app.repositories.declaration_review_repo import DeclarationReviewRepository
    from tests.test_scoped_account_statements import client
    seed=committed_scope;params=await review_seed(seed)
    async def fail(*args,**kwargs): raise RuntimeError('synthetic validation failure')
    monkeypatch.setattr(DeclarationReviewRepository,'apply_issue_revalidation',fail)
    async with synthetic_enforcement(seed),client({'org':params['org'],'actor':params['actor']},'actor') as api:
        response=await api.patch(f"/api/declaration-imports/{params['job']}/candidates/{params['candidate']}",
            json={'draft_revision':1,'decision':'ACCEPTED'})
        assert response.status_code==503
        async with get_tenant_session(params['org']) as db:
            await actor_session(db,params['actor'])
            assert await db.scalar(text('SELECT draft_revision FROM declaration_import WHERE id=:job'),params)==1
            assert await db.scalar(text('SELECT review_status FROM declaration_candidate WHERE id=:candidate'),params)=='PENDING'
            assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:job AND action='declaration_import.candidate_reviewed'"),params)==0


@pytest.mark.parametrize('change,repair',[
    ('GRANT app_rw TO app_review','REVOKE app_rw FROM app_review'),
    ('GRANT app_review TO app_rw','REVOKE app_review FROM app_rw'),
    ('ALTER ROLE app_review INHERIT','ALTER ROLE app_review NOINHERIT'),
    ('ALTER ROLE app_review REPLICATION','ALTER ROLE app_review NOREPLICATION'),
    ('GRANT UPDATE(first_name) ON person TO app_review','REVOKE UPDATE(first_name) ON person FROM app_review'),
    ('GRANT SELECT ON person TO app_review','REVOKE SELECT ON person FROM app_review'),
])
async def test_review_connection_rejects_role_drift(seed_data,change,repair):
    from app.core.db import get_review_tenant_session
    try:
        await mutate(text(change),{})
        with pytest.raises(RuntimeError,match='isolated app_review'):
            async with get_review_tenant_session(seed_data['org_a_id']): pytest.fail('Unsafe review role accepted')
    finally: await mutate(text(repair),{})


async def test_review_connection_has_no_fallback_to_web_credentials(seed_data,monkeypatch):
    from app.core import db
    monkeypatch.setattr(db,'_review_session_factory',db.get_session_factory())
    with pytest.raises(RuntimeError,match='isolated app_review'):
        async with db.get_review_tenant_session(seed_data['org_a_id']): pytest.fail('Web role accepted as review validator')
    monkeypatch.setattr(db,'_review_session_factory',None)
    monkeypatch.setattr(db.settings,'database_url_review',None)
    with pytest.raises(RuntimeError,match='HV_DATABASE_URL_REVIEW'):
        db.get_review_session_factory()


async def test_review_receipt_cannot_cross_committed_transaction_on_same_backend(committed_scope):
    from app.core.db import get_review_session_factory,set_tenant_context
    from sqlalchemy.ext.asyncio import AsyncSession
    seed=committed_scope;params=await review_seed(seed)
    async with synthetic_enforcement(seed),get_review_session_factory().kw['bind'].connect() as connection:
        async with AsyncSession(bind=connection) as db:
            async with db.begin():
                await set_tenant_context(db,params['org']);await actor_session(db,params['actor'])
                assert await db.scalar(text(COMMAND),command_params(params))==2
                pid=await db.scalar(text('SELECT pg_backend_pid()'))
            async with db.begin():
                await set_tenant_context(db,params['org']);await actor_session(db,params['actor'])
                assert await db.scalar(text('SELECT pg_backend_pid()'))==pid
                with pytest.raises(DBAPIError,match='Current review transaction context required'):
                    async with db.begin_nested():
                        await db.execute(text(ISSUE_COMMAND),{**params,'revision':2,'effects':'[]'})


async def test_review_context_cannot_resolve_another_import_issue(committed_scope):
    import json
    from app.core.db import get_review_tenant_session
    seed=committed_scope;params=await review_seed(seed);other=await review_seed(seed);other_issue=await managed_issue(other)
    async with synthetic_enforcement(seed),get_review_tenant_session(params['org']) as db:
        await actor_session(db,params['actor'])
        assert await db.scalar(text(COMMAND),command_params(params))==2
        with pytest.raises(DBAPIError,match='INVALID_ISSUE_EFFECTS'):
            async with db.begin_nested():
                await db.execute(text(ISSUE_COMMAND),{**params,'revision':2,'effects':json.dumps([{'id':str(other_issue),'resolution_status':'RESOLVED'}])})
        assert await db.scalar(text('SELECT resolution_status FROM declaration_candidate_issue WHERE id=:issue'),{'issue':other_issue})=='OPEN'


async def test_review_runtime_migration_roundtrip_preserves_decisions(committed_scope):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    seed=committed_scope;params=await review_seed(seed)
    migration=importlib.import_module('migrations.versions.0073_declaration_review_runtime');engine=create_engine(MIGRATOR_SYNC_URL)
    dependencies='LOCK TABLE '+','.join(migration.TABLES)+',declaration_review_context IN ACCESS EXCLUSIVE MODE'
    try:
        async with synthetic_enforcement(seed):
            with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                # Exercise the semantic guard after incidental table contention;
                # busy-dependency refusal is covered separately below.
                db.execute(text("SET LOCAL lock_timeout='5s'"))
                db.execute(text(dependencies))
                with pytest.raises(DBAPIError,match='Retain isolated review validation'):
                    with db.begin_nested(): migration.downgrade()
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            db.execute(text("SET LOCAL lock_timeout='5s'"))
            db.execute(text(dependencies))
            point=db.begin_nested()
            try:
                before=db.scalar(text("SELECT md5(COALESCE(string_agg(to_jsonb(c)::text,',' ORDER BY id),'')) FROM declaration_candidate c"))
                migration.downgrade();migration.upgrade()
                assert db.scalar(text("SELECT md5(COALESCE(string_agg(to_jsonb(c)::text,',' ORDER BY id),'')) FROM declaration_candidate c"))==before
                assert not db.scalar(text("SELECT has_function_privilege('app_rw','apply_declaration_review(uuid,uuid,integer,uuid[],text,jsonb,text,boolean)','EXECUTE')"))
                assert db.scalar(text("SELECT has_function_privilege('app_review','apply_declaration_review(uuid,uuid,integer,uuid[],text,jsonb,text,boolean)','EXECUTE')"))
                assert not db.scalar(text('SELECT property_source_activation_ready(:org)'),params)
            finally: point.rollback()
    finally: engine.dispose()


@pytest.mark.parametrize('table',['declaration_candidate','declaration_candidate_issue','declaration_review_context','audit_event','membership'])
async def test_review_runtime_migration_refuses_busy_dependency(seed_data,table):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0073_declaration_review_runtime');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as reader:
            reader.execute(text(f'SELECT 1 FROM {table} LIMIT 1'))
            with pytest.raises(DBAPIError) as denied:
                with engine.begin() as db,Operations.context(MigrationContext.configure(db)): migration.downgrade()
            assert denied.value.orig.pgcode=='55P03'
    finally: engine.dispose()


@pytest.mark.parametrize('change',[
    'GRANT app_rw TO app_review', 'GRANT app_review TO app_rw',
    'ALTER ROLE app_review INHERIT','ALTER ROLE app_review REPLICATION',
    'ALTER TABLE declaration_candidate OWNER TO app_review',
    'GRANT UPDATE(first_name) ON person TO app_review',
    'GRANT SELECT ON person TO app_review',
])
async def test_review_runtime_migration_rejects_unsafe_role(seed_data,change):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0073_declaration_review_runtime');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            point=db.begin_nested()
            try:
                db.execute(text(change))
                with pytest.raises(DBAPIError,match='Provision isolated app_review'):
                    with db.begin_nested(): migration.upgrade()
            finally: point.rollback()
    finally: engine.dispose()


async def test_expired_review_context_rolls_back_entire_http_review(committed_scope):
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    from tests.test_scoped_account_statements import client
    seed=committed_scope;params=await review_seed(seed);engine=create_async_engine(MIGRATOR_URL)
    # Disposable-only clock fault injection: expiry is forced after the decision
    # command has written all its effects but before issue revalidation starts.
    try:
        async with engine.begin() as db:
            await db.execute(text("""CREATE FUNCTION test_expire_review_context() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN NEW.expires_at:=clock_timestamp()-interval '1 second';RETURN NEW;END $$"""))
            await db.execute(text('CREATE TRIGGER test_expire_review_context BEFORE INSERT ON declaration_review_context FOR EACH ROW EXECUTE FUNCTION test_expire_review_context()'))
        async with synthetic_enforcement(seed),client({'org':params['org'],'actor':params['actor']},'actor') as api:
            response=await api.patch(f"/api/declaration-imports/{params['job']}/candidates/{params['candidate']}",
                json={'draft_revision':1,'decision':'ACCEPTED'})
            assert response.status_code==409,response.text
            assert response.json()['detail']=={'code':'REVIEW_AUTHORITY_CHANGED'}
            async with get_tenant_session(params['org']) as db:
                await actor_session(db,params['actor'])
                assert await db.scalar(text('SELECT draft_revision FROM declaration_import WHERE id=:job'),params)==1
                assert await db.scalar(text('SELECT review_status FROM declaration_candidate WHERE id=:candidate'),params)=='PENDING'
                assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:job AND action='declaration_import.candidate_reviewed'"),params)==0
    finally:
        async with engine.begin() as db:
            await db.execute(text('DROP TRIGGER IF EXISTS test_expire_review_context ON declaration_review_context'))
            await db.execute(text('DROP FUNCTION IF EXISTS test_expire_review_context()'))
        await engine.dispose()
