"""Unit repository -- tenant-scoped."""

import uuid
from typing import Sequence

from sqlalchemy import select

from app.models.unit import Unit
from app.repositories.base import TenantRepository


class UnitRepository(TenantRepository[Unit]):
    model = Unit

    async def list_by_property(self, property_id: uuid.UUID) -> Sequence[Unit]:
        stmt = self._base_query().where(Unit.property_id == property_id)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_by_property_for_update(
        self, property_id: uuid.UUID
    ) -> Sequence[Unit]:
        stmt = (
            self._base_query()
            .where(Unit.property_id == property_id)
            .order_by(Unit.id)
            .with_for_update()
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_by_id_for_update(self, unit_id: uuid.UUID) -> Unit | None:
        stmt = self._base_query().where(Unit.id == unit_id).with_for_update()
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
