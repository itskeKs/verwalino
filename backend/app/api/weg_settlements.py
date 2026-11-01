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
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError

from app.core.rbac import ScopeCapability
from app.repositories.coverage_declaration_repo import CoverageDeclarationRepository
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
from app.services.reserve_ledger_service import ReserveLedgerService, OPENING_IMPORT_ONLY, OPENING_IMPORT_EXISTS

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

    @field_validator('economic_plan_id', mode='before')
    @classmethod
    def require_valid_plan_id(cls, value):
        try:
            uuid.UUID(value)
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(422, {'code': 'WEG_PLAN_REQUIRED',
                'message': 'Bitte wählen Sie einen gültigen Wirtschaftsplan.'}) from None
        return value


class PlanSuccessorSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_plan_id: uuid.UUID
    successor_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2000)
    confirmed: bool = Field(strict=True)


class PaidAdvanceIn(BaseModel):
    owner_profile_id: str
    paid_total: str


class WegComputeRequest(BaseModel):
    reserve_opening_balance: str | None = None
    reserve_withdrawals_total: str | None = None
    reserve_override_reason: str = Field(default='', max_length=2000)
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
    reserve_opening_balance: str | None = None
    reserve_withdrawals_total: str | None = None
    reserve_contributions_total: str | None = None
    reserve_override_reason: str = Field(default='', max_length=2000)
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


def _dec(value: str | None, field: str) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(value)
        if not result.is_finite():
            raise ValueError('finite amount required')
        return result
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=422, detail=f"Invalid decimal for {field}: {value}")


