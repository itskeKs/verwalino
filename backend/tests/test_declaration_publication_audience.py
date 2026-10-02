"""Explicit new-property audience and publication authorization evidence."""
import uuid
import pytest
from sqlalchemy import text
from app.core.db import get_tenant_session
from tests.test_declaration_approval_new_property import fully_reviewed_new_property_import  # noqa: F401
from tests.test_property_source_access import access
from tests.test_declaration_import_api import enable_declaration_imports  # noqa: F401

pytestmark=pytest.mark.asyncio


def command(seed,item):
    return {'draft_revision':item.draft_revision,'idempotency_key':str(uuid.uuid4()),
        'new_property_audience':{'label':'Reviewed property sources','reason':'Explicit new property publication',
            'grants':[
                {'user_id':str(seed['user_a_admin_id']),'capabilities':['read','prepare','approve','export'],'reason':'Publish and manage reviewed sources'},
                {'user_id':str(seed['user_a_clerk_id']),'capabilities':['read'],'reason':'Read the approved property sources'},
            ]}}


async def test_explicit_audience_is_atomic_traceable_and_replay_safe(client_a_admin,seed_data,fully_reviewed_new_property_import):
    item=fully_reviewed_new_property_import.item
    body=command(seed_data,item)
    response=await client_a_admin.post(f'/api/declaration-imports/{item.id}/approve',json=body)
    assert response.status_code==200,response.text
    result=response.json()
    prop=uuid.UUID(result['property_id'])
    assert await access(seed_data,'property',prop,actor=seed_data['user_a_clerk_id'])
    assert not await access(seed_data,'property',prop,cap='prepare',actor=seed_data['user_a_clerk_id'])
    replay=await client_a_admin.post(f'/api/declaration-imports/{item.id}/approve',json=body)
    assert replay.status_code==200 and replay.json()==result
    changed={**body,'new_property_audience':{**body['new_property_audience'],'reason':'Changed instruction'}}
    assert (await client_a_admin.post(f'/api/declaration-imports/{item.id}/approve',json=changed)).status_code==409
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        row=(await db.execute(text('SELECT publication_domain_id,publication_grant_id,approval_request_digest FROM declaration_import WHERE id=:id'),{'id':item.id})).one()
        assert row.publication_domain_id and row.publication_grant_id and len(row.approval_request_digest)==64
        assert await db.scalar(text('SELECT count(*) FROM property_data_grant WHERE domain_id=:id'),{'id':row.publication_domain_id})==2
        assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:id AND action='declaration_import.audience_published'"),{'id':item.id})==1
    response=await client_a_admin.post(f'/api/property-data-domains/{row.publication_domain_id}/grants/{row.publication_grant_id}/revoke')
    assert response.status_code==204
    assert (await client_a_admin.post(f'/api/declaration-imports/{item.id}/approve',json=body)).status_code==404
    assert await access(seed_data,'property_document',fully_reviewed_new_property_import.main.id,actor=seed_data['user_a_clerk_id'])


@pytest.mark.parametrize('denial',['clerk','foreign','missing_publisher','foreign_grantee','duplicate_member'])
async def test_publication_audience_denials_leave_the_draft_unchanged(client_a_admin,client_a_clerk,client_b_admin,seed_data,fully_reviewed_new_property_import,denial):
    from app.declarations.approval import deterministic_id
    item=fully_reviewed_new_property_import.item
    body=command(seed_data,item)
    api=client_a_admin
    expected=422
    if denial=='clerk': api,expected=client_a_clerk,403
    if denial=='foreign': api,expected=client_b_admin,404
    if denial=='missing_publisher': body['new_property_audience']['grants']=body['new_property_audience']['grants'][1:]
    if denial=='foreign_grantee':
        body['new_property_audience']['grants'][1]['user_id']=str(seed_data['user_b_admin_id'])
        expected=404
    if denial=='duplicate_member': body['new_property_audience']['grants'].append(body['new_property_audience']['grants'][0])
    response=await api.post(f'/api/declaration-imports/{item.id}/approve',json=body)
    assert response.status_code==expected,response.text
    async with get_tenant_session(seed_data['org_a_id']) as db:
        assert await db.scalar(text('SELECT status FROM declaration_import WHERE id=:id'),{'id':item.id})=='NEEDS_REVIEW'
        assert await db.scalar(text('SELECT count(*) FROM property WHERE id=:id'),
            {'id':deterministic_id(seed_data['org_a_id'],item.id,'property','new')})==0
        assert await db.scalar(text('SELECT count(*) FROM property_data_domain WHERE request_key=:key'),{'key':uuid.uuid5(item.id,'publication-domain')})==0


