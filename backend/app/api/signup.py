"""Hosted registration; no tenant exists until the mailbox is confirmed."""
from __future__ import annotations

import itertools
import logging
import smtplib
from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.auth import LoginResponse, _DUMMY_PASSWORD_HASH, _set_session_cookie
from app.api.setup import SetupRequest
from app.core import rate_limit
from app.core.auth_db import lookup_login_principal
from app.core.config import settings
from app.core.db import get_session_factory
from app.core.deployment_policy import get_deployment_policy
from app.core.honeypot import honeypot_response
from app.core.rate_limit import ThrottleRequest, check_and_record, throttled
from app.core.security import create_session_token, hash_password, verify_password
from app.services import mail_service

logger = logging.getLogger(__name__)
MESSAGE = {"message": "Wenn die Adresse frei ist, haben wir eine E-Mail geschickt"}
REFUSED = "Bestätigung nicht möglich. Bitte fordern Sie einen neuen Link an."
PASSWORD_REFUSED = "Passwort nicht korrekt. Bitte versuchen Sie es erneut."
# Best-effort cadence per worker; retained state and cleanup locks live in SQL.
SIGNUP_CLEANUP_EVERY = 100
_signup_attempts = itertools.count(1)


def require_signup() -> None:
    if not get_deployment_policy().self_service_signup_enabled:
        raise HTTPException(404, "Not Found")


def validate_signup_configuration() -> None:
    """Fail startup before offering signup with unusable confirmation links."""
    if not get_deployment_policy().self_service_signup_enabled:
        return
    confirmation_origin()
    mail_service.validate_configuration(settings)


def confirmation_origin() -> str:
    """Use the operator's routing hostname, never request headers or a second URL."""
    return mail_service.public_origin(settings)


class ConfirmationLinkPrivacyMiddleware:
    """The browser reads the query; the server must never retain it in access logs."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] in {"/signup/confirm", "/passwort-zuruecksetzen"}:
            # Mutate the original scope used by Uvicorn's access logger too.
            # No server handler consumes this query; the SPA reads window.location.
            scope["query_string"] = b""
        await self.app(scope, receive, send)


class SignupRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            # Mode closure precedes parsing, dependency execution and DB access.
            require_signup()
            try:
                response = await handler(request)
            except RequestValidationError:
                response = JSONResponse(status_code=422, content={
                    "detail": "Eingaben ungültig. Bitte prüfen Sie die Felder und verwenden Sie ein Passwort mit 12 bis 128 Zeichen.",
                })
            response.headers["Cache-Control"] = "no-store"
            return response
        return handle


router = APIRouter(prefix="/api/signup", tags=["signup"], route_class=SignupRoute)


class EmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    email: str
    website: str = Field(default="", max_length=2048)

    @field_validator("email", mode="before")
    @classmethod
    def valid_email(cls, value: object) -> str:
        return mail_service.validate_address(SetupRequest.email_is_valid(value))


class SignupRequest(EmailRequest):
    organization_name: str = Field(min_length=1, max_length=255)
    admin_name: str = Field(min_length=1, max_length=255)
    password: SecretStr

    @field_validator("organization_name", "admin_name", mode="before")
    @classmethod
    def clean_names(cls, value: object) -> object:
        return SetupRequest.clean_text(value)

    @field_validator("password")
    @classmethod
    def valid_password(cls, value: SecretStr) -> SecretStr:
        return SetupRequest.password_is_valid(value)


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    token: SecretStr = Field(max_length=256)
    password: SecretStr = Field(default=SecretStr(""), max_length=128)


async def send_confirmation(email: str, token: str) -> None:
    try:
        await run_in_threadpool(mail_service.send_signup_confirmation, settings, recipient=email,
                                link=f"{confirmation_origin()}/signup/confirm?token={token}")
    except (mail_service.MailConfigurationError, smtplib.SMTPException, OSError):
        # Keep existing/unknown/failed-submission responses identical. Never log
        # SMTP diagnostics: they can include recipient addresses or message data.
        logger.warning("Signup confirmation submission failed")


async def cleanup_expired_requests() -> None:
    """Bounded privacy cleanup after the request transaction has committed."""
    try:
        async with get_session_factory()() as session, session.begin():
            await session.execute(text("SET LOCAL lock_timeout='100ms'"))
            await session.execute(text("SET LOCAL statement_timeout='1s'"))
            await session.execute(text('SELECT public.cleanup_signup_requests()'))
    except DBAPIError:
        logger.warning("Signup request cleanup failed")


async def accept_request(body: EmailRequest, throttle: ThrottleRequest, background: BackgroundTasks, *, new: SignupRequest | None = None) -> dict:
    token = None
    try:
        async with get_session_factory()() as session:
            async with session.begin():
                # Submission/resend admission counts only the scoped IP flood bucket.
                attempt = await check_and_record(session, throttle.scope, {"ip": throttle.ip},
                    get_deployment_policy().rate_limit_rules, request_id=throttle.request_id)
            # Hash for both existing and free addresses outside transactions.
            password_hash = hash_password(new.password.get_secret_value()) if attempt.allowed and new is not None else None
            async with session.begin():
                # Count accepted submissions too: success must not permit mail floods.
                await attempt.record_failure()
                if attempt.allowed:
                    if new is not None:
                        token = await session.scalar(text("SELECT public.request_signup(:email,:org,:name,:password)"), {
                            "email": body.email, "org": new.organization_name,
                            "name": new.admin_name, "password": password_hash,
                        })
                    else:
                        token = await session.scalar(text("SELECT public.resend_signup(:email)"), {"email": body.email})
    except DBAPIError as error:
        error.hide_parameters = True
        raise HTTPException(503, "Registrierung derzeit nicht verfügbar.") from None
    if rate_limit.cleanup_due():
        background.add_task(rate_limit.cleanup_stale, attempt.rules)
    if token is not None:
        # Starlette sends the response body before running this task. SMTP
        # duration and recipient failures cannot reveal address availability.
        background.add_task(send_confirmation, body.email, token)
    if new is not None and next(_signup_attempts) % SIGNUP_CLEANUP_EVERY == 0:
        background.add_task(cleanup_expired_requests)
    return MESSAGE


@router.post("", status_code=202)
async def signup(body: SignupRequest, background: BackgroundTasks, throttle: ThrottleRequest = Depends(throttled("signup"))):
    trap = honeypot_response({"website": body.website}, MESSAGE)
    if trap is not None:
        trap.status_code = 202
        return trap
    return await accept_request(body, throttle, background, new=body)


@router.post("/resend", status_code=202)
async def resend(body: EmailRequest, background: BackgroundTasks, throttle: ThrottleRequest = Depends(throttled("signup_resend"))):
    trap = honeypot_response({"website": body.website}, MESSAGE)
    if trap is not None:
        trap.status_code = 202
        return trap
    return await accept_request(body, throttle, background)


@router.post("/confirm", status_code=201, response_model=LoginResponse)
async def confirm(body: ConfirmRequest, response: Response, throttle: ThrottleRequest = Depends(throttled("signup_confirm"))) -> LoginResponse:
    token = body.token.get_secret_value()
    principal = None
    password_refused = False
    try:
        async with get_session_factory()() as session:
            async with session.begin():
                checked = (await session.execute(text("SELECT * FROM public.signup_confirmation(:token)"),
                                                 {"token": token})).mappings().one_or_none()
                email = checked['email_normalized'] if checked else None
                attempt = await throttle.check(session, email or "invalid-token@signup.invalid")
            checked_hash = checked['password_hash'] if checked and attempt.allowed else None
            password_matches = verify_password(body.password.get_secret_value(), checked_hash or _DUMMY_PASSWORD_HASH)
            async with session.begin():
                password_refused = attempt.allowed and checked_hash is not None and not password_matches
                if attempt.allowed and checked_hash and password_matches:
                    # The definer revalidates this exact snapshot under its row lock.
                    # Savepoint retains counters when provisioning is refused.
                    try:
                        async with session.begin_nested():
                            org = await session.scalar(text("SELECT public.provision_organization(:token,:password_hash)"),
                                                       {"token": token, "password_hash": checked_hash})
                            principal = await lookup_login_principal(session, email)
                            if principal is None or principal.organization_id != org:
                                raise HTTPException(403, REFUSED)
                    except DBAPIError as error:
                        error.hide_parameters = True
                        if getattr(error.orig, "sqlstate", None) != "P0001":
                            raise
                if principal is None:
                    await attempt.record_failure()
                else:
                    await attempt.reset()
    except DBAPIError as error:
        error.hide_parameters = True
        raise HTTPException(503, "Registrierung derzeit nicht verfügbar.") from None
    if rate_limit.cleanup_due():
        await rate_limit.cleanup_stale(attempt.rules)
    if principal is None:
        if password_refused:
            raise HTTPException(422, PASSWORD_REFUSED)
        raise HTTPException(403, REFUSED)
    _set_session_cookie(response, create_session_token(principal.user_id, principal.organization_id, principal.session_version))
    return LoginResponse(user_id=str(principal.user_id), organization_id=str(principal.organization_id), role=principal.role)


@router.api_route("", methods=["GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"], include_in_schema=False)
@router.api_route("/confirm", methods=["GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"], include_in_schema=False)
@router.api_route("/resend", methods=["GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"], include_in_schema=False)
async def unsupported_method() -> None:
    raise HTTPException(405, "Method Not Allowed", headers={"Allow": "POST"})
