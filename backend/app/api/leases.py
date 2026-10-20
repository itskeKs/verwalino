"""Lease agreement endpoints -- temporal records within the current organization."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import (
    AuthContext,
    get_current_user,
    require_write_dep,
)
from app.core.live_principal import get_live_member_db, get_live_writer_db
from app.services.history_source_access import require_history_prepare
from app.services.financial_scope_service import FinancialScopeService
from app.core.temporal import (
    db_exclusive_to_api_inclusive,
    inclusive_last_day_to_db_exclusive,
)
from app.repositories.lease_agreement_repo import LeaseAgreementRepository
from app.models.lease_agreement import LeaseAgreement
from app.models.lease_rent_change import LeaseRentChange
from app.models.audit_event import AuditEvent
from app.repositories.lease_rent_change_repo import LeaseRentChangeRepository
from app.repositories.audit_repo import AuditRepository

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
    db: AsyncSession = Depends(get_live_member_db),
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
    db: AsyncSession = Depends(get_live_writer_db),
):
    repo = LeaseAgreementRepository(db, auth.organization_id)
    # API write edge (ADR-0006): the user enters the INCLUSIVE last occupied day
    # ("endet 30.06." -> 2025-06-30); storage is HALF-OPEN [valid_from, valid_to)
    # so persist the EXCLUSIVE bound (last day + 1). valid_from is already the
    # first occupied day and is stored unchanged; NULL stays open-ended. This is
    # what lets "A bis 30.06." + "B ab 01.07." coexist without a false EXCLUDE 409.
    la = LeaseAgreement(
        id=uuid.uuid4(),
        unit_id=uuid.UUID(body.unit_id),
        tenant_profile_id=uuid.UUID(body.tenant_profile_id),
        valid_from=body.valid_from,
        valid_to=inclusive_last_day_to_db_exclusive(body.valid_to),
        base_rent=Decimal(body.base_rent),
        advance_payment=Decimal(body.advance_payment),
        deposit=Decimal(body.deposit) if body.deposit else None,
        notes=body.notes,
    )
    await require_history_prepare(db, auth.organization_id, "lease_agreement", la)
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


class LeaseUpdate(BaseModel):
    valid_to: date | None = None
    notes: str | None = None
    base_rent: str | None = None
    advance_payment: str | None = None
    deposit: str | None = None


@router.patch("/{lease_id}", response_model=LeaseOut, status_code=200)
async def update_lease(
    lease_id: uuid.UUID,
    body: LeaseUpdate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    """Update lease agreement: end date, notes, rent amounts.

    Monetary changes return 409 after booking or finalization; end/notes remain editable.
    """
    repo = LeaseAgreementRepository(db, auth.organization_id)
    existing = await repo.get_by_id(lease_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Lease agreement not found")

    await require_history_prepare(db, auth.organization_id, "lease_agreement", existing)
    amount_fields = {"base_rent", "advance_payment", "deposit"}
    changes_amount = any(
        getattr(body, field) is not None
        and Decimal(getattr(body, field)) != getattr(existing, field)
        for field in amount_fields & body.model_fields_set
    )
    if changes_amount and (
        await repo.has_booked_payments(lease_id)
        or await repo.has_finalized_settlement(lease_id)
    ):
        raise HTTPException(
            status_code=409,
            detail="Cannot change amounts: has booked payments or finalized settlement",
        )
    if "valid_to" in body.model_fields_set:
        if body.valid_to is not None and body.valid_to < existing.valid_from:
            raise HTTPException(422, "Lease end must not precede its start")
        existing.valid_to = inclusive_last_day_to_db_exclusive(body.valid_to)
    if body.notes is not None:
        existing.notes = body.notes
    for field in amount_fields & body.model_fields_set:
        value = getattr(body, field)
        if value is not None:
            setattr(existing, field, Decimal(value))
    try:
        await db.flush()
    except IntegrityError as exc:
        if "excl_lease_no_overlap" in str(exc):
            raise HTTPException(409, "Overlapping lease period for this unit") from exc
        raise
    return _to_out(existing)


RentAmount = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)]


class RentChangeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid_from: date
    base_rent: RentAmount
    advance_payment: RentAmount


class RentChangeOut(BaseModel):
    id: str
    lease_agreement_id: str
    valid_from: date
    base_rent: str
    advance_payment: str
    created_at: datetime


def _rent_change_out(row: LeaseRentChange) -> RentChangeOut:
    return RentChangeOut(
        id=str(row.id), lease_agreement_id=str(row.lease_agreement_id),
        valid_from=row.valid_from, base_rent=f"{row.base_rent:.2f}",
        advance_payment=f"{row.advance_payment:.2f}", created_at=row.created_at,
    )


@router.get("/{lease_id}/rent-changes", response_model=list[RentChangeOut])
async def list_rent_changes(
    lease_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    if await LeaseAgreementRepository(db, auth.organization_id).get_by_id(lease_id) is None:
        raise HTTPException(404, "Lease agreement not found")
    rows = await LeaseRentChangeRepository(db, auth.organization_id).list_for_lease(lease_id)
    return [_rent_change_out(row) for row in rows]


@router.post("/{lease_id}/rent-changes", response_model=RentChangeOut, status_code=201)
async def create_rent_change(
    lease_id: uuid.UUID,
    body: RentChangeCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    # Keep the financial-input boundary before locking an individual contract.
    await FinancialScopeService(db, auth.organization_id, auth.user_id).lock_inputs()
    lease = await LeaseAgreementRepository(db, auth.organization_id).get_by_id_for_update(lease_id)
    if lease is None:
        raise HTTPException(404, "Lease agreement not found")
    await require_history_prepare(db, auth.organization_id, "lease_agreement", lease)
    if body.valid_from < lease.valid_from or (lease.valid_to is not None and body.valid_from >= lease.valid_to):
        raise HTTPException(422, "Rent change must be within the lease period")
    row = LeaseRentChange(
        id=uuid.uuid4(), lease_agreement_id=lease.id, valid_from=body.valid_from,
        base_rent=body.base_rent, advance_payment=body.advance_payment,
    )
    try:
        async with db.begin_nested():
            await LeaseRentChangeRepository(db, auth.organization_id).create(row)
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise HTTPException(409, "A rent change already exists for this lease and date") from exc
        raise
    await AuditRepository(db, auth.organization_id).create(AuditEvent(
        actor_user_id=auth.user_id, entity_type="lease_agreement", entity_id=lease.id,
        action="lease.rent_change_created", payload_diff={
            "rent_change_id": str(row.id), "valid_from": row.valid_from.isoformat(),
            "base_rent": f"{row.base_rent:.2f}", "advance_payment": f"{row.advance_payment:.2f}",
        },
    ))
    return _rent_change_out(row)


def _to_out(la: LeaseAgreement) -> LeaseOut:
    # API display edge (ADR-0006): storage holds the EXCLUSIVE upper bound
    # (first non-occupied day); return the INCLUSIVE last occupied day (bound - 1)
    # so a GET shows exactly what the user entered. Applies to list, detail, and
    # the tenant_at_date path (all route through here). NULL stays open-ended.
    return LeaseOut(
        id=str(la.id),
        unit_id=str(la.unit_id),
        tenant_profile_id=str(la.tenant_profile_id),
        valid_from=la.valid_from,
        valid_to=db_exclusive_to_api_inclusive(la.valid_to),
        base_rent=str(la.base_rent),
        advance_payment=str(la.advance_payment),
        deposit=str(la.deposit) if la.deposit is not None else None,
        notes=la.notes,
    )
