"""Economic-plan (Wirtschaftsplan) endpoints -- create / compute / finalize /
correction (m4 section 10.4).

RBAC: GET for all roles; POST/compute/finalize/correction require write role.
Foreign ID -> 404 (not 403), consistent with M0/M2/M3. No DELETE endpoint for
finalized plans (ADR-0003).

PDF is served only for a FINALIZED plan (the versendete/beschlussreife Unterlage
must equal the frozen snapshot).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db, require_write_dep
from app.core.live_principal import get_live_writer_db
from app.models.economic_plan import EconomicPlan
from app.models.economic_plan_line import EconomicPlanLine
from app.repositories.economic_plan_repo import (
    EconomicPlanLineRepository,
    EconomicPlanRepository,
)
from app.services.financial_scope_service import FinancialScopeService
from app.services.economic_plan_service import (
    EconomicPlanService,
    PlanInputs,
    PlannedCostEntry,
)
from app.services.render_names import wirtschaftsplan_render_names
from app.services.wirtschaftsplan_pdf import render_wirtschaftsplan_pdf

router = APIRouter(prefix="/api/economic-plans", tags=["economic-plans"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class EconomicPlanCreate(BaseModel):
    accounting_scope_id: uuid.UUID | None = None
    property_id: str
    plan_year: int
    period_start: date | None = None
    period_end: date | None = None
    reserve_contribution_total: str | None = None
    monthly_due_day: int | None = None
    management_mandate_id: str | None = None


class PlannedCostIn(BaseModel):
    category_code: str
    label: str
    planned_amount: str
    distribution_key: str = "MEA"
    category_id: str | None = None
    cost_group: str = "UMLAGEFAEHIG"


PreviousMoney = Annotated[StrictStr, Field(pattern=r"^[0-9]{1,12}(?:\.[0-9]{1,2})?$")]


class PreviousPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_id: uuid.UUID
    plan_year: Annotated[StrictInt, Field(ge=2, le=9998)]
    source_accounting_scope_id: uuid.UUID | None = None
    accounting_scope_id: uuid.UUID | None = None


class PreviousPlannedCostIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: StrictStr
    category_code: StrictStr
    label: StrictStr
    planned_amount: PreviousMoney
    distribution_key: StrictStr
    cost_group: StrictStr


class PreviousPlanCreate(PreviousPlanRequest):
    expected_source_digest: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    planned_costs: list[PreviousPlannedCostIn]
    reserve_contribution_total: PreviousMoney


class PlanComputeRequest(BaseModel):
    planned_costs: list[PlannedCostIn]
    reserve_contribution_total: str


class PlanLineOut(BaseModel):
    id: str
    line_type: str
    cost_category_code: str | None
    owner_profile_id: str | None
    unit_id: str | None
    label: str
    distribution_key: str | None
    distribution_base_total: str | None
    distribution_base_share: str | None
    planned_amount: str
    monthly_amount: str | None
    cost_group: str | None
    sort_order: int
    note: str | None


class EconomicPlanOut(BaseModel):
    id: str
    organization_id: str
    property_id: str
    management_mandate_id: str | None
    plan_year: int
    period_start: date
    period_end: date
    monthly_due_day: int | None
    reserve_contribution_total: str
    status: str
    snapshot_sha256: str | None
    engine_version: str
    finalized_at: str | None
    finalized_by: str | None
    superseded_by_id: str | None
    lines: list[PlanLineOut] = []
    draft_inputs: dict | None = None
    accounting_scope_id: str | None = None


def _dec(value: str, field: str) -> Decimal:
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=422, detail=f"Invalid decimal for {field}: {value}")


def _to_inputs(body: PlanComputeRequest | None) -> PlanInputs:
    if body is None:
        return PlanInputs()
    return PlanInputs(
        planned_costs=[
            PlannedCostEntry(
                category_code=pc.category_code,
                label=pc.label,
                planned_amount=_dec(pc.planned_amount, "planned_amount"),
                distribution_key=pc.distribution_key,
                category_id=uuid.UUID(pc.category_id) if pc.category_id else None,
                cost_group=pc.cost_group,
            )
            for pc in body.planned_costs
        ],
        reserve_contribution_total=_dec(body.reserve_contribution_total, "reserve_contribution_total"),
    )


def _line_out(ln: EconomicPlanLine) -> PlanLineOut:
    return PlanLineOut(
        id=str(ln.id),
        line_type=ln.line_type,
        cost_category_code=ln.cost_category_code,
        owner_profile_id=str(ln.owner_profile_id) if ln.owner_profile_id else None,
        unit_id=str(ln.unit_id) if ln.unit_id else None,
        label=ln.label,
        distribution_key=ln.distribution_key,
        distribution_base_total=str(ln.distribution_base_total) if ln.distribution_base_total is not None else None,
        distribution_base_share=str(ln.distribution_base_share) if ln.distribution_base_share is not None else None,
        planned_amount=str(ln.planned_amount),
        monthly_amount=str(ln.monthly_amount) if ln.monthly_amount is not None else None,
        cost_group=ln.cost_group,
        sort_order=ln.sort_order,
        note=ln.note,
    )


def _plan_out(plan: EconomicPlan, lines: list[EconomicPlanLine]) -> EconomicPlanOut:
    return EconomicPlanOut(
        id=str(plan.id),
        organization_id=str(plan.organization_id),
        property_id=str(plan.property_id),
        management_mandate_id=str(plan.management_mandate_id) if plan.management_mandate_id else None,
        plan_year=plan.plan_year,
        period_start=plan.period_start,
        period_end=plan.period_end,
        monthly_due_day=plan.monthly_due_day,
        reserve_contribution_total=str(plan.reserve_contribution_total),
        status=plan.status,
        snapshot_sha256=plan.snapshot_sha256,
        engine_version=plan.engine_version,
        finalized_at=str(plan.finalized_at) if plan.finalized_at else None,
        finalized_by=str(plan.finalized_by) if plan.finalized_by else None,
        superseded_by_id=str(plan.superseded_by_id) if plan.superseded_by_id else None,
        lines=[_line_out(ln) for ln in lines],
        draft_inputs=(plan.snapshot_payload or {}).get("draft_inputs"),
        accounting_scope_id=(plan.snapshot_payload or {}).get("previous_actuals", {}).get("accounting_scope_id"),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("", response_model=list[EconomicPlanOut])
async def list_plans(
    property_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = EconomicPlanRepository(db, auth.organization_id)
    plans = await (repo.list_by_property(property_id) if property_id else repo.list_active())
    return [_plan_out(p, []) for p in plans]


@router.post("/previous/preview")
async def preview_previous_plan(
    body: PreviousPlanRequest,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db, scope="function"),
):
    return await EconomicPlanService(db, auth.organization_id, auth.user_id, session_version=auth.session_version).previous_preview(
        body.property_id, body.plan_year, body.source_accounting_scope_id, body.accounting_scope_id)


@router.post("/previous", response_model=EconomicPlanOut, status_code=201)
async def create_previous_plan(
    body: PreviousPlanCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db, scope="function"),
):
    plan = await EconomicPlanService(db, auth.organization_id, auth.user_id, session_version=auth.session_version).create_from_previous(
        property_id=body.property_id, plan_year=body.plan_year,
        source_accounting_scope_id=body.source_accounting_scope_id,
        accounting_scope_id=body.accounting_scope_id,
        expected_source_digest=body.expected_source_digest,
        planned_costs=[row.model_dump() for row in body.planned_costs],
        reserve_contribution_total=body.reserve_contribution_total)
    return _plan_out(plan, [])


@router.get("/{plan_id}", response_model=EconomicPlanOut)
async def get_plan(
    plan_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = EconomicPlanRepository(db, auth.organization_id)
    plan = await repo.get_by_id(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Economic plan not found")
    line_repo = EconomicPlanLineRepository(db, auth.organization_id)
    lines = await line_repo.list_for_plan(plan_id)
    return _plan_out(plan, list(lines))


@router.post("", response_model=EconomicPlanOut, status_code=201)
async def create_plan(
    body: EconomicPlanCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = EconomicPlanService(db, auth.organization_id, auth.user_id, session_version=auth.session_version)
    plan = await svc.create_draft(
        accounting_scope_id=body.accounting_scope_id,
        property_id=uuid.UUID(body.property_id),
        plan_year=body.plan_year,
        period_start=body.period_start,
        period_end=body.period_end,
        reserve_contribution_total=_dec(body.reserve_contribution_total, "reserve_contribution_total")
        if body.reserve_contribution_total is not None else None,
        monthly_due_day=body.monthly_due_day,
        management_mandate_id=uuid.UUID(body.management_mandate_id) if body.management_mandate_id else None,
    )
    return _plan_out(plan, [])


@router.post("/{plan_id}/compute", response_model=EconomicPlanOut)
async def compute_plan(
    plan_id: uuid.UUID,
    body: PlanComputeRequest | None = None,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = EconomicPlanService(db, auth.organization_id, auth.user_id, session_version=auth.session_version)
    plan, _result = await svc.compute(plan_id, _to_inputs(body))
    line_repo = EconomicPlanLineRepository(db, auth.organization_id)
    lines = await line_repo.list_for_plan(plan_id)
    return _plan_out(plan, list(lines))


@router.post("/{plan_id}/finalize", response_model=EconomicPlanOut)
async def finalize_plan(
    plan_id: uuid.UUID,
    body: PlanComputeRequest | None = None,
    auth: AuthContext = Depends(require_write_dep),
    # Commit before the client can request the finalized plan's PDF.
    db: AsyncSession = Depends(get_db, scope="function"),
):
    svc = EconomicPlanService(db, auth.organization_id, auth.user_id, session_version=auth.session_version)
    plan = await svc.finalize(plan_id, _to_inputs(body))
    line_repo = EconomicPlanLineRepository(db, auth.organization_id)
    lines = await line_repo.list_for_plan(plan_id)
    return _plan_out(plan, list(lines))


@router.post("/{plan_id}/correction", response_model=EconomicPlanOut, status_code=201)
async def create_plan_correction(
    plan_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = EconomicPlanService(db, auth.organization_id, auth.user_id, session_version=auth.session_version)
    plan = await svc.create_correction_run(plan_id)
    return _plan_out(plan, [])


@router.get("/{plan_id}/pdf")
async def get_plan_pdf(
    plan_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = EconomicPlanRepository(db, auth.organization_id)
    plan = await repo.get_by_id(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Economic plan not found")
    if plan.status != "finalized":
        raise HTTPException(status_code=409, detail="PDF is only available for a finalized plan")
    await FinancialScopeService(db, auth.organization_id, auth.user_id).require_export(plan)
    line_repo = EconomicPlanLineRepository(db, auth.organization_id)
    lines = await line_repo.list_for_plan(plan_id)
    # Anzeige-Namen: eingefroren aus dem Snapshot (finalisiert), sonst live.
    names = await wirtschaftsplan_render_names(db, auth.organization_id, plan)
    pdf_bytes = render_wirtschaftsplan_pdf(plan, list(lines), names)
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="wirtschaftsplan-{plan_id}.pdf"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )
