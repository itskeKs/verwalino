"""Worker outputs require a private transaction receipt for the live lease."""
import uuid
from datetime import datetime,timezone
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.db import get_tenant_session,get_worker_tenant_session
from app.repositories.declaration_import_repo import DeclarationImportRepository
from tests.test_derived_source_access import committed_scope,actor_session  # noqa: F401
from tests.test_financial_scope_boundary import scope_seed,worker_tenant  # noqa: F401
from tests.test_declaration_import_api import isolated_document_store,enable_declaration_imports  # noqa: F401
from tests.test_declaration_pipeline import _seed_pipeline_import,make_text_pdf
from tests.test_property_source_enforcement import synthetic_enforcement
from tests.test_scoped_account_statements import mutate

pytestmark=pytest.mark.asyncio

RUN_SQL="""INSERT INTO declaration_extraction_run(organization_id,import_id,provider,model_name,prompt_version,schema_version,harness_version)
    VALUES(:org,:job,'test','test','test','test','test')"""
CANDIDATE_SQL="""INSERT INTO declaration_candidate(organization_id,import_id,stable_key,candidate_type,subject_key,field_key,operation,
    proposed_value_json,confidence,risk_class) VALUES(:org,:job,'worker-proof','PROPERTY_FIELD','property:main','name','CREATE',
    '{"kind":"TEXT","value":"Synthetic"}',.99,'LOW')"""


@pytest.mark.parametrize('effect',[RUN_SQL,CANDIDATE_SQL,'UPDATE property_document SET page_count=2 WHERE id=:document'])
@pytest.mark.parametrize('runtime',['web','worker'])
async def test_source_preparer_cannot_write_worker_outputs_without_receipt(committed_scope,effect,runtime):
    seed=committed_scope;lease,key,_=await _seed_pipeline_import(seed,make_text_pdf('Synthetic declaration'))
    await mutate(text("UPDATE declaration_import SET status='RECONCILING' WHERE id=:job"),{'job':lease.import_id})
    factory=get_tenant_session if runtime=='web' else get_worker_tenant_session
    async with synthetic_enforcement(seed),factory(lease.organization_id) as db:
        await actor_session(db,seed['user_a_admin_id'])
        # Even correctly guessed runtime GUCs are not a database-owned receipt.
        await db.execute(text("SELECT set_config('app.worker_lease_token',:token,true)"),{'token':str(lease.lease_token)})
        with pytest.raises(DBAPIError,match='Current worker effect receipt required'):
            async with db.begin_nested():
                await db.execute(text(effect),{'org':lease.organization_id,'job':lease.import_id,'document':uuid.UUID(key)})


async def effect_seed(seed):
    lease,key,_=await _seed_pipeline_import(seed,make_text_pdf('Synthetic declaration'))
    await mutate(text("UPDATE declaration_import SET status='RECONCILING' WHERE id=:job"),{'job':lease.import_id})
    return lease,{'org':lease.organization_id,'job':lease.import_id,'document':uuid.UUID(key)}


@pytest.mark.parametrize('runtime',['web','worker'])
async def test_receipt_is_private_and_lease_lock_only_authorizes_actual_worker(committed_scope,runtime):
    seed=committed_scope;lease,params=await effect_seed(seed)
    factory=get_tenant_session if runtime=='web' else get_worker_tenant_session
    async with synthetic_enforcement(seed),factory(lease.organization_id) as db:
        await actor_session(db,seed['user_a_admin_id'])
        assert await DeclarationImportRepository(db,lease.organization_id).lock_current_lease(lease,{'RECONCILING'})
        for privilege in ['SELECT','INSERT','UPDATE','DELETE','TRUNCATE']:
            assert not await db.scalar(text("SELECT has_table_privilege(current_user,'declaration_worker_context',:privilege)"),{'privilege':privilege})
        assert not await db.scalar(text("SELECT has_function_privilege(current_user,'lock_declaration_lease_before_effects(uuid,uuid,uuid,bigint,uuid,text[])','EXECUTE')"))
        if runtime=='worker':
            await db.execute(text(RUN_SQL),params)
            await db.execute(text(CANDIDATE_SQL),params)
            await db.execute(text('UPDATE property_document SET page_count=2 WHERE id=:document'),params)
        else:
            with pytest.raises(DBAPIError,match='Current worker effect receipt required'):
                async with db.begin_nested(): await db.execute(text(RUN_SQL),params)


async def test_receipt_cannot_be_reused_after_commit_on_same_backend(committed_scope):
    from app.core.db import get_worker_session_factory
    from sqlalchemy.ext.asyncio import AsyncSession
    seed=committed_scope;lease,params=await effect_seed(seed)
    engine=get_worker_session_factory().kw['bind']
    async with synthetic_enforcement(seed),engine.connect() as connection:
        async with AsyncSession(bind=connection) as db:
            async with db.begin():
                await db.execute(text("SELECT set_config('app.current_org_id',:org,true)"),{'org':str(lease.organization_id)})
                assert await DeclarationImportRepository(db,lease.organization_id).lock_current_lease(lease,{'RECONCILING'})
                await db.execute(text(RUN_SQL),params)
                pid=await db.scalar(text('SELECT pg_backend_pid()'))
            async with db.begin():
                assert await db.scalar(text('SELECT pg_backend_pid()'))==pid
                await db.execute(text("SELECT set_config('app.current_org_id',:org,true)"),{'org':str(lease.organization_id)})
                await actor_session(db,seed['user_a_admin_id'])
                with pytest.raises(DBAPIError,match='Current worker effect receipt required'):
                    async with db.begin_nested(): await db.execute(text(CANDIDATE_SQL),params)


