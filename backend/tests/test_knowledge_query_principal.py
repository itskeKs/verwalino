"""Knowledge-provider answers require the same live principal at every phase."""
import pytest
from sqlalchemy import text
from app.api.property_knowledge import get_knowledge_provider
from app.core.db import get_tenant_session
from app.declarations.fake_knowledge_provider import FakeKnowledgeProvider
from app.main import app

pytestmark = pytest.mark.asyncio


async def test_query_reservation_records_actual_request_actor(approved_knowledge_graph, client_a_admin, seed_data, migrator_session):
    provider = FakeKnowledgeProvider()
    app.dependency_overrides[get_knowledge_provider] = lambda: provider
    try:
        response = await client_a_admin.post(f'/api/properties/{approved_knowledge_graph.property_id}/knowledge/query',json={'question':'Heizkosten'})
        assert response.status_code == 200, response.text
        async with get_tenant_session(approved_knowledge_graph.organization_id) as session:
            actor = await session.scalar(text('SELECT initiated_by FROM property_knowledge_query_run WHERE property_id=:property'),{'property':approved_knowledge_graph.property_id})
            assert actor == seed_data['user_a_admin_id']
    finally:
        app.dependency_overrides.pop(get_knowledge_provider,None)
        await migrator_session.execute(text("DELETE FROM property_knowledge_query_run WHERE property_id=:property"), {"property": approved_knowledge_graph.property_id})


from app.core import tenant
from tests.test_scoped_account_statements import mutate


async def set_actor(session, actor):
    await session.execute(text("SELECT set_config('app.current_user_id', :actor, true)"), {'actor':str(actor)})


async def active(seed, value):
    await mutate(text('UPDATE "user" SET is_active=:active WHERE id=:actor'), {'active':value,'actor':seed['user_a_admin_id']})


@pytest.mark.parametrize('path', ['knowledge', 'rules', 'knowledge/search?q=Heizkosten', 'knowledge/query'])
async def test_stale_http_principal_denied_before_knowledge_or_provider(approved_knowledge_graph, client_a_admin, seed_data, monkeypatch, path):
    original = tenant.validate_session_principal
    async def stale(*args, **kwargs):
        role = await original(*args, **kwargs)
        await active(seed_data, False)
        return role
    monkeypatch.setattr(tenant, 'validate_session_principal', stale)
    def forbidden():
        raise AssertionError('Denied principal must not construct provider')
    app.dependency_overrides[get_knowledge_provider] = forbidden
    try:
        route = f'/api/properties/{approved_knowledge_graph.property_id}/{path}'
        response = await client_a_admin.post(route,json={'question':'Heizkosten'}) if path.endswith('query') else await client_a_admin.get(route)
        assert response.status_code == 401, response.text
    finally:
        await active(seed_data, True)
        app.dependency_overrides.pop(get_knowledge_provider,None)


async def test_read_only_member_retains_knowledge_query(approved_knowledge_graph, client_a_admin, seed_data, migrator_session):
    actor = seed_data['user_a_admin_id']
    await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE user_id=:actor"), {'actor':actor})
    app.dependency_overrides[get_knowledge_provider] = lambda: FakeKnowledgeProvider()
    try:
        response = await client_a_admin.post(f'/api/properties/{approved_knowledge_graph.property_id}/knowledge/query',json={'question':'Heizkosten'})
        assert response.status_code == 200, response.text
    finally:
        await mutate(text("UPDATE membership SET role='OWNER_ADMIN' WHERE user_id=:actor"), {'actor':actor})
        app.dependency_overrides.pop(get_knowledge_provider,None)
        await migrator_session.execute(text('DELETE FROM property_knowledge_query_run WHERE property_id=:property'), {'property':approved_knowledge_graph.property_id})