async def test_guarded_publication_resolves_unbound_sources_and_rolls_back_on_late_failure(client_a_admin,seed_data,fully_reviewed_new_property_import,monkeypatch):
    from app.repositories.property_knowledge_repo import PropertyKnowledgeRepository
    from app.declarations.approval import deterministic_id
    item=fully_reviewed_new_property_import.item
    original=PropertyKnowledgeRepository.apply
    async def fail_after_apply(repo,plan,approved_by,approved_at,documents):
        assert all(document.property_id is None for document in documents)
        for document in documents:
            assert await repo._session.scalar(text("SELECT property_source_access(:org,'property_document',:id,'approve')"),
                {'org':seed_data['org_a_id'],'id':document.id})
        await original(repo,plan,approved_by,approved_at,documents)
        raise RuntimeError('forced-publication-rollback')
    monkeypatch.setattr(PropertyKnowledgeRepository,'apply',fail_after_apply)
    with pytest.raises(RuntimeError,match='forced-publication-rollback'):
        await client_a_admin.post(f'/api/declaration-imports/{item.id}/approve',json=command(seed_data,item))
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        row=(await db.execute(text('SELECT status,publication_domain_id,approval_request_digest FROM declaration_import WHERE id=:id'),{'id':item.id})).one()
        assert row.status=='NEEDS_REVIEW' and row.publication_domain_id is None and row.approval_request_digest is None
        prop=deterministic_id(seed_data['org_a_id'],item.id,'property','new')
        assert await db.scalar(text('SELECT count(*) FROM property WHERE id=:id'),{'id':prop})==0
        assert await db.scalar(text('SELECT count(*) FROM property_data_domain WHERE property_id=:id'),{'id':prop})==0
        assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:id AND action='declaration_import.audience_published'"),{'id':item.id})==0
        assert await db.scalar(text('SELECT property_id FROM property_document WHERE id=:id'),{'id':fully_reviewed_new_property_import.main.id}) is None


async def test_published_proof_and_source_set_are_immutable(client_a_admin,seed_data,fully_reviewed_new_property_import):
    from sqlalchemy.exc import DBAPIError
    item=fully_reviewed_new_property_import.item
    response=await client_a_admin.post(f'/api/declaration-imports/{item.id}/approve',json=command(seed_data,item))
    assert response.status_code==200,response.text
    other=await client_a_admin.post('/api/declaration-imports',json={'mode':'NEW_PROPERTY'})
    assert other.status_code==201
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        for statement in ["UPDATE declaration_import SET publication_grant_id=NULL WHERE id=:id",
                          "UPDATE declaration_import SET approval_request_digest=repeat('0',64) WHERE id=:id",
                          'DELETE FROM declaration_import_document WHERE import_id=:id',
                          'UPDATE declaration_import_document SET sequence_no=99 WHERE import_id=:id',
                          'INSERT INTO declaration_import_document(organization_id,import_id,document_id,sequence_no,document_role) '
                          'VALUES(:org,:other,:document,1,\'DECLARATION\')']:
            with pytest.raises(DBAPIError,match='immutable'):
                async with db.begin_nested():
                    await db.execute(text(statement),{'id':item.id,'org':seed_data['org_a_id'],
                        'other':uuid.UUID(other.json()['id']),'document':fully_reviewed_new_property_import.main.id})


