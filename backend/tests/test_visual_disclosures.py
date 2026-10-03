"""Destination-only disclosure never conveys original custody or private notes."""
import hashlib
import uuid

import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store, fixture
from tests.test_scoped_account_statements import client, mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_visual_extract_artifacts import basis

pytestmark = pytest.mark.asyncio


async def prepared(api, ids):
    ref, original, logical, whole, create = await basis(api, ids)
    response = await api.post('/api/logical-documents/' + logical['id'] + '/visual-extracts',
                             json=create, headers={'Idempotency-Key': str(uuid.uuid4())})
    assert response.status_code == 201, response.text
    ids['destination'] = uuid.uuid4()
    await mutate(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES(:destination,:org,:other_property,'MIET','Destination only','2025-01-01',true,'REVIEWED',:actor)"), ids)
    for actor, caps in [('clerk', ['read', 'prepare', 'approve', 'export']), ('actor', ['read', 'approve', 'export'])]:
        await mutate(text('INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:destination,:subject,:caps,:actor,\'Explicit disclosure fixture\')'),
                     {**ids, 'subject': ids[actor], 'caps': caps})
    return response.json(), ref, original, logical, whole


def publish_command(artifact, ids, expected=None, key=None):
    return {'json': {'scope_id': str(ids['destination']), 'partition_revision_id': artifact['partition_revision_id'],
                    'content_sha256': artifact['content_sha256'], 'reason': 'PRIVATE DISCLOSURE REVIEW NOTE', 'visible_content_reviewed': True},
            'headers': {'Idempotency-Key': key or str(uuid.uuid4()), 'If-Match': '"' + (expected or 'none') + '"'}}


async def test_destination_only_reads_exact_bytes_without_original_or_private_metadata(migrator_session, scope_seed, store, client_b_admin):
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as source, client(ids, 'actor') as destination:
        artifact, _, original, logical, _ = await prepared(source, ids)
        request = publish_command(artifact, ids)
        response = await source.post('/api/visual-extracts/' + artifact['id'] + '/disclosures', **request)
        assert response.status_code == 201, response.text
        published = response.json()
        assert published['receipt']['kind'] == 'published' and published['current']['available']
        target = published['receipt']['id']
        private_tokens = ('PRIVATE DISCLOSURE REVIEW NOTE', artifact['id'], artifact['source_sha256'], logical['id'], 'storage_uri', 'renderer_profile', 'verified_page_count')
        for api in (source, destination):
            detail = await api.get('/api/visual-disclosures/' + target)
            assert detail.status_code == 200, detail.text
            assert all(token not in detail.text for token in private_tokens)
            content = await api.get('/api/visual-disclosures/' + target + '/content')
            assert content.status_code == 200, content.text
            assert hashlib.sha256(content.content).hexdigest() == artifact['content_sha256']
        assert (await destination.get('/api/visual-extracts/' + artifact['id'] + '/content')).status_code == 404
        assert (await destination.get('/api/intake-batches/originals/' + original['original_id'] + '/content')).status_code == 404
        assert (await client_b_admin.get('/api/visual-disclosures/' + target + '/content')).status_code == 404
        listed = await destination.get('/api/accounting-scopes/' + str(ids['destination']) + '/visual-disclosures')
        assert listed.status_code == 200 and len(listed.json()['items']) == 1
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org', ids) == 0


async def test_withdrawal_does_not_reactivate_on_historical_publish_retry(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as source, client(ids, 'actor') as destination:
        artifact, *_ = await prepared(source, ids)
        url = '/api/visual-extracts/' + artifact['id'] + '/disclosures'
        command = publish_command(artifact, ids)
        first = await source.post(url, **command)
        assert first.status_code == 201, first.text
        original = first.json()['receipt']
        withdrawal = {'json': {'reason': 'Destination review withdrew access', 'withdrawal_confirmed': True},
                      'headers': {'Idempotency-Key': str(uuid.uuid4()), 'If-Match': '"' + original['id'] + '"'}}
        path = '/api/visual-disclosures/' + original['id'] + '/withdrawals'
        response = await destination.post(path, **withdrawal)
        assert response.status_code == 201, response.text
        withdrawn = response.json()
        assert withdrawn['receipt']['kind'] == 'withdrawn' and not withdrawn['current']['available']
        assert artifact['id'] not in response.text and 'PRIVATE DISCLOSURE' not in response.text
        assert (await destination.post(path, **withdrawal)).json() == withdrawn
        replay = (await source.post(url, **command)).json()
        assert replay['receipt'] == original and not replay['current']['available']
        assert (await destination.get('/api/visual-disclosures/' + original['id'] + '/content')).status_code == 404
        assert (await source.get('/api/visual-disclosures/' + withdrawn['receipt']['id'] + '/content')).status_code == 404
        conflict = await source.post(url, **publish_command(artifact, ids))
        assert conflict.status_code == 409, conflict.text
        republished = await source.post(url, **publish_command(artifact, ids, expected=withdrawn['receipt']['id']))
        assert republished.status_code == 201, republished.text
        assert republished.json()['current']['available']
        assert (await destination.get('/api/visual-disclosures/' + original['id'] + '/content')).status_code == 404


async def test_source_only_withdrawal_survives_lost_destination_changed_inspection_and_pause(migrator_session, scope_seed, store):
    from tests.test_property_source_enforcement import synthetic_enforcement, change_mode
    from tests.test_source_partitions import command as partition_command
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as source, client(ids, 'actor') as destination:
        artifact, ref, original, _, whole = await prepared(source, ids)
        publish = await source.post('/api/visual-extracts/' + artifact['id'] + '/disclosures', **publish_command(artifact, ids))
        assert publish.status_code == 201, publish.text
        target = publish.json()['receipt']['id']
        changed = await source.post('/api/document-refs/' + ref['id'] + '/partition', **partition_command([whole], previous=artifact['partition_revision_id']))
        assert changed.status_code == 201, changed.text
        assert (await destination.get('/api/visual-disclosures/' + target + '/content')).status_code == 200
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:destination AND user_id=:clerk AND revoked_at IS NULL'), ids)
        await mutate(text("UPDATE workflow_job SET status='blocked' WHERE organization_id=:org AND original_id=:original"), {**ids, 'original': uuid.UUID(original['original_id'])})
        async with synthetic_enforcement(scope_seed):
            await change_mode(scope_seed, 'PAUSED')
            managed = await source.get('/api/visual-extracts/' + artifact['id'] + '/disclosures')
            assert managed.status_code == 200, managed.text
            assert managed.json()['items'][0]['receipt']['scope_id'] is None
            assert str(ids['destination']) not in managed.text
            outgoing = await source.get('/api/visual-disclosures/outgoing')
            assert outgoing.status_code == 200 and outgoing.json()['items'] == managed.json()['items']
            assert (await destination.get('/api/visual-disclosures/outgoing')).json()['items'] == []
            result = await source.post('/api/visual-disclosures/' + target + '/withdrawals',
                json={'reason': 'Withdraw despite inaccessible destination and changed source', 'withdrawal_confirmed': True},
                headers={'Idempotency-Key': str(uuid.uuid4()), 'If-Match': '"' + target + '"'})
            assert result.status_code == 201, result.text
            assert result.json()['receipt']['scope_id'] is None and not result.json()['current']['available']
            assert (await destination.get('/api/visual-disclosures/' + target + '/content')).status_code == 404


async def test_new_publication_rejects_stale_partition_hash_and_corrupt_storage(migrator_session, scope_seed, store):
    from tests.test_source_partitions import command as partition_command
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as source:
        artifact, ref, _, _, whole = await prepared(source, ids)
        url = '/api/visual-extracts/' + artifact['id'] + '/disclosures'
        command = publish_command(artifact, ids)
        command['json']['content_sha256'] = '0' * 64
        assert (await source.post(url, **command)).status_code == 422
        uri = await read_value('SELECT storage_uri FROM visual_extract_artifact WHERE id=:target', {**ids, 'target': uuid.UUID(artifact['id'])})
        path = store._resolve_key(uri); original = path.read_bytes()
        path.chmod(0o600); path.write_bytes(b'altered derivative')
        assert (await source.post(url, **publish_command(artifact, ids))).status_code == 409
        path.write_bytes(original); path.chmod(0o400)
        changed = await source.post('/api/document-refs/' + ref['id'] + '/partition', **partition_command([whole], previous=artifact['partition_revision_id']))
        assert changed.status_code == 201, changed.text
        stale = await source.post(url, **publish_command(artifact, ids))
        assert stale.status_code == 409, stale.text
    assert await read_value('SELECT count(*) FROM visual_disclosure_event WHERE organization_id=:org', ids) == 0
    assert await read_value('SELECT count(*) FROM visual_disclosure_attestation WHERE organization_id=:org', ids) == 0


async def test_races_exact_replay_and_destination_export_revocation(migrator_session, scope_seed, store):
    import asyncio
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as source, client(ids, 'actor') as destination:
        artifact, *_ = await prepared(source, ids)
        url = '/api/visual-extracts/' + artifact['id'] + '/disclosures'
        command = publish_command(artifact, ids)
        results = await asyncio.gather(*(source.post(url, **command) for _ in range(2)))
        assert [r.status_code for r in results] == [201, 201], [r.text for r in results]
        assert results[0].json() == results[1].json()
        target = results[0].json()['receipt']['id']
        assert await read_value('SELECT count(*) FROM visual_disclosure_event WHERE organization_id=:org', ids) == 1
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:destination AND user_id=:actor AND revoked_at IS NULL'), ids)
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:destination,:actor,ARRAY['read','approve'],:actor,'Read and withdrawal only')"), ids)
        detail = await destination.get('/api/visual-disclosures/' + target)
        assert detail.status_code == 200 and detail.json()['current']['available'] and not detail.json()['current']['can_download']
        assert (await destination.get('/api/visual-disclosures/' + target + '/content')).status_code == 404
        withdrawal = {'json': {'reason': 'Reviewed withdrawal', 'withdrawal_confirmed': True},
                      'headers': {'Idempotency-Key': str(uuid.uuid4()), 'If-Match': '"' + target + '"'}}
        competing = {'json': withdrawal['json'], 'headers': {**withdrawal['headers'], 'Idempotency-Key': str(uuid.uuid4())}}
        results = await asyncio.gather(destination.post('/api/visual-disclosures/' + target + '/withdrawals', **withdrawal),
                                       source.post('/api/visual-disclosures/' + target + '/withdrawals', **competing))
        assert sorted(r.status_code for r in results) == [201, 409], [r.text for r in results]
        assert (await destination.get('/api/accounting-scopes/' + str(ids['destination']) + '/visual-disclosures')).json()['items'] == []
        # A current source publication retry cannot use a stale destination grant.
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:destination AND user_id=:clerk AND revoked_at IS NULL'), ids)
        assert (await source.post(url, **command)).status_code == 404


async def test_strict_acknowledgments_and_expired_destination_grant(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as source, client(ids, 'actor') as destination:
        artifact, *_ = await prepared(source, ids)
        url = '/api/visual-extracts/' + artifact['id'] + '/disclosures'
        for value in (1, 1.0, 'true', '1', False, None):
            command = publish_command(artifact, ids); command['json']['visible_content_reviewed'] = value
            assert (await source.post(url, **command)).status_code == 422
        published = await source.post(url, **publish_command(artifact, ids))
        assert published.status_code == 201, published.text
        target = published.json()['receipt']['id']
        for value in (1, 1.0, 'true', '1', False, None):
            response = await destination.post('/api/visual-disclosures/' + target + '/withdrawals',
                json={'reason': 'Explicit review required', 'withdrawal_confirmed': value},
                headers={'Idempotency-Key': str(uuid.uuid4()), 'If-Match': '"' + target + '"'})
            assert response.status_code == 422
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:destination AND user_id=:actor AND revoked_at IS NULL'), ids)
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason,created_at,expires_at) VALUES(:org,:destination,:actor,ARRAY['read','approve','export'],:actor,'Expired destination grant',clock_timestamp()-interval '2 days',clock_timestamp()-interval '1 day')"), ids)
        assert (await destination.get('/api/visual-disclosures/' + target)).status_code == 404
        assert (await destination.get('/api/visual-disclosures/' + target + '/content')).status_code == 404
        denied = await destination.post('/api/visual-disclosures/' + target + '/withdrawals',
            json={'reason': 'Expired grant cannot withdraw', 'withdrawal_confirmed': True},
            headers={'Idempotency-Key': str(uuid.uuid4()), 'If-Match': '"' + target + '"'})
        assert denied.status_code == 404


async def test_interrupted_completion_proof_cannot_cross_transactions(migrator_session, scope_seed, store, monkeypatch):
    from fastapi import HTTPException
    from sqlalchemy.exc import DBAPIError
    from app.services.visual_disclosure_service import VisualDisclosureService
    ids = await fixture(migrator_session, scope_seed)
    original_call = VisualDisclosureService.call
    captured = {}
    async def fail_publish(self, sql, **params):
        if sql.startswith('SELECT publish_visual_disclosure('):
            captured.update(params)
            raise HTTPException(503, 'Injected publication interruption')
        return await original_call(self, sql, **params)
    async with client(ids) as source:
        artifact, *_ = await prepared(source, ids)
        command = publish_command(artifact, ids)
        with monkeypatch.context() as patch:
            patch.setattr(VisualDisclosureService, 'call', fail_publish)
            failed = await source.post('/api/visual-extracts/' + artifact['id'] + '/disclosures', **command)
            assert failed.status_code == 503, failed.text
        assert await read_value('SELECT count(*) FROM visual_disclosure_event WHERE organization_id=:org', ids) == 0
        assert await read_value('SELECT count(*) FROM visual_disclosure_attestation WHERE organization_id=:org', ids) == 1
        with pytest.raises(DBAPIError, match='Current transaction disclosure proof'):
            await read_value('SELECT publish_visual_disclosure(:org,:target,:destination,:partition,:expected,:hash,:request,:reason,:reviewed,:proof)', {**ids, **captured}, actor_key='clerk')
        retry = await source.post('/api/visual-extracts/' + artifact['id'] + '/disclosures', **command)
        assert retry.status_code == 201, retry.text


@pytest.mark.parametrize('change', ['actor', 'pid', 'transaction', 'digest', 'hash', 'size'])
async def test_mismatched_trusted_proof_never_publishes(migrator_session, scope_seed, store, monkeypatch, change):
    from app.services.intake_service import IntakeService
    ids = await fixture(migrator_session, scope_seed)
    original_call = IntakeService.call
    async def changed_proof(self, sql, **params):
        if sql.startswith('SELECT attest_visual_disclosure('):
            if change == 'actor':
                await self.db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(ids['actor'])})
            elif change == 'pid': params['pid'] += 1
            elif change == 'transaction': params['transaction'] = str(int(params['transaction']) + 1)
            elif change == 'digest': params['digest'] = 'b' * 64
            elif change == 'hash': params['hash'] = 'b' * 64
            else: params['size'] += 1
        return await original_call(self, sql, **params)
    async with client(ids) as source:
        artifact, *_ = await prepared(source, ids)
        monkeypatch.setattr(IntakeService, 'call', changed_proof)
        result = await source.post('/api/visual-extracts/' + artifact['id'] + '/disclosures', **publish_command(artifact, ids))
        assert result.status_code == 422, result.text
    assert await read_value('SELECT count(*) FROM visual_disclosure_event WHERE organization_id=:org', ids) == 0


