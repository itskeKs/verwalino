"""Projected audiences retain the complete live predicate dependency graph."""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tests.test_declaration_import_api import isolated_document_store, enable_declaration_imports  # noqa: F401
from tests.test_invoice_source_custody import store  # noqa: F401
from tests.conftest import MIGRATOR_URL
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_scoped_account_statements import statement_fixture, client
from tests.test_report_source_access import contacts_for_clerk, property_for_clerk
from tests.test_property_source_enforcement import synthetic_enforcement, change_mode
from tests.test_scope_payment_reconciliation import read_value

pytestmark = pytest.mark.asyncio


async def projected(ids, expression, mode='ENFORCED', actor='clerk', selected=None, caller='actor'):
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as db:
            await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:caller,true),set_config('app.current_scope_id',:selected,true)"),
                {'org':str(ids['org']) if ids['org'] else '','caller':str(ids[caller]),'selected':str(selected) if selected else ''})
            state = text("SELECT property_source_security_mode(:org),current_setting('app.current_user_id'),current_setting('app.current_scope_id')")
            before = (await db.execute(state,ids)).one()
            result = await db.scalar(text('SELECT '+expression), {**ids,'target':ids[actor],'mode':mode})
            assert (await db.execute(state,ids)).one() == before
            return result
    finally:
        await engine.dispose()


async def test_projected_report_requires_same_actor_property_and_recipient(migrator_session,scope_seed):
    ids = await statement_fixture(migrator_session,scope_seed,'lease')
    expression = "report_scope_source_access_at_mode(:org,:scope,'read',:target,:mode)"
    assert await read_value("SELECT report_scope_source_access(:org,:scope,'read')",ids,actor_key='clerk')
    assert not await projected(ids,expression)
    await property_for_clerk(ids,scope_seed)
    assert not await projected(ids,expression)
    domain,receipt = await contacts_for_clerk(ids,scope_seed)
    assert await projected(ids,expression)
    assert not await projected(ids,expression,actor='actor')
    assert not await projected(ids,expression.replace("'read'","'export'"))
    async with client(ids,'actor') as api:
        assert (await api.post(f'/api/person-data-domains/{domain}/grants/{receipt}/revoke')).status_code == 204
    assert not await projected(ids,expression)


@pytest.mark.parametrize('mode',['PREPARATION','ENFORCED','PAUSED'])
async def test_projected_finance_and_mandate_match_live_modes(migrator_session,scope_seed,mode):
    ids = await statement_fixture(migrator_session,scope_seed,'lease')
    async def compare():
        for cap in ('read','prepare','confirm','approve','send','export'):
            for name,args in [('financial_scope_access',":org,'bank_account',:bank"),('mandate_source_access',':org,:scope')]:
                params = {**ids,'cap':cap}
                actual = await read_value(f'SELECT {name}({args},:cap)',params,actor_key='clerk')
                expected = await projected(params,f'{name}_at_mode({args},:cap,:target,:mode)',mode)
                assert expected == actual,(name,cap,mode)
    if mode == 'PREPARATION':
        await compare()
    else:
        async with synthetic_enforcement(scope_seed):
            if mode == 'PAUSED': await change_mode(scope_seed,mode)
            await compare()


