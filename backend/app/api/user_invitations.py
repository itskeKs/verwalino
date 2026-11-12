"""User invitations -- admin issuance and public single-use acceptance."""

from __future__ import annotations

import hashlib
import logging
import secrets
import smtplib
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.password_reset import PASSWORD_POLICY, RECIPIENT_RULES
from app.api.users import user_admin_db
from app.core import rate_limit
from app.core.config import settings
from app.core.db import get_session_factory
from app.core.deployment_policy import get_deployment_policy
from app.core.rate_limit import ThrottleRequest, check_and_record, throttled
from app.core.rbac import Role
from app.core.security import hash_password, normalize_email, validate_new_password
from app.core.tenant import AuthContext, require_admin_dep
from app.mail import render_mail
from app.mail.contexts import UserInvitation
from app.models.audit_event import AuditEvent
from app.repositories.audit_repo import AuditRepository as CanonicalAuditRepository
from app.services import mail_service

logger = logging.getLogger(__name__)

# One documented validity for the single-use link (owner decision on #135).
INVITATION_VALID_HOURS = 72
PENDING = "accepted_at IS NULL AND revoked_at IS NULL"
OPEN = PENDING + " AND expires_at > now()"


class AcceptRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                response = await handler(request)
            except RequestValidationError as error:
                detail = (
                    PASSWORD_POLICY
                    if any(
                        item["loc"][:2] == ("body", "password")
                        for item in error.errors()
                    )
                    else "Eingaben ungültig. Bitte prüfen Sie den Link und die Felder."
                )
                response = JSONResponse(status_code=422, content={"detail": detail})
            except HTTPException as error:
                response = JSONResponse(
                    status_code=error.status_code, content={"detail": error.detail}
                )
            response.headers["Cache-Control"] = "no-store"
            return response

        return handle


router = APIRouter(prefix="/api/users", tags=["users"])
accept_router = APIRouter(
    prefix="/api/auth/invitation", tags=["auth"], route_class=AcceptRoute
)


class InvitationOut(BaseModel):
    id: str
    email: str
    role: str
    created_at: datetime
    expires_at: datetime
    last_sent_at: datetime


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    email: str
    role: str

    @field_validator("email", mode="before")
    @classmethod
    def email_has_canonical_form(cls, value: object) -> str:
        return mail_service.validate_address(normalize_email(value))


class AcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    token: SecretStr = Field(max_length=256)
    password: SecretStr

    @field_validator("password")
    @classmethod
    def valid_password(cls, value: SecretStr) -> SecretStr:
        validate_new_password(value.get_secret_value())
        return value


async def audit_invitation(
    db: AsyncSession, auth: AuthContext, invitation_id: str, action: str, payload: dict
) -> None:
    await CanonicalAuditRepository(db, auth.organization_id).create(
        AuditEvent(
            actor_user_id=auth.user_id,
            entity_type="user_invitation",
            entity_id=invitation_id,
            action=action,
            payload_diff=payload,
        )
    )


async def submit_invitation_mail(message: EmailMessage) -> None:
    try:
        await run_in_threadpool(
            mail_service.submit_notification_mail, settings, message
        )
    except (mail_service.MailConfigurationError, smtplib.SMTPException, OSError):
        logger.warning("Invitation mail submission failed")


async def build_invitation_mail(
    db: AsyncSession, auth: AuthContext, *, token: str, recipient: str
) -> EmailMessage:
    meta = (
        await db.execute(
            text(
                'SELECT u.display_name AS inviter, o.name AS organization FROM "user" u '
                "CROSS JOIN organization o WHERE u.id=:actor AND u.organization_id=:org AND o.id=:org"
            ),
            {"actor": str(auth.user_id), "org": str(auth.organization_id)},
        )
    ).one()
    rendered = render_mail(
        UserInvitation(
            inviter_name=meta.inviter or "Ihre Verwaltung",
            organization_name=meta.organization,
            set_password_link=f"{mail_service.public_origin(settings)}/einladung?token={token}",
            valid_hours=INVITATION_VALID_HOURS,
        )
    )
    message = EmailMessage()
    try:
        sender = mail_service.validate_address(settings.smtp_from)
    except mail_service.MailConfigurationError:
        sender = ""
    message["From"] = sender
    message["To"] = recipient
    message["Date"] = formatdate(localtime=False)
    rendered.apply_to(message)
    return message


async def recipient_admission(
    db: AsyncSession, email: str, throttle: ThrottleRequest
) -> tuple[object, bytes]:
    """IP window, per-recipient serialization and recipient window, in order."""
    attempt = await check_and_record(
        db,
        throttle.scope,
        {"ip": throttle.ip},
        get_deployment_policy().rate_limit_rules,
        request_id=throttle.request_id,
    )
    if not attempt.allowed:
        raise HTTPException(
            429, "Zu viele Einladungen. Bitte versuchen Sie es später erneut."
        )
    recipient_hash = rate_limit.key_hash("user_invitation_recipient", "email", email)
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": "invite-recipient:" + recipient_hash.hex()},
    )
    recipient_attempt = await check_and_record(
        db,
        "user_invitation_recipient",
        {"email": email},
        RECIPIENT_RULES,
        request_id=throttle.request_id,
    )
    if not recipient_attempt.allowed:
        raise HTTPException(
            429,
            "Zu viele Einladungen für diese Adresse. "
            "Bitte versuchen Sie es später erneut.",
        )
    return recipient_attempt, recipient_hash


