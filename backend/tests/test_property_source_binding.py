"""Strict original binding requires durable audience proof and current approval."""
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.db import get_tenant_session
from tests.test_property_source_admission import setup
from tests.test_property_source_admission_review import legacy_source,pending_body
from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
from tests.test_declaration_import_api import isolated_document_store,enable_declaration_imports  # noqa: F401

pytestmark=pytest.mark.asyncio


async def approve_grant(api,seed,ids):
    if ids.get('uploader_grant'):
        assert (await api.post(f"/api/property-data-domains/{ids['domain']}/grants/{ids['uploader_grant']}/revoke")).status_code==204
    response=await api.post(f"/api/property-data-domains/{ids['domain']}/grants",headers={'Idempotency-Key':str(uuid.uuid4())},
        json={'user_id':str(seed['user_a_admin_id']),'capabilities':['read','prepare','approve'],'reason':'Authorize original publication'})
    assert response.status_code==201,response.text
    return response.json()['id']


async def applied(seed,ids):
    async with get_tenant_session(seed['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed['user_a_admin_id'])})
        await db.execute(text("UPDATE declaration_import SET status='APPLIED',applied_property_id=:property,approved_by=:actor,approved_at=clock_timestamp(), "
            "approval_idempotency_key='binding-test',approval_result_json=jsonb_build_object('property_id',CAST(:property AS uuid)) WHERE id=:job"),
            {**ids,'actor':seed['user_a_admin_id']})


async def bind(seed,ids,actor=None):
    async with get_tenant_session(seed['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(actor or seed['user_a_admin_id'])})
        await db.execute(text('UPDATE property_document SET property_id=:property WHERE id=:document'),ids)


@pytest.mark.parametrize('problem',['no_proof','no_approve','paused','not_applied'])
async def test_strict_binding_rejects_missing_proof_right_or_active_state(client_a_admin,seed_data,problem):
    ids=await setup(client_a_admin,seed_data,authorized=problem!='no_proof')
    if problem!='no_approve': await approve_grant(client_a_admin,seed_data,ids)
    if problem!='not_applied': await applied(seed_data,ids)
    async with synthetic_enforcement(seed_data):
        if problem=='paused': await change_mode(seed_data,'PAUSED')
        with pytest.raises(DBAPIError,match='Original binding requires'):
            await bind(seed_data,ids)
        async with get_tenant_session(seed_data['org_a_id']) as db:
            assert await db.scalar(text('SELECT property_id FROM property_document WHERE id=:document'),ids) is None


@pytest.mark.parametrize('proof',['attachment','review'])
async def test_strict_binding_accepts_exact_proof_and_audits_once(client_a_admin,seed_data,proof):
    if proof=='attachment':
        ids=await setup(client_a_admin,seed_data)
        await approve_grant(client_a_admin,seed_data,ids)
    else:
        ids=await legacy_source(client_a_admin,seed_data)
        response=await client_a_admin.post('/api/property-source-admission-reviews',headers={'Idempotency-Key':str(uuid.uuid4())},json=await pending_body(client_a_admin,ids))
        assert response.status_code==201,response.text
    await applied(seed_data,ids)
    async with synthetic_enforcement(seed_data):
        await bind(seed_data,ids)
        await bind(seed_data,ids)
        async with get_tenant_session(seed_data['org_a_id']) as db:
            assert await db.scalar(text('SELECT property_id FROM property_document WHERE id=:document'),ids)==ids['property']
            audit=(await db.execute(text("SELECT actor_user_id,payload_diff FROM audit_event WHERE entity_id=:document AND action='property_document.binding_approved'"),ids)).one()
            assert audit.actor_user_id==seed_data['user_a_admin_id']
            assert audit.payload_diff['property_id']==str(ids['property'])
            assert audit.payload_diff['import_id']==str(ids['job'])


async def test_strict_document_insert_cannot_skip_unbound_admission(client_a_admin,seed_data):
    ids=await setup(client_a_admin,seed_data)
    await approve_grant(client_a_admin,seed_data,ids)
    async with synthetic_enforcement(seed_data):
        with pytest.raises(DBAPIError,match='Original binding requires'):
            async with get_tenant_session(seed_data['org_a_id']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
                await db.execute(text("INSERT INTO property_document(organization_id,property_id,doc_type,title,original_filename,mime_type,content_byte_size,content_sha256,storage_uri,storage_backend,page_count,uploaded_by) "
                    "SELECT organization_id,:property,doc_type,title,original_filename,mime_type,content_byte_size,content_sha256,storage_uri,storage_backend,page_count,uploaded_by FROM property_document WHERE id=:document"),ids)


async def test_strict_new_property_approval_binds_all_originals_atomically(client_a_admin,seed_data,fully_reviewed_new_property_import):
    from tests.test_declaration_publication_audience import command
    item=fully_reviewed_new_property_import.item
    async with synthetic_enforcement(seed_data):
        response=await client_a_admin.post(f'/api/declaration-imports/{item.id}/approve',json=command(seed_data,item))
        assert response.status_code==200,response.text
        async with get_tenant_session(seed_data['org_a_id']) as db:
            docs=(await db.execute(text('SELECT d.id,d.property_id FROM property_document d JOIN declaration_import_document l ON l.document_id=d.id AND l.organization_id=d.organization_id WHERE l.import_id=:job'),{'job':item.id})).all()
            assert docs and all(str(row.property_id)==response.json()['property_id'] for row in docs)
            assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE action='property_document.binding_approved' AND payload_diff->>'import_id'=:job"),{'job':str(item.id)})==len(docs)


@pytest.mark.parametrize('bound',[False,True])
async def test_strict_original_cannot_gain_second_import_link(client_a_admin,seed_data,bound):
    ids=await setup(client_a_admin,seed_data)
    await approve_grant(client_a_admin,seed_data,ids)
    await applied(seed_data,ids)
    other=await client_a_admin.post('/api/declaration-imports',json={'mode':'EXISTING_PROPERTY','target_property_id':str(ids['property'])})
    assert other.status_code==201
    async with synthetic_enforcement(seed_data):
        if bound: await bind(seed_data,ids)
        with pytest.raises(DBAPIError,match='single unbound import link'):
            async with get_tenant_session(seed_data['org_a_id']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
                await db.execute(text("INSERT INTO declaration_import_document(organization_id,import_id,document_id,sequence_no,document_role) VALUES(:org,:job,:document,1,'DECLARATION')"),
                    {'org':seed_data['org_a_id'],'job':uuid.UUID(other.json()['id']),'document':ids['document']})


async def test_binding_migration_refuses_strict_rollback_and_roundtrips_preparation(seed_data):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0055_property_source_binding')
    async with synthetic_enforcement(seed_data):
        engine=create_engine(MIGRATOR_SYNC_URL)
        try:
            with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError,match='Retain enforced original binding'):
                    with db.begin_nested(): migration.downgrade()
                nested=db.begin_nested()
                try:
                    db.execute(text('TRUNCATE property_source_security,property_source_classification,property_creation_receipt'))
                    importlib.import_module('migrations.versions.0061_property_master_access').downgrade()
                    importlib.import_module('migrations.versions.0059_financial_source_input').downgrade()
                    importlib.import_module('migrations.versions.0056_property_source_classification').downgrade()
                    migration.downgrade()
                    assert db.scalar(text("SELECT to_regprocedure('guard_strict_property_source_binding()')")) is None
                    migration.upgrade()
                    assert 'row_security=off' in db.scalar(text("SELECT proconfig FROM pg_proc WHERE oid='guard_strict_property_source_binding()'::regprocedure"))
                finally: nested.rollback()
        finally: engine.dispose()


@pytest.mark.parametrize('table',['property_document','declaration_import_document'])
async def test_busy_binding_dependency_refuses_nowait(seed_data,table):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0055_property_source_binding')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        async with get_tenant_session(seed_data['org_a_id']) as reader:
            await reader.execute(text(f'SELECT id FROM {table} LIMIT 1'))
            with pytest.raises(DBAPIError) as caught:
                with engine.begin() as db:
                    db.execute(text("SET LOCAL lock_timeout='1s'"))
                    with Operations.context(MigrationContext.configure(db)): migration.upgrade()
            assert caught.value.orig.pgcode=='55P03' and 'could not obtain lock' in str(caught.value.orig)
    finally: engine.dispose()


async def test_parallel_bindings_produce_one_canonical_audit(client_a_admin,seed_data):
    import asyncio
    ids=await setup(client_a_admin,seed_data)
    await approve_grant(client_a_admin,seed_data,ids)
    await applied(seed_data,ids)
    async with synthetic_enforcement(seed_data):
        await asyncio.wait_for(asyncio.gather(bind(seed_data,ids),bind(seed_data,ids)),5)
        async with get_tenant_session(seed_data['org_a_id']) as db:
            assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:document AND action='property_document.binding_approved'"),ids)==1


async def test_incorrect_binding_definer_owner_cannot_hide_strict_state(client_a_admin,seed_data):
    from sqlalchemy import create_engine
    from tests.conftest import MIGRATOR_SYNC_URL
    ids=await setup(client_a_admin,seed_data)
    async with synthetic_enforcement(seed_data):
        engine=create_engine(MIGRATOR_SYNC_URL)
        try:
            with engine.begin() as db:
                nested=db.begin_nested()
                try:
                    db.execute(text('GRANT SELECT ON property_source_security TO app_rw'))
                    db.execute(text('ALTER FUNCTION guard_strict_property_source_binding() OWNER TO app_rw'))
                    db.execute(text('SET LOCAL ROLE app_rw'))
                    db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
                        {'org':str(seed_data['org_a_id']),'actor':str(seed_data['user_a_clerk_id'])})
                    with pytest.raises(DBAPIError,match='row-level security'):
                        with db.begin_nested(): db.execute(text('UPDATE property_document SET property_id=:property WHERE id=:document'),ids)
                finally: nested.rollback()
        finally: engine.dispose()


async def test_competing_binding_and_extra_link_preserve_single_source_topology(client_a_admin,seed_data):
    import asyncio
    ids=await setup(client_a_admin,seed_data)
    await approve_grant(client_a_admin,seed_data,ids)
    await applied(seed_data,ids)
    other=await client_a_admin.post('/api/declaration-imports',json={'mode':'EXISTING_PROPERTY','target_property_id':str(ids['property'])})
    assert other.status_code==201
    async with synthetic_enforcement(seed_data):
        async def extra_link():
            with pytest.raises(DBAPIError,match='single unbound import link'):
                async with get_tenant_session(seed_data['org_a_id']) as db:
                    await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
                    await db.execute(text("INSERT INTO declaration_import_document(organization_id,import_id,document_id,sequence_no,document_role) VALUES(:org,:job,:document,1,'ANNEX')"),
                        {'org':seed_data['org_a_id'],'job':uuid.UUID(other.json()['id']),'document':ids['document']})
        await asyncio.wait_for(asyncio.gather(bind(seed_data,ids),extra_link()),5)
        async with get_tenant_session(seed_data['org_a_id']) as db:
            assert await db.scalar(text('SELECT count(*) FROM declaration_import_document WHERE document_id=:document'),ids)==1
            assert await db.scalar(text('SELECT property_id FROM property_document WHERE id=:document'),ids)==ids['property']
