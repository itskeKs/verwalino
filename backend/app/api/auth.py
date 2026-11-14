"""Auth endpoints backed exclusively by the app runtime database role."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit
from app.core.auth_db import lookup_login_principal, validate_session_principal
from app.core.deployment_policy import get_deployment_policy
from app.core.live_principal import get_live_member_db, require_live_principal
from app.core.config import settings
from app.core.db import get_session_factory, set_tenant_context
from app.core.security import (
    MAX_PASSWORD_LENGTH,
    create_session_token,
    hash_password,
    normalize_email,
    validate_new_password,
    verify_password,
)
from app.core.tenant import AuthContext, get_current_user, get_db
from app.core.rate_limit import ThrottleRequest, throttled
from app.models.organization import Organization
from app.models.user import User
from app.models.user_recovery_code import UserRecoveryCode
from app.models.user_second_factor import UserSecondFactor
from app.services import second_factor_service
from app.services.user_preferences_service import UserPreferencesService

router = APIRouter(prefix="/api/auth", tags=["auth"])
_DUMMY_PASSWORD_HASH = hash_password("runtime-auth-dummy-password")

_pending_serializer = URLSafeTimedSerializer(settings.session_secret)
_PENDING_SALT = "hv-login-pending"


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def normalize_login_email(cls, value: object) -> str:
        return normalize_email(value)

    @field_validator("password")
    @classmethod
    def login_password_is_bounded(cls, value: str) -> str:
        if len(value) > MAX_PASSWORD_LENGTH:
            raise ValueError(f"Password must not exceed {MAX_PASSWORD_LENGTH} characters")
        return value


class LoginResponse(BaseModel):
    user_id: str
    organization_id: str
    role: str
    # Issue #208: an enrolled factor turns login into two steps; hosted mode
    # sends an unenrolled staff account through enrolment first.
    second_factor_required: bool = False
    second_factor_enrolment_required: bool = False
    pending_token: str | None = None


class UserPreferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    design_theme: Literal['klar', 'warm']


@router.get('/preferences', response_model=UserPreferences)
async def preferences(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    return await UserPreferencesService(db, auth).get()


@router.patch('/preferences', response_model=UserPreferences)
async def update_preferences(
    body: UserPreferences,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    # Every user UPDATE acquires the exclusive ACL lock (migration 0138).
    # Acquire it before any user row lock, including for personal read-only users.
    await require_live_principal(db, auth, writer=False, exclusive=True)
    return await UserPreferencesService(db, auth).update(body.design_theme)


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def new_password_is_valid(cls, value: str) -> str:
        return validate_new_password(value)


def _invalid_credentials() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=settings.session_max_age_seconds,
        path="/",
    )


async def _confirmed_second_factor(session: AsyncSession, principal) -> bool:
    """Whether the principal has a confirmed TOTP factor (issue #208)."""
    await set_tenant_context(session, principal.organization_id)
    await session.execute(
        text("SELECT set_config('app.current_user_id', :actor, true)"),
        {"actor": str(principal.user_id)},
    )
    row = await session.scalar(
        select(UserSecondFactor.confirmed).where(
            UserSecondFactor.organization_id == principal.organization_id,
            UserSecondFactor.user_id == principal.user_id,
        )
    )
    return row is True


def _create_pending_token(principal, email: str) -> str:
    """Short-lived proof that the password step passed, awaiting the code."""
    return _pending_serializer.dumps(
        {
            "uid": str(principal.user_id),
            "oid": str(principal.organization_id),
            "sv": principal.session_version,
            "email": email,
        },
        salt=_PENDING_SALT,
    )


def _decode_pending_token(token: str) -> dict | None:
    try:
        data = _pending_serializer.loads(
            token, salt=_PENDING_SALT,
            max_age=second_factor_service.PENDING_TOKEN_MAX_AGE_SECONDS,
        )
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or set(data) != {"uid", "oid", "sv", "email"}:
        return None
    try:
        uuid.UUID(data["uid"])
        uuid.UUID(data["oid"])
    except (ValueError, AttributeError):
        return None
    if type(data["sv"]) is not int or data["sv"] < 0 or not isinstance(data["email"], str):
        return None
    return data


def _login_answer(principal, **flags) -> LoginResponse:
    return LoginResponse(
        user_id=str(principal.user_id),
        organization_id=str(principal.organization_id),
        role=principal.role,
        **flags,
    )


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, response: Response, throttle: ThrottleRequest = Depends(throttled("login"))):
    """Authenticate with the narrow app_rw lookup function only."""
    async with get_session_factory()() as session:
        async with session.begin():
            attempt = await throttle.check(session, body.email)
            principal = await lookup_login_principal(session, body.email) if attempt.allowed else None

        # Every failure path pays for one hash, with no transaction/row lock held.
        password_hash = principal.password_hash if principal is not None else _DUMMY_PASSWORD_HASH
        password_matches = verify_password(body.password, password_hash)
        authenticated = bool(principal is not None and password_matches and principal.is_active
                             and principal.role in {"OWNER_ADMIN", "CLERK", "READ_ONLY"})
        factor_confirmed = False
        async with session.begin():
            if authenticated:
                if factor_confirmed := await _confirmed_second_factor(session, principal):
                    # The code still has to pass: refund only this attempt's
                    # reservation so the failure history cannot be cleared
                    # with the password before the second step.
                    await attempt.refund_reservation()
                else:
                    await attempt.reset()
            else:
                await attempt.record_failure()

    if rate_limit.cleanup_due():
        await rate_limit.cleanup_stale(attempt.rules)

    # Raise only after committing lockout state; an HTTP refusal must not roll
    # back failure finalization. Reservations already committed before hashing.
    if not authenticated:
        raise _invalid_credentials()

    if factor_confirmed:
        return _login_answer(
            principal,
            second_factor_required=True,
            pending_token=_create_pending_token(principal, body.email),
        )

    if get_deployment_policy().deployment_mode == "hosted":
        # Every hosted staff account needs a factor (owner decision, 5 Nov
        # 2026). The cookie is a normal session: get_current_user derives the
        # restriction from the database and lets this session reach only the
        # enrolment flow until a factor is confirmed. Self-hosted mode keeps
        # the factor optional.
        token = create_session_token(principal.user_id, principal.organization_id, principal.session_version)
        _set_session_cookie(response, token)
        return _login_answer(principal, second_factor_enrolment_required=True)

    token = create_session_token(principal.user_id, principal.organization_id, principal.session_version)
    _set_session_cookie(response, token)
    return _login_answer(principal)


