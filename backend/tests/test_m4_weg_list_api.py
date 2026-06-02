"""Additive M4 read endpoint: GET /api/weg-settlements (run_type-filtered list).

Mirrors the BK settlements list so the frontend can list WEG-Jahresabrechnungen
and Vermoegensberichte. Minimal coverage: auth, run_type filter, RBAC-read.
"""

import pytest


class TestWegRunList:
    @pytest.mark.asyncio
    async def test_list_default_run_type_ok(self, client_a_admin, seed_data):
        resp = await client_a_admin.get("/api/weg-settlements")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)  # empty for a fresh org, but valid

    @pytest.mark.asyncio
    async def test_list_asset_reports_ok(self, client_a_admin, seed_data):
        resp = await client_a_admin.get(
            "/api/weg-settlements?run_type=VERMOEGENSBERICHT"
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_list_invalid_run_type_422(self, client_a_admin, seed_data):
        resp = await client_a_admin.get("/api/weg-settlements?run_type=BK_MIETER")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_list_requires_auth(self, client_unauth):
        resp = await client_unauth.get("/api/weg-settlements")
        assert resp.status_code == 401
