"""Q&A validates complete source authority at each short phase, including cost closure."""
import pytest
from sqlalchemy import text
from app.api.property_knowledge import get_knowledge_provider
from app.declarations.fake_knowledge_provider import FakeKnowledgeProvider
from app.main import app
from tests.test_property_source_enforcement import synthetic_enforcement,change_mode

pytestmark=pytest.mark.asyncio


async def test_strict_query_without_source_grant_never_calls_provider(approved_knowledge_graph,seed_data,client_a_admin,migrator_session):
    provider=FakeKnowledgeProvider()
    app.dependency_overrides[get_knowledge_provider]=lambda:provider
    try:
        async with synthetic_enforcement(seed_data):
            result=await client_a_admin.post(f'/api/properties/{approved_knowledge_graph.property_id}/knowledge/query',json={'question':'Heizkosten'})
            assert result.status_code==404,result.text
            assert provider.calls==[]
    finally:
        app.dependency_overrides.pop(get_knowledge_provider,None)
        await migrator_session.execute(text('DELETE FROM property_knowledge_query_run WHERE property_id=:property'),{'property':approved_knowledge_graph.property_id})


from contextlib import asynccontextmanager
from app.core.db import get_tenant_session
from tests.test_property_source_access import grant
from tests.test_scoped_account_statements import mutate


async def configure_budget(seed):
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    from app.core.config import settings
    engine=create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as db:
            await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
                {'org':str(seed['org_a_id']),'actor':str(seed['user_a_admin_id'])})
            await db.execute(text("INSERT INTO knowledge_budget_policy(organization_id,max_cost_usd,window_minutes,reservation_seconds,changed_by,reason) "
                "VALUES(:org,:cap,:window,:seconds,:actor,'Synthetic configured Q&A policy')"),
                {'org':seed['org_a_id'],'actor':seed['user_a_admin_id'],'cap':settings.declaration_qa_max_cost_usd,
                 'window':settings.declaration_qa_budget_window_minutes,'seconds':settings.declaration_qa_reservation_seconds})
    finally: await engine.dispose()


@asynccontextmanager
async def granted_graph(api,seed,graph):
    import uuid
    from tests.test_bound_source_access import classify
    domain,grant_id=await grant(api,seed,graph.property_id,caps=['read','prepare','approve'])
    try:
        # The fixture contains bound legacy originals. Review their known actual
        # uploader through the production API before exercising strict Q&A.
        async with get_tenant_session(graph.organization_id) as db:
            documents=(await db.scalars(text('SELECT id FROM property_document WHERE property_id=:property'),{'property':graph.property_id})).all()
        for document in documents:
            await classify(api,{'document':document,'property':graph.property_id})
        assert (await api.post(f'/api/property-data-domains/{domain}/grants/{grant_id}/revoke')).status_code==204
        response=await api.post(f'/api/property-data-domains/{domain}/grants',headers={'Idempotency-Key':str(uuid.uuid4())},
            json={'user_id':str(seed['user_a_admin_id']),'capabilities':['read'],'reason':'Query source reader'})
        assert response.status_code==201,response.text
        grant_id=response.json()['id']
        await configure_budget(seed)
        yield domain,grant_id
    finally:
        await mutate(text('DELETE FROM property_knowledge_query_run WHERE property_id=:property'),{'property':graph.property_id})
        # Only the guarded disposable test DB permits dropping synthetic immutable
        # setup through TRUNCATE. Production cleanup cannot erase these receipts.
        await mutate(text('TRUNCATE property_data_domain CASCADE'),{})
        await mutate(text('TRUNCATE knowledge_budget_policy'),{})


async def ledger(graph):
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    engine=create_async_engine(MIGRATOR_URL)
    try:
        async with engine.connect() as db:
            return (await db.execute(text('SELECT * FROM property_knowledge_query_run WHERE property_id=:property'),{'property':graph.property_id})).mappings().all()
    finally: await engine.dispose()


