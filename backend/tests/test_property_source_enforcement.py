"""Dormant source enforcement integrates immutable worker actors before rollout."""
import uuid
from contextlib import asynccontextmanager
from datetime import datetime,timezone
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.db import get_tenant_session, get_worker_tenant_session
from tests.test_scoped_account_statements import mutate,client
from tests.test_financial_scope_boundary import scope_seed,worker_tenant  # noqa: F401
from tests.test_m3_settlement import m3_seed  # noqa: F401
from tests.test_special_levy import levy_db, levy_fixture  # noqa: F401
from tests.test_declaration_import_api import isolated_document_store,enable_declaration_imports  # noqa: F401
from tests.test_declaration_job_principal import new_draft,inspect_job
from app.repositories.declaration_import_repo import DeclarationImportRepository,DeclarationPrincipalDenied

pytestmark=pytest.mark.asyncio


async def test_source_enforcement_is_dormant_and_activation_not_available(seed_data):
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        assert await db.scalar(text('SELECT property_source_security_mode(:org)'),{'org':seed_data['org_a_id']})=='PREPARATION'
        assert await db.scalar(text('SELECT property_source_security_mode(:org)'),{'org':seed_data['org_b_id']}) is None
        assert not await db.scalar(text("SELECT has_function_privilege('app_rw','property_source_activation_ready(uuid)','EXECUTE')"))
        for action in ['INSERT','UPDATE','DELETE','TRUNCATE']:
            assert not await db.scalar(text("SELECT has_table_privilege('app_rw','property_source_security',:action)"),{'action':action})
    with pytest.raises(DBAPIError,match='Source integration and coverage are incomplete'):
        await change_mode(seed_data,'ENFORCED')


async def change_mode(seed,mode):
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    engine=create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as db:
            await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
                {'org':str(seed['org_a_id']),'actor':str(seed['user_a_admin_id'])})
            await db.execute(text('INSERT INTO property_source_security(organization_id,mode,changed_by,reason) VALUES(:org,:mode,:actor,:reason) '
                'ON CONFLICT(organization_id) DO UPDATE SET mode=excluded.mode,changed_by=excluded.changed_by,reason=excluded.reason'),
                {'org':seed['org_a_id'],'actor':seed['user_a_admin_id'],'mode':mode,'reason':'Synthetic guarded integration evidence'})
    finally: await engine.dispose()


@asynccontextmanager
async def synthetic_enforcement(seed):
    # This suite is constrained by require_disposable_database_settings. Only the
    # readiness gate is substituted; restore its exact current implementation.
    from tests.test_scope_payment_reconciliation import read_value
    definition = await read_value("SELECT pg_get_functiondef('property_source_activation_ready(uuid)'::regprocedure)",{})
    await mutate(text('CREATE OR REPLACE FUNCTION property_source_activation_ready(org uuid) RETURNS boolean '
                      'LANGUAGE sql SET search_path=pg_catalog,pg_temp AS $$ SELECT true $$'),{})
    try:
        await change_mode(seed,'ENFORCED')
        yield
    finally:
        await mutate(text('TRUNCATE property_source_security'),{})
        await mutate(text(definition),{})


async def test_enforced_queue_checks_creator_and_current_authority_before_replay(scope_seed,migrator_session):
    await migrator_session.commit()
    ids={'org':scope_seed['org_a_id'],'actor':scope_seed['user_a_admin_id'],'clerk':scope_seed['user_a_clerk_id']}
    job=await new_draft(ids)
    async with synthetic_enforcement(scope_seed):
        async with client(ids) as other:
            assert (await other.post(f'/api/declaration-imports/{job}/start',headers={'Idempotency-Key':str(uuid.uuid4())})).status_code==404
        async with client(ids,'actor') as owner:
            key={'Idempotency-Key':str(uuid.uuid4())}
            queued=await owner.post(f'/api/declaration-imports/{job}/start',headers=key)
            assert queued.status_code==200,queued.text
            await change_mode(scope_seed,'PAUSED')
            assert (await owner.post(f'/api/declaration-imports/{job}/start',headers=key)).status_code==404
        async with get_worker_tenant_session(ids['org']) as db:
            assert await DeclarationImportRepository(db,ids['org']).claim_next('source-test',datetime.now(timezone.utc),120) is None
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['actor'])})
            row=(await db.execute(text('SELECT status,error_code FROM declaration_import WHERE id=:id'),{'id':uuid.UUID(job)})).one()
            assert row==('CANCELLED','JOB_PERMISSION_CHANGED')


async def test_pause_keeps_reads_strict_and_cannot_widen_or_erase_state(scope_seed,migrator_session):
    await migrator_session.commit()
    ids={'org':scope_seed['org_a_id'],'actor':scope_seed['user_a_admin_id'],'clerk':scope_seed['user_a_clerk_id']}
    job=await new_draft(ids)
    async with synthetic_enforcement(scope_seed):
        await change_mode(scope_seed,'PAUSED')
        async with get_tenant_session(ids['org']) as db:
            params={'org':ids['org'],'id':uuid.UUID(job)}
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['actor'])})
            assert await db.scalar(text("SELECT property_source_permitted(:org,'declaration_import',:id,'read')"),params)
            assert not await db.scalar(text("SELECT property_source_permitted(:org,'declaration_import',:id,'prepare')"),params)
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
            assert not await db.scalar(text("SELECT property_source_permitted(:org,'declaration_import',:id,'read')"),params)
        with pytest.raises(DBAPIError,match='cannot return to compatibility'):
            await change_mode(scope_seed,'PREPARATION')
        # Even a migration owner cannot delete retained state normally.
        with pytest.raises(DBAPIError,match='cannot be deleted'):
            await delete_state(ids)


async def delete_state(ids):
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    engine=create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as db:
            await db.execute(text("SELECT set_config('app.current_org_id',:org,true)"),{'org':str(ids['org'])})
            await db.execute(text('DELETE FROM property_source_security WHERE organization_id=:org'),ids)
    finally: await engine.dispose()


async def test_claim_uses_receipt_actor_not_privileged_process_actor(scope_seed,migrator_session):
    from tests.test_property_source_admission import setup
    await migrator_session.commit()
    ids={'org':scope_seed['org_a_id'],'actor':scope_seed['user_a_admin_id']}
    async with client(ids,'actor') as api:
        source=await setup(api,scope_seed)
        queued=await api.post(f"/api/declaration-imports/{source['job']}/start",headers={'Idempotency-Key':str(uuid.uuid4())})
        assert queued.status_code==200,queued.text
    async with synthetic_enforcement(scope_seed):
        # Grant the unrelated process user prepare, then revoke only the queue actor.
        async with client(ids,'actor') as api:
            assert (await api.post(f"/api/property-data-domains/{source['domain']}/grants/{source['reader_grant']}/revoke")).status_code==204
            assert (await api.post(f"/api/property-data-domains/{source['domain']}/grants",headers={'Idempotency-Key':str(uuid.uuid4())},
                json={'user_id':str(scope_seed['user_a_clerk_id']),'capabilities':['read','prepare'],'reason':'Different actor'})).status_code==201
            assert (await api.post(f"/api/property-data-domains/{source['domain']}/grants/{source['uploader_grant']}/revoke")).status_code==204
        await mutate(text("CREATE POLICY test_source_gate ON declaration_import AS RESTRICTIVE FOR SELECT USING(property_source_permitted(organization_id,'declaration_import',id,'read'))"),{})
        try:
            async with get_worker_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(scope_seed['user_a_clerk_id'])})
                assert await DeclarationImportRepository(db,ids['org']).claim_next('source-test',datetime.now(timezone.utc),120) is None
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(scope_seed['user_a_clerk_id'])})
                row=(await db.execute(text('SELECT status,error_code FROM declaration_import WHERE id=:job'),source)).one()
                assert row==('CANCELLED','JOB_PERMISSION_CHANGED')
        finally: await mutate(text('DROP POLICY test_source_gate ON declaration_import'),{})


