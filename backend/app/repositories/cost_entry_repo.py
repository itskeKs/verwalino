"""CostEntry repository -- tenant-scoped.

Extends TenantRepository with active-chain query and status-filtered queries.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select, Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost_entry import CostEntry
from app.repositories.base import TenantRepository


class CostEntryRepository(TenantRepository[CostEntry]):
    model = CostEntry

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

    async def update(self, entity: CostEntry) -> CostEntry:
        """Flush changes on an already-tracked entity."""
        await self._session.flush()
        return entity

    async def delete(self, entity: CostEntry) -> None:
        """Delete a draft/rejected entry. Trigger blocks confirmed rows."""
        await self._session.delete(entity)
        await self._session.flush()