@pytest.mark.parametrize('change',['revoke','pause'])
@pytest.mark.parametrize('phase',['reservation','answer'])
async def test_source_loss_between_phases_denies_provider_or_durably_cancels_hidden_run(
    approved_knowledge_graph,seed_data,client_a_admin,monkeypatch,change,phase
):
    from app.services import property_knowledge_service as service
    graph=approved_knowledge_graph
    async with granted_graph(client_a_admin,seed_data,graph) as (domain,grant_id),synthetic_enforcement(seed_data):
        async def lose_access():
            if change=='pause': await change_mode(seed_data,'PAUSED')
            else: assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants/{grant_id}/revoke')).status_code==204
        original=service._knowledge_actor
        checks=0
        async def check(*args,**kwargs):
            nonlocal checks
            checks+=1
            if checks==2 and phase=='reservation': await lose_access()
            await original(*args,**kwargs)
        monkeypatch.setattr(service,'_knowledge_actor',check)
        class Provider(FakeKnowledgeProvider):
            async def answer(self,*args,**kwargs):
                result=await super().answer(*args,**kwargs)
                if phase=='answer': await lose_access()
                return result
        provider=Provider()
        app.dependency_overrides[get_knowledge_provider]=lambda:provider
        await mutate(text("CREATE POLICY test_knowledge_source ON property_knowledge_query_run AS RESTRICTIVE FOR SELECT USING(knowledge_query_source_access(organization_id,property_id))"),{})
        try:
            response=await client_a_admin.post(f'/api/properties/{graph.property_id}/knowledge/query',json={'question':'Heizkosten'})
            assert response.status_code==404,response.text
            rows=await ledger(graph)
            if phase=='reservation': assert not rows and provider.calls==[]
            else:
                assert len(provider.calls)==len(rows)==1
                row=rows[0]
                assert row['status']=='FINALIZED' and row['outcome']==row['error_code']=='QA_PERMISSION_CHANGED'
                assert row['estimated_cost_usd']==row['reserved_cost_usd']>0
                assert row['input_tokens']==row['output_tokens']==0 and row['latency_ms'] is None
                async with get_tenant_session(graph.organization_id) as db:
                    await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
                    assert await db.scalar(text('SELECT count(*) FROM property_knowledge_query_run WHERE id=:run'),{'run':row['id']})==0
                    assert not await db.scalar(text('SELECT cancel_unauthorized_knowledge(:org,:run)'),{'org':graph.organization_id,'run':row['id']})
                    assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:run AND action='knowledge.permission_cancelled'"),{'run':row['id']})==1
        finally:
            await mutate(text('DROP POLICY test_knowledge_source ON property_knowledge_query_run'),{})
            app.dependency_overrides.pop(get_knowledge_provider,None)


async def test_hidden_mismatched_source_blocks_whole_provider_input(approved_knowledge_graph,seed_data,client_a_admin):
    import uuid
    graph=approved_knowledge_graph
    async with granted_graph(client_a_admin,seed_data,graph),synthetic_enforcement(seed_data):
        # Deliberately inconsistent legacy provenance is not made safe by hiding
        # this one rule in ordinary RLS-filtered search results.
        other=await client_a_admin.post('/api/properties',json={'name':'Other source target','management_type':'WEG'})
        job=uuid.uuid4()
        await mutate(text("INSERT INTO declaration_import(id,organization_id,mode,target_property_id,status,initiated_by,max_cost_usd) "
            "VALUES(:job,:org,'EXISTING_PROPERTY',:property,'DRAFT',:actor,1)"),
            {'job':job,'org':graph.organization_id,'property':uuid.UUID(other.json()['id']),'actor':seed_data['user_a_admin_id']})
        bad_rule=uuid.uuid4()
        await mutate(text("INSERT INTO property_rule(id,organization_id,property_id,stable_key,topic,title,summary,scope_json,effect_json,status,source_import_id,approved_by,approved_at,search_text) "
            "SELECT :bad,organization_id,property_id,'legacy-mismatch','heating_costs','Legacy mismatch',summary,scope_json,effect_json,status,:job,approved_by,approved_at,search_text "
            "FROM property_rule WHERE id=:rule"),{'bad':bad_rule,'job':job,'rule':graph.heating_rule_id})
        await mutate(text("INSERT INTO property_rule_source(id,organization_id,property_id,property_rule_id,property_document_id,pdf_page,quote) "
            "SELECT gen_random_uuid(),organization_id,property_id,:bad,property_document_id,pdf_page,quote FROM property_rule_source WHERE property_rule_id=:rule"),
            {'bad':bad_rule,'rule':graph.heating_rule_id})
        provider=FakeKnowledgeProvider()
        app.dependency_overrides[get_knowledge_provider]=lambda:provider
        await mutate(text("CREATE POLICY test_hidden_knowledge_rule ON property_rule AS RESTRICTIVE FOR SELECT USING(property_source_access(organization_id,'property_rule',id,'read'))"),{})
        try:
            async with get_tenant_session(graph.organization_id) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
                assert await db.scalar(text('SELECT count(*) FROM property_rule WHERE property_id=:property'),{'property':graph.property_id})>0
                assert await db.scalar(text('SELECT count(*) FROM property_rule WHERE id=:rule'),{'rule':bad_rule})==0
            response=await client_a_admin.post(f'/api/properties/{graph.property_id}/knowledge/query',json={'question':'Aufzug'})
            assert response.status_code==404,response.text
            assert provider.calls==[] and not await ledger(graph)
        finally:
            await mutate(text('DROP POLICY test_hidden_knowledge_rule ON property_rule'),{})
            await mutate(text('DELETE FROM property_rule_source WHERE property_rule_id=:rule'),{'rule':bad_rule})
            await mutate(text('DELETE FROM property_rule WHERE id=:rule'),{'rule':bad_rule})
            app.dependency_overrides.pop(get_knowledge_provider,None)


@pytest.mark.parametrize('expired',[False,True])
async def test_budget_counts_and_expires_other_hidden_properties(approved_knowledge_graph,seed_data,client_a_admin,monkeypatch,expired):
    import uuid
    from datetime import datetime,timezone
    from decimal import Decimal
    from app.core.config import settings
    from app.services.property_knowledge_service import _utc_window_start
    graph=approved_knowledge_graph
    other=await client_a_admin.post('/api/properties',json={'name':'Hidden budget source','management_type':'WEG'})
    hidden_property=uuid.UUID(other.json()['id']);run=uuid.uuid4()
    amount=Decimal('0.001204')
    monkeypatch.setattr(settings,'declaration_qa_max_cost_usd',amount)
    async with get_tenant_session(graph.organization_id) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        await db.execute(text("INSERT INTO property_knowledge_query_run(id,organization_id,property_id,initiated_by,budget_window_start,provider,model,reserved_cost_usd,reservation_expires_at) "
            "VALUES(:run,:org,:property,:actor,:window,'fake','hidden',:amount,clock_timestamp()+CASE WHEN :expired THEN interval '-1 second' ELSE interval '1 hour' END)"),
            {'run':run,'org':graph.organization_id,'property':hidden_property,'actor':seed_data['user_a_admin_id'],'window':_utc_window_start(datetime.now(timezone.utc),settings.declaration_qa_budget_window_minutes),'amount':amount,'expired':expired})
    async with granted_graph(client_a_admin,seed_data,graph),synthetic_enforcement(seed_data):
        await mutate(text("CREATE POLICY test_knowledge_source ON property_knowledge_query_run AS RESTRICTIVE FOR SELECT USING(knowledge_query_source_access(organization_id,property_id))"),{})
        provider=FakeKnowledgeProvider();app.dependency_overrides[get_knowledge_provider]=lambda:provider
        try:
            async with get_tenant_session(graph.organization_id) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
                assert await db.scalar(text('SELECT count(*) FROM property_knowledge_query_run WHERE id=:run'),{'run':run})==0
            response=await client_a_admin.post(f'/api/properties/{graph.property_id}/knowledge/query',json={'question':'Heizkosten'})
            assert response.status_code==(200 if expired else 429),response.text
            assert len(provider.calls)==(1 if expired else 0)
            from types import SimpleNamespace
            hidden=(await ledger(SimpleNamespace(property_id=hidden_property)))[0]
            assert hidden['status']==('EXPIRED' if expired else 'RESERVED')
        finally:
            await mutate(text('DROP POLICY test_knowledge_source ON property_knowledge_query_run'),{})
            app.dependency_overrides.pop(get_knowledge_provider,None)
            await mutate(text('DELETE FROM property_knowledge_query_run WHERE id=:run'),{'run':run})


