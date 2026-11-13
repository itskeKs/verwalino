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
    session_version: int = 0


def _get_versioned_session(request: Request) -> tuple[uuid.UUID, uuid.UUID, int, bool] | None:
    """Decode once; legacy cookies represent generation zero, never a wildcard."""
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    data = decode_session_token(token)
    if not data:
        return None
    return uuid.UUID(data["uid"]), uuid.UUID(data["oid"]), data.get("sv", 0), data.get("r") == 1


def _get_session_identities(request: Request) -> tuple[uuid.UUID, uuid.UUID] | None:
    """Compatibility helper for callers that only need cookie identities."""
    identities = _get_versioned_session(request)
    return identities[:2] if identities is not None else None


# Routes a hosted second-factor enrolment session may reach: enrolment,
# session state and logout (issue #208, owner decision of 5 Nov 2026).
SECOND_FACTOR_ENROLMENT_PATHS = {
    "/api/auth/second-factor",
    "/api/auth/second-factor/enrolment",
    "/api/auth/second-factor/enrolment/confirm",
    "/api/auth/session",
    "/api/auth/logout",
}


def _second_factor_enrolment_required(request: Request) -> bool:
    """Hosted: a restricted session reaches only enrolment and sign-out."""
    from app.core.deployment_policy import get_deployment_policy

    if get_deployment_policy().deployment_mode != "hosted":
        return False
    identities = _get_versioned_session(request)
    if identities is None or not identities[3]:
        return False
    return request.url.path not in SECOND_FACTOR_ENROLMENT_PATHS


async def get_current_user(request: Request) -> AuthContext:
    """FastAPI dependency -- returns AuthContext or raises 401."""
    identities = _get_versioned_session(request)
    if identities is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    if _second_factor_enrolment_required(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "second_factor_enrolment_required"},
        )
    user_id, organization_id, session_version = identities[:3]
    async with get_session_factory()() as session:
        async with session.begin():
            role = await validate_session_principal(session, user_id, organization_id, session_version)
            if role is not None:
                await set_tenant_context(session, organization_id)
                await session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(user_id)})
                deletion = await session.scalar(text("SELECT status FROM organization_deletion WHERE organization_id=:org"), {"org": organization_id})
                if deletion == "deleted_pending_retention":
                    role = None
                elif deletion == "cooling_off" and request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path not in {
                    "/api/organizations/deletion/cancel", "/api/organizations/export", "/api/auth/logout"
                }:
                    raise HTTPException(409, "Ihre Organisation ist während der siebentägigen Widerrufsfrist schreibgeschützt.")
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    request.state.organization_id = organization_id
    return AuthContext(user_id=user_id, organization_id=organization_id, role=role, session_version=session_version)


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
