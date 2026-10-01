"""Reviewed scope setup is explicit, tenant bounded and safe after lost replies."""
import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def create_property(client, kind='MISCH'):
    response = await client.post('/api/properties', json={
        'name': 'Scope API test', 'street': 'Test 1', 'zip_code': '12345',
        'city': 'Test', 'management_type': kind,
    })
    assert response.status_code == 201, response.text
    return response.json()['id']


def scope_body(property_id, **extra):
    return dict(property_id=property_id, scope_type='WEG', label='Reviewed WEG',
                valid_from='2025-01-01', whole_property=True, units=[], **extra)


async def test_scope_create_replay_and_tenant_visibility(client_a_admin, client_a_clerk, client_b_admin):
    prop = await create_property(client_a_admin)
    body = scope_body(prop)
    headers = {'Idempotency-Key': str(uuid.uuid4())}
    created = await client_a_admin.post('/api/accounting-scopes', json=body, headers=headers)
    assert created.status_code == 201, created.text
    sphere = created.json()
    again = await client_a_admin.post('/api/accounting-scopes', json=body, headers=headers)
    assert again.status_code == 201 and again.json()['id'] == sphere['id']
    changed = await client_a_admin.post('/api/accounting-scopes', json={**body, 'label': 'Changed'}, headers=headers)
    assert changed.status_code == 409
    for client in (client_a_clerk, client_b_admin):
        assert (await client.get('/api/accounting-scopes/' + sphere['id'])).status_code == 404
        page = await client.get('/api/accounting-scopes/page', params={'property_id': prop})
        assert page.status_code == 200 and page.json()['items'] == []
    assert (await client_a_clerk.post('/api/accounting-scopes', json=body, headers=headers)).status_code == 403


async def test_grant_retry_revocation_and_scope_access(client_a_admin, client_a_clerk, client_b_admin, seed_data):
    prop = await create_property(client_a_admin)
    sphere = (await client_a_admin.post('/api/accounting-scopes', json=scope_body(prop), headers={'Idempotency-Key': str(uuid.uuid4())})).json()
    path = '/api/accounting-scopes/' + sphere['id']
    body = {'user_id': str(seed_data['user_a_clerk_id']), 'capabilities': ['read', 'prepare'], 'reason': 'Reviewed team assignment'}
    headers = {'Idempotency-Key': str(uuid.uuid4())}
    grant = await client_a_admin.post(path + '/grants', json=body, headers=headers)
    assert grant.status_code == 201, grant.text
    replay = await client_a_admin.post(path + '/grants', json=body, headers=headers)
    assert replay.json()['id'] == grant.json()['id']
    assert (await client_a_clerk.get(path)).status_code == 200
    assert (await client_b_admin.get(path)).status_code == 404
    assert (await client_a_clerk.post(path + '/grants', json=body, headers=headers)).status_code == 403
    assert (await client_a_admin.post(path + '/grants', json={**body, 'capabilities': ['read', 'approve']}, headers=headers)).status_code == 409
    revoke = path + '/grants/' + grant.json()['id'] + '/revoke'
    assert (await client_a_admin.post(revoke)).status_code == 204
    assert (await client_a_admin.post(revoke)).status_code == 204
    assert (await client_a_clerk.get(path)).status_code == 404
    # Replaying a pre-revocation request cannot resurrect permission.
    replay = await client_a_admin.post(path + '/grants', json=body, headers=headers)
    assert replay.status_code == 201 and replay.json()['revoked_at'] is not None
    assert (await client_a_clerk.get(path)).status_code == 404


async def test_scope_validation_is_atomic(client_a_admin, client_b_admin):
    prop = await create_property(client_a_admin)
    foreign = await create_property(client_b_admin)
    headers = {'Idempotency-Key': str(uuid.uuid4())}
    cases = [
        {**scope_body(prop), 'scope_type': 'SEV'},
        {**scope_body(prop), 'valid_to': '2024-12-31'},
        {**scope_body(prop), 'whole_property': False, 'units': []},
        {**scope_body(prop), 'whole_property': False, 'units': [{'unit_id': str(uuid.uuid4())}]},
        scope_body(foreign),
    ]
    for body in cases:
        response = await client_a_admin.post('/api/accounting-scopes', json=body, headers=headers)
        assert response.status_code in (404, 422), response.text
    # A failed attempt leaves neither a partial scope nor an occupied request key.
    ok = await client_a_admin.post('/api/accounting-scopes', json=scope_body(prop), headers=headers)
    assert ok.status_code == 201, ok.text
    page = await client_a_admin.get('/api/accounting-scopes/page', params={'property_id': prop})
    assert [row['id'] for row in page.json()['items']] == [ok.json()['id']]


async def test_binding_needs_explicit_financial_grant_and_cannot_reparent(client_a_admin, seed_data):
    prop = await create_property(client_a_admin)
    scope = (await client_a_admin.post('/api/accounting-scopes', json=scope_body(prop), headers={'Idempotency-Key': str(uuid.uuid4())})).json()
    account = (await client_a_admin.post('/api/bank-accounts', json={'property_id': prop, 'account_type': 'LAUFEND', 'label': 'Scope account'})).json()
    path = '/api/accounting-scopes/' + scope['id']
    body = {'resource_type': 'bank_account', 'resource_id': account['id'], 'reason': 'Reviewed original account'}
    assert (await client_a_admin.post(path + '/bindings', json=body)).status_code == 404
    grant = await client_a_admin.post(path + '/grants', json={'user_id': str(seed_data['user_a_admin_id']), 'capabilities': ['read', 'prepare'], 'reason': 'Explicit setup grant'}, headers={'Idempotency-Key': str(uuid.uuid4())})
    assert grant.status_code == 201
    bound = await client_a_admin.post(path + '/bindings', json=body)
    assert bound.status_code == 201, bound.text
    assert (await client_a_admin.post(path + '/bindings', json=body)).json()['id'] == bound.json()['id']
    assert (await client_a_admin.post(path + '/bindings', json={**body, 'reason': 'Different review'})).status_code == 409
    bad = await client_a_admin.post(path + '/bindings', json={**body, 'resource_id': str(uuid.uuid4())})
    assert bad.status_code == 404


