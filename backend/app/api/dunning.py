"""Dunning (Mahnwesen) API endpoints -- M7.

RBAC (like M0-M6): GET for all roles; POST/issue/DELETE require a write role.
Foreign ID -> 404 (not 403). An issued dunning notice is never hard-deleted
(409); the DB trigger is the final guard. PDF only for issued notices.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, StrictStr, field_validator, model_validator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.live_principal import get_live_writer_db

from app.core.tenant import AuthContext, get_current_user, get_db, require_write_dep
from app.models.dunning_notice import DunningNotice
from app.repositories.dunning_notice_repo import DunningNoticeRepository
from app.services.dunning_pdf import render_dunning_pdf
from app.services.dunning_service import DunningService
from app.services.dunning_batch_service import DunningBatchService
from app.services.dunning_scope_service import DunningScopeService
from app.core.rbac import ScopeCapability

router = APIRouter(prefix="/api/dunning", tags=["dunning"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class DunningInterestInput(BaseModel):
    base_interest_rate: StrictStr | None = None

    @field_validator("base_interest_rate")
    @classmethod
    def validate_base_rate(cls, value):
        if value is not None:
            rate = _dec(value, "base_interest_rate")
            if len(rate.as_tuple().digits) > 100 or abs(rate.as_tuple().exponent) > 100:
                raise ValueError("Base interest rate exceeds supported precision")
        return value

    def resolved_base_rate(self) -> Decimal | None:
        if "base_interest_rate" in self.model_fields_set:
            return _dec(self.base_interest_rate, "base_interest_rate") if self.base_interest_rate is not None else None
        return settings.dunning_base_interest_rate


class DunningCreateTenant(DunningInterestInput):
    accounting_scope_id: uuid.UUID | None = None
    tenant_profile_id: str
    lease_agreement_id: str
    period_start: date
    period_end: date
    reference_date: date | None = None
    grace_days: int = 0
    override_fee: str | None = None


class DunningCreateOwner(DunningInterestInput):
    accounting_scope_id: uuid.UUID | None = None
    owner_profile_id: str
    period_start: date
    period_end: date
    economic_plan_id: str | None = None
    reference_date: date | None = None
    grace_days: int = 0
    override_fee: str | None = None


class DunningBatchPreview(DunningInterestInput):
    model_config = {"extra": "forbid"}

    period_start: date
    period_end: date
    reference_date: date
    threshold: StrictStr
    grace_days: int = Field(default=0, strict=True, ge=0, le=365)
    override_fee: StrictStr | None = None
    accounting_scope_id: uuid.UUID | None = None

    @field_validator("threshold", "override_fee")
    @classmethod
    def validate_money(cls, value):
        if value is not None and not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,2})?", value):
            raise ValueError("Amount must be a nonnegative plain decimal within Numeric(14,2)")
        return value

    @model_validator(mode="after")
    def validate_period(self):
        if self.period_start > self.period_end:
            raise ValueError("Period must not end before it starts")
        return self


class DunningBatchApply(DunningBatchPreview):
    lease_agreement_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    expected_source_digest: StrictStr = Field(pattern=r"^[0-9a-fA-F]{64}$")
    request_id: uuid.UUID

    @field_validator("lease_agreement_ids")
    @classmethod
    def unique_leases(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Lease agreement IDs must be unique")
        return value

    @field_validator("expected_source_digest")
    @classmethod
    def normalize_digest(cls, value):
        return value.lower()


class DunningBatchCandidate(BaseModel):
    lease_agreement_id: str
    tenant_profile_id: str
    person_name: str
    unit_number: str
    property_name: str
    principal_amount: str
    fee_amount: str
    interest_amount: str
    total_amount: str
    level: int


class DunningBatchPreviewOut(BaseModel):
    candidates: list[DunningBatchCandidate]
    source_digest: str


class DunningOut(BaseModel):
    accounting_scope_id: str | None = None
    id: str
    organization_id: str
    tenant_profile_id: str | None
    owner_profile_id: str | None
    level: int
    issue_date: date
    due_date: date
    principal_amount: str
    fee_amount: str
    total_amount: str
    interest_amount: str
    interest_snapshot: dict | None
    included_items: list[dict] | None
    status: str
    issued_at: str | None
    issued_by: str | None
    note: str | None

    model_config = {"from_attributes": True}


def _to_out(n: DunningNotice) -> DunningOut:
    return DunningOut(
        id=str(n.id),
        accounting_scope_id=str(n.accounting_scope_id) if n.accounting_scope_id else None,
        organization_id=str(n.organization_id),
        tenant_profile_id=str(n.tenant_profile_id) if n.tenant_profile_id else None,
        owner_profile_id=str(n.owner_profile_id) if n.owner_profile_id else None,
        level=n.level,
        issue_date=n.issue_date,
        due_date=n.due_date,
        principal_amount=str(n.principal_amount),
        fee_amount=str(n.fee_amount),
        total_amount=str(n.total_amount),
        interest_amount=str(n.interest_amount),
        interest_snapshot=n.interest_snapshot,
        included_items=n.included_items,
        status="cancelled" if n.cancelled_at else n.status,
        issued_at=str(n.issued_at) if n.issued_at else None,
        issued_by=str(n.issued_by) if n.issued_by else None,
        note=n.note,
    )


def _dec(value: str, field: str) -> Decimal:
    try:
        result = Decimal(value)
        if not result.is_finite():
            raise InvalidOperation
        return result
    except (InvalidOperation, TypeError):
        raise HTTPException(status_code=422, detail=f"{field} is not a valid decimal: {value!r}")


# ---------------------------------------------------------------------------
# List / detail (GET -- all roles)
# ---------------------------------------------------------------------------
@router.get("", response_model=list[DunningOut])
async def list_dunning_notices(
    tenant_profile_id: uuid.UUID | None = None,
    owner_profile_id: uuid.UUID | None = None,
    level: int | None = None,
    status: str | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = DunningNoticeRepository(db, auth.organization_id)
    any_filter = any(
        v is not None
        for v in (tenant_profile_id, owner_profile_id, level, status)
    )
    if any_filter:
        items = await repo.list_by_filters(
            tenant_profile_id=tenant_profile_id,
            owner_profile_id=owner_profile_id,
            level=level,
            status=status,
        )
    else:
        items = await repo.list_all_ordered()
    return [_to_out(n) for n in items]


@router.get("/settings")
async def dunning_settings(auth: AuthContext = Depends(get_current_user)):
    rate = settings.dunning_base_interest_rate
    return {"base_interest_rate": str(rate) if rate is not None else None}


@router.post("/batch/preview", response_model=DunningBatchPreviewOut)
async def preview_dunning_batch(
    body: DunningBatchPreview,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db, scope="function"),
):
    return await DunningBatchService(db, auth.organization_id, auth.user_id).preview(body)


@router.post("/batch", response_model=list[DunningOut], status_code=201)
async def create_dunning_batch(
    body: DunningBatchApply,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db, scope="function"),
):
    notices = await DunningBatchService(db, auth.organization_id, auth.user_id).apply(body)
    return [_to_out(notice) for notice in notices]


@router.get("/{dunning_id}", response_model=DunningOut)
async def get_dunning_notice(
    dunning_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = DunningNoticeRepository(db, auth.organization_id)
    notice = await repo.get_by_id(dunning_id)
    if notice is None:
        raise HTTPException(status_code=404, detail="Dunning notice not found")
    return _to_out(notice)


# ---------------------------------------------------------------------------
# Create (POST -- write role)
# ---------------------------------------------------------------------------
@router.post("/tenant", response_model=DunningOut, status_code=201)
async def create_dunning_for_tenant(
    body: DunningCreateTenant,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = DunningService(db, auth.organization_id, auth.user_id)
    notice = await svc.create_dunning_for_tenant(
        accounting_scope_id=body.accounting_scope_id,
        tenant_profile_id=uuid.UUID(body.tenant_profile_id),
        lease_agreement_id=uuid.UUID(body.lease_agreement_id),
        period_start=body.period_start,
        period_end=body.period_end,
        reference_date=body.reference_date,
        grace_days=body.grace_days,
        base_interest_rate=body.resolved_base_rate(),
        override_fee=_dec(body.override_fee, "override_fee") if body.override_fee else None,
    )
    return _to_out(notice)


@router.post("/owner", response_model=DunningOut, status_code=201)
async def create_dunning_for_owner(
    body: DunningCreateOwner,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = DunningService(db, auth.organization_id, auth.user_id)
    notice = await svc.create_dunning_for_owner(
        accounting_scope_id=body.accounting_scope_id,
        owner_profile_id=uuid.UUID(body.owner_profile_id),
        period_start=body.period_start,
        period_end=body.period_end,
        economic_plan_id=uuid.UUID(body.economic_plan_id) if body.economic_plan_id else None,
        reference_date=body.reference_date,
        grace_days=body.grace_days,
        base_interest_rate=body.resolved_base_rate(),
        override_fee=_dec(body.override_fee, "override_fee") if body.override_fee else None,
    )
    return _to_out(notice)


# ---------------------------------------------------------------------------
# Issue (POST -- write role)
# ---------------------------------------------------------------------------
@router.post("/{dunning_id}/issue", response_model=DunningOut)
async def issue_dunning_notice(
    dunning_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = DunningService(db, auth.organization_id, auth.user_id)
    notice = await svc.issue(dunning_id)
    return _to_out(notice)


# ---------------------------------------------------------------------------
# Delete draft (DELETE -- write role)
# ---------------------------------------------------------------------------
@router.delete("/{dunning_id}", status_code=204)
async def delete_dunning_notice(
    dunning_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = DunningService(db, auth.organization_id, auth.user_id)
    await svc.delete_draft(dunning_id)


# ---------------------------------------------------------------------------
# PDF (GET -- all roles, but only for issued notices)
# ---------------------------------------------------------------------------
@router.get("/{dunning_id}/pdf")
async def dunning_pdf(
    dunning_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = DunningService(db, auth.organization_id, auth.user_id)
    repo = DunningNoticeRepository(db, auth.organization_id)
    notice = await repo.get_by_id(dunning_id)
    if notice is None:
        raise HTTPException(status_code=404, detail="Dunning notice not found")
    if notice.status != "issued":
        raise HTTPException(
            status_code=409,
            detail="PDF is only available for issued dunning notices",
        )

    await DunningScopeService(db, auth.organization_id, auth.user_id).enter(notice, ScopeCapability.EXPORT)
    # Anzeige-Namen: eingefroren aus der ausgestellten Mahnung, sonst live
    # (Altbestand ohne eingefrorene Namen) -- s. DunningService / ADR-0012.
    names = await svc.render_display_names(notice)
    pdf_bytes = render_dunning_pdf(notice, names)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="mahnung_{dunning_id}.pdf"'
        },
    )
