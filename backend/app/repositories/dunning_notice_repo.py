"""DunningNotice repository -- tenant-scoped.

Extends TenantRepository with queries for the Mahnwesen (M7).
"""

from __future__ import annotations

import uuid
from typing import Any, Mapping, Sequence

from sqlalchemy import Boolean, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH

from app.engine.mietkonto import PT_HK_VORAUSZAHLUNG, PT_NK_VORAUSZAHLUNG
from app.models.dunning_notice import LEASE_SOURCE_KEY, DunningNotice
from app.models.dunning_scope_context import DunningScopeContext
from app.models.lease_agreement import LeaseAgreement
from app.repositories.base import TenantRepository


# Item count columns per statement; PostgreSQL allows 1664 select-list entries.
ITEM_KEYS_PER_STATEMENT = 500

# A stored item of an issued notice demands the item with the given key when its period is the
# same, its payment type is the same (or it is the joint operating advance that also demanded
# the heating advance, $joint_advance) and its due date is the same. An item stored without a
# due date (before the due date joined the key) matches every due date of its month and type;
# so does a key without one.
_DEMANDED_ITEM = cast(
    "$[*] ? (@.period_year == $period_year && @.period_month == $period_month"
    " && (@.payment_type == $payment_type"
    " || (@.payment_type == $joint_advance && @.combined_advance == true))"
    " && ($due_date == null || !exists(@.due_date) || @.due_date == $due_date))",
    JSONPATH,
)


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

    async def highest_item_demand_count(
        self,
        *,
        item_keys: Sequence[Mapping[str, Any]],
        tenant_profile_id: uuid.UUID | None = None,
        owner_profile_id: uuid.UUID | None = None,
        lease_id: uuid.UUID | None = None,
        scope_id: uuid.UUID | None = None,
    ) -> int:
        """How often the most often demanded of the given items was already demanded.

        An item was demanded once per already-issued notice of the same person (the
        same lease for tenants, mirroring issued_for_lease) whose frozen included_items
        contain it -- identity is the engine's item_key (payment_type + period_year +
        period_month + the due date of a dated item), so part payments and corrected or
        reversed postings stay the same item while two items of one month stay apart.
        An item stored without a due date matches every item of its month and type. The
        heating advance also counts a demand made inside the joint operating advance
        (combined_advance) of its month, because it reappears as its own item once the
        joint receipt is reversed. Drafts count nothing; an item without earlier notices
        counts 0. The level of a new notice is one more than this number
        (next_dunning_level). An unscoped tenant notice without any lease link (issued
        before the link existed) matches on the tenant and the item alone.
        """
        if not item_keys:
            return 0
        if len(item_keys) > ITEM_KEYS_PER_STATEMENT:
            return max([await self.highest_item_demand_count(
                item_keys=item_keys[start:start + ITEM_KEYS_PER_STATEMENT],
                tenant_profile_id=tenant_profile_id, owner_profile_id=owner_profile_id,
                lease_id=lease_id, scope_id=scope_id,
            ) for start in range(0, len(item_keys), ITEM_KEYS_PER_STATEMENT)])
        demanded = [func.jsonb_path_exists(DunningNotice.included_items, _DEMANDED_ITEM, cast({
            "payment_type": key["payment_type"], "period_year": key["period_year"],
            "period_month": key["period_month"], "due_date": key.get("due_date"),
            "joint_advance": PT_NK_VORAUSZAHLUNG if key["payment_type"] == PT_HK_VORAUSZAHLUNG else None,
        }, JSONB), type_=Boolean) for key in item_keys]
        # One pass over the person's issued notices, one count column per item.
        stmt = select(*(func.count().filter(item) for item in demanded)).select_from(DunningNotice)
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
            or_(*demanded),
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
        return max((await self._session.execute(stmt)).one())

    async def update(self, entity: DunningNotice) -> DunningNotice:
        """Flush changes on an already-tracked entity (trigger blocks issued)."""
        await self._session.flush()
        return entity

    async def delete(self, entity: DunningNotice) -> None:
        """Delete a draft dunning notice. Trigger blocks issued rows."""
        await self._session.delete(entity)
        await self._session.flush()
