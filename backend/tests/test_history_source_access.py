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
from tests.contact_source_fixtures import grant_property_contacts,grant_contact

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
    await grant_contact(client_a_admin,seed_data,person)
    async with synthetic_enforcement(seed_data):
        assert (await client_a_admin.get(f'/api/dsgvo/persons/{person}/status')).status_code==409
        assert (await client_a_admin.get(f'/api/dsgvo/persons/{person}/export')).status_code==409
    await clear_disclosure_grants()


async def test_mixed_history_cannot_be_filtered_into_apparent_complete_property(client_a_admin,seed_data,m3_seed,m4_seed):
    from tests.test_financial_input_policy import draft
    from tests.test_m3_settlement import _extras_for_exit
    run=await draft(client_a_admin,m3_seed,'0.00')
    from tests.test_scoped_account_statements import mutate
    from app.models.person import Person
    async with get_tenant_session(seed_data['org_b_id']) as db:
        person=Person(organization_id=seed_data['org_b_id'],first_name='Foreign',last_name='History')
        db.add(person)
        await db.flush()
        foreign_person=person.id
    # A synthetic historical row, not a guessed repair. Remove the whole fixture
    # in finally; no released record is touched.
    history=uuid.uuid4()
    ids={'id':history,'org':seed_data['org_a_id'],'unit':uuid.UUID(m3_seed['u1']),
         'profile':uuid.uuid4(),'foreign_person':foreign_person}
    try:
        # The lease/profile edge is tenant-coherent under the parent constraints. The profile's
        # single-column person FK still permits the mixed historical identity.
        await mutate(text('INSERT INTO tenant_profile(id,organization_id,person_id) VALUES(:profile,:org,:foreign_person)'),ids)
        await mutate(text("INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,valid_to,base_rent,advance_payment,notes) VALUES(:id,:org,:unit,:profile,'2000-01-01','2001-01-01',10,1,'Mixed legacy fixture')"),ids)
        await grant(client_a_admin,seed_data,m3_seed['property'],caps=['read','prepare','approve','export'])
        async with synthetic_enforcement(seed_data),get_tenant_session(seed_data['org_a_id']) as db:
            await actor_session(db,seed_data['user_a_admin_id'])
            assert not await db.scalar(text("SELECT property_financial_disclosure_access(:org,:property,'read')"),{'org':seed_data['org_a_id'],'property':uuid.UUID(m3_seed['property'])})
            assert await db.scalar(text('SELECT count(*) FROM lease_agreement WHERE id=:id'),ids)==0
            assert (await client_a_admin.post(f'/api/settlements/{run}/compute',json=_extras_for_exit(m3_seed))).status_code==404
    finally:
        await mutate(text('DELETE FROM lease_agreement WHERE id=:id'),{'id':history})
        await mutate(text('DELETE FROM tenant_profile WHERE id=:profile'),ids)
        await mutate(text('DELETE FROM person WHERE id=:foreign_person'),ids)
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
        await grant_property_contacts(client_a_admin,seed_data,prop)
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
        await grant_property_contacts(client_a_admin,seed_data,m3_seed['property'])
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
        await grant_property_contacts(client_a_admin,seed_data,m3_seed['property'])
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


