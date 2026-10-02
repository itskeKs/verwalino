"""Strict bound source access needs retained proof; quarantine dominates it."""
import uuid
import pytest
from sqlalchemy import text
from app.core.db import get_tenant_session
from tests.test_property_source_access import access
from tests.test_property_source_admission import setup
from tests.test_property_source_binding import approve_grant, applied, bind
from tests.test_property_source_classification import legacy_bound, classification_body
from tests.test_property_source_enforcement import synthetic_enforcement, change_mode
from tests.test_declaration_import_api import isolated_document_store, enable_declaration_imports  # noqa: F401

pytestmark = pytest.mark.asyncio


async def classify(api, ids, decision='ADMITTED'):
    response = await api.post('/api/property-source-classifications',
        headers={'Idempotency-Key': str(uuid.uuid4())},
        json=await classification_body(api, ids, decision))
    assert response.status_code == 201, response.text


async def check_sources(seed, ids, expected):
    for kind, resource in [('property_document', ids['document']), ('declaration_import', ids['job'])]:
        assert await access(seed, kind, resource, actor=seed['user_a_clerk_id']) is expected, kind


async def test_bound_legacy_association_is_not_strict_proof(client_a_admin, seed_data):
    ids = await legacy_bound(client_a_admin, seed_data)
    await check_sources(seed_data, ids, True)  # Existing prepared resolver compatibility.
    async with synthetic_enforcement(seed_data):
        await check_sources(seed_data, ids, False)
        await change_mode(seed_data, 'PAUSED')
        await check_sources(seed_data, ids, False)


async def test_quarantine_dominates_retained_attachment_proof(client_a_admin, seed_data):
    ids = await setup(client_a_admin, seed_data)
    await approve_grant(client_a_admin, seed_data, ids)
    await applied(seed_data, ids)
    await bind(seed_data, ids)
    await classify(client_a_admin, ids, 'QUARANTINED')
    await check_sources(seed_data, ids, True)
    async with synthetic_enforcement(seed_data):
        await check_sources(seed_data, ids, False)


async def test_current_admission_proof_survives_reviewer_revocation(client_a_admin, seed_data):
    ids = await legacy_bound(client_a_admin, seed_data)
    await classify(client_a_admin, ids)
    assert (await client_a_admin.post(f"/api/property-data-domains/{ids['domain']}/grants/{ids['review_grant']}/revoke")).status_code == 204
    async with synthetic_enforcement(seed_data):
        await check_sources(seed_data, ids, True)
        assert not await access(seed_data, 'property_document', ids['document'])
        assert (await client_a_admin.post(f"/api/property-data-domains/{ids['domain']}/grants/{ids['reader_grant']}/revoke")).status_code == 204
        await check_sources(seed_data, ids, False)


@pytest.mark.parametrize('proof', ['attachment', 'review'])
async def test_bound_exact_earlier_proof_is_retained(client_a_admin, seed_data, proof):
    if proof == 'attachment':
        ids = await setup(client_a_admin, seed_data)
        await approve_grant(client_a_admin, seed_data, ids)
    else:
        from tests.test_property_source_admission_review import legacy_source, pending_body
        ids = await legacy_source(client_a_admin, seed_data)
        response = await client_a_admin.post('/api/property-source-admission-reviews',
            headers={'Idempotency-Key': str(uuid.uuid4())}, json=await pending_body(client_a_admin, ids))
        assert response.status_code == 201, response.text
    await applied(seed_data, ids)
    async with synthetic_enforcement(seed_data):
        await bind(seed_data, ids)
        await check_sources(seed_data, ids, True)


async def test_new_publication_remains_accessible_after_binding(client_a_admin, seed_data, fully_reviewed_new_property_import):
    from tests.test_declaration_publication_audience import command
    item = fully_reviewed_new_property_import.item
    async with synthetic_enforcement(seed_data):
        response = await client_a_admin.post(f'/api/declaration-imports/{item.id}/approve', json=command(seed_data, item))
        assert response.status_code == 200, response.text
        assert await access(seed_data, 'declaration_import', item.id)
        async with get_tenant_session(seed_data['org_a_id']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(seed_data['user_a_admin_id'])})
            documents = (await db.scalars(text('SELECT document_id FROM declaration_import_document WHERE import_id=:job'), {'job':item.id})).all()
        assert documents
        for document in documents:
            assert await access(seed_data, 'property_document', document)


