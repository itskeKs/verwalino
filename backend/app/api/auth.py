"""Auth endpoints: login / logout.

Login uses the migrator engine to look up users because the app_rw role
with RLS cannot query users before knowing the org.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.core.config import settings
from app.core.security import (
    create_session_token,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    user_id: str
    organization_id: str
    role: str


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, response: Response):
    """Authenticate and set session cookie.

    Uses the migrator connection for the auth lookup because the
    user table has RLS and we don't yet know the org.
    """
    auth_engine = create_async_engine(settings.database_url_migrator, pool_size=2)
    try:
        async with auth_engine.connect() as conn:
            # Look up user
            result = await conn.execute(
                text('SELECT id, organization_id, email, password_hash, is_active FROM "user" WHERE email = :email'),
                {"email": body.email},
            )
            user = result.first()

            if user is None:
                raise HTTPException(status_code=401, detail="Invalid credentials")

            if not verify_password(body.password, user.password_hash):
                raise HTTPException(status_code=401, detail="Invalid credentials")

            if not user.is_active:
                raise HTTPException(status_code=403, detail="Account disabled")

            # Look up membership/role
            result2 = await conn.execute(
                text(
                    "SELECT role FROM membership "
                    "WHERE user_id = :uid AND organization_id = :oid"
                ),
                {"uid": user.id, "oid": user.organization_id},
            )
            membership = result2.first()

            if membership is None:
                raise HTTPException(status_code=403, detail="No role assigned")
    finally:
        await auth_engine.dispose()

    token = create_session_token(
        user_id=user.id,
        organization_id=user.organization_id,
        role=membership.role,
    )

    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=False,  # Set to True in production (HTTPS)
        samesite="lax",
        max_age=settings.session_max_age_seconds,
        path="/",
    )

    return LoginResponse(
        user_id=str(user.id),
        organization_id=str(user.organization_id),
        role=membership.role,
    )


@router.post("/logout", status_code=204)
async def logout(response: Response):
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
    )
