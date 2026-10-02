"""Meeting records, historical copies and PDFs retain their property audience."""
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.db import get_tenant_session
from tests.test_m8_meetings import m8_seed  # noqa: F401
from tests.test_derived_source_access import actor_session
from tests.test_property_source_access import grant
from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
from tests.test_financial_source_disclosure import clear_disclosure_grants

pytestmark=pytest.mark.asyncio
TABLES=('owners_meeting','agenda_item','resolution','resolution_register_entry')


async def meeting_graph(api,property):
    result=await api.post('/api/meetings',json={'property_id':str(property),'meeting_date':'2026-10-02','meeting_type':'ORDENTLICH','location':'Private source location'})
    assert result.status_code==201,result.text
    meeting=result.json()['id']
    result=await api.post(f'/api/meetings/{meeting}/agenda',json={'meeting_id':meeting,'title':'Private agenda'})
    assert result.status_code==201,result.text
    agenda=result.json()['id']
    result=await api.post('/api/meetings/resolutions',json={'meeting_id':meeting,'agenda_item_id':agenda,'wording':'Private resolution','votes_yes':1,'votes_no':0,'votes_abstain':0,'result':'ANGENOMMEN'})
    assert result.status_code==201,result.text
    resolution=result.json()['id']
    result=await api.post(f'/api/meetings/resolutions/{resolution}/register')
    assert result.status_code==201,result.text
    return dict(zip(TABLES,map(uuid.UUID,[meeting,agenda,resolution,result.json()['id']])))


async def test_meeting_copies_are_hidden_without_current_property_source_read(client_a_admin,m8_seed):
    rows=await meeting_graph(client_a_admin,m8_seed['prop_m8'])
    async with synthetic_enforcement(m8_seed),get_tenant_session(m8_seed['org_a_id']) as db:
        await actor_session(db,m8_seed['user_a_admin_id'])
        for table in TABLES:
            assert await db.scalar(text(f'SELECT count(*) FROM {table} WHERE id=:id'),{'id':rows[table]})==0,table
        assert (await client_a_admin.get(f"/api/meetings/{rows['owners_meeting']}")).status_code==404


@pytest.mark.parametrize('family',['meeting','register'])
async def test_read_grant_does_not_export_meeting_sources(client_a_admin,m8_seed,monkeypatch,family):
    rows=await meeting_graph(client_a_admin,m8_seed['prop_m8'])
    try:
        await grant(client_a_admin,m8_seed,m8_seed['prop_m8'],caps=['read'])
        def forbidden(*args,**kwargs): raise AssertionError('Denied meeting export reached renderer')
        renderer='render_niederschrift_pdf' if family=='meeting' else 'render_register_pdf'
        monkeypatch.setattr('app.api.meetings.'+renderer,forbidden)
        async with synthetic_enforcement(m8_seed):
            assert (await client_a_admin.get(f"/api/meetings/{rows['owners_meeting']}")).status_code==200
            path=f"/api/meetings/{rows['owners_meeting']}/pdf" if family=='meeting' else f"/api/meetings/register/{m8_seed['prop_m8']}/pdf"
            assert (await client_a_admin.get(path)).status_code==404
    finally: await clear_disclosure_grants()


async def test_read_only_property_grant_cannot_change_meeting_copies(client_a_admin,m8_seed):
    rows=await meeting_graph(client_a_admin,m8_seed['prop_m8'])
    try:
        await grant(client_a_admin,m8_seed,m8_seed['prop_m8'],caps=['read'])
        async with synthetic_enforcement(m8_seed),get_tenant_session(m8_seed['org_a_id']) as db:
            await actor_session(db,m8_seed['user_a_admin_id'])
            assert (await db.execute(text("UPDATE owners_meeting SET location='Unauthorized' WHERE id=:id"),{'id':rows['owners_meeting']})).rowcount==0
            assert (await client_a_admin.put(f"/api/meetings/{rows['owners_meeting']}",json={'location':'Unauthorized'})).status_code==404
    finally: await clear_disclosure_grants()


