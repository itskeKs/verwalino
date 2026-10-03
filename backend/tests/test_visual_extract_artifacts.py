"""Private derivative receipts bind trusted bytes and retained source authority."""
import hashlib
import uuid

import pytest
from sqlalchemy import text

from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store, fixture
from tests.test_scoped_account_statements import client, mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_logical_documents import clean_reference
from tests.test_source_partitions import candidates, command

pytestmark = pytest.mark.asyncio


async def basis(api, ids):
    await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'), ids)
    await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:scope,:clerk,ARRAY['read','prepare','export'],:actor,'Explicit visual export fixture')"), ids)
    ref, original = await clean_reference(api, ids, scoped=True)
    whole, first, rest, *_ = await candidates(api, ref)
    partition = await api.post('/api/document-refs/' + ref['id'] + '/partition', **command([first, rest]))
    assert partition.status_code == 201, partition.text
    body = {'partition_revision_id': partition.json()['id'], 'reason': 'Prepare exact visible pages for disclosure review'}
    return ref, original, first, whole, body


async def test_private_artifact_exact_bytes_retry_and_current_partition(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        ref, _, first, whole, body = await basis(api, ids)
        url = '/api/logical-documents/' + first['id'] + '/visual-extracts'
        headers = {'Idempotency-Key': str(uuid.uuid4())}
        reply = await api.post(url, json=body, headers=headers)
        assert reply.status_code == 201, reply.text
        receipt = reply.json()
        assert receipt['page_start'] == receipt['page_end'] == receipt['output_page_count'] == 1
        assert receipt['source_sha256'] == ref['content_sha256']
        assert not {'storage_uri', 'storage_version', 'storage_backend'}.intersection(receipt)
        download = await api.get('/api/visual-extracts/' + receipt['id'] + '/content')
        assert download.status_code == 200, download.text
        assert hashlib.sha256(download.content).hexdigest() == receipt['content_sha256']
        assert download.headers['cache-control'] == 'private, no-store'
        assert (await api.post(url, json=body, headers=headers)).json() == receipt
        changed = await api.post('/api/document-refs/' + ref['id'] + '/partition', **command([whole], previous=body['partition_revision_id']))
        assert changed.status_code == 201, changed.text
        assert (await api.post(url, json=body, headers=headers)).json() == receipt
        assert (await api.post(url, json=body, headers={'Idempotency-Key': str(uuid.uuid4())})).status_code == 409
        assert (await api.post(url, json={**body, 'reason': 'Changed command'}, headers=headers)).status_code == 409
        history = await api.get(url)
        assert history.status_code == 200 and history.json()['items'] == [receipt]
    assert await read_value('SELECT count(*) FROM visual_extract_artifact WHERE organization_id=:org', ids) == 1
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org', ids) == 0


async def test_private_artifact_custody_scope_and_changed_storage(migrator_session, scope_seed, store, client_b_admin):
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api, client(ids, 'actor') as admin:
        _, _, first, _, body = await basis(api, ids)
        url = '/api/logical-documents/' + first['id'] + '/visual-extracts'
        headers = {'Idempotency-Key': str(uuid.uuid4())}
        reply = await api.post(url, json=body, headers=headers)
        assert reply.status_code == 201, reply.text
        receipt = reply.json()
        content = '/api/visual-extracts/' + receipt['id'] + '/content'
        for denied in (admin, client_b_admin):
            assert (await denied.get(content)).status_code == 404
            assert (await denied.get(url)).status_code == 404
            assert (await denied.post(url, json=body, headers=headers)).status_code == 404
        uri = await read_value('SELECT storage_uri FROM visual_extract_artifact WHERE id=:target', {**ids, 'target': uuid.UUID(receipt['id'])})
        path = store._resolve_key(uri)
        path.chmod(0o600); path.write_bytes(b'changed output')
        assert (await api.get(content)).status_code == 409
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND revoked_at IS NULL'), ids)
        assert (await api.get(content)).status_code == 404
        assert (await api.post(url, json=body, headers=headers)).status_code == 404


async def test_same_request_race_pagination_and_invalid_member(migrator_session, scope_seed, store):
    import asyncio
    from sqlalchemy.exc import DBAPIError
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        _, _, first, whole, body = await basis(api, ids)
        url = '/api/logical-documents/' + first['id'] + '/visual-extracts'
        headers = {'Idempotency-Key': str(uuid.uuid4())}
        replies = await asyncio.gather(*(api.post(url, json=body, headers=headers) for _ in range(2)))
        assert [r.status_code for r in replies] == [201, 201], [r.text for r in replies]
        assert replies[0].json() == replies[1].json()
        assert await read_value('SELECT count(*) FROM visual_extract_artifact WHERE organization_id=:org', ids) == 1
        bad = await api.post('/api/logical-documents/' + whole['id'] + '/visual-extracts', json=body, headers={'Idempotency-Key': str(uuid.uuid4())})
        assert bad.status_code == 422, bad.text
        second = await api.post(url, json=body, headers={'Idempotency-Key': str(uuid.uuid4())})
        assert second.status_code == 201, second.text
        page = (await api.get(url, params={'limit': 1})).json()
        assert len(page['items']) == 1 and page['next_cursor']
        next_page = (await api.get(url, params={'limit': 1, 'cursor': page['next_cursor']})).json()
        assert next_page['items'][0]['id'] != page['items'][0]['id'] and next_page['next_cursor'] is None
        assert (await api.get('/api/logical-documents/' + whole['id'] + '/visual-extracts', params={'cursor': page['next_cursor']})).status_code == 422
        params = {**ids, 'logical': uuid.UUID(first['id'])}
        with pytest.raises(DBAPIError, match='Bounded history'):
            await read_value('SELECT visual_extract_history(:org,:logical,NULL,NULL,NULL)', params, actor_key='clerk')


async def test_no_export_grant_and_corrupt_original_fail_before_render(migrator_session, scope_seed, store, monkeypatch):
    from app.services import visual_extract_service
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        _, original, first, _, body = await basis(api, ids)
        url = '/api/logical-documents/' + first['id'] + '/visual-extracts'
        def unexpected(*args, **kwargs):
            pytest.fail('Renderer invoked for unauthorized or corrupt source')
        monkeypatch.setattr(visual_extract_service, 'run_isolated_visual_extract', unexpected)
        uri = await read_value('SELECT storage_uri FROM intake_original WHERE id=:target', {**ids, 'target': uuid.UUID(original['original_id'])})
        path = store._resolve_key(uri)
        path.chmod(0o600); path.write_bytes(b'changed original')
        assert (await api.post(url, json=body, headers={'Idempotency-Key': str(uuid.uuid4())})).status_code == 409
        await mutate(text("UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND 'export'=ANY(capabilities) AND revoked_at IS NULL"), ids)
        assert (await api.post(url, json=body, headers={'Idempotency-Key': str(uuid.uuid4())})).status_code == 404
        assert (await api.get(url)).status_code == 404
    assert await read_value('SELECT count(*) FROM visual_extract_artifact WHERE organization_id=:org', ids) == 0


async def test_completion_rollback_and_cross_transaction_proof_rejection(migrator_session, scope_seed, store, monkeypatch):
    from fastapi import HTTPException
    from sqlalchemy.exc import DBAPIError
    from app.services.visual_extract_service import VisualExtractService
    ids = await fixture(migrator_session, scope_seed)
    original_call = VisualExtractService.call
    captured = {}
    async def fail_completion(self, sql, **params):
        if sql.startswith('SELECT complete_visual_extract('):
            captured.update(params)
            raise HTTPException(503, 'Injected completion interruption')
        return await original_call(self, sql, **params)
    async with client(ids) as api:
        _, _, first, _, body = await basis(api, ids)
        url = '/api/logical-documents/' + first['id'] + '/visual-extracts'
        headers = {'Idempotency-Key': str(uuid.uuid4())}
        with monkeypatch.context() as patch:
            patch.setattr(VisualExtractService, 'call', fail_completion)
            assert (await api.post(url, json=body, headers=headers)).status_code == 503
        assert captured
        assert await read_value('SELECT count(*) FROM visual_extract_artifact WHERE organization_id=:org', ids) == 0
        assert await read_value('SELECT count(*) FROM visual_extract_attestation WHERE organization_id=:org', ids) == 1
        with pytest.raises(DBAPIError, match='Current transaction renderer proof required'):
            await read_value('SELECT complete_visual_extract(:org,:logical,:partition,:request,:reason,:proof)', {**ids, **captured}, actor_key='clerk')
        with pytest.raises(DBAPIError, match='permission denied'):
            await read_value("SELECT attest_visual_extract(:org,:request,:digest,1,'1','{}')", {**ids, 'request': uuid.uuid4(), 'digest': 'a'*64}, actor_key='clerk')
        for table in ('visual_extract_artifact', 'visual_extract_attestation'):
            with pytest.raises(DBAPIError, match='permission denied'):
                await read_value(f'SELECT * FROM {table}', ids, actor_key='clerk')
        retry = await api.post(url, json=body, headers=headers)
        assert retry.status_code == 201, retry.text
        with pytest.raises(DBAPIError, match='immutable|Immutable'):
            await mutate(text('UPDATE visual_extract_artifact SET reason=\'changed\' WHERE organization_id=:org'), ids)


async def test_storage_readback_failure_never_attests(migrator_session, scope_seed, store, monkeypatch):
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        _, _, first, _, body = await basis(api, ids)
        original_put = store.put
        def damaged_put(*args, **kwargs):
            ref = original_put(*args, **kwargs)
            path = store._resolve_key(ref.uri)
            path.chmod(0o600); path.write_bytes(b'broken stored derivative')
            return ref
        monkeypatch.setattr(store, 'put', damaged_put)
        reply = await api.post('/api/logical-documents/' + first['id'] + '/visual-extracts', json=body, headers={'Idempotency-Key': str(uuid.uuid4())})
        assert reply.status_code == 409, reply.text
    assert await read_value('SELECT count(*) FROM visual_extract_attestation WHERE organization_id=:org', ids) == 0
    assert await read_value('SELECT count(*) FROM visual_extract_artifact WHERE organization_id=:org', ids) == 0


async def test_migration_empty_roundtrip_retention_manifest_and_private_roles(migrator_session, scope_seed, store):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_access_coverage import capture
    ids = await fixture(migrator_session, scope_seed)
    migration = importlib.import_module('migrations.versions.0117_visual_extract_artifacts')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            before = db.scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
            point = db.begin_nested()
            try:
                db.execute(text('TRUNCATE visual_extract_attestation CASCADE'))
                db.execute(text('TRUNCATE allocation_source_claim CASCADE'))
                importlib.import_module('migrations.versions.0120_allocation_source_aliases').downgrade()
                importlib.import_module('migrations.versions.0119_allocation_source_claims').downgrade()
                importlib.import_module('migrations.versions.0118_visual_disclosures').downgrade()
                migration.downgrade(); migration.upgrade()
                importlib.import_module('migrations.versions.0118_visual_disclosures').upgrade()
                importlib.import_module('migrations.versions.0119_allocation_source_claims').upgrade()
                importlib.import_module('migrations.versions.0120_allocation_source_aliases').upgrade()
                assert before == db.scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
            finally:
                point.rollback()
            for role in ('app_rw', 'app_worker', 'app_review'):
                for table in ('visual_extract_artifact', 'visual_extract_attestation'):
                    assert not db.scalar(text("SELECT has_table_privilege(:role,:table,'SELECT,INSERT,UPDATE,DELETE')"), {'role': role, 'table': table})
                assert not db.scalar(text("SELECT has_function_privilege(:role,'visual_extract_result(uuid,uuid)','EXECUTE')"), {'role': role})
            # app_worker inherits app_rw; the session_user boundary is exercised
            # through actual runtime connections below, not ACL metadata alone.
        async with client(ids) as api, client(ids, 'actor') as admin:
            _, _, first, _, body = await basis(api, ids)
            before = (await capture(admin))['manifest_digest']
            reply = await api.post('/api/logical-documents/' + first['id'] + '/visual-extracts', json=body, headers={'Idempotency-Key': str(uuid.uuid4())})
            assert reply.status_code == 201, reply.text
            assert (await capture(admin))['manifest_digest'] != before
            from app.core.db import get_worker_session_factory, get_review_session_factory, set_tenant_context
            for factory in (get_worker_session_factory(), get_review_session_factory()):
                for sql in ('SELECT visual_extract_history(:org,:logical,NULL,NULL,10)',
                            'SELECT visual_extract_content(:org,:target)',
                            "SELECT prepare_visual_extract(:org,:logical,:partition,:request,'Private preparation')"):
                    with pytest.raises(DBAPIError, match='permission denied|Interactive runtime required'):
                        async with factory() as runtime, runtime.begin():
                            await set_tenant_context(runtime, ids['org'])
                            await runtime.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(ids['clerk'])})
                            await runtime.execute(text(sql), {'org': ids['org'], 'logical': uuid.UUID(first['id']),
                                'target': uuid.UUID(reply.json()['id']), 'partition': uuid.UUID(body['partition_revision_id']), 'request': uuid.uuid4()})
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError, match='Retain visual extract'), db.begin_nested():
                db.execute(text('TRUNCATE visual_disclosure_attestation CASCADE'))
                db.execute(text('TRUNCATE allocation_source_claim CASCADE'))
                db.execute(text('TRUNCATE allocation_set CASCADE'))
                importlib.import_module('migrations.versions.0120_allocation_source_aliases').downgrade()
                importlib.import_module('migrations.versions.0119_allocation_source_claims').downgrade()
                importlib.import_module('migrations.versions.0118_visual_disclosures').downgrade()
                migration.downgrade()
            with pytest.raises(DBAPIError, match='immutable|Immutable'), db.begin_nested():
                db.execute(text('DELETE FROM visual_extract_attestation WHERE organization_id=:org'), ids)
    finally:
        engine.dispose()


@pytest.mark.parametrize('change', ['actor', 'pid', 'transaction', 'digest', 'source_hash', 'page_range'])
async def test_mismatched_trusted_proof_never_creates_artifact(migrator_session, scope_seed, store, monkeypatch, change):
    import json
    from app.services.intake_service import IntakeService
    ids = await fixture(migrator_session, scope_seed)
    original_call = IntakeService.call
    async def changed_proof(self, sql, **params):
        if sql.startswith('SELECT attest_visual_extract('):
            if change == 'actor':
                await self.db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(ids['actor'])})
            elif change == 'pid': params['pid'] += 1
            elif change == 'transaction': params['transaction'] = str(int(params['transaction']) + 1)
            elif change == 'digest': params['digest'] = 'b' * 64
            else:
                payload = json.loads(params['payload'])
                if change == 'source_hash': payload['source_sha256'] = 'b' * 64
                else: payload['page_start'] = 2
                params['payload'] = json.dumps(payload)
        return await original_call(self, sql, **params)
    async with client(ids) as api:
        _, _, first, _, body = await basis(api, ids)
        monkeypatch.setattr(IntakeService, 'call', changed_proof)
        response = await api.post('/api/logical-documents/' + first['id'] + '/visual-extracts', json=body, headers={'Idempotency-Key': str(uuid.uuid4())})
        assert response.status_code == 422, response.text
    assert await read_value('SELECT count(*) FROM visual_extract_artifact WHERE organization_id=:org', ids) == 0
