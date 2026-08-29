"""Unit endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import (
    AuthContext,
    get_current_user,
    get_db,
    require_write_dep,
)
from app.repositories.unit_repo import UnitRepository
from app.models.unit import Unit
from app.models.property import Property
from app.repositories.property_repo import PropertyRepository

router = APIRouter(prefix="/api/units", tags=["units"])


class UnitOut(BaseModel):
    id: str
    property_id: str
    number: str
    location: str
    area: str | None
    heated_area: str | None
    usage_type: str
    mea: str | None
    building_part: str

    model_config = {"from_attributes": True}


class UnitCreate(BaseModel):
    property_id: str
    number: str
    location: str = ""
    area: str | None = None
    heated_area: str | None = None
    usage_type: str
    mea: str | None = None
    building_part: str = ""


class UnitUpdate(BaseModel):
    number: str | None = None
    location: str | None = None
    area: str | None = None
    heated_area: str | None = None
    usage_type: str | None = None
    mea: str | None = None
    building_part: str | None = None


@router.get("", response_model=list[UnitOut])
async def list_units(
    property_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = UnitRepository(db, auth.organization_id)
    if property_id:
        items = await repo.list_by_property(property_id)
    else:
        items = await repo.list_all()
    return [_to_out(u) for u in items]


@router.get("/{unit_id}", response_model=UnitOut)
async def get_unit(
    unit_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = UnitRepository(db, auth.organization_id)
    u = await repo.get_by_id(unit_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Unit not found")
    return _to_out(u)


@router.post("", response_model=UnitOut, status_code=201)
async def create_unit(
    body: UnitCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    _validate_usage_type(body.usage_type)
    repo = UnitRepository(db, auth.organization_id)
    mea = _parse_mea_numerator(body.mea) if body.mea is not None else None
    if mea is not None:
        prop = await _require_confirmed_mea_scale(
            db, auth.organization_id, uuid.UUID(body.property_id)
        )
        units = await repo.list_by_property_for_update(prop.id)
        _validate_mea_total(mea, prop, units)
    unit = Unit(
        property_id=uuid.UUID(body.property_id),
        number=body.number,
        location=body.location,
        area=Decimal(body.area) if body.area else None,
        heated_area=Decimal(body.heated_area) if body.heated_area else None,
        usage_type=body.usage_type,
        mea=mea,
        building_part=body.building_part,
    )
    unit = await repo.create(unit)
    return _to_out(unit)


@router.put("/{unit_id}", response_model=UnitOut)
async def update_unit(
    unit_id: uuid.UUID,
    body: UnitUpdate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    repo = UnitRepository(db, auth.organization_id)
    unit = await repo.get_by_id(unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    if body.usage_type is not None:
        _validate_usage_type(body.usage_type)

    mea = _parse_mea_numerator(body.mea) if body.mea is not None else None
    if mea is not None:
        prop = await _require_confirmed_mea_scale(db, auth.organization_id, unit.property_id)
        unit = await repo.get_by_id_for_update(unit_id)
        if unit is None or unit.property_id != prop.id:
            raise HTTPException(status_code=404, detail="Unit not found")
        units = await repo.list_by_property_for_update(prop.id)
        _validate_mea_total(mea, prop, units, replacing_unit=unit)

    if body.number is not None:
        unit.number = body.number
    if body.location is not None:
        unit.location = body.location
    if body.usage_type is not None:
        unit.usage_type = body.usage_type
    if body.area is not None:
        unit.area = Decimal(body.area)
    if body.heated_area is not None:
        unit.heated_area = Decimal(body.heated_area)
    if mea is not None:
        unit.mea = mea
    if body.building_part is not None:
        unit.building_part = body.building_part

    await db.flush()
    return _to_out(unit)


def _to_out(u: Unit) -> UnitOut:
    return UnitOut(
        id=str(u.id),
        property_id=str(u.property_id),
        number=u.number,
        location=u.location,
        area=str(u.area) if u.area is not None else None,
        heated_area=str(u.heated_area) if u.heated_area is not None else None,
        usage_type=u.usage_type,
        mea=str(u.mea) if u.mea is not None else None,
        building_part=u.building_part,
    )


def _validate_usage_type(ut: str) -> None:
    if ut not in ("WOHNUNG", "GEWERBE", "STELLPLATZ", "SONSTIGE"):
        raise HTTPException(status_code=422, detail="Invalid usage_type")


def _parse_mea_numerator(value: str) -> Decimal:
    try:
        mea = Decimal(value)
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=422, detail="MEA_MUST_BE_INTEGER") from None
    if not mea.is_finite() or mea != mea.to_integral_value():
        raise HTTPException(status_code=422, detail="MEA_MUST_BE_INTEGER")
    if mea < 0:
        raise HTTPException(status_code=422, detail="MEA_MUST_BE_NONNEGATIVE")
    return mea


async def _require_confirmed_mea_scale(
    db: AsyncSession,
    org_id: uuid.UUID,
    property_id: uuid.UUID,
) -> Property:
    property_repo = PropertyRepository(db, org_id)
    prop = await property_repo.get_by_id_for_update(property_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    if prop.mea_scale_status != "CONFIRMED" or prop.mea_denominator is None:
        raise HTTPException(
            status_code=409,
            detail="Property MEA scale must be confirmed before writing MEA values",
        )
    return prop


def _validate_mea_total(
    proposed: Decimal,
    prop: Property,
    units: list[Unit] | tuple[Unit, ...],
    *,
    replacing_unit: Unit | None = None,
) -> None:
    denominator = Decimal(prop.mea_denominator)
    if proposed > denominator:
        raise HTTPException(status_code=422, detail="MEA_EXCEEDS_DENOMINATOR")
    total = sum((unit.mea or Decimal(0) for unit in units), Decimal(0))
    if replacing_unit is not None and replacing_unit.mea is not None:
        total -= replacing_unit.mea
    if total + proposed > denominator:
        raise HTTPException(status_code=409, detail="MEA_TOTAL_EXCEEDS_DENOMINATOR")
