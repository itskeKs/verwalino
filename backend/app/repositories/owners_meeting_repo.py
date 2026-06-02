"""OwnersMeeting repository -- tenant-scoped.

Extends TenantRepository with queries for the Versammlung (M8).
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.owners_meeting import OwnersMeeting
from app.repositories.base import TenantRepository


class OwnersMeetingRepository(TenantRepository[OwnersMeeting]):
    model = OwnersMeeting

    async def list_for_property(self, property_id: uuid.UUID) -> Sequence[OwnersMeeting]:
        stmt = (
            self._base_query()
            .where(OwnersMeeting.property_id == property_id)
            .order_by(OwnersMeeting.meeting_date.desc(), OwnersMeeting.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_all_ordered(self) -> Sequence[OwnersMeeting]:
        stmt = self._base_query().order_by(
            OwnersMeeting.meeting_date.desc(), OwnersMeeting.created_at.desc()
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def update(self, entity: OwnersMeeting) -> OwnersMeeting:
        await self._session.flush()
        return entity

    async def delete(self, entity: OwnersMeeting) -> None:
        await self._session.delete(entity)
        await self._session.flush()