@pytest.mark.parametrize('phase', ['reservation', 'answer'])
async def test_revocation_between_transactions_suppresses_answer_and_preserves_cost(approved_knowledge_graph, client_a_admin, seed_data, migrator_session, monkeypatch, phase):
    from app.services import property_knowledge_service as service
    original_actor = service._knowledge_actor
    checks = 0
    async def actor_check(*args, **kwargs):
        nonlocal checks
        checks += 1
        if phase == 'reservation' and checks == 2:
            await active(seed_data, False)
        await original_actor(*args, **kwargs)
    monkeypatch.setattr(service, '_knowledge_actor', actor_check)
    class RevokingProvider(FakeKnowledgeProvider):
        async def answer(self, request, *, max_output_tokens):
            result = await super().answer(request,max_output_tokens=max_output_tokens)
            await active(seed_data, False)
            return result
    provider = RevokingProvider()
    app.dependency_overrides[get_knowledge_provider] = lambda: provider
    try:
        response = await client_a_admin.post(f'/api/properties/{approved_knowledge_graph.property_id}/knowledge/query',json={'question':'Heizkosten'})
        assert response.status_code == 401, response.text
        async with get_tenant_session(approved_knowledge_graph.organization_id) as session:
            row = (await session.execute(text('SELECT * FROM property_knowledge_query_run WHERE property_id=:property'), {'property':approved_knowledge_graph.property_id})).mappings().first()
            if phase == 'reservation':
                assert row is None
                assert provider.calls == []
            else:
                assert row['status'] == 'FINALIZED'
                assert row['error_code'] == row['outcome'] == 'QA_PERMISSION_CHANGED'
                assert row['estimated_cost_usd'] == row['reserved_cost_usd'] > 0
                assert row['input_tokens'] == row['output_tokens'] == 0
                assert row['latency_ms'] is None
                assert await session.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:run AND action='knowledge.permission_cancelled'"), {'run':row['id']}) == 1
                assert await session.scalar(text("SELECT actor_user_id FROM audit_event WHERE entity_id=:run AND action='knowledge.permission_cancelled'"), {'run':row['id']}) == seed_data['user_a_admin_id']
                await set_actor(session,seed_data['user_a_admin_id'])
                assert not await session.scalar(text('SELECT cancel_unauthorized_knowledge(:org,:run)'), {'org':approved_knowledge_graph.organization_id,'run':row['id']})
    finally:
        await active(seed_data,True)
        app.dependency_overrides.pop(get_knowledge_provider,None)
        await migrator_session.execute(text('DELETE FROM property_knowledge_query_run WHERE property_id=:property'), {'property':approved_knowledge_graph.property_id})


async def reserve_fixture(graph, actor, *, expired=False):
    import uuid
    run = uuid.uuid4()
    async with get_tenant_session(graph.organization_id) as session:
        await set_actor(session,actor)
        await session.execute(text("""INSERT INTO property_knowledge_query_run
          (id,organization_id,property_id,initiated_by,budget_window_start,provider,model,reserved_cost_usd,reservation_expires_at)
          VALUES(:run,:org,:property,:actor,date_trunc('hour',now()),'fake','test',0.01,
            now()+CASE WHEN :expired THEN interval '-1 second' ELSE interval '1 hour' END)"""),
          {'run':run,'org':graph.organization_id,'property':graph.property_id,'actor':actor,'expired':expired})
    return run


async def cleanup_runs(session, graph):
    await session.execute(text('DELETE FROM property_knowledge_query_run WHERE property_id=:property'),{'property':graph.property_id})


async def test_actor_and_cancellation_fences(approved_knowledge_graph, seed_data, migrator_session):
    from sqlalchemy.exc import DBAPIError
    graph = approved_knowledge_graph
    actor, clerk = seed_data['user_a_admin_id'], seed_data['user_a_clerk_id']
    run = await reserve_fixture(graph,actor)
    params = {'org':graph.organization_id,'run':run,'actor':clerk}
    try:
        async with get_tenant_session(graph.organization_id) as session:
            await set_actor(session,actor)
            for sql in [
                'UPDATE property_knowledge_query_run SET initiated_by=:actor WHERE id=:run',
                "UPDATE property_knowledge_query_run SET status='EXPIRED',outcome='RESERVATION_EXPIRED',error_code='RESERVATION_EXPIRED',finalized_at=now() WHERE id=:run",
            ]:
                with pytest.raises(DBAPIError):
                    async with session.begin_nested():
                        await session.execute(text(sql),params)
            assert not await session.scalar(text('SELECT cancel_unauthorized_knowledge(:org,:run)'),params)
            await set_actor(session,clerk)
            with pytest.raises(DBAPIError,match='actor mismatch'):
                async with session.begin_nested():
                    await session.execute(text("UPDATE property_knowledge_query_run SET status='FINALIZED',outcome='SUCCESS',finalized_at=now() WHERE id=:run"),params)
        await active(seed_data,False)
        async with get_tenant_session(graph.organization_id) as session:
            await set_actor(session,clerk)
            assert not await session.scalar(text('SELECT cancel_unauthorized_knowledge(:org,:run)'),params)
        async with get_tenant_session(seed_data['org_b_id']) as session:
            await set_actor(session,actor)
            assert not await session.scalar(text('SELECT cancel_unauthorized_knowledge(:org,:run)'),params)
        async with get_tenant_session(graph.organization_id) as session:
            await set_actor(session,actor)
            with pytest.raises(DBAPIError,match='metadata invalid'):
                async with session.begin_nested():
                    await session.execute(text("UPDATE property_knowledge_query_run SET status='FINALIZED',outcome='SUCCESS',finalized_at=now() WHERE id=:run"),params)
            assert await session.scalar(text('SELECT cancel_unauthorized_knowledge(:org,:run)'),params)
            assert not await session.scalar(text('SELECT cancel_unauthorized_knowledge(:org,:run)'),params)
    finally:
        await active(seed_data,True)
        await cleanup_runs(migrator_session,graph)