async def test_sev_owner_must_cover_each_unit_and_full_period(client_a_admin):
    prop = await create_property(client_a_admin)
    unit = (await client_a_admin.post('/api/units', json={'property_id': prop, 'number': 'SEV1', 'usage_type': 'WOHNUNG'})).json()
    person = (await client_a_admin.post('/api/persons', json={'first_name': 'Scope', 'last_name': 'Owner'})).json()
    owner = (await client_a_admin.post('/api/owner-profiles', json={'person_id': person['id']})).json()
    body = {**scope_body(prop), 'scope_type': 'SEV', 'whole_property': False,
            'valid_to': '2025-12-31', 'units': [{'unit_id': unit['id'], 'owner_profile_id': owner['id']}]}
    headers = {'Idempotency-Key': str(uuid.uuid4())}
    unrelated = await client_a_admin.post('/api/accounting-scopes', json=body, headers=headers)
    assert unrelated.status_code == 422, unrelated.text
    # Ownership is half-open in storage; the API accepts the inclusive last day.
    ownership = {'unit_id': unit['id'], 'owner_profile_id': owner['id'], 'valid_from': '2025-01-01', 'valid_to': '2025-06-30'}
    assert (await client_a_admin.post('/api/ownership', json=ownership)).status_code == 201
    partial = await client_a_admin.post('/api/accounting-scopes', json=body, headers=headers)
    assert partial.status_code == 422
    assert (await client_a_admin.post('/api/ownership', json={**ownership, 'valid_from': '2025-07-01', 'valid_to': '2025-12-31'})).status_code == 201
    created = await client_a_admin.post('/api/accounting-scopes', json=body, headers=headers)
    assert created.status_code == 201, created.text
    detail = (await client_a_admin.get('/api/accounting-scopes/' + created.json()['id'])).json()
    assert detail['units'] == body['units']
    assert (await client_a_admin.post('/api/accounting-scopes', json=body, headers=headers)).json()['id'] == created.json()['id']
    page = await client_a_admin.get('/api/owner-profiles/page', params={'q': 'Scope'})
    assert owner['id'] in [row['id'] for row in page.json()['items']]
    open_ended = await client_a_admin.post('/api/accounting-scopes', json={**body, 'valid_to': None}, headers={'Idempotency-Key': str(uuid.uuid4())})
    assert open_ended.status_code == 422


async def test_binding_invalid_type_returns_atomic_validation_error(client_a_admin, seed_data):
    prop = await create_property(client_a_admin)
    sphere = (await client_a_admin.post('/api/accounting-scopes', json=scope_body(prop), headers={'Idempotency-Key': str(uuid.uuid4())})).json()
    path = '/api/accounting-scopes/' + sphere['id']
    await client_a_admin.post(path + '/grants', json={'user_id': str(seed_data['user_a_admin_id']), 'capabilities': ['read', 'prepare'], 'reason': 'Explicit setup'}, headers={'Idempotency-Key': str(uuid.uuid4())})
    account = (await client_a_admin.post('/api/bank-accounts', json={'property_id': prop, 'account_type': 'KAUTION', 'label': 'Incompatible account'})).json()
    response = await client_a_admin.post(path + '/bindings', json={'resource_type': 'bank_account', 'resource_id': account['id'], 'reason': 'Wrong WEG mapping'})
    assert response.status_code == 422, response.text


async def test_scope_user_selector_excludes_inactive_and_unassigned(client_a_admin, migrator_session, seed_data):
    from sqlalchemy import text
    unassigned = uuid.uuid4()
    await migrator_session.execute(text('INSERT INTO "user"(id,organization_id,email,password_hash,display_name) VALUES (:id,:org,:email,:password,:name)'), {'id': unassigned, 'org': seed_data['org_a_id'], 'email': f'{unassigned}@example.test', 'password': 'not-a-login', 'name': 'Unassigned selector test'})
    async with migrator_session.begin_nested():
        await migrator_session.execute(text('UPDATE "user" SET is_active=false WHERE id=:user'), {'user': seed_data['user_a_clerk_id']})
    await migrator_session.commit()
    try:
        page = await client_a_admin.get('/api/users/page', params={'eligible_for_scope': 'true', 'limit': 1})
        assert page.status_code == 200
        cursor = page.json()['next_cursor']
        assert cursor
        assert (await client_a_admin.get('/api/users/page', params={'cursor': cursor})).status_code == 422
        all_members = await client_a_admin.get('/api/users/page', params={'eligible_for_scope': 'true'})
        assert str(seed_data['user_a_clerk_id']) not in [row['id'] for row in all_members.json()['items']]
        assert all(row['is_active'] for row in all_members.json()['items'])
        assert str(unassigned) not in [row['id'] for row in all_members.json()['items']]
    finally:
        # The session fixture is shared, so restore this member in a fresh transaction.
        from sqlalchemy.ext.asyncio import create_async_engine
        from tests.conftest import MIGRATOR_URL
        engine = create_async_engine(MIGRATOR_URL)
        try:
            async with engine.begin() as connection:
                await connection.execute(text('UPDATE "user" SET is_active=true WHERE id=:user'), {'user': seed_data['user_a_clerk_id']})
        finally:
            await engine.dispose()
