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

    async def list_numbers_by_property(
        self, property_id: uuid.UUID, *, exclude_unit_id: uuid.UUID | None = None
    ) -> Sequence[str]:
        stmt = select(Unit.number).where(
            Unit.organization_id == self._org_id, Unit.property_id == property_id
        )
        if exclude_unit_id is not None:
            stmt = stmt.where(Unit.id != exclude_unit_id)
        return (await self._session.scalars(stmt)).all()

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
