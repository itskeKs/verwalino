"""Audit payloads must not bypass the current typed source audience."""
import uuid
import pytest
from sqlalchemy import text
from app.core.db import get_tenant_session
from app.models.audit_event import AuditEvent
from app.repositories.audit_repo import AuditRepository
from tests.test_derived_source_access import committed_scope,actor_session,CANONICAL  # noqa: F401
from tests.test_financial_scope_boundary import scope_seed,worker_tenant  # noqa: F401
from tests.test_property_source_context import graph
from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
from tests.test_knowledge_source_enforcement import granted_graph
from tests.test_m3_settlement import m3_seed  # noqa: F401
from tests.test_declaration_import_api import isolated_document_store,enable_declaration_imports  # noqa: F401

pytestmark=pytest.mark.asyncio


async def add_events(org,actor,rows):
    events={}
    async with get_tenant_session(org) as db:
        for kind,resource in rows.items():
            events[kind]=uuid.uuid4()
            await db.execute(text("INSERT INTO audit_event(id,organization_id,actor_user_id,entity_type,entity_id,action,payload_diff) VALUES(:id,:org,:actor,:kind,:resource,'test.source',CAST(:payload AS jsonb))"),
                {'id':events[kind],'org':org,'actor':actor,'kind':kind,'resource':resource,'payload':'{"copied_source":"restricted"}'})
    return events


async def visible(db,events):
    return set(await db.scalars(text('SELECT id FROM audit_event WHERE id=ANY(:ids)'),{'ids':list(events.values())}))


async def test_original_review_and_unknown_audit_payloads_follow_current_source(committed_scope):
    seed=committed_scope
    org,rows=await graph(seed)
    rows['unknown_source_kind']=uuid.uuid4()
    events=await add_events(org,seed['user_a_admin_id'],rows)
    async with synthetic_enforcement(seed),get_tenant_session(org) as db:
        await actor_session(db,seed['user_a_clerk_id'])
        assert await visible(db,events)==set()
        await actor_session(db,seed['user_a_admin_id'])
        assert await visible(db,events)=={event for kind,event in events.items() if kind not in ('property','unit','unknown_source_kind')}
        assert await AuditRepository(db,org).list_for_entity('unknown_source_kind',rows['unknown_source_kind'])==[]
    async with synthetic_enforcement(seed):
        await change_mode(seed,'PAUSED')
        async with get_tenant_session(org) as db:
            await actor_session(db,seed['user_a_admin_id'])
            assert events['declaration_import'] in await visible(db,events)


async def test_copied_canonical_audits_require_whole_graph_and_live_grant(approved_knowledge_graph,seed_data,client_a_admin):
    source=approved_knowledge_graph
    rows={'property':source.property_id}
    async with get_tenant_session(source.organization_id) as db:
        for table in CANONICAL:
            rows[table]=await db.scalar(text(f'SELECT id FROM {table} WHERE property_id=:property LIMIT 1'),{'property':source.property_id})
            assert rows[table]
    events=await add_events(source.organization_id,seed_data['user_a_admin_id'],rows)
    async with granted_graph(client_a_admin,seed_data,source) as (domain,receipt),synthetic_enforcement(seed_data):
        async with get_tenant_session(source.organization_id) as db:
            await actor_session(db,seed_data['user_a_admin_id'])
            assert await visible(db,events)==set(events.values())
        assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code==204
        async with get_tenant_session(source.organization_id) as db:
            await actor_session(db,seed_data['user_a_admin_id'])
            assert await visible(db,events)==set()


async def test_missing_and_foreign_typed_targets_do_not_disclose_audit(seed_data):
    org=seed_data['org_a_id']
    from app.models.property import Property
    async with get_tenant_session(seed_data['org_b_id']) as db:
        foreign=Property(organization_id=seed_data['org_b_id'],name='Foreign audit target',management_type='WEG')
        db.add(foreign)
        await db.flush()
        foreign_id=foreign.id
    events=await add_events(org,seed_data['user_a_admin_id'],{'property':foreign_id,'declaration_import':uuid.uuid4()})
    async with synthetic_enforcement(seed_data),get_tenant_session(org) as db:
        await actor_session(db,seed_data['user_a_admin_id'])
        assert await visible(db,events)==set()