class SecondFactorRequest(BaseModel):
    pending_token: str
    code: str | None = None
    recovery_code: str | None = None

    @model_validator(mode="after")
    def exactly_one_factor(self):
        if (self.code is None) == (self.recovery_code is None):
            raise ValueError("Provide exactly one of code or recovery_code")
        return self


class SecondFactorResponse(LoginResponse):
    # How many one-time recovery codes remain after this answer, if one was used.
    recovery_codes_remaining: int | None = None


async def _issue_full_session(session: AsyncSession, claims: dict) -> tuple:
    """Validate the pending claims against the live principal and factor."""
    principal = await lookup_login_principal(session, claims["email"])
    if (principal is None or not principal.is_active
            or str(principal.user_id) != claims["uid"]
            or principal.session_version != claims["sv"]
            or principal.role not in {"OWNER_ADMIN", "CLERK", "READ_ONLY"}):
        return None, None
    factor = await _load_factor(session, principal)
    if factor is None or not factor.confirmed:
        return None, None
    return principal, factor


async def _load_factor(session: AsyncSession, principal) -> UserSecondFactor | None:
    await set_tenant_context(session, principal.organization_id)
    await session.execute(
        text("SELECT set_config('app.current_user_id', :actor, true)"),
        {"actor": str(principal.user_id)},
    )
    return await session.scalar(
        select(UserSecondFactor).where(
            UserSecondFactor.organization_id == principal.organization_id,
            UserSecondFactor.user_id == principal.user_id,
        ).with_for_update()
    )


