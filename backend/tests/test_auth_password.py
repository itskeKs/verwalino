"""Tests for T14: password change endpoint."""

import pytest
import httpx

from app.core.config import settings


@pytest.mark.anyio
async def test_change_password_success(client_a_admin):
    """Existing password must be verified, new password hashed and stored."""
    # Change password
    resp = await client_a_admin.post(
        "/api/auth/password",
        json={"old_password": "testpass1234", "new_password": "NewPassword456!"},
    )
    assert resp.status_code == 204

    # Verify the new password works by logging in
    resp = await client_a_admin.post(
        "/api/auth/login",
        json={"email": "admin@alpha.de", "password": "NewPassword456!"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "OWNER_ADMIN"

    # Old password must no longer work
    old_login = await client_a_admin.post(
        "/api/auth/login",
        json={"email": "admin@alpha.de", "password": "testpass1234"},
    )
    assert old_login.status_code == 401


@pytest.mark.anyio
async def test_change_password_wrong_old(client_a_admin):
    """Wrong old password must return 401."""
    resp = await client_a_admin.post(
        "/api/auth/password",
        json={"old_password": "WrongPassword123!", "new_password": "NewPassword456!"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_change_password_weak_new(client_a_admin):
    """New password below minimum length must be rejected."""
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
