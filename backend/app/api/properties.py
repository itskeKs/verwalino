"""Property endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import (
    AuthContext,
    get_current_user,
    get_db,
    require_write_dep,
)
from app.repositories.property_repo import PropertyRepository
from app.models.property import Property

router = APIRouter(prefix="/api/properties", tags=["properties"])


class PropertyOut(BaseModel):
    id: str
    name: str
    street: str
    zip_code: str
    city: str
    management_type: str
    fiscal_year_start_month: int

    model_config = {"from_attributes": True}


class PropertyCreate(BaseModel):
    name: str
    street: str = ""
    zip_code: str = ""
    city: str = ""
    management_type: str
    fiscal_year_start_month: int = 1


class PropertyUpdate(BaseModel):
    name: str | None = None
    street: str | None = None
    zip_code: str | None = None
    city: str | None = None
    management_type: str | None = None
    fiscal_year_start_month: int | None = None


@router.get("", response_model=list[PropertyOut])
async def list_properties(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = PropertyRepository(db, auth.organization_id)
    items = await repo.list_all()
    return [_to_out(p) for p in items]


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


def _to_out(p: Property) -> PropertyOut:
    return PropertyOut(
        id=str(p.id),
        name=p.name,
        street=p.street,
        zip_code=p.zip_code,
        city=p.city,
        management_type=p.management_type,
        fiscal_year_start_month=p.fiscal_year_start_month,
    )


def _validate_management_type(mt: str) -> None:
    if mt not in ("MIET", "WEG", "SEV", "MISCH"):
        raise HTTPException(status_code=422, detail="Invalid management_type")