async def test_history_migration_preserves_records_and_private_graph(seed_data,m3_seed):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0067_history_source_access')
    rent_migration=importlib.import_module('migrations.versions.0126_lease_rent_change')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        async with synthetic_enforcement(seed_data):
            with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                heads_before=MigrationContext.configure(db).get_current_heads()
                assert heads_before
                fixture=db.begin_nested()
                try:
                    rent_id=uuid.uuid4()
                    db.execute(text("""INSERT INTO lease_rent_change
                        (id,organization_id,lease_agreement_id,valid_from,base_rent,advance_payment)
                        VALUES (:id,:org,:lease,'2098-02-01',123.45,67.89)"""),
                        {'id':rent_id,'org':seed_data['org_a_id'],'lease':uuid.UUID(m3_seed['lease_a'])})
                    rent_rows_sql=text('SELECT to_jsonb(r) FROM lease_rent_change r ORDER BY id')
                    rent_policies_sql=text("""SELECT policyname,permissive,roles,cmd,qual,with_check
                        FROM pg_policies WHERE schemaname='public' AND tablename='lease_rent_change'
                        ORDER BY policyname""")
                    rent_security_sql=text("""SELECT relrowsecurity,relforcerowsecurity,
                        has_table_privilege('app_rw','lease_rent_change','SELECT'),
                        has_table_privilege('app_rw','lease_rent_change','INSERT'),
                        has_table_privilege('app_rw','lease_rent_change','UPDATE'),
                        has_table_privilege('app_rw','lease_rent_change','DELETE')
                        FROM pg_class WHERE oid='lease_rent_change'::regclass""")
                    rent_rows=db.execute(rent_rows_sql).scalars().all()
                    assert any(row['id']==str(rent_id) for row in rent_rows)
                    rent_policies=db.execute(rent_policies_sql).all()
                    assert {row.policyname for row in rent_policies}=={'lease_rent_change_read','lease_rent_change_insert'}
                    rent_security=db.execute(rent_security_sql).one()
                    assert tuple(rent_security)==(True,True,True,True,False,False)
                    with pytest.raises(DBAPIError,match='Retain strict history'):
                        with db.begin_nested(): migration.downgrade()
                    savepoint=db.begin_nested()
                    try:
                        db.execute(text('TRUNCATE property_source_security'))
                        tables=('lease_agreement','ownership_history','audit_event')
                        snapshots={table:db.scalar(text(f"SELECT md5(string_agg(row_to_json(r)::text,'' ORDER BY id)) FROM {table} r")) for table in tables}
                        # 0126 depends on the history predicate owned by 0067.
                        # Rehearse its real downgrade/upgrade only inside this savepoint.
                        rent_migration.downgrade()
                        migration.downgrade()
                        migration.upgrade()
                        rent_migration.upgrade()
                        for table,before in snapshots.items():
                            assert db.scalar(text(f"SELECT md5(string_agg(row_to_json(r)::text,'' ORDER BY id)) FROM {table} r"))==before
                        for signature in ['property_history_graph_valid(uuid,uuid)','property_source_graph_before_history(uuid,uuid,uuid,text,boolean)',
                            'property_complete_graph_actor_access(uuid,uuid,uuid,text,boolean)','privacy_source_context_complete(uuid,uuid,text)','privacy_output_source_complete(uuid,uuid,text)']:
                            assert not db.scalar(text("SELECT has_function_privilege('app_rw',:signature,'EXECUTE')"),{'signature':signature})
                    finally: savepoint.rollback()
                    assert db.execute(rent_rows_sql).scalars().all()==rent_rows
                    assert db.execute(rent_policies_sql).all()==rent_policies
                    assert db.execute(rent_security_sql).one()==rent_security
                    for table,before in snapshots.items():
                        assert db.scalar(text(f"SELECT md5(string_agg(row_to_json(r)::text,'' ORDER BY id)) FROM {table} r"))==before
                finally: fixture.rollback()
                assert MigrationContext.configure(db).get_current_heads()==heads_before
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


