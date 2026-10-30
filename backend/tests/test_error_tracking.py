"""Offline tracker transport and shared privacy filtering contracts."""

import copy
import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request

from app.core.tenant import get_current_user

from app.main import app


PRIVATE_MESSAGE = 'Max Mustermann DE89 3704 0044 0532 0130 00 max@example.org Bearer super-secret-token {"password": "json-private", "access_token": "json-token"}'


@pytest.fixture
def tracking_routes(monkeypatch):
    monkeypatch.setattr(app.router, "routes", list(app.router.routes))

    @app.post("/api/test-tracker/{kind}")
    async def crash(kind: str, request: Request):
        import sentry_sdk
        sentry_sdk.add_breadcrumb(message=PRIVATE_MESSAGE, data={"password": "breadcrumb-password"})
        if kind == "http":
            raise HTTPException(500, "Internal Server Error")
        raise RuntimeError(PRIVATE_MESSAGE)

    @app.get("/api/test-tracker-authenticated")
    async def authenticated_crash(auth=Depends(get_current_user)):
        raise RuntimeError("Authenticated failure")

    return "/api/test-tracker"


@pytest.fixture
def offline_tracker(monkeypatch):
    import sentry_sdk
    from sentry_sdk.transport import Transport
    from app.core.config import Settings
    from app.core.error_tracking import configure_error_tracking

    events = []

    class OfflineTransport(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.headers.get("type") == "event":
                    events.append(item.payload.json)
                else:
                    pytest.fail("Only error events may be sent")

    # A real transport is always a test failure, including SDK background work.
    monkeypatch.setattr("sentry_sdk.transport.HttpTransport.capture_envelope", lambda *a, **kw: pytest.fail("Network transport used"))
    previous = sentry_sdk.get_global_scope().client
    with sentry_sdk.isolation_scope():
        client = configure_error_tracking(Settings(_env_file=None, error_tracking_dsn="https://public@tracker.invalid/1", error_tracking_environment="test"), release="0.1.0", transport=OfflineTransport)
        try:
            yield events, client
        finally:
            client.close()
            sentry_sdk.get_global_scope().set_client(previous)


@pytest.mark.parametrize("kind", ["crash", "http"])
async def test_server_error_is_one_offline_event_with_the_response_id(kind, tracking_routes, offline_tracker):
    events, client = offline_tracker
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as browser:
        response = await browser.post(tracking_routes + "/" + kind + "?email=query-secret", headers={"X-Request-ID": "tracking-123", "Authorization": "Bearer header-secret", "Cookie": "hv_session=cookie-secret"}, json={"name": "Body Name", "password": "body-secret"})
    client.flush()
    assert response.status_code >= 500
    assert len(events) == 1
    event = events[0]
    assert event["tags"]["request_id"] == response.headers["X-Request-ID"] == response.json()["request_id"]
    assert event["release"] == "0.1.0"
    assert event["environment"] == "test"
    assert not event.get("server_name")
    serialized = json.dumps(event)
    for secret in ("Max Mustermann", "DE89 3704 0044 0532 0130 00", "max@example.org", "super-secret-token", "json-private", "json-token", "breadcrumb-password", "query-secret", "header-secret", "cookie-secret", "body-secret", "Body Name"):
        assert secret not in serialized
    assert "data" not in event.get("request", {})


async def test_authenticated_error_tags_only_the_validated_organization(tracking_routes, offline_tracker, client_a_admin, seed_data):
    events, client = offline_tracker
    response = await client_a_admin.get("/api/test-tracker-authenticated", headers={"X-Request-ID": "tenant-error-123", "organization_id": str(seed_data["org_b_id"])})
    assert response.status_code == 500
    assert len(events) == 1
    assert events[0]["tags"]["organization_id"] == str(seed_data["org_a_id"])
    assert "Verwaltung Alpha" not in json.dumps(events)
    assert "Admin Alpha" not in json.dumps(events)


@pytest.mark.parametrize("handled", [False, True])
async def test_fresh_instrumented_stack_reports_the_error_once(handled, offline_tracker):
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from app.core.request_id import RequestIdMiddleware, http_error, unhandled_error

    fresh = FastAPI()
    fresh.add_middleware(RequestIdMiddleware)
    fresh.add_exception_handler(StarletteHTTPException, http_error)
    fresh.add_exception_handler(Exception, unhandled_error)

    @fresh.get("/failure")
    async def failure():
        if handled:
            raise HTTPException(500, "Internal Server Error")
        raise RuntimeError(PRIVATE_MESSAGE)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fresh), base_url="http://test") as browser:
        response = await browser.get("/failure", headers={"X-Request-ID": "fresh-stack-123"})
    events, client = offline_tracker
    assert response.status_code >= 500
    assert len(events) == 1
    assert events[0]["tags"]["request_id"] == "fresh-stack-123"


