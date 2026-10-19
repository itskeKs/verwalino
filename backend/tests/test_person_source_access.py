"""Contact custody and exact audiences govern PII, profiles and privacy effects."""
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.db import get_tenant_session
from tests.test_derived_source_access import actor_session
from tests.test_property_source_enforcement import synthetic_enforcement, change_mode
from tests.test_m3_settlement import m3_seed  # noqa: F401
from tests.test_m7_dunning import m7_seed  # noqa: F401

pytestmark=pytest.mark.asyncio


async def contact(api):
    response=await api.post('/api/persons',json={'first_name':'Private','last_name':'Contact boundary','email':'private@example.test'})
    assert response.status_code==201,response.text
    return response.json()['id']


async def audience(api,person,actor,caps=('read',)):
    response=await api.post('/api/person-data-domains',json={'person_id':person,'reason_code':'INITIAL_SETUP'},headers={'Idempotency-Key':str(uuid.uuid4())})
    assert response.status_code==201,response.text
    domain=response.json()['id']
    response=await api.post(f'/api/person-data-domains/{domain}/grants',json={'user_id':str(actor),'capabilities':list(caps),'reason_code':'TEAM_ACCESS'},headers={'Idempotency-Key':str(uuid.uuid4())})
    assert response.status_code==201,response.text
    return domain,response.json()['id']


async def test_creator_custody_and_publication_never_restore_revoked_access(client_a_admin,client_a_clerk,seed_data):
    person=await contact(client_a_clerk)
    async with synthetic_enforcement(seed_data):
        assert (await client_a_clerk.get('/api/persons/'+person)).status_code==200
        assert (await client_a_admin.get('/api/persons/'+person)).status_code==404
        domain,grant=await audience(client_a_admin,person,seed_data['user_a_clerk_id'])
        assert (await client_a_clerk.get('/api/persons/'+person)).status_code==200
        assert (await client_a_admin.post(f'/api/person-data-domains/{domain}/grants/{grant}/revoke')).status_code==204
        assert (await client_a_clerk.get('/api/persons/'+person)).status_code==404


@pytest.mark.parametrize('route',['tenant-profiles','owner-profiles','vendors'])
async def test_profiles_follow_person_read_and_require_prepare_to_create(client_a_admin,client_a_clerk,seed_data,route):
    person=await contact(client_a_admin)
    profile=await client_a_admin.post('/api/'+route,json={'person_id':person,'notes':'Private profile details'})
    assert profile.status_code==201
    async with synthetic_enforcement(seed_data):
        assert (await client_a_clerk.get('/api/'+route,params={'person_id':person})).json()==[]
        await audience(client_a_admin,person,seed_data['user_a_clerk_id'])
        visible=await client_a_clerk.get('/api/'+route,params={'person_id':person})
        assert len(visible.json())==1
        assert (await client_a_clerk.post('/api/'+route,json={'person_id':person,'notes':'Unauthorized write'})).status_code==404


async def test_read_grant_cannot_edit_contact_and_pause_retains_only_reads(client_a_admin,client_a_clerk,seed_data):
    person=await contact(client_a_admin)
    async with synthetic_enforcement(seed_data):
        await audience(client_a_admin,person,seed_data['user_a_clerk_id'])
        assert (await client_a_clerk.put('/api/persons/'+person,json={'phone':'changed'})).status_code==404
        await change_mode(seed_data,'PAUSED')
        assert (await client_a_clerk.get('/api/persons/'+person)).status_code==200
        assert (await client_a_clerk.post('/api/persons',json={'last_name':'Paused creation'})).status_code==409


async def test_contact_preparer_cannot_forge_privacy_state(client_a_admin,seed_data):
    person=await contact(client_a_admin)
    async with synthetic_enforcement(seed_data):
        await audience(client_a_admin,person,seed_data['user_a_clerk_id'],('read','prepare'))
        async with get_tenant_session(seed_data['org_a_id']) as db:
            await actor_session(db,seed_data['user_a_clerk_id'])
            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(text('UPDATE person SET anonymized_at=now() WHERE id=:id'),{'id':uuid.UUID(person)})


async def test_hidden_expected_contacts_block_financial_disclosure_without_hiding_property(client_a_admin,seed_data,m3_seed):
    from tests.test_property_source_access import grant
    from tests.test_financial_source_disclosure import clear_disclosure_grants
    try:
        async with synthetic_enforcement(seed_data):
            await grant(client_a_admin,seed_data,m3_seed['property'],caps=['read','prepare','export'])
            async with get_tenant_session(seed_data['org_a_id']) as db:
                await actor_session(db,seed_data['user_a_admin_id'])
                assert await db.scalar(text('SELECT count(*) FROM property WHERE id=:id'),{'id':m3_seed['property']})==1
                assert not await db.scalar(text("SELECT property_financial_disclosure_access(:org,:property,'read')"),
                    {'org':seed_data['org_a_id'],'property':m3_seed['property']})
    finally: await clear_disclosure_grants()


@pytest.mark.parametrize('column',['deletion_requested_at','processing_restricted','anonymized_at'])
async def test_raw_admin_cannot_bypass_privacy_command(client_a_admin,seed_data,column):
    person=await contact(client_a_admin)
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await actor_session(db,seed_data['user_a_admin_id'])
        value='true' if column=='processing_restricted' else 'now()'
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text(f'UPDATE person SET {column}={value} WHERE id=:id'),{'id':uuid.UUID(person)})


async def test_direct_privacy_command_requires_live_admin_and_current_person_prepare(client_a_admin,seed_data):
    person=await contact(client_a_admin)
    async with synthetic_enforcement(seed_data):
        domain,grant=await audience(client_a_admin,person,seed_data['user_a_admin_id'])
        async with get_tenant_session(seed_data['org_a_id']) as db:
            await actor_session(db,seed_data['user_a_admin_id'])
            result=await db.scalar(text("SELECT apply_person_privacy(:org,:id,'ANONYMIZE')"),{'org':seed_data['org_a_id'],'id':uuid.UUID(person)})
            assert result=='FORBIDDEN'
            await actor_session(db,seed_data['user_a_clerk_id'])
            assert await db.scalar(text("SELECT apply_person_privacy(:org,:id,'ANONYMIZE')"),{'org':seed_data['org_a_id'],'id':uuid.UUID(person)})=='FORBIDDEN'
        assert (await client_a_admin.post(f'/api/person-data-domains/{domain}/grants/{grant}/revoke')).status_code==204
        assert (await client_a_admin.post(f'/api/person-data-domains/{domain}/grants',json={'user_id':str(seed_data['user_a_admin_id']),'capabilities':['read','prepare'],'reason_code':'REPLACEMENT'},headers={'Idempotency-Key':str(uuid.uuid4())})).status_code==201
        response=await client_a_admin.post(f'/api/dsgvo/persons/{person}/anonymize')
        assert response.status_code==200,response.text
        assert response.json()['applied']=='ANONYMIZED'
        assert (await client_a_admin.put('/api/persons/'+person,json={'last_name':'Restored PII'})).status_code==404


async def test_contact_financial_graph_needs_read_and_export_for_every_expected_person(client_a_admin,seed_data,m3_seed):
    from tests.test_property_source_access import grant
    from tests.contact_source_fixtures import grant_property_contacts
    from tests.test_financial_source_disclosure import clear_disclosure_grants
    try:
        await grant(client_a_admin,seed_data,m3_seed['property'],caps=['read','export'])
        people=await grant_property_contacts(client_a_admin,seed_data,m3_seed['property'],caps=('read',))
        assert len(people)>=6
        async with synthetic_enforcement(seed_data),get_tenant_session(seed_data['org_a_id']) as db:
            await actor_session(db,seed_data['user_a_admin_id'])
            args={'org':seed_data['org_a_id'],'property':m3_seed['property']}
            assert await db.scalar(text("SELECT property_financial_disclosure_access(:org,:property,'read')"),args)
            assert not await db.scalar(text("SELECT property_financial_disclosure_access(:org,:property,'export')"),args)
    finally: await clear_disclosure_grants()


