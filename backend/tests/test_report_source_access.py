"""Indirect statements and dunning snapshots retain their property source audience."""
import uuid
import pytest
from sqlalchemy import text
from app.core.db import get_tenant_session
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_scoped_account_statements import statement_fixture, client, mutate, url
from tests.test_scoped_dunning import grant as financial_grant, tenant_body
from tests.test_property_source_enforcement import synthetic_enforcement
from tests.contact_source_fixtures import grant_contact

pytestmark = pytest.mark.asyncio


async def contacts_for_clerk(ids, seed):
    async with client(ids, 'actor') as admin:
        return await grant_contact(admin, {**seed, 'user_a_admin_id': ids['clerk']}, ids['person'])


async def test_owner_statement_cannot_disclose_hidden_property_sources(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'owner')
    await contacts_for_clerk(ids, scope_seed)
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        result = await api.get(url(ids, 'owner'), params={'accounting_scope_id': str(ids['scope']),
            'period_start': '2025-01-01', 'period_end': '2025-12-31'})
        assert result.status_code == 404, result.text


async def test_stored_dunning_cannot_disclose_hidden_property_sources(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await financial_grant(ids, ['read', 'prepare', 'approve', 'export'])
    async with client(ids) as api:
        created = await api.post('/api/dunning/tenant', json=tenant_body(ids))
        assert created.status_code == 201, created.text
        ids['notice'] = uuid.UUID(created.json()['id'])
        assert (await api.post(f"/api/dunning/{ids['notice']}/issue")).status_code == 200
    await contacts_for_clerk(ids, scope_seed)
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        result = await api.get(f"/api/dunning/{ids['notice']}")
        assert result.status_code == 404, result.text
        assert (await api.get('/api/dunning')).json() == []
        async with get_tenant_session(ids['org']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(ids['clerk'])})
            assert await db.scalar(text('SELECT count(*) FROM dunning_notice WHERE id=:notice'), ids) == 0
            assert await db.scalar(text('SELECT count(*) FROM dunning_scope_context WHERE dunning_notice_id=:notice'), ids) == 0


async def property_for_clerk(ids, seed, caps=('read',)):
    from tests.test_property_source_access import grant
    async with client(ids, 'actor') as admin:
        return await grant(admin, seed, ids['property'], actor=ids['clerk'], caps=list(caps))


@pytest.mark.parametrize('kind', ['lease', 'owner'])
async def test_statement_requires_complete_current_property_and_contact_sources(migrator_session, scope_seed, kind):
    ids = await statement_fixture(migrator_session, scope_seed, kind)
    contact_domain, contact_receipt = await contacts_for_clerk(ids, scope_seed)
    domain, receipt = await property_for_clerk(ids, scope_seed)
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        params = {'accounting_scope_id': str(ids['scope']), 'period_start': '2025-01-01', 'period_end': '2025-12-31'}
        result = await api.get(url(ids, kind), params=params)
        assert result.status_code == 200 and result.json()['ist_total'] == '100.00', result.text
        # Counterparty source revocation must not silently become a zero report.
        async with client(ids, 'actor') as admin:
            assert (await admin.post(f'/api/person-data-domains/{contact_domain}/grants/{contact_receipt}/revoke')).status_code == 204
        assert (await api.get(url(ids, kind), params=params)).status_code == 404


async def test_dunning_read_export_and_effects_keep_separate_source_rights(migrator_session, scope_seed, monkeypatch):
    from tests.test_property_source_enforcement import change_mode
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await financial_grant(ids, ['read', 'prepare', 'approve', 'export'])
    await contacts_for_clerk(ids, scope_seed)
    domain, receipt = await property_for_clerk(ids, scope_seed)
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        result = await api.post('/api/dunning/tenant', json=tenant_body(ids))
        assert result.status_code == 201, result.text
        ids['notice'] = uuid.UUID(result.json()['id'])
        assert (await api.post(f"/api/dunning/{ids['notice']}/issue")).status_code == 200
        def forbidden(*args, **kwargs):
            raise AssertionError('Unreadable export must not render PDF')
        with monkeypatch.context() as patch:
            patch.setattr('app.api.dunning.render_dunning_pdf', forbidden)
            assert (await api.get(f"/api/dunning/{ids['notice']}/pdf")).status_code == 404
        async with client(ids, 'actor') as admin:
            assert (await admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code == 204
            granted = await admin.post(f'/api/property-data-domains/{domain}/grants', headers={'Idempotency-Key': str(uuid.uuid4())},
                json={'user_id': str(ids['clerk']), 'capabilities': ['read', 'export'], 'reason': 'Reviewed report export'})
            assert granted.status_code == 201, granted.text
        await change_mode(scope_seed, 'PAUSED')
        assert (await api.get(f"/api/dunning/{ids['notice']}")).status_code == 200
        pdf = await api.get(f"/api/dunning/{ids['notice']}/pdf")
        assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF'), pdf.text
        assert (await api.post('/api/dunning/tenant', json=tenant_body(ids))).status_code == 404


async def test_unclassified_property_original_blocks_visible_statement_and_dunning(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await financial_grant(ids, ['read', 'prepare', 'approve', 'export'])
    await contacts_for_clerk(ids, scope_seed)
    await property_for_clerk(ids, scope_seed, ('read', 'export'))
    await mutate(text("""INSERT INTO property_document(organization_id,property_id,doc_type,title,original_filename,mime_type,
        content_byte_size,content_sha256,storage_uri,storage_backend,page_count)
        VALUES(:org,:property,'ANNEX','Unknown legacy source','legacy.pdf','application/pdf',1,:sha,'synthetic/legacy','TEST',1)"""), {**ids, 'sha': 'a' * 64})
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        params = {'accounting_scope_id': str(ids['scope']), 'period_start': '2025-01-01', 'period_end': '2025-12-31'}
        assert (await api.get(url(ids, 'lease'), params=params)).status_code == 404
        assert (await api.post('/api/dunning/tenant', json=tenant_body(ids))).status_code == 404


async def test_hidden_dunning_remains_a_privacy_blocker(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await financial_grant(ids, ['read', 'prepare', 'approve', 'export'])
    await mutate(text("UPDATE membership SET role='OWNER_ADMIN' WHERE organization_id=:org AND user_id=:clerk"), ids)
    async with client(ids) as api:
        result = await api.post('/api/dunning/tenant', json=tenant_body(ids))
        assert result.status_code == 201, result.text
        notice = result.json()['id']
        assert (await api.post(f'/api/dunning/{notice}/issue')).status_code == 200
    await contacts_for_clerk(ids, scope_seed)
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        assert (await api.get(f'/api/dunning/{notice}')).status_code == 404
        for action in ['status', 'export']:
            assert (await api.get(f"/api/dsgvo/persons/{ids['person']}/{action}")).status_code == 409
        assert (await api.post(f"/api/dsgvo/persons/{ids['person']}/anonymize")).status_code == 409


async def test_report_migration_preserves_historical_snapshots_and_refuses_strict_downgrade(migrator_session, scope_seed):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await financial_grant(ids, ['read', 'prepare'])
    async with client(ids) as api:
        assert (await api.post('/api/dunning/tenant', json=tenant_body(ids))).status_code == 201
    migration = importlib.import_module('migrations.versions.0075_report_source_access')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        async with synthetic_enforcement(scope_seed):
            with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError, match='Retain strict report source access'):
                    with db.begin_nested():
                        migration.downgrade()
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            point = db.begin_nested()
            try:
                before = db.scalar(text("SELECT md5(COALESCE(string_agg(to_jsonb(n)::text,',' ORDER BY id),'')) FROM dunning_notice n"))
                migration.downgrade()
                migration.upgrade()
                assert db.scalar(text("SELECT md5(COALESCE(string_agg(to_jsonb(n)::text,',' ORDER BY id),'')) FROM dunning_notice n")) == before
                assert not db.scalar(text('SELECT property_source_activation_ready(:org)'), ids)
            finally:
                point.rollback()
    finally:
        engine.dispose()


async def test_deadline_monitor_cannot_report_partial_hidden_sources(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await contacts_for_clerk(ids, scope_seed)
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        result = await api.get('/api/fristen', params={'accounting_scope_id': str(ids['scope']), 'reference_date': '2025-12-31'})
        assert result.status_code == 404, result.text
        await property_for_clerk(ids, scope_seed)
        permitted = await api.get('/api/fristen', params={'accounting_scope_id': str(ids['scope']), 'reference_date': '2025-12-31'})
        assert permitted.status_code == 200, permitted.text
        overdue = next(row for row in permitted.json() if row['kind'] == 'OFFENE_POSTEN')
        assert overdue['status'] != 'OK', overdue
        assert (await api.get('/api/fristen', params={'reference_date': '2025-12-31'})).status_code == 404


async def test_raw_dunning_context_cannot_prepare_from_hidden_sources(migrator_session, scope_seed):
    from sqlalchemy.exc import DBAPIError
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await financial_grant(ids, ['read', 'prepare'])
    await contacts_for_clerk(ids, scope_seed)
    async with synthetic_enforcement(scope_seed), get_tenant_session(ids['org']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('app.current_scope_id',:scope,true)"),
            {'actor': str(ids['clerk']), 'scope': str(ids['scope'])})
        with pytest.raises(DBAPIError, match='row-level security'):
            async with db.begin_nested():
                await db.execute(text("""INSERT INTO dunning_scope_context(organization_id,dunning_notice_id,scope_id,
                    lease_agreement_id,period_start,period_end,created_by,reason)
                    VALUES(:org,:notice,:scope,:lease,'2025-01-01','2025-12-31',:clerk,'Direct source bypass')"""),
                    {**ids, 'notice': uuid.uuid4()})
