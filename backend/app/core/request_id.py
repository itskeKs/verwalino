"""HTTP and background-job correlation without storing request data."""

from contextlib import contextmanager
from contextvars import ContextVar
import logging
import re
from uuid import uuid4

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id: ContextVar[str] = ContextVar("request_id", default="-")
REPORTED_HTTP_STATUS_CODES = frozenset({500})
REPORTED_ERROR_ATTRIBUTE = "_verwalino_request_error_logged"
_VALID_ID = re.compile(r"[A-Za-z0-9._-]{8,128}")
logger = logging.getLogger(__name__)


def is_valid_request_id(value: object) -> bool:
    return isinstance(value, str) and _VALID_ID.fullmatch(value) is not None


@contextmanager
def correlation_context(value: str):
    """Use a durable job ID for all records, including child async tasks."""
    token = request_id.set(str(value))
    try:
        yield
    finally:
        request_id.reset(token)


async def unhandled_error(request: Request, error: Exception) -> JSONResponse:
    import sentry_sdk
    from app.core.error_tracking import request_state

    # A stream can fail after user middleware has unwound. Restore its state
    # while the outer ServerErrorMiddleware invokes this handler.
    with correlation_context(request.state.request_id):
        state_token = request_state.set(request.scope["state"])
        try:
            sentry_sdk.capture_exception(error)
            logger.error("Unhandled request error", exc_info=error)
            setattr(error, REPORTED_ERROR_ATTRIBUTE, True)
        finally:
            request_state.reset(state_token)
    return JSONResponse(status_code=500, content={
        "detail": "Internal Server Error", "request_id": request.state.request_id,
    })


async def http_error(request: Request, error: HTTPException) -> JSONResponse:
    content = {"detail": error.detail}
    if error.status_code >= 500:
        if error.status_code in REPORTED_HTTP_STATUS_CODES:
            import sentry_sdk
            sentry_sdk.capture_exception(error)
            logger.error("HTTP request failed status=%s", error.status_code)
        else:
            logger.warning("HTTP request failed status=%s", error.status_code)
        content["request_id"] = request.state.request_id
    return JSONResponse(status_code=error.status_code, content=jsonable_encoder(content), headers=error.headers)


class RequestIdMiddleware:
    """Run outside the perimeter and catch failures before context is reset.

    Starlette's outer ServerErrorMiddleware otherwise generates its 500 after
    user middleware has unwound, losing both the context and response header.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        values = [value.decode("latin-1") for key, value in scope.get("headers", []) if key.lower() == b"x-request-id"]
        supplied = values[0] if len(values) == 1 else ""
        value = supplied if is_valid_request_id(supplied) else str(uuid4())
        scope.setdefault("state", {})["request_id"] = value
        started = False

        async def send_with_id(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message = dict(message)
                message["headers"] = [(key, val) for key, val in message.get("headers", []) if key.lower() != b"x-request-id"] + [(b"x-request-id", value.encode("ascii"))]
            await send(message)

        with correlation_context(value):
            from app.core.error_tracking import request_state
            state_token = request_state.set(scope["state"])
            try:
                await self.app(scope, receive, send_with_id)
            except Exception as error:
                if started:
                    # The outer error handler reports this once; propagating
                    # also lets the server abort the incomplete response.
                    raise
                response = await unhandled_error(Request(scope), error)
                await response(scope, receive, send_with_id)
            finally:
                request_state.reset(state_token)