@pytest.mark.parametrize('expire',[False,True])
async def test_direct_privacy_command_holds_acl_and_rechecks_expiry_after_financial_wait(client_a_admin,seed_data,expire):
    import asyncio
    from datetime import datetime,timedelta,timezone
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    person=await contact(client_a_admin)
    domain,grant=await audience(client_a_admin,person,seed_data['user_a_admin_id'],('read','prepare'))
    if expire:
        assert (await client_a_admin.post(f'/api/person-data-domains/{domain}/grants/{grant}/revoke')).status_code==204
        result=await client_a_admin.post(f'/api/person-data-domains/{domain}/grants',json={
            'user_id':str(seed_data['user_a_admin_id']),'capabilities':['read','prepare'],'reason_code':'TEMPORARY_ACCESS',
            'expires_at':(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat()},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert result.status_code==201,result.text
        grant=result.json()['id']
    org=seed_data['org_a_id']; actor=seed_data['user_a_admin_id']; tasks=[]
    async def apply():
        async with get_tenant_session(org) as db:
            await actor_session(db,actor)
            await db.execute(text("SET LOCAL application_name='hv-contact-command-race'"))
            return await db.scalar(text("SELECT apply_person_privacy(:org,:person,'ANONYMIZE')"),{'org':org,'person':uuid.UUID(person)})
    async def revoke():
        async with get_tenant_session(org) as db:
            await actor_session(db,actor)
            await db.execute(text("SET LOCAL application_name='hv-contact-revoke-race'"))
            await db.execute(text('UPDATE person_data_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE id=:grant'),{'actor':actor,'grant':uuid.UUID(grant)})
    async def wait_blocked(db,name):
        for _ in range(100):
            await db.execute(text('SELECT pg_stat_clear_snapshot()'))
            if tasks and tasks[0].done():
                pytest.fail('Command did not wait: '+str(tasks[0].result()))
            if await db.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name=:name AND wait_event='advisory')"),{'name':name}): return
            await asyncio.sleep(.02)
        pytest.fail('Expected advisory lock wait: '+name)
    async with synthetic_enforcement(seed_data):
        try:
            async with get_tenant_session(org) as blocker:
                await actor_session(blocker,actor)
                await blocker.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('financial-inputs:'||CAST(:org AS text),0))"),{'org':str(org)})
                command=asyncio.create_task(apply());tasks.append(command)
                await wait_blocked(blocker,'hv-contact-command-race')
                if expire:
                    await blocker.execute(text('SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM expires_at-clock_timestamp()))+0.02) FROM person_data_grant WHERE id=:grant'),{'grant':uuid.UUID(grant)})
                else:
                    revocation=asyncio.create_task(revoke());tasks.append(revocation)
                    await wait_blocked(blocker,'hv-contact-revoke-race')
            outcome=await asyncio.wait_for(command,5)
            if expire: assert outcome in {'NOT_FOUND','INCOMPLETE','FORBIDDEN'}
            else:
                assert outcome=='ANONYMIZED'
                await asyncio.wait_for(revocation,5)
            engine=create_async_engine(MIGRATOR_URL)
            try:
                async with engine.connect() as db:
                    stamp=await db.scalar(text('SELECT anonymized_at FROM person WHERE id=:id'),{'id':uuid.UUID(person)})
                    assert (stamp is None)==expire
            finally: await engine.dispose()
        finally:
            for task in tasks:
                if not task.done(): task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)


async def test_person_migration_keeps_custody_and_roundtrips_legacy_without_backfill(client_a_admin,seed_data):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0069_person_source_access')
    person=await contact(client_a_admin)
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            assert db.scalar(text('SELECT created_by FROM person WHERE id=:id'),{'id':uuid.UUID(person)})==seed_data['user_a_admin_id']
            with pytest.raises(DBAPIError,match='Retain contact custody'):
                with db.begin_nested(): migration.downgrade()
            savepoint=db.begin_nested()
            try:
                from tests.migration_rehearsal import prepare_historical_revision
                prepare_historical_revision(db, '0069')
                legacy=uuid.uuid4()
                db.execute(text("INSERT INTO person(id,organization_id,first_name,last_name) VALUES(:id,:org,'Legacy','No inferred custody')"),{'id':legacy,'org':seed_data['org_a_id']})
                before=db.scalar(text("SELECT md5(first_name||last_name) FROM person WHERE id=:id"),{'id':legacy})
                migration.downgrade();migration.upgrade()
                assert db.scalar(text('SELECT created_by FROM person WHERE id=:id'),{'id':legacy}) is None
                assert db.scalar(text("SELECT md5(first_name||last_name) FROM person WHERE id=:id"),{'id':legacy})==before
                assert not db.scalar(text("SELECT has_function_privilege('app_rw','privacy_retention_facts(uuid,uuid)','EXECUTE')"))
                assert not db.scalar(text('SELECT property_source_activation_ready(:org)'),{'org':seed_data['org_a_id']})
            finally: savepoint.rollback()
    finally: engine.dispose()


