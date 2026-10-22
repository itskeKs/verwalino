"""Tests for T14: password change endpoint."""

import uuid

import pytest
import pytest_asyncio
import httpx

from app.core.config import settings
from app.core.security import create_session_token
from tests.conftest import _api_test_client
from tests.test_auth_hardening import temporary_auth_rows


@pytest_asyncio.fixture
async def password_identity():
    # Password changes durably revoke sessions; never mutate the shared seed user.
    rows = [{"email": f"password-{uuid.uuid4()}@example.test", "role": "OWNER_ADMIN"}]
    with temporary_auth_rows(rows):
        identity = rows[0]
        token = create_session_token(identity["user_id"], identity["organization_id"])
        async with _api_test_client(cookies={settings.session_cookie_name: token}) as client:
            yield client, identity["email"]


@pytest.mark.anyio
async def test_password_identity_preserves_local_api_routing(monkeypatch):
    from contextlib import aclosing

    monkeypatch.setenv("HV_TEST_LOCAL_API", "1")
    async with aclosing(password_identity.__wrapped__()) as identities:
        client, _ = await anext(identities)
        assert str(client.base_url) == "http://127.0.0.1:18000"
        assert isinstance(client._transport, httpx.AsyncHTTPTransport)


@pytest.mark.anyio
async def test_change_password_success(password_identity):
    """Existing password must be verified, new password hashed and stored."""
    client_a_admin, email = password_identity
    # Change password
    resp = await client_a_admin.post(
        "/api/auth/password",
        json={"old_password": "testpass1234", "new_password": "NewPassword456!"},
    )
    assert resp.status_code == 204

    # Verify the new password works by logging in
    resp = await client_a_admin.post(
        "/api/auth/login",
        json={"email": email, "password": "NewPassword456!"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "OWNER_ADMIN"

    # Old password must no longer work
    old_login = await client_a_admin.post(
        "/api/auth/login",
        json={"email": email, "password": "testpass1234"},
    )
    assert old_login.status_code == 401


@pytest.mark.anyio
async def test_change_password_wrong_old(password_identity):
    """Wrong old password must return 401."""
    client_a_admin, _ = password_identity
    resp = await client_a_admin.post(
        "/api/auth/password",
        json={"old_password": "WrongPassword123!", "new_password": "NewPassword456!"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_change_password_weak_new(password_identity):
    """New password below minimum length must be rejected."""
    client_a_admin, _ = password_identity
    resp = await client_a_admin.post(
        "/api/auth/password",
        json={"old_password": "testpass1234", "new_password": "short"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_change_password_unauthenticated(client_unauth):
    """Unauthenticated requests must return 401."""
    resp = await client_unauth.post(
        "/api/auth/password",
        json={"old_password": "testpass1234", "new_password": "NewPassword456!"},
    )
    assert resp.status_code == 401
