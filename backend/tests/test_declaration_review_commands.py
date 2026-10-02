"""Atomic decision stamps, revision advancement and canonical audit evidence."""
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.db import get_tenant_session
from tests.test_derived_source_access import committed_scope,actor_session  # noqa: F401
from tests.test_financial_scope_boundary import scope_seed,worker_tenant  # noqa: F401
from tests.test_declaration_import_api import isolated_document_store,enable_declaration_imports  # noqa: F401
from tests.test_declaration_worker_effects import effect_seed,CANDIDATE_SQL
from tests.test_property_source_enforcement import synthetic_enforcement
from tests.test_scoped_account_statements import mutate

pytestmark=pytest.mark.asyncio


async def review_seed(seed):
    lease,params=await effect_seed(seed)
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    engine=create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as db:
            params['candidate']=await db.scalar(text(CANDIDATE_SQL+' RETURNING id'),params)
            await db.execute(text("INSERT INTO declaration_candidate_evidence(organization_id,candidate_id,document_id,pdf_page,quote) VALUES(:org,:candidate,:document,1,'Synthetic')"),params)
            await db.execute(text("UPDATE declaration_import SET status='NEEDS_REVIEW',lease_token=NULL,lease_owner=NULL,lease_expires_at=NULL WHERE id=:job"),params)
    finally: await engine.dispose()
    params['actor']=seed['user_a_admin_id']
    return params


@pytest.mark.parametrize('sql',[
    "UPDATE declaration_import SET draft_revision=draft_revision+1 WHERE id=:job",
    "UPDATE declaration_candidate SET review_status='ACCEPTED',reviewed_at=clock_timestamp(),reviewed_by=:actor WHERE id=:candidate",
    "INSERT INTO audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff) VALUES(:org,:actor,'declaration_import',:job,'declaration_import.candidate_reviewed','{}')",
])
async def test_raw_review_effects_require_atomic_command(committed_scope,sql):
    seed=committed_scope;params=await review_seed(seed)
    async with synthetic_enforcement(seed),get_tenant_session(params['org']) as db:
        await actor_session(db,params['actor'])
        with pytest.raises(DBAPIError,match='Atomic declaration review command required'):
            async with db.begin_nested(): await db.execute(text(sql),params)


COMMAND="""SELECT apply_declaration_review(:org,:job,:revision,CAST(:ids AS uuid[]),:decision,CAST(:correction AS jsonb),:comment,:bulk)"""


def command_params(params,**changes):
    return {**params,'revision':1,'ids':[params['candidate']],'decision':'ACCEPTED',
            'correction':None,'comment':None,'bulk':False,**changes}


async def test_command_owns_actor_revision_versions_audit_and_exact_current_revision_replay(committed_scope):
    seed=committed_scope;params=await review_seed(seed)
    async with synthetic_enforcement(seed),get_tenant_session(params['org']) as db:
        await actor_session(db,params['actor'])
        assert await db.scalar(text(COMMAND),command_params(params))==2
        candidate=(await db.execute(text('SELECT review_status,reviewed_by,reviewed_at,candidate_version FROM declaration_candidate WHERE id=:candidate'),params)).one()
        assert candidate.review_status=='ACCEPTED' and candidate.reviewed_by==params['actor'] and candidate.reviewed_at and candidate.candidate_version==2
        audit=(await db.execute(text("SELECT actor_user_id,payload_diff FROM audit_event WHERE entity_id=:job AND action='declaration_import.candidate_reviewed'"),params)).one()
        assert audit.actor_user_id==params['actor'] and audit.payload_diff=={
            'import_id':str(params['job']),'candidate_id':str(params['candidate']),'decision':'ACCEPTED','revision':2}
        assert await db.scalar(text(COMMAND),command_params(params,revision=2))==2
        assert (await db.execute(text('SELECT review_status,reviewed_by,reviewed_at,candidate_version FROM declaration_candidate WHERE id=:candidate'),params)).one()==candidate
        assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:job AND action='declaration_import.candidate_reviewed'"),params)==1
        with pytest.raises(DBAPIError,match='STALE_DRAFT_REVISION'):
            async with db.begin_nested(): await db.execute(text(COMMAND),command_params(params))


@pytest.mark.parametrize('change,code',[
    ({'revision':0},'STALE_DRAFT_REVISION'),({'ids':[]},'INVALID_REVIEW_COMMAND'),
    ({'decision':'PENDING'},'INVALID_REVIEW_COMMAND'),({'decision':'CORRECTED'},'INVALID_REVIEW_COMMAND'),
    ({'correction':'{}'},'INVALID_REVIEW_COMMAND'),({'bulk':True},'INDIVIDUAL_REVIEW_REQUIRED'),
    ({'decision':'NEEDS_CLARIFICATION'},'COMMENT_REQUIRED'),({'comment':'x'*2001},'INVALID_REVIEW_COMMAND'),
])
async def test_command_invalid_envelopes_have_no_partial_effects(committed_scope,change,code):
    seed=committed_scope;params=await review_seed(seed)
    async with synthetic_enforcement(seed),get_tenant_session(params['org']) as db:
        await actor_session(db,params['actor'])
        with pytest.raises(DBAPIError,match=code):
            async with db.begin_nested(): await db.execute(text(COMMAND),command_params(params,**change))
        assert await db.scalar(text('SELECT draft_revision FROM declaration_import WHERE id=:job'),params)==1
        assert await db.scalar(text('SELECT review_status FROM declaration_candidate WHERE id=:candidate'),params)=='PENDING'
        assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:job AND action='declaration_import.candidate_reviewed'"),params)==0


@pytest.mark.parametrize('boundary',['worker','wrong_org','unknown_candidate','duplicate_candidate','missing_evidence','inactive_actor','hidden_actor'])
async def test_review_command_rejects_identity_and_source_boundaries(committed_scope,seed_data,boundary):
    from app.core.db import get_worker_tenant_session
    seed=committed_scope;params=await review_seed(seed);command=command_params(params)
    if boundary=='unknown_candidate': command['ids']=[uuid.uuid4()]
    if boundary=='duplicate_candidate': command.update(ids=[params['candidate']]*2,bulk=True)
    if boundary=='wrong_org': command['org']=seed_data['org_b_id']
    if boundary=='missing_evidence': await mutate(text('DELETE FROM declaration_candidate_evidence WHERE candidate_id=:candidate'),params)
    factory=get_worker_tenant_session if boundary=='worker' else get_tenant_session
    async with synthetic_enforcement(seed):
        if boundary=='inactive_actor': await mutate(text('UPDATE "user" SET is_active=false WHERE id=:actor'),params)
        try:
            async with factory(params['org']) as db:
                await actor_session(db,seed['user_a_clerk_id'] if boundary=='hidden_actor' else params['actor'])
                with pytest.raises(DBAPIError):
                    async with db.begin_nested(): await db.execute(text(COMMAND),command)
        finally:
            if boundary=='inactive_actor': await mutate(text('UPDATE "user" SET is_active=true WHERE id=:actor'),params)


async def test_command_reopens_managed_issues_without_trusting_caller_revalidation(committed_scope):
    seed=committed_scope;params=await review_seed(seed)
    await mutate(text("""INSERT INTO declaration_candidate_issue(organization_id,import_id,candidate_id,code,severity,message,
      resolution_status,resolved_by,resolved_at) VALUES(:org,:job,:candidate,'CONFLICTING_ALTERNATIVES','BLOCKER','Synthetic','RESOLVED',:actor,clock_timestamp())"""),params)
    async with synthetic_enforcement(seed),get_tenant_session(params['org']) as db:
        await actor_session(db,params['actor'])
        assert await db.scalar(text(COMMAND),command_params(params))==2
        assert (await db.execute(text('SELECT resolution_status,resolved_by,resolved_at FROM declaration_candidate_issue WHERE import_id=:job'),params)).one()==('OPEN',None,None)


async def test_command_and_caller_transaction_failure_roll_back_together(committed_scope):
    seed=committed_scope;params=await review_seed(seed)
    async with synthetic_enforcement(seed),get_tenant_session(params['org']) as db:
        await actor_session(db,params['actor'])
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                assert await db.scalar(text(COMMAND),command_params(params))==2
                await db.execute(text('SELECT 1/0'))
        assert await db.scalar(text('SELECT draft_revision FROM declaration_import WHERE id=:job'),params)==1
        assert await db.scalar(text('SELECT review_status FROM declaration_candidate WHERE id=:candidate'),params)=='PENDING'
        assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:job AND action='declaration_import.candidate_reviewed'"),params)==0


async def test_simultaneous_reviews_have_one_revision_winner(committed_scope):
    import asyncio
    seed=committed_scope;params=await review_seed(seed)
    async def review(decision):
        try:
            async with get_tenant_session(params['org']) as db:
                await actor_session(db,params['actor'])
                return await db.scalar(text(COMMAND),command_params(params,decision=decision))
        except DBAPIError as exc:
            assert 'STALE_DRAFT_REVISION' in str(exc.orig)
            return 'stale'
    async with synthetic_enforcement(seed):
        results=await asyncio.gather(review('ACCEPTED'),review('REJECTED'))
        assert sorted(results,key=str)==[2,'stale']
        async with get_tenant_session(params['org']) as db:
            await actor_session(db,params['actor'])
            assert await db.scalar(text('SELECT draft_revision FROM declaration_import WHERE id=:job'),params)==2
            assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:job AND action='declaration_import.candidate_reviewed'"),params)==1


async def test_waiting_review_rechecks_revoked_actor_after_acl_lock(committed_scope):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    seed=committed_scope;params=await review_seed(seed);engine=create_async_engine(MIGRATOR_URL)
    started=asyncio.Event()
    async def review():
        async with get_tenant_session(params['org']) as db:
            await actor_session(db,params['actor'])
            started.set()
            with pytest.raises(DBAPIError,match='Current human review authority required'):
                async with db.begin_nested(): await db.execute(text(COMMAND),command_params(params))
    task=None
    try:
        async with synthetic_enforcement(seed):
            try:
                async with engine.begin() as blocker:
                    await blocker.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('scope-acl:'||CAST(CAST(:org AS uuid) AS text),0))"),params)
                    await blocker.execute(text('UPDATE "user" SET is_active=false WHERE id=:actor'),params)
                    task=asyncio.create_task(review())
                    await asyncio.wait_for(started.wait(),5)
                    await asyncio.sleep(.1)
                    assert not task.done()
                await asyncio.wait_for(task,5)
            finally:
                if task and not task.done(): task.cancel()
                if task: await asyncio.gather(task,return_exceptions=True)
                await mutate(text('UPDATE "user" SET is_active=true WHERE id=:actor'),params)
    finally: await engine.dispose()


async def test_review_command_migration_preserves_history_and_refuses_strict_downgrade(committed_scope):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    seed=committed_scope;params=await review_seed(seed)
    migration=importlib.import_module('migrations.versions.0072_declaration_review_commands')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        async with synthetic_enforcement(seed):
            async with get_tenant_session(params['org']) as db:
                await actor_session(db,params['actor'])
                assert await db.scalar(text(COMMAND),command_params(params))==2
            with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError,match='Retain atomic declaration reviews'):
                    with db.begin_nested(): migration.downgrade()
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            point=db.begin_nested()
            try:
                snapshots={table:db.scalar(text(f"SELECT md5(COALESCE(string_agg(to_jsonb(r)::text,',' ORDER BY id),'')) FROM {table} r"))
                           for table in ['declaration_candidate','declaration_candidate_issue','declaration_import','audit_event']}
                migration.downgrade();migration.upgrade()
                for table,digest in snapshots.items():
                    assert db.scalar(text(f"SELECT md5(COALESCE(string_agg(to_jsonb(r)::text,',' ORDER BY id),'')) FROM {table} r"))==digest
                assert not db.scalar(text('SELECT property_source_activation_ready(:org)'),params)
            finally: point.rollback()
    finally: engine.dispose()


@pytest.mark.parametrize('table',['declaration_import','declaration_candidate','declaration_candidate_evidence','declaration_candidate_issue','audit_event','membership'])
async def test_review_command_migration_refuses_busy_dependency(seed_data,table):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0072_declaration_review_commands');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as reader:
            reader.execute(text(f'SELECT 1 FROM {table} LIMIT 1'))
            with pytest.raises(DBAPIError) as denied:
                with engine.begin() as db,Operations.context(MigrationContext.configure(db)): migration.downgrade()
            assert denied.value.orig.pgcode=='55P03'
    finally: engine.dispose()


async def test_strict_http_review_uses_atomic_command_and_refreshes_result(committed_scope):
    from tests.test_scoped_account_statements import client
    seed=committed_scope;params=await review_seed(seed)
    async with synthetic_enforcement(seed),client({'org':params['org'],'actor':params['actor']},'actor') as api:
        response=await api.patch(f"/api/declaration-imports/{params['job']}/candidates/{params['candidate']}",
            json={'draft_revision':1,'decision':'CORRECTED','corrected_value':'New verified name'})
        assert response.status_code==200,response.text
        assert response.json()['draft_revision']==2
        assert response.json()['candidate']['review_status']=='CORRECTED'
        assert response.json()['candidate']['corrected_value_json']=={'kind':'TEXT','value':'New verified name'}


async def test_bulk_low_risk_command_has_one_revision_and_one_audit_per_candidate(committed_scope):
    seed=committed_scope;params=await review_seed(seed)
    await mutate(text("UPDATE declaration_candidate SET candidate_type='UNIT_FIELD',subject_key='unit:1',field_key='area',proposed_value_json='{\"kind\":\"DECIMAL\",\"value\":\"50\",\"unit\":\"m2\"}' WHERE id=:candidate"),params)
    params['second']=uuid.uuid4()
    await mutate(text("""INSERT INTO declaration_candidate(id,organization_id,import_id,stable_key,candidate_type,subject_key,field_key,
      operation,proposed_value_json,confidence,risk_class) SELECT :second,organization_id,import_id,'bulk-second',candidate_type,
      'unit:2',field_key,operation,proposed_value_json,confidence,risk_class FROM declaration_candidate WHERE id=:candidate"""),params)
    await mutate(text("""INSERT INTO declaration_candidate_evidence(organization_id,candidate_id,document_id,pdf_page,quote)
      VALUES(:org,:second,:document,1,'Synthetic')"""),params)
    async with synthetic_enforcement(seed),get_tenant_session(params['org']) as db:
        await actor_session(db,params['actor'])
        assert await db.scalar(text(COMMAND),command_params(params,bulk=True,ids=[params['candidate'],params['second']]))==2
        assert await db.scalar(text('SELECT count(*) FROM declaration_candidate WHERE import_id=:job AND candidate_version=2'),params)==2
        assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:job AND action='declaration_import.candidate_reviewed' AND payload_diff->>'revision'='2'"),params)==2
