"""Shared validation and append-only meter reading creation."""
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, field_validator

from app.models.meter import MeterReading
from app.repositories.meter_repo import MeterRepository, MeterReadingRepository
from app.services.property_master_service import require_property_master


class ReadingCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    reading_date: date
    value: str  # decimal string
    period: str | None = None

    @field_validator("value")
    @classmethod
    def _check_value(cls, v: str) -> str:
        try:
            dec = Decimal(v)
        except (InvalidOperation, ValueError):
            raise ValueError("value must be a valid decimal string")

        if not dec.is_finite():
            raise ValueError("value must be finite")
        if dec < 0:
            raise ValueError("value must be non-negative")

        # Max 14 total digits, 4 decimal places => max 10 integer digits
        sign, digits, exponent = dec.as_tuple()
        if exponent < 0:
            decimal_places = abs(exponent)
        else:
            decimal_places = 0

        if decimal_places > 4:
            raise ValueError("value exceeds 4 decimal places")

        if exponent >= 0:
            int_digits = len(digits) + exponent
        else:
            int_digits = len(digits) + exponent  # exponent is negative

        if int_digits > 10:
            raise ValueError("value exceeds 10 integer digits (max 14 total, 4 decimal)")
        return v

    @field_validator("reading_date")
    @classmethod
    def _check_date(cls, v: date) -> date:
        return v

    @field_validator("period")
    @classmethod
    def _check_period(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 50:
            raise ValueError("period exceeds 50 characters")
        return v



def reading_out(reading):
    return {'id': str(reading.id), 'meter_id': str(reading.meter_id),
            'reading_date': reading.reading_date.isoformat(), 'value': f'{reading.value:.4f}',
            'period': reading.period}


class MeterReadingService:
    def __init__(self, db, auth):
        self.db, self.auth, self.org = db, auth, auth.organization_id

    async def record(self, meter_id, body):
        meter = await MeterRepository(self.db, self.org).get_by_id_for_update(meter_id)
        if meter is None:
            raise HTTPException(404, 'Zähler nicht zugänglich')
        await require_property_master(self.db, self.org, meter.property_id, capability='prepare')
        repo = MeterReadingRepository(self.db, self.org)
        preceding = await repo.preceding_value(meter_id, body.reading_date)
        value = Decimal(body.value)
        if preceding is not None and value < preceding:
            raise HTTPException(422, 'Zählerstand darf nicht kleiner als die vorherige Ablesung sein.')
        reading = await repo.create(MeterReading(organization_id=self.org, meter_id=meter_id,
            reading_date=body.reading_date, value=value, period=body.period))
        return reading_out(reading)
