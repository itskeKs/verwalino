"""Strict worker lifecycle mutations require a current database command/fence."""
import uuid
from datetime import datetime,timezone
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.db import get_tenant_session, get_worker_tenant_session
from app.repositories.declaration_import_repo import DeclarationImportRepository
from tests.test_derived_source_access import committed_scope,actor_session  # noqa: F401
from tests.test_financial_scope_boundary import scope_seed,worker_tenant  # noqa: F401
from tests.test_original_source_access import actors
from tests.test_declaration_job_principal import new_draft
from tests.test_scoped_account_statements import client
from tests.test_property_source_enforcement import synthetic_enforcement
from tests.test_declaration_import_api import isolated_document_store,enable_declaration_imports  # noqa: F401

pytestmark=pytest.mark.asyncio


@pytest.mark.parametrize('patch',[
    "lease_generation=lease_generation+1", "lease_owner='forged',lease_token=gen_random_uuid(),lease_expires_at=clock_timestamp()+interval '1 hour'",
    "status='PREFLIGHT'", "progress_percent=90", "input_tokens=100", "next_attempt_at=clock_timestamp()", "max_cost_usd=100", "draft_revision=draft_revision+1",
])
async def test_strict_raw_preparer_cannot_forge_worker_state(committed_scope,patch):
    seed=committed_scope; ids=actors(seed); job=uuid.UUID(await new_draft(ids))
    async with synthetic_enforcement(seed),get_tenant_session(ids['org']) as db:
        await actor_session(db,ids['actor'])
        with pytest.raises(DBAPIError,match='Worker lifecycle command required'):
            async with db.begin_nested():
                await db.execute(text('UPDATE declaration_import SET '+patch+' WHERE id=:job'),{'job':job})


async def strict_queue(seed):
    ids=actors(seed); job=uuid.UUID(await new_draft(ids))
    async with client(ids,'actor') as api:
        response=await api.post(f'/api/declaration-imports/{job}/start',headers={'Idempotency-Key':str(uuid.uuid4())})
        assert response.status_code==200,response.text
    return ids,job


async def test_strict_commands_claim_renew_transition_cancel_and_reject_stale_lease(committed_scope):
    seed=committed_scope
    async with synthetic_enforcement(seed):
        ids,job=await strict_queue(seed)
        async with get_worker_tenant_session(ids['org']) as db:
            lease=await DeclarationImportRepository(db,ids['org']).claim_next('strict-worker',datetime.now(timezone.utc),120)
            assert lease and lease.import_id==job
        async with get_worker_tenant_session(ids['org']) as db:
            repo=DeclarationImportRepository(db,ids['org'])
            assert await repo.heartbeat(lease,datetime.now(timezone.utc),120)
            assert await repo.transition_with_lease(lease,{'QUEUED'},'PREFLIGHT',{'current_stage':'preflight'})
        async with client(ids,'actor') as api:
            response=await api.post(f'/api/declaration-imports/{job}/cancel')
            assert response.status_code==200,response.text
        async with get_worker_tenant_session(ids['org']) as db:
            assert not await DeclarationImportRepository(db,ids['org']).transition_with_lease(lease,{'PREFLIGHT'},'EXTRACTING',{})


async def test_web_role_cannot_claim_work_or_impersonate_worker(committed_scope):
    seed=committed_scope
    async with synthetic_enforcement(seed):
        ids,job=await strict_queue(seed)
        async with get_tenant_session(ids['org']) as db:
            await actor_session(db,ids['actor'])
            for sql in ["SELECT * FROM claim_declaration_work(:org,'forged',120)","SET LOCAL ROLE app_worker"]:
                with pytest.raises(DBAPIError):
                    async with db.begin_nested(): await db.execute(text(sql),{'org':ids['org']})
            for signature in ['claim_declaration_work(uuid,text,integer)',
                'renew_declaration_work(uuid,uuid,uuid,bigint,uuid,integer)',
                'transition_declaration_work(uuid,uuid,uuid,bigint,uuid,text[],text,jsonb)']:
                assert not await db.scalar(text("SELECT has_function_privilege(current_user,:signature,'EXECUTE')"),{'signature':signature})


