"""AgendaItem repository -- tenant-scoped.

Extends TenantRepository with queries for TOPs (M8).
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agenda_item import AgendaItem
from app.repositories.base import TenantRepository


class AgendaItemRepository(TenantRepository[AgendaItem]):
    model = AgendaItem

    async def list_for_meeting(self, meeting_id: uuid.UUID) -> Sequence[AgendaItem]:
        stmt = (
            self._base_query()
            .where(AgendaItem.meeting_id == meeting_id)
            .order_by(AgendaItem.item_number)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def next_item_number(self, meeting_id: uuid.UUID) -> int:
        """Return the next sequential item_number for a meeting."""
        stmt = (
            select(func.coalesce(func.max(AgendaItem.item_number), 0))
            .where(AgendaItem.organization_id == self._org_id)
            .where(AgendaItem.meeting_id == meeting_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one() + 1

    async def update(self, entity: AgendaItem) -> AgendaItem:
        await self._session.flush()
        return entity

    async def delete(self, entity: AgendaItem) -> None:
        await self._session.delete(entity)
        await self._session.flush()