async def test_cross_actor_expiry_requires_fixed_metadata(approved_knowledge_graph, seed_data, migrator_session):
    from sqlalchemy.exc import DBAPIError
    graph = approved_knowledge_graph
    run = await reserve_fixture(graph,seed_data['user_a_clerk_id'],expired=True)
    try:
        async with get_tenant_session(graph.organization_id) as session:
            await set_actor(session,seed_data['user_a_admin_id'])
            with pytest.raises(DBAPIError,match='cannot expire'):
                async with session.begin_nested():
                    await session.execute(text("UPDATE property_knowledge_query_run SET status='EXPIRED',outcome='arbitrary',finalized_at=now() WHERE id=:run"), {'run':run})
            await session.execute(text("UPDATE property_knowledge_query_run SET status='EXPIRED',outcome='RESERVATION_EXPIRED',error_code='RESERVATION_EXPIRED',finalized_at=now() WHERE id=:run"), {'run':run})
            assert await session.scalar(text('SELECT initiated_by FROM property_knowledge_query_run WHERE id=:run'), {'run':run}) == seed_data['user_a_clerk_id']
    finally:
        await cleanup_runs(migrator_session,graph)


async def test_migration_preserves_unknowns_and_refuses_loss_of_known_principals(approved_knowledge_graph, seed_data, migrator_session):
    import importlib
    import uuid
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL
    migration = importlib.import_module('migrations.versions.0044_knowledge_query_principal')
    graph = approved_knowledge_graph
    run = await reserve_fixture(graph,seed_data['user_a_admin_id'])
    engine = create_engine(MIGRATOR_SYNC_URL)
    legacy = uuid.uuid4()
    try:
        with engine.begin() as connection:
            with pytest.raises(DBAPIError,match='Retain populated'):
                with connection.begin_nested(), Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
            connection.execute(text('DELETE FROM property_knowledge_query_run WHERE id=:run'),{'run':run})
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                connection.execute(text("""INSERT INTO property_knowledge_query_run
                  (id,organization_id,property_id,budget_window_start,provider,model,reserved_cost_usd,reservation_expires_at)
                  VALUES(:run,:org,:property,date_trunc('hour',now()),'fake','legacy',0.01,now()-interval '1 hour')"""),
                  {'run':legacy,'org':graph.organization_id,'property':graph.property_id})
                migration.upgrade()
            assert connection.scalar(text('SELECT initiated_by FROM property_knowledge_query_run WHERE id=:run'),{'run':legacy}) is None
        async with get_tenant_session(graph.organization_id) as session:
            await set_actor(session,seed_data['user_a_admin_id'])
            await session.execute(text("UPDATE property_knowledge_query_run SET status='EXPIRED',outcome='RESERVATION_EXPIRED',error_code='RESERVATION_EXPIRED',finalized_at=now() WHERE id=:run"), {'run':legacy})
            assert await session.scalar(text('SELECT initiated_by FROM property_knowledge_query_run WHERE id=:run'),{'run':legacy}) is None
    finally:
        engine.dispose()
        await cleanup_runs(migrator_session,graph)


@pytest.mark.parametrize('identity', ['missing', 'other_member', 'foreign_member'])
async def test_new_reservation_requires_exact_current_actor(approved_knowledge_graph, seed_data, identity):
    import uuid
    from sqlalchemy.exc import DBAPIError
    actor = {'missing':None, 'other_member':seed_data['user_a_clerk_id'], 'foreign_member':seed_data['user_b_admin_id']}[identity]
    async with get_tenant_session(approved_knowledge_graph.organization_id) as session:
        await set_actor(session,seed_data['user_a_admin_id'])
        with pytest.raises(DBAPIError,match='requires live actor'):
            async with session.begin_nested():
                await session.execute(text("""INSERT INTO property_knowledge_query_run
                  (id,organization_id,property_id,initiated_by,budget_window_start,provider,model,reserved_cost_usd,reservation_expires_at)
                  VALUES(:run,:org,:property,:actor,date_trunc('hour',now()),'fake','test',0.01,now()+interval '1 hour')"""),
                  {'run':uuid.uuid4(),'org':approved_knowledge_graph.organization_id,'property':approved_knowledge_graph.property_id,'actor':actor})
