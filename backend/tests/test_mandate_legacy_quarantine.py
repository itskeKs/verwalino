"""Legacy mandate quarantine observes current metadata without claiming custody."""
import pytest
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_scoped_mandate_documents import document_fixture, upload
from tests.test_scoped_account_statements import client

pytestmark = pytest.mark.asyncio


async def test_unclassified_mandate_upload_is_denied_even_in_preparation(migrator_session, scope_seed, tmp_path, monkeypatch):
    from app.core.storage import LocalFsDocumentStore, set_store
    store = LocalFsDocumentStore(tmp_path)
    set_store(store)
    ids = await document_fixture(migrator_session, scope_seed)
    try:
        def denied_put(*args, **kwargs):
            raise AssertionError('Unclassified upload must be rejected before storage in every mode')
        monkeypatch.setattr(store, 'put', denied_put)
        async with client(ids) as api:
            response = await api.post(f"/api/mandates/{ids['mandate']}/documents", **upload(ids, scoped=False))
            assert response.status_code == 404, response.text
    finally:
        set_store(None)


async def test_legacy_observation_is_private_and_cannot_be_rewritten(migrator_session, scope_seed):
    from sqlalchemy.exc import DBAPIError
    from tests.mandate_legacy_fixture import seed_legacy_mandate
    from tests.test_scope_payment_reconciliation import read_value
    ids = await document_fixture(migrator_session, scope_seed)
    ids['document'] = await seed_legacy_mandate(ids)
    assert await read_value("SELECT snapshot_digest=encode(sha256(convert_to(to_jsonb(d)::text,'UTF8')),'hex') FROM mandate_legacy_observation o JOIN mandate_document d ON d.organization_id=o.organization_id AND d.id=o.document_id WHERE d.id=:document", ids)
    for statement in [
        "UPDATE mandate_document SET uploaded_by=:actor WHERE id=:document RETURNING id",
        "UPDATE mandate_document SET storage_uri='changed' WHERE id=:document RETURNING id",
        'DELETE FROM mandate_document WHERE id=:document RETURNING id',
        'DELETE FROM mandate_legacy_observation WHERE document_id=:document RETURNING document_id',
    ]:
        with pytest.raises(DBAPIError):
            await read_value(statement, ids)
    for actor in ['actor', 'clerk']:
        assert await read_value('SELECT count(*) FROM mandate_document WHERE id=:document', ids, actor_key=actor) == 0
        with pytest.raises(DBAPIError):
            await read_value('SELECT * FROM mandate_legacy_observation WHERE document_id=:document', ids, actor_key=actor)
        async with client(ids, actor) as api:
            endpoint = f"/api/mandates/{ids['mandate']}/documents"
            assert (await api.get(endpoint)).json() == []
            assert (await api.get(endpoint + '/' + str(ids['document']) + '/download')).status_code == 404


async def test_quarantine_migration_observes_legacy_without_rewriting_or_losing_evidence(migrator_session, scope_seed):
    import importlib
    import uuid
    from sqlalchemy import text, create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = await document_fixture(migrator_session, scope_seed)
    ids['document'] = uuid.uuid4()
    migration = importlib.import_module('migrations.versions.0078_mandate_legacy_quarantine')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            point = db.begin_nested()
            try:
                # Rehearse the actual 0077->0078 transition only in this guarded,
                # disposable transaction, then restore all current fixtures.
                db.execute(text('TRUNCATE mandate_document CASCADE'))
                disposition = importlib.import_module('migrations.versions.0079_mandate_quarantine_disposition')
                disposition.downgrade()
                migration.downgrade()
                db.execute(text("""INSERT INTO mandate_document(id,organization_id,management_mandate_id,doc_type,title,mime_type,
                    content_byte_size,content_sha256,storage_uri,storage_backend,uploaded_by)
                    VALUES(:document,:org,:mandate,'SONSTIGES','Unverified uploader','application/pdf',1,repeat('a',64),
                    'unverified/reference','TEST',:clerk)"""), ids)
                original = db.scalar(text('SELECT to_jsonb(d) FROM mandate_document d WHERE id=:document'), ids)
                migration.upgrade()
                assert db.scalar(text('SELECT to_jsonb(d) FROM mandate_document d WHERE id=:document'), ids) == original
                assert db.scalar(text("SELECT snapshot_digest=encode(sha256(convert_to(to_jsonb(d)::text,'UTF8')),'hex') FROM mandate_legacy_observation o JOIN mandate_document d ON d.organization_id=o.organization_id AND d.id=o.document_id WHERE d.id=:document"), ids)
                with pytest.raises(DBAPIError, match='Retain legacy mandate quarantine'):
                    with db.begin_nested():
                        migration.downgrade()
                assert not db.scalar(text('SELECT property_source_activation_ready(:org)'), ids)
            finally:
                point.rollback()
    finally:
        engine.dispose()
