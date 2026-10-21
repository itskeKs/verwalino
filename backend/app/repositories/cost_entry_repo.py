"""CostEntry repository -- tenant-scoped.

Extends TenantRepository with active-chain query and status-filtered queries.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Sequence

from sqlalchemy import func, select, Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost_category import CostCategory
from app.models.cost_entry import CostEntry
from app.repositories.base import TenantRepository


class CostEntryRepository(TenantRepository[CostEntry]):
    model = CostEntry

    async def list_by_filters(self, *, status: str | None = None,
                              property_id: uuid.UUID | None = None) -> Sequence[CostEntry]:
        stmt = self._base_query()
        if status is not None:
            stmt = stmt.where(CostEntry.status == status)
        if property_id is not None:
            stmt = stmt.where(CostEntry.property_id == property_id)
        if status is not None:
            stmt = stmt.order_by(CostEntry.created_at.desc())
        elif property_id is not None:
            stmt = stmt.order_by(CostEntry.invoice_date.desc())
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_active(self) -> Sequence[CostEntry]:
        """List only active (non-superseded) confirmed entries."""
        stmt = (
            self._base_query()
            .where(CostEntry.status == "confirmed")
            .where(CostEntry.superseded_by_id.is_(None))
            .order_by(CostEntry.invoice_date.desc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_by_status(self, status: str) -> Sequence[CostEntry]:
        stmt = (
            self._base_query()
            .where(CostEntry.status == status)
            .order_by(CostEntry.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_by_property(self, property_id: uuid.UUID) -> Sequence[CostEntry]:
        stmt = (
            self._base_query()
            .where(CostEntry.property_id == property_id)
            .order_by(CostEntry.invoice_date.desc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def property_year_totals(self, property_id: uuid.UUID, start: date, end: date):
        """Each current confirmed invoice contributes its gross amount exactly once."""
        stmt = (self._base_query()
            .join(CostCategory, (CostCategory.id == CostEntry.cost_category_id)
                  & (CostCategory.plan_version == CostEntry.cost_category_plan_version))
            .where(CostEntry.property_id == property_id, CostEntry.status == 'confirmed',
                   CostEntry.superseded_by_id.is_(None),
                   CostEntry.invoice_date >= start, CostEntry.invoice_date <= end)
            .with_only_columns(CostCategory.id.label('category_id'),
                CostCategory.code.label('category_code'), CostCategory.label.label('category_name'),
                CostEntry.tenant_apportionable.label('tenant_apportionability'),
                func.sum(CostEntry.gross_amount).label('amount'))
            .group_by(CostCategory.id, CostCategory.code, CostCategory.label, CostEntry.tenant_apportionable)
            .order_by(CostCategory.code, CostCategory.id, CostEntry.tenant_apportionable))
        return (await self._session.execute(stmt)).all()

    async def update(self, entity: CostEntry) -> CostEntry:
        """Flush changes on an already-tracked entity."""
        await self._session.flush()
        return entity

    async def delete(self, entity: CostEntry) -> None:
        """Delete a draft/rejected entry. Trigger blocks confirmed rows."""
        await self._session.delete(entity)
        await self._session.flush()