@pytest.mark.parametrize('phase',['load','provider','publish','heartbeat'])
async def test_paused_worker_durably_cancels_every_phase_without_source_visibility(scope_seed,migrator_session,phase):
    from tests.test_declaration_pipeline import _seed_pipeline_import,_RecordingProvider,make_text_pdf
    from app.declarations.pipeline import DeclarationPipeline
    await migrator_session.commit()
    lease,key,store=await _seed_pipeline_import(scope_seed,make_text_pdf('Synthetic declaration'))
    async with synthetic_enforcement(scope_seed):
        class Provider(_RecordingProvider):
            async def inventory(self,*args,**kwargs):
                result=await super().inventory(*args,**kwargs)
                if phase=='provider': await change_mode(scope_seed,'PAUSED')
                return result
        class Pipeline(DeclarationPipeline):
            async def _publish(self,*args):
                if phase=='publish': await change_mode(scope_seed,'PAUSED')
                return await super()._publish(*args)
        provider=Provider(key)
        if phase in {'load','heartbeat'}: await change_mode(scope_seed,'PAUSED')
        # Simulate the future source-row barrier independently of safe status helpers.
        await mutate(text("CREATE POLICY test_source_gate ON declaration_import AS RESTRICTIVE FOR SELECT USING(property_source_permitted(organization_id,'declaration_import',id,'prepare'))"),{})
        try:
            if phase=='heartbeat':
                async with get_worker_tenant_session(lease.organization_id) as db:
                    result=await DeclarationImportRepository(db,lease.organization_id).heartbeat(lease,datetime.now(timezone.utc),120)
                    assert isinstance(result,DeclarationPrincipalDenied)
            else:
                with pytest.raises(DeclarationPrincipalDenied): await Pipeline(provider,store=store).run(lease)
        finally: await mutate(text('DROP POLICY test_source_gate ON declaration_import'),{})
    item,candidates,runs,audits=await inspect_job(lease)
    assert item['status']=='CANCELLED' and item['error_code']=='JOB_PERMISSION_CHANGED'
    assert candidates==0 and audits==1
    assert item['lease_generation']==lease.lease_generation+1
    assert all(row==('FAILED','JOB_PERMISSION_CHANGED') for row in runs)
    if phase in {'load','heartbeat'}: assert provider.calls==[]
    if phase=='provider': assert provider.calls==['inventory'] and item['input_tokens']>0


async def test_sql_queue_command_rejects_unadmitted_source_despite_target_grant(scope_seed,migrator_session):
    from tests.test_property_source_admission_review import legacy_source
    await migrator_session.commit()
    ids={'org':scope_seed['org_a_id'],'actor':scope_seed['user_a_admin_id']}
    async with client(ids,'actor') as api: source=await legacy_source(api,scope_seed)
    async with synthetic_enforcement(scope_seed),get_tenant_session(ids['org']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['actor'])})
        with pytest.raises(DBAPIError,match='Import inaccessible'):
            async with db.begin_nested():
                await db.scalar(text("SELECT record_declaration_queue_request(:org,:job,:key,'START')"),{**source,'org':ids['org'],'key':uuid.uuid4()})
        assert await db.scalar(text('SELECT count(*) FROM declaration_queue_request WHERE import_id=:job'),source)==0


async def test_state_change_waits_for_inflight_source_authorization(scope_seed,migrator_session):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    await migrator_session.commit()
    async with synthetic_enforcement(scope_seed):
        async with get_tenant_session(scope_seed['org_a_id']) as db:
            assert await db.scalar(text('SELECT property_source_security_mode(:org)'),{'org':scope_seed['org_a_id']})=='ENFORCED'
            change=asyncio.create_task(change_mode(scope_seed,'PAUSED'))
            engine=create_async_engine(MIGRATOR_URL)
            try:
                async with engine.connect() as observer:
                    for _ in range(100):
                        await observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                        blocked=await observer.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE 'INSERT INTO property_source_security%')"))
                        if blocked: break
                        await asyncio.sleep(.01)
                    assert blocked and not change.done()
            finally: await engine.dispose()
        await asyncio.wait_for(change,3)
        async with get_tenant_session(scope_seed['org_a_id']) as db:
            assert await db.scalar(text('SELECT property_source_security_mode(:org)'),{'org':scope_seed['org_a_id']})=='PAUSED'


