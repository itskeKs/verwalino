"""Strict Q&A consumes a fresh runtime proof and the currently reviewed policy."""
import uuid
from decimal import Decimal
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.config import settings
from app.core.db import get_tenant_session
from app.api.property_knowledge import get_knowledge_provider
from app.declarations.fake_knowledge_provider import FakeKnowledgeProvider
from app.main import app
from tests.test_knowledge_source_enforcement import granted_graph,ledger
from tests.test_property_source_enforcement import synthetic_enforcement

pytestmark = pytest.mark.asyncio


async def review_budget(api):
    status = await api.get('/api/access-preparation/runtime-budget')
    assert status.status_code==200,status.text
    state = status.json()
    response = await api.post('/api/access-preparation/runtime-budget/review',json={
        'expected_review_id':state['review']['id'] if state['review'] else None,
        'policy_digest':state['policy_digest'],'reason':'Synthetic query runtime review','acknowledged':True},
        headers={'Idempotency-Key':str(uuid.uuid4())})
    assert response.status_code==201,response.text
    return response.json()


@pytest.mark.parametrize('old_proof',[False,True])
async def test_strict_reservation_requires_actual_runtime_proof(approved_knowledge_graph,seed_data,client_a_admin,monkeypatch,old_proof):
    monkeypatch.setattr(settings,'declaration_qa_enabled',True)
    graph = approved_knowledge_graph
    async with granted_graph(client_a_admin,seed_data,graph),synthetic_enforcement(seed_data):
        await review_budget(client_a_admin)
        if old_proof:
            from app.core.runtime_budget import attest_runtime_budget,runtime_budget_policy
            async with get_tenant_session(graph.organization_id) as prior:
                await attest_runtime_budget(prior,graph.organization_id,seed_data['user_a_admin_id'],runtime_budget_policy())
        with pytest.raises(DBAPIError,match='Reviewed runtime budget required'):
            async with get_tenant_session(graph.organization_id) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
                await db.execute(text("SELECT * FROM reserve_knowledge_query(:org,:property,'fake','unattested',0.001)"),
                    {'org':graph.organization_id,'property':graph.property_id})
        assert not await ledger(graph)


@pytest.mark.parametrize('change',['cap','window','expiry','disabled'])
async def test_runtime_drift_stops_before_provider_and_new_review_recovers(approved_knowledge_graph,seed_data,client_a_admin,monkeypatch,change):
    monkeypatch.setattr(settings,'declaration_qa_enabled',True)
    graph = approved_knowledge_graph
    provider = FakeKnowledgeProvider()
    app.dependency_overrides[get_knowledge_provider] = lambda:provider
    try:
        async with granted_graph(client_a_admin,seed_data,graph),synthetic_enforcement(seed_data):
            await review_budget(client_a_admin)
            if change=='cap': monkeypatch.setattr(settings,'declaration_qa_max_cost_usd',settings.declaration_qa_max_cost_usd+Decimal('0.01'))
            elif change=='window': monkeypatch.setattr(settings,'declaration_qa_budget_window_minutes',settings.declaration_qa_budget_window_minutes+1)
            elif change=='expiry': monkeypatch.setattr(settings,'declaration_qa_reservation_seconds',settings.declaration_qa_reservation_seconds+1)
            else: monkeypatch.setattr(settings,'declaration_qa_enabled',False)
            response = await client_a_admin.post(f'/api/properties/{graph.property_id}/knowledge/query',json={'question':'Heizkosten'})
            assert response.status_code==503,response.text
            assert provider.calls==[] and not await ledger(graph)
            await review_budget(client_a_admin)
            response = await client_a_admin.post(f'/api/properties/{graph.property_id}/knowledge/query',json={'question':'Heizkosten'})
            assert response.status_code==(200 if change!='disabled' else 503),response.text
            assert len(provider.calls)==(1 if change!='disabled' else 0)
    finally: app.dependency_overrides.pop(get_knowledge_provider,None)


async def test_missing_registrar_prevents_provider_work(approved_knowledge_graph,seed_data,client_a_admin,monkeypatch):
    from contextlib import asynccontextmanager
    from app.core import runtime_budget
    graph = approved_knowledge_graph
    provider = FakeKnowledgeProvider()
    app.dependency_overrides[get_knowledge_provider] = lambda:provider
    try:
        async with granted_graph(client_a_admin,seed_data,graph),synthetic_enforcement(seed_data):
            @asynccontextmanager
            async def unavailable(org):
                raise RuntimeError('unconfigured registrar')
                yield
            monkeypatch.setattr(runtime_budget,'get_review_tenant_session',unavailable)
            response = await client_a_admin.post(f'/api/properties/{graph.property_id}/knowledge/query',json={'question':'Heizkosten'})
            assert response.status_code==503,response.text
            assert provider.calls==[] and not await ledger(graph)
    finally: app.dependency_overrides.pop(get_knowledge_provider,None)


async def test_inflight_policy_change_still_accounts_admitted_work(approved_knowledge_graph,seed_data,client_a_admin,monkeypatch):
    monkeypatch.setattr(settings,'declaration_qa_enabled',True)
    graph = approved_knowledge_graph
    class Provider(FakeKnowledgeProvider):
        async def answer(self,*args,**kwargs):
            result = await super().answer(*args,**kwargs)
            monkeypatch.setattr(settings,'declaration_qa_enabled',False)
            return result
    provider = Provider()
    app.dependency_overrides[get_knowledge_provider] = lambda:provider
    try:
        async with granted_graph(client_a_admin,seed_data,graph),synthetic_enforcement(seed_data):
            first = await client_a_admin.post(f'/api/properties/{graph.property_id}/knowledge/query',json={'question':'Heizkosten'})
            assert first.status_code==200,first.text
            rows = await ledger(graph)
            assert len(rows)==1 and rows[0]['status']=='FINALIZED' and rows[0]['estimated_cost_usd']>0
            second = await client_a_admin.post(f'/api/properties/{graph.property_id}/knowledge/query',json={'question':'Heizkosten'})
            assert second.status_code==503,second.text
            assert len(provider.calls)==1 and len(await ledger(graph))==1
    finally: app.dependency_overrides.pop(get_knowledge_provider,None)


from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401


async def test_reservation_migration_preserves_identity_and_refuses_strict_rollback(migrator_session,scope_seed):
    import hashlib,importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_access_coverage import fixture
    from tests.test_property_source_enforcement import change_mode
    await fixture(migrator_session,scope_seed)
    migration = importlib.import_module('migrations.versions.0089_runtime_budget_reservation')
    engine = create_engine(MIGRATOR_SYNC_URL)
    identity = text('SELECT oid,proowner,proacl::text,provolatile,prosecdef,proconfig,prosrc FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)')
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            point = db.begin_nested()
            try:
                original = db.execute(identity,{'signature':migration.SIGNATURE}).one()
                migration.downgrade()
                restored = db.execute(identity,{'signature':migration.SIGNATURE}).one()
                assert restored[:-1]==original[:-1]
                assert hashlib.sha256(restored.prosrc.encode()).hexdigest()==migration.EXPECTED
                migration.upgrade()
                assert db.execute(identity,{'signature':migration.SIGNATURE}).one()==original
            finally: point.rollback()
        async with synthetic_enforcement(scope_seed):
            for mode in ('ENFORCED','PAUSED'):
                if mode=='PAUSED': await change_mode(scope_seed,mode)
                with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
                    with pytest.raises(DBAPIError,match='Retain strict runtime budget enforcement'):
                        with db.begin_nested(): migration.downgrade()
    finally: engine.dispose()