async def record_send(
    db: AsyncSession, recipient_attempt: object, recipient_hash: bytes
) -> None:
    await recipient_attempt.record_failure()
    # Keep a partial count until a full quiet hour; like the password reset.
    await db.execute(
        text(
            "UPDATE public.auth_throttle SET window_start=updated_at "
            "WHERE key_kind='email' AND key_hash=:key"
        ),
        {"key": recipient_hash},
    )


def invitation_out(row) -> InvitationOut:
    return InvitationOut(
        id=str(row.id),
        email=row.email,
        role=row.role,
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_sent_at=row.last_sent_at,
    )


@router.get("/invitations", response_model=list[InvitationOut])
async def list_invitations(
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(user_admin_db),
):
    rows = (
        await db.execute(
            text(
                f"SELECT id,email,role,created_at,expires_at,last_sent_at FROM user_invitation "
                f"WHERE {OPEN} ORDER BY created_at, id"
            )
        )
    ).all()
    return [invitation_out(row) for row in rows]


@router.post("/invitations", response_model=InvitationOut, status_code=201)
async def create_invitation(
    body: InvitationCreate,
    background: BackgroundTasks,
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(user_admin_db),
    throttle: ThrottleRequest = Depends(throttled("user_invitation")),
):
    if body.role not in {role.value for role in Role}:
        raise HTTPException(status_code=422, detail=f"Ungültige Rolle: {body.role}")
    if not mail_service.password_reset_available(settings):
        raise HTTPException(
            409, "Einladungen sind ohne konfigurierten E-Mail-Versand nicht verfügbar."
        )
    recipient_attempt, recipient_hash = await recipient_admission(
        db, body.email, throttle
    )
    taken = await db.scalar(
        text("SELECT public.signup_email_registered(:email)"), {"email": body.email}
    )
    if taken:
        raise HTTPException(409, "Diese E-Mail-Adresse wird bereits verwendet.")
    existing = (
        await db.execute(
            text(
                f"SELECT id, expires_at FROM user_invitation WHERE organization_id=:org "
                f"AND lower(btrim(email))=lower(btrim(:email)) AND {PENDING}"
            ),
            {"org": str(auth.organization_id), "email": body.email},
        )
    ).one_or_none()
    if existing is not None and existing.expires_at > datetime.now(timezone.utc):
        raise HTTPException(
            409, "Für diese Adresse besteht bereits eine offene Einladung."
        )
    token = secrets.token_hex(32)
    if existing is not None:
        # The pending invitation expired: re-inviting rotates it like a resend.
        row = (
            await db.execute(
                text(
                    "UPDATE user_invitation SET token_hash=sha256(convert_to(:token,'UTF8')), "
                    "role=:role, invited_by=:actor, "
                    "expires_at=now() + (:hours || ' hours')::interval, last_sent_at=now() "
                    f"WHERE id=:id AND {PENDING} "
                    "RETURNING id,email,role,created_at,expires_at,last_sent_at"
                ),
                {
                    "id": str(existing.id),
                    "token": token,
                    "role": body.role,
                    "actor": str(auth.user_id),
                    "hours": str(INVITATION_VALID_HOURS),
                },
            )
        ).one()
        await record_send(db, recipient_attempt, recipient_hash)
        await audit_invitation(
            db,
            auth,
            str(row.id),
            "RESEND",
            {"after": {"email": row.email, "role": row.role}},
        )
        message = await build_invitation_mail(
            db, auth, token=token, recipient=row.email
        )
        background.add_task(submit_invitation_mail, message)
        return invitation_out(row)
    row = (
        await db.execute(
            text(
                "INSERT INTO user_invitation(organization_id,email,role,invited_by,token_hash,expires_at) "
                "VALUES (:org,:email,:role,:actor,sha256(convert_to(:token,'UTF8')),"
                "now() + (:hours || ' hours')::interval) RETURNING id,email,role,created_at,expires_at,last_sent_at"
            ),
            {
                "org": str(auth.organization_id),
                "email": body.email,
                "role": body.role,
                "actor": str(auth.user_id),
                "token": token,
                "hours": str(INVITATION_VALID_HOURS),
            },
        )
    ).one()
    await record_send(db, recipient_attempt, recipient_hash)
    await audit_invitation(
        db,
        auth,
        str(row.id),
        "INVITE",
        {"after": {"email": row.email, "role": row.role}},
    )
    message = await build_invitation_mail(db, auth, token=token, recipient=row.email)
    background.add_task(submit_invitation_mail, message)
    return invitation_out(row)


