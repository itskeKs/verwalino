"""Quarantined dunning remains retained history without invented provenance."""
import uuid
import pytest
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_scoped_account_statements import statement_fixture, client
from tests.test_scoped_dunning import legacy_notice

pytestmark = pytest.mark.asyncio
PATH = '/api/dunning-quarantine'


async def test_admin_reviews_dunning_uncertainty_without_disclosure_or_history_loss(migrator_session, scope_seed):
    from tests.test_scope_payment_reconciliation import read_value
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await legacy_notice(ids)
    async with client(ids, 'actor') as api:
        result = await api.get(PATH + '/page')
        assert result.status_code == 200, result.text
        row = result.json()['items'][0]
        assert row['notice_id'] == str(ids['notice'])
        assert not any(value in result.text for value in ['Statement Person', '7000', str(ids['profile'])])
        body = {'notice_id': row['notice_id'], 'snapshot_digest': row['snapshot_digest'], 'reason': 'SOURCE_CONTEXT_UNVERIFIED'}
        key = {'Idempotency-Key': str(uuid.uuid4())}
        response = await api.post(PATH, json=body, headers=key)
        assert response.status_code == 201, response.text
        assert (await api.post(PATH, json=body, headers=key)).json() == response.json()
        assert (await api.get(f"/api/dunning/{ids['notice']}/pdf")).status_code == 404
        assert (await api.get(f"/api/dsgvo/persons/{ids['person']}/status")).status_code == 409
    assert not await read_value('SELECT dunning_history_complete(:org,:profile,NULL)', ids, actor_key='clerk')
    assert await read_value('SELECT count(*) FROM dunning_notice WHERE id=:notice', ids) == 1


async def test_clerk_cannot_review_dunning_quarantine(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await legacy_notice(ids)
    async with client(ids) as api:
        assert (await api.get(PATH + '/page')).status_code == 403
        assert (await api.post(PATH, headers={'Idempotency-Key': str(uuid.uuid4())}, json={
            'notice_id': str(ids['notice']), 'snapshot_digest': 'a' * 64, 'reason': 'SOURCE_CONTEXT_UNVERIFIED'})).status_code == 403


async def draft_fixture(session, seed):
    from sqlalchemy import text
    from tests.test_scope_grants import scope_fixture
    ids = await scope_fixture(session, seed)
    ids.update(person=uuid.uuid4(), profile=uuid.uuid4(), notice=uuid.uuid4())
    await session.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES(:person,:org,'Unknown','History','','','')"), ids)
    await session.execute(text("INSERT INTO tenant_profile(id,organization_id,person_id,notes) VALUES(:profile,:org,:person,'')"), ids)
    await session.execute(text("""INSERT INTO dunning_notice(id,organization_id,tenant_profile_id,level,issue_date,due_date,
        principal_amount,fee_amount,total_amount,included_items,status)
        VALUES(:notice,:org,:profile,1,'2026-01-01','2026-01-15',100,0,100,'[]','draft')"""), ids)
    await session.commit()
    return ids


async def test_mutable_legacy_draft_conflict_and_permanent_quarantine(migrator_session, scope_seed):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from tests.test_scoped_account_statements import mutate
    from tests.test_scope_payment_reconciliation import read_value
    ids = await draft_fixture(migrator_session, scope_seed)
    async with client(ids, 'actor') as api:
        assert (await api.get(f"/api/dunning/{ids['notice']}")).status_code == 200
        row = (await api.get(PATH + '/page')).json()['items'][0]
        body = {'notice_id': row['notice_id'], 'snapshot_digest': row['snapshot_digest'], 'reason': 'SOURCE_CONTEXT_UNVERIFIED'}
        key = {'Idempotency-Key': str(uuid.uuid4())}
        await mutate(text("UPDATE dunning_notice SET note='Concurrent draft change' WHERE id=:notice"), ids)
        assert (await api.post(PATH, json=body, headers=key)).status_code == 409
        row = (await api.get(PATH + '/page')).json()['items'][0]
        assert (await api.post(PATH, json={**body, 'snapshot_digest': row['snapshot_digest']}, headers=key)).status_code == 201
        assert (await api.get(f"/api/dunning/{ids['notice']}")).status_code == 404
        assert (await api.get(f"/api/dunning/{ids['notice']}/pdf")).status_code == 404
    assert await read_value('SELECT count(*) FROM audit_event WHERE entity_id=:notice', ids, actor_key='actor') == 0
    for sql in ["UPDATE dunning_notice SET note='Rewrite' WHERE id=:notice RETURNING id",
                'DELETE FROM dunning_notice WHERE id=:notice RETURNING id',
                'DELETE FROM dunning_quarantine_disposition WHERE notice_id=:notice RETURNING id',
                "INSERT INTO dunning_scope_context(organization_id,dunning_notice_id,scope_id,lease_agreement_id,period_start,period_end,created_by,reason) VALUES(:org,:notice,:scope,:notice,'2025-01-01','2025-12-31',:actor,'Invented provenance') RETURNING dunning_notice_id"]:
        with pytest.raises(DBAPIError, match='immutable'):
            await read_value(sql, ids)
    assert await read_value("SELECT note FROM dunning_notice WHERE id=:notice", ids) == 'Concurrent draft change'


async def test_parallel_retry_and_tenant_paging(migrator_session, scope_seed, client_b_admin):
    import asyncio
    from tests.test_scope_payment_reconciliation import read_value
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await legacy_notice(ids)
    first = str(ids['notice'])
    await legacy_notice(ids)
    async with client(ids, 'actor') as api:
        page = (await api.get(PATH + '/page', params={'limit': 1})).json()
        assert len(page['items']) == 1 and page['next_cursor']
        next_page = (await api.get(PATH + '/page', params={'limit': 1, 'cursor': page['next_cursor']})).json()
        assert [page['items'][0]['notice_id'], next_page['items'][0]['notice_id']] == sorted([first, str(ids['notice'])])
        assert (await client_b_admin.get(PATH + '/page', params={'cursor': page['next_cursor']})).status_code == 422
        assert (await client_b_admin.get(PATH + '/page')).json()['items'] == []
        assert (await api.get(PATH + '/page', params={'cursor': 'tampered'})).status_code == 422
        assert (await api.get(PATH + '/page', params={'limit': 201})).status_code == 422
        row = page['items'][0]
        body = {'notice_id': row['notice_id'], 'snapshot_digest': row['snapshot_digest'], 'reason': 'MIXED_SCOPE_HISTORY'}
        key = {'Idempotency-Key': str(uuid.uuid4())}
        results = await asyncio.gather(*(api.post(PATH, json=body, headers=key) for _ in range(3)))
        assert all(result.status_code == 201 and result.json() == results[0].json() for result in results)
        assert (await api.post(PATH, json={**body, 'reason': 'SOURCE_CONTEXT_UNVERIFIED'}, headers=key)).status_code == 409
        ids['notice'] = uuid.UUID(row['notice_id'])
    assert await read_value('SELECT count(*) FROM dunning_quarantine_disposition WHERE notice_id=:notice', ids) == 1
    assert await read_value("SELECT count(*) FROM audit_event WHERE entity_id=:notice AND action='dunning_notice.quarantine_reviewed'", ids) == 1


@pytest.mark.parametrize('boundary', ['worker', 'wrong_org', 'clerk'])
async def test_direct_sql_cannot_bypass_dunning_review_authority(migrator_session, scope_seed, seed_data, boundary):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_tenant_session, get_worker_tenant_session
    ids = await draft_fixture(migrator_session, scope_seed)
    factory = get_worker_tenant_session if boundary == 'worker' else get_tenant_session
    async with factory(ids['org']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(ids['clerk'] if boundary == 'clerk' else ids['actor'])})
        params = {'org': seed_data['org_b_id'] if boundary == 'wrong_org' else ids['org'], 'notice': ids['notice'], 'key': uuid.uuid4()}
        for command in ['SELECT dunning_quarantine_page(:org,NULL,50)',
                        "SELECT review_dunning_quarantine(:org,:notice,:key,repeat('a',64),'SOURCE_CONTEXT_UNVERIFIED')",
                        'SELECT * FROM dunning_quarantine_disposition']:
            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(text(command), params)


