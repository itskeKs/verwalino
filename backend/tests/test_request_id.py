"""Correlation across HTTP responses, application logs and worker jobs."""

import asyncio
import importlib
import io
import logging
import uuid

import httpx
import pytest
from fastapi import HTTPException, Request

from app.main import app


@pytest.fixture
def error_routes(monkeypatch):
    monkeypatch.setattr(app.router, "routes", list(app.router.routes))

    @app.get("/api/test-request-id/{kind}")
    async def endpoint(kind: str, request: Request, number: int = 1):
        logging.getLogger("app.services.test").warning("Service called")
        logging.getLogger("sqlalchemy.engine").warning("Database called")
        if kind == "crash":
            raise RuntimeError("private failure")
        if kind == "http":
            raise HTTPException(503, "Unavailable", headers={"Retry-After": "10"})
        if kind == "client":
            raise HTTPException(409, "Conflict")
        await asyncio.sleep(0)
        return {"request_id": request.state.request_id}

    return "/api/test-request-id"


@pytest.mark.parametrize("supplied", [None, "short", "a" * 129, "invalid id", "bad\tvalue", "ü" * 8])
async def test_missing_or_invalid_header_generates_uuid(supplied, error_routes):
    headers = [] if supplied is None else [(b"x-request-id", supplied.encode("utf-8"))]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(error_routes + "/ok", headers=headers)
    value = response.headers["X-Request-ID"]
    assert str(uuid.UUID(value, version=4)) == value
    assert response.json()["request_id"] == value


@pytest.mark.parametrize("supplied", ["a.B_c-12", "a" * 128, "12345678"])
async def test_valid_header_is_echoed_and_available_to_services(supplied, error_routes, caplog):
    with caplog.at_level(logging.WARNING):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(error_routes + "/ok", headers={"X-Request-ID": supplied})
    assert response.headers["X-Request-ID"] == response.json()["request_id"] == supplied
    records = [r for r in caplog.records if r.name in {"app.services.test", "sqlalchemy.engine"}]
    assert len(records) == 2
    assert all(record.request_id == supplied for record in records)


@pytest.mark.parametrize(("path", "status"), [("/missing/route", 404), ("/ok?number=no", 422), ("/crash", 500), ("/http", 503), ("/client", 409)])
async def test_error_responses_keep_the_correlation_header(path, status, error_routes, caplog):
    from app.core.logging import RequestFormatter
    caplog.handler.setFormatter(RequestFormatter())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        response = await client.get(error_routes + path, headers={"X-Request-ID": "error-id-123"})
    assert response.status_code == status
    assert response.headers["X-Request-ID"] == "error-id-123"
    if status >= 500:
        assert response.json()["request_id"] == "error-id-123"
        assert "private failure" not in response.text
        expected_level = logging.WARNING if status == 503 else logging.ERROR
        assert any(r.levelno == expected_level and r.request_id == "error-id-123" for r in caplog.records)
        assert "request_id=error-id-123" in caplog.text
    else:
        assert "request_id" not in response.json()
    if status == 503:
        assert response.json()["detail"] == "Unavailable"
        assert response.headers["Retry-After"] == "10"


async def test_unhandled_exceptions_propagate_and_reset_request_context(error_routes, caplog):
    from app.core.error_tracking import request_state
    from app.core.request_id import request_id

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="private failure"):
            await client.get(error_routes + "/crash", headers={"X-Request-ID": "propagate-123"})
    assert request_id.get() == "-"
    assert request_state.get() is None
    records = [record for record in caplog.records if record.name == "app.core.request_id"]
    assert len(records) == 1
    assert records[0].request_id == "propagate-123"


async def test_upload_limit_response_also_has_a_request_id():
    from app.core.uploads import MAX_REQUEST_BODY_BYTES

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/invoice-documents/upload", headers={"Content-Length": str(MAX_REQUEST_BODY_BYTES + 1), "X-Request-ID": "upload-error-123"})
    assert response.status_code == 413
    assert response.headers["X-Request-ID"] == "upload-error-123"


async def test_perimeter_and_spa_responses_have_request_ids(tmp_path, monkeypatch):
    import app.main as main

    (tmp_path / "index.html").write_text("safe spa")
    monkeypatch.setenv("HV_FRONTEND_DIST", str(tmp_path))
    original_app = main.app
    production = importlib.reload(main).app
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=production), base_url="http://test") as client:
            for path, status in [("/fristen", 200), ("/api/missing", 404), ("/api", 404)]:
                response = await client.get(path)
                assert response.status_code == status
                assert uuid.UUID(response.headers["X-Request-ID"])
            rejected = await client.get("/", headers={"Host": "untrusted.invalid"})
            assert rejected.status_code == 400
            assert rejected.headers["X-Request-ID"]
    finally:
        main.app = original_app


async def test_concurrent_requests_do_not_leak_context(error_routes):
    from app.core.request_id import request_id

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        ids = [f"concurrent-{index}" for index in range(12)]
        responses = await asyncio.gather(*(client.get(error_routes + "/ok", headers={"X-Request-ID": value}) for value in ids))
    assert [response.json()["request_id"] for response in responses] == ids
    assert request_id.get() == "-"


def test_formatter_always_includes_correlation_and_worker_context():
    from app.core.logging import RequestFormatter
    from app.core.request_id import correlation_context

    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(RequestFormatter())
    logger = logging.getLogger("test.worker")
    logger.addHandler(handler)
    try:
        with correlation_context("job-123456"):
            logger.warning("Worker called")
        logger.warning("Outside job")
    finally:
        logger.removeHandler(handler)
    assert "request_id=job-123456" in output.getvalue()
    assert "request_id=-" in output.getvalue()


def test_pdf_dependency_loggers_do_not_emit_info_records():
    from app.core.logging import configure_logging

    configure_logging()
    for name in ("weasyprint", "fontTools"):
        assert not logging.getLogger(name).isEnabledFor(logging.INFO)
        assert logging.getLogger(name).isEnabledFor(logging.WARNING)
