"""DunningNotice repository -- tenant-scoped.

Extends TenantRepository with queries for the Mahnwesen (M7).
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dunning_notice import DunningNotice
from app.models.dunning_scope_context import DunningScopeContext
from app.models.lease_agreement import LeaseAgreement
from app.repositories.base import TenantRepository


class DunningNoticeRepository(TenantRepository[DunningNotice]):
    model = DunningNotice

    async def list_all_ordered(self) -> Sequence[DunningNotice]:
        stmt = self._base_query().order_by(
            DunningNotice.issue_date.desc(), DunningNotice.created_at.desc()
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_by_filters(
        self,
        *,
        tenant_profile_id: uuid.UUID | None = None,
        owner_profile_id: uuid.UUID | None = None,
        level: int | None = None,
        status: str | None = None,
    ) -> Sequence[DunningNotice]:
        stmt = self._base_query()
        if tenant_profile_id is not None:
            stmt = stmt.where(DunningNotice.tenant_profile_id == tenant_profile_id)
        if owner_profile_id is not None:
            stmt = stmt.where(DunningNotice.owner_profile_id == owner_profile_id)
        if level is not None:
            stmt = stmt.where(DunningNotice.level == level)
        if status == 'cancelled':
            stmt = stmt.where(DunningNotice.cancelled_at.is_not(None))
        elif status is not None:
            stmt = stmt.where(DunningNotice.status == status, DunningNotice.cancelled_at.is_(None))
        stmt = stmt.order_by(
            DunningNotice.issue_date.desc(), DunningNotice.created_at.desc()
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def issued_for_lease(self, lease_id: uuid.UUID, scope_id: uuid.UUID | None):
        tenant_id = select(LeaseAgreement.tenant_profile_id).where(
            LeaseAgreement.organization_id == self._org_id, LeaseAgreement.id == lease_id,
        ).scalar_subquery()
        context = DunningScopeContext
        # Before provenance was introduced, notices were attached to a tenant
        # profile only. Show those in the legacy account view for that tenant.
        stmt = select(
            DunningNotice.id, DunningNotice.issue_date, DunningNotice.level,
            DunningNotice.fee_amount, DunningNotice.interest_amount,
        ).outerjoin(context, and_(
            context.organization_id == DunningNotice.organization_id,
            context.dunning_notice_id == DunningNotice.id,
        )).where(
            DunningNotice.organization_id == self._org_id,
            DunningNotice.tenant_profile_id == tenant_id,
            DunningNotice.status == "issued",
            DunningNotice.cancelled_at.is_(None),
            context.scope_id == scope_id,
            context.lease_agreement_id == (lease_id if scope_id is not None else None),
        ).order_by(DunningNotice.issue_date.desc(), DunningNotice.id)
        return (await self._session.execute(stmt)).mappings().all()

    async def count_issued_for_tenant(self, tenant_profile_id: uuid.UUID) -> int:
        """Count issued dunning notices for a tenant (for escalation level)."""
        stmt = (
            select(func.count())
            .select_from(DunningNotice)
            .where(DunningNotice.organization_id == self._org_id)
            .where(DunningNotice.tenant_profile_id == tenant_profile_id)
            .where(DunningNotice.status == "issued")
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def count_issued_for_owner(self, owner_profile_id: uuid.UUID) -> int:
        """Count issued dunning notices for an owner (for escalation level)."""
        stmt = (
            select(func.count())
            .select_from(DunningNotice)
            .where(DunningNotice.organization_id == self._org_id)
            .where(DunningNotice.owner_profile_id == owner_profile_id)
            .where(DunningNotice.status == "issued")
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def update(self, entity: DunningNotice) -> DunningNotice:
        """Flush changes on an already-tracked entity (trigger blocks issued)."""
        await self._session.flush()
        return entity

    async def delete(self, entity: DunningNotice) -> None:
        """Delete a draft dunning notice. Trigger blocks issued rows."""
        await self._session.delete(entity)
        await self._session.flush()
