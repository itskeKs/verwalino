"""Public metadata exposes only deployment facts and mail capability."""

import pytest
import httpx

from app.core.config import settings
from app.main import app


@pytest.mark.parametrize("mode", ["selfhost", "hosted"])
async def test_meta_is_public_and_contains_only_deployment_facts_and_mail_capability(monkeypatch, mode):
    monkeypatch.setattr(settings, "deployment_mode", mode)
    monkeypatch.setattr(settings, "smtp_host", None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/meta")
    assert response.status_code == 200
    assert response.json() == {"deployment_mode": mode, "version": app.version, "mail_available": False}
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize('origin,available', [('https://signup.example.test', True), (None, False), ('https://user:password@example.test', False), ('http://example.test', False)])
async def test_meta_reports_only_usable_mail_configuration(client_unauth, signup_mail, monkeypatch, origin, available):
    monkeypatch.setattr(settings, 'public_url', origin)
    response = await client_unauth.get('/api/meta')
    assert response.json()['mail_available'] is available
    assert set(response.json()) == {'deployment_mode', 'version', 'mail_available'}


from tests.signup_support import signup_mail  # noqa: E402,F401