@pytest.mark.parametrize('first_gate', ['financial', 'acl'])
async def test_waiting_history_insert_cannot_activate_an_anonymized_person(client_a_admin,seed_data,m3_seed,monkeypatch,first_gate):
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
        assert await db.scalar(text('SELECT anonymized_at FROM person WHERE id=:id'),{'id':person}) is None
    checked=asyncio.Event(); release=asyncio.Event(); acl_held=asyncio.Event(); release_acl=asyncio.Event()
    history=uuid.uuid4()
    pids={name:asyncio.get_running_loop().create_future() for name in ('eraser','writer','acl')}
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
            pids['eraser'].set_result(await db.scalar(text('SELECT pg_backend_pid()')))
            await DsgvoService(db,seed_data['org_a_id'],seed_data['user_a_admin_id']).anonymize(person)
    async def queue_acl_change():
        async with get_tenant_session(seed_data['org_a_id']) as db:
            pids['acl'].set_result(await db.scalar(text('SELECT pg_backend_pid()')))
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {'key':f"scope-acl:{seed_data['org_a_id']}"})
            acl_held.set()
            await release_acl.wait()
    async def activate():
        async with get_tenant_session(seed_data['org_a_id']) as db:
            await actor_session(db,seed_data['user_a_admin_id'])
            await db.execute(text("SET LOCAL application_name='hv-history-privacy-race'"))
            await db.execute(text("SET LOCAL lock_timeout='5s'"))
            pids['writer'].set_result(await db.scalar(text('SELECT pg_backend_pid()')))
            await db.execute(text("INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment,notes) VALUES(:id,:org,:unit,:profile,'2027-01-01',1,1,'Synthetic race')"),
                {'id':history,'org':seed_data['org_a_id'],'unit':unit_id,'profile':profile})
    async def lock_present(db, pid, key, *, granted, mode='ExclusiveLock'):
        return await db.scalar(text("""SELECT EXISTS(SELECT 1 FROM pg_locks
            WHERE locktype='advisory' AND pid=:pid AND granted=:granted AND mode=:mode
            AND classid=((hashtextextended(:key,0)>>32)&4294967295)::oid
            AND objid=(hashtextextended(:key,0)&4294967295)::oid AND objsubid=1)"""),
            {'pid':pid,'key':f"{key}:{seed_data['org_a_id']}",'granted':granted,'mode':mode})
    async def wait_for_lock(db, pid, key, *, mode='ExclusiveLock'):
        # pg_locks is live, unlike pg_stat_activity's transaction-cached snapshot.
        # SQL awaits yield to the actual writer; no sleeps or timing assumptions.
        async def observed():
            while not await lock_present(db,pid,key,granted=False,mode=mode):
                pass
        await asyncio.wait_for(observed(),3)
    tasks=[]
    try:
        await grant_property_contacts(client_a_admin,seed_data,m3_seed['property'])
        await grant(client_a_admin,seed_data,m3_seed['property'],caps=['read','prepare'])
        async with synthetic_enforcement(seed_data):
            try:
                eraser=asyncio.create_task(erase()); tasks.append(eraser)
                await asyncio.wait_for(checked.wait(),3)
                async with get_tenant_session(seed_data['org_a_id']) as observer:
                    eraser_pid=await pids['eraser']
                    assert await lock_present(observer,eraser_pid,'scope-acl',granted=True,mode='ShareLock')
                    assert await lock_present(observer,eraser_pid,'financial-inputs',granted=True)
                    if first_gate=='acl':
                        tasks.append(asyncio.create_task(queue_acl_change()))
                        acl_pid=await asyncio.wait_for(pids['acl'],3)
                        await wait_for_lock(observer,acl_pid,'scope-acl')
                    # Reproduce the old observer schedule before starting writer.
                    await observer.execute(text('SELECT pid FROM pg_stat_activity'))
                    writer=asyncio.create_task(activate()); tasks.append(writer)
                    writer_pid=await asyncio.wait_for(pids['writer'],3)
                    if first_gate=='financial':
                        await wait_for_lock(observer,writer_pid,'financial-inputs')
                        assert await lock_present(observer,writer_pid,'scope-acl',granted=True,mode='ShareLock')
                        assert eraser_pid in await observer.scalar(text('SELECT pg_blocking_pids(:pid)'),{'pid':writer_pid})
                        # The old polling assertion deterministically missed this
                        # real wait after its first, too-early activity snapshot.
                        assert not await observer.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='hv-history-privacy-race' AND wait_event='advisory')"))
                        await observer.execute(text('SELECT pg_stat_clear_snapshot()'))
                        assert await observer.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='hv-history-privacy-race' AND wait_event='advisory')"))
                    else:
                        await wait_for_lock(observer,writer_pid,'scope-acl',mode='ShareLock')
                        assert not await lock_present(observer,writer_pid,'financial-inputs',granted=False)
                        assert not await lock_present(observer,writer_pid,'financial-inputs',granted=True)
                        assert acl_pid in await observer.scalar(text('SELECT pg_blocking_pids(:pid)'),{'pid':writer_pid})
                    release.set()
                    await asyncio.wait_for(eraser,3)
                    if first_gate=='acl':
                        await asyncio.wait_for(acl_held.wait(),3)
                        assert await lock_present(observer,acl_pid,'scope-acl',granted=True)
                        assert await lock_present(observer,writer_pid,'scope-acl',granted=False,mode='ShareLock')
                        release_acl.set()
                    with pytest.raises(DBAPIError) as denied:
                        await asyncio.wait_for(writer,3)
                    assert denied.value.orig.sqlstate=='42501'
                async with get_tenant_session(seed_data['org_a_id']) as db:
                    await actor_session(db,seed_data['user_a_admin_id'])
                    assert await db.scalar(text('SELECT anonymized_at FROM person WHERE id=:id'),{'id':person}) is not None
                from tests.test_scope_payment_reconciliation import read_value
                assert await read_value('SELECT count(*) FROM lease_agreement WHERE id=:id',{'id':history})==0
            finally:
                release.set(); release_acl.set()
                for task in tasks:
                    if not task.done(): task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
    finally:
        await mutate(text('DELETE FROM lease_agreement WHERE id=:id'),{'id':history})
        await clear_disclosure_grants()
