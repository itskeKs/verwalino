"""Explicit mandate sources retain originals and check rights before file access."""
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_scope_grants import scope_fixture
from tests.test_scoped_account_statements import client, mutate
from tests.test_scope_payment_reconciliation import read_value

pytestmark = pytest.mark.asyncio


async def document_fixture(session, seed):
    ids = await scope_fixture(session, seed)
    ids.update(mandate=uuid.uuid4(), source_scope=uuid.uuid4())
    await session.execute(text("INSERT INTO management_mandate(id,organization_id,mandate_type,label,start_date) VALUES(:mandate,:org,'WEG','Source mandate','2024-01-01')"), ids)
    await session.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,management_mandate_id,scope_type,label,valid_from,origin,created_by) VALUES(:source_scope,:org,:property,:mandate,'WEG','Source scope','2025-01-01','REVIEWED',:actor)"), ids)
    await session.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:source_scope,:clerk,ARRAY['read','prepare'],:actor,'Source preparation')"), ids)
    await session.commit()
    return ids


def upload(ids, *, scoped=True):
    return {'files': {'file': ('contract.pdf', b'%PDF-1.4 synthetic scoped mandate', 'application/pdf')},
            'data': {'doc_type': 'VERWALTERVERTRAG', 'title': 'Reviewed contract',
                     **({'accounting_scope_id': str(ids['source_scope']), 'scope_reason': 'Reviewed content belongs to the selected source scope'} if scoped else {})}}


async def test_scope_upload_checks_before_storage_and_separates_read_from_export(migrator_session, scope_seed, tmp_path, monkeypatch):
    from app.core.storage import LocalFsDocumentStore, set_store
    store = LocalFsDocumentStore(tmp_path)
    set_store(store)
    ids = await document_fixture(migrator_session, scope_seed)
    endpoint = f"/api/mandates/{ids['mandate']}/documents"
    try:
        async with client(ids) as api:
            response = await api.post(endpoint, **upload(ids))
            assert response.status_code == 201, response.text
            doc = response.json()
            assert doc['accounting_scope_id'] == str(ids['source_scope'])
            assert doc['archived_at'] is None
            assert (await api.get(endpoint)).json()[0]['id'] == doc['id']
            assert (await api.get(endpoint)).headers['x-document-visibility'] == 'accessible-only'
            def denied_read(*args, **kwargs):
                raise AssertionError('Storage must not be opened without export')
            monkeypatch.setattr(store, 'iter_chunks', denied_read)
            assert (await api.get(endpoint + '/' + doc['id'] + '/download')).status_code == 404
        async with client(ids, 'actor') as admin:
            assert (await admin.get(endpoint)).json() == []
            def denied_put(*args, **kwargs):
                raise AssertionError('Storage must not be written without prepare')
            monkeypatch.setattr(store, 'put', denied_put)
            assert (await admin.post(endpoint, **upload(ids))).status_code == 404
    finally:
        set_store(None)


async def test_archive_retains_immutable_original_and_audit_and_requires_live_prepare(migrator_session, scope_seed, tmp_path):
    from app.core.storage import LocalFsDocumentStore, set_store
    from sqlalchemy.exc import DBAPIError
    set_store(LocalFsDocumentStore(tmp_path))
    ids = await document_fixture(migrator_session, scope_seed)
    endpoint = f"/api/mandates/{ids['mandate']}/documents"
    try:
        async with client(ids) as api:
            result = await api.post(endpoint, **upload(ids))
            ids['document'] = uuid.UUID(result.json()['id'])
            path = endpoint + '/' + str(ids['document'])
            assert (await api.delete(path)).status_code == 409
            original = await read_value('SELECT to_jsonb(d) FROM mandate_document d WHERE id=:document', ids)
            for sql in ["UPDATE mandate_document SET title='Rewritten' WHERE id=:document RETURNING id",
                        'UPDATE mandate_document SET accounting_scope_id=NULL WHERE id=:document RETURNING id',
                        'DELETE FROM mandate_document WHERE id=:document RETURNING id']:
                with pytest.raises(DBAPIError):
                    await read_value(sql, ids, actor_key='clerk')
            archived = await api.post(path + '/archive', json={'reason': 'Replaced by reviewed contract'})
            assert archived.status_code == 200, archived.text
            assert archived.json()['archived_at']
            assert (await api.post(path + '/archive', json={'reason': 'Replaced by reviewed contract'})).json() == archived.json()
            assert (await api.post(path + '/archive', json={'reason': 'Changed reason'})).status_code == 409
            assert (await api.get(endpoint)).json() == []
            assert (await api.get(endpoint, params={'include_archived': True})).json()[0]['id'] == str(ids['document'])
        after = await read_value('SELECT to_jsonb(d) FROM mandate_document d WHERE id=:document', ids)
        for field in original:
            if field not in {'archived_at','archived_by','archive_reason','updated_at'}:
                assert after[field] == original[field]
        from sqlalchemy.ext.asyncio import create_async_engine
        from tests.conftest import APP_RW_URL
        engine = create_async_engine(APP_RW_URL)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
                    {'org': str(ids['org']), 'actor': str(ids['clerk'])})
                for scope, count in [(ids['source_scope'], 2), (ids['scope'], 0)]:
                    await connection.execute(text("SELECT set_config('app.current_scope_id',:scope,true)"), {'scope': str(scope)})
                    assert await connection.scalar(text('SELECT count(*) FROM audit_event WHERE entity_id=:document'), ids) == count
        finally:
            await engine.dispose()
        assert after['archived_by'] == str(ids['clerk'])
        assert await read_value("SELECT count(*) FROM audit_event WHERE entity_id=:document AND action='mandate_document.archived'", ids, actor_key='clerk') == 1
        assert await read_value("SELECT count(*) FROM audit_event WHERE entity_id=:document", ids, actor_key='actor') == 0
        with pytest.raises(DBAPIError):
            await read_value('UPDATE mandate_document SET archived_at=NULL,archived_by=NULL,archive_reason=NULL WHERE id=:document RETURNING id', ids, actor_key='clerk')
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:source_scope AND revoked_at IS NULL'), ids)
        async with client(ids) as api:
            assert (await api.post(path + '/archive', json={'reason': 'Replaced by reviewed contract'})).status_code == 404
            assert (await api.get(endpoint, params={'include_archived': True})).json() == []
    finally:
        set_store(None)


async def test_export_and_pause_recheck_while_legacy_documents_remain_compatible(migrator_session, scope_seed, tmp_path):
    from app.core.storage import LocalFsDocumentStore, set_store
    from tests.test_scope_control import change
    set_store(LocalFsDocumentStore(tmp_path))
    ids = await document_fixture(migrator_session, scope_seed)
    endpoint = f"/api/mandates/{ids['mandate']}/documents"
    try:
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:source_scope AND revoked_at IS NULL'), ids)
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:source_scope,:clerk,ARRAY['read','prepare','export'],:actor,'Export review')"), ids)
        async with client(ids) as api:
            doc = (await api.post(endpoint, **upload(ids))).json()
            path = endpoint + '/' + doc['id']
            assert (await api.get(path + '/download')).content == b'%PDF-1.4 synthetic scoped mandate'
            wrong = upload({**ids, 'source_scope': ids['scope']})
            assert (await api.post(endpoint, **wrong)).status_code == 422
            assert (await api.get(endpoint, params={'accounting_scope_id': str(ids['scope'])})).status_code == 404
            assert (await api.get(endpoint, params={'accounting_scope_id': str(ids['source_scope']), 'scope_reason': 'Reviewed content belongs to the selected source scope'})).json()[0]['id'] == doc['id']
            assert (await change(ids)).status_code == 201
            assert (await api.post(endpoint, **upload(ids))).status_code == 404
            assert (await api.post(path + '/archive', json={'reason': 'Paused bypass'})).status_code == 404
            assert (await api.get(path + '/download')).status_code == 200
            legacy = await api.post(endpoint, **upload(ids, scoped=False))
            assert legacy.status_code == 201, legacy.text
            assert (await api.delete(endpoint + '/' + legacy.json()['id'])).status_code == 204
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:source_scope AND revoked_at IS NULL'), ids)
        async with client(ids) as api:
            assert (await api.get(path + '/download')).status_code == 404
    finally:
        set_store(None)


async def test_expiry_during_storage_cannot_commit_scoped_metadata(migrator_session, scope_seed, tmp_path, monkeypatch):
    import time
    from app.core.storage import LocalFsDocumentStore, set_store
    store = LocalFsDocumentStore(tmp_path)
    set_store(store)
    ids = await document_fixture(migrator_session, scope_seed)
    endpoint = f"/api/mandates/{ids['mandate']}/documents"
    try:
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:source_scope AND revoked_at IS NULL'), ids)
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason,expires_at) VALUES(:org,:source_scope,:clerk,ARRAY['read','prepare'],:actor,'Short source grant',clock_timestamp()+interval '1 second')"), ids)
        original_put = store.put
        calls = []
        def delayed_put(*args, **kwargs):
            calls.append(True)
            result = original_put(*args, **kwargs)
            time.sleep(1.1)
            return result
        monkeypatch.setattr(store, 'put', delayed_put)
        async with client(ids) as api:
            response = await api.post(endpoint, **upload(ids))
            assert response.status_code == 404, response.text
        assert calls == [True]
        assert await read_value('SELECT count(*) FROM mandate_document WHERE management_mandate_id=:mandate', ids) == 0
    finally:
        set_store(None)


async def test_database_context_key_original_storage_and_populated_downgrade(migrator_session, scope_seed, tmp_path):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from app.core.storage import LocalFsDocumentStore, set_store
    set_store(LocalFsDocumentStore(tmp_path))
    ids = await document_fixture(migrator_session, scope_seed)
    try:
        async with client(ids) as api:
            response = await api.post(f"/api/mandates/{ids['mandate']}/documents", **upload(ids))
            assert response.status_code == 201, response.text
            ids['document'] = uuid.UUID(response.json()['id'])
        # A direct app writer cannot claim a different mandate or storage tenant.
        ids['wrong_mandate'] = uuid.uuid4()
        await mutate(text("INSERT INTO management_mandate(id,organization_id,mandate_type,label,start_date) VALUES(:wrong_mandate,:org,'WEG','Other','2024-01-01')"), ids)
        template = """INSERT INTO mandate_document(organization_id,management_mandate_id,accounting_scope_id,scope_reason,
          doc_type,title,original_filename,mime_type,content_byte_size,content_sha256,storage_uri,storage_backend,uploaded_by)
          SELECT organization_id,{mandate},accounting_scope_id,scope_reason,doc_type,title,original_filename,mime_type,
            content_byte_size,content_sha256,{uri},storage_backend,uploaded_by FROM mandate_document WHERE id=:document RETURNING id"""
        for mandate, uri in [(':wrong_mandate', 'storage_uri'), ('management_mandate_id', "'foreign/'||content_sha256||'.bin'")]:
            with pytest.raises(DBAPIError):
                await read_value(template.format(mandate=mandate, uri=uri), ids, actor_key='clerk')
        spec = importlib.util.spec_from_file_location('mandate_scope_migration', Path(__file__).parents[1] / 'migrations/versions/0042_scoped_mandate_documents.py')
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine(MIGRATOR_SYNC_URL)
        try:
            with engine.connect() as connection, connection.begin() as transaction:
                with pytest.raises(DBAPIError, match='Preserve classified mandate originals'):
                    with connection.begin_nested(), Operations.context(MigrationContext.configure(connection)):
                        migration.downgrade()
                connection.execute(text('TRUNCATE mandate_document'))
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
                    migration.upgrade()
                transaction.rollback()
            assert await read_value('SELECT count(*) FROM mandate_document WHERE id=:document', ids) == 1
        finally:
            engine.dispose()
    finally:
        set_store(None)
