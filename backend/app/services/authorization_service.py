"""Live exact-scope checks. Call before reading sources or committing effects.

The shared transaction lock orders effects against grant revocation. It does not
replace rechecking a queued job in a fresh transaction immediately before effects.
Organization roles never substitute for a financial capability.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import ScopeCapability
from app.core.tenant import AuthContext
from app.models.accounting_scope import AccountingScope
from app.models.audit_event import AuditEvent
from app.models.scope_grant import ScopeGrant


class AuthorizationService:
    def __init__(self, session: AsyncSession, auth: AuthContext):
        self.session = session
        self.auth = auth

    async def _lock(self, *, exclusive: bool = False) -> None:
        # Identity originates in get_db or a validated worker principal, never
        # a scope header or a caller-supplied grant list.
        context = (await self.session.execute(text(
            "SELECT NULLIF(current_setting('app.current_org_id',true),'')::uuid AS org, "
            "NULLIF(current_setting('app.current_user_id',true),'')::uuid AS actor"
        ))).one()
        if context.org != self.auth.organization_id or context.actor != self.auth.user_id:
            raise HTTPException(403, "Authenticated scope context required")
        lock = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
        await self.session.execute(text(
            f"SELECT {lock}(hashtextextended('scope-acl:' || CAST(:org AS text),0))"
        ), {"org": str(self.auth.organization_id)})

    async def require(self, scope_id: uuid.UUID, capability: ScopeCapability) -> AccountingScope:
        await self._lock()
        allowed = await self.session.scalar(text(
            "SELECT has_scope_capability(:org,:scope,:capability)"
        ), {"org": self.auth.organization_id, "scope": scope_id, "capability": capability.value})
        if not allowed:
            # Do not reveal whether a foreign/inaccessible scope exists.
            raise HTTPException(404, "Accounting scope not accessible")
        scope = await self.session.scalar(select(AccountingScope).where(
            AccountingScope.organization_id == self.auth.organization_id,
            AccountingScope.id == scope_id,
        ))
        if scope is None:
            raise HTTPException(404, "Accounting scope not accessible")
        return scope

    async def _require_administration(self) -> None:
        await self._lock(exclusive=True)
        role = await self.session.scalar(text("SELECT scope_actor_role(:org)"), {"org": self.auth.organization_id})
        if role != "OWNER_ADMIN":
            raise HTTPException(403, "Scope administration requires an active administrator")

    async def grant(self, scope_id: uuid.UUID, user_id: uuid.UUID,
                    capabilities: set[ScopeCapability], reason: str,
                    expires_at: datetime | None = None) -> ScopeGrant:
        await self._require_administration()
        if not reason.strip() or ScopeCapability.READ not in capabilities:
            raise HTTPException(422, "A reason and read capability are required")
        scope = await self.session.scalar(select(AccountingScope).where(
            AccountingScope.organization_id == self.auth.organization_id,
            AccountingScope.id == scope_id,
        ))
        if scope is None:
            raise HTTPException(404, "Accounting scope not accessible")
        if not await self.session.scalar(text(
            'SELECT EXISTS(SELECT 1 FROM membership m JOIN "user" u ON u.id=m.user_id '
            'AND u.organization_id=m.organization_id WHERE m.organization_id=:org AND m.user_id=:user AND u.is_active)'
        ), {"org": self.auth.organization_id, "user": user_id}):
            raise HTTPException(404, "Team member not found")
        existing = await self.session.scalar(select(ScopeGrant).where(
            ScopeGrant.organization_id == self.auth.organization_id,
            ScopeGrant.scope_id == scope_id, ScopeGrant.user_id == user_id,
            ScopeGrant.revoked_at.is_(None),
        ))
        if existing is not None:
            raise HTTPException(409, "Revoke the previous grant before replacing it")
        grant = ScopeGrant(organization_id=self.auth.organization_id, scope_id=scope_id,
                           user_id=user_id, capabilities=sorted(c.value for c in capabilities),
                           granted_by=self.auth.user_id, reason=reason.strip(), expires_at=expires_at)
        self.session.add(grant)
        await self.session.flush()
        self.session.add(AuditEvent(organization_id=self.auth.organization_id,
            actor_user_id=self.auth.user_id, entity_type="scope_grant", entity_id=grant.id,
            action="scope.granted", payload_diff={"scope_id": str(scope_id), "user_id": str(user_id), "capabilities": grant.capabilities}))
        await self.session.flush()
        return grant

    async def revoke(self, grant_id: uuid.UUID) -> None:
        await self._require_administration()
        grant = await self.session.scalar(select(ScopeGrant).where(
            ScopeGrant.organization_id == self.auth.organization_id,
            ScopeGrant.id == grant_id,
        ).with_for_update())
        if grant is None:
            raise HTTPException(404, "Grant not found")
        if grant.revoked_at is not None:
            return
        await self.session.execute(text(
            "UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor "
            "WHERE organization_id=:org AND id=:grant"
        ), {"actor": self.auth.user_id, "org": self.auth.organization_id, "grant": grant.id})
        await self.session.refresh(grant)
        self.session.add(AuditEvent(organization_id=self.auth.organization_id,
            actor_user_id=self.auth.user_id, entity_type="scope_grant", entity_id=grant.id,
            action="scope.revoked", payload_diff={"scope_id": str(grant.scope_id), "user_id": str(grant.user_id)}))
        await self.session.flush()