async def test_raw_roles_migration_retention_and_manifest(migrator_session, scope_seed, store):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_access_coverage import capture
    from app.core.db import get_worker_session_factory, get_review_session_factory, set_tenant_context
    ids = await fixture(migrator_session, scope_seed)
    migration = importlib.import_module('migrations.versions.0118_visual_disclosures')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            before = db.scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
            point = db.begin_nested()
            try:
                importlib.import_module('migrations.versions.0122_allocation_source_replacements').downgrade()
                importlib.import_module('migrations.versions.0121_visual_allocation_sources').downgrade()
                db.execute(text('TRUNCATE visual_disclosure_attestation CASCADE'))
                db.execute(text('TRUNCATE allocation_source_claim CASCADE'))
                db.execute(text('TRUNCATE visual_extract_attestation CASCADE'))
                importlib.import_module('migrations.versions.0120_allocation_source_aliases').downgrade()
                importlib.import_module('migrations.versions.0119_allocation_source_claims').downgrade()
                migration.downgrade(); migration.upgrade()
                importlib.import_module('migrations.versions.0119_allocation_source_claims').upgrade()
                importlib.import_module('migrations.versions.0120_allocation_source_aliases').upgrade()
                importlib.import_module('migrations.versions.0121_visual_allocation_sources').upgrade()
                importlib.import_module('migrations.versions.0122_allocation_source_replacements').upgrade()
                assert before == db.scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
            finally: point.rollback()
            for role in ('app_rw', 'app_worker', 'app_review'):
                for table in ('visual_disclosure_event', 'visual_disclosure_attestation'):
                    assert not db.scalar(text("SELECT has_table_privilege(:role,:table,'SELECT,INSERT,UPDATE,DELETE')"), {'role': role, 'table': table})
                for function in ('visual_disclosure_result(uuid,uuid)', 'visual_disclosure_source_access(uuid,uuid)', 'visual_disclosure_withdraw_access(uuid,uuid,uuid)'):
                    assert not db.scalar(text("SELECT has_function_privilege(:role,:function,'EXECUTE')"), {'role': role, 'function': function})
        async with client(ids) as source, client(ids, 'actor') as admin:
            artifact, *_ = await prepared(source, ids)
            before = (await capture(admin))['manifest_digest']
            result = await source.post('/api/visual-extracts/' + artifact['id'] + '/disclosures', **publish_command(artifact, ids))
            assert result.status_code == 201, result.text
            assert (await capture(admin))['manifest_digest'] != before
            for factory in (get_worker_session_factory(), get_review_session_factory()):
                for sql in ('SELECT visual_disclosure_outgoing(:org,NULL,NULL,10)', 'SELECT visual_disclosure_detail(:org,:target)', 'SELECT visual_disclosure_content(:org,:target)'):
                    with pytest.raises(DBAPIError, match='permission denied|Interactive runtime required'):
                        async with factory() as runtime, runtime.begin():
                            await set_tenant_context(runtime, ids['org'])
                            await runtime.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(ids['clerk'])})
                            await runtime.execute(text(sql), {'org': ids['org'], 'target': uuid.UUID(result.json()['receipt']['id'])})
            with pytest.raises(DBAPIError, match='permission denied'):
                await read_value("SELECT attest_visual_disclosure(:org,:target,:request,:digest,1,'1',:hash,100)",
                    {**ids, 'target': uuid.UUID(artifact['id']), 'request': uuid.uuid4(), 'digest': 'a'*64, 'hash': artifact['content_sha256']}, actor_key='clerk')
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError, match='Retain visual disclosure'), db.begin_nested(): migration.downgrade()
            for table in ('visual_disclosure_event', 'visual_disclosure_attestation'):
                with pytest.raises(DBAPIError, match='immutable|Immutable'), db.begin_nested():
                    db.execute(text(f'DELETE FROM {table} WHERE organization_id=:org'), ids)
            # Even an orphaned trusted observation must prevent evidence destruction.
            with db.begin_nested() as point:
                importlib.import_module('migrations.versions.0122_allocation_source_replacements').downgrade()
                importlib.import_module('migrations.versions.0121_visual_allocation_sources').downgrade()
                db.execute(text('TRUNCATE visual_disclosure_event'))
                with pytest.raises(DBAPIError, match='Retain visual disclosure'), db.begin_nested(): migration.downgrade()
                point.rollback()
    finally: engine.dispose()


