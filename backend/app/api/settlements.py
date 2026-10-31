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
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db, require_write_dep
from app.core.live_principal import get_live_member_db, require_live_principal
from app.api.leases import RentChangeOut, _rent_change_out
from app.models.settlement_line import SettlementLine
from app.models.settlement_run import SettlementRun
from app.repositories.settlement_repo import (
    SettlementLineRepository,
    SettlementRunRepository,
)
from app.services.financial_scope_service import FinancialScopeService
from app.services.render_names import settlement_render_names
from app.services.settlement_pdf import render_settlement_pdf
from app.services.settlement_service import (
    ComputeExtras,
    SettlementService,
    decode_compute_extras,
)

router = APIRouter(prefix="/api/settlements", tags=["settlements"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class SettlementRunCreate(BaseModel):
    accounting_scope_id: uuid.UUID | None = None
    property_id: str
    lease_agreement_id: str
    period_start: date
    period_end: date
    advance_payments_total: str | None = None
    management_mandate_id: str | None = None


class SettlementBatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_id: uuid.UUID
    period_start: date
    period_end: date
    accounting_scope_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def valid_period(self):
        if self.period_start > self.period_end:
            raise ValueError("Settlement period must not end before it starts")
        return self


class ConsumptionReadingOut(BaseModel):
    id: str
    reading_date: date
    value: str


class ConsumptionSuggestionOut(BaseModel):
    unit_id: str
    unit_label: str
    consumption: str | None
    meter_id: str | None
    start_reading: ConsumptionReadingOut | None
    end_reading: ConsumptionReadingOut | None
    reason: str | None


class AdvanceProposalOut(BaseModel):
    lease_agreement_id: str
    tenant_cost: str
    months: str
    monthly_amount: str
    period_start: date
    period_end: date


class AdvanceProposalAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid_from: date


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


class HeatingSliceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: uuid.UUID
    period_from: date
    period_to: date
    consumption: str | None = None
    base_quantity: str | None = None

    @field_validator('consumption','base_quantity')
    @classmethod
    def valid_quantity(cls, value):
        if value is None:
            return value
        try:
            number = Decimal(value)
            if (not number.is_finite() or not Decimal(0)<=number<Decimal('1000000000000000000')
                    or number != number.quantize(Decimal('0.000001'))):
                raise ValueError('Menge muss endlich, nichtnegativ und auf höchstens sechs Nachkommastellen exakt sein')
        except InvalidOperation as exc:
            raise ValueError('Ungültige Menge') from exc
        return value


class HeatingConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cost_entry_id: uuid.UUID
    kind: Literal['HEATING','HOT_WATER']
    consumption_fraction: str
    base_key: Literal['AREA','HEATED_AREA','VOLUME']
    consumption_recorded: StrictBool
    mandatory_seventy_percent: StrictBool
    meter_type: str = Field(min_length=1,max_length=100)
    slices: list[HeatingSliceIn] = Field(default_factory=list,max_length=10000)

    @field_validator('consumption_fraction')
    @classmethod
    def valid_fraction(cls, value):
        try:
            number = Decimal(value)
            if not number.is_finite() or not Decimal('0.50')<=number<=Decimal('0.70'):
                raise ValueError('Verbrauchsanteil muss 50–70 % betragen')
        except InvalidOperation as exc:
            raise ValueError('Ungültiger Verbrauchsanteil') from exc
        return value


class ComputeRequest(BaseModel):
    """Structured extra inputs for the engine (provisional source O3-1/O3-5)."""

    model_config = ConfigDict(extra="forbid")
    heating_inputs: list[HeatingConfigIn] = Field(default_factory=list,max_length=1000)
    external_amounts: list[ExternalAmountIn] = []
    manual_amounts: list[ManualAmountIn] = []
    unit_metrics: list[UnitMetricIn] = []


class SettlementLineOut(BaseModel):
    id: str
    cost_entry_id: str | None = None
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
    extra_inputs: dict | None = None


class SettlementBatchSkipped(BaseModel):
    lease_agreement_id: str
    settlement_run_id: str


class SettlementBatchError(BaseModel):
    lease_agreement_id: str
    code: str
    error: str


class SettlementBatchOut(BaseModel):
    created: list[SettlementRunOut]
    skipped: list[SettlementBatchSkipped]
    errors: list[SettlementBatchError] = Field(default_factory=list, exclude_if=lambda errors: not errors)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _dec(value: str, field: str) -> Decimal:
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=422, detail=f"Invalid decimal for {field}: {value}")


def _to_extras(body: ComputeRequest | None) -> ComputeExtras | None:
    if body is None:
        return None
    try:
        extras = decode_compute_extras(body.model_dump(mode='json'))
    except (ValueError, InvalidOperation) as exc:
        raise HTTPException(422,'Ungültige UUID, Menge oder Datumsangabe') from exc
    return extras


def _line_out(ln: SettlementLine, cost_entry_id: str | None = None) -> SettlementLineOut:
    return SettlementLineOut(
        id=str(ln.id),
        cost_entry_id=cost_entry_id,
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
    # Relational legacy lines omit source IDs. Use only unambiguous exact matches
    # from the already-frozen snapshot; never infer source links by amount alone.
    sources = {}
    for frozen in (run.snapshot_payload or {}).get("result", {}).get("lines", []):
        key = (frozen.get("sort_order"), frozen.get("line_type"), frozen.get("label"),
               frozen.get("category_code"), frozen.get("amount"))
        try:
            source_id = str(uuid.UUID(frozen["cost_entry_id"])) if frozen.get("cost_entry_id") else None
        except (ValueError, TypeError):
            source_id = None
        sources[key] = None if key in sources else source_id
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
        extra_inputs=(run.snapshot_payload or {}).get("extra_inputs"),
        lines=[_line_out(ln, sources.get((ln.sort_order, ln.line_type, ln.label, ln.cost_category_code, str(ln.amount)))) for ln in lines],
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


@router.post("/batch", response_model=SettlementBatchOut)
async def create_settlement_batch(
    body: SettlementBatchCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    service = SettlementService(db, auth.organization_id, auth.user_id)
    created, skipped, errors = await service.create_batch(
        body.property_id, body.period_start, body.period_end, body.accounting_scope_id,
    )
    return SettlementBatchOut(
        created=[_run_out(run, []) for run in created],
        skipped=[SettlementBatchSkipped(
            lease_agreement_id=str(run.lease_agreement_id), settlement_run_id=str(run.id),
        ) for run in skipped],
        errors=[SettlementBatchError(lease_agreement_id=str(lease_id), code=error["code"], error=error["message"])
                for lease_id, error in errors],
    )


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


@router.get("/{run_id}/consumption-suggestions", response_model=list[ConsumptionSuggestionOut])
async def get_consumption_suggestions(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    return await SettlementService(db, auth.organization_id, auth.user_id).consumption_suggestions(run_id)


@router.get("/{run_id}/heating-consumption")
async def get_heating_consumption(
    run_id: uuid.UUID,
    cost_entry_id: uuid.UUID,
    meter_type: Annotated[str, Query(min_length=1,max_length=100)],
    base_key: Literal['AREA','HEATED_AREA','VOLUME'],
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    return await SettlementService(db,auth.organization_id,auth.user_id).heating_suggestions(
        run_id,cost_entry_id,meter_type,base_key)


@router.get("/{run_id}/advance-proposal", response_model=AdvanceProposalOut)
async def get_advance_proposal(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    return await SettlementService(db, auth.organization_id, auth.user_id).advance_proposal(run_id)


@router.post("/{run_id}/advance-proposal", response_model=RentChangeOut, status_code=201)
async def accept_advance_proposal(
    run_id: uuid.UUID,
    body: AdvanceProposalAccept,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    await require_live_principal(db, auth, writer=True)
    row = await SettlementService(db, auth.organization_id, auth.user_id).advance_proposal(
        run_id, accept_from=body.valid_from,
    )
    return _rent_change_out(row)


@router.post("", response_model=SettlementRunOut, status_code=201)
async def create_settlement(
    body: SettlementRunCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = SettlementService(db, auth.organization_id, auth.user_id)
    run = await svc.create_draft(
        accounting_scope_id=body.accounting_scope_id,
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


@router.patch("/{run_id}/advance-payments-derived", response_model=SettlementRunOut)
async def set_advance_payments_derived(
    run_id: uuid.UUID,
    period_year: int,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    """Fill advance_payments_total from booked NK payments (M6 §6.2, DERIVED).

    Opt-in bridge; MANUAL stays the default. The M3 engine is unchanged -- this
    only sets the value (bit-identical to MANUAL, I6-E) + source='DERIVED'.
    """
    svc = SettlementService(db, auth.organization_id, auth.user_id)
    run = await svc.set_advance_payments_derived(run_id, period_year)
    return _run_out(run, [])


@router.post("/{run_id}/compute", response_model=SettlementRunOut)
async def compute_settlement(
    run_id: uuid.UUID,
    body: ComputeRequest | None = None,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    await require_live_principal(db, auth, writer=True)
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
    await require_live_principal(db, auth, writer=True)
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
            cost_entry_id=str(ln.cost_entry_id) if ln.cost_entry_id else None,
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
    # Commit before sending success: the next HTTP request may open the PDF.
    db: AsyncSession = Depends(get_db, scope="function"),
):
    await require_live_principal(db, auth, writer=True)
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
    await FinancialScopeService(db, auth.organization_id, auth.user_id).require_export(run)
    lines_repo = SettlementLineRepository(db, auth.organization_id)
    lines = await lines_repo.list_for_run(run_id)

    # Anzeige-Namen: eingefroren aus dem Snapshot (finalisiert), sonst live
    # (Entwurf/Altbestand) -- s. render_names / ADR-0012.
    names = await settlement_render_names(db, auth.organization_id, run)

    pdf_bytes = render_settlement_pdf(run, list(lines), names)
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="bk-abrechnung-{run_id}.pdf"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )
