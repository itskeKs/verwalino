"""Opt-in, errors-only Sentry-compatible reporting with filtered payloads."""

from contextvars import ContextVar

import sentry_sdk
from sentry_sdk.integrations.dedupe import DedupeIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app.core.config import Settings
from app.core.request_id import request_id
from app.core.scrub import scrub

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
        sample_rate=settings.error_tracking_sample_rate,
        traces_sample_rate=None,
        send_default_pii=False,
        include_local_variables=False,
        include_source_context=False,
        max_request_body_size="never",
        max_breadcrumbs=10,
        before_send=before_send,
        default_integrations=False,
        auto_enabling_integrations=False,
        integrations=[FastApiIntegration(), StarletteIntegration(), DedupeIntegration(), LoggingIntegration(event_level=None)],
        auto_session_tracking=False,
        send_client_reports=False,
        transport=transport,
    )
    return sentry_sdk.get_client()
