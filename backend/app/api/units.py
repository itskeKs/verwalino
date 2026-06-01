"""Unit endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid
from decimal import Decimal

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

    model_config = {"from_attributes": True}


class UnitCreate(BaseModel):
    property_id: str
    number: str
    location: str = ""
    area: str | None = None
    heated_area: str | None = None
    usage_type: str
    mea: str | None = None


class UnitUpdate(BaseModel):
    number: str | None = None
    location: str | None = None
    area: str | None = None
    heated_area: str | None = None
    usage_type: str | None = None
    mea: str | None = None


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
    unit = Unit(
        property_id=uuid.UUID(body.property_id),
        number=body.number,
        location=body.location,
        area=Decimal(body.area) if body.area else None,
        heated_area=Decimal(body.heated_area) if body.heated_area else None,
        usage_type=body.usage_type,
        mea=Decimal(body.mea) if body.mea else None,
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
    if body.mea is not None:
        unit.mea = Decimal(body.mea)

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
    )


def _validate_usage_type(ut: str) -> None:
    if ut not in ("WOHNUNG", "GEWERBE", "STELLPLATZ", "SONSTIGE"):
        raise HTTPException(status_code=422, detail="Invalid usage_type")
