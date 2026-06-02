"""Resolution repository -- tenant-scoped.

Extends TenantRepository with queries for Beschlüsse (M8).
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resolution import Resolution
from app.repositories.base import TenantRepository


class ResolutionRepository(TenantRepository[Resolution]):
    model = Resolution

    async def list_for_meeting(self, meeting_id: uuid.UUID) -> Sequence[Resolution]:
        stmt = (
            self._base_query()
            .where(Resolution.meeting_id == meeting_id)
            .order_by(Resolution.created_at)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def update(self, entity: Resolution) -> Resolution:
        await self._session.flush()
        return entity

    async def delete(self, entity: Resolution) -> None:
        await self._session.delete(entity)
        await self._session.flush()