@pytest.mark.parametrize('field',['lease_token','lease_generation','queue_request_id'])
async def test_wrong_lease_cannot_mint_receipt(committed_scope,field):
    from dataclasses import replace
    seed=committed_scope;lease,params=await effect_seed(seed)
    stale=replace(lease,**{field:lease.lease_generation+1 if field=='lease_generation' else uuid.uuid4()})
    async with synthetic_enforcement(seed),get_worker_tenant_session(lease.organization_id) as db:
        await actor_session(db,seed['user_a_admin_id'])
        assert await DeclarationImportRepository(db,lease.organization_id).lock_current_lease(stale,{'RECONCILING'}) is None
        with pytest.raises(DBAPIError,match='Current worker effect receipt required'):
            async with db.begin_nested(): await db.execute(text(RUN_SQL),params)


async def test_receipt_rechecks_queue_actor_and_expiry_before_effect(committed_scope):
    import asyncio
    seed=committed_scope;lease,params=await effect_seed(seed)
    await mutate(text("UPDATE declaration_import SET lease_expires_at=clock_timestamp()+interval '1 second' WHERE id=:job"),params)
    async with synthetic_enforcement(seed),get_worker_tenant_session(lease.organization_id) as db:
        assert await DeclarationImportRepository(db,lease.organization_id).lock_current_lease(lease,{'RECONCILING'})
        await db.execute(text("SELECT set_config('app.current_user_id','',true)"))
        assert not await db.scalar(text("SELECT declaration_worker_effect_access(:org,:job,ARRAY['RECONCILING'])"),params)
        await actor_session(db,seed['user_a_admin_id'])
        assert await db.scalar(text("SELECT declaration_worker_effect_access(:org,:job,ARRAY['RECONCILING'])"),params)
        await asyncio.sleep(1.1)
        with pytest.raises(DBAPIError,match='Current worker effect receipt required'):
            async with db.begin_nested(): await db.execute(text(RUN_SQL),params)


async def test_revoked_queue_actor_cannot_mint_worker_receipt(committed_scope):
    seed=committed_scope;lease,params=await effect_seed(seed)
    async with synthetic_enforcement(seed):
        await mutate(text('UPDATE "user" SET is_active=false WHERE id=:actor'),{'actor':seed['user_a_admin_id']})
        try:
            async with get_worker_tenant_session(lease.organization_id) as db:
                from app.repositories.declaration_import_repo import DeclarationPrincipalDenied
                with pytest.raises(DeclarationPrincipalDenied):
                    await DeclarationImportRepository(db,lease.organization_id).lock_current_lease(lease,{'RECONCILING'})
                assert not await db.scalar(text("SELECT declaration_worker_effect_access(:org,:job,ARRAY['RECONCILING'])"),params)
        finally:
            await mutate(text('UPDATE "user" SET is_active=true WHERE id=:actor'),{'actor':seed['user_a_admin_id']})


@pytest.mark.parametrize('table,patch',[
    ('declaration_candidate',"review_status='ACCEPTED',reviewed_at=clock_timestamp(),reviewed_by=:actor"),
    ('declaration_candidate_issue',"resolution_status='RESOLVED',resolved_at=clock_timestamp(),resolved_by=:actor"),
])
async def test_worker_receipt_cannot_forge_human_review(committed_scope,table,patch):
    seed=committed_scope;lease,params=await effect_seed(seed);params['actor']=seed['user_a_admin_id']
    async with synthetic_enforcement(seed),get_worker_tenant_session(lease.organization_id) as db:
        assert await DeclarationImportRepository(db,lease.organization_id).lock_current_lease(lease,{'RECONCILING'})
        await db.execute(text(CANDIDATE_SQL),params)
        await db.execute(text("INSERT INTO declaration_candidate_issue(organization_id,import_id,code,severity,message) VALUES(:org,:job,'TEST','WARNING','Synthetic')"),params)
        with pytest.raises(DBAPIError,match='Current worker effect receipt required'):
            async with db.begin_nested(): await db.execute(text(f'UPDATE {table} SET {patch} WHERE import_id=:job'),params)


async def test_worker_effect_migration_roundtrip_and_strict_refusal(committed_scope):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0071_declaration_worker_effects')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        async with synthetic_enforcement(committed_scope):
            with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError,match='Retain strict worker effect fencing'):
                    with db.begin_nested(): migration.downgrade()
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            point=db.begin_nested()
            try:
                migration.downgrade();migration.upgrade()
                assert not db.scalar(text("SELECT has_table_privilege('app_worker','declaration_worker_context','INSERT')"))
                assert not db.scalar(text('SELECT property_source_activation_ready(:org)'),{'org':committed_scope['org_a_id']})
            finally: point.rollback()
    finally: engine.dispose()


