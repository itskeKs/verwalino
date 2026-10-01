"""Tenant-context middleware and FastAPI dependencies.

The middleware:
  1. Extracts the session cookie on every request.
  2. Decodes it (server-side signed token).
  3. Stores user_id, organization_id, and role in the request state.
  4. The DB session dependency uses organization_id to SET LOCAL the GUC.

Fail-closed: if no valid session exists, the request state has no org context
and any tenant DB query returns zero rows (GUC defaults to impossible UUID).
Auth-required endpoints reject unauthenticated requests explicitly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.auth_db import validate_session_principal
from app.core.db import get_session_factory, set_tenant_context
from app.core.security import decode_session_token
from app.core.rbac import Role, require_role
from sqlalchemy import text


# ---------------------------------------------------------------------------
# Request-scoped auth context
# ---------------------------------------------------------------------------
@dataclass
class AuthContext:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str


def _get_session_identities(request: Request) -> tuple[uuid.UUID, uuid.UUID] | None:
    """Extract auth context from the session cookie.  Returns None if absent/invalid."""
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    data = decode_session_token(token)
    if not data:
        return None
    return uuid.UUID(data["uid"]), uuid.UUID(data["oid"])


async def get_current_user(request: Request) -> AuthContext:
    """FastAPI dependency -- returns AuthContext or raises 401."""
    identities = _get_session_identities(request)
    if identities is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    user_id, organization_id = identities
    async with get_session_factory()() as session:
        async with session.begin():
            role = await validate_session_principal(session, user_id, organization_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return AuthContext(user_id=user_id, organization_id=organization_id, role=role)


async def get_db(
    auth: AuthContext = Depends(get_current_user),
) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency -- provides a tenant-scoped DB session.

    The organization_id comes exclusively from the server-side session,
    never from client-supplied parameters.
    """
    async with get_session_factory()() as session:
        async with session.begin():
            await set_tenant_context(session, auth.organization_id)
            await session.execute(text("SELECT set_config('app.current_user_id', :actor, true)"), {"actor": str(auth.user_id)})
            yield session


# ---------------------------------------------------------------------------
# Role-gated dependencies (combine auth + RBAC)
# ---------------------------------------------------------------------------
def require_admin_dep(auth: AuthContext = Depends(get_current_user)) -> AuthContext:
    require_role(auth.role, [Role.OWNER_ADMIN])
    return auth


def require_write_dep(auth: AuthContext = Depends(get_current_user)) -> AuthContext:
    require_role(auth.role, [Role.OWNER_ADMIN, Role.CLERK])
    return auth