@router.post("/login/second-factor", response_model=SecondFactorResponse)
async def login_second_factor(
    body: SecondFactorRequest,
    response: Response,
    throttle: ThrottleRequest = Depends(throttled("login")),
):
    """Finish a two-step login with a TOTP code or a one-time recovery code.

    A wrong answer counts toward the same lockout as a wrong password (#20)
    and is refused with the same generic message.
    """
    claims = _decode_pending_token(body.pending_token)
    remaining: int | None = None
    async with get_session_factory()() as session:
        async with session.begin():
            attempt = await throttle.check(session, claims["email"]) if claims else None

        accepted = False
        principal = factor = None
        if claims is not None and attempt is not None and attempt.allowed:
            async with session.begin():
                principal, factor = await _issue_full_session(session, claims)
                if factor is not None:
                    if body.code is not None:
                        secret = second_factor_service.decrypt_secret(
                            principal.organization_id, factor.secret_encrypted
                        )
                        if secret is None:
                            accepted = False
                        else:
                            accepted, used_step = second_factor_service.verify_totp(
                                secret, body.code, time.time(), factor.last_totp_step
                            )
                            if accepted and (factor.last_totp_step is None or used_step > factor.last_totp_step):
                                factor.last_totp_step = used_step
                    else:
                        row = await session.scalar(
                            select(UserRecoveryCode).where(
                                UserRecoveryCode.organization_id == principal.organization_id,
                                UserRecoveryCode.user_id == principal.user_id,
                                UserRecoveryCode.code_hash
                                == second_factor_service.recovery_code_hash(
                                    principal.organization_id, body.recovery_code
                                ),
                                UserRecoveryCode.used_at.is_(None),
                            ).with_for_update()
                        )
                        if row is not None:
                            row.used_at = datetime.now(timezone.utc)
                            accepted = True
                            remaining = await session.scalar(
                                select(func.count())
                                .select_from(UserRecoveryCode)
                                .where(
                                    UserRecoveryCode.organization_id == principal.organization_id,
                                    UserRecoveryCode.user_id == principal.user_id,
                                    UserRecoveryCode.used_at.is_(None),
                                )
                            )
        async with session.begin():
            if accepted:
                await attempt.reset()
            elif attempt is not None:
                await attempt.record_failure()

    if not accepted:
        raise _invalid_credentials()

    token = create_session_token(principal.user_id, principal.organization_id, principal.session_version)
    _set_session_cookie(response, token)
    return SecondFactorResponse(
        user_id=str(principal.user_id),
        organization_id=str(principal.organization_id),
        role=principal.role,
        recovery_codes_remaining=remaining,
    )


@router.get("/session", response_model=LoginResponse)
async def current_session(request: Request, auth: AuthContext = Depends(get_current_user)):
    return LoginResponse(
        user_id=str(auth.user_id),
        organization_id=str(auth.organization_id),
        role=auth.role,
        second_factor_enrolment_required=bool(
            getattr(request.state, "second_factor_enrolment_required", False)
        ),
    )


@router.post("/logout", status_code=204)
async def logout(response: Response):
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/password", status_code=204)
async def change_password(
    body: PasswordChangeRequest,
    auth: AuthContext = Depends(get_current_user),
):
    """Change the password of the currently authenticated user."""
    async with get_session_factory()() as session:
        async with session.begin():
            await set_tenant_context(session, auth.organization_id)
            await session.execute(
                text("SELECT set_config('app.current_user_id', :actor, true)"),
                {"actor": str(auth.user_id)},
            )
            await require_live_principal(session, auth, writer=False, exclusive=True)
            role = await validate_session_principal(
                session, auth.user_id, auth.organization_id, auth.session_version
            )
            if role is None:
                raise _invalid_credentials()
            stmt = select(User).where(
                User.id == auth.user_id,
                User.organization_id == auth.organization_id,
            ).with_for_update()
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if user is None or not verify_password(body.old_password, user.password_hash):
                raise _invalid_credentials()

            user.password_hash = hash_password(body.new_password)
            await session.flush()


# ---------------------------------------------------------------------------
# Second factor (issue #208): enrolment under the authenticated account.
# Same tenant-session pattern as change_password.
# ---------------------------------------------------------------------------
class SecondFactorStatus(BaseModel):
    enrolled: bool