async def test_state_migration_preserves_evidence_and_roundtrips_empty(scope_seed,levy_db,levy_fixture,m3_seed,tmp_path):
    import importlib
    from pathlib import Path
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.migration_rehearsal import historical_source_security_slots,source_security_function_templates
    from tests.test_special_levy import create as create_levy
    from app.core.pdf import render_pdf
    from app.core.storage import LocalFsDocumentStore,set_store
    migration=importlib.import_module('migrations.versions.0053_property_source_security')
    knowledge_migration=importlib.import_module('migrations.versions.0054_knowledge_source_security')
    contact_migration=importlib.import_module('migrations.versions.0069_person_source_access')
    rent_migration=importlib.import_module('migrations.versions.0126_lease_rent_change')
    ids=levy_fixture
    set_store(LocalFsDocumentStore(tmp_path))
    try:
        async with client(ids) as api:
            levy=await create_levy(api,ids)
            assert levy['allocations']
        pdf=render_pdf('<p>Retained source-security rehearsal attachment</p>')
        async with client(ids,'actor') as api:
            person=await api.post('/api/persons',json={'first_name':'Retained','last_name':'Source custody'})
            assert person.status_code==201,person.text
            profile=await api.post('/api/tenant-profiles',json={'person_id':person.json()['id']})
            assert profile.status_code==201,profile.text
            attachment=await api.post('/api/attachments',data={'property_id':str(ids['property']),'title':'Retained during 0069 rehearsal'},
                files={'file':('original.pdf',pdf,'application/pdf')})
            assert attachment.status_code==201,attachment.text
            assert (await api.get('/api/attachments/'+attachment.json()['id']+'/download')).content==pdf
        profile_id=uuid.UUID(profile.json()['id'])
        original_key=Path(str(ids['org']))/attachment.json()['content_sha256'][:2]/(attachment.json()['content_sha256']+'.bin')
        assert (tmp_path/original_key).read_bytes()==pdf
        engine=create_engine(MIGRATOR_SYNC_URL)
        try:
            with engine.connect() as templates_db:
                functions=source_security_function_templates(templates_db)
            async with synthetic_enforcement(scope_seed):
                with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                    heads_before=MigrationContext.configure(db).get_current_heads()
                    assert heads_before
                    tables=db.execute(text("""SELECT schemaname,tablename FROM pg_tables
                        WHERE schemaname NOT IN ('pg_catalog','information_schema')
                        AND schemaname NOT LIKE 'pg_%' ORDER BY schemaname,tablename""")).all()
                    schemas=sorted({schema for schema,_ in tables})
                    quote=db.dialect.identifier_preparer.quote
                    def runtime_rows():
                        return {(schema,table):db.execute(text(
                            f'SELECT to_jsonb(r) FROM {quote(schema)}.{quote(table)} r ORDER BY to_jsonb(r)::text'
                        )).scalars().all() for schema,table in tables}
                    def runtime_security():
                        # Catalog deparsing is search-path dependent; compare in
                        # one fixed context even inside the historical slots.
                        savepoint=db.begin_nested()
                        try:
                            db.execute(text("SELECT set_config('search_path','public',true)"))
                            return {
                                'functions':db.execute(text("""SELECT n.nspname,p.proname,p.oid,
                                    pg_get_functiondef(p.oid),p.proacl,p.proconfig,p.proowner,p.prosecdef
                                    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                                    WHERE n.nspname=ANY(:schemas) ORDER BY p.oid"""),{'schemas':schemas}).all(),
                                'policies':db.execute(text("SELECT * FROM pg_policies WHERE schemaname=ANY(:schemas) ORDER BY schemaname,tablename,policyname"),{'schemas':schemas}).all(),
                                'policy_dependencies':db.execute(text("""SELECT p.oid,p.polrelid,d.refclassid,d.refobjid,d.refobjsubid,d.deptype
                                    FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
                                    JOIN pg_namespace n ON n.oid=c.relnamespace
                                    JOIN pg_depend d ON d.classid='pg_policy'::regclass AND d.objid=p.oid
                                    WHERE n.nspname=ANY(:schemas)
                                    ORDER BY p.oid,d.refclassid,d.refobjid,d.refobjsubid,d.deptype"""),{'schemas':schemas}).all(),
                                'rls':db.execute(text("""SELECT n.nspname,c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relacl
                                    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                                    WHERE n.nspname=ANY(:schemas) AND c.relkind IN ('r','p') ORDER BY n.nspname,c.relname"""),{'schemas':schemas}).all(),
                            }
                        finally: savepoint.rollback()
                    def guard_probes(historical_schema=None):
                        cases=[(ids['actor'],ids['org'],profile_id,'tenant_profile',True),
                            (ids['clerk'],ids['org'],profile_id,'tenant_profile',False),
                            (uuid.uuid4(),ids['org'],profile_id,'tenant_profile',False),
                            (ids['actor'],uuid.uuid4(),profile_id,'tenant_profile',False),
                            (ids['actor'],ids['org'],uuid.uuid4(),'tenant_profile',False),
                            (ids['actor'],ids['org'],profile_id,'unknown',False)]
                        for actor,org,record,kind,expected in cases:
                            probe=db.begin_nested()
                            try:
                                db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
                                    {'org':str(org),'actor':str(actor)})
                                db.execute(text('SET LOCAL ROLE app_rw'))
                                for capability in ('read','prepare','export'):
                                    values={'org':org,'profile':record,'kind':kind,'capability':capability}
                                    assert db.scalar(text('SELECT public.profile_source_access(:org,:kind,:profile,:capability)'),values) is expected
                                    if historical_schema:
                                        assert db.scalar(text(f'SELECT {historical_schema}.profile_source_access(:org,:kind,:profile,:capability)'),values) is expected
                            finally: probe.rollback()
                    rows_before=runtime_rows()
                    security_before=runtime_security()
                    assert any(row['id']==levy['id'] for row in rows_before['public','special_levy'])
                    assert rows_before['public','special_levy_allocation']
                    assert any(row['id']==attachment.json()['id'] for row in rows_before['public','attachment'])
                    assert any(row['id']==person.json()['id'] and row['created_by']==str(ids['actor'])
                        for row in rows_before['public','person'])
                    guard_probes()
                    policy_dependency=db.execute(text("""SELECT p.oid,d.refobjid,pg_get_functiondef(d.refobjid)
                        FROM pg_policy p JOIN pg_depend d ON d.classid='pg_policy'::regclass AND d.objid=p.oid
                        WHERE p.polrelid='public.special_levy_allocation'::regclass AND p.polname='special_levy_allocation_insert'
                        AND d.refobjid='public.profile_source_access(uuid,text,uuid,text)'::regprocedure""")).one()
                    fixture=db.begin_nested()
                    try:
                        rent_id=uuid.uuid4()
                        db.execute(text("""INSERT INTO lease_rent_change
                            (id,organization_id,lease_agreement_id,valid_from,base_rent,advance_payment)
                            VALUES (:id,:org,:lease,'2098-02-01',123.45,67.89)"""),
                            {'id':rent_id,'org':m3_seed['org_a_id'],'lease':uuid.UUID(m3_seed['lease_a'])})
                        rent_rows_sql=text('SELECT to_jsonb(r) FROM public.lease_rent_change r ORDER BY id')
                        rent_policies_sql=text("""SELECT policyname,permissive,roles,cmd,qual,with_check
                            FROM pg_policies WHERE schemaname='public' AND tablename='lease_rent_change' ORDER BY policyname""")
                        rent_security_sql=text("""SELECT relrowsecurity,relforcerowsecurity,
                            has_table_privilege('app_rw','public.lease_rent_change','SELECT'),
                            has_table_privilege('app_rw','public.lease_rent_change','INSERT'),
                            has_table_privilege('app_rw','public.lease_rent_change','UPDATE'),
                            has_table_privilege('app_rw','public.lease_rent_change','DELETE')
                            FROM pg_class WHERE oid='public.lease_rent_change'::regclass""")
                        rent_rows=db.execute(rent_rows_sql).scalars().all()
                        assert any(row['id']==str(rent_id) for row in rent_rows)
                        rent_policies=db.execute(rent_policies_sql).all()
                        assert {row.policyname for row in rent_policies}=={'lease_rent_change_read','lease_rent_change_insert'}
                        rent_security=db.execute(rent_security_sql).one()
                        assert tuple(rent_security)==(True,True,True,True,False,False)
                        fixture_rows=runtime_rows()
                        with pytest.raises(DBAPIError,match='Retain populated source security'):
                            with db.begin_nested(): migration.downgrade()
                        with pytest.raises(DBAPIError,match='Retain contact custody and enforcement'):
                            with db.begin_nested(): contact_migration.downgrade()
                        nested=db.begin_nested()
                        try:
                            historical_tables=(*contact_migration.TABLES,'organization','declaration_import','declaration_queue_request',
                                'property_knowledge_query_run','knowledge_budget_policy','lease_rent_change')
                            with historical_source_security_slots(db,historical_tables,functions) as historical:
                                guard_probes(historical)
                                assert runtime_rows()==fixture_rows
                                assert runtime_security()==security_before
                                assert db.scalar(text(f'SELECT count(*) FROM {historical}.person'))==0
                                assert db.scalar(text(f'SELECT count(*) FROM {historical}.property_source_security'))==0
                                # These are genuine migration functions on empty,
                                # historically owned slots, not current originals.
                                rent_migration.downgrade()
                                assert db.scalar(text(f"SELECT to_regclass('{historical}.lease_rent_change')")) is None
                                contact_migration.downgrade()
                                assert db.scalar(text(f"SELECT to_regprocedure('{historical}.profile_source_access(uuid,text,uuid,text)')")) is None
                                guard_probes()
                                assert runtime_rows()==fixture_rows
                                assert runtime_security()==security_before
                                knowledge_migration.downgrade()
                                migration.downgrade()
                                assert db.scalar(text(f"SELECT to_regclass('{historical}.property_source_security')")) is None
                                assert db.scalar(text("SELECT to_regclass('public.property_source_security')")) is not None
                                guard_probes()
                                assert runtime_rows()==fixture_rows
                                assert runtime_security()==security_before
                                migration.upgrade()
                                assert db.scalar(text(f'SELECT {historical}.property_source_activation_ready(:org)'),{'org':scope_seed['org_a_id']}) is False
                                knowledge_migration.upgrade()
                                contact_migration.upgrade()
                                rent_migration.upgrade()
                                historical_rent_security=text(rent_security_sql.text.replace(
                                    'public.lease_rent_change',f'{historical}.lease_rent_change'))
                                assert db.execute(historical_rent_security).one()==rent_security
                                historical_rent_policies=db.execute(text("""SELECT policyname FROM pg_policies
                                    WHERE schemaname=:schema AND tablename='lease_rent_change'"""),{'schema':historical}).all()
                                assert {row.policyname for row in historical_rent_policies}=={'lease_rent_change_read','lease_rent_change_insert'}
                                guard_probes(historical)
                                assert runtime_rows()==fixture_rows
                                assert runtime_security()==security_before
                        finally: nested.rollback()
                        assert db.execute(rent_rows_sql).scalars().all()==rent_rows
                        assert db.execute(rent_policies_sql).all()==rent_policies
                        assert db.execute(rent_security_sql).one()==rent_security
                        assert runtime_rows()==fixture_rows
                        assert runtime_security()==security_before
                        assert db.execute(text("""SELECT p.oid,d.refobjid,pg_get_functiondef(d.refobjid)
                            FROM pg_policy p JOIN pg_depend d ON d.classid='pg_policy'::regclass AND d.objid=p.oid
                            WHERE p.polrelid='public.special_levy_allocation'::regclass AND p.polname='special_levy_allocation_insert'
                            AND d.refobjid='public.profile_source_access(uuid,text,uuid,text)'::regprocedure""")).one()==policy_dependency
                    finally: fixture.rollback()
                    assert runtime_rows()==rows_before
                    assert runtime_security()==security_before
                    assert MigrationContext.configure(db).get_current_heads()==heads_before
                    assert db.scalar(text('SELECT mode FROM public.property_source_security WHERE organization_id=:org'),{'org':scope_seed['org_a_id']})=='ENFORCED'
                    assert (tmp_path/original_key).read_bytes()==pdf
                    assert db.scalar(text("SELECT to_regnamespace('rehearsal_source_security')")) is None
        finally: engine.dispose()
    finally: set_store(None)


