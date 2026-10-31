"""Mailbox recovery before tenant selection, in both deployment modes."""
from __future__ import annotations

import hashlib
import logging
import secrets
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

from app.api.auth import LoginRequest
from app.core import rate_limit
from app.core.auth_db import lookup_login_principal
from app.core.config import settings
from app.core.db import get_session_factory, set_tenant_context
from app.core.deployment_policy import get_deployment_policy
from app.core.honeypot import honeypot_response
from app.core.rate_limit import ThrottleRequest, check_and_record, throttled
from app.core.security import hash_password, normalize_email, validate_new_password
from app.services import mail_service

logger = logging.getLogger(__name__)
MESSAGE = {'message': 'Wenn ein Konto zu dieser Adresse besteht, erhalten Sie eine E-Mail zum Zurücksetzen Ihres Passworts.'}
REFUSED = 'Zurücksetzen nicht möglich. Bitte fordern Sie einen neuen Link an.'
# Configuration is deployment-wide; one warning per process avoids a log flood.
_warned_unavailable = False


class ResetRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()
        async def handle(request: Request) -> Response:
            try:
                response = await handler(request)
            except RequestValidationError:
                # Malformed bodies cannot bypass the IP request budget.
                scope = 'password_reset_confirm' if request.url.path.endswith('/confirm') else 'password_reset'
                throttle = await throttled(scope)(request)
                await run_in_threadpool(hash_password, 'password-reset-validation-dummy')
                async with get_session_factory()() as session, session.begin():
                    attempt = await check_and_record(session, scope, {'ip': throttle.ip},
                        get_deployment_policy().rate_limit_rules, request_id=throttle.request_id)
                    await attempt.record_failure()
                response = JSONResponse(status_code=403, content={'detail': REFUSED})
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


async def submit_mail(message) -> None:
    try:
        await run_in_threadpool(mail_service.submit_password_mail, settings, message)
    except (mail_service.MailConfigurationError, smtplib.SMTPException, OSError):
        logger.warning('Password reset mail submission failed')


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
    async with get_session_factory()() as session:
        async with session.begin():
            attempt = await check_and_record(session, throttle.scope, {'ip': throttle.ip},
                get_deployment_policy().rate_limit_rules, request_id=throttle.request_id)
            principal = await lookup_login_principal(session, body.email)
        # Same expensive work for existing, absent and throttled addresses. SMTP
        # is deferred until after the response; it cannot reveal mailbox presence.
        await run_in_threadpool(hash_password, 'password-reset-request-dummy')
        token = secrets.token_hex(32)
        digest = hashlib.sha256(token.encode()).digest()
        message = mail_service.build_password_reset(
            settings, recipient=body.email,
            link=f"{(settings.public_url or '').rstrip('/')}/passwort-zuruecksetzen?token={token}")
        async with session.begin():
            await attempt.record_failure()
            if available and attempt.allowed and principal and principal.is_active:
                await session.execute(text('''INSERT INTO public.password_reset_request(user_id,organization_id,token_hash)
                    VALUES (:user,:org,:digest)'''), {'user': principal.user_id, 'org': principal.organization_id, 'digest': digest})
                background.add_task(submit_mail, message)
    if rate_limit.cleanup_due():
        await rate_limit.cleanup_stale(attempt.rules)
    return MESSAGE


@router.post('/confirm', status_code=204)
async def confirm_reset(body: ConfirmRequest, background: BackgroundTasks,
                        throttle: ThrottleRequest = Depends(throttled('password_reset_confirm'))):
    token = body.token.get_secret_value()
    password = body.password.get_secret_value()
    try:
        LoginRequest.login_password_is_bounded(password)
        validate_new_password(password)
        valid_password = True
    except ValueError:
        valid_password = False
    # Every parsed confirmation pays for a new hash, even absent/used/locked
    # tokens. Hashing never holds a DB transaction or a row/ACL lock.
    password_hash = await run_in_threadpool(hash_password, password if valid_password else 'password-reset-confirm-dummy')
    changed = None
    async with get_session_factory()() as session:
        async with session.begin():
            pending = (await session.execute(text('SELECT user_id,organization_id FROM public.password_reset_request WHERE token_hash=:digest'),
                {'digest': hashlib.sha256(token.encode()).digest()})).mappings().one_or_none()
            # Opaque wrong tokens have no user. All such attempts share a dummy
            # bucket; retained expired/used tokens continue to count on their user.
            user_key = str(pending['user_id']) if pending else 'invalid-token'
            attempt = await throttle.check(session, user_key + '@password-reset.invalid')
        async with session.begin():
            if attempt.allowed and valid_password:
                try:
                    async with session.begin_nested():
                        changed = await session.scalar(text('SELECT public.reset_password_with_token(:token,:password_hash)'),
                            {'token': token, 'password_hash': password_hash})
                        await set_tenant_context(session, pending['organization_id'])
                        email = await session.scalar(text('SELECT email FROM public."user" WHERE id=:user'), {'user': changed})
                except DBAPIError as error:
                    error.hide_parameters = True
                    if getattr(error.orig, 'sqlstate', None) != 'P0001':
                        raise
            # IP counts requests, including success; only refusals count on users.
            if changed is None:
                await attempt.record_failure()
            else:
                ip_attempt = await check_and_record(session, throttle.scope, {'ip': throttle.ip},
                    attempt.rules, request_id=throttle.request_id)
                await ip_attempt.record_failure()
    if rate_limit.cleanup_due():
        await rate_limit.cleanup_stale(attempt.rules)
    if changed is None:
        raise HTTPException(403, REFUSED)
    background.add_task(submit_mail, mail_service.build_password_changed(settings, recipient=email))
    return Response(status_code=204)
