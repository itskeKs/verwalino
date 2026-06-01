"""Lease agreement endpoints -- temporal records within the current organization."""

from __future__ import annotations

import uuid
from datetime import date
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
from app.repositories.lease_agreement_repo import LeaseAgreementRepository
from app.models.lease_agreement import LeaseAgreement

router = APIRouter(prefix="/api/leases", tags=["leases"])


class LeaseOut(BaseModel):
    id: str
    unit_id: str
    tenant_profile_id: str
    valid_from: date
    valid_to: date | None
    base_rent: str
    advance_payment: str
    deposit: str | None
    notes: str

    model_config = {"from_attributes": True}


class LeaseCreate(BaseModel):
    unit_id: str
    tenant_profile_id: str
    valid_from: date
    valid_to: date | None = None
    base_rent: str
    advance_payment: str
    deposit: str | None = None
    notes: str = ""


@router.get("", response_model=list[LeaseOut])
async def list_leases(
    unit_id: uuid.UUID | None = None,
    at_date: date | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = LeaseAgreementRepository(db, auth.organization_id)
    if unit_id and at_date:
        item = await repo.tenant_at_date(unit_id, at_date)
        return [_to_out(item)] if item else []
    elif unit_id:
        items = await repo.list_by_unit(unit_id)
    else:
        items = await repo.list_all()
    return [_to_out(la) for la in items]


@router.post("", response_model=LeaseOut, status_code=201)
async def create_lease(
    body: LeaseCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    repo = LeaseAgreementRepository(db, auth.organization_id)
    la = LeaseAgreement(
        unit_id=uuid.UUID(body.unit_id),
        tenant_profile_id=uuid.UUID(body.tenant_profile_id),
        valid_from=body.valid_from,
        valid_to=body.valid_to,
        base_rent=Decimal(body.base_rent),
        advance_payment=Decimal(body.advance_payment),
        deposit=Decimal(body.deposit) if body.deposit else None,
        notes=body.notes,
    )
    try:
        la = await repo.create(la)
    except Exception as e:
        if "excl_lease_no_overlap" in str(e):
            raise HTTPException(
                status_code=409,
                detail="Overlapping lease period for this unit",
            )
        raise
    return _to_out(la)


def _to_out(la: LeaseAgreement) -> LeaseOut:
    return LeaseOut(
        id=str(la.id),
        unit_id=str(la.unit_id),
        tenant_profile_id=str(la.tenant_profile_id),
        valid_from=la.valid_from,
        valid_to=la.valid_to,
        base_rent=str(la.base_rent),
        advance_payment=str(la.advance_payment),
        deposit=str(la.deposit) if la.deposit is not None else None,
        notes=la.notes,
    )
