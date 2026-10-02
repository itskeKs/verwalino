"""Versioned coverage reports evaluate one real actor across combined sources."""
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_scoped_account_statements import statement_fixture,client,mutate
from tests.test_report_source_access import property_for_clerk,contacts_for_clerk
from tests.test_declaration_import_api import isolated_document_store, enable_declaration_imports  # noqa: F401
from tests.test_invoice_source_custody import store  # noqa: F401
from tests.test_projected_combined_authority import projected
from tests.test_access_coverage import capture
from tests.test_scope_payment_reconciliation import read_value

pytestmark = pytest.mark.asyncio


async def items(api,snapshot):
    response = await api.get(f"/api/access-preparation/snapshots/{snapshot['id']}/items",params={'limit':200})
    assert response.status_code == 200,response.text
    assert response.json()['next_cursor'] is None
    return response.json()['items']


async def test_snapshot_projects_combined_actor_without_preparation_bypass(migrator_session,scope_seed):
    ids = await statement_fixture(migrator_session,scope_seed,'lease')
    async with client(ids,'actor') as admin:
        report = await capture(admin)
        assert report['checker_version'] == 2
        rows = await items(admin,report)
        bank = next(r for r in rows if r['kind']=='bank_account' and r['resource_id']==str(ids['bank']))
        assert bank['references']['strict_audience'] == []
        assert 'COMBINED_READER_REQUIRED' in bank['findings']
        await property_for_clerk(ids,scope_seed)
        await contacts_for_clerk(ids,scope_seed)
        current = await capture(admin,predecessor=report['id'])
        rows = await items(admin,current)
        bank = next(r for r in rows if r['kind']=='bank_account' and r['resource_id']==str(ids['bank']))
        assert bank['references']['strict_audience'] == [{'user_id':str(ids['clerk']),'capabilities':['read']}]
        assert 'COMBINED_READER_REQUIRED' not in bank['findings']
        history = next(r for r in rows if r['kind']=='lease_agreement' and r['resource_id']==str(ids['lease']))
        assert 'PARENT_CONTEXT_REVIEW_REQUIRED' not in history['findings']
        assert history['references']['strict_audience'] == [{'user_id':str(ids['clerk']),'capabilities':['read']}]
        assert await items(admin,report) != rows
        assert current['activation_available'] is False
        assert await read_value('SELECT property_source_security_mode(:org)',ids,actor_key='actor') == 'PREPARATION'