async def test_signed_pagination_and_raw_bounds(migrator_session, scope_seed, store):
    from sqlalchemy.exc import DBAPIError
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as source, client(ids, 'actor') as destination:
        artifact, *_ = await prepared(source, ids)
        for index in range(3):
            if index:
                ids['destination'] = uuid.uuid4()
                await mutate(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES(:destination,:org,:other_property,'MIET','Another destination','2025-01-01',true,'REVIEWED',:actor)"), ids)
                await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:destination,:clerk,ARRAY['read','prepare','approve','export'],:actor,'Explicit pagination fixture')"), ids)
            response = await source.post('/api/visual-extracts/' + artifact['id'] + '/disclosures', **publish_command(artifact, ids))
            assert response.status_code == 201, response.text
        url = '/api/visual-extracts/' + artifact['id'] + '/disclosures'
        first = (await source.get(url, params={'limit': 1})).json()
        assert len(first['items']) == 1 and first['next_cursor']
        second = (await source.get(url, params={'limit': 1, 'cursor': first['next_cursor']})).json()
        assert first['items'][0]['receipt']['id'] != second['items'][0]['receipt']['id']
        for api, path in ((destination, url), (source, '/api/visual-disclosures/outgoing'), (source, '/api/accounting-scopes/' + str(ids['destination']) + '/visual-disclosures')):
            assert (await api.get(path, params={'cursor': first['next_cursor']})).status_code == 422
        assert (await source.get(url, params={'cursor': first['next_cursor'] + 'x'})).status_code == 422
        for take in (None, 0, 51):
            for sql in ('SELECT visual_disclosure_outgoing(:org,NULL,NULL,:take)', 'SELECT visual_disclosure_list(:org,NULL,:target,NULL,NULL,:take)'):
                with pytest.raises(DBAPIError, match='Bounded'):
                    await read_value(sql, {**ids, 'target': uuid.UUID(artifact['id']), 'take': take}, actor_key='clerk')


async def test_last_publication_always_allows_withdrawal(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as source:
        artifact, *_ = await prepared(source, ids)
        url = '/api/visual-extracts/' + artifact['id'] + '/disclosures'
        expected = None
        for _ in range(50):
            result = await source.post(url, **publish_command(artifact, ids, expected=expected))
            assert result.status_code == 201, result.text
            target = result.json()['receipt']['id']
            result = await source.post('/api/visual-disclosures/' + target + '/withdrawals',
                json={'reason': 'Explicit bounded-chain withdrawal', 'withdrawal_confirmed': True},
                headers={'Idempotency-Key': str(uuid.uuid4()), 'If-Match': '"' + target + '"'})
            assert result.status_code == 201, result.text
            expected = result.json()['receipt']['id']
        assert not result.json()['current']['available']
        assert (await source.post(url, **publish_command(artifact, ids, expected=expected))).status_code == 409
        assert (await source.get('/api/visual-disclosures/' + target + '/content')).status_code == 404
    assert await read_value('SELECT count(*) FROM visual_disclosure_event WHERE organization_id=:org', ids) == 100
