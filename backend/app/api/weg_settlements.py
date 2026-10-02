"""WEG-Jahresabrechnung + Vermoegensbericht endpoints (m4 section 10.4).

Both are settlement_run run_types (E4-1). Separate router from the BK path so
the WEG-specific create/compute payloads stay clear; the underlying entity is
the shared settlement_run.

RBAC: GET for all roles; POST/compute/finalize require write role. Foreign ID
-> 404. No DELETE of a finalized run (ADR-0003). PDF only for a finalized run.
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
from app.services.financial_scope_service import FinancialScopeService
from app.services.render_names import (
    vermoegensbericht_render_names,
    weg_render_names,
)
from app.services.vermoegensbericht_pdf import render_vermoegensbericht_pdf
from app.services.weg_jahresabrechnung_pdf import render_weg_jahresabrechnung_pdf
from app.services.weg_settlement_service import (
    AssetItemEntry,
    AssetReportInputs,
    BankBalanceEntry,
    ManualOwnerAdvance,
    WegComputeExtras,
    WegSettlementService,
)

router = APIRouter(prefix="/api/weg-settlements", tags=["weg-settlements"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class WegRunCreate(BaseModel):
    accounting_scope_id: uuid.UUID | None = None
    property_id: str
    economic_plan_id: str
    period_start: date
    period_end: date
    management_mandate_id: str | None = None


class PaidAdvanceIn(BaseModel):
    owner_profile_id: str
    paid_total: str


class WegComputeRequest(BaseModel):
    reserve_opening_balance: str
    reserve_withdrawals_total: str
    paid_advances: list[PaidAdvanceIn]


class AssetReportCreate(BaseModel):
    accounting_scope_id: uuid.UUID | None = None
    property_id: str
    period_start: date
    period_end: date
    management_mandate_id: str | None = None


class BankBalanceIn(BaseModel):
    bank_account_id: str
    label: str
    account_type: str
    balance: str


class AssetItemIn(BaseModel):
    label: str
    amount: str


class AssetReportComputeRequest(BaseModel):
    reserve_opening_balance: str
    reserve_withdrawals_total: str
    reserve_contributions_total: str
    bank_balances: list[BankBalanceIn]
    receivables: list[AssetItemIn]
    payables: list[AssetItemIn]


class WegLineOut(BaseModel):
    id: str
    line_type: str
    addressee_owner_profile_id: str | None
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


class WegRunOut(BaseModel):
    id: str
    organization_id: str
    property_id: str
    run_type: str
    period_start: date
    period_end: date
    economic_plan_id: str | None
    total_cost: str | None
    balance: str | None
    status: str
    snapshot_sha256: str | None
    engine_version: str
    finalized_at: str | None
    superseded_by_id: str | None
    lines: list[WegLineOut] = []


def _dec(value: str, field: str) -> Decimal:
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=422, detail=f"Invalid decimal for {field}: {value}")


def _weg_extras(body: WegComputeRequest | None) -> WegComputeExtras:
    if body is None:
        return WegComputeExtras()
    return WegComputeExtras(
        reserve_opening_balance=_dec(body.reserve_opening_balance, "reserve_opening_balance"),
        reserve_withdrawals_total=_dec(body.reserve_withdrawals_total, "reserve_withdrawals_total"),
        paid_advances=[
            ManualOwnerAdvance(
                owner_profile_id=uuid.UUID(p.owner_profile_id),
                paid_total=_dec(p.paid_total, "paid_total"),
            )
            for p in body.paid_advances
        ],
    )


def _report_inputs(body: AssetReportComputeRequest | None) -> AssetReportInputs:
    if body is None:
        return AssetReportInputs()
    return AssetReportInputs(
        reserve_opening_balance=_dec(body.reserve_opening_balance, "reserve_opening_balance"),
        reserve_withdrawals_total=_dec(body.reserve_withdrawals_total, "reserve_withdrawals_total"),
        reserve_contributions_total=_dec(body.reserve_contributions_total, "reserve_contributions_total"),
        bank_balances=[
            BankBalanceEntry(
                bank_account_id=uuid.UUID(b.bank_account_id), label=b.label,
                account_type=b.account_type, balance=_dec(b.balance, "balance"),
            )
            for b in body.bank_balances
        ],
        receivables=[AssetItemEntry(label=r.label, amount=_dec(r.amount, "amount")) for r in body.receivables],
        payables=[AssetItemEntry(label=p.label, amount=_dec(p.amount, "amount")) for p in body.payables],
    )


def _line_out(ln: SettlementLine) -> WegLineOut:
    return WegLineOut(
        id=str(ln.id),
        line_type=ln.line_type,
        addressee_owner_profile_id=str(ln.addressee_owner_profile_id) if ln.addressee_owner_profile_id else None,
        cost_category_code=ln.cost_category_code,
        label=ln.label,
        distribution_key=ln.distribution_key,
        total_amount=str(ln.total_amount) if ln.total_amount is not None else None,
        distribution_base_total=str(ln.distribution_base_total) if ln.distribution_base_total is not None else None,
        distribution_base_share=str(ln.distribution_base_share) if ln.distribution_base_share is not None else None,
        time_share_days=ln.time_share_days,
        time_share_total_days=ln.time_share_total_days,
        amount=str(ln.amount),
        sort_order=ln.sort_order,
        note=ln.note,
    )


def _run_out(run: SettlementRun, lines: list[SettlementLine]) -> WegRunOut:
    return WegRunOut(
        id=str(run.id),
        organization_id=str(run.organization_id),
        property_id=str(run.property_id),
        run_type=run.run_type,
        period_start=run.period_start,
        period_end=run.period_end,
        economic_plan_id=str(run.economic_plan_id) if run.economic_plan_id else None,
        total_cost=str(run.total_cost) if run.total_cost is not None else None,
        balance=str(run.balance) if run.balance is not None else None,
        status=run.status,
        snapshot_sha256=run.snapshot_sha256,
        engine_version=run.engine_version,
        finalized_at=str(run.finalized_at) if run.finalized_at else None,
        superseded_by_id=str(run.superseded_by_id) if run.superseded_by_id else None,
        lines=[_line_out(ln) for ln in lines],
    )


# ---------------------------------------------------------------------------
# Shared read endpoints (both run types)
# ---------------------------------------------------------------------------
@router.get("", response_model=list[WegRunOut])
async def list_weg_runs(
    property_id: uuid.UUID | None = None,
    run_type: str = "WEG_JAHRESABRECHNUNG",
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List active WEG runs of one run_type (WEG_JAHRESABRECHNUNG | VERMOEGENSBERICHT).

    Lines are omitted in the list (load the detail for lines), mirroring the BK
    settlements list. Superseded (corrected) runs are excluded.
    """
    if run_type not in ("WEG_JAHRESABRECHNUNG", "VERMOEGENSBERICHT"):
        raise HTTPException(status_code=422, detail="invalid run_type")
    runs_repo = SettlementRunRepository(db, auth.organization_id)
    runs = await (
        runs_repo.list_by_property(property_id) if property_id else runs_repo.list_active()
    )
    return [
        _run_out(r, [])
        for r in runs
        if r.run_type == run_type and r.superseded_by_id is None
    ]