async def test_legacy_applied_receipt_cannot_acquire_audience_proof(client_a_admin,seed_data,fully_reviewed_new_property_import):
    from sqlalchemy.exc import DBAPIError
    item=fully_reviewed_new_property_import.item
    body=command(seed_data,item)
    body.pop('new_property_audience')
    assert (await client_a_admin.post(f'/api/declaration-imports/{item.id}/approve',json=body)).status_code==200
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        with pytest.raises(DBAPIError,match='immutable'):
            async with db.begin_nested():
                await db.execute(text('UPDATE declaration_import SET publication_domain_id=:domain,publication_grant_id=:grant WHERE id=:id'),
                    {'id':item.id,'domain':uuid.uuid4(),'grant':uuid.uuid4()})


async def test_concurrent_audience_approval_serializes_before_shared_locks(client_a_admin,seed_data,fully_reviewed_new_property_import):
    import asyncio
    item=fully_reviewed_new_property_import.item
    body=command(seed_data,item)
    responses=await asyncio.wait_for(asyncio.gather(*[
        client_a_admin.post(f'/api/declaration-imports/{item.id}/approve',json=body) for _ in range(2)
    ]),5)
    assert [response.status_code for response in responses]==[200,200]
    assert responses[0].json()==responses[1].json()
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        assert await db.scalar(text('SELECT count(*) FROM property_data_grant WHERE domain_id=(SELECT publication_domain_id FROM declaration_import WHERE id=:id)'),{'id':item.id})==2


async def test_database_proof_guard_requires_creator_and_exact_current_approve_grant(client_a_admin,seed_data,fully_reviewed_new_property_import):
    import json
    from sqlalchemy.exc import DBAPIError
    from tests.test_property_source_access import grant
    item=fully_reviewed_new_property_import.item
    prop=await client_a_admin.post('/api/properties',json={'name':'Proof guard target','management_type':'WEG'})
    assert prop.status_code==201
    property_id=uuid.UUID(prop.json()['id'])
    domain,grant_id=await grant(client_a_admin,seed_data,property_id,actor=seed_data['user_a_admin_id'],caps=['read','prepare'])
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        parameters={'id':item.id,'property':property_id,'domain':uuid.UUID(domain),'grant':uuid.UUID(grant_id),
                    'actor':seed_data['user_a_admin_id'],'result':json.dumps({'status':'APPLIED','property_id':str(property_id)})}
        with pytest.raises(DBAPIError,match='current creator and destination authority'):
            async with db.begin_nested():
                await db.execute(text("UPDATE declaration_import SET status='APPLIED',applied_property_id=:property,approved_by=:actor,approved_at=clock_timestamp(),"
                    "approval_result_json=CAST(:result AS jsonb),approval_idempotency_key='direct-proof-test',approval_request_digest=repeat('a',64),"
                    'publication_domain_id=:domain,publication_grant_id=:grant WHERE id=:id'),parameters)
        with pytest.raises(DBAPIError,match='reviewed transition'):
            async with db.begin_nested():
                await db.execute(text("INSERT INTO declaration_import(organization_id,mode,status,initiated_by,max_cost_usd,publication_domain_id) "
                    "VALUES(:org,'NEW_PROPERTY','DRAFT',:actor,1,:domain)"),{**parameters,'org':seed_data['org_a_id']})


