"""Hosted registration; no tenant exists until the mailbox is confirmed."""
from __future__ import annotations

import hashlib
import itertools
import logging
import secrets
import smtplib
from collections.abc import Callable
from datetime import timedelta
from urllib.parse import urlsplit

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
# Every newly issued token expires after 24 hours; sends share a ten-minute gap.
TOKEN_LIFETIME = timedelta(hours=24)
RESEND_INTERVAL = timedelta(minutes=10)
# Best-effort cadence per worker; retained state and cleanup locks live in SQL.
SIGNUP_CLEANUP_EVERY = 100
SIGNUP_CLEANUP_BATCH = 100
EXPIRED_RETENTION = timedelta(days=7)
_signup_attempts = itertools.count(1)


def require_signup() -> None:
    if not get_deployment_policy().self_service_signup_enabled:
        raise HTTPException(404, "Not Found")


def validate_signup_configuration() -> None:
    """Fail startup before offering signup with unusable confirmation links."""
    if not get_deployment_policy().self_service_signup_enabled:
        return
    url = urlsplit(settings.public_url or "")
    if (url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password
        or url.query or url.fragment or url.path not in {"", "/"}
        or (url.scheme != "https" and url.hostname not in {"localhost", "127.0.0.1", "::1"})):
        raise ValueError("Hosted signup requires HV_PUBLIC_URL (HTTPS origin; HTTP only on loopback)")
    mail_service.validate_configuration(settings)