async def test_classification_change_waits_for_inflight_source_read(client_a_admin, seed_data):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    ids = await setup(client_a_admin, seed_data)
    await approve_grant(client_a_admin, seed_data, ids)
    await applied(seed_data, ids)
    await bind(seed_data, ids)
    body = await classification_body(client_a_admin, ids, 'QUARANTINED')
    engine = create_async_engine(MIGRATOR_URL)
    task = None
    try:
        async with synthetic_enforcement(seed_data):
            async with get_tenant_session(seed_data['org_a_id']) as reader:
                await reader.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(seed_data['user_a_clerk_id'])})
                assert await reader.scalar(text("SELECT property_source_permitted(:org,'property_document',:document,'read')"), {**ids,'org':seed_data['org_a_id']})
                task = asyncio.create_task(client_a_admin.post('/api/property-source-classifications',
                    headers={'Idempotency-Key':str(uuid.uuid4())}, json=body))
                async with engine.connect() as observer:
                    blocked = False
                    for _ in range(300):
                        await observer.execute(text('SELECT pg_stat_clear_snapshot()'))
                        blocked = await observer.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE 'SELECT pg_advisory_xact_lock(%')"))
                        if blocked: break
                        await asyncio.sleep(.01)
                    assert blocked and not task.done()
                await reader.execute(text('SELECT id FROM declaration_import WHERE id=:job FOR UPDATE NOWAIT'), ids)
                await reader.execute(text('SELECT id FROM property_document WHERE id=:document FOR UPDATE NOWAIT'), ids)
            response = await asyncio.wait_for(task, 5)
            assert response.status_code == 201, response.text
            await check_sources(seed_data, ids, False)
    finally:
        await engine.dispose()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_quarantined_original_blocks_all_derived_and_qa_input(approved_knowledge_graph, seed_data, client_a_admin):
    from app.api.property_knowledge import get_knowledge_provider
    from app.declarations.fake_knowledge_provider import FakeKnowledgeProvider
    from app.main import app
    from tests.test_knowledge_source_enforcement import configure_budget, ledger
    from tests.test_property_source_access import grant
    from tests.test_scoped_account_statements import mutate
    graph = approved_knowledge_graph
    await grant(client_a_admin, seed_data, graph.property_id)
    await configure_budget(seed_data)
    provider = FakeKnowledgeProvider()
    app.dependency_overrides[get_knowledge_provider] = lambda: provider
    try:
        async with get_tenant_session(graph.organization_id) as db:
            documents = (await db.scalars(text('SELECT id FROM property_document WHERE property_id=:property ORDER BY id'), {'property':graph.property_id})).all()
            derived = {}
            for kind in ['approved_fact_source', 'property_rule_source', 'property_document_relation']:
                derived[kind] = (await db.scalars(text(f'SELECT id FROM {kind} WHERE property_id=:property'), {'property':graph.property_id})).all()
                assert derived[kind]
        for index, document in enumerate(documents):
            await classify(client_a_admin, {'document':document}, 'QUARANTINED' if index == 0 else 'ADMITTED')
        async with synthetic_enforcement(seed_data):
            # Withhold the denied original from ordinary reads; the expected
            # graph must still include it, regardless of selected search hits.
            await mutate(text("CREATE POLICY test_bound_hidden ON property_document AS RESTRICTIVE FOR SELECT USING(property_source_permitted(organization_id,'property_document',id,'read'))"), {})
            try:
                assert not await access(seed_data, 'property_document', documents[0])
                async with get_tenant_session(graph.organization_id) as db:
                    await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(seed_data['user_a_admin_id'])})
                    assert await db.scalar(text('SELECT count(*) FROM property_document WHERE property_id=:property'), {'property':graph.property_id}) == len(documents)-1
                    for kind, expected in derived.items():
                        rows = (await db.scalars(text(f"SELECT id FROM {kind} WHERE property_id=:property"), {'property':graph.property_id})).all()
                        assert rows == []
                        results = [await access(seed_data, kind, row) for row in expected]
                        assert not all(results), kind
                response = await client_a_admin.post(f'/api/properties/{graph.property_id}/knowledge/query',json={'question':'Aufzug'})
                assert response.status_code == 404, response.text
                assert provider.calls == [] and not await ledger(graph)
            finally: await mutate(text('DROP POLICY test_bound_hidden ON property_document'), {})
    finally:
        app.dependency_overrides.pop(get_knowledge_provider, None)
        await mutate(text('TRUNCATE property_data_domain CASCADE'), {})
        await mutate(text('TRUNCATE knowledge_budget_policy'), {})


async def test_bound_proof_migration_retains_strict_state_and_classifications(client_a_admin, seed_data):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = await legacy_bound(client_a_admin, seed_data)
    await classify(client_a_admin, ids)
    migration = importlib.import_module('migrations.versions.0057_bound_source_access')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        async with synthetic_enforcement(seed_data):
            with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError, match='Retain strict bound source resolution'):
                    with db.begin_nested(): migration.downgrade()
                savepoint = db.begin_nested()
                try:
                    db.execute(text('TRUNCATE property_source_security'))
                    migration.downgrade()
                    assert db.scalar(text('SELECT count(*) FROM property_source_classification WHERE document_id=:document'), ids) == 1
                    assert db.scalar(text("SELECT to_regprocedure('property_source_bound_proven(uuid,uuid)')")) is None
                    migration.upgrade()
                    for function in ['property_source_bound_proven(uuid,uuid)', 'property_source_import_actor_access(uuid,uuid,uuid,text)', 'property_source_document_actor_access(uuid,uuid,uuid,text)']:
                        assert 'row_security=off' in db.scalar(text('SELECT proconfig FROM pg_proc WHERE oid=CAST(:name AS regprocedure)'), {'name':function})
                        assert not db.scalar(text("SELECT has_function_privilege('app_rw',:name,'EXECUTE')"), {'name':function})
                    # The disposable context substituted readiness; this
                    # migration must not alter even that temporary definition.
                    assert db.scalar(text('SELECT property_source_activation_ready(:org)'), {'org':seed_data['org_a_id']}) is True
                finally: savepoint.rollback()
            await check_sources(seed_data, ids, True)
        with engine.begin() as db:
            assert db.scalar(text('SELECT property_source_activation_ready(:org)'), {'org':seed_data['org_a_id']}) is False
    finally: engine.dispose()


@pytest.mark.parametrize('table', ['property_document','declaration_import','declaration_import_document','property_source_admission','property_source_admission_review','property_source_classification','property_source_security'])
async def test_bound_proof_migration_requires_quiescent_dependencies(seed_data, table):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration = importlib.import_module('migrations.versions.0057_bound_source_access')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as reader:
            reader.execute(text(f'SELECT 1 FROM {table} LIMIT 1'))
            with pytest.raises(DBAPIError) as caught:
                with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
                    db.execute(text("SET LOCAL lock_timeout='1s'"))
                    migration.upgrade()
            assert caught.value.orig.pgcode == '55P03'
    finally: engine.dispose()


async def test_wrong_proof_owner_cannot_hide_quarantine(client_a_admin, seed_data):
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = await legacy_bound(client_a_admin, seed_data)
    await classify(client_a_admin, ids, 'QUARANTINED')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        async with synthetic_enforcement(seed_data):
            with engine.begin() as db:
                savepoint = db.begin_nested()
                try:
                    db.execute(text('ALTER FUNCTION property_source_bound_proven(uuid,uuid) OWNER TO app_rw'))
                    db.execute(text('SET LOCAL ROLE app_rw'))
                    db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
                        {'org':str(seed_data['org_a_id']),'actor':str(seed_data['user_a_clerk_id'])})
                    with pytest.raises(DBAPIError, match='row-level security'):
                        with db.begin_nested():
                            db.scalar(text("SELECT property_source_permitted(:org,'property_document',:document,'read')"), {**ids,'org':seed_data['org_a_id']})
                finally: savepoint.rollback()
    finally: engine.dispose()


async def test_worker_cancels_queued_bound_quarantined_source_before_load(client_a_admin, seed_data):
    from datetime import datetime, timezone
    from app.repositories.declaration_import_repo import DeclarationImportRepository
    ids = await setup(client_a_admin, seed_data)
    await approve_grant(client_a_admin, seed_data, ids)
    # A legacy bound draft can be quarantined but never admitted as an applied
    # original. PREPARATION still permits its existing compatibility queue flow.
    await bind(seed_data, ids)
    await classify(client_a_admin, ids, 'QUARANTINED')
    response = await client_a_admin.post(f"/api/declaration-imports/{ids['job']}/start", headers={'Idempotency-Key':str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    async with synthetic_enforcement(seed_data):
        async with get_tenant_session(seed_data['org_a_id']) as db:
            assert await DeclarationImportRepository(db, seed_data['org_a_id']).claim_next('bound-quarantine', datetime.now(timezone.utc), 120) is None
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(seed_data['user_a_admin_id'])})
            assert await db.scalar(text('SELECT count(*) FROM declaration_import WHERE id=:job'), ids) == 0
    # Inspect retained cancellation after leaving the synthetic strict state;
    # hidden ordinary rows cannot prove whether the worker durably cancelled.
    async with get_tenant_session(seed_data['org_a_id']) as db:
        row = (await db.execute(text('SELECT status,error_code FROM declaration_import WHERE id=:job'), ids)).one()
        assert row == ('CANCELLED','JOB_PERMISSION_CHANGED')
        assert await db.scalar(text('SELECT count(*) FROM declaration_extraction_run WHERE import_id=:job'), ids) == 0
