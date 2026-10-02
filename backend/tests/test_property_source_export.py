"""Every property-source PDF read needs explicit strict export authority."""
import uuid
import pytest
from app.core.storage import get_store
from tests.test_property_source_admission import setup
from tests.test_property_source_binding import approve_grant, applied, bind
from tests.test_property_source_admission_review import legacy_source
from tests.test_property_source_classification import legacy_bound
from tests.test_bound_source_access import classify
from tests.test_property_source_enforcement import synthetic_enforcement, change_mode
from tests.test_declaration_import_api import isolated_document_store, enable_declaration_imports  # noqa: F401

pytestmark = pytest.mark.asyncio


async def export_grant(api, seed, ids, old):
    assert (await api.post(f"/api/property-data-domains/{ids['domain']}/grants/{old}/revoke")).status_code == 204
    result = await api.post(f"/api/property-data-domains/{ids['domain']}/grants", headers={'Idempotency-Key':str(uuid.uuid4())},
        json={'user_id':str(seed['user_a_admin_id']),'capabilities':['read','prepare','approve','export'],'reason':'Explicit original export'})
    assert result.status_code == 201, result.text
    return result.json()['id']


async def content_source(api, seed, path):
    if path == 'admission':
        ids = await legacy_source(api, seed)
        key = 'review_grant'
        url = f"/api/property-source-admission-reviews/originals/{ids['document']}/content"
    elif path == 'classification':
        ids = await legacy_bound(api, seed)
        key = 'review_grant'
        url = f"/api/property-source-classifications/originals/{ids['document']}/content"
    else:
        ids = await setup(api, seed)
        key = 'uploader_grant'
        if path == 'bound':
            ids[key] = await approve_grant(api, seed, ids)
            await applied(seed, ids)
            await bind(seed, ids)
            url = f"/api/properties/{ids['property']}/documents/{ids['document']}/content"
        else:
            url = f"/api/declaration-imports/{ids['job']}/documents/{ids['document']}/content"
    return ids, ids[key], url


@pytest.mark.parametrize('path', ['bound','draft','admission','classification'])
async def test_strict_export_is_distinct_from_read_and_review(client_a_admin, client_a_clerk, client_b_admin, seed_data, monkeypatch, path):
    ids, old, url = await content_source(client_a_admin, seed_data, path)
    assert (await client_a_admin.get(url)).status_code == 200  # Compatibility remains.
    store = get_store()
    original = store.get
    def forbidden(*args, **kwargs): raise AssertionError('Denied export must not read storage')
    async with synthetic_enforcement(seed_data):
        with monkeypatch.context() as patch:
            patch.setattr(store, 'get', forbidden)
            for api in (client_a_admin, client_a_clerk, client_b_admin):
                response = await api.get(url)
                assert response.status_code in (403,404), response.text
        receipt = await export_grant(client_a_admin, seed_data, ids, old)
        calls = []
        def counted(key):
            calls.append(key)
            return original(key)
        monkeypatch.setattr(store, 'get', counted)
        response = await client_a_admin.get(url)
        assert response.status_code == 200 and response.content == b'%PDF-1.4\n%%EOF', response.text
        assert len(calls) == 1
        await change_mode(seed_data, 'PAUSED')
        assert (await client_a_admin.get(url)).status_code == 200
        assert (await client_a_admin.post(f"/api/property-data-domains/{ids['domain']}/grants/{receipt}/revoke")).status_code == 204
        monkeypatch.setattr(store, 'get', forbidden)
        assert (await client_a_admin.get(url)).status_code == 404


async def test_quarantine_blocks_review_export_even_for_authorized_uploader(client_a_admin, seed_data, monkeypatch):
    ids, old, url = await content_source(client_a_admin, seed_data, 'classification')
    await export_grant(client_a_admin, seed_data, ids, old)
    await classify(client_a_admin, ids, 'QUARANTINED')
    async with synthetic_enforcement(seed_data):
        def forbidden(*args, **kwargs): raise AssertionError('Quarantined original must not read storage')
        monkeypatch.setattr(get_store(), 'get', forbidden)
        assert (await client_a_admin.get(url)).status_code == 404


@pytest.mark.parametrize('path', ['admission','classification'])
async def test_review_page_matches_export_decision(client_a_admin, seed_data, path):
    ids, old, _ = await content_source(client_a_admin, seed_data, path)
    route = '/api/property-source-admission-reviews/pending/page' if path == 'admission' else '/api/property-source-classifications/pending/page'
    async def preview():
        response = await client_a_admin.get(route)
        assert response.status_code == 200, response.text
        return next(row for row in response.json()['items'] if row['document_id'] == str(ids['document']))['can_preview']
    assert await preview()
    async with synthetic_enforcement(seed_data):
        assert not await preview()
        await export_grant(client_a_admin, seed_data, ids, old)
        assert await preview()


async def test_review_purpose_does_not_confer_source_custody(client_a_admin, seed_data):
    from sqlalchemy import text
    from app.core.db import get_tenant_session
    ids, _, _ = await content_source(client_a_admin, seed_data, 'classification')
    assert (await client_a_admin.post(f"/api/property-data-domains/{ids['domain']}/grants/{ids['reader_grant']}/revoke")).status_code == 204
    result = await client_a_admin.post(f"/api/property-data-domains/{ids['domain']}/grants", headers={'Idempotency-Key':str(uuid.uuid4())},
        json={'user_id':str(seed_data['user_a_clerk_id']),'capabilities':['read','prepare','approve','export'],'reason':'Different source principal'})
    assert result.status_code == 201
    async with synthetic_enforcement(seed_data), get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(seed_data['user_a_clerk_id'])})
        for purpose in ['SOURCE','ADMISSION_REVIEW','CLASSIFICATION_REVIEW','unknown',None]:
            assert not await db.scalar(text('SELECT property_source_export_access(:org,:document,:purpose)'), {**ids,'org':seed_data['org_a_id'],'purpose':purpose})
        assert not await db.scalar(text("SELECT property_source_export_access(:org,:document,'CLASSIFICATION_REVIEW')"), {**ids,'org':seed_data['org_b_id']})


async def test_export_migration_roundtrip_and_strict_rollback_guard(seed_data):
    import importlib
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration = importlib.import_module('migrations.versions.0058_property_source_export')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        async with synthetic_enforcement(seed_data):
            with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError, match='Retain strict source export checks'):
                    with db.begin_nested(): migration.downgrade()
                savepoint = db.begin_nested()
                try:
                    db.execute(text('TRUNCATE property_source_security'))
                    migration.downgrade()
                    assert db.scalar(text("SELECT to_regprocedure('property_source_export_access(uuid,uuid,text)')")) is None
                    migration.upgrade()
                    assert 'row_security=off' in db.scalar(text("SELECT proconfig FROM pg_proc WHERE oid='property_source_export_access(uuid,uuid,text)'::regprocedure"))
                    assert db.scalar(text("SELECT has_function_privilege('app_rw','property_source_export_access(uuid,uuid,text)','EXECUTE')"))
                finally: savepoint.rollback()
    finally: engine.dispose()


@pytest.mark.parametrize('table', ['property_document','declaration_import','declaration_import_document','property_source_classification','property_source_security'])
async def test_export_migration_dependency_nowait(seed_data, table):
    import importlib
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration = importlib.import_module('migrations.versions.0058_property_source_export')
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