async def test_projected_snapshot_stales_when_independent_financial_pause_changes(migrator_session,scope_seed):
    ids = await statement_fixture(migrator_session,scope_seed,'lease')
    await property_for_clerk(ids,scope_seed)
    await contacts_for_clerk(ids,scope_seed)
    async with client(ids,'actor') as admin:
        report = await capture(admin)
        await read_value("SELECT change_scoped_effect_control(:org,:key,'paused',NULL,'Coverage boundary')",{**ids,'key':uuid.uuid4()},actor_key='actor')
        response = await admin.post(f"/api/access-preparation/snapshots/{report['id']}/review",json={
            'manifest_digest':report['manifest_digest'],'acknowledged':True},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert response.status_code == 409,response.text


async def test_projection_never_turns_a_missing_parent_into_a_reader(migrator_session,scope_seed):
    from sqlalchemy.exc import DBAPIError
    ids = await statement_fixture(migrator_session,scope_seed,'lease')
    expression = "SELECT access_coverage_actor_access(:org,:clerk,'invoice_document',:missing,'read')"
    params = {**ids,'missing':uuid.uuid4()}
    assert not await projected(params,expression.removeprefix('SELECT '))
    with pytest.raises(DBAPIError,match='permission denied'):
        await read_value(expression,params,actor_key='actor')


@pytest.mark.parametrize('mismatch',[False,True])
async def test_coverage_declaration_requires_exact_parent_property(migrator_session,scope_seed,mismatch):
    ids = await statement_fixture(migrator_session,scope_seed,'owner')
    await property_for_clerk(ids,scope_seed)
    await contacts_for_clerk(ids,scope_seed)
    ids.update(plan=uuid.uuid4(),declaration=uuid.uuid4(),declared=ids['other_property'] if mismatch else ids['property'])
    await mutate(text("INSERT INTO economic_plan(id,organization_id,property_id,plan_year,period_start,period_end,status,engine_version) VALUES(:plan,:org,:property,2025,'2025-01-01','2025-12-31','draft','fixture')"),ids)
    await mutate(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,economic_plan_id,origin,reason,reviewed_by) VALUES(:org,:property,:scope,:plan,'REVIEWED','Exact coverage fixture',:actor)"),ids)
    await mutate(text("""INSERT INTO coverage_declaration(id,organization_id,economic_plan_id,property_id,component,period_start,period_end,revision,state,values,source_document_ids,source_revision,reason,attestor_id)
        VALUES(:declaration,:org,:plan,:declared,'economic_plan','2025-01-01','2025-12-31',1,'known','{}','[]',repeat('a',64),'Fixture',:actor)"""),ids)
    expression = "access_coverage_actor_access(:org,:clerk,'coverage_declaration',:declaration,'read')"
    assert await projected(ids,expression) is (not mismatch)
    async with client(ids,'actor') as admin:
        rows = await items(admin,await capture(admin))
        row = next(r for r in rows if r['resource_id']==str(ids['declaration']))
        assert bool(row['references']['strict_audience']) is (not mismatch)
        assert ('PARENT_PROPERTY_MISMATCH' in row['findings']) is mismatch


@pytest.mark.parametrize('mode',['PREPARATION','ENFORCED','PAUSED'])
async def test_meeting_and_classification_projection_matches_live(migrator_session,scope_seed,mode):
    from tests.test_meeting_source_access import meeting_graph
    from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
    ids = await statement_fixture(migrator_session,scope_seed,'lease')
    async with client(ids,'actor') as admin:
        meetings = await meeting_graph(admin,ids['property'])
    await property_for_clerk(ids,scope_seed,('read','prepare','approve','export'))
    async def compare():
        for cap in ('read','prepare','approve','export'):
            for kind,resource in meetings.items():
                params = {**ids,'kind':kind,'resource':resource,'cap':cap}
                expected = await projected(params,'property_meeting_access_at_mode(:org,:kind,:resource,:cap,:target,:mode)',mode)
                actual = await read_value('SELECT property_meeting_access(:org,:kind,:resource,:cap)',params,actor_key='clerk')
                assert expected == actual,(kind,cap,mode)
                assert not await projected(params,'access_coverage_actor_access(:org,:actor,:kind,:resource,:cap)')
        assert await projected(ids,'scope_financial_inputs_classified_at_mode(:org,:scope,:target,:mode)',mode)
    if mode=='PREPARATION': await compare()
    else:
        async with synthetic_enforcement(scope_seed):
            if mode=='PAUSED': await change_mode(scope_seed,mode)
            await compare()


async def test_projection_migration_retains_v2_and_preserves_v1(migrator_session,scope_seed):
    import importlib,hashlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = await statement_fixture(migrator_session,scope_seed,'lease')
    migration = importlib.import_module('migrations.versions.0087_projected_access_coverage')
    async with client(ids,'actor') as admin:
        snapshot = await capture(admin)
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain projected coverage'):
                with db.begin_nested(): migration.downgrade()
            savepoint = db.begin_nested()
            try:
                db.execute(text('TRUNCATE access_coverage_snapshot CASCADE'))
                original = {name:db.execute(text('SELECT oid,proowner,proacl::text,provolatile,prosecdef,proconfig FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'),{'signature':signature}).one()
                    for name,(signature,_) in migration.EXPECTED.items()}
                migration.downgrade()
                for name,(signature,digest) in migration.EXPECTED.items():
                    assert hashlib.sha256(db.scalar(text('SELECT prosrc FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'),{'signature':signature}).encode()).hexdigest()==digest
                # Existing immutable v1 rows survive upgrade verbatim.
                db.execute(text("INSERT INTO access_coverage_snapshot(id,organization_id,generated_by,request_key,request_digest,manifest_digest,manifest,item_count,finding_count) VALUES(:snapshot,:org,:actor,:request,repeat('a',64),repeat('b',64),jsonb_build_object('checker_version',1,'items','[]'::jsonb),0,0)"),
                    {**ids,'snapshot':uuid.UUID(snapshot['id']),'request':uuid.uuid4()})
                before = db.scalar(text('SELECT to_jsonb(s)::text FROM access_coverage_snapshot s'))
                migration.upgrade()
                assert db.scalar(text('SELECT to_jsonb(s)::text FROM access_coverage_snapshot s'))==before
                for name,(signature,_) in migration.EXPECTED.items():
                    assert db.execute(text('SELECT oid,proowner,proacl::text,provolatile,prosecdef,proconfig FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'),{'signature':signature}).one()==original[name]
                    for role in ('app_rw','app_worker','app_review'):
                        assert not db.scalar(text("SELECT has_function_privilege(:role,:signature,'EXECUTE')"),{'role':role,'signature':migration._internal(name,signature)})
                migration.downgrade()
                assert db.scalar(text('SELECT to_jsonb(s)::text FROM access_coverage_snapshot s'))==before
            finally: savepoint.rollback()
    finally: engine.dispose()


@pytest.mark.parametrize('source_kind',['own','other_actor','missing','malformed'])
async def test_financial_coverage_cannot_borrow_source_authority(migrator_session,scope_seed,store,source_kind):
    import json
    from tests.test_invoice_source_custody import upload
    ids = await statement_fixture(migrator_session,scope_seed,'owner')
    await property_for_clerk(ids,scope_seed)
    await contacts_for_clerk(ids,scope_seed)
    if source_kind in ('own','other_actor'):
        async with client(ids,'clerk' if source_kind=='own' else 'actor') as api:
            source = str(await upload(api))
    else: source = str(uuid.uuid4()) if source_kind=='missing' else 'not-an-identity'
    ids.update(plan=uuid.uuid4(),declaration=uuid.uuid4(),sources=json.dumps([source]))
    await mutate(text("INSERT INTO economic_plan(id,organization_id,property_id,plan_year,period_start,period_end,status,engine_version) VALUES(:plan,:org,:property,2025,'2025-01-01','2025-12-31','draft','fixture')"),ids)
    await mutate(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,economic_plan_id,origin,reason,reviewed_by) VALUES(:org,:property,:scope,:plan,'REVIEWED','Exact coverage fixture',:actor)"),ids)
    await mutate(text("""INSERT INTO coverage_declaration(id,organization_id,economic_plan_id,property_id,component,period_start,period_end,revision,state,values,source_document_ids,source_revision,reason,attestor_id)
        VALUES(:declaration,:org,:plan,:property,'economic_plan','2025-01-01','2025-12-31',1,'known','{}',CAST(:sources AS jsonb),repeat('a',64),'Fixture',:actor)"""),ids)
    assert await projected(ids,"access_coverage_actor_access(:org,:clerk,'coverage_declaration',:declaration,'read')") is (source_kind=='own')
    assert not await projected(ids,"access_coverage_actor_access(:org,:actor,'coverage_declaration',:declaration,'read')")
    async with client(ids,'actor') as admin:
        row = next(r for r in await items(admin,await capture(admin)) if r['resource_id']==str(ids['declaration']))
        assert bool(row['references']['strict_audience']) is (source_kind=='own')


async def test_financial_pause_removes_effect_caps_from_new_snapshot_only(migrator_session,scope_seed):
    from tests.test_scoped_dunning import grant as financial_grant
    ids = await statement_fixture(migrator_session,scope_seed,'lease')
    await financial_grant(ids,['read','prepare'])
    await property_for_clerk(ids,scope_seed)
    await contacts_for_clerk(ids,scope_seed)
    async with client(ids,'actor') as admin:
        first = await capture(admin)
        before = await items(admin,first)
        bank = next(r for r in before if r['resource_id']==str(ids['bank']))
        assert bank['references']['strict_audience'] == [{'user_id':str(ids['clerk']),'capabilities':['prepare','read']}]
        await read_value("SELECT change_scoped_effect_control(:org,:key,'paused',NULL,'Retain report reads')",{**ids,'key':uuid.uuid4()},actor_key='actor')
        after = await items(admin,await capture(admin))
        bank = next(r for r in after if r['resource_id']==str(ids['bank']))
        assert bank['references']['strict_audience'] == [{'user_id':str(ids['clerk']),'capabilities':['read']}]
        assert await items(admin,first)==before


async def test_applied_import_projection_uses_approve_even_when_prepare_is_granted(approved_knowledge_graph,seed_data,client_a_admin):
    import json
    from tests.test_knowledge_source_enforcement import granted_graph
    from tests.test_property_source_enforcement import synthetic_enforcement
    graph = approved_knowledge_graph
    ids = {'org':graph.organization_id,'property':graph.property_id,'actor':seed_data['user_a_admin_id'],'clerk':seed_data['user_a_clerk_id']}
    async with granted_graph(client_a_admin,seed_data,graph) as (domain,_):
        response = await client_a_admin.post(f'/api/property-data-domains/{domain}/grants',json={
            'user_id':str(ids['clerk']),'capabilities':['read','prepare'],'reason':'Prepare is not approval'},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert response.status_code == 201,response.text
        context = await read_value('SELECT to_jsonb(r) FROM declaration_import r WHERE applied_property_id=:property LIMIT 1',ids)
        assert context['status']=='APPLIED'
        ids.update(context=json.dumps(context),job=uuid.UUID(context['id']))
        assert await projected(ids,"property_import_runtime_access_at_mode(:org,:job,'prepare',:target,:mode)")
        assert await projected(ids,"property_import_row_access_at_mode(:org,CAST(:context AS jsonb),'read',false,:target,:mode)")
        assert not await projected(ids,"property_import_row_access_at_mode(:org,CAST(:context AS jsonb),'prepare',false,:target,:mode)")
        assert not await projected(ids,"access_coverage_actor_access(:org,:clerk,'declaration_import',:job,'prepare')")
        async with synthetic_enforcement(seed_data):
            assert not await read_value("SELECT property_import_row_access(:org,CAST(:context AS jsonb),'prepare',false)",ids,actor_key='clerk')
        documents = await read_value('SELECT jsonb_agg(to_jsonb(d)) FROM property_document d WHERE property_id=:property',ids)
        for document in documents:
            params = {**ids,'document':uuid.UUID(document['id']),'creator':uuid.UUID(document['uploaded_by'])}
            expression = "property_original_row_access_at_mode(:org,:document,:property,:creator,'read',:target,:mode)"
            assert await projected(params,expression)
            assert not await projected(params,expression,mode=None)