async def test_publication_migration_retains_evidence_and_worker_checks_expanded_schema(client_a_admin,seed_data,fully_reviewed_new_property_import):
    import importlib
    from contextlib import asynccontextmanager
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL,clear_disposable_source_context
    from app.workers.declaration_import import DeclarationImportWorker
    item=fully_reviewed_new_property_import.item
    assert (await client_a_admin.post(f'/api/declaration-imports/{item.id}/approve',json=command(seed_data,item))).status_code==200
    migration=importlib.import_module('migrations.versions.0051_declaration_publication_audience')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as connection,Operations.context(MigrationContext.configure(connection)):
            class View:
                async def execute(self,statement,params=None): return connection.execute(statement,params or {})
                async def scalar(self,statement): return connection.scalar(statement)
            @asynccontextmanager
            async def factory(): yield View()
            worker=DeclarationImportWorker(object(),unscoped_session_factory=factory)
            assert await worker.schema_ready()
            with pytest.raises(DBAPIError,match='Retain populated publication'):
                with connection.begin_nested(): migration.downgrade()
            nested=connection.begin_nested()
            try:
                clear_disposable_source_context(connection.connection.cursor())
                importlib.import_module('migrations.versions.0052_property_source_admission_review').downgrade()
                migration.downgrade()
                assert not await worker.schema_ready()
                migration.upgrade()
                assert await worker.schema_ready()
            finally:
                nested.rollback()
            assert connection.scalar(text('SELECT publication_domain_id FROM declaration_import WHERE id=:id'),{'id':item.id}) is not None
            assert connection.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:id AND action='declaration_import.audience_published'"),{'id':item.id})==1
    finally:
        engine.dispose()


async def test_other_active_administrator_cannot_publish_creator_sources_even_via_sql(client_a_admin,client_a_clerk,seed_data,fully_reviewed_new_property_import):
    import json
    from sqlalchemy.exc import DBAPIError
    from tests.test_property_source_access import grant
    from tests.test_scoped_account_statements import mutate
    item=fully_reviewed_new_property_import.item
    prop=await client_a_admin.post('/api/properties',json={'name':'Other publisher target','management_type':'WEG'})
    property_id=uuid.UUID(prop.json()['id'])
    domain,grant_id=await grant(client_a_admin,seed_data,property_id,actor=seed_data['user_a_clerk_id'],caps=['read','prepare','approve'])
    actor=seed_data['user_a_clerk_id']
    await mutate(text("UPDATE membership SET role='OWNER_ADMIN' WHERE organization_id=:org AND user_id=:actor"),{'org':seed_data['org_a_id'],'actor':actor})
    try:
        assert (await client_a_clerk.post(f'/api/declaration-imports/{item.id}/approve',json=command(seed_data,item))).status_code==404
        async with get_tenant_session(seed_data['org_a_id']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(actor)})
            with pytest.raises(DBAPIError,match='current creator and destination authority'):
                async with db.begin_nested():
                    await db.execute(text("UPDATE declaration_import SET status='APPLIED',applied_property_id=:property,approved_by=:actor,approved_at=clock_timestamp(),"
                        "approval_result_json=CAST(:result AS jsonb),approval_idempotency_key='other-creator-proof',approval_request_digest=repeat('b',64),"
                        'publication_domain_id=:domain,publication_grant_id=:grant WHERE id=:id'),
                        {'id':item.id,'property':property_id,'actor':actor,'domain':uuid.UUID(domain),'grant':uuid.UUID(grant_id),
                         'result':json.dumps({'status':'APPLIED','property_id':str(property_id)})})
    finally:
        await mutate(text("UPDATE membership SET role='CLERK' WHERE organization_id=:org AND user_id=:actor"),{'org':seed_data['org_a_id'],'actor':actor})


@pytest.mark.parametrize('table',['property_data_domain','property_data_grant'])
async def test_busy_publication_fk_parent_refuses_migration_immediately(seed_data,table):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0051_declaration_publication_audience')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        async with get_tenant_session(seed_data['org_a_id']) as reader:
            await reader.execute(text(f'SELECT id FROM {table} LIMIT 1'))
            with pytest.raises(DBAPIError) as caught:
                with engine.begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout='1s'"))
                    with Operations.context(MigrationContext.configure(connection)): migration.upgrade()
            assert caught.value.orig.pgcode=='55P03'
            assert 'could not obtain lock' in str(caught.value.orig)
            await reader.execute(text("SET LOCAL lock_timeout='1s'"))
            await reader.execute(text('SELECT id FROM declaration_import LIMIT 1'))
    finally:
        engine.dispose()
