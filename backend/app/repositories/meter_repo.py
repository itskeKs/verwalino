"""Meter and MeterReading repositories -- tenant-scoped."""

import uuid
from typing import Sequence

from sqlalchemy import Select, and_, func

from app.models.meter import Meter, MeterReading
from app.models.property import Property
from app.repositories.base import TenantRepository


class MeterRepository(TenantRepository[Meter]):
    model = Meter

    def _base_query(self) -> Select:
        return (
            super()._base_query()
            .join(Property, and_(Property.id == Meter.property_id,
                                 Property.organization_id == self._org_id))
            .where(func.property_master_access(self._org_id, Meter.property_id, "read"))
            .order_by(Meter.meter_number, Meter.id)
        )

    async def list_by_property(self, property_id: uuid.UUID) -> Sequence[Meter]:
        stmt = self._base_query().where(Meter.property_id == property_id)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_by_unit(self, unit_id: uuid.UUID) -> Sequence[Meter]:
        stmt = self._base_query().where(Meter.unit_id == unit_id)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_by_property_and_unit(
        self, property_id: uuid.UUID, unit_id: uuid.UUID
    ) -> Sequence[Meter]:
        stmt = (
            self._base_query()
            .where(Meter.property_id == property_id, Meter.unit_id == unit_id)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()


class MeterReadingRepository(TenantRepository[MeterReading]):
    model = MeterReading

    async def list_by_meter(
        self, meter_id: uuid.UUID
    ) -> Sequence[MeterReading]:
        stmt = (
            self._base_query()
            .where(MeterReading.meter_id == meter_id)
            .order_by(MeterReading.reading_date, MeterReading.id)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()