@pytest.mark.parametrize('table',['organization','"user"','declaration_import','declaration_queue_request'])
async def test_busy_security_dependency_refuses_migration_nowait(seed_data,table):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0053_property_source_security')
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


async def test_clerk_state_lookup_and_queue_never_fall_back_to_preparation(scope_seed,migrator_session):
    await migrator_session.commit()
    ids={'org':scope_seed['org_a_id'],'actor':scope_seed['user_a_clerk_id']}
    job=await new_draft(ids)
    async with synthetic_enforcement(scope_seed):
        async with client(ids,'actor') as api:
            response=await api.post(f'/api/declaration-imports/{job}/start',headers={'Idempotency-Key':str(uuid.uuid4())})
            assert response.status_code==200,response.text
        async with get_worker_tenant_session(ids['org']) as db:
            # Worker has no current actor until the immutable receipt is resolved.
            assert await db.scalar(text('SELECT property_source_security_mode(:org)'),ids)=='ENFORCED'
            lease=await DeclarationImportRepository(db,ids['org']).claim_next('clerk-source',datetime.now(timezone.utc),120)
            assert str(lease.import_id)==job
            assert await db.scalar(text("SELECT current_setting('app.current_user_id')"))==str(ids['actor'])
        await change_mode(scope_seed,'PAUSED')
        async with get_worker_tenant_session(ids['org']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['actor'])})
            assert await db.scalar(text('SELECT property_source_security_mode(:org)'),ids)=='PAUSED'
            assert isinstance(await DeclarationImportRepository(db,ids['org']).heartbeat(lease,datetime.now(timezone.utc),120),DeclarationPrincipalDenied)
        item,_,_,audits=await inspect_job(lease,actor=ids['actor'])
        assert item['status']=='CANCELLED' and audits==1


