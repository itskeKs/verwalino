"""Tenant isolation tests -- the core of the M0 exit criterion.

Architecture.md section 7.4, assertions 1-5:

1. Read isolation (API): Client A sees only org_a data, never org_b.
2. Direct access to foreign ID (API): Client A fetching org_b resource -> 404.
3. Write isolation / WITH CHECK: Attempt to insert with foreign org_id -> rejected by DB.
4. RLS without app filter (DB level): Unfiltered SELECT with app_rw + GUC -> only own org rows.
5. Fail-closed without context: Session without GUC -> zero rows.
"""

import uuid

import pytest
from sqlalchemy import text


# ============================================================
# Assertion 1: Read isolation (API level)
# ============================================================
class TestReadIsolationAPI:
    """Client A sees only org_a data; Client B sees only org_b data."""

    @pytest.mark.asyncio
    async def test_list_mandates_org_a(self, client_a_admin, seed_data):
        resp = await client_a_admin.get("/api/mandates")
        assert resp.status_code == 200
        mandates = resp.json()
        assert len(mandates) >= 1
        ids = {m["id"] for m in mandates}
        # Must contain org_a's mandate
        assert str(seed_data["mandate_a_id"]) in ids
        # Must NOT contain org_b's mandate
        assert str(seed_data["mandate_b_id"]) not in ids

    @pytest.mark.asyncio
    async def test_list_mandates_org_b(self, client_b_admin, seed_data):
        resp = await client_b_admin.get("/api/mandates")
        assert resp.status_code == 200
        mandates = resp.json()
        assert len(mandates) >= 1
        ids = {m["id"] for m in mandates}
        assert str(seed_data["mandate_b_id"]) in ids
        assert str(seed_data["mandate_a_id"]) not in ids

    @pytest.mark.asyncio
    async def test_org_a_never_sees_org_b_mandates(self, client_a_admin, seed_data):
        resp = await client_a_admin.get("/api/mandates")
        assert resp.status_code == 200
        mandates = resp.json()
        ids = {m["id"] for m in mandates}
        # org_b mandate must NOT be visible to org_a
        assert str(seed_data["mandate_b_id"]) not in ids

    @pytest.mark.asyncio
    async def test_org_b_never_sees_org_a_mandates(self, client_b_admin, seed_data):
        resp = await client_b_admin.get("/api/mandates")
        assert resp.status_code == 200
        mandates = resp.json()
        ids = {m["id"] for m in mandates}
        assert str(seed_data["mandate_a_id"]) not in ids

    @pytest.mark.asyncio
    async def test_list_users_isolation(self, client_a_admin, client_b_admin, seed_data):
        resp_a = await client_a_admin.get("/api/users")
        resp_b = await client_b_admin.get("/api/users")
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200

        emails_a = {u["email"] for u in resp_a.json()}
        emails_b = {u["email"] for u in resp_b.json()}

        # No overlap
        assert emails_a & emails_b == set()
        # org_a has alpha users
        assert "admin@alpha.de" in emails_a
        assert "admin@beta.de" not in emails_a
        # org_b has beta users
        assert "admin@beta.de" in emails_b
        assert "admin@alpha.de" not in emails_b


# ============================================================
# Assertion 2: Direct access to foreign resource (API level)
# ============================================================
class TestDirectAccessForeignID:
    """Client A fetching a known org_b resource by UUID gets 404 (not 403)."""

    @pytest.mark.asyncio
    async def test_foreign_mandate_returns_404(self, client_a_admin, seed_data):
        # org_a tries to access org_b's mandate by its known UUID
        resp = await client_a_admin.get(f"/api/mandates/{seed_data['mandate_b_id']}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_foreign_user_returns_404(self, client_a_admin, seed_data):
        # org_a tries to access org_b's user
        resp = await client_a_admin.get(f"/api/users/{seed_data['user_b_admin_id']}")
        assert resp.status_code == 404


# ============================================================
# Assertion 3: Write isolation / WITH CHECK
# ============================================================
class TestWriteIsolation:
    """Attempt to create a row with a foreign organization_id is rejected by DB."""

    @pytest.mark.asyncio
    async def test_insert_mandate_with_foreign_org_rejected(
        self, app_rw_session, seed_data
    ):
        """Using app_rw with org_a context, try to INSERT a mandate for org_b."""
        session = app_rw_session
        # Set context to org_a
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(seed_data["org_a_id"])},
        )

        # Try to insert a mandate belonging to org_b -- WITH CHECK should reject
        with pytest.raises(Exception):
            await session.execute(
                text(
                    "INSERT INTO management_mandate "
                    "(id, organization_id, mandate_type, label, start_date) "
                    "VALUES (:id, :org, 'WEG', 'Sneaky', '2025-01-01')"
                ),
                {"id": uuid.uuid4(), "org": str(seed_data["org_b_id"])},
            )


