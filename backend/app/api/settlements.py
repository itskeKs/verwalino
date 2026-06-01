"""Settlement endpoints -- create / compute / preview / finalize / correction.

RBAC: GET for all roles; POST/PATCH/finalize/correction require write role.
Foreign ID -> 404 (not 403), consistent with M0/M2.
No DELETE endpoint for finalized runs (ADR-0003); draft discard is out of the
M3 API surface for now (recompute replaces lines).

PDF is served only for a FINALIZED run (the versendete Dokument must equal the
frozen snapshot, section 8.2).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db, require_write_dep
from app.models.settlement_line import SettlementLine
from app.models.settlement_run import SettlementRun
from app.repositories.settlement_repo import (
    SettlementLineRepository,
    SettlementRunRepository,
)
from app.services.settlement_pdf import render_settlement_pdf
from app.services.settlement_service import (
    ComputeExtras,
    ExternalAmountEntry,
    ManualAmountEntry,
    SettlementService,
    UnitMetric,
)

router = APIRouter(prefix="/api/settlements", tags=["settlements"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class SettlementRunCreate(BaseModel):
    property_id: str
    lease_agreement_id: str
    period_start: date
    period_end: date
    advance_payments_total: str | None = None
    management_mandate_id: str | None = None


class AdvancePaymentsUpdate(BaseModel):
    advance_payments_total: str


class ExternalAmountIn(BaseModel):
    cost_entry_id: str
    unit_id: str
    tenant_amount: str
    period_from: date | None = None
    period_to: date | None = None


class ManualAmountIn(BaseModel):
    cost_entry_id: str
    unit_id: str
    amount: str


class UnitMetricIn(BaseModel):
    unit_id: str
    consumption: str | None = None
    person_count: int | None = None


class ComputeRequest(BaseModel):
    """Structured extra inputs for the engine (provisional source O3-1/O3-5)."""

    external_amounts: list[ExternalAmountIn] = []
    manual_amounts: list[ManualAmountIn] = []
    unit_metrics: list[UnitMetricIn] = []


class SettlementLineOut(BaseModel):
    id: str
    line_type: str
    cost_category_code: str | None
    label: str
    distribution_key: str | None
    total_amount: str | None
    distribution_base_total: str | None
    distribution_base_share: str | None
    time_share_days: int | None
    time_share_total_days: int | None
    amount: str
    sort_order: int
    note: str | None


class SettlementRunOut(BaseModel):
    id: str
    organization_id: str
    property_id: str
    management_mandate_id: str | None
    run_type: str
    period_start: date
    period_end: date
    unit_id: str | None
    lease_agreement_id: str | None
    tenant_profile_id: str | None
    advance_payments_total: str | None
    advance_payments_source: str
    total_cost: str | None
    balance: str | None
    status: str
    snapshot_sha256: str | None
    engine_version: str
    finalized_at: str | None
    finalized_by: str | None
    superseded_by_id: str | None
    lines: list[SettlementLineOut] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _dec(value: str, field: str) -> Decimal:
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=422, detail=f"Invalid decimal for {field}: {value}")


def _to_extras(body: ComputeRequest | None) -> ComputeExtras:
    if body is None:
        return ComputeExtras()
    return ComputeExtras(
        external_amounts=[
            ExternalAmountEntry(
                cost_entry_id=uuid.UUID(e.cost_entry_id),
                unit_id=uuid.UUID(e.unit_id),
                tenant_amount=_dec(e.tenant_amount, "tenant_amount"),
                period_from=e.period_from,
                period_to=e.period_to,
            )
            for e in body.external_amounts
        ],
        manual_amounts=[
            ManualAmountEntry(
                cost_entry_id=uuid.UUID(m.cost_entry_id),
                unit_id=uuid.UUID(m.unit_id),
                amount=_dec(m.amount, "amount"),
            )
            for m in body.manual_amounts
        ],
        unit_metrics=[
            UnitMetric(
                unit_id=uuid.UUID(u.unit_id),
                consumption=_dec(u.consumption, "consumption") if u.consumption else None,
                person_count=u.person_count,
            )
            for u in body.unit_metrics
        ],
    )


def _line_out(ln: SettlementLine) -> SettlementLineOut:
    return SettlementLineOut(
        id=str(ln.id),
        line_type=ln.line_type,
        cost_category_code=ln.cost_category_code,
        label=ln.label,
        distribution_key=ln.distribution_key,
        total_amount=str(ln.total_amount) if ln.total_amount is not None else None,
        distribution_base_total=str(ln.distribution_base_total)
        if ln.distribution_base_total is not None
        else None,
        distribution_base_share=str(ln.distribution_base_share)
        if ln.distribution_base_share is not None
        else None,
        time_share_days=ln.time_share_days,
        time_share_total_days=ln.time_share_total_days,
        amount=str(ln.amount),
        sort_order=ln.sort_order,
        note=ln.note,
    )


def _run_out(run: SettlementRun, lines: list[SettlementLine]) -> SettlementRunOut:
    return SettlementRunOut(
        id=str(run.id),
        organization_id=str(run.organization_id),
        property_id=str(run.property_id),
        management_mandate_id=str(run.management_mandate_id) if run.management_mandate_id else None,
        run_type=run.run_type,
        period_start=run.period_start,
        period_end=run.period_end,
        unit_id=str(run.unit_id) if run.unit_id else None,
        lease_agreement_id=str(run.lease_agreement_id) if run.lease_agreement_id else None,
        tenant_profile_id=str(run.tenant_profile_id) if run.tenant_profile_id else None,
        advance_payments_total=str(run.advance_payments_total)
        if run.advance_payments_total is not None
        else None,
        advance_payments_source=run.advance_payments_source,
        total_cost=str(run.total_cost) if run.total_cost is not None else None,
        balance=str(run.balance) if run.balance is not None else None,
        status=run.status,
        snapshot_sha256=run.snapshot_sha256,
        engine_version=run.engine_version,
        finalized_at=str(run.finalized_at) if run.finalized_at else None,
        finalized_by=str(run.finalized_by) if run.finalized_by else None,
        superseded_by_id=str(run.superseded_by_id) if run.superseded_by_id else None,
        lines=[_line_out(ln) for ln in lines],
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("", response_model=list[SettlementRunOut])
async def list_settlements(
    property_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    runs_repo = SettlementRunRepository(db, auth.organization_id)
    if property_id:
        runs = await runs_repo.list_by_property(property_id)
    else:
        runs = await runs_repo.list_all()
    # List view omits lines for brevity.
    return [_run_out(r, []) for r in runs]


@router.get("/{run_id}", response_model=SettlementRunOut)
async def get_settlement(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    runs_repo = SettlementRunRepository(db, auth.organization_id)
    run = await runs_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Settlement run not found")
    lines_repo = SettlementLineRepository(db, auth.organization_id)
    lines = await lines_repo.list_for_run(run_id)
    return _run_out(run, list(lines))


@router.post("", response_model=SettlementRunOut, status_code=201)
async def create_settlement(
    body: SettlementRunCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = SettlementService(db, auth.organization_id, auth.user_id)
    run = await svc.create_draft(
        property_id=uuid.UUID(body.property_id),
        lease_agreement_id=uuid.UUID(body.lease_agreement_id),
        period_start=body.period_start,
        period_end=body.period_end,
        advance_payments_total=_dec(body.advance_payments_total, "advance_payments_total")
        if body.advance_payments_total is not None
        else None,
        management_mandate_id=uuid.UUID(body.management_mandate_id)
        if body.management_mandate_id
        else None,
    )
    return _run_out(run, [])


@router.patch("/{run_id}/advance-payments", response_model=SettlementRunOut)
async def set_advance_payments(
    run_id: uuid.UUID,
    body: AdvancePaymentsUpdate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = SettlementService(db, auth.organization_id, auth.user_id)
    run = await svc.set_advance_payments(
        run_id, _dec(body.advance_payments_total, "advance_payments_total")
    )
    return _run_out(run, [])


@router.post("/{run_id}/compute", response_model=SettlementRunOut)
async def compute_settlement(
    run_id: uuid.UUID,
    body: ComputeRequest | None = None,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = SettlementService(db, auth.organization_id, auth.user_id)
    run, _result = await svc.compute(run_id, _to_extras(body))
    lines_repo = SettlementLineRepository(db, auth.organization_id)
    lines = await lines_repo.list_for_run(run_id)
    return _run_out(run, list(lines))


@router.post("/{run_id}/preview", response_model=SettlementRunOut)
async def preview_settlement(
    run_id: uuid.UUID,
    body: ComputeRequest | None = None,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    """Compute without persisting -- returns the would-be lines (read-only)."""
    svc = SettlementService(db, auth.organization_id, auth.user_id)
    runs_repo = SettlementRunRepository(db, auth.organization_id)
    run = await runs_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Settlement run not found")
    result = await svc.preview(run_id, _to_extras(body))
    # Map the pure result lines to the output shape (no DB ids).
    out = _run_out(run, [])
    out.total_cost = str(result.total_cost)
    out.balance = str(result.balance)
    out.lines = [
        SettlementLineOut(
            id="",
            line_type=ln.line_type,
            cost_category_code=ln.category_code,
            label=ln.label,
            distribution_key=ln.distribution_key,
            total_amount=str(ln.total_amount) if ln.total_amount is not None else None,
            distribution_base_total=str(ln.distribution_base_total)
            if ln.distribution_base_total is not None
            else None,
            distribution_base_share=str(ln.distribution_base_share)
            if ln.distribution_base_share is not None
            else None,
            time_share_days=ln.time_share_days,
            time_share_total_days=ln.time_share_total_days,
            amount=str(ln.amount),
            sort_order=ln.sort_order,
            note=ln.note,
        )
        for ln in result.lines
    ]
    return out


@router.post("/{run_id}/finalize", response_model=SettlementRunOut)
async def finalize_settlement(
    run_id: uuid.UUID,
    body: ComputeRequest | None = None,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = SettlementService(db, auth.organization_id, auth.user_id)
    run = await svc.finalize(run_id, _to_extras(body))
    lines_repo = SettlementLineRepository(db, auth.organization_id)
    lines = await lines_repo.list_for_run(run_id)
    return _run_out(run, list(lines))


@router.post("/{run_id}/correction", response_model=SettlementRunOut, status_code=201)
async def create_correction(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = SettlementService(db, auth.organization_id, auth.user_id)
    run = await svc.create_correction_run(run_id)
    return _run_out(run, [])


@router.get("/{run_id}/pdf")
async def get_settlement_pdf(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Render the BK-Abrechnung PDF for a FINALIZED run (section 8.2)."""
    runs_repo = SettlementRunRepository(db, auth.organization_id)
    run = await runs_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Settlement run not found")
    if run.status != "finalized":
        raise HTTPException(
            status_code=409,
            detail="PDF is only available for a finalized settlement run",
        )
    lines_repo = SettlementLineRepository(db, auth.organization_id)
    lines = await lines_repo.list_for_run(run_id)
    pdf_bytes = render_settlement_pdf(run, list(lines))
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="bk-abrechnung-{run_id}.pdf"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )
