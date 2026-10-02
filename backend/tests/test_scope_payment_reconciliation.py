"""Proven bank descendants are classified without rewriting historical money."""
import uuid
from datetime import date
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_scope_grants import scope_fixture
from tests.test_scoped_account_statements import client, mutate

pytestmark = pytest.mark.asyncio


async def reconciliation_fixture(session, seed, *, partial=False):
    ids = await scope_fixture(session, seed)
    if partial:
        ids.update(scope=uuid.uuid4(), unit=uuid.uuid4())
        await session.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (:unit,:org,:property,'Partial','WOHNUNG')"), ids)
        await session.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:scope,:org,:property,'WEG','Partial scope','2025-01-01',false,'REVIEWED',:actor)"), ids)
        await session.execute(text('INSERT INTO accounting_scope_unit(organization_id,scope_id,unit_id,property_id) VALUES (:org,:scope,:unit,:property)'), ids)
    ids.update(person=uuid.uuid4(), owner=uuid.uuid4(), payment=uuid.uuid4(), blocked=uuid.uuid4())
    await session.execute(text("INSERT INTO person(id,organization_id,last_name,street,zip_code,city) VALUES (:person,:org,'Reconciliation','','','')"), ids)
    await session.execute(text('INSERT INTO owner_profile(id,organization_id,person_id) VALUES (:owner,:org,:person)'), ids)
    for key, value_date in [('payment','2025-06-01'), ('blocked','2024-06-01')]:
        await session.execute(text("""INSERT INTO payment(id,organization_id,property_id,bank_account_id,direction,amount,value_date,
            payment_type,owner_profile_id,period_year,period_month,status)
            VALUES (:id,:org,:property,:bank,'IN',100,:date,'HAUSGELD_VORSCHUSS',:owner,2025,6,'booked')"""),
            {**ids, 'id': ids[key], 'date': date.fromisoformat(value_date)})
    await session.execute(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,bank_account_id,origin,reason,reviewed_by) VALUES (:org,:property,:scope,:bank,'REVIEWED','Reviewed bank',:actor)"), ids)
    await session.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:actor,ARRAY['read','prepare'],:actor,'Reviewed migration access')"), ids)
    await session.commit()
    return ids


def path(ids):
    return f"/api/accounting-scopes/{ids['scope']}/legacy-payments"


def command(rows):
    return {'reason': 'Verified original bank classification', 'items': [
        {'payment_id': row['id'], 'source_digest': row['source_digest']} for row in rows]}


async def test_proven_payment_preview_apply_replay_and_revocation(migrator_session, scope_seed):
    ids = await reconciliation_fixture(migrator_session, scope_seed)
    async with client(ids, 'actor') as api:
        response = await api.get(path(ids))
        assert response.status_code == 200, response.text
        rows = response.json()['items']
        assert len(rows) == 2
        proven = next(row for row in rows if row['id'] == str(ids['payment']))
        blocked = next(row for row in rows if row['id'] == str(ids['blocked']))
        assert proven['proof_status'] == 'proven'
        assert blocked['proof_status'] == 'review_required'
        headers = {'Idempotency-Key': str(uuid.uuid4())}
        body = command([proven])
        applied = await api.post(path(ids), json=body, headers=headers)
        assert applied.status_code == 201, applied.text
        assert len(applied.json()['items']) == 1
        replay = await api.post(path(ids), json=body, headers=headers)
        assert replay.status_code == 201 and replay.json() == applied.json()
        assert (await api.post(path(ids), json={**body, 'reason': 'Changed'}, headers=headers)).status_code == 409
        assert [row['id'] for row in (await api.get(path(ids))).json()['items']] == [str(ids['blocked'])]
    await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:actor AND revoked_at IS NULL'), ids)
    async with client(ids, 'actor') as api:
        assert (await api.get(path(ids))).status_code == 404
        assert (await api.post(path(ids), json=body, headers=headers)).status_code == 404


async def test_invalid_candidate_aborts_batch_and_does_not_consume_retry_key(migrator_session, scope_seed):
    ids = await reconciliation_fixture(migrator_session, scope_seed)
    async with client(ids, 'actor') as api:
        rows = (await api.get(path(ids))).json()['items']
        headers = {'Idempotency-Key': str(uuid.uuid4())}
        rejected = await api.post(path(ids), json=command(rows), headers=headers)
        assert rejected.status_code == 409, rejected.text
        assert len((await api.get(path(ids))).json()['items']) == 2
        valid = [row for row in rows if row['proof_status'] == 'proven']
        assert (await api.post(path(ids), json=command(valid), headers=headers)).status_code == 201


async def test_exact_scope_and_administration_required_for_preview(migrator_session, scope_seed, client_b_admin):
    ids = await reconciliation_fixture(migrator_session, scope_seed)
    assert (await client_b_admin.get(path(ids))).status_code == 404
    async with client(ids) as clerk:
        assert (await clerk.get(path(ids))).status_code == 403
    async with client(ids, 'actor') as admin:
        wrong = {**ids, 'scope': ids['other_scope']}
        assert (await admin.get(path(wrong))).status_code == 404


async def read_value(sql, params, *, actor_key=None):
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import APP_RW_URL, MIGRATOR_URL
    engine = create_async_engine(APP_RW_URL if actor_key else MIGRATOR_URL)
    try:
        async with engine.begin() as connection:
            if actor_key:
                await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true)"),
                    {'org': str(params['org']), 'user': str(params[actor_key])})
            return await connection.scalar(text(sql), params)
    finally:
        await engine.dispose()


async def test_parallel_replay_preserves_money_and_scopes_receipt_and_audit(migrator_session, scope_seed):
    import asyncio
    ids = await reconciliation_fixture(migrator_session, scope_seed)
    original = await read_value('SELECT to_jsonb(p) FROM payment p WHERE p.id=:payment', ids)
    async with client(ids, 'actor') as api:
        rows = (await api.get(path(ids))).json()['items']
        body = command([row for row in rows if row['proof_status'] == 'proven'])
        headers = {'Idempotency-Key': str(uuid.uuid4())}
        first, second = await asyncio.gather(*(api.post(path(ids), json=body, headers=headers) for _ in range(2)))
        assert first.status_code == second.status_code == 201
        assert first.json() == second.json()
        ids['receipt'] = uuid.UUID(first.json()['id'])
    assert await read_value('SELECT to_jsonb(p) FROM payment p WHERE p.id=:payment', ids) == original
    assert await read_value('SELECT origin FROM accounting_scope_binding WHERE organization_id=:org AND payment_id=:payment', ids) == 'PROVEN_LEGACY'
    assert await read_value('SELECT count(*) FROM scope_payment_reconciliation WHERE organization_id=:org', ids) == 1
    # Even another administrator with a prepare grant cannot read this actor's receipt.
    await mutate(text("UPDATE membership SET role='OWNER_ADMIN' WHERE organization_id=:org AND user_id=:clerk"), ids)
    for actor, visible in [('actor', 1), ('clerk', 0)]:
        for table, condition in [('scope_payment_reconciliation', 'id=:receipt'),
                                 ('scope_payment_reconciliation_item', 'reconciliation_id=:receipt'),
                                 ('audit_event', "entity_type='scope_payment_reconciliation' AND entity_id=:receipt")]:
            assert await read_value(f'SELECT count(*) FROM {table} WHERE {condition}', ids, actor_key=actor) == visible


async def test_mismatched_source_digest_requires_new_preview(migrator_session, scope_seed):
    ids = await reconciliation_fixture(migrator_session, scope_seed)
    async with client(ids, 'actor') as api:
        rows = (await api.get(path(ids))).json()['items']
        body = command([row for row in rows if row['proof_status'] == 'proven'])
        body['items'][0]['source_digest'] = '0'*64
        response = await api.post(path(ids), json=body, headers={'Idempotency-Key': str(uuid.uuid4())})
        assert response.status_code == 409
        assert len((await api.get(path(ids))).json()['items']) == 2
    assert await read_value('SELECT count(*) FROM scope_payment_reconciliation WHERE organization_id=:org', ids) == 0


async def test_raw_principal_cannot_forge_proven_origin_receipt_or_bypass_prepare(migrator_session, scope_seed):
    import json
    from sqlalchemy.exc import DBAPIError
    ids = await reconciliation_fixture(migrator_session, scope_seed)
    # The general binding policy still permits only reviewed, actor-authored rows.
    with pytest.raises(DBAPIError):
        await read_value("""INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,payment_id,payment_bank_account_id,origin,reason,reviewed_by)
            VALUES (:org,:property,:scope,:payment,:bank,'PROVEN_LEGACY','Forged proof',:actor) RETURNING id""", ids, actor_key='actor')
    with pytest.raises(DBAPIError):
        await read_value("""INSERT INTO scope_payment_reconciliation(organization_id,scope_id,actor_user_id,request_key,request_digest,reason)
            VALUES (:org,:scope,:actor,:key,:digest,'Forged receipt') RETURNING id""",
            {**ids, 'key': uuid.uuid4(), 'digest': '0'*64}, actor_key='actor')
    async with client(ids, 'actor') as api:
        rows = (await api.get(path(ids))).json()['items']
    params = {**ids, 'items': json.dumps(command([row for row in rows if row['proof_status'] == 'proven'])['items']), 'key': uuid.uuid4()}
    # A clerk's prepare grant cannot invoke the definer to evade administration.
    with pytest.raises(DBAPIError, match='not accessible'):
        await read_value("SELECT reconcile_legacy_payments(:org,:scope,:key,CAST(:items AS jsonb),'Raw bypass')", params, actor_key='clerk')
    await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:actor AND revoked_at IS NULL'), ids)
    with pytest.raises(DBAPIError, match='not accessible'):
        await read_value("SELECT reconcile_legacy_payments(:org,:scope,:key,CAST(:items AS jsonb),'Raw bypass')", params, actor_key='actor')


