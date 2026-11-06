"""Unit endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import Response
from app.services.master_delete_service import MasterDeleteService, master_delete_writer_db
from app.core.live_principal import get_live_member_db, get_live_writer_db
from app.services.property_master_service import require_property_master

from app.core.tenant import (
    AuthContext,
    get_current_user,
    require_write_dep,
)
from app.repositories.unit_repo import UnitRepository
from app.models.unit import UNIT_NUMBER_MAX_LENGTH, Unit
from app.services.unit_household_service import UnitHouseholdService

from app.repositories.pagination import Page, PageQuery, page_query, page_rows

from app.services.master_creation_service import (
    UnitCreate, create_unit as create_unit_record, _validate_usage_type,
    _parse_mea_numerator, _require_confirmed_mea_scale, _validate_mea_total,
    lock_unit_property, parse_unit_area, require_available_unit_number,
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

    def validate_number_length(self) -> None:
        if self.number is not None and len(self.number) > UNIT_NUMBER_MAX_LENGTH:
            raise HTTPException(422, detail={
                "code": "unit_number_too_long",
                "message": f"Die Bezeichnung darf höchstens {UNIT_NUMBER_MAX_LENGTH} Zeichen lang sein.",
            })


class HouseholdRow(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra='forbid')
    valid_from: date
    persons: int = Field(strict=True, ge=0, le=2147483647)
    is_vacancy: bool = Field(strict=True)


@router.get('/{unit_id}/household', response_model=list[HouseholdRow])
async def get_household(unit_id: uuid.UUID, auth: AuthContext = Depends(get_current_user),
                        db: AsyncSession = Depends(get_live_member_db)):
    return await UnitHouseholdService(db, auth).get(unit_id)


@router.put('/{unit_id}/household', response_model=list[HouseholdRow])
async def replace_household(unit_id: uuid.UUID, body: list[HouseholdRow],
                            auth: AuthContext = Depends(require_write_dep),
                            db: AsyncSession = Depends(get_live_writer_db)):
    return await UnitHouseholdService(db, auth).replace(unit_id, body)


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
    # A foreign unit must still return 404, even for an overlong number.
    body.validate_number_length()

    if body.usage_type is not None:
        _validate_usage_type(body.usage_type)

    mea = _parse_mea_numerator(body.mea) if body.mea is not None else None
    if body.number is not None or mea is not None:
        prop = await lock_unit_property(db, auth.organization_id, unit.property_id)
        if mea is not None:
            _require_confirmed_mea_scale(prop)
        unit = await repo.get_by_id_for_update(unit_id)
        if unit is None or unit.property_id != prop.id:
            raise HTTPException(status_code=404, detail="Unit not found")
        if mea is not None:
            units = await repo.list_by_property_for_update(prop.id)
            _validate_mea_total(mea, prop, units, replacing_unit=unit)
        # An unchanged number must not prevent editing a legacy duplicate's other fields.
        if body.number is not None and body.number != unit.number:
            await require_available_unit_number(repo, prop.id, body.number, exclude_unit_id=unit_id)

    area = parse_unit_area(body.area, "area") if body.area is not None else None
    heated_area = parse_unit_area(body.heated_area, "heated_area") if body.heated_area is not None else None
    if body.number is not None:
        unit.number = body.number
    if body.location is not None:
        unit.location = body.location
    if body.usage_type is not None:
        unit.usage_type = body.usage_type
    if area is not None:
        unit.area = area
    if heated_area is not None:
        unit.heated_area = heated_area
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


@router.delete("/{unit_id}", status_code=204, response_class=Response)
async def delete_unit(
    unit_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(master_delete_writer_db),
):
    await MasterDeleteService(db, auth).delete("unit", unit_id)
    return Response(status_code=204)