@pytest.mark.parametrize('target,patch',[
    ('NEEDS_REVIEW',{}), ('APPLIED',{}), ('PREFLIGHT',{'input_tokens':-1}),
    ('PREFLIGHT',{'estimated_cost_usd':'NaN'}), ('PREFLIGHT',{'lease_owner':'forged'}),
    ('PREFLIGHT',{'max_cost_usd':100}), ('PREFLIGHT',{'unknown':1}),
])
async def test_worker_command_rejects_invalid_transition_and_patch(committed_scope,target,patch):
    import json
    seed=committed_scope
    async with synthetic_enforcement(seed):
        ids,_=await strict_queue(seed)
        async with get_worker_tenant_session(ids['org']) as db:
            lease=await DeclarationImportRepository(db,ids['org']).claim_next('strict-worker',datetime.now(timezone.utc),120)
            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(text("SELECT transition_declaration_work(:org,:job,:request,:generation,:token,ARRAY['QUEUED'],:target,CAST(:patch AS jsonb))"),
                        {'org':ids['org'],'job':lease.import_id,'request':lease.queue_request_id,'generation':lease.lease_generation,
                         'token':lease.lease_token,'target':target,'patch':json.dumps(patch)})
            assert await db.scalar(text('SELECT status FROM declaration_import WHERE id=:job'),{'job':lease.import_id})=='QUEUED'


async def test_worker_connection_rejects_web_credentials(seed_data,monkeypatch):
    from app.core import db
    monkeypatch.setattr(db,'_worker_session_factory',db.get_session_factory())
    with pytest.raises(RuntimeError,match='isolated app_worker'):
        async with get_worker_tenant_session(seed_data['org_a_id']): pytest.fail('Web role accepted as worker')


async def test_real_strict_pipeline_reaches_review_with_separate_worker_role(committed_scope):
    from tests.test_declaration_pipeline import _seed_pipeline_import,_RecordingProvider,make_text_pdf
    from app.declarations.pipeline import DeclarationPipeline
    seed=committed_scope
    lease,key,store=await _seed_pipeline_import(seed,make_text_pdf('Synthetic declaration'))
    async with synthetic_enforcement(seed):
        await DeclarationPipeline(_RecordingProvider(key),store=store).run(lease)
        async with get_worker_tenant_session(lease.organization_id) as db:
            await actor_session(db,seed['user_a_admin_id'])
            item=(await db.execute(text('SELECT status,progress_percent,lease_token,draft_revision,input_tokens FROM declaration_import WHERE id=:job'),{'job':lease.import_id})).one()
            assert item.status=='NEEDS_REVIEW' and item.progress_percent==100 and item.lease_token is None
            assert item.draft_revision==2 and item.input_tokens>0


async def test_worker_command_migration_roundtrip_and_strict_refusal(committed_scope):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0070_declaration_worker_commands')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        async with synthetic_enforcement(committed_scope):
            with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError,match='Retain strict worker'):
                    with db.begin_nested(): migration.downgrade()
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            point=db.begin_nested()
            try:
                before=db.scalar(text("SELECT md5(COALESCE(string_agg(to_jsonb(i)::text,',' ORDER BY id),'')) FROM declaration_import i"))
                migration.downgrade();migration.upgrade()
                assert db.scalar(text("SELECT md5(COALESCE(string_agg(to_jsonb(i)::text,',' ORDER BY id),'')) FROM declaration_import i"))==before
                assert not db.scalar(text("SELECT has_function_privilege('app_rw','claim_declaration_work(uuid,text,integer)','EXECUTE')"))
                assert db.scalar(text("SELECT has_function_privilege('app_worker','claim_declaration_work(uuid,text,integer)','EXECUTE')"))
                assert not db.scalar(text('SELECT property_source_activation_ready(:org)'),{'org':committed_scope['org_a_id']})
            finally: point.rollback()
    finally: engine.dispose()