@pytest.mark.parametrize('table',['person','tenant_profile','owner_profile','vendor','invoice_document','person_data_domain','membership','property_source_security'])
async def test_contact_source_migration_refuses_busy_dependencies(seed_data,table):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0069_person_source_access')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as reader:
            reader.execute(text(f'SELECT 1 FROM {table} LIMIT 1'))
            with pytest.raises(DBAPIError) as denied:
                with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                    db.execute(text("SET LOCAL lock_timeout='1s'"));migration.upgrade()
            assert denied.value.orig.pgcode=='55P03'
    finally: engine.dispose()


async def test_legacy_dunning_contact_grant_does_not_establish_source_provenance(client_a_admin,seed_data,m7_seed):
    from tests.test_financial_source_disclosure import clear_disclosure_grants
    result=await client_a_admin.post('/api/dunning/tenant',json={
        'tenant_profile_id':str(m7_seed['tenant_m7']),'lease_agreement_id':str(m7_seed['lease_m7']),
        'period_start':'2025-01-01','period_end':'2025-03-31','reference_date':'2025-04-15'})
    assert result.status_code==201,result.text
    notice=result.json()['id']
    assert (await client_a_admin.post('/api/dunning/'+notice+'/issue')).status_code==200
    try:
        async with synthetic_enforcement(seed_data):
            assert (await client_a_admin.get('/api/dunning/'+notice)).status_code==404
            domain,grant=await audience(client_a_admin,str(m7_seed['person_m7']),seed_data['user_a_admin_id'])
            # A contact audience alone cannot classify historical property sources.
            assert (await client_a_admin.get('/api/dunning/'+notice)).status_code==404
            assert (await client_a_admin.get('/api/dunning/'+notice+'/pdf')).status_code==404
            assert (await client_a_admin.post(f'/api/person-data-domains/{domain}/grants/{grant}/revoke')).status_code==204
            assert (await client_a_admin.get('/api/dunning/'+notice)).status_code==404
    finally: await clear_disclosure_grants()


@pytest.mark.parametrize('anonymized',[False,True])
async def test_raw_cost_cannot_link_hidden_or_anonymized_vendor(client_a_admin,seed_data,m3_seed,anonymized):
    person=await contact(client_a_admin)
    response=await client_a_admin.post('/api/vendors',json={'person_id':person})
    assert response.status_code==201,response.text
    vendor=uuid.UUID(response.json()['id'])
    if anonymized:
        assert (await client_a_admin.post(f'/api/dsgvo/persons/{person}/anonymize')).status_code==200
    async with synthetic_enforcement(seed_data),get_tenant_session(seed_data['org_a_id']) as db:
        await actor_session(db,seed_data['user_a_admin_id'] if anonymized else seed_data['user_a_clerk_id'])
        category=await db.scalar(text("SELECT id FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1"))
        with pytest.raises(DBAPIError,match='Readable active contact required'):
            async with db.begin_nested():
                await db.execute(text("""INSERT INTO cost_entry(organization_id,property_id,cost_category_id,cost_category_plan_version,
                    vendor_id,invoice_date,service_period_from,service_period_to,gross_amount,tenant_apportionable,status,source)
                    VALUES(:org,:property,:category,1,:vendor,'2025-01-01','2025-01-01','2025-12-31',10,'NICHT_UMLAGEFAEHIG','needs_review','MANUAL')"""),
                    {'org':seed_data['org_a_id'],'property':m3_seed['property'],'category':category,'vendor':vendor})


async def test_dunning_anonymized_recipient_remains_readable_but_cannot_prepare(client_a_admin,seed_data):
    person=await contact(client_a_admin)
    response=await client_a_admin.post('/api/tenant-profiles',json={'person_id':person})
    assert response.status_code==201,response.text
    profile=uuid.UUID(response.json()['id'])
    assert (await client_a_admin.post(f'/api/dsgvo/persons/{person}/anonymize')).status_code==200
    async with synthetic_enforcement(seed_data),get_tenant_session(seed_data['org_a_id']) as db:
        await actor_session(db,seed_data['user_a_admin_id'])
        args={'org':seed_data['org_a_id'],'profile':profile}
        assert await db.scalar(text("SELECT dunning_contact_access(:org,:profile,NULL,'read')"),args)
        assert not await db.scalar(text("SELECT dunning_contact_access(:org,:profile,NULL,'prepare')"),args)