class ConfirmationLinkPrivacyMiddleware:
    """The browser reads the query; the server must never retain it in access logs."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == "/signup/confirm":
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
                                link=f"{settings.public_url.rstrip('/')}/signup/confirm?token={token}")
    except (mail_service.MailConfigurationError, smtplib.SMTPException, OSError):
        # Keep existing/unknown/failed-submission responses identical. Never log
        # SMTP diagnostics: they can include recipient addresses or message data.
        logger.warning("Signup confirmation submission failed")


async def cleanup_expired_requests() -> None:
    """Bounded privacy cleanup after the request transaction has committed."""
    try:
        async with get_session_factory()() as session, session.begin():
            await session.execute(text("""DELETE FROM public.signup_request WHERE id IN (
                SELECT id FROM public.signup_request
                WHERE expires_at < clock_timestamp()-CAST(:retention AS interval)
                ORDER BY expires_at,id LIMIT :batch FOR UPDATE SKIP LOCKED)"""),
                {"retention": EXPIRED_RETENTION, "batch": SIGNUP_CLEANUP_BATCH})
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
                checked_hash = await session.scalar(text("SELECT password_hash FROM public.signup_request WHERE email_normalized=:email"),
                                                   {"email": body.email}) if attempt.allowed and new is not None else None
            # Existing and free emails perform equivalent work outside transactions.
            password_hash = hash_password(new.password.get_secret_value()) if attempt.allowed and new is not None else None
            same_password = verify_password(new.password.get_secret_value(), checked_hash or _DUMMY_PASSWORD_HASH) if password_hash else False
            async with session.begin():
                # Count accepted submissions too: success must not permit mail floods.
                await attempt.record_failure()
                if attempt.allowed and not await session.scalar(
                    text("SELECT public.signup_email_registered(:email)"), {"email": body.email}
                ):
                    # The upsert and row lock serialize pending double submits.
                    candidate = secrets.token_hex(32)
                    digest = hashlib.sha256(candidate.encode()).digest()
                    if new is not None:
                        token = await session.scalar(text("""INSERT INTO public.signup_request
                            (email_normalized,organization_name,admin_name,password_hash,token_hash,created_at,expires_at)
                            VALUES (:email,:org,:name,:password,:digest,statement_timestamp(),statement_timestamp()+:lifetime)
                            ON CONFLICT (email_normalized) DO NOTHING RETURNING id"""), {
                                "email": body.email, "org": new.organization_name, "name": new.admin_name,
                                "password": password_hash, "digest": digest,
                                "lifetime": TOKEN_LIFETIME,
                            })
                    if token is None:
                        pending = (await session.execute(text("SELECT * FROM public.signup_request WHERE email_normalized=:email FOR UPDATE"),
                                                         {"email": body.email})).mappings().one_or_none()
                        now = await session.scalar(text("SELECT clock_timestamp()"))
                        if pending and pending['confirmed_at'] is None:
                            changed = new is not None and (
                                new.organization_name != pending['organization_name'] or new.admin_name != pending['admin_name']
                                or not same_password or pending['password_hash'] != checked_hash
                            )
                            can_send = (pending['resent_at'] or pending['created_at']) + RESEND_INTERVAL <= now
                            if changed:
                                # The delivered link stays usable during cooldown;
                                # confirmation must prove this updated password.
                                await session.execute(text("""UPDATE public.signup_request SET organization_name=:org,
                                    admin_name=:name,password_hash=:password
                                    WHERE id=:id"""), {"org": new.organization_name, "name": new.admin_name,
                                        "password": password_hash,
                                        "id": pending['id']})
                            if can_send:
                                await session.execute(text("""UPDATE public.signup_request SET token_hash=:digest,
                                    resent_at=:now,expires_at=:expires WHERE id=:id"""),
                                    {"digest": digest, "now": now, "expires": now + TOKEN_LIFETIME, "id": pending['id']})
                                token = pending['id']
                    token = candidate if token is not None else None
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
    digest = hashlib.sha256(token.encode()).digest()
    principal = None
    try:
        async with get_session_factory()() as session:
            async with session.begin():
                checked = (await session.execute(text("SELECT email_normalized,password_hash FROM public.signup_request WHERE token_hash=:digest"),
                                                 {"digest": digest})).mappings().one_or_none()
                email = checked['email_normalized'] if checked else None
                attempt = await throttle.check(session, email or "invalid-token@signup.invalid")
            checked_hash = checked['password_hash'] if checked and attempt.allowed else None
            password_matches = verify_password(body.password.get_secret_value(), checked_hash or _DUMMY_PASSWORD_HASH)
            async with session.begin():
                if attempt.allowed and checked_hash and password_matches:
                    # Same order as provisioning/bootstrap. Revalidate the exact
                    # password proof under the pending row lock before provisioning.
                    await session.execute(text("LOCK TABLE public.organization IN SHARE ROW EXCLUSIVE MODE"))
                    pending = (await session.execute(text("SELECT email_normalized,password_hash FROM public.signup_request WHERE token_hash=:digest FOR UPDATE"),
                                                     {"digest": digest})).mappings().one_or_none()
                    if pending and pending['password_hash'] == checked_hash and pending['email_normalized'] == email:
                        # Savepoint retains counters when provisioning is refused.
                        try:
                            async with session.begin_nested():
                                org = await session.scalar(text("SELECT public.provision_organization(:token)"), {"token": token})
                                principal = await lookup_login_principal(session, email)
                                if principal is None or principal.organization_id != org:
                                    raise HTTPException(403, REFUSED)
                        except DBAPIError as error:
                            error.hide_parameters = True
                            if getattr(error.orig, "sqlstate", None) != "P0001":
                                raise
                if principal is None:
                    await attempt.record_failure()
    except DBAPIError as error:
        error.hide_parameters = True
        raise HTTPException(503, "Registrierung derzeit nicht verfügbar.") from None
    if rate_limit.cleanup_due():
        await rate_limit.cleanup_stale(attempt.rules)
    if principal is None:
        raise HTTPException(403, REFUSED)
    _set_session_cookie(response, create_session_token(principal.user_id, principal.organization_id, principal.session_version))
    return LoginResponse(user_id=str(principal.user_id), organization_id=str(principal.organization_id), role=principal.role)


@router.api_route("", methods=["GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"], include_in_schema=False)
@router.api_route("/confirm", methods=["GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"], include_in_schema=False)
@router.api_route("/resend", methods=["GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"], include_in_schema=False)
async def unsupported_method() -> None:
    raise HTTPException(405, "Method Not Allowed", headers={"Allow": "POST"})
