"""Auth endpoints backed exclusively by the app runtime database role."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select, text

from app.core.auth_db import lookup_login_principal, validate_session_principal
from app.core.live_principal import require_live_principal
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
from app.core.tenant import AuthContext, get_current_user
from app.models.user import User

router = APIRouter(prefix="/api/auth", tags=["auth"])
_DUMMY_PASSWORD_HASH = hash_password("runtime-auth-dummy-password")


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


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, response: Response):
    """Authenticate with the narrow app_rw lookup function only."""
    async with get_session_factory()() as session:
        async with session.begin():
            principal = await lookup_login_principal(session, body.email)

    # Always verify one Argon2 hash so unknown identities have no cheap path.
    password_hash = principal.password_hash if principal is not None else _DUMMY_PASSWORD_HASH
    password_matches = verify_password(body.password, password_hash)
    if principal is None or not password_matches or not principal.is_active or principal.role not in {"OWNER_ADMIN", "CLERK", "READ_ONLY"}:
        raise _invalid_credentials()

    token = create_session_token(principal.user_id, principal.organization_id, principal.session_version)
    _set_session_cookie(response, token)
    return LoginResponse(
        user_id=str(principal.user_id),
        organization_id=str(principal.organization_id),
        role=principal.role,
    )


@router.get("/session", response_model=LoginResponse)
async def current_session(auth: AuthContext = Depends(get_current_user)):
    return LoginResponse(
        user_id=str(auth.user_id),
        organization_id=str(auth.organization_id),
        role=auth.role,
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