@pytest.mark.parametrize('table',['declaration_candidate','declaration_import_pass','declaration_candidate_evidence','declaration_candidate_issue','declaration_worker_context'])
async def test_worker_effect_migration_refuses_busy_dependency(seed_data,table):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0071_declaration_worker_effects');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as reader:
            reader.execute(text(f'SELECT 1 FROM {table} LIMIT 1'))
            with pytest.raises(DBAPIError) as denied:
                with engine.begin() as db,Operations.context(MigrationContext.configure(db)): migration.downgrade()
            assert denied.value.orig.pgcode=='55P03'
    finally: engine.dispose()


@pytest.mark.parametrize('runtime',['web','worker'])
async def test_pass_and_evidence_effects_need_receipt_including_legacy_parent_fill(committed_scope,runtime):
    import json
    from tests.test_declaration_pipeline import _safe_attempt_metadata
    seed=committed_scope;lease,params=await effect_seed(seed)
    pass_sql="""INSERT INTO declaration_import_pass(organization_id,declaration_extraction_run_id,attempt_no,safe_metadata)
        VALUES(:org,:run,:attempt,CAST(:metadata AS jsonb))"""
    evidence_sql="""INSERT INTO declaration_candidate_evidence(organization_id,candidate_id,document_id,pdf_page,quote)
        VALUES(:org,:candidate,:document,1,'Synthetic')"""
    async with synthetic_enforcement(seed):
        async with get_worker_tenant_session(lease.organization_id) as db:
            assert await DeclarationImportRepository(db,lease.organization_id).lock_current_lease(lease,{'RECONCILING'})
            params['run']=await db.scalar(text(RUN_SQL+' RETURNING id'),params)
            params['candidate']=await db.scalar(text(CANDIDATE_SQL+' RETURNING id'),params)
            params.update(attempt=1,metadata=json.dumps(_safe_attempt_metadata().as_safe_dict()))
            await db.execute(text(pass_sql),params)
            await db.execute(text(evidence_sql),params)
        factory=get_tenant_session if runtime=='web' else get_worker_tenant_session
        async with factory(lease.organization_id) as db:
            await actor_session(db,seed['user_a_admin_id'])
            params.update(attempt=2,metadata=json.dumps(_safe_attempt_metadata(attempt_no=2).as_safe_dict()))
            for sql in [pass_sql,evidence_sql,
                "UPDATE declaration_candidate_evidence SET quote='Forged' WHERE candidate_id=:candidate",
                'DELETE FROM declaration_candidate_evidence WHERE candidate_id=:candidate']:
                with pytest.raises(DBAPIError,match='Current worker effect receipt required'):
                    async with db.begin_nested(): await db.execute(text(sql),params)
            # The pass ledger also independently rejects mutation (append-only).
            for sql in ['UPDATE declaration_import_pass SET safe_metadata=safe_metadata WHERE declaration_extraction_run_id=:run',
                        'DELETE FROM declaration_import_pass WHERE declaration_extraction_run_id=:run']:
                with pytest.raises(DBAPIError):
                    async with db.begin_nested(): await db.execute(text(sql),params)


async def test_worker_receipt_serializes_cancellation_and_cannot_survive_it(committed_scope):
    import asyncio
    seed=committed_scope;lease,params=await effect_seed(seed)
    started=asyncio.Event()
    async def cancel():
        async with get_tenant_session(lease.organization_id) as db:
            await actor_session(db,seed['user_a_admin_id'])
            started.set()
            return await db.scalar(text('SELECT cancel_declaration_work(:org,:job)'),params)
    async with synthetic_enforcement(seed):
        task=None
        try:
            async with get_worker_tenant_session(lease.organization_id) as db:
                assert await DeclarationImportRepository(db,lease.organization_id).lock_current_lease(lease,{'RECONCILING'})
                await db.execute(text(RUN_SQL),params)
                task=asyncio.create_task(cancel())
                await asyncio.wait_for(started.wait(),5)
                await asyncio.sleep(.1)
                assert not task.done()
                await db.execute(text(CANDIDATE_SQL),params)
            assert await asyncio.wait_for(task,5)
            async with get_worker_tenant_session(lease.organization_id) as db:
                await actor_session(db,seed['user_a_admin_id'])
                assert await db.scalar(text('SELECT status FROM declaration_import WHERE id=:job'),params)=='CANCELLED'
                assert await db.scalar(text("SELECT count(*) FROM declaration_extraction_run WHERE import_id=:job AND status='RUNNING'"),params)==0
                assert await DeclarationImportRepository(db,lease.organization_id).lock_current_lease(lease,{'RECONCILING'}) is None
                with pytest.raises(DBAPIError,match='Current worker effect receipt required'):
                    async with db.begin_nested(): await db.execute(text(RUN_SQL),params)
        finally:
            if task and not task.done(): task.cancel()
            if task: await asyncio.gather(task,return_exceptions=True)
