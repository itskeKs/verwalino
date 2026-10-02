"""Current administrative quarantine decisions never grant original access."""
import uuid
import pytest
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_scoped_mandate_documents import document_fixture
from tests.test_scoped_account_statements import client
from tests.mandate_legacy_fixture import seed_legacy_mandate

pytestmark = pytest.mark.asyncio
PATH = '/api/mandate-quarantine'


async def test_admin_reviews_only_opaque_quarantine_metadata_and_retry_is_durable(migrator_session, scope_seed):
    ids = await document_fixture(migrator_session, scope_seed)
    document = await seed_legacy_mandate(ids)
    async with client(ids, 'actor') as api:
        page = await api.get(PATH + '/page')
        assert page.status_code == 200, page.text
        row = next(item for item in page.json()['items'] if item['document_id'] == str(document))
        assert not any(value in page.text for value in ['Hidden legacy', 'secret-legacy.pdf', 'synthetic/unverified', str(ids['clerk'])])
        assert row['disposition'] is None
        body = {'document_id': str(document), 'snapshot_digest': row['snapshot_digest'], 'reason': 'CUSTODY_UNVERIFIED'}
        headers = {'Idempotency-Key': str(uuid.uuid4())}
        reviewed = await api.post(PATH, json=body, headers=headers)
        assert reviewed.status_code == 201, reviewed.text
        assert (await api.post(PATH, json=body, headers=headers)).json() == reviewed.json()
        assert (await api.post(PATH, json={**body, 'reason': 'EXTERNAL_ORIGINAL_UNAVAILABLE'}, headers=headers)).status_code == 409
        assert (await api.get(f"/api/mandates/{ids['mandate']}/documents/{document}/download")).status_code == 404


async def test_clerk_cannot_inventory_or_review_quarantine(migrator_session, scope_seed):
    ids = await document_fixture(migrator_session, scope_seed)
    document = await seed_legacy_mandate(ids)
    async with client(ids) as api:
        assert (await api.get(PATH + '/page')).status_code == 403
        assert (await api.post(PATH, headers={'Idempotency-Key': str(uuid.uuid4())}, json={
            'document_id': str(document), 'snapshot_digest': 'a' * 64, 'reason': 'CUSTODY_UNVERIFIED'})).status_code == 403


async def test_pages_are_bounded_signed_and_tenant_scoped(migrator_session, scope_seed, client_b_admin):
    ids = await document_fixture(migrator_session, scope_seed)
    expected = sorted([str(await seed_legacy_mandate(ids)) for _ in range(3)])
    async with client(ids, 'actor') as api:
        seen = []
        cursor = None
        while True:
            result = await api.get(PATH + '/page', params={'limit': 1, **({'cursor': cursor} if cursor else {})})
            assert result.status_code == 200, result.text
            seen.extend(item['document_id'] for item in result.json()['items'])
            cursor = result.json()['next_cursor']
            if not cursor:
                break
            assert (await client_b_admin.get(PATH + '/page', params={'cursor': cursor})).status_code == 422
        assert seen == expected
        assert (await api.get(PATH + '/page', params={'cursor': 'tampered'})).status_code == 422
        assert (await api.get(PATH + '/page', params={'limit': 201})).status_code == 422
        assert (await api.get(PATH + '/page', params={'q': 'secret'})).status_code == 422
    foreign = await client_b_admin.get(PATH + '/page')
    assert foreign.status_code == 200 and foreign.json()['items'] == []


