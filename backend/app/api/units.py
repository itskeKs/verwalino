"""Unit endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.live_principal import get_live_member_db, get_live_writer_db
from app.services.property_master_service import require_property_master

from app.core.tenant import (
    AuthContext,
    get_current_user,
    require_write_dep,
)
from app.repositories.unit_repo import UnitRepository
from app.models.unit import Unit

from app.repositories.pagination import Page, PageQuery, page_query, page_rows

from app.services.master_creation_service import (
    UnitCreate, create_unit as create_unit_record, _validate_usage_type,
    _parse_mea_numerator, _require_confirmed_mea_scale, _validate_mea_total,
)

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
    db: AsyncSession = Depends(get_live_member_db),
):
    repo = UnitRepository(db, auth.organization_id)
    if property_id:
        items = await repo.list_by_property(property_id)
    else:
        items = await repo.list_all()
    return [_to_out(u) for u in items]


@router.get("/page", response_model=Page[UnitOut])
async def page_units(
    property_id: uuid.UUID | None = None,
    query: PageQuery = Depends(page_query),
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    items, cursor = await page_rows(db, auth, Unit, Unit.number, [Unit.number, Unit.location, Unit.building_part], query, filters=([Unit.property_id == property_id] if property_id else []), context=str(property_id or ""))
    return Page(items=[_to_out(item) for item in items], next_cursor=cursor)


@router.get("/{unit_id}", response_model=UnitOut)
async def get_unit(
    unit_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
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
    db: AsyncSession = Depends(get_live_writer_db),
):
    return _to_out(await create_unit_record(db, auth, body))


@router.put("/{unit_id}", response_model=UnitOut)
async def update_unit(
    unit_id: uuid.UUID,
    body: UnitUpdate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    repo = UnitRepository(db, auth.organization_id)
    unit = await repo.get_by_id(unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    await require_property_master(db, auth.organization_id, unit.property_id)

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