async def test_rule_fact_source_participates_in_complete_graph(approved_knowledge_graph,seed_data,client_a_admin):
    graph=approved_knowledge_graph
    await mutate(text("INSERT INTO approved_fact_source(organization_id,property_id,subject_kind,subject_id,field_key,approved_value_json,value_fingerprint,status,valid_from,property_document_id,pdf_page,quote,source_import_id,search_text) "
        "SELECT organization_id,property_id,'RULE',:rule,'title',approved_value_json,value_fingerprint,status,valid_from,property_document_id,pdf_page,quote,source_import_id,search_text "
        "FROM approved_fact_source WHERE property_id=:property AND subject_kind='PROPERTY' AND status='EFFECTIVE' LIMIT 1"),
        {'rule':graph.heating_rule_id,'property':graph.property_id})
    provider=FakeKnowledgeProvider();app.dependency_overrides[get_knowledge_provider]=lambda:provider
    try:
        async with granted_graph(client_a_admin,seed_data,graph),synthetic_enforcement(seed_data):
            response=await client_a_admin.post(f'/api/properties/{graph.property_id}/knowledge/query',json={'question':'Heizkosten'})
            assert response.status_code==200,response.text
            assert len(provider.calls)==1
    finally: app.dependency_overrides.pop(get_knowledge_provider,None)


async def test_strict_reservation_rejects_raw_insert_compat_oracle_and_policy_changes(approved_knowledge_graph,seed_data,client_a_admin):
    from sqlalchemy.exc import DBAPIError
    graph=approved_knowledge_graph
    async with granted_graph(client_a_admin,seed_data,graph),synthetic_enforcement(seed_data):
        commands=[
            "INSERT INTO property_knowledge_query_run(organization_id,property_id,initiated_by,budget_window_start,provider,model,reserved_cost_usd,reservation_expires_at) VALUES(:org,:property,:actor,'2001-01-01','fake','raw',999,clock_timestamp()+interval '1 hour')",
            "SELECT * FROM reserve_knowledge_query_compat(:org,:property,'fake','raw',999,'2001-01-01',1000000,clock_timestamp()+interval '1 hour')",
            "SELECT knowledge_budget_available(:org,:property,clock_timestamp(),0,1000000)",
            "UPDATE knowledge_budget_policy SET max_cost_usd=1000000 WHERE organization_id=:org",
            "SELECT * FROM knowledge_budget_policy",
        ]
        for command in commands:
            with pytest.raises(DBAPIError) as caught:
                async with get_tenant_session(graph.organization_id) as db:
                    await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
                    await db.execute(text(command),{'org':graph.organization_id,'property':graph.property_id,'actor':seed_data['user_a_admin_id']})
            assert caught.value.orig.sqlstate=='42501'
        assert not await ledger(graph)


async def test_strict_reserve_owns_window_expiry_and_caps_atomic_admission(approved_knowledge_graph,seed_data,client_a_admin):
    from decimal import Decimal
    from app.core.config import settings
    graph=approved_knowledge_graph
    async with granted_graph(client_a_admin,seed_data,graph),synthetic_enforcement(seed_data):
        async with get_tenant_session(graph.organization_id) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
            params={'org':graph.organization_id,'property':graph.property_id,'amount':settings.declaration_qa_max_cost_usd+Decimal('0.000001')}
            command=text("SELECT * FROM reserve_knowledge_query(:org,:property,'fake','policy',:amount)")
            assert (await db.execute(command,params)).first() is None
            params['amount']=settings.declaration_qa_max_cost_usd
            row=(await db.execute(command,params)).mappings().one()
            assert row['reserved_cost_usd']==params['amount']
            assert (await db.execute(command,params)).first() is None
            checks=(await db.execute(text("SELECT budget_window_start=to_timestamp(floor(extract(epoch FROM clock_timestamp())/(:minutes*60))*(:minutes*60)), "
                "reservation_expires_at BETWEEN clock_timestamp()+make_interval(secs=>:seconds)-interval '5 seconds' AND clock_timestamp()+make_interval(secs=>:seconds) "
                "FROM property_knowledge_query_run WHERE id=:run"),
                {'minutes':settings.declaration_qa_budget_window_minutes,'seconds':settings.declaration_qa_reservation_seconds,'run':row['run_id']})).one()
            assert checks==(True,True)
        assert len(await ledger(graph))==1


