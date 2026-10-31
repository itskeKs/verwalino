"""Meter and MeterReading repositories -- tenant-scoped."""

import uuid
from datetime import date
from typing import Sequence

from sqlalchemy import Select, and_, func, or_, select

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

    async def preceding_reading(self, meter_id, reading_date):
        return await self._session.scalar(self._base_query().where(
            MeterReading.meter_id == meter_id, MeterReading.reading_date <= reading_date,
        ).order_by(MeterReading.reading_date.desc(), MeterReading.value.desc(), MeterReading.id).limit(1))

    async def list_boundary_readings(
        self, meter_ids: Sequence[uuid.UUID], period_start: date, period_end: date,
    ) -> Sequence[MeterReading]:
        def window(boundary):
            ordinal = boundary.toordinal()
            return (date.fromordinal(max(1, ordinal - 31)),
                    date.fromordinal(min(date.max.toordinal(), ordinal + 31)))

        start_low, start_high = window(period_start)
        end_low, end_high = window(period_end)
        rows = []
        for offset in range(0, len(meter_ids), 1000):
            result = await self._session.execute(self._base_query().where(
                MeterReading.meter_id.in_(meter_ids[offset:offset + 1000]),
                or_(MeterReading.reading_date.between(start_low, start_high),
                    MeterReading.reading_date.between(end_low, end_high)),
            ).order_by(MeterReading.reading_date, MeterReading.id))
            rows.extend(result.scalars().all())
        return rows

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
