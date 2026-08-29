"""Security tests for the production SPA and health endpoints."""

from __future__ import annotations

import importlib
import os

import httpx
import pytest

from app.core.static_files import resolve_spa_file
from app.core.config import Settings
from app.main import app


def test_production_settings_parse_exact_host_and_disable_docs():
    production = Settings(
        session_cookie_name="__Host-hv_session",
        session_cookie_secure=True,
        allowed_hosts="verwaltung.karat.immo",
        enable_api_docs=False,
    )

    assert production.session_cookie_name == "__Host-hv_session"
    assert production.session_cookie_secure is True
    assert production.allowed_host_list == ["verwaltung.karat.immo"]
    assert production.docs_url is None
    assert production.openapi_url is None


@pytest.fixture
def dist_dir(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / "app.js").write_text("console.log('ok')")
    (dist / "index.html").write_text("<html></html>")
    return dist


def test_resolve_spa_file_returns_regular_file_inside_dist(dist_dir):
    assert resolve_spa_file(dist_dir, "assets/app.js") == (dist_dir / "assets/app.js")


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "assets/../../outside.txt",
        "/etc/passwd",
        "%2e%2e/outside.txt",
        "%00",
        r"..\\outside.txt",
    ],
)
def test_resolve_spa_file_rejects_escaping_paths(dist_dir, tmp_path, path):
    (tmp_path / "outside.txt").write_text("private")
    assert resolve_spa_file(dist_dir, path) == (dist_dir / "index.html")


def test_resolve_spa_file_rejects_symlink_pointing_outside_dist(dist_dir, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    os.symlink(outside, dist_dir / "leak.txt")

    assert resolve_spa_file(dist_dir, "leak.txt") == (dist_dir / "index.html")


@pytest.mark.asyncio
async def test_production_spa_catchall_rejects_traversal_and_keeps_api_404(
    tmp_path, monkeypatch
):
    import app.main as main_module

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("safe spa")
    (tmp_path / "secret.txt").write_text("private")
    monkeypatch.setenv("HV_FRONTEND_DIST", str(dist))
    original_app = main_module.app
    production_main = importlib.reload(main_module)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=production_main.app), base_url="http://test"
        ) as client:
            traversal = await client.get("/%2e%2e/secret.txt")
            api_root = await client.get("/api")
            api_path = await client.get("/api/not-a-route")

        assert traversal.status_code == 200
        assert traversal.text == "safe spa"
        assert api_root.status_code == 404
        assert api_path.status_code == 404
    finally:
        monkeypatch.delenv("HV_FRONTEND_DIST", raising=False)
        # Other modules and the shared test clients retain the original app
        # object. Restore that exact object instead of reloading a third app,
        # otherwise later tests override dependencies on a different instance.
        main_module.app = original_app


@pytest.mark.asyncio
async def test_liveness_does_not_depend_on_database():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_reports_ok_when_database_and_storage_are_available():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_legacy_health_endpoint_is_readiness_alias():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_returns_unavailable_when_database_probe_fails(monkeypatch):
    from app.core import health

    async def unavailable_database():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(health, "probe_database", unavailable_database)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


@pytest.mark.asyncio
async def test_readiness_logs_redacted_database_probe_failure(caplog, monkeypatch):
    from app.core import health

    async def unavailable_database():
        raise RuntimeError(
            "connection failed for postgresql://user:SECRET@host/db"
        )

    monkeypatch.setattr(health, "probe_database", unavailable_database)
    with caplog.at_level("WARNING", logger="app.core.health"):
        assert await health.is_ready() is False

    assert "RuntimeError: connection failed" in caplog.text
    assert "SECRET" not in caplog.text
    assert "Traceback" not in caplog.text
    assert "document_storage_root" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("detail", "secrets"),
    [
        ("connection failed for postgresql://:URI_SECRET@host/db", ["URI_SECRET"]),
        ("database_password=DATABASE_SECRET", ["DATABASE_SECRET"]),
        ("client_secret: CLIENT_SECRET", ["CLIENT_SECRET"]),
        (
            "access_token=ACCESS_SECRET api_key=API_SECRET",
            ["ACCESS_SECRET", "API_SECRET"],
        ),
    ],
)
async def test_readiness_redacts_reviewer_credential_examples(
    caplog, monkeypatch, detail, secrets
):
    from app.core import health

    async def unavailable_database():
        raise RuntimeError(detail)

    monkeypatch.setattr(health, "probe_database", unavailable_database)
    with caplog.at_level("WARNING", logger="app.core.health"):
        assert await health.is_ready() is False

    for secret in secrets:
        assert secret not in caplog.text
    assert "RuntimeError:" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_readiness_returns_unavailable_when_storage_probe_fails(monkeypatch):
    from app.core import health

    async def unavailable_storage():
        raise OSError("volume unavailable")

    monkeypatch.setattr(health, "probe_document_storage", unavailable_storage)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
