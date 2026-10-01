"""Property endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import (
    AuthContext,
    get_current_user,
    get_db,
    require_write_dep,
)
from app.repositories.property_repo import PropertyRepository
from app.repositories.unit_repo import UnitRepository
from app.repositories.audit_repo import AuditRepository
from app.models.property import Property
from app.models.audit_event import AuditEvent

from app.repositories.pagination import Page, PageQuery, page_query, page_rows

router = APIRouter(prefix="/api/properties", tags=["properties"])


class PropertyOut(BaseModel):
    id: str
    name: str
    street: str
    zip_code: str
    city: str
    management_type: str
    fiscal_year_start_month: int
    mea_denominator: int | None
    mea_scale_status: str

    model_config = {"from_attributes": True}


class PropertyCreate(BaseModel):
    name: str
    street: str = ""
    zip_code: str = ""
    city: str = ""
    management_type: str
    fiscal_year_start_month: int = 1
    mea_denominator: int | None = Field(default=None, ge=1, le=1_000_000_000_000)


class PropertyUpdate(BaseModel):
    name: str | None = None
    street: str | None = None
    zip_code: str | None = None
    city: str | None = None
    management_type: str | None = None
    fiscal_year_start_month: int | None = None


class MeaScaleConfirmation(BaseModel):
    denominator: int = Field(ge=1, le=1_000_000_000_000)
    expected_scale_status: str
    confirm_existing_values_as_numerators: Literal[True]


@router.get("", response_model=list[PropertyOut])
async def list_properties(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = PropertyRepository(db, auth.organization_id)
    items = await repo.list_all()
    return [_to_out(p) for p in items]


@router.get("/page", response_model=Page[PropertyOut])
async def page_properties(
    query: PageQuery = Depends(page_query),
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, cursor = await page_rows(db, auth, Property, Property.name, [Property.name, Property.street, Property.city], query)
    return Page(items=[_to_out(item) for item in items], next_cursor=cursor)


@router.get("/{property_id}", response_model=PropertyOut)
async def get_property(
    property_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = PropertyRepository(db, auth.organization_id)
    p = await repo.get_by_id(property_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return _to_out(p)


@router.post("", response_model=PropertyOut, status_code=201)
async def create_property(
    body: PropertyCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    _validate_management_type(body.management_type)
    repo = PropertyRepository(db, auth.organization_id)
    prop = Property(
        name=body.name,
        street=body.street,
        zip_code=body.zip_code,
        city=body.city,
        management_type=body.management_type,
        fiscal_year_start_month=body.fiscal_year_start_month,
        mea_denominator=body.mea_denominator,
        mea_scale_status=("CONFIRMED" if body.mea_denominator is not None else "LEGACY_UNCONFIRMED"),
    )
    prop = await repo.create(prop)
    return _to_out(prop)


@router.put("/{property_id}", response_model=PropertyOut)
async def update_property(
    property_id: uuid.UUID,
    body: PropertyUpdate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    repo = PropertyRepository(db, auth.organization_id)
    prop = await repo.get_by_id(property_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")

    if body.management_type is not None:
        _validate_management_type(body.management_type)

    for field in ("name", "street", "zip_code", "city", "management_type", "fiscal_year_start_month"):
        val = getattr(body, field)
        if val is not None:
            setattr(prop, field, val)

    await db.flush()
    return _to_out(prop)


@router.post("/{property_id}/mea-scale/confirm", response_model=PropertyOut)
async def confirm_mea_scale(
    property_id: uuid.UUID,
    body: MeaScaleConfirmation,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    property_repo = PropertyRepository(db, auth.organization_id)
    prop = await property_repo.get_by_id_for_update(property_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    if (
        prop.mea_scale_status != "LEGACY_UNCONFIRMED"
        or body.expected_scale_status != prop.mea_scale_status
    ):
        raise HTTPException(status_code=409, detail="MEA_SCALE_STATUS_CHANGED")

    units = await UnitRepository(db, auth.organization_id).list_by_property_for_update(
        property_id
    )
    numerator_sum = Decimal(0)
    denominator = Decimal(body.denominator)
    for unit in units:
        if unit.mea is None:
            continue
        mea = unit.mea
        if not mea.is_finite() or mea < 0 or mea != mea.to_integral_value():
            raise HTTPException(status_code=409, detail="LEGACY_MEA_NOT_INTEGER")
        if mea > denominator:
            raise HTTPException(
                status_code=409, detail="LEGACY_MEA_EXCEEDS_DENOMINATOR"
            )
        numerator_sum += mea

    if numerator_sum > denominator:
        raise HTTPException(status_code=409, detail="LEGACY_MEA_EXCEEDS_DENOMINATOR")

    old_scale = {
        "mea_scale_status": prop.mea_scale_status,
        "mea_denominator": prop.mea_denominator,
    }
    prop.mea_scale_status = "CONFIRMED"
    prop.mea_denominator = body.denominator
    await AuditRepository(db, auth.organization_id).create(
        AuditEvent(
            actor_user_id=auth.user_id,
            entity_type="property",
            entity_id=prop.id,
            action="property.mea_scale_confirmed",
            payload_diff={
                "old_scale": old_scale,
                "new_scale": {
                    "mea_scale_status": prop.mea_scale_status,
                    "mea_denominator": prop.mea_denominator,
                },
                "existing_values_confirmed_as_numerators": True,
            },
        )
    )
    return _to_out(prop)


def _to_out(p: Property) -> PropertyOut:
    return PropertyOut(
        id=str(p.id),
        name=p.name,
        street=p.street,
        zip_code=p.zip_code,
        city=p.city,
        management_type=p.management_type,
        fiscal_year_start_month=p.fiscal_year_start_month,
        mea_denominator=p.mea_denominator,
        mea_scale_status=p.mea_scale_status,
    )


def _validate_management_type(mt: str) -> None:
    if mt not in ("MIET", "WEG", "SEV", "MISCH"):
        raise HTTPException(status_code=422, detail="Invalid management_type")
