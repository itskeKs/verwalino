"""Hidden rental/ownership history must not become vacancy or erasure evidence."""
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.db import get_tenant_session
from tests.test_m3_settlement import m3_seed  # noqa: F401
from tests.test_m4_weg import m4_seed  # noqa: F401
from tests.test_derived_source_access import actor_session
from tests.test_property_source_access import grant
from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
from tests.test_financial_source_disclosure import clear_disclosure_grants

pytestmark=pytest.mark.asyncio


async def test_history_rows_need_current_property_source_read(client_a_admin,seed_data,m3_seed,m4_seed):
    async with synthetic_enforcement(seed_data),get_tenant_session(seed_data['org_a_id']) as db:
        await actor_session(db,seed_data['user_a_admin_id'])
        assert await db.scalar(text('SELECT count(*) FROM lease_agreement WHERE id=:id'),{'id':uuid.UUID(m3_seed['lease_a'])})==0
        assert await db.scalar(text('SELECT count(*) FROM ownership_history WHERE unit_id=:unit'),{'unit':uuid.UUID(m4_seed['u1'])})==0
    async with synthetic_enforcement(seed_data):
        assert (await client_a_admin.get('/api/leases',params={'unit_id':m3_seed['u1']})).json()==[]
        assert (await client_a_admin.get('/api/ownership',params={'unit_id':m4_seed['u1']})).json()==[]


async def test_history_read_grant_does_not_authorize_raw_changes(client_a_admin,seed_data,m3_seed):
    try:
        await grant(client_a_admin,seed_data,m3_seed['property'],caps=['read'])
        async with synthetic_enforcement(seed_data),get_tenant_session(seed_data['org_a_id']) as db:
            await actor_session(db,seed_data['user_a_admin_id'])
            assert await db.scalar(text('SELECT count(*) FROM lease_agreement WHERE id=:id'),{'id':uuid.UUID(m3_seed['lease_a'])})==1
            assert (await db.execute(text("UPDATE lease_agreement SET notes='Denied' WHERE id=:id"),{'id':uuid.UUID(m3_seed['lease_a'])})).rowcount==0
    finally: await clear_disclosure_grants()


async def test_privacy_requires_expected_history_even_without_retained_outputs(client_a_admin,seed_data,m3_seed):
    async with get_tenant_session(seed_data['org_a_id']) as db:
        person=await db.scalar(text('SELECT person_id FROM tenant_profile WHERE id=:id'),{'id':uuid.UUID(m3_seed['tp_b'])})
    async with synthetic_enforcement(seed_data):
        assert (await client_a_admin.get(f'/api/dsgvo/persons/{person}/status')).status_code==409
        assert (await client_a_admin.get(f'/api/dsgvo/persons/{person}/export')).status_code==409


async def test_mixed_history_cannot_be_filtered_into_apparent_complete_property(client_a_admin,seed_data,m3_seed,m4_seed):
    from tests.test_financial_input_policy import draft
    from tests.test_m3_settlement import _extras_for_exit
    run=await draft(client_a_admin,m3_seed,'0.00')
    from tests.test_scoped_account_statements import mutate
    from app.models.person import Person
    from app.models.tenant_profile import TenantProfile
    async with get_tenant_session(seed_data['org_b_id']) as db:
        person=Person(organization_id=seed_data['org_b_id'],first_name='Foreign',last_name='History')
        db.add(person)
        await db.flush()
        profile=TenantProfile(organization_id=seed_data['org_b_id'],person_id=person.id,notes='Foreign')
        db.add(profile)
        await db.flush()
        foreign_id=profile.id
    # A synthetic historical row, not a guessed repair. Remove the whole fixture
    # in finally; no released record is touched.
    history=uuid.uuid4()
    ids={'id':history,'org':seed_data['org_a_id'],'unit':uuid.UUID(m3_seed['u1']),'profile':foreign_id}
    try:
        await mutate(text("INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,valid_to,base_rent,advance_payment,notes) VALUES(:id,:org,:unit,:profile,'2000-01-01','2001-01-01',10,1,'Mixed legacy fixture')"),ids)
        await grant(client_a_admin,seed_data,m3_seed['property'],caps=['read','prepare','approve','export'])
        async with synthetic_enforcement(seed_data),get_tenant_session(seed_data['org_a_id']) as db:
            await actor_session(db,seed_data['user_a_admin_id'])
            assert not await db.scalar(text("SELECT property_financial_disclosure_access(:org,:property,'read')"),{'org':seed_data['org_a_id'],'property':uuid.UUID(m3_seed['property'])})
            assert await db.scalar(text('SELECT count(*) FROM lease_agreement WHERE id=:id'),ids)==0
            assert (await client_a_admin.post(f'/api/settlements/{run}/compute',json=_extras_for_exit(m3_seed))).status_code==404
    finally:
        await mutate(text('DELETE FROM lease_agreement WHERE id=:id'),{'id':history})
        await clear_disclosure_grants()


@pytest.mark.parametrize('family',['lease','ownership'])
async def test_strict_relationship_create_read_audit_and_pause(client_a_admin,seed_data,m3_seed,m4_seed,family):
    from tests.test_source_audit_access import add_events,visible
    if family=='lease':
        table='lease_agreement'; endpoint='/api/leases'; prop=m3_seed['property']
        body={'unit_id':m3_seed['u1'],'tenant_profile_id':m3_seed['tp_a'],'valid_from':'1990-01-01','valid_to':'1990-12-31','base_rent':'100','advance_payment':'20'}
    else:
        table='ownership_history'; endpoint='/api/ownership'; prop=m4_seed['property']
        body={'unit_id':m4_seed['u1'],'owner_profile_id':m4_seed['owner_a'],'valid_from':'1990-01-01','valid_to':'1990-12-31','mea_share':'1'}
    try:
        domain,receipt=await grant(client_a_admin,seed_data,prop,caps=['read','prepare'])
        async with synthetic_enforcement(seed_data):
            response=await client_a_admin.post(endpoint,json=body)
            assert response.status_code==201,response.text
            history=uuid.UUID(response.json()['id'])
            assert response.json()['valid_to']=='1990-12-31'
            events=await add_events(seed_data['org_a_id'],seed_data['user_a_admin_id'],{table:history})
            async with get_tenant_session(seed_data['org_a_id']) as db:
                await actor_session(db,seed_data['user_a_admin_id'])
                assert await visible(db,events)==set(events.values())
                assert await db.scalar(text(f'SELECT count(*) FROM {table} WHERE id=:id'),{'id':history})==1
            await change_mode(seed_data,'PAUSED')
            assert (await client_a_admin.get(endpoint,params={'unit_id':body['unit_id']})).status_code==200
            assert (await client_a_admin.post(endpoint,json={**body,'valid_from':'1980-01-01','valid_to':'1980-12-31'})).status_code==404
            assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code==204
            async with get_tenant_session(seed_data['org_a_id']) as db:
                await actor_session(db,seed_data['user_a_admin_id'])
                assert await visible(db,events)==set()
    finally: await clear_disclosure_grants()


async def test_history_prepare_cannot_attach_foreign_profile_or_reparent(client_a_admin,seed_data,m3_seed):
    try:
        await grant(client_a_admin,seed_data,m3_seed['property'])
        async with synthetic_enforcement(seed_data):
            body={'unit_id':m3_seed['u1'],'tenant_profile_id':str(uuid.uuid4()),'valid_from':'1981-01-01','valid_to':'1981-12-31','base_rent':'1','advance_payment':'1'}
            assert (await client_a_admin.post('/api/leases',json=body)).status_code==404
            async with get_tenant_session(seed_data['org_a_id']) as db:
                await actor_session(db,seed_data['user_a_admin_id'])
                with pytest.raises(DBAPIError,match='History source context is immutable'):
                    async with db.begin_nested():
                        await db.execute(text('UPDATE lease_agreement SET tenant_profile_id=:profile WHERE id=:id'),{'profile':uuid.UUID(m3_seed['tp_b']),'id':uuid.UUID(m3_seed['lease_a'])})
                with pytest.raises(DBAPIError) as denied:
                    async with db.begin_nested():
                        await db.execute(text("INSERT INTO lease_agreement(organization_id,unit_id,tenant_profile_id,valid_from,valid_to,base_rent,advance_payment,notes) VALUES(:org,:unit,:profile,'1982-01-01','1983-01-01',1,1,'Denied')"),
                            {'org':seed_data['org_a_id'],'unit':uuid.UUID(m3_seed['u1']),'profile':uuid.uuid4()})
                assert denied.value.orig.sqlstate=='42501'
    finally: await clear_disclosure_grants()