@router.get("/{run_id}", response_model=WegRunOut)
async def get_weg_run(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    runs_repo = SettlementRunRepository(db, auth.organization_id)
    run = await runs_repo.get_by_id(run_id)
    if run is None or run.run_type not in ("WEG_JAHRESABRECHNUNG", "VERMOEGENSBERICHT"):
        raise HTTPException(status_code=404, detail="WEG settlement run not found")
    lines_repo = SettlementLineRepository(db, auth.organization_id)
    lines = await lines_repo.list_for_run(run_id)
    return _run_out(run, list(lines))


# ---------------------------------------------------------------------------
# WEG-Jahresabrechnung
# ---------------------------------------------------------------------------
@router.post("", response_model=WegRunOut, status_code=201)
async def create_weg_run(
    body: WegRunCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = WegSettlementService(db, auth.organization_id, auth.user_id)
    run = await svc.create_weg_draft(
        accounting_scope_id=body.accounting_scope_id,
        property_id=uuid.UUID(body.property_id),
        economic_plan_id=uuid.UUID(body.economic_plan_id),
        period_start=body.period_start,
        period_end=body.period_end,
        management_mandate_id=uuid.UUID(body.management_mandate_id) if body.management_mandate_id else None,
    )
    return _run_out(run, [])


@router.post("/{run_id}/compute", response_model=WegRunOut)
async def compute_weg_run(
    run_id: uuid.UUID,
    body: WegComputeRequest | None = None,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = WegSettlementService(db, auth.organization_id, auth.user_id)
    run, _result = await svc.compute_weg(run_id, _weg_extras(body))
    lines_repo = SettlementLineRepository(db, auth.organization_id)
    lines = await lines_repo.list_for_run(run_id)
    return _run_out(run, list(lines))


@router.post("/{run_id}/finalize", response_model=WegRunOut)
async def finalize_weg_run(
    run_id: uuid.UUID,
    body: WegComputeRequest | None = None,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = WegSettlementService(db, auth.organization_id, auth.user_id)
    run = await svc.finalize_weg(run_id, _weg_extras(body))
    lines_repo = SettlementLineRepository(db, auth.organization_id)
    lines = await lines_repo.list_for_run(run_id)
    return _run_out(run, list(lines))


# ---------------------------------------------------------------------------
# Vermoegensbericht
# ---------------------------------------------------------------------------
@router.post("/asset-reports", response_model=WegRunOut, status_code=201)
async def create_asset_report(
    body: AssetReportCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = WegSettlementService(db, auth.organization_id, auth.user_id)
    run = await svc.create_asset_report_draft(
        accounting_scope_id=body.accounting_scope_id,
        property_id=uuid.UUID(body.property_id),
        period_start=body.period_start,
        period_end=body.period_end,
        management_mandate_id=uuid.UUID(body.management_mandate_id) if body.management_mandate_id else None,
    )
    return _run_out(run, [])


@router.post("/asset-reports/{run_id}/compute", response_model=WegRunOut)
async def compute_asset_report(
    run_id: uuid.UUID,
    body: AssetReportComputeRequest | None = None,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = WegSettlementService(db, auth.organization_id, auth.user_id)
    run, _result = await svc.compute_asset_report(run_id, _report_inputs(body))
    lines_repo = SettlementLineRepository(db, auth.organization_id)
    lines = await lines_repo.list_for_run(run_id)
    return _run_out(run, list(lines))


@router.post("/asset-reports/{run_id}/finalize", response_model=WegRunOut)
async def finalize_asset_report(
    run_id: uuid.UUID,
    body: AssetReportComputeRequest | None = None,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = WegSettlementService(db, auth.organization_id, auth.user_id)
    run = await svc.finalize_asset_report(run_id, _report_inputs(body))
    lines_repo = SettlementLineRepository(db, auth.organization_id)
    lines = await lines_repo.list_for_run(run_id)
    return _run_out(run, list(lines))


# ---------------------------------------------------------------------------
# PDFs
# ---------------------------------------------------------------------------
@router.get("/{run_id}/pdf")
async def get_weg_pdf(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    runs_repo = SettlementRunRepository(db, auth.organization_id)
    run = await runs_repo.get_by_id(run_id)
    if run is None or run.run_type != "WEG_JAHRESABRECHNUNG":
        raise HTTPException(status_code=404, detail="WEG-Jahresabrechnung not found")
    if run.status != "finalized":
        raise HTTPException(status_code=409, detail="PDF is only available for a finalized run")
    await FinancialScopeService(db, auth.organization_id, auth.user_id).require_export(run)
    lines_repo = SettlementLineRepository(db, auth.organization_id)
    lines = await lines_repo.list_for_run(run_id)

    # Anzeige-Namen: eingefroren aus dem Snapshot (finalisiert), sonst live.
    dn = await weg_render_names(db, auth.organization_id, run)

    pdf_bytes = render_weg_jahresabrechnung_pdf(run, list(lines), dn)
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="weg-jahresabrechnung-{run_id}.pdf"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


@router.get("/asset-reports/{run_id}/pdf")
async def get_asset_report_pdf(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    runs_repo = SettlementRunRepository(db, auth.organization_id)
    run = await runs_repo.get_by_id(run_id)
    if run is None or run.run_type != "VERMOEGENSBERICHT":
        raise HTTPException(status_code=404, detail="Vermoegensbericht not found")
    if run.status != "finalized":
        raise HTTPException(status_code=409, detail="PDF is only available for a finalized run")
    await FinancialScopeService(db, auth.organization_id, auth.user_id).require_export(run)
    lines_repo = SettlementLineRepository(db, auth.organization_id)
    lines = await lines_repo.list_for_run(run_id)

    # Anzeige-Namen: eingefroren aus dem Snapshot (finalisiert), sonst live.
    dn = await vermoegensbericht_render_names(db, auth.organization_id, run)

    pdf_bytes = render_vermoegensbericht_pdf(run, list(lines), dn)
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="vermoegensbericht-{run_id}.pdf"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )
