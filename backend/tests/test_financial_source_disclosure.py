"""Copied financial results retain current source read/export authority."""
import uuid
import pytest
from sqlalchemy import text
from app.core.db import get_tenant_session
from tests.test_m4_weg import m4_seed, _create_and_finalize_plan  # noqa: F401
from tests.test_m3_settlement import m3_seed, _extras_for_exit  # noqa: F401
from tests.test_financial_input_policy import draft, attest
from tests.test_property_source_access import grant
from tests.test_property_source_enforcement import synthetic_enforcement, change_mode
from tests.test_scoped_account_statements import mutate
from tests.contact_source_fixtures import grant_property_contacts

pytestmark = pytest.mark.asyncio


async def bind_output(seed, property_id, subject, *, kind='economic_plan', scope_type='WEG', linked_plan=None, caps=None):
    """Install reviewed financial evidence independently of source grants."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    ids = dict(org=seed['org_a_id'], actor=seed['user_a_admin_id'], property=uuid.UUID(property_id),
               subject=uuid.UUID(subject), sphere=uuid.uuid4(), scope_type=scope_type,
               caps=caps or ['read','export'])
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as db:
            await db.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:sphere,:org,:property,:scope_type,'Reviewed export scope','2020-01-01',true,'REVIEWED',:actor)"), ids)
            if linked_plan:
                await db.execute(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,economic_plan_id,origin,reason,reviewed_by) VALUES (:org,:property,:sphere,:subject,'REVIEWED','Reviewed PDF fixture',:actor)"), {**ids, 'subject':uuid.UUID(linked_plan)})
            await db.execute(text(f"INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,{kind}_id,origin,reason,reviewed_by) VALUES (:org,:property,:sphere,:subject,'REVIEWED','Reviewed PDF fixture',:actor)"), ids)
            await db.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:sphere,:actor,:caps,:actor,'Explicit financial export fixture')"), ids)
    finally: await engine.dispose()
    return ids


async def clear_disclosure_grants():
    await mutate(text('TRUNCATE property_data_domain,person_data_domain,accounting_scope CASCADE'), {})


async def test_unbound_pdf_denied_and_bound_pdf_needs_financial_export(client_a_admin, seed_data, m4_seed):
    plan = await _create_and_finalize_plan(client_a_admin, m4_seed)
    try:
        await grant_property_contacts(client_a_admin,seed_data,m4_seed['property'])
        await grant(client_a_admin, seed_data, m4_seed['property'], caps=['read','export'])
        async with synthetic_enforcement(seed_data):
            for mode in ['ENFORCED','PAUSED']:
                await change_mode(seed_data, mode)
                assert (await client_a_admin.get(f'/api/economic-plans/{plan}')).status_code == 404
                assert (await client_a_admin.get(f'/api/economic-plans/{plan}/pdf')).status_code == 404
            ids = await bind_output(seed_data, m4_seed['property'], plan, caps=['read'])
            assert (await client_a_admin.get(f'/api/economic-plans/{plan}/pdf')).status_code == 404
            await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE scope_id=:sphere'), ids)
            await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:sphere,:actor,ARRAY['read','export'],:actor,'Reviewed export authorization')"), ids)
            pdf = await client_a_admin.get(f'/api/economic-plans/{plan}/pdf')
            assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF'), pdf.text
    finally: await clear_disclosure_grants()


async def test_saved_plan_rows_and_pdf_require_current_source_access(client_a_admin, seed_data, m4_seed, monkeypatch):
    plan = await _create_and_finalize_plan(client_a_admin, m4_seed)
    before = await client_a_admin.get(f'/api/economic-plans/{plan}')
    assert before.status_code == 200
    original_hash = before.json()['snapshot_sha256']
    await bind_output(seed_data, m4_seed['property'], plan)
    try:
        async with synthetic_enforcement(seed_data):
            assert (await client_a_admin.get(f'/api/economic-plans/{plan}')).status_code == 404
            assert (await client_a_admin.get(f'/api/economic-plans/{plan}/pdf')).status_code == 404
            response = await client_a_admin.get('/api/economic-plans', params={'property_id':m4_seed['property']})
            assert response.status_code == 200 and all(row['id'] != plan for row in response.json())
            async with get_tenant_session(seed_data['org_a_id']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(seed_data['user_a_admin_id'])})
                for table, key in [('economic_plan','id'),('economic_plan_line','economic_plan_id'),('coverage_declaration','economic_plan_id')]:
                    assert await db.scalar(text(f'SELECT count(*) FROM {table} WHERE {key}=:id'), {'id':uuid.UUID(plan)}) == 0
            await grant_property_contacts(client_a_admin,seed_data,m4_seed['property'])
            domain, receipt = await grant(client_a_admin, seed_data, m4_seed['property'], caps=['read'])
            result = await client_a_admin.get(f'/api/economic-plans/{plan}')
            assert result.status_code == 200 and result.json()['snapshot_sha256'] == original_hash
            def forbidden(*args, **kwargs): raise AssertionError('Denied source export must not render PDF')
            with monkeypatch.context() as patch:
                patch.setattr('app.api.economic_plans.render_wirtschaftsplan_pdf', forbidden)
                assert (await client_a_admin.get(f'/api/economic-plans/{plan}/pdf')).status_code == 404
            assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code == 204
            response = await client_a_admin.post(f'/api/property-data-domains/{domain}/grants', headers={'Idempotency-Key':str(uuid.uuid4())},
                json={'user_id':str(seed_data['user_a_admin_id']),'capabilities':['read','export'],'reason':'Export copied financial source facts'})
            assert response.status_code == 201
            await change_mode(seed_data, 'PAUSED')
            assert (await client_a_admin.get(f'/api/economic-plans/{plan}')).status_code == 200
            pdf = await client_a_admin.get(f'/api/economic-plans/{plan}/pdf')
            assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF')
    finally: await clear_disclosure_grants()


async def test_saved_rental_sources_cannot_be_read_from_raw_child_rows(client_a_admin, seed_data, m3_seed):
    run = await draft(client_a_admin, m3_seed, '0.00')
    values = {'advance_payments_total':'0.00','advance_payments_source':'MANUAL'}
    assert (await attest(client_a_admin, 'settlement', run, values)).status_code == 201
    result = await client_a_admin.post(f'/api/settlements/{run}/finalize', json=_extras_for_exit(m3_seed))
    assert result.status_code == 200, result.text
    async with synthetic_enforcement(seed_data):
        assert (await client_a_admin.get(f'/api/settlements/{run}')).status_code == 404
        async with get_tenant_session(seed_data['org_a_id']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(seed_data['user_a_admin_id'])})
            for table, key in [('settlement_run','id'),('settlement_line','settlement_run_id'),('coverage_declaration','settlement_run_id')]:
                assert await db.scalar(text(f'SELECT count(*) FROM {table} WHERE {key}=:id'), {'id':uuid.UUID(run)}) == 0


@pytest.mark.parametrize('family', ['rental','weg','assets'])
async def test_each_pdf_family_checks_source_export_before_render(client_a_admin, seed_data, m3_seed, m4_seed, monkeypatch, family):
    from tests.financial_coverage_helpers import weg_values
    linked_plan = None
    if family == 'rental':
        property_id = m3_seed['property']
        endpoint = '/api/settlements'
        subject = await draft(client_a_admin, m3_seed, '0.00')
        values = {'advance_payments_total':'0.00','advance_payments_source':'MANUAL'}
        final_values = _extras_for_exit(m3_seed)
        renderer = 'app.api.settlements.render_settlement_pdf'
    else:
        property_id = m4_seed['property']
        body = {'property_id':property_id,'period_start':'2025-01-01','period_end':'2025-12-31'}
        if family == 'weg':
            endpoint = '/api/weg-settlements'
            linked_plan = await _create_and_finalize_plan(client_a_admin, m4_seed)
            body['economic_plan_id'] = linked_plan
            values = weg_values(m4_seed)
            renderer = 'app.api.weg_settlements.render_weg_jahresabrechnung_pdf'
        else:
            endpoint = '/api/weg-settlements/asset-reports'
            values = {'reserve_opening_balance':'0.00','reserve_withdrawals_total':'0.00','reserve_contributions_total':'0.00',
                'receivables':[],'payables':[], 'bank_balances':[
                    {'bank_account_id':m4_seed['bank_giro'],'label':'Giro','account_type':'LAUFEND','balance':'0.00'},
                    {'bank_account_id':m4_seed['bank_ruecklage'],'label':'Reserve','account_type':'RUECKLAGE','balance':'0.00'}]}
            renderer = 'app.api.weg_settlements.render_vermoegensbericht_pdf'
        created = await client_a_admin.post(endpoint, json=body)
        assert created.status_code == 201, created.text
        subject = created.json()['id']
        final_values = values
    assert (await attest(client_a_admin, 'settlement', subject, values)).status_code == 201
    assert (await client_a_admin.post(f'{endpoint}/{subject}/finalize', json=final_values)).status_code == 200
    await bind_output(seed_data, property_id, subject, kind='settlement_run',
        scope_type='MIET' if family == 'rental' else 'WEG', linked_plan=linked_plan)
    try:
        await grant_property_contacts(client_a_admin,seed_data,property_id)
        domain, receipt = await grant(client_a_admin, seed_data, property_id, caps=['read'])
        async with synthetic_enforcement(seed_data):
            def forbidden(*args, **kwargs): raise AssertionError('Denied source export must not render PDF')
            with monkeypatch.context() as patch:
                patch.setattr(renderer, forbidden)
                assert (await client_a_admin.get(f'{endpoint}/{subject}/pdf')).status_code == 404
            assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code == 204
            response = await client_a_admin.post(f'/api/property-data-domains/{domain}/grants', headers={'Idempotency-Key':str(uuid.uuid4())},
                json={'user_id':str(seed_data['user_a_admin_id']),'capabilities':['read','export'],'reason':'Reviewed financial source export'})
            assert response.status_code == 201
            pdf = await client_a_admin.get(f'{endpoint}/{subject}/pdf')
            assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF'), pdf.text
    finally: await clear_disclosure_grants()


async def test_disclosure_migration_retains_strict_state_and_roundtrips(seed_data):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration = importlib.import_module('migrations.versions.0060_financial_source_disclosure')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        async with synthetic_enforcement(seed_data):
            with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError, match='Retain strict financial source disclosure'):
                    with db.begin_nested(): migration.downgrade()
                savepoint = db.begin_nested()
                try:
                    db.execute(text('TRUNCATE property_source_security'))
                    migration.downgrade()
                    assert db.scalar(text("SELECT to_regprocedure('property_financial_disclosure_access(uuid,uuid,text)')")) is None
                    migration.upgrade()
                    assert db.scalar(text("SELECT count(*) FROM pg_policy WHERE polname='financial_source_disclosure' AND NOT polpermissive")) == 5
                    assert not db.scalar(text("SELECT has_function_privilege('app_rw','property_complete_graph_actor_access(uuid,uuid,uuid,text,boolean)','EXECUTE')"))
                    for function in ['property_complete_graph_actor_access(uuid,uuid,uuid,text,boolean)',
                                     'property_financial_disclosure_access(uuid,uuid,text)', 'financial_parent_source_access(uuid,text,uuid,uuid)']:
                        assert 'row_security=off' in db.scalar(text('SELECT proconfig FROM pg_proc WHERE oid=CAST(:name AS regprocedure)'), {'name':function})
                finally: savepoint.rollback()
    finally: engine.dispose()


@pytest.mark.parametrize('table', ['property','unit','property_source_security','settlement_run','settlement_line','economic_plan','economic_plan_line','coverage_declaration'])
async def test_disclosure_migration_nowait_dependencies(seed_data, table):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration = importlib.import_module('migrations.versions.0060_financial_source_disclosure')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as reader:
            reader.execute(text(f'SELECT 1 FROM {table} LIMIT 1'))
            with pytest.raises(DBAPIError) as caught:
                with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
                    db.execute(text("SET LOCAL lock_timeout='1s'"))
                    migration.upgrade()
            assert caught.value.orig.pgcode == '55P03'
    finally: engine.dispose()