def _weg_extras(body: WegComputeRequest | None) -> WegComputeExtras:
    if body is None:
        return WegComputeExtras()
    return WegComputeExtras(
        reserve_override_reason=body.reserve_override_reason,
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
        reserve_override_reason=body.reserve_override_reason,
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
class ReserveOverrideRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    component: Literal['OPENING', 'CONTRIBUTION', 'WITHDRAWAL', 'ADJUSTMENT', 'FUNDING']
    amount: Decimal = Field(max_digits=14, decimal_places=2, allow_inf_nan=False)
    reason: str = Field(max_length=2000)
    payment_id: uuid.UUID | None = None
    cost_entry_id: uuid.UUID | None = None
    register_entry_id: uuid.UUID | None = None


@router.get('/{run_id}/reserve-ledger')
async def reserve_ledger(run_id: uuid.UUID, auth: AuthContext = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    service = ReserveLedgerService(db, auth.organization_id, auth.user_id)
    run = await service.subject(run_id)
    return await service.read_display(run)


def _raise_reserve_opening_error(exc: DBAPIError):
    code = getattr(exc.orig, 'sqlstate', None)
    constraint = getattr(getattr(exc.orig, '__cause__', None), 'constraint_name', None)
    if (code, constraint) == ('23514', 'reserve_opening_import_only'):
        raise HTTPException(422, OPENING_IMPORT_ONLY) from None
    if (code, constraint) == ('23514', 'reserve_opening_import_exists'):
        raise HTTPException(422, OPENING_IMPORT_EXISTS) from None


@router.post('/{run_id}/reserve-ledger/overrides', status_code=201)
async def reserve_override(run_id: uuid.UUID, body: ReserveOverrideRequest,
                           auth: AuthContext = Depends(require_write_dep), db: AsyncSession = Depends(get_db, scope='function')):
    service = ReserveLedgerService(db, auth.organization_id, auth.user_id)
    run = await service.subject(run_id, write=True)
    try:
        entry = await service.add(run, **body.model_dump())
    except DBAPIError as exc:
        _raise_reserve_opening_error(exc)
        code = getattr(exc.orig, 'sqlstate', None)
        if code in {'23514', '23505', '23503'}:
            constraint = getattr(getattr(exc.orig, '__cause__', None), 'constraint_name', None)
            if constraint == 'reserve_assignment_amount':
                raise HTTPException(422, {'code': 'RESERVE_ASSIGNMENT_EXCEEDS_PAYMENT',
                    'message': 'Die Rücklagenzuordnung muss zwischen null und dem vorzeichenbehafteten Zahlungsbetrag liegen.'}) from None
            duplicate_payment = code == '23505' and constraint == 'reserve_ledger_entry_organization_id_payment_id_key'
            raise HTTPException(409 if duplicate_payment else 422,
                {'code': 'RESERVE_PAYMENT_ALREADY_ASSIGNED' if duplicate_payment else 'RESERVE_INPUT_INVALID',
                 'message': 'Bitte prüfen Sie Rücklagenzeitraum, Auszahlung, Kostenbetrag und Beschlussverknüpfung.'}) from None
        raise
    return {'id': str(entry.id)}


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
@router.get('/{run_id}/input-owners', response_model=list[uuid.UUID])
async def input_owners(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    run = await SettlementRunRepository(db, auth.organization_id).get_by_id(run_id)
    if run is None or run.run_type != 'WEG_JAHRESABRECHNUNG':
        raise HTTPException(404, 'WEG settlement run not found')
    await FinancialScopeService(db, auth.organization_id, auth.user_id).enter(
        run, capability=ScopeCapability.READ)
    # Draft visibility alone does not authorize disclosure of its complete sources.
    if not await db.scalar(text(
        "SELECT property_financial_disclosure_access(:org,:property,'read')"),
        {'org': auth.organization_id, 'property': run.property_id}):
        raise HTTPException(404, 'Complete property sources not accessible')
    return sorted(await CoverageDeclarationRepository(db, auth.organization_id).owner_ids(run))


@router.get('/{run_id}/economic-plan-successor')
async def plan_successor_preview(run_id: uuid.UUID, auth: AuthContext = Depends(require_write_dep),
                                 db: AsyncSession = Depends(get_db)):
    return await WegSettlementService(db, auth.organization_id, auth.user_id).plan_successor_preview(run_id)


@router.patch('/{run_id}/economic-plan', response_model=WegRunOut)
async def select_plan_successor(run_id: uuid.UUID, body: PlanSuccessorSelection,
                                auth: AuthContext = Depends(require_write_dep), db: AsyncSession = Depends(get_db)):
    if not body.confirmed or not body.reason.strip():
        raise HTTPException(422, 'Explicit review and reason required')
    run = await WegSettlementService(db, auth.organization_id, auth.user_id).select_plan_successor(
        run_id, body.expected_plan_id, body.successor_id, body.reason.strip())
    lines = await SettlementLineRepository(db, auth.organization_id).list_for_run(run.id)
    return _run_out(run, list(lines))


@router.post("", response_model=WegRunOut, status_code=201)
async def create_weg_run(
    body: WegRunCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = WegSettlementService(db, auth.organization_id, auth.user_id)
    try:
        run = await svc.create_weg_draft(
            accounting_scope_id=body.accounting_scope_id,
            property_id=uuid.UUID(body.property_id),
            economic_plan_id=uuid.UUID(body.economic_plan_id),
            period_start=body.period_start,
            period_end=body.period_end,
            management_mandate_id=uuid.UUID(body.management_mandate_id) if body.management_mandate_id else None,
        )
    except DBAPIError as exc:
        _raise_reserve_opening_error(exc)
        raise
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
    # The PDF may be opened as soon as the success response arrives.
    db: AsyncSession = Depends(get_db, scope="function"),
):
    svc = WegSettlementService(db, auth.organization_id, auth.user_id)
    run = await svc.finalize_weg(run_id, _weg_extras(body))
    lines_repo = SettlementLineRepository(db, auth.organization_id)
    lines = await lines_repo.list_for_run(run_id)
    return _run_out(run, list(lines))


# ---------------------------------------------------------------------------
# Paid advances derivation (T09)
# ---------------------------------------------------------------------------
class PaidAdvancesResult(BaseModel):
    owner_profile_id: str
    paid_total: str


@router.post("/{run_id}/paid-advances-derived", response_model=list[PaidAdvancesResult])
async def derive_paid_advances_route(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    """Derive paid advances per owner from actual payments (nachrichtlich)."""
    svc = WegSettlementService(db, auth.organization_id, auth.user_id)
    run = await db.get(SettlementRun, run_id)
    if run is None or run.run_type != "WEG_JAHRESABRECHNUNG":
        raise HTTPException(status_code=404, detail="WEG-Jahresabrechnung not found")
    
    result = await svc.derive_paid_advances(
        run.economic_plan_id, run.period_start.year, run.period_start, run.period_end)
    return [
        PaidAdvancesResult(
            owner_profile_id=str(r.owner_profile_id),
            paid_total=str(r.paid_total),
        )
        for r in result
    ]


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
    try:
        run = await svc.create_asset_report_draft(
            accounting_scope_id=body.accounting_scope_id,
            property_id=uuid.UUID(body.property_id),
            period_start=body.period_start,
            period_end=body.period_end,
            management_mandate_id=uuid.UUID(body.management_mandate_id) if body.management_mandate_id else None,
        )
    except DBAPIError as exc:
        _raise_reserve_opening_error(exc)
        raise
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
    # Commit before the client can request the finalized report's PDF.
    db: AsyncSession = Depends(get_db, scope="function"),
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
