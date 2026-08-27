"""Dunning (Mahnwesen) API endpoints -- M7.

RBAC (like M0-M6): GET for all roles; POST/issue/DELETE require a write role.
Foreign ID -> 404 (not 403). An issued dunning notice is never hard-deleted
(409); the DB trigger is the final guard. PDF only for issued notices.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db, require_write_dep
from app.models.dunning_notice import DunningNotice
from app.repositories.dunning_notice_repo import DunningNoticeRepository
from app.services.dunning_pdf import render_dunning_pdf
from app.services.dunning_service import DunningService

router = APIRouter(prefix="/api/dunning", tags=["dunning"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class DunningCreateTenant(BaseModel):
    tenant_profile_id: str
    lease_agreement_id: str
    period_start: date
    period_end: date
    reference_date: date | None = None
    grace_days: int = 0
    override_fee: str | None = None


class DunningCreateOwner(BaseModel):
    owner_profile_id: str
    period_start: date
    period_end: date
    economic_plan_id: str | None = None
    reference_date: date | None = None
    grace_days: int = 0
    override_fee: str | None = None


class DunningOut(BaseModel):
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
    included_items: list[dict] | None
    status: str
    issued_at: str | None
    issued_by: str | None
    note: str | None

    model_config = {"from_attributes": True}


def _to_out(n: DunningNotice) -> DunningOut:
    return DunningOut(
        id=str(n.id),
        organization_id=str(n.organization_id),
        tenant_profile_id=str(n.tenant_profile_id) if n.tenant_profile_id else None,
        owner_profile_id=str(n.owner_profile_id) if n.owner_profile_id else None,
        level=n.level,
        issue_date=n.issue_date,
        due_date=n.due_date,
        principal_amount=str(n.principal_amount),
        fee_amount=str(n.fee_amount),
        total_amount=str(n.total_amount),
        included_items=n.included_items,
        status=n.status,
        issued_at=str(n.issued_at) if n.issued_at else None,
        issued_by=str(n.issued_by) if n.issued_by else None,
        note=n.note,
    )


def _dec(value: str, field: str) -> Decimal:
    try:
        return Decimal(value)
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
        tenant_profile_id=uuid.UUID(body.tenant_profile_id),
        lease_agreement_id=uuid.UUID(body.lease_agreement_id),
        period_start=body.period_start,
        period_end=body.period_end,
        reference_date=body.reference_date,
        grace_days=body.grace_days,
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
        owner_profile_id=uuid.UUID(body.owner_profile_id),
        period_start=body.period_start,
        period_end=body.period_end,
        economic_plan_id=uuid.UUID(body.economic_plan_id) if body.economic_plan_id else None,
        reference_date=body.reference_date,
        grace_days=body.grace_days,
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