async def test_stale_digest_parallel_retry_and_immutable_disposition(migrator_session, scope_seed):
    import asyncio
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from tests.test_scope_payment_reconciliation import read_value
    ids = await document_fixture(migrator_session, scope_seed)
    ids['document'] = await seed_legacy_mandate(ids)
    original = await read_value('SELECT to_jsonb(d) FROM mandate_document d WHERE id=:document', ids)
    async with client(ids, 'actor') as api:
        row = (await api.get(PATH + '/page')).json()['items'][0]
        body = {'document_id': str(ids['document']), 'snapshot_digest': row['snapshot_digest'], 'reason': 'CUSTODY_UNVERIFIED'}
        headers = {'Idempotency-Key': str(uuid.uuid4())}
        assert (await api.post(PATH, json={**body, 'snapshot_digest': 'b' * 64}, headers=headers)).status_code == 409
        responses = await asyncio.gather(*(api.post(PATH, json=body, headers=headers) for _ in range(3)))
        assert all(result.status_code == 201 for result in responses)
        assert all(result.json() == responses[0].json() for result in responses)
        assert (await api.post(PATH, json=body, headers={'Idempotency-Key': str(uuid.uuid4())})).status_code == 409
        page = await api.get(PATH + '/page')
        assert page.json()['items'][0]['disposition'] == responses[0].json()
    assert await read_value('SELECT to_jsonb(d) FROM mandate_document d WHERE id=:document', ids) == original
    assert await read_value('SELECT count(*) FROM mandate_quarantine_disposition WHERE document_id=:document', ids) == 1
    assert await read_value("SELECT count(*) FROM audit_event WHERE entity_id=:document AND action='mandate_document.quarantine_reviewed'", ids) == 1
    for sql in ['DELETE FROM mandate_quarantine_disposition WHERE document_id=:document',
                "UPDATE mandate_quarantine_disposition SET reason='EXTERNAL_ORIGINAL_UNAVAILABLE' WHERE document_id=:document"]:
        with pytest.raises(DBAPIError):
            await read_value(sql, ids)
    with pytest.raises(DBAPIError):
        await read_value('SELECT * FROM mandate_quarantine_disposition WHERE document_id=:document', ids, actor_key='actor')


@pytest.mark.parametrize('boundary', ['worker', 'wrong_org', 'clerk'])
async def test_direct_database_cannot_bypass_admin_boundary(migrator_session, scope_seed, seed_data, boundary):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_tenant_session, get_worker_tenant_session
    ids = await document_fixture(migrator_session, scope_seed)
    document = await seed_legacy_mandate(ids)
    factory = get_worker_tenant_session if boundary == 'worker' else get_tenant_session
    async with factory(ids['org']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(ids['clerk'] if boundary == 'clerk' else ids['actor'])})
        params = {'org': seed_data['org_b_id'] if boundary == 'wrong_org' else ids['org'], 'document': document, 'key': uuid.uuid4()}
        for command in ['SELECT mandate_quarantine_page(:org,NULL,50)',
                        "SELECT review_mandate_quarantine(:org,:document,:key,repeat('a',64),'CUSTODY_UNVERIFIED')"]:
            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(text(command), params)


@pytest.mark.parametrize('change', ['demote', 'deactivate'])
async def test_live_admin_is_rechecked_before_receipt_replay(migrator_session, scope_seed, change):
    from sqlalchemy import text
    from app.main import app
    from app.core.tenant import AuthContext, get_current_user
    from tests.test_scoped_account_statements import mutate
    ids = await document_fixture(migrator_session, scope_seed)
    document = await seed_legacy_mandate(ids)
    async with client(ids, 'actor') as api:
        row = (await api.get(PATH + '/page')).json()['items'][0]
        body = {'document_id': str(document), 'snapshot_digest': row['snapshot_digest'], 'reason': 'CUSTODY_UNVERIFIED'}
        headers = {'Idempotency-Key': str(uuid.uuid4())}
        assert (await api.post(PATH, json=body, headers=headers)).status_code == 201
        app.dependency_overrides[get_current_user] = lambda: AuthContext(ids['actor'], ids['org'], 'OWNER_ADMIN')
        try:
            await mutate(text("UPDATE membership SET role='CLERK' WHERE organization_id=:org AND user_id=:actor") if change == 'demote'
                         else text('UPDATE "user" SET is_active=false WHERE id=:actor'), ids)
            for result in [await api.get(PATH + '/page'), await api.post(PATH, json=body, headers=headers)]:
                assert result.status_code == (403 if change == 'demote' else 401)
        finally:
            app.dependency_overrides.pop(get_current_user, None)


async def test_populated_disposition_downgrade_refuses_evidence_loss(migrator_session, scope_seed):
    import importlib
    from sqlalchemy import text, create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = await document_fixture(migrator_session, scope_seed)
    document = await seed_legacy_mandate(ids)
    async with client(ids, 'actor') as api:
        row = (await api.get(PATH + '/page')).json()['items'][0]
        assert (await api.post(PATH, json={'document_id': str(document), 'snapshot_digest': row['snapshot_digest'], 'reason': 'CUSTODY_UNVERIFIED'},
                               headers={'Idempotency-Key': str(uuid.uuid4())})).status_code == 201
    migration = importlib.import_module('migrations.versions.0079_mandate_quarantine_disposition')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError, match='Retain reviewed quarantine decisions'):
                with db.begin_nested():
                    migration.downgrade()
            assert not db.scalar(text('SELECT property_source_activation_ready(:org)'), ids)
    finally:
        engine.dispose()