# ============================================================
# Assertion 4: RLS without app-layer filter (DB level)
# ============================================================
class TestRLSWithoutAppFilter:
    """Direct unfiltered SELECT via app_rw with GUC set returns only own org rows.

    This is THE defense-in-depth test: even if the application layer forgets
    the WHERE org_id = ... filter, RLS ensures no data leaks.
    """

    @pytest.mark.asyncio
    async def test_unfiltered_select_mandates(self, app_rw_session, seed_data):
        session = app_rw_session
        # Set context to org_a
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(seed_data["org_a_id"])},
        )

        # Unfiltered SELECT -- no WHERE clause
        result = await session.execute(text("SELECT id, organization_id FROM management_mandate"))
        rows = result.fetchall()

        assert len(rows) >= 1
        for row in rows:
            assert str(row.organization_id) == str(seed_data["org_a_id"])

    @pytest.mark.asyncio
    async def test_unfiltered_select_users(self, app_rw_session, seed_data):
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(seed_data["org_b_id"])},
        )

        result = await session.execute(text('SELECT id, organization_id FROM "user"'))
        rows = result.fetchall()

        assert len(rows) >= 1
        for row in rows:
            assert str(row.organization_id) == str(seed_data["org_b_id"])

    @pytest.mark.asyncio
    async def test_unfiltered_select_audit_events(self, app_rw_session, seed_data):
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(seed_data["org_a_id"])},
        )

        result = await session.execute(text("SELECT id, organization_id FROM audit_event"))
        rows = result.fetchall()

        for row in rows:
            assert str(row.organization_id) == str(seed_data["org_a_id"])


# ============================================================
# Assertion 5: Fail-closed without context
# ============================================================
class TestFailClosed:
    """Session without app.current_org_id set returns zero rows -- never all rows."""

    @pytest.mark.asyncio
    async def test_no_context_returns_zero_mandates(self, app_rw_session):
        session = app_rw_session
        # Explicitly set to empty string (simulates unset GUC)
        await session.execute(
            text("SELECT set_config('app.current_org_id', '', true)")
        )

        result = await session.execute(text("SELECT * FROM management_mandate"))
        rows = result.fetchall()
        assert len(rows) == 0, "Fail-closed violated: got rows without tenant context"

    @pytest.mark.asyncio
    async def test_no_context_returns_zero_users(self, app_rw_session):
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', '', true)")
        )

        result = await session.execute(text('SELECT * FROM "user"'))
        rows = result.fetchall()
        assert len(rows) == 0, "Fail-closed violated: got rows without tenant context"

    @pytest.mark.asyncio
    async def test_no_context_returns_zero_audit_events(self, app_rw_session):
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', '', true)")
        )

        result = await session.execute(text("SELECT * FROM audit_event"))
        rows = result.fetchall()
        assert len(rows) == 0

    @pytest.mark.asyncio
    async def test_bogus_uuid_returns_zero(self, app_rw_session):
        """A random UUID that doesn't match any org also returns zero."""
        session = app_rw_session
        bogus = str(uuid.uuid4())
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": bogus},
        )

        result = await session.execute(text("SELECT * FROM management_mandate"))
        rows = result.fetchall()
        assert len(rows) == 0


# ============================================================
# Unauthenticated access
# ============================================================
class TestUnauthenticatedAccess:
    """Endpoints require authentication."""

    @pytest.mark.asyncio
    async def test_unauthenticated_gets_401(self, client_unauth):
        for path in ["/api/mandates", "/api/users", "/api/organizations/me"]:
            resp = await client_unauth.get(path)
            assert resp.status_code == 401, f"Expected 401 for {path}, got {resp.status_code}"