@pytest.mark.parametrize('mode',['PREPARATION','ENFORCED','PAUSED'])
async def test_entire_shared_closure_matches_runtime(migrator_session,scope_seed,store,mode):
    import importlib,json
    from tests.test_invoice_source_custody import upload
    from tests.test_scoped_dunning import grant as financial_grant,tenant_body
    ids = await statement_fixture(migrator_session,scope_seed,'lease')
    await financial_grant(ids,['read','prepare','approve','export'])
    ids['domain'],_ = await contacts_for_clerk(ids,scope_seed)
    await property_for_clerk(ids,scope_seed,('read','prepare','approve','export'))
    async with client(ids) as api:
        ids['document'] = await upload(api)
        notice = await api.post('/api/dunning/tenant',json=tenant_body(ids))
        assert notice.status_code == 201,notice.text
        ids['notice'] = notice.json()['id']
    ids['context'] = json.dumps(await read_value('SELECT to_jsonb(h) FROM lease_agreement h WHERE id=:lease',ids))
    ids['derived'] = json.dumps({'organization_id':str(ids['org']),'id':str(ids['property']),'property_id':str(ids['property'])})
    expressions = {
        'dunning_contact_access':':org,:profile,NULL,:cap',
        'dunning_source_access':':org,:notice,:cap',
        'financial_disclosure_before_contacts':':org,:property,:cap',
        'financial_scope_access':":org,'invoice_document',:document,:cap",
        'financial_sources_before_contacts':':org,:property',
        'has_person_data_capability':':org,:domain,:cap',
        'has_scope_capability':':org,:scope,:cap',
        'history_source_before_contacts':":org,'lease_agreement',CAST(:context AS jsonb),:cap",
        'invoice_source_access':':org,:document,:cap',
        'invoice_source_row_access':'(SELECT d FROM invoice_document d WHERE d.id=:document),:cap',
        'knowledge_query_actor_access':':org,:clerk,:property',
        'mandate_source_access':':org,:scope,:cap',
        'person_source_access':':org,:person,:cap',
        'person_source_row_access':':org,:person,NULL,:cap',
        'profile_reference_access':":org,'tenant_profile',:profile",
        'profile_source_access':":org,'tenant_profile',:profile,:cap",
        'property_derived_row_access':":org,'property_knowledge_query_run',CAST(:derived AS jsonb),:cap",
        'property_financial_contact_access':':org,:property,:cap',
        'property_financial_disclosure_access':':org,:property,:cap',
        'property_financial_source_access':':org,:property',
        'property_history_row_access':":org,'lease_agreement',CAST(:context AS jsonb),:cap",
        'property_import_runtime_access':':org,NULL,:cap',
        'property_master_access':':org,:property,:cap',
        'property_source_permitted':":org,'property',:property,:cap",
        'report_scope_source_access':':org,:scope,:cap',
    }
    migration = importlib.import_module('migrations.versions.0086_combined_authority_projection')
    assert expressions.keys() == migration.EXPECTED.keys()
    runtime_access = {name:await read_value("SELECT has_function_privilege('app_rw',:signature,'EXECUTE')",{'signature':signature})
        for name,(signature,_) in migration.EXPECTED.items()}
    async def compare():
        for name,args in expressions.items():
            for cap in ('read','prepare','approve','export'):
                params = {**ids,'cap':cap}
                extra = '' if name in migration.EXPLICIT_ACTOR else ',:target'
                expected = await projected(params,f'{name}_at_mode({args}{extra},:mode)',mode)
                actual = (await read_value(f'SELECT {name}({args})',params,actor_key='clerk')
                    if runtime_access[name] else await projected(params,f'{name}({args})',mode,caller='clerk'))
                assert expected == actual,(name,cap,mode,expected,actual)
    if mode == 'PREPARATION':
        await compare()
    else:
        async with synthetic_enforcement(scope_seed):
            if mode == 'PAUSED': await change_mode(scope_seed,mode)
            await compare()


async def test_projection_preserves_selected_scope_and_live_financial_pause(migrator_session,scope_seed):
    import uuid
    from tests.test_scoped_dunning import grant as financial_grant
    ids = await statement_fixture(migrator_session,scope_seed,'lease')
    await financial_grant(ids,['read','prepare','export'])
    expression = "financial_scope_access_at_mode(:org,'bank_account',:bank,:cap,:target,:mode)"
    for cap in ('read','prepare','export'):
        assert await projected({**ids,'cap':cap},expression,selected=ids['scope'])
        assert not await projected({**ids,'cap':cap},expression,selected=ids['second_scope'])
    await read_value("SELECT change_scoped_effect_control(:org,:key,'paused',NULL,'Projected authority fixture')",{**ids,'key':uuid.uuid4()},actor_key='actor')
    assert not await projected({**ids,'cap':'prepare'},expression)
    assert await projected({**ids,'cap':'read'},expression)
    assert await projected({**ids,'cap':'export'},expression)


