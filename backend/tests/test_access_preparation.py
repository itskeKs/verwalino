"""Administrators see aggregate preparation gaps without hidden source content."""
import pytest
from sqlalchemy import text
from app.core.db import get_tenant_session
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_scoped_account_statements import statement_fixture, client
from tests.test_property_source_enforcement import synthetic_enforcement

pytestmark = pytest.mark.asyncio


async def test_preparation_inventory_counts_hidden_sources_without_disclosing_values(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    async with synthetic_enforcement(scope_seed), client(ids, 'actor') as api:
        result = await api.get('/api/access-preparation')
        assert result.status_code == 200, result.text
        body = result.json()
        assert body['mode'] == 'ENFORCED'
        assert body['activation_available'] is False
        assert body['counts']['properties_total'] == 2
        assert body['counts']['properties_without_domain'] == 2
        assert body['counts']['properties_without_current_reader'] == 2
        assert body['counts']['contacts_total'] == 1
        assert body['counts']['contacts_without_current_reader'] == 1
        assert 'Statement Person' not in result.text
        assert str(ids['person']) not in result.text
        async with get_tenant_session(ids['org']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(ids['actor'])})
            assert await db.scalar(text('SELECT count(*) FROM person')) == 0


async def test_preparation_inventory_is_administrator_only(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    async with client(ids) as api:
        assert (await api.get('/api/access-preparation')).status_code == 403


async def test_inventory_follows_explicit_read_grants_and_expiry(migrator_session, scope_seed):
    import uuid
    from datetime import datetime, timedelta, timezone
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    async with client(ids, 'actor') as api:
        domain = await api.post('/api/property-data-domains', headers={'Idempotency-Key': str(uuid.uuid4())},
            json={'property_id': str(ids['property']), 'label': 'Preparation test', 'reason': 'Explicit review'})
        assert domain.status_code == 201, domain.text
        grant = await api.post(f"/api/property-data-domains/{domain.json()['id']}/grants", headers={'Idempotency-Key': str(uuid.uuid4())},
            json={'user_id': str(ids['clerk']), 'capabilities': ['read'], 'reason': 'Temporary reader',
                  'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()})
        assert grant.status_code == 201, grant.text
        before = (await api.get('/api/access-preparation')).json()
        assert before['counts']['properties_without_domain'] == 1
        assert before['counts']['properties_without_current_reader'] == 1
        async with get_tenant_session(ids['org']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(ids['actor'])})
            await db.execute(text('SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM expires_at-clock_timestamp()))+0.02) FROM property_data_grant WHERE id=:grant'),
                {'grant': uuid.UUID(grant.json()['id'])})
        after = (await api.get('/api/access-preparation')).json()
        assert after['counts']['properties_without_current_reader'] == 2
        assert after['activation_available'] is False


@pytest.mark.parametrize('change', ['demote', 'deactivate'])
async def test_inventory_rechecks_live_administrator_inside_data_transaction(migrator_session, scope_seed, change):
    from app.main import app
    from app.core.tenant import AuthContext, get_current_user
    from tests.test_scoped_account_statements import mutate
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    app.dependency_overrides[get_current_user] = lambda: AuthContext(ids['actor'], ids['org'], 'OWNER_ADMIN')
    try:
        if change == 'demote':
            await mutate(text("UPDATE membership SET role='CLERK' WHERE organization_id=:org AND user_id=:actor"), ids)
        else:
            await mutate(text('UPDATE "user" SET is_active=false WHERE id=:actor'), ids)
        async with client(ids, 'actor') as api:
            assert (await api.get('/api/access-preparation')).status_code == (403 if change == 'demote' else 401)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        await mutate(text("UPDATE membership SET role='OWNER_ADMIN' WHERE organization_id=:org AND user_id=:actor"), ids)
        await mutate(text('UPDATE "user" SET is_active=true WHERE id=:actor'), ids)


@pytest.mark.parametrize('boundary', ['clerk', 'wrong_org', 'worker'])
async def test_inventory_direct_sql_cannot_bypass_administration(migrator_session, scope_seed, boundary, seed_data):
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_worker_tenant_session
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    factory = get_worker_tenant_session if boundary == 'worker' else get_tenant_session
    async with factory(ids['org']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),
            {'actor': str(ids['clerk'] if boundary == 'clerk' else ids['actor'])})
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text('SELECT access_preparation_inventory(:org)'),
                    {'org': seed_data['org_b_id'] if boundary == 'wrong_org' else ids['org']})


async def test_inventory_is_exactly_tenant_scoped(migrator_session, scope_seed, seed_data, client_b_admin):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    async with client(ids, 'actor') as api:
        before = (await api.get('/api/access-preparation')).json()['counts']
        foreign = await client_b_admin.post('/api/persons', json={'first_name': 'Foreign secret', 'last_name': 'Invisible'})
        assert foreign.status_code == 201, foreign.text
        after = await api.get('/api/access-preparation', params={'organization_id': str(seed_data['org_b_id'])})
        assert after.json()['counts'] == before
        assert 'Foreign secret' not in after.text


@pytest.mark.parametrize('change', ['revoke', 'deactivate'])
async def test_inventory_excludes_revoked_or_inactive_readers(migrator_session, scope_seed, change):
    import uuid
    from tests.test_scoped_account_statements import mutate
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    async with client(ids, 'actor') as api:
        domain = await api.post('/api/property-data-domains', headers={'Idempotency-Key': str(uuid.uuid4())},
            json={'property_id': str(ids['property']), 'label': 'Reader check', 'reason': 'Explicit review'})
        assert domain.status_code == 201, domain.text
        path = f"/api/property-data-domains/{domain.json()['id']}/grants"
        grant = await api.post(path, headers={'Idempotency-Key': str(uuid.uuid4())},
            json={'user_id': str(ids['clerk']), 'capabilities': ['read'], 'reason': 'Reviewed reader'})
        assert grant.status_code == 201, grant.text
        assert (await api.get('/api/access-preparation')).json()['counts']['properties_without_current_reader'] == 1
        if change == 'revoke':
            assert (await api.post(f"{path}/{grant.json()['id']}/revoke")).status_code == 204
        else:
            await mutate(text('UPDATE "user" SET is_active=false WHERE id=:clerk'), ids)
        assert (await api.get('/api/access-preparation')).json()['counts']['properties_without_current_reader'] == 2


async def test_inventory_counts_unclassified_legacy_without_changing_originals(migrator_session, scope_seed):
    import uuid
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_scoped_dunning import legacy_notice
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await legacy_notice(ids)
    ids.update(mandate=uuid.uuid4(), unbound_bank=uuid.uuid4(), sha='a' * 64)
    from tests.test_scoped_account_statements import mutate
    await mutate(text("INSERT INTO management_mandate(id,organization_id,mandate_type,label,start_date) VALUES(:mandate,:org,'MIET','Hidden legacy mandate','2024-01-01')"), ids)
    from tests.mandate_legacy_fixture import seed_legacy_mandate
    await seed_legacy_mandate(ids)
    for prop in (ids['property'], None):
        await mutate(text("""INSERT INTO property_document(organization_id,property_id,doc_type,title,original_filename,mime_type,
            content_byte_size,content_sha256,storage_uri,storage_backend,page_count)
            VALUES(:org,:prop,'ANNEX','Hidden original','secret.pdf','application/pdf',1,:sha,'synthetic/legacy','TEST',1)"""), {**ids, 'prop': prop})
    await mutate(text("INSERT INTO bank_account(id,organization_id,property_id,account_type,label) VALUES(:unbound_bank,:org,:property,'LAUFEND','Hidden unclassified bank')"), ids)
    await mutate(text("""INSERT INTO payment(organization_id,property_id,bank_account_id,amount,direction,value_date,payment_type,status,tenant_profile_id)
        VALUES(:org,:property,:unbound_bank,123.45,'IN','2025-06-01','KALTMIETE','draft',:profile)"""), ids)
    async with synthetic_enforcement(scope_seed), client(ids, 'actor') as api:
        response = await api.get('/api/access-preparation')
        assert response.status_code == 200, response.text
        counts = response.json()['counts']
        assert counts['originals_total'] == 2
        assert counts['bound_originals_without_review_record'] == 1
        assert counts['unbound_originals_unknown_uploader'] == 1
        assert counts['originals_quarantined'] == 0
        assert counts['bank_accounts_without_scope'] == 1
        assert counts['payments_without_scope'] == 1
        assert counts['dunning_without_context'] == 1
        assert counts['mandate_originals_without_scope'] == 1
        assert not any(value in response.text for value in ['Hidden', 'private.pdf', 'secret.pdf', '123.45', str(ids['notice'])])
    # A read-only helper migration can be removed/reinstalled without touching history.
    migration = importlib.import_module('migrations.versions.0076_access_preparation_inventory')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            point = db.begin_nested()
            try:
                def fingerprints():
                    return [db.scalar(text(f"SELECT md5(COALESCE(string_agg(to_jsonb(r)::text,',' ORDER BY id),'')) FROM {table} r"))
                            for table in ['dunning_notice', 'mandate_document', 'property_document', 'payment', 'scope_grant']]
                before = fingerprints()
                migration.downgrade()
                migration.upgrade()
                assert fingerprints() == before
                assert not db.scalar(text('SELECT property_source_activation_ready(:org)'), ids)
            finally:
                point.rollback()
    finally:
        engine.dispose()
