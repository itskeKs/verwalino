"""Vendor endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live_principal import get_live_member_db, get_live_writer_db
from app.services.person_source_access import require_person_prepare
from app.core.tenant import (
    AuthContext,
    get_current_user,
    require_write_dep,
)
from app.repositories.vendor_repo import VendorRepository
from app.models.vendor import Vendor

router = APIRouter(prefix="/api/vendors", tags=["vendors"])


class VendorOut(BaseModel):
    id: str
    person_id: str
    tax_id: str | None
    vat_id: str | None
    notes: str

    model_config = {"from_attributes": True}


class VendorCreate(BaseModel):
    person_id: str
    tax_id: str | None = None
    vat_id: str | None = None
    notes: str = ""


@router.get("", response_model=list[VendorOut])
async def list_vendors(
    person_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    repo = VendorRepository(db, auth.organization_id)
    items = await repo.list_all()
    if person_id:
        items = [i for i in items if i.person_id == person_id]
    return [_to_out(v) for v in items]


@router.post("", response_model=VendorOut, status_code=201)
async def create_vendor(
    body: VendorCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    await require_person_prepare(db, auth.organization_id, uuid.UUID(body.person_id))
    repo = VendorRepository(db, auth.organization_id)
    vendor = Vendor(
        person_id=uuid.UUID(body.person_id),
        tax_id=body.tax_id,
        vat_id=body.vat_id,
        notes=body.notes,
    )
    vendor = await repo.create(vendor)
    return _to_out(vendor)


def _to_out(v: Vendor) -> VendorOut:
    return VendorOut(
        id=str(v.id),
        person_id=str(v.person_id),
        tax_id=v.tax_id,
        vat_id=v.vat_id,
        notes=v.notes,
    )
