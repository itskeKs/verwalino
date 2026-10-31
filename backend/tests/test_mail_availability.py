"""Public mail capability needs configuration only, without a database or SMTP."""
import asyncio

import httpx

from app.core.config import DeploymentMode, settings
from app.services.mail_service import password_reset_available


def test_selfhost_smtp_and_public_host_enable_mail_and_public_metadata(monkeypatch):
    from app.main import app

    for name, value in {
        "deployment_mode": DeploymentMode.SELFHOST,
        "public_host": "app.example.org",
        "smtp_host": "smtp.example.org",
        "smtp_port": 587,
        "smtp_from": "verwaltung@example.org",
        "smtp_security": "starttls",
        "smtp_username": None,
        "smtp_password": None,
    }.items():
        monkeypatch.setattr(settings, name, value)
    assert password_reset_available(settings) is True

    async def metadata():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/api/meta")

    response = asyncio.run(metadata())
    assert response.status_code == 200
    assert response.json()["deployment_mode"] == "selfhost"
    assert response.json()["mail_available"] is True
