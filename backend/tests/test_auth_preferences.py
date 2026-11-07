"""Personal appearance is session-owned, durable and available to every role."""
import uuid

import pytest
import psycopg2

from app.core.config import settings
from app.core.security import create_session_token
from tests.conftest import MIGRATOR_SYNC_URL, _api_test_client
from tests.test_auth_hardening import temporary_auth_rows


@pytest.mark.anyio
@pytest.mark.parametrize('role', ['OWNER_ADMIN', 'CLERK', 'READ_ONLY'])
async def test_each_role_can_persist_only_its_own_theme_and_login_contract_stays_unchanged(role):
    rows = [{'email': f'theme-{uuid.uuid4()}@example.test', 'role': role}]
    with temporary_auth_rows(rows):
        identity = rows[0]
        cookie = create_session_token(identity['user_id'], identity['organization_id'])
        async with _api_test_client(cookies={settings.session_cookie_name: cookie}) as client:
            assert (await client.get('/api/auth/preferences')).json() == {'design_theme': 'klar'}
            changed = await client.patch('/api/auth/preferences', json={'design_theme': 'warm'})
            assert changed.status_code == 200, changed.text
            assert changed.json() == {'design_theme': 'warm'}
            await client.post('/api/auth/logout')
            login = await client.post('/api/auth/login', json={'email': identity['email'], 'password': 'testpass1234'})
            assert set(login.json()) == {'user_id', 'organization_id', 'role'}
            assert (await client.get('/api/auth/preferences')).json() == {'design_theme': 'warm'}
        async with _api_test_client(cookies={settings.session_cookie_name: cookie}) as reloaded:
            assert (await reloaded.get('/api/auth/preferences')).json() == {'design_theme': 'warm'}


@pytest.mark.anyio
async def test_theme_does_not_change_a_colleague_or_another_organization(client_a_admin, client_a_clerk, client_b_admin):
    clients = [client_a_admin, client_a_clerk, client_b_admin]
    before = [(await client.get('/api/auth/preferences')).json() for client in clients]
    try:
        assert (await client_a_admin.patch('/api/auth/preferences', json={'design_theme': 'warm'})).status_code == 200
        assert (await client_a_admin.get('/api/auth/preferences')).json() == {'design_theme': 'warm'}
        for client, expected in zip(clients[1:], before[1:]):
            assert (await client.get('/api/auth/preferences')).json() == expected
    finally:
        if 'design_theme' in before[0]:
            await client_a_admin.patch('/api/auth/preferences', json=before[0])


@pytest.mark.anyio
@pytest.mark.parametrize('body', [
    {}, {'design_theme': None}, {'design_theme': 'dark'}, {'design_theme': 'Klar'},
    {'design_theme': 'warm', 'user_id': str(uuid.uuid4())},
    {'design_theme': 'warm', 'organization_id': str(uuid.uuid4())},
    {'design_theme': 'warm', 'role': 'OWNER_ADMIN'},
])
async def test_preferences_reject_invalid_values_and_extra_identity_fields(client_a_readonly, body):
    response = await client_a_readonly.patch('/api/auth/preferences', json=body)
    assert response.status_code == 422, response.text


@pytest.mark.anyio
async def test_preferences_require_an_active_authenticated_member(client_unauth):
    for method in ('get', 'patch'):
        response = await getattr(client_unauth, method)('/api/auth/preferences', **({'json': {'design_theme': 'warm'}} if method == 'patch' else {}))
        assert response.status_code == 401
    rows = [{'email': f'theme-revoked-{uuid.uuid4()}@example.test', 'role': 'READ_ONLY'}]
    with temporary_auth_rows(rows):
        row = rows[0]
        token = create_session_token(row['user_id'], row['organization_id'])
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
            cursor.execute('UPDATE public."user" SET is_active=false WHERE id=%s', (str(row['user_id']),))
        async with _api_test_client(cookies={settings.session_cookie_name: token}) as client:
            assert (await client.get('/api/auth/preferences')).status_code == 401
            assert (await client.patch('/api/auth/preferences', json={'design_theme': 'warm'})).status_code == 401


def test_theme_database_constraint_refuses_unknown_values(seed_data):
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
        with connection.cursor() as cursor:
            with pytest.raises(psycopg2.errors.CheckViolation):
                cursor.execute('UPDATE public."user" SET design_theme=\'unknown\' WHERE id=%s', (str(seed_data['user_a_admin_id']),))
        connection.rollback()
