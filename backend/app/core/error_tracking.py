"""Opt-in, errors-only Sentry-compatible reporting with filtered payloads."""

from contextvars import ContextVar

import sentry_sdk
from sentry_sdk.integrations.dedupe import DedupeIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app.core.config import Settings
from app.core.request_id import REPORTED_HTTP_STATUS_CODES, request_id
from app.core.scrub import REQUEST_HEADER_ALLOWLIST, scrub

# The middleware owns/reset this dictionary; authentication fills it only after
# database-backed principal validation. No client-supplied tenant tag is trusted.
request_state: ContextVar[dict | None] = ContextVar("tracking_request_state", default=None)


def before_send(event, hint):
    tags = event.setdefault("tags", {})
    tags["request_id"] = request_id.get()
    state = request_state.get() or {}
    if state.get("organization_id") is not None:
        tags["organization_id"] = str(state["organization_id"])
    return scrub(event)


def configure_error_tracking(settings: Settings, *, release: str, transport=None):
    dsn = settings.error_tracking_dsn.get_secret_value().strip()
    if not dsn:
        return None
    sentry_sdk.init(
        dsn=dsn,
        environment=settings.error_tracking_environment,
        release=release,
        server_name="",
        sample_rate=settings.error_tracking_sample_rate,
        traces_sample_rate=None,
        data_collection={
            "user_info": False,
            "cookies": {"mode": "off"},
            "http_headers": {"request": {"mode": "allowlist", "terms": list(REQUEST_HEADER_ALLOWLIST)}},
            "http_bodies": [],
            "url_query_params": {"mode": "off"},
            "graphql": {"document": False, "variables": False},
            "gen_ai": {"inputs": False, "outputs": False},
            "database_query_data": False,
            "queues": False,
            "stack_frame_variables": False,
            "frame_context_lines": 0,
        },
        max_request_body_size="never",
        max_breadcrumbs=10,
        before_send=before_send,
        default_integrations=False,
        auto_enabling_integrations=False,
        integrations=[
            FastApiIntegration(failed_request_status_codes=REPORTED_HTTP_STATUS_CODES),
            StarletteIntegration(failed_request_status_codes=REPORTED_HTTP_STATUS_CODES),
            DedupeIntegration(),
            LoggingIntegration(event_level=None),
        ],
        auto_session_tracking=False,
        send_client_reports=False,
        transport=transport,
    )
    return sentry_sdk.get_client()