async def test_audit_append_does_not_require_disclosure_of_unknown_source(seed_data):
    org=seed_data['org_a_id']
    event=AuditEvent(id=uuid.uuid4(),actor_user_id=seed_data['user_a_admin_id'],entity_type='future_source',entity_id=uuid.uuid4(),action='test.retained')
    async with synthetic_enforcement(seed_data),get_tenant_session(org) as db:
        await actor_session(db,seed_data['user_a_admin_id'])
        await AuditRepository(db,org).create(event)
        assert not await db.scalar(text('SELECT EXISTS(SELECT 1 FROM audit_event WHERE id=:id)'),{'id':event.id})
    async with get_tenant_session(org) as db:
        assert await db.scalar(text('SELECT action FROM audit_event WHERE id=:id'),{'id':event.id})=='test.retained'


async def test_control_audits_are_admin_metadata_without_source_inheritance(seed_data):
    org=seed_data['org_a_id']
    async with synthetic_enforcement(seed_data),get_tenant_session(org) as db:
        await actor_session(db,seed_data['user_a_admin_id'])
        rows=await AuditRepository(db,org).list_for_entity('property_source_security',org)
        assert rows and rows[-1].action=='property_source.security_changed'
        await actor_session(db,seed_data['user_a_clerk_id'])
        assert await AuditRepository(db,org).list_for_entity('property_source_security',org)==[]


async def test_financial_draft_audit_requires_sources_even_when_parent_is_visible(client_a_admin,seed_data,m3_seed):
    from tests.test_financial_input_policy import draft
    from tests.test_property_source_access import grant
    from tests.test_financial_source_disclosure import clear_disclosure_grants
    run=await draft(client_a_admin,m3_seed,'0.00')
    org=seed_data['org_a_id']
    events=await add_events(org,seed_data['user_a_admin_id'],{'settlement_run':uuid.UUID(run)})
    try:
        async with synthetic_enforcement(seed_data):
            async with get_tenant_session(org) as db:
                await actor_session(db,seed_data['user_a_admin_id'])
                assert await db.scalar(text('SELECT count(*) FROM settlement_run WHERE id=:id'),{'id':uuid.UUID(run)})==1
                assert await visible(db,events)==set()
            await grant(client_a_admin,seed_data,m3_seed['property'],caps=['read'])
            async with get_tenant_session(org) as db:
                await actor_session(db,seed_data['user_a_admin_id'])
                assert await visible(db,events)==set(events.values())
    finally: await clear_disclosure_grants()


async def test_audit_migration_roundtrip_retains_history_and_refuses_strict_rollback(seed_data):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0065_source_audit_access')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        async with synthetic_enforcement(seed_data):
            with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError,match='Retain strict audit'):
                    with db.begin_nested(): migration.downgrade()
                savepoint=db.begin_nested()
                try:
                    db.execute(text('TRUNCATE property_source_security'))
                    before=db.scalar(text("SELECT md5(string_agg(row_to_json(e)::text,'' ORDER BY id)) FROM audit_event e"))
                    migration.downgrade()
                    migration.upgrade()
                    assert db.scalar(text("SELECT md5(string_agg(row_to_json(e)::text,'' ORDER BY id)) FROM audit_event e"))==before
                    assert not db.scalar(text("SELECT prosecdef FROM pg_proc WHERE oid='property_source_audit_access(uuid,text,uuid)'::regprocedure"))
                    assert 'row_security=on' in db.scalar(text("SELECT proconfig FROM pg_proc WHERE oid='property_source_audit_access(uuid,text,uuid)'::regprocedure"))
                finally: savepoint.rollback()
    finally: engine.dispose()
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await actor_session(db,seed_data['user_a_admin_id'])
        assert not await db.scalar(text("SELECT property_source_audit_access(:org,'property',:id)"),{'org':seed_data['org_b_id'],'id':uuid.uuid4()})


@pytest.mark.parametrize('table',['audit_event','property_source_security','declaration_import','property_document','property','economic_plan'])
async def test_audit_migration_refuses_busy_dependencies(seed_data,table):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0065_source_audit_access')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as reader:
            reader.execute(text(f'SELECT 1 FROM {table} LIMIT 1'))
            with pytest.raises(DBAPIError) as denied:
                with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                    db.execute(text("SET LOCAL lock_timeout='1s'"))
                    migration.upgrade()
            assert denied.value.orig.pgcode=='55P03'
    finally: engine.dispose()