@router.delete("/invitations/{invitation_id}", status_code=204, response_class=Response)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(user_admin_db),
):
    revoked = await db.scalar(
        text(
            f"UPDATE user_invitation SET revoked_at=now() WHERE id=:id AND {PENDING} RETURNING id"
        ),
        {"id": invitation_id},
    )
    if revoked is None:
        raise HTTPException(status_code=404, detail="Einladung nicht gefunden.")
    await audit_invitation(
        db, auth, str(revoked), "REVOKE", {"before": {"id": str(invitation_id)}}
    )
    return Response(status_code=204)


@router.post("/invitations/{invitation_id}/resend", response_model=InvitationOut)
async def resend_invitation(
    invitation_id: uuid.UUID,
    background: BackgroundTasks,
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(user_admin_db),
    throttle: ThrottleRequest = Depends(throttled("user_invitation")),
):
    locked = await db.scalar(
        text(f"SELECT id FROM user_invitation WHERE id=:id AND {PENDING} FOR UPDATE"),
        {"id": invitation_id},
    )
    if locked is None:
        raise HTTPException(status_code=404, detail="Einladung nicht gefunden.")
    if not mail_service.password_reset_available(settings):
        raise HTTPException(
            409, "Einladungen sind ohne konfigurierten E-Mail-Versand nicht verfügbar."
        )
    email = await db.scalar(
        text("SELECT email FROM user_invitation WHERE id=:id"), {"id": invitation_id}
    )
    recipient_attempt, recipient_hash = await recipient_admission(db, email, throttle)
    token = secrets.token_hex(32)
    row = (
        await db.execute(
            text(
                "UPDATE user_invitation SET token_hash=sha256(convert_to(:token,'UTF8')), "
                "invited_by=:actor, "
                "expires_at=now() + (:hours || ' hours')::interval, last_sent_at=now() "
                f"WHERE id=:id AND {PENDING} "
                "RETURNING id,email,role,created_at,expires_at,last_sent_at"
            ),
            {
                "id": invitation_id,
                "actor": str(auth.user_id),
                "token": token,
                "hours": str(INVITATION_VALID_HOURS),
            },
        )
    ).one()
    await record_send(db, recipient_attempt, recipient_hash)
    await audit_invitation(
        db,
        auth,
        str(row.id),
        "RESEND",
        {"after": {"email": row.email, "role": row.role}},
    )
    message = await build_invitation_mail(db, auth, token=token, recipient=row.email)
    background.add_task(submit_invitation_mail, message)
    return invitation_out(row)


@accept_router.post("/accept", status_code=204)
async def accept_invitation(
    body: AcceptRequest,
    throttle: ThrottleRequest = Depends(throttled("user_invitation_accept")),
):
    token = body.token.get_secret_value()
    # Every policy-valid attempt pays for a hash, even absent/used tokens.
    password_hash = await run_in_threadpool(
        hash_password, body.password.get_secret_value()
    )
    accepted = None
    async with get_session_factory()() as session:
        async with session.begin():
            token_key = hashlib.sha256(token.encode()).hexdigest()
            rules = get_deployment_policy().rate_limit_rules
            attempt = await check_and_record(
                session,
                throttle.scope,
                {"email": token_key + "@user-invitation.invalid"},
                rules,
                request_id=throttle.request_id,
            )
            ip_attempt = await check_and_record(
                session,
                throttle.scope,
                {"ip": throttle.ip},
                rules,
                request_id=throttle.request_id,
            )
        async with session.begin():
            throttled = not (ip_attempt.allowed and attempt.allowed)
            if not throttled:
                try:
                    async with session.begin_nested():
                        accepted = (
                            (
                                await session.execute(
                                    text(
                                        "SELECT organization_id,email,role "
                                        "FROM public.accept_user_invitation(:token,:password_hash)"
                                    ),
                                    {"token": token, "password_hash": password_hash},
                                )
                            )
                            .mappings()
                            .one_or_none()
                        )
                except DBAPIError as error:
                    # P0001 is the uniform refusal; P0023 is the erasure guard.
                    error.hide_parameters = True
                    if getattr(error.orig, "sqlstate", None) not in {"P0001", "P0023"}:
                        raise
            if accepted is None:
                await attempt.record_failure()
            else:
                await attempt.reset()
            await ip_attempt.record_failure()
    if rate_limit.cleanup_due():
        await rate_limit.cleanup_stale(get_deployment_policy().rate_limit_rules)
    if accepted is None:
        if throttled:
            raise HTTPException(
                429, "Zu viele Versuche. Bitte versuchen Sie es später erneut."
            )
        raise HTTPException(
            403,
            "Diese Einladung ist nicht mehr gültig. "
            "Bitte wenden Sie sich an Ihre Verwaltung.",
        )
    return Response(status_code=204)