@pytest.mark.parametrize('table',['declaration_import','declaration_extraction_run','declaration_queue_request','property_source_security','property_document','membership'])
async def test_worker_command_migration_refuses_busy_dependency(seed_data,table):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0070_declaration_worker_commands');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as reader:
            reader.execute(text(f'SELECT 1 FROM {table} LIMIT 1'))
            with pytest.raises(DBAPIError) as denied:
                with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                    migration.upgrade()
            assert denied.value.orig.pgcode=='55P03'
    finally: engine.dispose()


async def test_worker_role_inherits_contact_privacy_guards(client_a_admin,seed_data):
    from tests.test_person_source_access import contact
    person=await contact(client_a_admin)
    assert (await client_a_admin.post(f'/api/dsgvo/persons/{person}/anonymize')).status_code==200
    async with get_worker_tenant_session(seed_data['org_a_id']) as db:
        await actor_session(db,seed_data['user_a_admin_id'])
        with pytest.raises(DBAPIError,match='Anonymized contact is immutable'):
            async with db.begin_nested():
                await db.execute(text("UPDATE person SET last_name='Repopulated' WHERE id=:person"),{'person':uuid.UUID(person)})
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text('UPDATE person SET anonymized_at=NULL WHERE id=:person'),{'person':uuid.UUID(person)})


@pytest.mark.parametrize('unsafe_role_change',[
    'GRANT app_migrator TO app_worker',
    'CREATE ROLE test_worker_delegate NOLOGIN; GRANT app_worker TO test_worker_delegate',
    'ALTER ROLE app_worker NOINHERIT',
    'ALTER ROLE app_worker REPLICATION',
])
async def test_migration_rejects_unsafe_worker_role_membership(seed_data,unsafe_role_change):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0070_declaration_worker_commands');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            point=db.begin_nested()
            try:
                db.execute(text(unsafe_role_change))
                with pytest.raises(DBAPIError,match='Provision isolated app_worker'):
                    with db.begin_nested(): migration.upgrade()
            finally: point.rollback()
    finally: engine.dispose()


async def test_review_revision_compatibility_is_explicitly_not_worker_proof(committed_scope):
    from tests.test_scoped_account_statements import mutate
    seed=committed_scope;ids=actors(seed);job=uuid.UUID(await new_draft(ids))
    await mutate(text("UPDATE declaration_import SET status='NEEDS_REVIEW' WHERE id=:job"),{'job':job})
    async with synthetic_enforcement(seed),get_tenant_session(ids['org']) as db:
        await actor_session(db,ids['actor'])
        # Preexisting review-service compatibility remains an explicit B1 gap.
        assert (await db.execute(text('UPDATE declaration_import SET draft_revision=draft_revision+1 WHERE id=:job'),{'job':job})).rowcount==1


@pytest.mark.parametrize('change,repair',[
    ('REVOKE app_rw FROM app_worker','GRANT app_rw TO app_worker'),
    ('GRANT app_rw TO app_worker WITH INHERIT FALSE','GRANT app_rw TO app_worker WITH INHERIT TRUE'),
    ('ALTER ROLE app_worker REPLICATION','ALTER ROLE app_worker NOREPLICATION'),
])
async def test_worker_sessions_reject_role_drift(seed_data,change,repair):
    from tests.test_scoped_account_statements import mutate
    try:
        await mutate(text(change),{})
        with pytest.raises(RuntimeError,match='isolated app_worker'):
            async with get_worker_tenant_session(seed_data['org_a_id']): pytest.fail('Unsafe role drift accepted')
    finally: await mutate(text(repair),{})
