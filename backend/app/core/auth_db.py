"""Typed access to the narrowly scoped runtime-auth database functions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class LoginPrincipal:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    password_hash: str
    is_active: bool
    role: str
    session_version: int


async def lookup_login_principal(
    session: AsyncSession, email: str
) -> LoginPrincipal | None:
    result = await session.execute(
        text("SELECT * FROM public.app_auth_lookup_versioned(:email)"), {"email": email}
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    return LoginPrincipal(
        user_id=row["user_id"],
        organization_id=row["organization_id"],
        password_hash=row["password_hash"],
        is_active=row["is_active"],
        role=row["role"],
        session_version=row["session_version"],
    )


async def validate_session_principal(
    session: AsyncSession, user_id: uuid.UUID, organization_id: uuid.UUID, session_version: int = 0
) -> str | None:
    result = await session.execute(
        text("SELECT * FROM public.app_validate_session_versioned(:user_id, :organization_id, :session_version)"),
        {"user_id": user_id, "organization_id": organization_id, "session_version": session_version},
    )
    return result.scalar_one_or_none()
