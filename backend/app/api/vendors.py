"""Vendor endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from app.repositories.person_repo import PersonRepository
from app.services.financial_scope_service import FinancialScopeService
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
    is_active: bool
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


class VendorUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tax_id: str | None = Field(default=None, max_length=20)
    vat_id: str | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=1000)
    is_active: StrictBool | None = None

    @field_validator("notes", "is_active")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Field cannot be null")
        return value


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


@router.put("/{vendor_id}", response_model=VendorOut)
async def update_vendor(
    vendor_id: uuid.UUID,
    body: VendorUpdate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    await FinancialScopeService(db, auth.organization_id, auth.user_id).lock_inputs()
    repo = VendorRepository(db, auth.organization_id)
    vendor = await repo.get_by_id_for_update(vendor_id)
    if vendor is None:
        raise HTTPException(404, "Vendor not found")
    await require_person_prepare(db, auth.organization_id, vendor.person_id)
    person = await PersonRepository(db, auth.organization_id).get_by_id(vendor.person_id)
    if person is None:
        raise HTTPException(404, "Contact not accessible")
    if person.anonymized_at is not None or person.processing_restricted or person.deletion_requested_at is not None:
        raise HTTPException(409, "Contact processing is restricted")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(vendor, key, value)
    await db.flush()
    return _to_out(vendor)


def _to_out(v: Vendor) -> VendorOut:
    return VendorOut(
        is_active=v.is_active,
        id=str(v.id),
        person_id=str(v.person_id),
        tax_id=v.tax_id,
        vat_id=v.vat_id,
        notes=v.notes,
    )
