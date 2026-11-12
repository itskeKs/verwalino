"""DunningNotice repository -- tenant-scoped.

Extends TenantRepository with queries for the Mahnwesen (M7).
"""

from __future__ import annotations

import uuid
from typing import Any, Mapping, Sequence

from sqlalchemy import and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from app.models.dunning_notice import LEASE_SOURCE_KEY, DunningNotice
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
            (context.lease_agreement_id == lease_id if scope_id is not None
             else DunningNotice.included_items[0][LEASE_SOURCE_KEY].astext == str(lease_id)),
        ).order_by(DunningNotice.issue_date.desc(), DunningNotice.id)
        return (await self._session.execute(stmt)).mappings().all()

    async def count_issued_with_overdue_items(
        self,
        *,
        item_keys: Sequence[Mapping[str, Any]],
        tenant_profile_id: uuid.UUID | None = None,
        owner_profile_id: uuid.UUID | None = None,
        lease_id: uuid.UUID | None = None,
        scope_id: uuid.UUID | None = None,
    ) -> int:
        """Count issued notices that escalate the currently overdue items.

        An already-issued notice raises the level of a new one only when it
        belongs to the same person (the same lease for tenants, mirroring
        issued_for_lease) and its frozen included_items contain at least one
        of the currently overdue items -- identity is payment_type +
        period_year + period_month. Notices for paid-off or unrelated arrears
        do not count. An unscoped tenant notice without any lease link (issued
        before the link existed) matches on the tenant and the items alone.
        """
        if not item_keys:
            return 0
        stmt = select(func.count()).select_from(DunningNotice)
        if lease_id is not None:
            context = DunningScopeContext
            stmt = stmt.outerjoin(context, and_(
                context.organization_id == DunningNotice.organization_id,
                context.dunning_notice_id == DunningNotice.id,
            ))
        stmt = stmt.where(
            DunningNotice.organization_id == self._org_id,
            DunningNotice.status == "issued",
            DunningNotice.cancelled_at.is_(None),
            or_(*(
                DunningNotice.included_items.op("@>")(cast([key], JSONB))
                for key in item_keys
            )),
        )
        if tenant_profile_id is not None:
            stmt = stmt.where(DunningNotice.tenant_profile_id == tenant_profile_id)
        else:
            stmt = stmt.where(DunningNotice.owner_profile_id == owner_profile_id)
        if lease_id is not None:
            # An unscoped notice issued before the lease link existed carries neither
            # the link nor a scope context; it counts for the tenant as it always did.
            linked_lease = DunningNotice.included_items[0][LEASE_SOURCE_KEY].astext
            stmt = stmt.where(
                context.scope_id == scope_id,
                (context.lease_agreement_id == lease_id if scope_id is not None
                 else or_(linked_lease == str(lease_id), linked_lease.is_(None))),
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