class EnrolmentStartResponse(BaseModel):
    secret: str
    otpauth_uri: str


class EnrolmentConfirmRequest(BaseModel):
    code: str


class EnrolmentConfirmResponse(BaseModel):
    recovery_codes: list[str]


async def _own_factor(session: AsyncSession, auth: AuthContext) -> UserSecondFactor | None:
    return await session.scalar(
        select(UserSecondFactor).where(
            UserSecondFactor.organization_id == auth.organization_id,
            UserSecondFactor.user_id == auth.user_id,
        ).with_for_update()
    )


@router.get("/second-factor", response_model=SecondFactorStatus)
async def second_factor_status(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    factor = await db.scalar(
        select(UserSecondFactor.confirmed).where(
            UserSecondFactor.organization_id == auth.organization_id,
            UserSecondFactor.user_id == auth.user_id,
        )
    )
    return SecondFactorStatus(enrolled=bool(factor))


@router.post("/second-factor/enrolment", response_model=EnrolmentStartResponse)
async def start_second_factor_enrolment(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Create a fresh pending enrolment for a factor-less account.

    An enrolled factor is replaced only through the audited admin reset
    (issue #208): starting enrolment over would silently drop the active
    protection."""
    await require_live_principal(db, auth, writer=False, exclusive=True)
    factor = await _own_factor(db, auth)
    if factor is not None and factor.confirmed:
        raise HTTPException(409, {"code": "second_factor_already_enrolled"})
    secret = second_factor_service.generate_secret()
    if factor is None:
        factor = UserSecondFactor(
            organization_id=auth.organization_id,
            user_id=auth.user_id,
            secret_encrypted="",
        )
        db.add(factor)
    factor.secret_encrypted = second_factor_service.encrypt_secret(auth.organization_id, secret)
    factor.confirmed = False
    factor.confirmed_at = None
    factor.last_totp_step = None
    await db.flush()

    user, organization = await _account_labels(db, auth)
    return EnrolmentStartResponse(
        secret=secret,
        otpauth_uri=second_factor_service.otpauth_uri(user, organization, secret),
    )


async def _account_labels(db: AsyncSession, auth: AuthContext) -> tuple[str, str]:
    user = await db.scalar(
        select(User.email).where(
            User.id == auth.user_id, User.organization_id == auth.organization_id
        )
    )
    organization = await db.scalar(
        select(Organization.name).where(Organization.id == auth.organization_id)
    )
    return user or "", organization or ""


@router.post("/second-factor/enrolment/confirm", response_model=EnrolmentConfirmResponse)
async def confirm_second_factor_enrolment(
    body: EnrolmentConfirmRequest,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Confirm the pending factor with one code; returns the recovery codes once."""
    await require_live_principal(db, auth, writer=False, exclusive=True)
    factor = await _own_factor(db, auth)
    if factor is None or factor.confirmed:
        raise HTTPException(409, {"code": "second_factor_no_pending_enrolment"})

    secret = second_factor_service.decrypt_secret(auth.organization_id, factor.secret_encrypted)
    if secret is None:
        raise _invalid_credentials()
    accepted, used_step = second_factor_service.verify_totp(secret, body.code, time.time(), None)
    if not accepted:
        raise _invalid_credentials()

    codes = second_factor_service.generate_recovery_codes()
    await db.execute(
        delete(UserRecoveryCode).where(
            UserRecoveryCode.organization_id == auth.organization_id,
            UserRecoveryCode.user_id == auth.user_id,
        )
    )
    for position, code in enumerate(codes):
        db.add(UserRecoveryCode(
            organization_id=auth.organization_id,
            user_id=auth.user_id,
            position=position,
            code_hash=second_factor_service.recovery_code_hash(auth.organization_id, code),
        ))
    factor.confirmed = True
    factor.confirmed_at = datetime.now(timezone.utc)
    # The confirm code is a used step: it must not sign anyone in again.
    factor.last_totp_step = used_step
    await db.flush()

    return EnrolmentConfirmResponse(recovery_codes=codes)