async def test_strict_meeting_lifecycle_audit_pause_and_revocation(client_a_admin,m8_seed):
    from app.repositories.audit_repo import AuditRepository
    try:
        domain,receipt=await grant(client_a_admin,m8_seed,m8_seed['prop_m8'])
        async with synthetic_enforcement(m8_seed):
            rows=await meeting_graph(client_a_admin,m8_seed['prop_m8'])
            meeting=rows['owners_meeting']
            assert (await client_a_admin.post(f'/api/meetings/{meeting}/hold')).status_code==200
            assert (await client_a_admin.put(f'/api/meetings/{meeting}',json={'protocol_text':'Reviewed protocol'})).status_code==200
            assert (await client_a_admin.put(f"/api/meetings/register/entry/{rows['resolution_register_entry']}/challenge",json={'annotation':'Reviewed challenge'})).status_code==200
            async with get_tenant_session(m8_seed['org_a_id']) as db:
                await actor_session(db,m8_seed['user_a_admin_id'])
                for table in TABLES:
                    assert await AuditRepository(db,m8_seed['org_a_id']).list_for_entity(table,rows[table]),table
            await change_mode(m8_seed,'PAUSED')
            for path in [f'/api/meetings/{meeting}/pdf',f"/api/meetings/register/{m8_seed['prop_m8']}/pdf"]:
                response=await client_a_admin.get(path)
                assert response.status_code==200 and response.content.startswith(b'%PDF'),response.text
            assert (await client_a_admin.put(f'/api/meetings/{meeting}',json={'protocol_text':'Paused'})).status_code==404
            assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code==204
            assert (await client_a_admin.get(f'/api/meetings/{meeting}')).status_code==404
            assert (await client_a_admin.get(f'/api/meetings/{meeting}/pdf')).status_code==404
            async with get_tenant_session(m8_seed['org_a_id']) as db:
                await actor_session(db,m8_seed['user_a_admin_id'])
                assert await AuditRepository(db,m8_seed['org_a_id']).list_for_entity('owners_meeting',meeting)==[]
    finally: await clear_disclosure_grants()


async def test_register_effects_need_approve_beyond_prepare(client_a_admin,m8_seed):
    rows=await meeting_graph(client_a_admin,m8_seed['prop_m8'])
    try:
        await grant(client_a_admin,m8_seed,m8_seed['prop_m8'],caps=['read','prepare'])
        async with synthetic_enforcement(m8_seed):
            assert (await client_a_admin.put(f"/api/meetings/{rows['owners_meeting']}",json={'location':'Prepared location'})).status_code==200
            assert (await client_a_admin.post(f"/api/meetings/resolutions/{rows['resolution']}/register")).status_code==404
            assert (await client_a_admin.put(f"/api/meetings/register/entry/{rows['resolution_register_entry']}/challenge",json={'annotation':'Denied'})).status_code==404
            async with get_tenant_session(m8_seed['org_a_id']) as db:
                await actor_session(db,m8_seed['user_a_admin_id'])
                assert (await db.execute(text("UPDATE resolution_register_entry SET challenge_annotation='Denied' WHERE id=:id"),{'id':rows['resolution_register_entry']})).rowcount==0
    finally: await clear_disclosure_grants()


async def test_mixed_legacy_meeting_graph_never_becomes_partial_register_pdf(client_a_admin,client_b_admin,m8_seed,monkeypatch):
    from tests.test_scoped_account_statements import mutate
    rows=await meeting_graph(client_a_admin,m8_seed['prop_m8'])
    foreign=await meeting_graph(client_b_admin,m8_seed['prop_b_m8'])
    try:
        await grant(client_a_admin,m8_seed,m8_seed['prop_m8'])
        # Synthetic pre-existing mixed relationship: preserve it, fail closed;
        # current strict writers must not be allowed to introduce this topology.
        await mutate(text('UPDATE resolution SET agenda_item_id=:agenda WHERE id=:id'),{'agenda':foreign['agenda_item'],'id':rows['resolution']})
        def forbidden(*args,**kwargs): raise AssertionError('Partial meeting graph reached renderer')
        monkeypatch.setattr('app.api.meetings.render_register_pdf',forbidden)
        async with synthetic_enforcement(m8_seed):
            assert (await client_a_admin.get(f"/api/meetings/{rows['owners_meeting']}")).status_code==404
            assert (await client_a_admin.get(f"/api/meetings/register/{m8_seed['prop_m8']}/pdf")).status_code==404
            async with get_tenant_session(m8_seed['org_a_id']) as db:
                await actor_session(db,m8_seed['user_a_admin_id'])
                for table in TABLES:
                    assert await db.scalar(text(f'SELECT count(*) FROM {table} WHERE id=:id'),{'id':rows[table]})==0
    finally:
        await mutate(text('UPDATE resolution SET agenda_item_id=:agenda WHERE id=:id'),{'agenda':rows['agenda_item'],'id':rows['resolution']})
        await clear_disclosure_grants()