async def test_current_scoped_notice_cannot_be_quarantined(migrator_session, scope_seed):
    from tests.test_scoped_dunning import grant, tenant_body
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await grant(ids, ['read', 'prepare'])
    async with client(ids) as api:
        result = await api.post('/api/dunning/tenant', json=tenant_body(ids))
        assert result.status_code == 201, result.text
        notice = result.json()['id']
    async with client(ids, 'actor') as api:
        assert (await api.get(PATH + '/page')).json()['items'] == []
        result = await api.post(PATH, headers={'Idempotency-Key': str(uuid.uuid4())}, json={
            'notice_id': notice, 'snapshot_digest': 'a' * 64, 'reason': 'SOURCE_CONTEXT_UNVERIFIED'})
        assert result.status_code == 404


async def test_dunning_quarantine_downgrade_preserves_evidence(migrator_session, scope_seed):
    import importlib
    from sqlalchemy import text, create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = await draft_fixture(migrator_session, scope_seed)
    async with client(ids, 'actor') as api:
        row = (await api.get(PATH + '/page')).json()['items'][0]
        assert (await api.post(PATH, json={'notice_id': row['notice_id'], 'snapshot_digest': row['snapshot_digest'], 'reason': 'SOURCE_CONTEXT_UNVERIFIED'},
                               headers={'Idempotency-Key': str(uuid.uuid4())})).status_code == 201
    migration = importlib.import_module('migrations.versions.0080_dunning_quarantine')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError, match='Retain quarantined dunning history'):
                with db.begin_nested():
                    migration.downgrade()
            assert not db.scalar(text('SELECT property_source_activation_ready(:org)'), ids)
    finally:
        engine.dispose()