async def test_waiting_raw_cost_rechecks_contact_after_privacy_commit(client_a_admin,seed_data,m3_seed):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    person=await contact(client_a_admin)
    response=await client_a_admin.post('/api/vendors',json={'person_id':person})
    assert response.status_code==201
    vendor=uuid.UUID(response.json()['id']); org=seed_data['org_a_id']; actor=seed_data['user_a_admin_id']
    async def insert():
        async with get_tenant_session(org) as db:
            await actor_session(db,actor)
            await db.execute(text("SET LOCAL application_name='hv-contact-cost-race'"))
            category=await db.scalar(text("SELECT id FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1"))
            await db.execute(text("""INSERT INTO cost_entry(organization_id,property_id,cost_category_id,cost_category_plan_version,
                vendor_id,invoice_date,service_period_from,service_period_to,gross_amount,tenant_apportionable,status,source)
                VALUES(:org,:property,:category,1,:vendor,'2025-01-01','2025-01-01','2025-12-31',10,'NICHT_UMLAGEFAEHIG','needs_review','MANUAL')"""),
                {'org':org,'property':m3_seed['property'],'category':category,'vendor':vendor})
    engine=create_async_engine(MIGRATOR_URL); task=None
    try:
        async with synthetic_enforcement(seed_data):
            try:
                async with get_tenant_session(org) as eraser:
                    await actor_session(eraser,actor)
                    assert await eraser.scalar(text("SELECT apply_person_privacy(:org,:person,'ANONYMIZE')"),{'org':org,'person':uuid.UUID(person)})=='ANONYMIZED'
                    task=asyncio.create_task(insert())
                    async with engine.connect() as observer:
                        for _ in range(100):
                            await observer.execute(text('SELECT pg_stat_clear_snapshot()'))
                            if await observer.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='hv-contact-cost-race' AND wait_event_type='Lock')")): break
                            if task.done(): await task
                            await asyncio.sleep(.03)
                        else: pytest.fail('Cost writer did not wait on privacy transaction')
                with pytest.raises(DBAPIError,match='Readable active contact required'):
                    await asyncio.wait_for(task,5)
            finally:
                if task and not task.done(): task.cancel()
                if task: await asyncio.gather(task,return_exceptions=True)
    finally: await engine.dispose()


async def test_person_response_does_not_release_loaded_pii_after_grant_expiry(client_a_admin,client_a_clerk,seed_data,monkeypatch):
    import asyncio
    from datetime import datetime,timedelta,timezone
    from app.api import persons
    person=await contact(client_a_admin)
    domain,grant=await audience(client_a_admin,person,seed_data['user_a_clerk_id'])
    assert (await client_a_admin.post(f'/api/person-data-domains/{domain}/grants/{grant}/revoke')).status_code==204
    expiry=datetime.now(timezone.utc)+timedelta(seconds=2)
    response=await client_a_admin.post(f'/api/person-data-domains/{domain}/grants',json={
        'user_id':str(seed_data['user_a_clerk_id']),'capabilities':['read'],'reason_code':'TEMPORARY_ACCESS',
        'expires_at':expiry.isoformat()},headers={'Idempotency-Key':str(uuid.uuid4())})
    assert response.status_code==201,response.text
    original=persons._outputs
    async def wait_after_loading(db,people):
        assert len(people)==1 and str(people[0].id)==person
        # Grant expiry uses the database clock, not the host/Docker wall clock.
        await asyncio.wait_for(db.execute(text(
            'SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM CAST(:expiry AS timestamptz)-clock_timestamp()))+0.05)'
        ),{'expiry':expiry}),5)
        assert await db.scalar(text('SELECT clock_timestamp()>=CAST(:expiry AS timestamptz)'),{'expiry':expiry})
        return await original(db,people)
    monkeypatch.setattr(persons,'_outputs',wait_after_loading)
    async with synthetic_enforcement(seed_data):
        response=await client_a_clerk.get('/api/persons/'+person)
        assert response.status_code==409,response.text
        assert 'private@example.test' not in response.text