async def test_strict_query_missing_budget_policy_fails_before_provider(approved_knowledge_graph,seed_data,client_a_admin):
    graph=approved_knowledge_graph
    provider=FakeKnowledgeProvider();app.dependency_overrides[get_knowledge_provider]=lambda:provider
    try:
        async with granted_graph(client_a_admin,seed_data,graph),synthetic_enforcement(seed_data):
            await mutate(text('TRUNCATE knowledge_budget_policy'),{})
            response=await client_a_admin.post(f'/api/properties/{graph.property_id}/knowledge/query',json={'question':'Heizkosten'})
            assert response.status_code==404,response.text
            assert provider.calls==[] and not await ledger(graph)
    finally: app.dependency_overrides.pop(get_knowledge_provider,None)


async def test_knowledge_migration_preserves_policy_and_roundtrips_empty(approved_knowledge_graph,seed_data,client_a_admin):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0054_knowledge_source_security')
    async with granted_graph(client_a_admin,seed_data,approved_knowledge_graph):
        engine=create_engine(MIGRATOR_SYNC_URL)
        try:
            with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError,match='Retain knowledge budget policy'):
                    with db.begin_nested(): migration.downgrade()
                nested=db.begin_nested()
                try:
                    db.execute(text('TRUNCATE knowledge_budget_policy'))
                    migration.downgrade()
                    assert db.scalar(text("SELECT to_regclass('knowledge_budget_policy')")) is None
                    migration.upgrade()
                    assert db.scalar(text("SELECT prosecdef FROM pg_proc WHERE oid='guard_knowledge_budget_admission()'::regprocedure")) is False
                finally: nested.rollback()
                assert db.scalar(text('SELECT count(*) FROM knowledge_budget_policy'))==1
        finally: engine.dispose()


@pytest.mark.parametrize('table',['organization','"user"','property_knowledge_query_run'])
async def test_busy_knowledge_dependency_refuses_migration_nowait(seed_data,table):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0054_knowledge_source_security')
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


async def test_concurrent_strict_reservations_share_one_org_budget(approved_knowledge_graph,seed_data,client_a_admin):
    import asyncio
    from decimal import Decimal
    from app.core.config import settings
    graph=approved_knowledge_graph
    async with granted_graph(client_a_admin,seed_data,graph),synthetic_enforcement(seed_data):
        barrier=asyncio.Barrier(2)
        async def reserve():
            async with get_tenant_session(graph.organization_id) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
                await barrier.wait()
                return (await db.execute(text("SELECT * FROM reserve_knowledge_query(:org,:property,'fake','parallel',:amount)"),
                    {'org':graph.organization_id,'property':graph.property_id,'amount':settings.declaration_qa_max_cost_usd*Decimal('0.6')})).first()
        results=await asyncio.wait_for(asyncio.gather(reserve(),reserve()),5)
        assert sum(result is not None for result in results)==1
        assert len(await ledger(graph))==1


async def test_budget_policy_requires_current_admin_and_retains_audit(approved_knowledge_graph,seed_data,client_a_admin):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_URL
    graph=approved_knowledge_graph
    async with granted_graph(client_a_admin,seed_data,graph):
        engine=create_async_engine(MIGRATOR_URL)
        try:
            for actor,command,message in [
                (seed_data['user_a_clerk_id'],"UPDATE knowledge_budget_policy SET changed_by=:actor,max_cost_usd=1 WHERE organization_id=:org",'administrator required'),
                (seed_data['user_a_admin_id'],"DELETE FROM knowledge_budget_policy WHERE organization_id=:org",'cannot be deleted'),
            ]:
                with pytest.raises(DBAPIError,match=message):
                    async with engine.begin() as db:
                        await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),{'org':str(graph.organization_id),'actor':str(actor)})
                        await db.execute(text(command),{'org':graph.organization_id,'actor':actor})
            async with engine.begin() as db:
                await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),{'org':str(graph.organization_id),'actor':str(seed_data['user_a_admin_id'])})
                await db.execute(text("UPDATE knowledge_budget_policy SET max_cost_usd=.04,changed_at='2001-01-01',reason='  Reviewed configuration  ' WHERE organization_id=:org"),{'org':graph.organization_id})
                row=(await db.execute(text("SELECT reason,changed_at>clock_timestamp()-interval '5 seconds' FROM knowledge_budget_policy WHERE organization_id=:org"),{'org':graph.organization_id})).one()
                assert row==('Reviewed configuration',True)
                assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE organization_id=:org AND action='knowledge.budget_policy_changed' AND payload_diff->>'reason'='Reviewed configuration'"),{'org':graph.organization_id})==1
        finally: await engine.dispose()