async def test_preview_pages_are_bounded_and_actor_query_bound(migrator_session, scope_seed):
    ids = await reconciliation_fixture(migrator_session, scope_seed)
    async with client(ids, 'actor') as api:
        first = await api.get(path(ids), params={'limit': 1})
        assert first.status_code == 200
        assert len(first.json()['items']) == 1 and first.json()['next_cursor']
        second = await api.get(path(ids), params={'limit': 1, 'cursor': first.json()['next_cursor']})
        assert second.status_code == 200 and len(second.json()['items']) == 1
        assert first.json()['items'][0]['id'] != second.json()['items'][0]['id']
        assert not second.json()['next_cursor']
        assert (await api.get(path(ids), params={'limit': 201})).status_code == 422
        assert (await api.get(path(ids), params={'cursor': first.json()['next_cursor'], 'q': 'changed'})).status_code == 422


async def test_reconciliation_evidence_is_immutable_and_blocks_downgrade(migrator_session, scope_seed):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = await reconciliation_fixture(migrator_session, scope_seed)
    async with client(ids, 'actor') as api:
        rows = (await api.get(path(ids))).json()['items']
        response = await api.post(path(ids), json=command([row for row in rows if row['proof_status'] == 'proven']),
                                  headers={'Idempotency-Key': str(uuid.uuid4())})
        assert response.status_code == 201, response.text
        ids['receipt'] = uuid.UUID(response.json()['id'])
    for sql in ["UPDATE scope_payment_reconciliation SET reason='Rewritten' WHERE id=:receipt",
                'DELETE FROM scope_payment_reconciliation_item WHERE reconciliation_id=:receipt']:
        with pytest.raises(DBAPIError, match='immutable'):
            await mutate(text(sql), ids)
    spec = importlib.util.spec_from_file_location('reconciliation_migration', Path(__file__).parents[1] / 'migrations/versions/0040_proven_payment_reconciliation.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with pytest.raises(DBAPIError, match='Cannot downgrade'):
            with engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
    finally:
        engine.dispose()



async def test_partial_unit_scope_is_review_required_not_guessed(migrator_session, scope_seed):
    ids = await reconciliation_fixture(migrator_session, scope_seed, partial=True)
    async with client(ids, 'actor') as api:
        rows = (await api.get(path(ids))).json()['items']
        assert len(rows) == 2
        assert all(row['proof_status'] == 'review_required' and row['blocker'] == 'scope_units_not_supported' for row in rows)
        assert (await api.post(path(ids), json=command(rows), headers={'Idempotency-Key': str(uuid.uuid4())})).status_code == 409


async def test_receipt_replay_rechecks_grant_expiry_after_waiting_for_source_lock(migrator_session, scope_seed):
    import asyncio
    import json
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_URL
    ids = await reconciliation_fixture(migrator_session, scope_seed)
    key = uuid.uuid4()
    async with client(ids, 'actor') as api:
        rows = (await api.get(path(ids))).json()['items']
        body = command([row for row in rows if row['proof_status'] == 'proven'])
        assert (await api.post(path(ids), json=body, headers={'Idempotency-Key': str(key)})).status_code == 201
    await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:actor AND revoked_at IS NULL'), ids)
    engine = create_async_engine(MIGRATOR_URL)
    task = None
    try:
        async with engine.begin() as blocker:
            # Deliberate standalone blocker models a long source transaction.
            await blocker.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:lock,0))"), {'lock': f"financial-inputs:{ids['org']}"})
            await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason,expires_at) VALUES (:org,:scope,:actor,ARRAY['read','prepare'],:actor,'Expiring review grant',clock_timestamp()+interval '1.5 seconds')"), ids)
            task = asyncio.create_task(read_value("SELECT reconcile_legacy_payments(:org,:scope,:key,CAST(:items AS jsonb),:reason)",
                {**ids, 'key': key, 'items': json.dumps(body['items']), 'reason': body['reason']}, actor_key='actor'))
            await asyncio.sleep(0.2)
            assert not task.done()
            await asyncio.sleep(1.5)
        with pytest.raises(DBAPIError, match='not accessible'):
            await task
    finally:
        await engine.dispose()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
