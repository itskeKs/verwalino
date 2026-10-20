"""Meter and MeterReading API -- CRUD for meters and append-only readings.

T29: GET/POST/PATCH /api/meters, GET/POST /api/meters/{id}/readings.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live_principal import (
    get_live_member_db,
    require_live_principal,
)
from app.core.tenant import AuthContext, get_current_user, get_db, require_write_dep
from app.models.meter import Meter, MeterReading
from app.repositories.meter_repo import MeterReadingRepository, MeterRepository
from app.repositories.property_repo import PropertyRepository
from app.repositories.unit_repo import UnitRepository
from app.services.financial_scope_service import FinancialScopeService
from app.services.property_master_service import require_property_master

router = APIRouter()


async def meter_writer_db(
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> AsyncSession:
    await require_live_principal(db, auth, writer=True)
    await FinancialScopeService(db, auth.organization_id, auth.user_id).lock_inputs()
    return db


async def require_meter_property(db, auth, property_id, *, capability="read"):
    if await PropertyRepository(db, auth.organization_id).get_by_id(property_id) is None:
        raise HTTPException(404, "Property not found")
    await require_property_master(db, auth.organization_id, property_id, capability=capability)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class MeterCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    property_id: uuid.UUID
    unit_id: uuid.UUID | None = None
    meter_number: str
    meter_type: str
    location: str = ""

    @field_validator("meter_number")
    @classmethod
    def _check_meter_number(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("meter_number must be non-blank")
        if len(v) > 100:
            raise ValueError("meter_number exceeds 100 characters")
        return v

    @field_validator("meter_type")
    @classmethod
    def _check_meter_type(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("meter_type must be non-blank")
        if len(v) > 50:
            raise ValueError("meter_type exceeds 50 characters")
        return v

    @field_validator("location")
    @classmethod
    def _check_location(cls, v: str) -> str:
        if len(v) > 255:
            raise ValueError("location exceeds 255 characters")
        return v


class MeterPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')

    meter_number: str | None = None
    meter_type: str | None = None
    location: str | None = None

    @field_validator("meter_number")
    @classmethod
    def _check_meter_number(cls, v: str | None) -> str | None:
        if v is None:
            raise ValueError("meter_number must be non-blank")
        v = v.strip()
        if not v:
            raise ValueError("meter_number must be non-blank")
        if len(v) > 100:
            raise ValueError("meter_number exceeds 100 characters")
        return v

    @field_validator("meter_type")
    @classmethod
    def _check_meter_type(cls, v: str | None) -> str | None:
        if v is None:
            raise ValueError("meter_type must be non-blank")
        v = v.strip()
        if not v:
            raise ValueError("meter_type must be non-blank")
        if len(v) > 50:
            raise ValueError("meter_type exceeds 50 characters")
        return v

    @field_validator("location")
    @classmethod
    def _check_location(cls, v: str | None) -> str | None:
        if v is None:
            raise ValueError("location exceeds 255 characters")
        if len(v) > 255:
            raise ValueError("location exceeds 255 characters")
        return v


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


# ---------------------------------------------------------------------------
# Meters: GET/POST/PATCH /api/meters
# ---------------------------------------------------------------------------

@router.get("/api/meters")
async def list_meters(
    property_id: uuid.UUID | None = Query(None),
    unit_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_live_member_db),
    auth: AuthContext = Depends(get_current_user),
):
    if property_id is not None:
        await require_meter_property(db, auth, property_id)
    if unit_id is not None:
        unit = await UnitRepository(db, auth.organization_id).get_by_id(unit_id)
        if unit is None:
            raise HTTPException(404, "Unit not found")
        if property_id is not None and unit.property_id != property_id:
            raise HTTPException(422, "Unit does not belong to the specified property")
        await require_meter_property(db, auth, unit.property_id)
    repo = MeterRepository(db, auth.organization_id)

    if property_id and unit_id:
        meters = await repo.list_by_property_and_unit(property_id, unit_id)
    elif property_id:
        meters = await repo.list_by_property(property_id)
    elif unit_id:
        meters = await repo.list_by_unit(unit_id)
    else:
        meters = await repo.list_all()

    return [
        {
            "id": str(m.id),
            "organization_id": str(m.organization_id),
            "property_id": str(m.property_id),
            "unit_id": str(m.unit_id) if m.unit_id else None,
            "meter_number": m.meter_number,
            "meter_type": m.meter_type,
            "location": m.location,
        }
        for m in meters
    ]


@router.get("/api/meters/{meter_id}")
async def get_meter(
    meter_id: uuid.UUID,
    db: AsyncSession = Depends(get_live_member_db),
    auth: AuthContext = Depends(get_current_user),
):
    repo = MeterRepository(db, auth.organization_id)
    meter = await repo.get_by_id(meter_id)
    if not meter:
        raise HTTPException(404, "Meter not found")

    return {
        "id": str(meter.id),
        "organization_id": str(meter.organization_id),
        "property_id": str(meter.property_id),
        "unit_id": str(meter.unit_id) if meter.unit_id else None,
        "meter_number": meter.meter_number,
        "meter_type": meter.meter_type,
        "location": meter.location,
    }


@router.post("/api/meters", status_code=201)
async def create_meter(
    body: MeterCreate,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(meter_writer_db),
):
    # Check property exists and is accessible to current org
    prop_repo = PropertyRepository(db, auth.organization_id)
    prop = await prop_repo.get_by_id(body.property_id)
    if not prop:
        raise HTTPException(404, "Property not found")

    await require_property_master(
        db, auth.organization_id, body.property_id, capability="prepare"
    )

    # Check unit belongs to property if provided
    if body.unit_id:
        unit_repo = UnitRepository(db, auth.organization_id)
        unit = await unit_repo.get_by_id(body.unit_id)
        if not unit:
            raise HTTPException(404, "Unit not found")
        if unit.property_id != body.property_id:
            raise HTTPException(
                422, "Unit does not belong to the specified property"
            )

    meter = Meter(
        organization_id=auth.organization_id,
        property_id=body.property_id,
        unit_id=body.unit_id,
        meter_number=body.meter_number.strip(),
        meter_type=body.meter_type.strip(),
        location=body.location.strip() if body.location else "",
    )

    repo = MeterRepository(db, auth.organization_id)
    meter = await repo.create(meter)
    await db.flush()

    return {
        "id": str(meter.id),
        "organization_id": str(meter.organization_id),
        "property_id": str(meter.property_id),
        "unit_id": str(meter.unit_id) if meter.unit_id else None,
        "meter_number": meter.meter_number,
        "meter_type": meter.meter_type,
        "location": meter.location,
    }


@router.patch("/api/meters/{meter_id}")
async def patch_meter(
    meter_id: uuid.UUID,
    body: MeterPatch,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(meter_writer_db),
):
    repo = MeterRepository(db, auth.organization_id)
    meter = await repo.get_by_id_for_update(meter_id)
    if not meter:
        raise HTTPException(404, "Meter not found")

    # Check source access (read capability for reading, prepare for writing)
    await require_property_master(
        db, auth.organization_id, meter.property_id, capability="prepare"
    )

    # Build update dict from non-None fields
    updates = {}
    if body.meter_number is not None:
        updates["meter_number"] = body.meter_number.strip()
    if body.meter_type is not None:
        updates["meter_type"] = body.meter_type.strip()
    if body.location is not None:
        updates["location"] = body.location.strip()

    for key, value in updates.items():
        setattr(meter, key, value)

    await db.flush()

    return {
        "id": str(meter.id),
        "organization_id": str(meter.organization_id),
        "property_id": str(meter.property_id),
        "unit_id": str(meter.unit_id) if meter.unit_id else None,
        "meter_number": meter.meter_number,
        "meter_type": meter.meter_type,
        "location": meter.location,
    }


# ---------------------------------------------------------------------------
# Readings: GET/POST /api/meters/{meter_id}/readings
# ---------------------------------------------------------------------------

@router.get("/api/meters/{meter_id}/readings")
async def list_readings(
    meter_id: uuid.UUID,
    db: AsyncSession = Depends(get_live_member_db),
    auth: AuthContext = Depends(get_current_user),
):
    # Check meter exists and is accessible
    meter_repo = MeterRepository(db, auth.organization_id)
    meter = await meter_repo.get_by_id(meter_id)
    if not meter:
        raise HTTPException(404, "Meter not found")

    reading_repo = MeterReadingRepository(db, auth.organization_id)
    readings = await reading_repo.list_by_meter(meter_id)

    return [
        {
            "id": str(r.id),
            "meter_id": str(r.meter_id),
            "reading_date": r.reading_date.isoformat(),
            "value": f'{r.value:.4f}',
            "period": r.period,
        }
        for r in readings
    ]


@router.post("/api/meters/{meter_id}/readings", status_code=201)
async def create_reading(
    meter_id: uuid.UUID,
    body: ReadingCreate,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(meter_writer_db),
):
    # Check meter exists and is accessible
    meter_repo = MeterRepository(db, auth.organization_id)
    meter = await meter_repo.get_by_id_for_update(meter_id)
    if not meter:
        raise HTTPException(404, "Meter not found")

    await require_property_master(
        db, auth.organization_id, meter.property_id, capability="prepare"
    )

    reading_value = Decimal(body.value)  # validated by pydantic
    reading = MeterReading(
        organization_id=auth.organization_id,
        meter_id=meter_id,
        reading_date=body.reading_date,
        value=reading_value,
        period=body.period,
    )

    reading_repo = MeterReadingRepository(db, auth.organization_id)
    reading = await reading_repo.create(reading)
    await db.flush()

    return {
        "id": str(reading.id),
        "meter_id": str(reading.meter_id),
        "reading_date": reading.reading_date.isoformat(),
        "value": f'{reading.value:.4f}',
        "period": reading.period,
    }