async def test_projection_private_acl_closed_dependencies_and_exact_roundtrip(migrator_session,scope_seed):
    import hashlib,importlib,re
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = await statement_fixture(migrator_session,scope_seed,'lease')
    migration = importlib.import_module('migrations.versions.0086_combined_authority_projection')
    for name,(signature,_) in migration.EXPECTED.items():
        private = migration._internal(name,signature)
        for role in ('app_rw','app_worker','app_review'):
            assert not await read_value("SELECT has_function_privilege(:role,:signature,'EXECUTE')",{'role':role,'signature':private})
        body = await read_value('SELECT prosrc FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)',{'signature':private})
        assert 'app.current_user_id' not in body and 'public.property_source_security_mode(' not in body
        calls = set(re.findall(r'public\.([a-z_]+)\(',body))
        assert not calls & (migration.EXPECTED.keys() | migration.PROPERTY.keys() | {'scope_actor_role'})
        assert calls - {n+'_at_mode' for n in migration.EXPECTED.keys() | migration.PROPERTY.keys()} <= {'property_source_actor_role','financial_scope_id','scoped_effects_enabled'}
    with pytest.raises(DBAPIError,match='permission denied'):
        await read_value("SELECT person_source_access_at_mode(:org,:person,'read',:clerk,'PREPARATION')",ids,actor_key='actor')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            def identities():
                return {name:db.execute(text('SELECT oid,proowner,proacl::text,provolatile,prosecdef,proconfig FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'),{'signature':signature}).one()
                    for name,(signature,_) in migration.EXPECTED.items()}
            before = identities()
            migration.downgrade()
            assert identities() == before
            for signature,digest in migration.EXPECTED.values():
                body = db.scalar(text('SELECT prosrc FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'),{'signature':signature})
                assert hashlib.sha256(body.encode()).hexdigest() == digest
            migration.upgrade()
            assert identities() == before
    finally:
        engine.dispose()


async def test_projection_rechecks_actor_and_never_borrows_callers_invoice_custody(migrator_session,scope_seed,store):
    from tests.test_invoice_source_custody import upload
    from tests.test_scoped_account_statements import mutate
    ids = await statement_fixture(migrator_session,scope_seed,'lease')
    async with client(ids) as api:
        ids['document'] = await upload(api)
    expression = "invoice_source_access_at_mode(:org,:document,'read',:target,:mode)"
    assert await projected(ids,expression)
    assert not await projected(ids,expression,actor='actor')
    for mode in (None,'UNKNOWN'):
        assert not await projected(ids,expression,mode)
    assert not await projected(ids,expression.replace(':org,',"'00000000-0000-0000-0000-000000000000'::uuid,"))
    await mutate(text('UPDATE "user" SET is_active=false WHERE id=:clerk'),ids)
    assert not await projected(ids,expression)


async def test_projection_rejects_missing_tenant_even_in_preparation(migrator_session,scope_seed):
    ids = await statement_fixture(migrator_session,scope_seed,'lease')
    ids['org'] = None
    assert not await projected(ids,"property_import_runtime_access_at_mode(:org,:property,'read',:target,:mode)",mode='PREPARATION')
    assert not await projected(ids,"property_source_permitted_at_mode(:org,'property',:property,'read',:target,:mode)",mode='PREPARATION')


@pytest.mark.parametrize('quarantined',[False,True])
async def test_outer_derived_projection_preserves_exact_source_graph(approved_knowledge_graph,seed_data,client_a_admin,quarantined):
    import json,uuid
    from tests.test_bound_source_access import classify
    from tests.test_property_source_access import grant
    from tests.test_scoped_account_statements import mutate
    source = approved_knowledge_graph
    ids = {'org':source.organization_id,'property':source.property_id,'actor':seed_data['user_a_admin_id'],
        'clerk':seed_data['user_a_clerk_id'],'rule':source.heating_rule_id}
    domain,receipt = await grant(client_a_admin,seed_data,source.property_id)
    try:
        main = await read_value('SELECT property_document_id FROM property_rule_source WHERE property_rule_id=:rule LIMIT 1',ids)
        documents = await read_value('SELECT array_agg(id) FROM property_document WHERE property_id=:property',ids)
        for document in documents:
            await classify(client_a_admin,{'document':document,'property':source.property_id},'QUARANTINED' if quarantined and document==main else 'ADMITTED')
        assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code == 204
        granted = await client_a_admin.post(f'/api/property-data-domains/{domain}/grants',json={
            'user_id':str(ids['clerk']),'capabilities':['read'],'reason':'Read exact source graph'},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert granted.status_code == 201,granted.text
        for kind in ('property_rule','property_rule_source','approved_fact_source'):
            contexts = await read_value(f'SELECT jsonb_agg(to_jsonb(r)) FROM {kind} r WHERE property_id=:property',ids)
            assert contexts
            for context in contexts:
                params = {**ids,'kind':kind,'context':json.dumps(context)}
                expression = 'property_derived_row_access_at_mode(:org,:kind,CAST(:context AS jsonb),\'read\',:target,:mode)'
                assert await projected(params,expression) is (not quarantined)
                assert not await projected(params,expression,actor='actor')
                async with synthetic_enforcement(seed_data):
                    actual = await read_value("SELECT property_derived_row_access(:org,:kind,CAST(:context AS jsonb),'read')",params,actor_key='clerk')
                    assert actual is (not quarantined)
    finally:
        await mutate(text('TRUNCATE property_data_domain CASCADE'),{})