async def test_raw_meeting_writes_reject_foreign_agenda_and_reparenting(client_a_admin,client_b_admin,m8_seed):
    rows=await meeting_graph(client_a_admin,m8_seed['prop_m8'])
    foreign=await meeting_graph(client_b_admin,m8_seed['prop_b_m8'])
    try:
        await grant(client_a_admin,m8_seed,m8_seed['prop_m8'])
        async with synthetic_enforcement(m8_seed),get_tenant_session(m8_seed['org_a_id']) as db:
            await actor_session(db,m8_seed['user_a_admin_id'])
            with pytest.raises(DBAPIError) as denied:
                async with db.begin_nested():
                    await db.execute(text('UPDATE resolution SET agenda_item_id=:agenda WHERE id=:id'),{'agenda':foreign['agenda_item'],'id':rows['resolution']})
            assert denied.value.orig.sqlstate=='42501'
            with pytest.raises(DBAPIError,match='Meeting source context is immutable'):
                async with db.begin_nested():
                    await db.execute(text('UPDATE owners_meeting SET property_id=:property WHERE id=:id'),{'property':m8_seed['prop_b_m8'],'id':rows['owners_meeting']})
            assert not await db.scalar(text("SELECT property_meeting_access(:org,'owners_meeting',:id,'read')"),{'org':m8_seed['org_b_id'],'id':foreign['owners_meeting']})
    finally: await clear_disclosure_grants()


async def test_export_expiry_during_register_discovery_refuses_partial_pdf(client_a_admin,m8_seed,monkeypatch):
    from datetime import datetime,timedelta,timezone
    from app.repositories.register_entry_repo import RegisterEntryRepository
    rows=await meeting_graph(client_a_admin,m8_seed['prop_m8'])
    try:
        domain,receipt=await grant(client_a_admin,m8_seed,m8_seed['prop_m8'],caps=['read','export'])
        assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code==204
        response=await client_a_admin.post(f'/api/property-data-domains/{domain}/grants',headers={'Idempotency-Key':str(uuid.uuid4())},
            json={'user_id':str(m8_seed['user_a_admin_id']),'capabilities':['read','export'],'reason':'Short PDF access',
                'expires_at':(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat()})
        assert response.status_code==201,response.text
        original=RegisterEntryRepository.list_for_property
        async def expire(repo,property):
            await repo._session.execute(text('SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM expires_at-clock_timestamp()))+0.02) FROM property_data_grant WHERE id=:id'),{'id':uuid.UUID(response.json()['id'])})
            return await original(repo,property)
        monkeypatch.setattr(RegisterEntryRepository,'list_for_property',expire)
        def forbidden(*args,**kwargs): raise AssertionError('Expired register graph reached renderer')
        monkeypatch.setattr('app.api.meetings.render_register_pdf',forbidden)
        async with synthetic_enforcement(m8_seed):
            assert (await client_a_admin.get(f"/api/meetings/register/{m8_seed['prop_m8']}/pdf")).status_code==404
    finally: await clear_disclosure_grants()


async def test_meeting_migration_preserves_records_and_private_graph(seed_data):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0066_meeting_source_access')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        async with synthetic_enforcement(seed_data):
            with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError,match='Retain strict meeting'):
                    with db.begin_nested(): migration.downgrade()
                savepoint=db.begin_nested()
                try:
                    db.execute(text('TRUNCATE property_source_security'))
                    snapshots={table:db.scalar(text(f"SELECT md5(string_agg(row_to_json(r)::text,'' ORDER BY id)) FROM {table} r")) for table in TABLES+('audit_event',)}
                    migration.downgrade()
                    migration.upgrade()
                    for table,before in snapshots.items():
                        assert db.scalar(text(f"SELECT md5(string_agg(row_to_json(r)::text,'' ORDER BY id)) FROM {table} r"))==before
                    assert not db.scalar(text("SELECT has_function_privilege('app_rw','property_meeting_graph_valid(uuid,uuid)','EXECUTE')"))
                    assert 'row_security=off' in db.scalar(text("SELECT proconfig FROM pg_proc WHERE oid='property_meeting_row_access(uuid,text,jsonb,text)'::regprocedure"))
                finally: savepoint.rollback()
    finally: engine.dispose()


@pytest.mark.parametrize('table',['property_source_security','property','audit_event',*TABLES])
async def test_meeting_migration_refuses_busy_dependencies(seed_data,table):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0066_meeting_source_access')
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