async def test_privacy_history_export_is_distinct_and_expiry_cannot_erase_active_person(client_a_admin,seed_data,m3_seed,monkeypatch):
    from datetime import datetime,timedelta,timezone
    from fastapi import HTTPException
    from app.services.dsgvo_service import DsgvoService
    from app.repositories.dsgvo_repo import DsgvoRepository
    async with get_tenant_session(seed_data['org_a_id']) as db:
        person=await db.scalar(text('SELECT person_id FROM tenant_profile WHERE id=:id'),{'id':uuid.UUID(m3_seed['tp_b'])})
    try:
        domain,receipt=await grant(client_a_admin,seed_data,m3_seed['property'],caps=['read'])
        async with synthetic_enforcement(seed_data):
            status=await client_a_admin.get(f'/api/dsgvo/persons/{person}/status')
            assert status.status_code==200 and status.json()['has_active_relationship'],status.text
            assert (await client_a_admin.get(f'/api/dsgvo/persons/{person}/export')).status_code==409
        assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code==204
        response=await client_a_admin.post(f'/api/property-data-domains/{domain}/grants',headers={'Idempotency-Key':str(uuid.uuid4())},
            json={'user_id':str(seed_data['user_a_admin_id']),'capabilities':['read','export'],'reason':'Short history review',
                'expires_at':(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat()})
        assert response.status_code==201,response.text
        original=DsgvoRepository.has_active_lease
        async def expire(repo,profiles,reference):
            await repo._session.execute(text('SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM expires_at-clock_timestamp()))+0.02) FROM property_data_grant WHERE id=:id'),{'id':uuid.UUID(response.json()['id'])})
            return await original(repo,profiles,reference)
        monkeypatch.setattr(DsgvoRepository,'has_active_lease',expire)
        async with synthetic_enforcement(seed_data),get_tenant_session(seed_data['org_a_id']) as db:
            await actor_session(db,seed_data['user_a_admin_id'])
            with pytest.raises(HTTPException) as denied:
                await DsgvoService(db,seed_data['org_a_id'],seed_data['user_a_admin_id']).anonymize(person)
            assert denied.value.status_code==409
            assert await db.scalar(text('SELECT anonymized_at FROM person WHERE id=:id'),{'id':person}) is None
    finally: await clear_disclosure_grants()


async def test_history_migration_preserves_records_and_private_graph(seed_data):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0067_history_source_access')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        async with synthetic_enforcement(seed_data):
            with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError,match='Retain strict history'):
                    with db.begin_nested(): migration.downgrade()
                savepoint=db.begin_nested()
                try:
                    db.execute(text('TRUNCATE property_source_security'))
                    tables=('lease_agreement','ownership_history','audit_event')
                    snapshots={table:db.scalar(text(f"SELECT md5(string_agg(row_to_json(r)::text,'' ORDER BY id)) FROM {table} r")) for table in tables}
                    migration.downgrade()
                    migration.upgrade()
                    for table,before in snapshots.items():
                        assert db.scalar(text(f"SELECT md5(string_agg(row_to_json(r)::text,'' ORDER BY id)) FROM {table} r"))==before
                    for signature in ['property_history_graph_valid(uuid,uuid)','property_source_graph_before_history(uuid,uuid,uuid,text,boolean)',
                        'property_complete_graph_actor_access(uuid,uuid,uuid,text,boolean)','privacy_source_context_complete(uuid,uuid,text)','privacy_output_source_complete(uuid,uuid,text)']:
                        assert not db.scalar(text("SELECT has_function_privilege('app_rw',:signature,'EXECUTE')"),{'signature':signature})
                finally: savepoint.rollback()
    finally: engine.dispose()


@pytest.mark.parametrize('table',['property_source_security','property','unit','person','tenant_profile','owner_profile','audit_event','lease_agreement','ownership_history'])
async def test_history_migration_refuses_busy_dependencies(seed_data,table):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0067_history_source_access')
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


