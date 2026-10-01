"""EconomicPlan service -- create draft / compute / finalize (atomic) / correct.

m4-weg-abrechnung.md section 10.3.

Boundary discipline (SDD section 16): the service loads ORM rows, maps them to
the engine's frozen dataclasses, calls the PURE plan engine, and persists the
result (economic_plan_line + snapshot_payload + hash). The engine never sees the
DB; the service never re-implements plan math.

finalize() is ONE atomic transaction (analogous to settlement_service.finalize):
  compute -> snapshot_payload -> snapshot_sha256 -> (re)write economic_plan_line
  -> status='finalized' + finalized_at/by -> audit_event. The ADR-0003 trigger
  freezes the plan from commit.

The plan's planned cost values are supplied as STRUCTURED INPUT at compute time
(the clerk's confirmed Planwerte; the repo's prior_year_actuals is only a
proposal helper). They are then frozen into the immutable snapshot.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.canonical import canonical_json, sha256_hex
from app.engine.rounding import DistributionError
from app.engine.snapshot import build_plan_snapshot_payload
from app.services.render_names import (
    DISPLAY_NAMES_KEY,
    freeze_block,
    resolve_wirtschaftsplan_values,
)
from app.engine.types import (
    OwnerPeriodInput,
    PlannedCostInput,
    PropertyInput,
    UnitInput,
    WirtschaftsplanInput,
)
from app.engine.types import PeriodOverlapError
from app.engine.weg import WegValidationError
from app.engine.wirtschaftsplan import PlanResult, compute_wirtschaftsplan
from app.core.temporal import db_exclusive_to_engine_inclusive
from app.models.audit_event import AuditEvent
from app.services.financial_input_policy import FinancialInputPolicy
from app.models.economic_plan import EconomicPlan
from app.models.economic_plan_line import EconomicPlanLine
from app.repositories.audit_repo import AuditRepository
from app.repositories.economic_plan_repo import (
    EconomicPlanLineRepository,
    EconomicPlanRepository,
)
from app.repositories.settlement_repo import SettlementRunRepository

ENGINE_VERSION = "m4.1.0"


@dataclass
class PlannedCostEntry:
    """A confirmed planned cost of a category (the clerk's Planwert)."""

    category_code: str
    label: str
    planned_amount: Decimal
    distribution_key: str = "MEA"
    category_id: uuid.UUID | None = None
    cost_group: str = "UMLAGEFAEHIG"


@dataclass
class PlanInputs:
    """Structured plan inputs supplied at compute time (m4 section 5.1)."""

    planned_costs: list[PlannedCostEntry] | None = None
    reserve_contribution_total: Decimal | None = None


class EconomicPlanService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID):
        self._session = session
        self._org_id = org_id
        self._user_id = user_id
        self._plans = EconomicPlanRepository(session, org_id)
        self._lines = EconomicPlanLineRepository(session, org_id)
        self._runs = SettlementRunRepository(session, org_id)  # property aggregation
        self._audit = AuditRepository(session, org_id)

    # ------------------------------------------------------------------
    # create draft
    # ------------------------------------------------------------------
    async def create_draft(
        self,
        property_id: uuid.UUID,
        plan_year: int,
        period_start: date,
        period_end: date,
        reserve_contribution_total: Decimal | None = None,
        monthly_due_day: int | None = None,
        management_mandate_id: uuid.UUID | None = None,
    ) -> EconomicPlan:
        plan = EconomicPlan(
            organization_id=self._org_id,
            property_id=property_id,
            management_mandate_id=management_mandate_id,
            plan_year=plan_year,
            period_start=period_start,
            period_end=period_end,
            monthly_due_day=monthly_due_day,
            reserve_contribution_total=reserve_contribution_total or Decimal("0.00"),
            status="draft",
            engine_version=ENGINE_VERSION,
        )
        plan = await self._plans.create(plan)
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="economic_plan",
                entity_id=plan.id,
                action="economic_plan.created",
                payload_diff={"plan_year": plan_year, "period": f"{period_start} - {period_end}"},
            )
        )
        return plan

    # ------------------------------------------------------------------
    # compute (draft, repeatable)
    # ------------------------------------------------------------------
    async def compute(
        self, plan_id: uuid.UUID, inputs: PlanInputs
    ) -> tuple[EconomicPlan, PlanResult]:
        plan = await self._get_draft_or_404(plan_id)
        inp, result = await self._compute_engine(plan, inputs)

        await self._lines.delete_for_plan(plan.id)
        await self._write_lines(plan, result)

        plan.snapshot_payload = {**build_plan_snapshot_payload(inp, result), "financial_input_coverage": self._coverage}
        plan.reserve_contribution_total = inputs.reserve_contribution_total
        await self._plans.update(plan)

        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="economic_plan",
                entity_id=plan.id,
                action="economic_plan.computed",
                payload_diff={"total_advance_volume": str(result.total_advance_volume)},
            )
        )
        return plan, result

    # ------------------------------------------------------------------
    # finalize (atomic snapshot + hash)
    # ------------------------------------------------------------------
    async def finalize(
        self, plan_id: uuid.UUID, inputs: PlanInputs
    ) -> EconomicPlan:
        plan = await self._get_draft_or_404(plan_id)
        inp, result = await self._compute_engine(plan, inputs)

        payload = build_plan_snapshot_payload(inp, result)
        payload["financial_input_coverage"] = self._coverage
        # Eigentümer-/Objekt-Anzeige-Namen einfrieren (ADR-0012), vor dem Hash --
        # s. settlement_service.finalize. Noch OLD.status='draft', Schreiben erlaubt.
        payload[DISPLAY_NAMES_KEY] = freeze_block(
            await resolve_wirtschaftsplan_values(self._session, self._org_id, plan)
        )
        sha = sha256_hex(canonical_json(payload))

        await self._lines.delete_for_plan(plan.id)
        await self._write_lines(plan, result)

        now = datetime.now(timezone.utc)
        plan.snapshot_payload = payload
        plan.snapshot_sha256 = sha
        plan.reserve_contribution_total = inputs.reserve_contribution_total
        plan.status = "finalized"
        plan.finalized_at = now
        plan.finalized_by = self._user_id
        await self._plans.update(plan)

        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="economic_plan",
                entity_id=plan.id,
                action="economic_plan.finalized",
                payload_diff={"snapshot_sha256": sha,
                              "total_advance_volume": str(result.total_advance_volume)},
            )
        )
        return plan

    async def create_correction_run(self, plan_id: uuid.UUID) -> EconomicPlan:
        original = await self._plans.get_by_id_for_update(plan_id)
        if original is None:
            raise HTTPException(status_code=404, detail="Economic plan not found")
        if original.status != "finalized":
            raise HTTPException(
                status_code=409,
                detail="Only finalized plans are corrected via a new plan; edit a draft directly",
            )
        if original.superseded_by_id is not None:
            raise HTTPException(status_code=409, detail="This plan has already been superseded")

        new_plan = EconomicPlan(
            organization_id=self._org_id,
            property_id=original.property_id,
            management_mandate_id=original.management_mandate_id,
            plan_year=original.plan_year,
            period_start=original.period_start,
            period_end=original.period_end,
            monthly_due_day=original.monthly_due_day,
            reserve_contribution_total=original.reserve_contribution_total,
            status="draft",
            engine_version=ENGINE_VERSION,
        )
        new_plan = await self._plans.create(new_plan)
        original.superseded_by_id = new_plan.id
        await self._plans.update(original)

        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id, entity_type="economic_plan",
                entity_id=original.id, action="economic_plan.superseded",
                payload_diff={"superseded_by_id": str(new_plan.id)},
            )
        )
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id, entity_type="economic_plan",
                entity_id=new_plan.id, action="economic_plan.created",
                payload_diff={"correction_of": str(original.id)},
            )
        )
        return new_plan

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    async def _get_draft_or_404(self, plan_id: uuid.UUID) -> EconomicPlan:
        plan = await self._plans.get_by_id_for_update(plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Economic plan not found")
        if plan.status != "draft":
            raise HTTPException(
                status_code=409,
                detail=f"Plan is '{plan.status}'; only draft plans can be (re)computed/finalized",
            )
        return plan

    async def _compute_engine(
        self, plan: EconomicPlan, inputs: PlanInputs
    ) -> tuple[WirtschaftsplanInput, PlanResult]:
        self._coverage = await FinancialInputPolicy(self._session, self._org_id, self._user_id).require(plan, "economic_plan", inputs)
        inp = await self._load_engine_input(plan, inputs)
        try:
            result = compute_wirtschaftsplan(inp)
        except (WegValidationError, DistributionError, PeriodOverlapError) as exc:
            raise HTTPException(status_code=422, detail=f"Plan-Berechnung nicht moeglich: {exc}")
        return inp, result

    async def _load_engine_input(
        self, plan: EconomicPlan, inputs: PlanInputs
    ) -> WirtschaftsplanInput:
        from app.repositories.property_repo import PropertyRepository

        prop_repo = PropertyRepository(self._session, self._org_id)
        prop = await prop_repo.get_by_id(plan.property_id)
        if prop is None:
            raise HTTPException(status_code=404, detail="Property not found")

        units = await self._runs.units_for_property(plan.property_id)
        ownership = await self._runs.ownership_for_property(plan.property_id)

        unit_inputs = tuple(
            UnitInput(
                id=u.id, number=u.number, usage_type=u.usage_type,
                area=u.area, heated_area=u.heated_area, mea=u.mea,
            )
            for u in units
        )
        owner_inputs = tuple(
            OwnerPeriodInput(
                unit_id=o.unit_id,
                owner_profile_id=o.owner_profile_id,
                valid_from=o.valid_from,
                valid_to=db_exclusive_to_engine_inclusive(o.valid_to),
                mea_share=o.mea_share,
            )
            for o in ownership
        )
        planned = tuple(
            PlannedCostInput(
                category_id=pc.category_id,
                category_code=pc.category_code,
                label=pc.label,
                planned_amount=pc.planned_amount,
                distribution_key=pc.distribution_key,
                cost_group=pc.cost_group,
            )
            for pc in inputs.planned_costs
        )
        return WirtschaftsplanInput(
            plan_year=plan.plan_year,
            period_start=plan.period_start,
            period_end=plan.period_end,
            property=PropertyInput(id=prop.id, name=prop.name, management_type=prop.management_type),
            units=unit_inputs,
            owner_periods=owner_inputs,
            planned_costs=planned,
            reserve_contribution_total=inputs.reserve_contribution_total,
            engine_version=ENGINE_VERSION,
        )

    async def _write_lines(self, plan: EconomicPlan, result: PlanResult) -> None:
        # Planned-cost + reserve lines (object-wide).
        for ln in result.planned_lines:
            self._session.add(
                EconomicPlanLine(
                    organization_id=self._org_id,
                    economic_plan_id=plan.id,
                    line_type=ln.line_type,
                    cost_category_id=ln.category_id,
                    cost_category_code=ln.category_code,
                    label=ln.label,
                    distribution_key=ln.distribution_key,
                    distribution_base_total=ln.distribution_base_total,
                    planned_amount=ln.amount,
                    cost_group=ln.note if ln.note in ("UMLAGEFAEHIG", "NICHT_UMLAGEFAEHIG") else None,
                    sort_order=ln.sort_order,
                    note=ln.note,
                )
            )
        # Per-owner advance lines.
        base = len(result.planned_lines)
        for i, o in enumerate(result.owners):
            self._session.add(
                EconomicPlanLine(
                    organization_id=self._org_id,
                    economic_plan_id=plan.id,
                    line_type="OWNER_ADVANCE",
                    owner_profile_id=o.owner_profile_id,
                    unit_id=o.unit_id,
                    label="Hausgeldvorschuss",
                    distribution_key="MEA",
                    distribution_base_share=o.distribution_base_share,
                    distribution_base_total=o.distribution_base_total,
                    planned_amount=o.annual_advance,
                    monthly_amount=o.monthly_amount,
                    sort_order=base + i,
                )
            )
        await self._session.flush()
