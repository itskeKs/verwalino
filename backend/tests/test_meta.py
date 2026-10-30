"""Public deployment metadata contains only the mode and application version."""

import pytest
import httpx

from app.core.config import settings
from app.main import app


@pytest.mark.parametrize("mode", ["selfhost", "hosted"])
async def test_meta_is_public_and_contains_only_mode_and_version(monkeypatch, mode):
    monkeypatch.setattr(settings, "deployment_mode", mode)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/meta")
    assert response.status_code == 200
    assert response.json() == {"deployment_mode": mode, "version": app.version}
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