@pytest.mark.parametrize('table',['tenant_profile','owner_profile'])
async def test_relationship_profile_cannot_reassign_its_person(seed_data,m3_seed,m4_seed,table):
    profile=uuid.UUID(m3_seed['tp_a'] if table=='tenant_profile' else m4_seed['owner_a'])
    replacement=uuid.UUID(m4_seed['person_b'])
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await actor_session(db,seed_data['user_a_admin_id'])
        with pytest.raises(DBAPIError,match='History source context is immutable'):
            async with db.begin_nested():
                await db.execute(text(f'UPDATE {table} SET person_id=:person WHERE id=:id'),{'person':replacement,'id':profile})


async def test_waiting_history_insert_cannot_activate_an_anonymized_person(client_a_admin,seed_data,m3_seed,monkeypatch):
    import asyncio
    from app.models.unit import Unit
    from app.services.dsgvo_service import DsgvoService
    from tests.test_scoped_account_statements import mutate
    person_response=await client_a_admin.post('/api/persons',json={'first_name':'Synthetic','last_name':'History race'})
    assert person_response.status_code==201,person_response.text
    person=uuid.UUID(person_response.json()['id'])
    profile_response=await client_a_admin.post('/api/tenant-profiles',json={'person_id':str(person)})
    assert profile_response.status_code==201,profile_response.text
    profile=uuid.UUID(profile_response.json()['id'])
    async with get_tenant_session(seed_data['org_a_id']) as db:
        unit=Unit(organization_id=seed_data['org_a_id'],property_id=uuid.UUID(m3_seed['property']),number=str(uuid.uuid4()),usage_type='WOHNUNG')
        db.add(unit)
        await db.flush()
        unit_id=unit.id
    checked=asyncio.Event(); release=asyncio.Event(); history=uuid.uuid4()
    original=DsgvoService._gather_bindings
    async def hold_after_final_check(service,person_id,**kwargs):
        result=await original(service,person_id,**kwargs)
        checked.set()
        await release.wait()
        return result
    monkeypatch.setattr(DsgvoService,'_gather_bindings',hold_after_final_check)
    async def erase():
        async with get_tenant_session(seed_data['org_a_id']) as db:
            await actor_session(db,seed_data['user_a_admin_id'])
            await DsgvoService(db,seed_data['org_a_id'],seed_data['user_a_admin_id']).anonymize(person)
    async def activate():
        async with get_tenant_session(seed_data['org_a_id']) as db:
            await actor_session(db,seed_data['user_a_admin_id'])
            await db.execute(text("SET LOCAL application_name='hv-history-privacy-race'"))
            await db.execute(text("SET LOCAL lock_timeout='5s'"))
            assert await db.scalar(text("SELECT property_financial_disclosure_access(:org,:property,'read')"),
                {'org':seed_data['org_a_id'],'property':uuid.UUID(m3_seed['property'])})
            assert await db.scalar(text('SELECT anonymized_at FROM person WHERE id=:id'),{'id':person}) is None
            await db.execute(text("INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment,notes) VALUES(:id,:org,:unit,:profile,'2027-01-01',1,1,'Synthetic race')"),
                {'id':history,'org':seed_data['org_a_id'],'unit':unit_id,'profile':profile})
    eraser=None; writer=None
    try:
        await grant(client_a_admin,seed_data,m3_seed['property'],caps=['read','prepare'])
        async with synthetic_enforcement(seed_data):
            eraser=asyncio.create_task(erase())
            await asyncio.wait_for(checked.wait(),3)
            writer=asyncio.create_task(activate())
            async with get_tenant_session(seed_data['org_a_id']) as db:
                blocked=False
                for _ in range(60):
                    blocked=await db.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='hv-history-privacy-race' AND wait_event='advisory')"))
                    if blocked: break
                    await asyncio.sleep(.02)
                assert blocked,'History writer must share the existing financial-inputs serialization'
            release.set()
            await asyncio.wait_for(eraser,3)
            with pytest.raises(DBAPIError) as denied:
                await asyncio.wait_for(writer,3)
            assert denied.value.orig.sqlstate=='42501'
    finally:
        release.set()
        if eraser is not None: await asyncio.gather(eraser,return_exceptions=True)
        if writer is not None: await asyncio.gather(writer,return_exceptions=True)
        await mutate(text('DELETE FROM lease_agreement WHERE id=:id'),{'id':history})
        await clear_disclosure_grants()
