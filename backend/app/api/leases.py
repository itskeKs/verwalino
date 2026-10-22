"""Lease agreement endpoints -- temporal records within the current organization."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import (
    AuthContext,
    get_current_user,
    require_write_dep,
)
from fastapi.responses import Response
from app.services.master_delete_service import MasterDeleteService, master_delete_writer_db
from app.core.live_principal import get_live_member_db, get_live_writer_db
from app.services.history_source_access import require_history_prepare
from app.services.lease_rent_change_service import LeaseRentChangeService
from app.core.temporal import (
    db_exclusive_to_api_inclusive,
    inclusive_last_day_to_db_exclusive,
)
from app.repositories.lease_agreement_repo import LeaseAgreementRepository
from app.models.lease_agreement import LeaseAgreement
from app.models.lease_rent_change import LeaseRentChange
from app.repositories.lease_rent_change_repo import LeaseRentChangeRepository

router = APIRouter(prefix="/api/leases", tags=["leases"])


class StaffelmieteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    staffel_start_date: date
    staffel_increase_amount: Annotated[Decimal, Field(
        gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)] | None = None
    # Fraction: 0.05 means five percent. The generated EUR amounts are rounded per step.
    staffel_increase_percent: Annotated[Decimal, Field(
        gt=0, le=1, max_digits=7, decimal_places=6, allow_inf_nan=False)] | None = None
    staffel_interval_months: int = Field(ge=12, le=120, strict=True)
    staffel_steps: int = Field(ge=1, le=50, strict=True)

    @model_validator(mode="after")
    def exactly_one_increase(self):
        if (self.staffel_increase_amount is None) == (self.staffel_increase_percent is None):
            raise ValueError("Provide exactly one fixed or percentage increase")
        return self


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


IndexValue = Annotated[Decimal, Field(gt=0, lt=Decimal("1000000000000"),
                                      max_digits=18, decimal_places=6, allow_inf_nan=False)]


class IndexRentCalculate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    old_index: IndexValue
    new_index: IndexValue
    declaration_received_on: date


class IndexRentAdopt(IndexRentCalculate):
    expected_base_rent: RentAmount
    expected_advance_payment: RentAmount
    expected_previous_valid_from: date


class IndexRentPreview(BaseModel):
    valid_from: date
    previous_valid_from: date
    previous_base_rent: str
    base_rent: str
    advance_payment: str


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
    row = await LeaseRentChangeService(db, auth.organization_id, auth.user_id).create(
        lease_id, body.valid_from, body.advance_payment, base_rent=body.base_rent,
    )
    return _rent_change_out(row)


@router.post("/{lease_id}/rent-changes/staffel", response_model=list[RentChangeOut], status_code=201)
async def create_staffelmiete_rent_changes(
    lease_id: uuid.UUID,
    body: StaffelmieteCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    rows = await LeaseRentChangeService(db, auth.organization_id, auth.user_id).create_staffel(
        lease_id, body.staffel_start_date, body.staffel_interval_months, body.staffel_steps,
        increase_amount=body.staffel_increase_amount,
        increase_percent=body.staffel_increase_percent,
    )
    return [_rent_change_out(row) for row in rows]


@router.post("/{lease_id}/rent-changes/index/preview", response_model=IndexRentPreview)
async def preview_index_rent(
    lease_id: uuid.UUID,
    body: IndexRentCalculate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    result = await LeaseRentChangeService(db, auth.organization_id, auth.user_id).calculate_index(
        lease_id, **body.model_dump(),
    )
    return IndexRentPreview(valid_from=result.valid_from, previous_valid_from=result.previous_valid_from,
                            previous_base_rent=f"{result.previous_base_rent:.2f}",
                            base_rent=f"{result.base_rent:.2f}", advance_payment=f"{result.advance_payment:.2f}")


@router.post("/{lease_id}/rent-changes/index", response_model=RentChangeOut, status_code=201)
async def adopt_index_rent(
    lease_id: uuid.UUID,
    body: IndexRentAdopt,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    row = await LeaseRentChangeService(db, auth.organization_id, auth.user_id).adopt_index(
        lease_id, **body.model_dump(),
    )
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


@router.delete("/{lease_id}", status_code=204, response_class=Response)
async def delete_lease_agreement(
    lease_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(master_delete_writer_db),
):
    await MasterDeleteService(db, auth).delete("lease_agreement", lease_id)
    return Response(status_code=204)