@pytest.mark.parametrize("status", [501, 502, 503, 504])
async def test_expected_http_failures_are_warnings_without_tracker_events(status, offline_tracker, caplog):
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from app.core.request_id import RequestIdMiddleware, http_error

    fresh = FastAPI()
    fresh.add_middleware(RequestIdMiddleware)
    fresh.add_exception_handler(StarletteHTTPException, http_error)

    @fresh.get("/unavailable")
    async def unavailable():
        raise HTTPException(status, "Feature disabled", headers={"Retry-After": "10"})

    with caplog.at_level(logging.WARNING):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fresh), base_url="http://test") as browser:
            response = await browser.get("/unavailable", headers={"X-Request-ID": "disabled-123"})
    events, client = offline_tracker
    client.flush()
    assert response.status_code == status
    assert response.headers["X-Request-ID"] == response.json()["request_id"] == "disabled-123"
    assert response.headers["Retry-After"] == "10"
    assert response.json()["detail"] == "Feature disabled"
    assert events == []
    records = [record for record in caplog.records if record.name == "app.core.request_id"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].request_id == "disabled-123"


@pytest.mark.parametrize("protocol", ["h11", "httptools"])
async def test_midstream_failure_is_reported_once_with_the_request_id(protocol, offline_tracker, caplog):
    from fastapi.responses import StreamingResponse
    from uvicorn.protocols.http import h11_impl, httptools_impl
    from app.core.request_id import RequestIdMiddleware, request_id, unhandled_error

    fresh = FastAPI()
    fresh.add_middleware(RequestIdMiddleware)
    fresh.add_exception_handler(Exception, unhandled_error)

    @fresh.get("/stream")
    async def stream():
        async def chunks():
            yield b"partial"
            raise RuntimeError(PRIVATE_MESSAGE)
        return StreamingResponse(chunks())

    messages = []

    async def receive():
        pytest.fail("ASGI 2.4 stream should not need a disconnect listener")

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.start":
            cycle.response_started = True

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1", "method": "GET", "scheme": "http", "path": "/stream", "query_string": b"", "headers": [(b"x-request-id", b"stream-error-123")], "server": ("test", 80)}
    # Execute the installed server's exception wrapper without sockets. It must
    # still close an incomplete response without emitting a duplicate error.
    cycle = SimpleNamespace(scope=scope, receive=receive, send=send, logger=logging.getLogger("uvicorn.error"), response_started=False, response_complete=False, disconnected=False, transport=Mock())
    implementation = h11_impl if protocol == "h11" else httptools_impl
    await implementation.RequestResponseCycle.run_asgi(cycle, fresh)
    cycle.transport.close.assert_called_once()
    events, client = offline_tracker
    client.flush()
    assert messages[0]["status"] == 200
    assert (b"x-request-id", b"stream-error-123") in messages[0]["headers"]
    assert messages[1]["body"] == b"partial"
    assert not any(message.get("more_body") is False for message in messages)
    records = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(records) == len(events) == 1
    assert records[0].request_id == events[0]["tags"]["request_id"] == "stream-error-123"
    assert request_id.get() == "-"


def test_unreported_uvicorn_failures_are_still_logged(caplog):
    logging.getLogger("uvicorn.error").error("ASGI failure", exc_info=RuntimeError("unreported failure"))
    assert len(caplog.records) == 1
    assert caplog.records[0].name == "uvicorn.error"


def test_tracker_uses_supported_data_collection_controls(offline_tracker):
    _, client = offline_tracker
    assert client.options["send_default_pii"] is None
    for category in ("user_info", "database_query_data", "stack_frame_variables"):
        assert client.options["data_collection"][category] is False
    assert client.options["data_collection"]["http_bodies"] == []
    assert client.options["data_collection"]["cookies"] == {"mode": "off"}
    assert client.options["data_collection"]["http_headers"]["request"] == {"mode": "allowlist", "terms": ["content-type", "content-length", "accept", "user-agent", "x-request-id"]}
    assert client.options["data_collection"]["url_query_params"] == {"mode": "off"}
    assert client.options["data_collection"]["frame_context_lines"] == 0


@pytest.mark.parametrize("level", [logging.INFO, logging.WARNING])
def test_logged_breadcrumb_names_are_masked_in_offline_events(level, offline_tracker, caplog):
    import sentry_sdk
    from app.core.request_id import correlation_context

    events, client = offline_tracker
    with caplog.at_level(level, logger="app.test.breadcrumb"):
        with correlation_context("breadcrumb-123"):
            logging.getLogger("app.test.breadcrumb").log(level, "Suche Mieter Erika Musterfrau")
            sentry_sdk.capture_exception(RuntimeError("lookup failed"))
    client.flush()
    assert len(events) == 1
    assert events[0]["tags"]["request_id"] == "breadcrumb-123"
    breadcrumbs = [item for item in events[0]["breadcrumbs"]["values"] if item.get("category") == "app.test.breadcrumb"]
    assert len(breadcrumbs) == 1
    assert breadcrumbs[0]["level"] == logging.getLevelName(level).lower()
    assert "Erika" not in json.dumps(events)
    assert "Musterfrau" not in json.dumps(events)
    assert "[redacted:name]" in breadcrumbs[0]["message"]


def test_tracker_reports_every_error_without_configurable_sampling(offline_tracker):
    from app.core.config import Settings

    _, client = offline_tracker
    assert client.options["sample_rate"] == 1.0
    assert {key for key in Settings.model_fields if key.startswith("error_tracking_")} == {"error_tracking_dsn", "error_tracking_environment"}


async def test_empty_dsn_never_initializes_a_transport(tracking_routes, monkeypatch):
    import sentry_sdk
    from app.core.config import Settings
    from app.core.error_tracking import configure_error_tracking

    monkeypatch.setenv("SENTRY_DSN", "https://public@tracker.invalid/1")
    monkeypatch.setattr(sentry_sdk, "init", lambda *a, **kw: pytest.fail("Disabled tracker initialized"))
    assert configure_error_tracking(Settings(_env_file=None, error_tracking_dsn=""), release="0.1.0") is None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as browser:
        response = await browser.post(tracking_routes + "/crash")
    assert response.status_code == 500


def test_scrubber_is_pure_and_removes_structured_sensitive_data():
    from app.core.scrub import scrub

    original = {
        "message": PRIVATE_MESSAGE + " +49 30 12345678 Hauptstraße 12 Musterstr. 4 GB82 WEST 1234 5698 7654 32 password='a secret' /path?first=one&second=two",
        "request": {"url": "https://test/path?name=Max%20Mustermann&token=query-token", "query_string": "first=one&second=two", "data": {"anything": "body-private"}, "headers": {"Authorization": "Bearer abc", "Cookie": "session=xyz", "X-Request-ID": "tracking-123"}},
        "user": {"name": "Unpatterned", "email": "user@example.org"},
        "exception": {"values": [{"stacktrace": {"frames": [{"vars": {"name": "Unpatterned"}}]}}]},
        "tags": {"request_id": "12345678", "organization_id": "9dbdd122-2427-4f90-bae3-6126ef537269"},
        "breadcrumbs": {"values": [{"data": {"session_token": "secret-token"}, "message": PRIVATE_MESSAGE}]},
    }
    before = copy.deepcopy(original)
    result = scrub(original)
    assert original == before
    serialized = json.dumps(result)
    for secret in ("Max Mustermann", "DE89", "max@example.org", "super-secret-token", "+49 30 12345678", "Hauptstraße 12", "Musterstr. 4", "GB82", "a secret", "first=one", "second=two", "body-private", "Bearer abc", "session=xyz", "Unpatterned", "query-token", "secret-token"):
        assert secret not in serialized
    for pattern in ("email", "iban", "phone", "address", "name", "token", "password", "query"):
        assert f"[redacted:{pattern}]" in serialized
    assert result["tags"] == original["tags"]
    assert result["request"]["headers"]["X-Request-ID"] == "tracking-123"
    assert "data" not in result["request"]


def test_error_log_formatter_scrubs_message_and_traceback_but_preserves_id():
    from app.core.logging import RequestFormatter
    from app.core.request_id import correlation_context

    try:
        raise RuntimeError(PRIVATE_MESSAGE)
    except RuntimeError as error:
        with correlation_context("12345678"):
            record = logging.getLogger("app.test").makeRecord("app.test", logging.ERROR, __file__, 1, "Failure %s", (PRIVATE_MESSAGE,), (type(error), error, error.__traceback__))
    formatted = RequestFormatter().format(record)
    assert "request_id=12345678" in formatted
    for value in ("Max Mustermann", "DE89", "max@example.org", "super-secret-token", "json-private", "json-token"):
        assert value not in formatted


@pytest.mark.parametrize("quote", ['"', "'"])
def test_quoted_credential_keys_are_scrubbed_in_serialized_messages(quote):
    from app.core.scrub import scrub_text

    message = f"{{{quote}password{quote}: {quote}private-password{quote}, {quote}access_token{quote}: {quote}private-token{quote}, {quote}Authorization{quote}: {quote}private-header{quote}}}"
    filtered = scrub_text(message)
    for value in ("private-password", "private-token", "private-header"):
        assert value not in filtered


def test_invalid_incoming_correlation_headers_are_not_exempt_from_scrubbing():
    from app.core.scrub import scrub

    event = {"request": {"headers": {"X-Request-ID": "alice@example.org", "organization_id": "Max Mustermann"}}, "tags": {"request_id": "12345678"}}
    filtered = scrub(event)
    assert "alice@example.org" not in json.dumps(filtered)
    assert "Max Mustermann" not in json.dumps(filtered)
    assert filtered["tags"]["request_id"] == "12345678"


def test_compressed_application_session_tokens_are_scrubbed(monkeypatch):
    import uuid
    from itsdangerous import URLSafeTimedSerializer
    from app.core import security
    from app.core.scrub import scrub_text

    monkeypatch.setattr(security, "_serializer", URLSafeTimedSerializer("synthetic-test-secret"))
    token = security.create_session_token(uuid.UUID(int=1), uuid.UUID(int=2))
    assert token.startswith(".")
    assert token not in scrub_text("Invalid credential " + token)
