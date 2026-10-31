"""Mailbox recovery before tenant selection, in both deployment modes."""
from __future__ import annotations

import hashlib
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

from app.core import rate_limit
from app.core.config import settings
from app.core.db import get_session_factory
from app.core.deployment_policy import RateLimitRules, get_deployment_policy
from app.core.honeypot import honeypot_response
from app.core.rate_limit import ThrottleRequest, check_and_record, throttled
from app.core.security import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, hash_password, normalize_email, validate_new_password
from app.services import mail_service

logger = logging.getLogger(__name__)
MESSAGE = {'message': 'Wenn ein Konto zu dieser Adresse besteht, erhalten Sie eine E-Mail zum Zurücksetzen Ihres Passworts.'}
REFUSED = 'Zurücksetzen nicht möglich. Bitte fordern Sie einen neuen Link an.'
PASSWORD_POLICY = f'Das Passwort muss zwischen {MIN_PASSWORD_LENGTH} und {MAX_PASSWORD_LENGTH} Zeichen lang sein.'
ADDRESS_FORMAT = 'Bitte geben Sie eine gültige E-Mail-Adresse ein.'
# Three issued mails per address; one hour after the third before a new window.
RECIPIENT_RULES = RateLimitRules(3, 3, 60, 60, 1, 60, 60, 100, 100)
# Like signup, retention cleanup is best effort after every 100th request.
RESET_CLEANUP_EVERY = 100
_reset_attempts = itertools.count(1)
# Configuration is deployment-wide; one warning per process avoids a log flood.
_warned_unavailable = False


class ResetRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()
        async def handle(request: Request) -> Response:
            try:
                response = await handler(request)
            except RequestValidationError as error:
                if request.url.path.endswith('/confirm'):
                    # Shape/policy is public information, independent of token state.
                    detail = PASSWORD_POLICY if any(
                        item['loc'][:2] == ('body', 'password') for item in error.errors()
                    ) else 'Eingaben ungültig. Bitte prüfen Sie den Link und die Felder.'
                else:
                    # Malformed request bodies still pay the IP request budget.
                    throttle = await throttled('password_reset')(request)
                    await run_in_threadpool(hash_password, 'password-reset-validation-dummy')
                    async with get_session_factory()() as session, session.begin():
                        attempt = await check_and_record(session, throttle.scope, {'ip': throttle.ip},
                            get_deployment_policy().rate_limit_rules, request_id=throttle.request_id)
                        await attempt.record_failure()
                    detail = ADDRESS_FORMAT
                response = JSONResponse(status_code=422, content={'detail': detail})
            except HTTPException as error:
                response = JSONResponse(status_code=error.status_code, content={'detail': error.detail})
            response.headers['Cache-Control'] = 'no-store'
            return response
        return handle


router = APIRouter(prefix='/api/auth/password-reset', tags=['auth'], route_class=ResetRoute)


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    email: str
    website: str = Field(default='', max_length=2048)

    @field_validator('email', mode='before')
    @classmethod
    def normalized_email(cls, value: object) -> str:
        return mail_service.validate_address(normalize_email(value))


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    token: SecretStr = Field(max_length=256)
    password: SecretStr

    @field_validator('password')
    @classmethod
    def valid_password(cls, value: SecretStr) -> SecretStr:
        validate_new_password(value.get_secret_value())
        return value


async def submit_mail(message) -> None:
    try:
        await run_in_threadpool(mail_service.submit_password_mail, settings, message)
    except (mail_service.MailConfigurationError, smtplib.SMTPException, OSError):
        logger.warning('Password reset mail submission failed')


async def cleanup_expired_requests() -> None:
    """Bounded definer cleanup in a separate post-response transaction."""
    try:
        async with get_session_factory()() as session, session.begin():
            await session.execute(text("SET LOCAL lock_timeout='100ms'"))
            await session.execute(text("SET LOCAL statement_timeout='1s'"))
            await session.execute(text('SELECT public.cleanup_password_reset_requests()'))
    except DBAPIError:
        logger.warning('Password reset request cleanup failed')