async def test_incorrect_definer_owner_fails_closed_instead_of_hiding_enforcement(scope_seed,migrator_session):
    from sqlalchemy import create_engine
    from tests.conftest import MIGRATOR_SYNC_URL
    await migrator_session.commit()
    async with synthetic_enforcement(scope_seed):
        engine=create_engine(MIGRATOR_SYNC_URL)
        try:
            with engine.begin() as db:
                nested=db.begin_nested()
                try:
                    assert 'row_security=off' in db.scalar(text("SELECT proconfig FROM pg_proc WHERE oid='property_source_security_mode(uuid)'::regprocedure"))
                    # Simulate a misconfigured owner with no BYPASSRLS, retaining
                    # SELECT so this specifically exercises FORCE RLS filtering.
                    db.execute(text('GRANT SELECT ON property_source_security TO app_rw'))
                    db.execute(text('ALTER FUNCTION property_source_security_mode(uuid) OWNER TO app_rw'))
                    db.execute(text('SET LOCAL ROLE app_rw'))
                    db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
                        {'org':str(scope_seed['org_a_id']),'actor':str(scope_seed['user_a_clerk_id'])})
                    with pytest.raises(DBAPIError,match='row-level security'):
                        with db.begin_nested(): db.scalar(text('SELECT property_source_security_mode(:org)'),{'org':scope_seed['org_a_id']})
                finally: nested.rollback()
        finally: engine.dispose()
