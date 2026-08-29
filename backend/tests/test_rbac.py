"""RBAC tests -- architecture.md section 7.4, assertion 6.

Tests that roles are enforced:
  - READ_ONLY can read, but cannot create/modify.
  - CLERK can operate (create data), but cannot manage users.
  - OWNER_ADMIN can do everything within their org.

Combined with isolation: all within the same org (org_a).
"""

import pytest


class TestReadOnlyRole:
    """READ_ONLY can read but not write."""

    @pytest.mark.asyncio
    async def test_readonly_can_list_mandates(self, client_a_readonly):
        resp = await client_a_readonly.get("/api/mandates")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_readonly_can_list_users(self, client_a_readonly):
        resp = await client_a_readonly.get("/api/users")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_readonly_can_view_org(self, client_a_readonly):
        resp = await client_a_readonly.get("/api/organizations/me")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_readonly_cannot_create_mandate(self, client_a_readonly):
        resp = await client_a_readonly.post(
            "/api/mandates",
            json={
                "mandate_type": "MIET",
                "label": "Should Fail",
                "start_date": "2025-01-01",
            },
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_readonly_cannot_create_user(self, client_a_readonly):
        resp = await client_a_readonly.post(
            "/api/users",
            json={
                "email": "readonly-attempt@alpha.de",
                "password": "testpassword12",
                "display_name": "Should Fail",
            },
        )
        assert resp.status_code == 403


class TestClerkRole:
    """CLERK can operate but not manage users/roles."""

    @pytest.mark.asyncio
    async def test_clerk_can_list_mandates(self, client_a_clerk):
        resp = await client_a_clerk.get("/api/mandates")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_clerk_cannot_create_mandate(self, client_a_clerk):
        """Mandates are OWNER_ADMIN only (per SDD section 4 role matrix)."""
        resp = await client_a_clerk.post(
            "/api/mandates",
            json={
                "mandate_type": "MIET",
                "label": "Clerk Attempt",
                "start_date": "2025-01-01",
            },
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_clerk_cannot_create_user(self, client_a_clerk):
        resp = await client_a_clerk.post(
            "/api/users",
            json={
                "email": "clerk-attempt@alpha.de",
                "password": "testpassword12",
            },
        )
        assert resp.status_code == 403


class TestOwnerAdminRole:
    """OWNER_ADMIN can do everything within their org."""

    @pytest.mark.asyncio
    async def test_admin_can_list_mandates(self, client_a_admin):
        resp = await client_a_admin.get("/api/mandates")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_can_create_mandate(self, client_a_admin):
        resp = await client_a_admin.post(
            "/api/mandates",
            json={
                "mandate_type": "SEV",
                "label": "Admin Created SEV",
                "start_date": "2025-03-01",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["mandate_type"] == "SEV"
        assert data["label"] == "Admin Created SEV"

    @pytest.mark.asyncio
    async def test_admin_can_create_user(self, client_a_admin):
        resp = await client_a_admin.post(
            "/api/users",
            json={
                "email": f"newuser-{__import__('uuid').uuid4().hex[:8]}@alpha.de",
                "password": "securepass123",
                "display_name": "New User",
                "role": "CLERK",
            },
        )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_admin_can_view_org(self, client_a_admin):
        resp = await client_a_admin.get("/api/organizations/me")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Verwaltung Alpha"


class TestCrossRoleIsolation:
    """OWNER_ADMIN of org_b cannot perform admin actions on org_a resources."""

    @pytest.mark.asyncio
    async def test_org_b_admin_cannot_see_org_a_users(self, client_b_admin, seed_data):
        resp = await client_b_admin.get(f"/api/users/{seed_data['user_a_admin_id']}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_org_b_admin_sees_own_org(self, client_b_admin):
        resp = await client_b_admin.get("/api/organizations/me")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Verwaltung Beta"
