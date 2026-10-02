"""Strict mandate originals require explicit scope and preserve paused disclosure."""
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_scoped_mandate_documents import document_fixture, upload
from tests.test_scoped_account_statements import client, mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_property_source_enforcement import synthetic_enforcement, change_mode

pytestmark = pytest.mark.asyncio


async def test_strict_legacy_mandate_original_is_hidden_and_retained(migrator_session, scope_seed, tmp_path, monkeypatch):
    from app.core.storage import LocalFsDocumentStore, set_store
    store = LocalFsDocumentStore(tmp_path)
    set_store(store)
    ids = await document_fixture(migrator_session, scope_seed)
    endpoint = f"/api/mandates/{ids['mandate']}/documents"
    try:
        async with client(ids) as api:
            response = await api.post(endpoint, **upload(ids, scoped=False))
            assert response.status_code == 201, response.text
            ids['document'] = uuid.UUID(response.json()['id'])
        def denied_storage(*args, **kwargs):
            raise AssertionError('Unknown scope must be denied before storage')
        monkeypatch.setattr(store, 'iter_chunks', denied_storage)
        async with synthetic_enforcement(scope_seed):
            for actor in ['actor', 'clerk']:
                async with client(ids, actor) as api:
                    assert (await api.get(endpoint)).json() == []
                    path = endpoint + '/' + str(ids['document'])
                    assert (await api.get(path + '/download')).status_code == 404
                    assert (await api.delete(path)).status_code == 404
                    assert (await api.post(path + '/archive', json={'reason': 'No invented context'})).status_code == 404
                assert await read_value('SELECT count(*) FROM mandate_document WHERE id=:document', ids, actor_key=actor) == 0
                assert await read_value('SELECT count(*) FROM audit_event WHERE entity_id=:document', ids, actor_key=actor) == 0
                assert await read_value("UPDATE mandate_document SET title='Forbidden' WHERE id=:document RETURNING id", ids, actor_key=actor) is None
                assert await read_value('DELETE FROM mandate_document WHERE id=:document RETURNING id', ids, actor_key=actor) is None
        # Returning to preparation is only the disposable test cleanup, not a supported rollback.
        assert await read_value('SELECT count(*) FROM mandate_document WHERE id=:document', ids) == 1
    finally:
        set_store(None)


async def test_strict_unscoped_upload_fails_before_storage(migrator_session, scope_seed, tmp_path, monkeypatch):
    from app.core.storage import LocalFsDocumentStore, set_store
    store = LocalFsDocumentStore(tmp_path)
    set_store(store)
    ids = await document_fixture(migrator_session, scope_seed)
    try:
        def denied_put(*args, **kwargs):
            raise AssertionError('Unknown scope must be denied before storage')
        monkeypatch.setattr(store, 'put', denied_put)
        async with synthetic_enforcement(scope_seed), client(ids) as api:
            response = await api.post(f"/api/mandates/{ids['mandate']}/documents", **upload(ids, scoped=False))
            assert response.status_code == 404, response.text
    finally:
        set_store(None)


async def test_source_pause_blocks_mandate_effects_but_keeps_authorized_export(migrator_session, scope_seed, tmp_path, monkeypatch):
    from app.core.storage import LocalFsDocumentStore, set_store
    store = LocalFsDocumentStore(tmp_path)
    set_store(store)
    ids = await document_fixture(migrator_session, scope_seed)
    endpoint = f"/api/mandates/{ids['mandate']}/documents"
    await mutate(text("UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:source_scope AND revoked_at IS NULL"), ids)
    await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:source_scope,:clerk,ARRAY['read','prepare','export'],:actor,'Explicit export')"), ids)
    try:
        async with synthetic_enforcement(scope_seed), client(ids) as api:
            response = await api.post(endpoint, **upload(ids))
            assert response.status_code == 201, response.text
            ids['document'] = uuid.UUID(response.json()['id'])
            path = endpoint + '/' + str(ids['document'])
            await change_mode(scope_seed, 'PAUSED')
            assert (await api.get(path + '/download')).content == b'%PDF-1.4 synthetic scoped mandate'
            assert (await api.get(endpoint)).json()[0]['id'] == str(ids['document'])
            assert (await api.post(path + '/archive', json={'reason': 'Paused effect'})).status_code == 404
            def denied_put(*args, **kwargs):
                raise AssertionError('Paused source must be denied before storage')
            monkeypatch.setattr(store, 'put', denied_put)
            assert (await api.post(endpoint, **upload(ids))).status_code == 404
    finally:
        set_store(None)


async def test_strict_database_rejects_unscoped_original_insert(migrator_session, scope_seed):
    from sqlalchemy.exc import DBAPIError
    ids = await document_fixture(migrator_session, scope_seed)
    async with synthetic_enforcement(scope_seed):
        with pytest.raises(DBAPIError):
            await read_value("""INSERT INTO mandate_document(organization_id,management_mandate_id,doc_type,title,
                mime_type,content_byte_size,content_sha256,storage_uri,storage_backend)
                VALUES(:org,:mandate,'SONSTIGES','Unclassified','application/pdf',1,repeat('a',64),'synthetic/unknown','TEST')
                RETURNING id""", ids, actor_key='clerk')


@pytest.mark.parametrize('change', ['revoke', 'deactivate', 'expire'])
async def test_strict_scoped_original_requires_current_reader(migrator_session, scope_seed, tmp_path, change):
    from app.core.storage import LocalFsDocumentStore, set_store
    set_store(LocalFsDocumentStore(tmp_path))
    ids = await document_fixture(migrator_session, scope_seed)
    endpoint = f"/api/mandates/{ids['mandate']}/documents"
    try:
        async with synthetic_enforcement(scope_seed), client(ids) as api:
            response = await api.post(endpoint, **upload(ids))
            assert response.status_code == 201, response.text
            ids['document'] = uuid.UUID(response.json()['id'])
            assert (await api.get(endpoint)).json()[0]['id'] == str(ids['document'])
            if change == 'deactivate':
                await mutate(text('UPDATE "user" SET is_active=false WHERE id=:clerk'), ids)
            else:
                await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:source_scope AND revoked_at IS NULL'), ids)
                if change == 'expire':
                    # Retained historical grant, already expired at read time.
                    await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason,created_at,expires_at) VALUES(:org,:source_scope,:clerk,ARRAY['read','prepare'],:actor,'Expired reader',clock_timestamp()-interval '2 seconds',clock_timestamp()-interval '1 second')"), ids)
            assert await read_value('SELECT count(*) FROM mandate_document WHERE id=:document', ids, actor_key='clerk') == 0
            result = await api.get(endpoint)
            assert result.status_code == (401 if change == 'deactivate' else 200)
            if change != 'deactivate':
                assert result.json() == []
    finally:
        set_store(None)


async def test_mandate_source_migration_preserves_history_and_refuses_strict_downgrade(migrator_session, scope_seed, tmp_path):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from app.core.storage import LocalFsDocumentStore, set_store
    set_store(LocalFsDocumentStore(tmp_path))
    ids = await document_fixture(migrator_session, scope_seed)
    migration = importlib.import_module('migrations.versions.0077_mandate_source_enforcement')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        async with client(ids) as api:
            response = await api.post(f"/api/mandates/{ids['mandate']}/documents", **upload(ids, scoped=False))
            assert response.status_code == 201, response.text
        async with synthetic_enforcement(scope_seed):
            with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError, match='Retain strict mandate source access'):
                    with db.begin_nested():
                        migration.downgrade()
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            point = db.begin_nested()
            try:
                def hashes():
                    return [db.scalar(text(f"SELECT md5(COALESCE(string_agg(to_jsonb(r)::text,',' ORDER BY id),'')) FROM {table} r"))
                            for table in ['mandate_document', 'audit_event']]
                before = hashes()
                oid = db.scalar(text("SELECT 'mandate_source_access(uuid,uuid,text)'::regprocedure::oid"))
                migration.downgrade()
                migration.upgrade()
                assert db.scalar(text("SELECT 'mandate_source_access(uuid,uuid,text)'::regprocedure::oid")) == oid
                assert hashes() == before
                assert not db.scalar(text('SELECT property_source_activation_ready(:org)'), ids)
            finally:
                point.rollback()
    finally:
        engine.dispose()
        set_store(None)