@router.post('', status_code=202)
async def request_reset(body: ResetRequest, background: BackgroundTasks,
                        throttle: ThrottleRequest = Depends(throttled('password_reset'))):
    global _warned_unavailable
    trapped = honeypot_response(body.model_dump(), MESSAGE)
    if trapped is not None:
        trapped.status_code = 202
        return trapped
    available = mail_service.password_reset_available(settings)
    if not available and not _warned_unavailable:
        _warned_unavailable = True
        logger.warning('Password reset unavailable: configure SMTP and a trusted public URL')
    token = None
    async with get_session_factory()() as session:
        async with session.begin():
            attempt = await check_and_record(session, throttle.scope, {'ip': throttle.ip},
                get_deployment_policy().rate_limit_rules, request_id=throttle.request_id)
        # Same expensive work for existing, absent and throttled addresses.
        await run_in_threadpool(hash_password, 'password-reset-request-dummy')
        async with session.begin():
            await attempt.record_failure()
            if available and attempt.allowed:
                # Serialize admission plus issuance across processes/IPs; the
                # limiter persists only the scoped normalized-address hash.
                recipient_hash = rate_limit.key_hash('password_reset_recipient', 'email', body.email)
                await session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
                    {'key': 'reset-recipient:' + recipient_hash.hex()})
                async with session.begin_nested() as reservation:
                    recipient_attempt = await check_and_record(session, 'password_reset_recipient',
                        {'email': body.email}, RECIPIENT_RULES, request_id=throttle.request_id)
                    if recipient_attempt.allowed:
                        token = await session.scalar(text('SELECT public.issue_password_reset(:email)'), {'email': body.email})
                    if token is not None:
                        await recipient_attempt.record_failure()
                        # Keep a partial count until a full quiet hour. This
                        # conservative rolling cap cannot burst at a window edge.
                        await session.execute(text("""UPDATE public.auth_throttle
                            SET window_start=updated_at WHERE key_kind='email' AND key_hash=:key"""),
                            {'key': recipient_hash})
                    else:
                        # Admission reserves a count. Keep only issued mail;
                        # rollback preserves earlier counts and active lockouts.
                        await reservation.rollback()
    # Construct mail even for absent/locked recipients, without minting a token
    # in the app. SMTP runs only after the committed issuance and HTTP response.
    message = mail_service.build_password_reset(
        settings, recipient=body.email,
        link=f"{mail_service.public_origin(settings) if available else ''}/passwort-zuruecksetzen?token={token or '0' * 64}")
    if token is not None:
        background.add_task(submit_mail, message)
    if rate_limit.cleanup_due():
        await rate_limit.cleanup_stale(attempt.rules)
    if next(_reset_attempts) % RESET_CLEANUP_EVERY == 0:
        background.add_task(cleanup_expired_requests)
    return MESSAGE


@router.post('/confirm', status_code=204)
async def confirm_reset(body: ConfirmRequest, background: BackgroundTasks,
                        throttle: ThrottleRequest = Depends(throttled('password_reset_confirm'))):
    token = body.token.get_secret_value()
    # Every policy-valid confirmation pays for a hash, even absent/used/locked
    # tokens. Hashing never holds a DB transaction or a row/ACL lock.
    password_hash = await run_in_threadpool(hash_password, body.password.get_secret_value())
    recipient = None
    async with get_session_factory()() as session:
        async with session.begin():
            # A token digest is opaque and needs no privileged pre-lookup.
            token_key = hashlib.sha256(token.encode()).hexdigest()
            rules = get_deployment_policy().rate_limit_rules
            attempt = await check_and_record(session, throttle.scope,
                {'email': token_key + '@password-reset.invalid'}, rules, request_id=throttle.request_id)
            ip_attempt = await check_and_record(session, throttle.scope, {'ip': throttle.ip},
                rules, request_id=throttle.request_id)
        async with session.begin():
            if ip_attempt.allowed and attempt.allowed:
                try:
                    async with session.begin_nested():
                        recipient = await session.scalar(text('SELECT public.reset_password_with_token(:token,:password_hash)'),
                            {'token': token, 'password_hash': password_hash})
                except DBAPIError as error:
                    error.hide_parameters = True
                    if getattr(error.orig, 'sqlstate', None) != 'P0001':
                        raise
            # IP counts valid-body requests, including success; only refusals
            # count against the opaque token key.
            if recipient is None:
                await attempt.record_failure()
            else:
                await attempt.reset()
            await ip_attempt.record_failure()
    if rate_limit.cleanup_due():
        await rate_limit.cleanup_stale(attempt.rules)
    if recipient is None:
        raise HTTPException(403, REFUSED)
    background.add_task(submit_mail, mail_service.build_password_changed(settings, recipient=recipient))
    return Response(status_code=204)
