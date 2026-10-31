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
(the clerk's confirmed Planwerte; prior-year actuals are only a
proposal helper). They are then frozen into the immutable snapshot.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext

from fastapi import HTTPException
from app.core.rbac import ScopeCapability
from app.core.tenant import AuthContext
from app.core.live_principal import require_live_principal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.periods import period_starting_in, validate_settlement_period
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
from app.services.financial_scope_service import FinancialScopeService
from app.services.authorization_service import AuthorizationService
from app.services.report_service import ReportService
from app.repositories.property_repo import PropertyRepository
from app.services.financial_computation_service import begin_computation, record_computation
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
    def __init__(self, session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, *, session_version: int = 0):
        self._session = session
        self._org_id = org_id
        self._user_id = user_id
        self._session_version = session_version
        self._plans = EconomicPlanRepository(session, org_id)
        self._lines = EconomicPlanLineRepository(session, org_id)
        self._runs = SettlementRunRepository(session, org_id)  # property aggregation
        self._audit = AuditRepository(session, org_id)

    async def _previous_switch(self, authorized_scopes: set[uuid.UUID]) -> None:
        selected = await self._session.scalar(text(
            "SELECT NULLIF(current_setting('app.current_scope_id',true),'')::uuid"))
        if selected is not None:
            if selected not in authorized_scopes:
                raise HTTPException(409, "Unexpected selected accounting scope")
            # Deliberately local to this two-role, cross-year workflow. The
            # ordinary FinancialScopeService mismatch checks remain unchanged.
            await self._session.execute(text(
                "SELECT set_config('app.current_scope_id','',true)"))

    @staticmethod
    def _previous_scope(sphere, property_id, start, end) -> None:
        if sphere.property_id != property_id:
            raise HTTPException(422, "Accounting scope belongs to another property")
        if sphere.scope_type != "WEG" or not sphere.whole_property:
            raise HTTPException(422, "Previous actuals require a whole-property WEG scope")
        if start < sphere.valid_from or sphere.valid_to is not None and end > sphere.valid_to:
            raise HTTPException(422, "Property period is outside accounting scope")

    async def _previous_admit(self, property_id, plan_year, source_scope_id, target_scope_id):
        if type(plan_year) is not int or not 2 <= plan_year <= 9998:
            raise HTTPException(422, "Plan year must be an integer between 2 and 9998")
        auth = AuthContext(self._user_id, self._org_id, "live database role required", session_version=self._session_version)
        await require_live_principal(self._session, auth, writer=True)
        context = FinancialScopeService(self._session, self._org_id, self._user_id)
        await context.lock_inputs()
        selected = await self._session.scalar(text(
            "SELECT NULLIF(current_setting('app.current_scope_id',true),'')::uuid"))
        if selected is not None and selected not in {source_scope_id, target_scope_id}:
            raise HTTPException(409, "Unexpected selected accounting scope")
        prop = await PropertyRepository(self._session, self._org_id).get_by_id(property_id)
        if prop is None:
            raise HTTPException(404, "Property not found")
        if prop.management_type not in {"WEG", "MISCH"}:
            raise HTTPException(422, "Previous actuals require a WEG or mixed property")
        start, end = period_starting_in(prop.fiscal_year_start_month, plan_year - 1)
        target_start, target_end = period_starting_in(prop.fiscal_year_start_month, plan_year)
        authorization = AuthorizationService(self._session, auth)
        authorized = set()
        for scope_id, capability, first, last in (
            (source_scope_id, ScopeCapability.READ, start, end),
            (target_scope_id, ScopeCapability.PREPARE, target_start, target_end),
        ):
            if scope_id is not None:
                sphere = await authorization.require(scope_id, capability)
                self._previous_scope(sphere, property_id, first, last)
                authorized.add(scope_id)
        await self._previous_switch(authorized)
        # A transient subject validates PREPARE without inserting or flushing a
        # plan, binding, audit event or computation during preview.
        target = EconomicPlan(organization_id=self._org_id, property_id=property_id,
            plan_year=plan_year, period_start=target_start, period_end=target_end,
            management_mandate_id=None, status="draft", engine_version=ENGINE_VERSION)
        target_sphere = await context.prepare_new(target, target_scope_id)
        if target_sphere is not None:
            self._previous_scope(target_sphere, property_id, target_start, target_end)
        await self._previous_switch(authorized)
        _, source_sphere = await ReportService(self._session, self._org_id, self._user_id).property_period(
            property_id, start, end, source_scope_id)
        if source_sphere is not None:
            self._previous_scope(source_sphere, property_id, start, end)
        return prop, source_sphere, target_sphere, authorized

    @staticmethod
    def _previous_money(value: str) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,2})?", value) is None:
            raise HTTPException(422, "Amounts must be finite nonnegative NUMERIC(14,2) strings without subcents")
        with localcontext() as arithmetic:
            arithmetic.prec = 42
            return format(Decimal(value), ".2f")

    @staticmethod
    def _previous_row_payload(row) -> dict:
        # Bind the digest to complete persisted source metadata, not just sums.
        return {column.name: (str(value) if isinstance(value, (uuid.UUID, date, Decimal)) else value)
            for column in row.__table__.columns
            for value in [getattr(row, column.name)]}

    async def _previous_proposal(self, property_id, plan_year, source_scope_id=None, target_scope_id=None):
        prop, source, target, authorized = await self._previous_admit(
            property_id, plan_year, source_scope_id, target_scope_id)
        start, end = period_starting_in(prop.fiscal_year_start_month, plan_year - 1)
        rows = await self._plans.prior_year_sources(property_id, start, end)
        if not rows:
            raise HTTPException(422, "No current confirmed prior-year cost sources")
        categories, totals, codes, sources = {}, {}, {}, []
        with localcontext() as arithmetic:
            arithmetic.prec = max(42, 28 + len(str(len(rows) + 1)))
            for cost, category in rows:
                if category is None or not category.is_active:
                    raise HTTPException(422, "Prior-year category is unmapped or inactive")
                if cost.cost_category_plan_version != category.plan_version:
                    raise HTTPException(422, "Prior-year category version is ambiguous")
                key = (category.id, category.plan_version)
                if category.code in codes and codes[category.code] != key:
                    raise HTTPException(422, "Multiple category versions share a prior-year code")
                codes[category.code] = key
                if category.cost_group not in {"UMLAGEFAEHIG", "NICHT_UMLAGEFAEHIG"}:
                    raise HTTPException(422, "Unsupported prior-year cost group")
                days = (cost.service_period_to - cost.service_period_from).days + 1
                if days <= 0 or not cost.gross_amount.is_finite():
                    raise HTTPException(422, "Invalid prior-year service period or gross amount")
                overlap = (min(end, cost.service_period_to) - max(start, cost.service_period_from)).days + 1
                amount = (cost.gross_amount * Decimal(overlap) / Decimal(days)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP)
                totals[key] = totals.get(key, Decimal("0.00")) + amount
                categories[key] = category
                sources.append({"cost": self._previous_row_payload(cost),
                    "category": self._previous_row_payload(category), "prorated_gross": format(amount, ".2f")})
            planned, reserve = [], Decimal("0.00")
            for key in sorted(categories, key=lambda k: (categories[k].sort_order, categories[k].code, str(k[0]), k[1])):
                category, amount = categories[key], totals[key]
                if amount < 0:
                    raise HTTPException(422, "Negative net prior-year category or reserve proposal")
                if category.reserve_relevant:
                    reserve += amount
                else:
                    planned.append({"category_id": str(category.id), "category_code": category.code,
                        "label": category.label, "planned_amount": self._previous_money(format(amount, ".2f")),
                        "distribution_key": "MEA", "cost_group": category.cost_group})
            reserve_value = self._previous_money(format(reserve, ".2f"))
        basis = {"basis": "confirmed_current_service_period_gross_inclusive_days_half_up_cents",
            "organization_id": str(self._org_id), "property_id": str(property_id),
            "management_type": prop.management_type, "fiscal_year_start_month": prop.fiscal_year_start_month, "source_year": plan_year - 1, "plan_year": plan_year,
            "source_accounting_scope_id": str(source.id) if source else None,
            "accounting_scope_id": str(target.id) if target else None,
            "source_scope": self._previous_row_payload(source) if source else None,
            "target_scope": self._previous_row_payload(target) if target else None,
            "period_start": start.isoformat(), "period_end": end.isoformat(),
            "sources": sources, "planned_costs": planned, "reserve_contribution_total": reserve_value}
        digest = sha256_hex(canonical_json(basis))
        preview = {"source_year": plan_year - 1, "plan_year": plan_year,
            "period_start": start.isoformat(), "period_end": end.isoformat(),
            "planned_costs": planned, "reserve_contribution_total": reserve_value, "source_digest": digest,
            "footnote": "Vorschlag aus aktuellen bestätigten Vorjahreskosten (volles WEG-Brutto), "
                "nach Leistungszeitraum tageanteilig (inklusive Tage, je Kostenposition HALF_UP auf Cent). "
                "Rücklagen separat; nur Planwerte zur ausdrücklichen Bestätigung. MEA ist der Planverteilerschlüssel; "
                "historische Verteilerschlüssel werden nicht übernommen. Keine Vollständigkeitsattestierung, "
                "keine Berechnung, keine Festschreibung und kein Beschluss."}
        provenance = {"source_year": plan_year - 1, "source_digest": digest,
            "source_cost_entry_ids": [str(cost.id) for cost, _ in rows],
            "source_accounting_scope_id": basis["source_accounting_scope_id"],
            "accounting_scope_id": basis["accounting_scope_id"], "basis": basis["basis"],
            "period_start": start.isoformat(), "period_end": end.isoformat()}
        return preview, provenance, authorized

    async def previous_preview(self, property_id, plan_year, source_accounting_scope_id=None, accounting_scope_id=None):
        preview, _, _ = await self._previous_proposal(
            property_id, plan_year, source_accounting_scope_id, accounting_scope_id)
        return preview

    async def create_from_previous(self, property_id, plan_year, expected_source_digest,
            planned_costs, reserve_contribution_total, source_accounting_scope_id=None, accounting_scope_id=None):
        preview, provenance, authorized = await self._previous_proposal(
            property_id, plan_year, source_accounting_scope_id, accounting_scope_id)
        if expected_source_digest != preview["source_digest"]:
            raise HTTPException(409, "Prior-year source basis changed; preview again before adopting")
        proposals = preview["planned_costs"]
        if len(planned_costs) != len(proposals):
            raise HTTPException(422, "Adoption must retain every proposed category exactly once and in order")
        normalized = []
        for supplied, proposal in zip(planned_costs, proposals):
            if {k: v for k, v in supplied.items() if k != "planned_amount"} != {
                k: v for k, v in proposal.items() if k != "planned_amount"
            }:
                raise HTTPException(422, "Only proposed amounts may be edited; category metadata must match")
            normalized.append({**proposal, "planned_amount": self._previous_money(supplied.get("planned_amount"))})
        reserve = self._previous_money(reserve_contribution_total)
        await self._previous_switch(authorized)
        start, end = period_starting_in(date.fromisoformat(preview["period_start"]).month, plan_year)
        plan = await self.create_draft(property_id, plan_year, start, end,
            reserve_contribution_total=Decimal(reserve), accounting_scope_id=accounting_scope_id)
        plan.snapshot_payload = {"draft_inputs": {"planned_costs": normalized,
            "reserve_contribution_total": reserve}, "previous_actuals": provenance}
        await self._plans.update(plan)
        # Re-admit live target preparation and complete source disclosure before
        # the function-scoped request transaction can commit; any denial rolls
        # back the whole draft, never a partial adoption.
        await self._previous_admit(property_id, plan_year, source_accounting_scope_id, accounting_scope_id)
        await self._previous_switch(authorized)
        await FinancialScopeService(self._session, self._org_id, self._user_id).prepare_new(plan, accounting_scope_id)
        return plan

    @staticmethod
    def _adopted_snapshot_inputs(plan, inputs: PlanInputs) -> dict:
        previous = (plan.snapshot_payload or {}).get("previous_actuals")
        if previous is None:
            return {}
        return {"previous_actuals": previous, "draft_inputs": {
            "planned_costs": [{"category_id": str(pc.category_id) if pc.category_id else None,
                "category_code": pc.category_code, "label": pc.label,
                "planned_amount": format(pc.planned_amount, ".2f"),
                "distribution_key": pc.distribution_key, "cost_group": pc.cost_group}
                for pc in inputs.planned_costs],
            "reserve_contribution_total": format(inputs.reserve_contribution_total, ".2f")}}

    # ------------------------------------------------------------------
    # create draft
    # ------------------------------------------------------------------
    async def create_draft(
        self,
        property_id: uuid.UUID,
        plan_year: int,
        period_start: date | None = None,
        period_end: date | None = None,
        reserve_contribution_total: Decimal | None = None,
        monthly_due_day: int | None = None,
        management_mandate_id: uuid.UUID | None = None,
        accounting_scope_id: uuid.UUID | None = None,
    ) -> EconomicPlan:
        scope = FinancialScopeService(self._session, self._org_id, self._user_id)
        explicit_period = period_start is not None and period_end is not None
        plan = EconomicPlan(
            organization_id=self._org_id,
            property_id=property_id,
            management_mandate_id=management_mandate_id,
            plan_year=plan_year,
            period_start=period_start if explicit_period else None,
            period_end=period_end if explicit_period else None,
            monthly_due_day=monthly_due_day,
            reserve_contribution_total=reserve_contribution_total or Decimal("0.00"),
            status="draft",
            engine_version=ENGINE_VERSION,
        )
        # Admit pause mode, parents and scope before resolving optional dates.
        await scope.prepare_new(plan, accounting_scope_id)
        plan_year = period_start.year if explicit_period else plan_year
        if not 1 <= plan_year <= 9998:
            raise HTTPException(422, "Plan year must be between 1 and 9998")
        prop = await PropertyRepository(self._session, self._org_id).get_by_id(property_id)
        if prop is None and not explicit_period:
            raise HTTPException(422, "Abrechnungszeitraum angeben: Beginn und Ende")
        if prop is not None:
            property_start, property_end = period_starting_in(prop.fiscal_year_start_month, plan_year)
            period_start = period_start if period_start is not None else property_start
            period_end = period_end if period_end is not None else property_end
        try:
            validate_settlement_period(period_start, period_end)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        plan.plan_year = period_start.year
        plan.period_start, plan.period_end = period_start, period_end
        if not explicit_period:
            # Defaults must also fit the admitted accounting scope.
            await scope.prepare_new(plan, accounting_scope_id)
        if await self._plans.has_conflicting_period(plan):
            raise HTTPException(409, "Der Zeitraum überschneidet einen finalisierten Wirtschaftsplan dieses Objekts.")
        if prop is not None and not (property_start <= period_start <= period_end <= property_end):
            previous = await self._plans.last_finalized_before(property_id, period_start)
            if (previous is None or period_start != previous.period_end + timedelta(days=1)
                    or period_end != property_start - timedelta(days=1)):
                raise HTTPException(422, "Der Zeitraum muss im Objektzeitraum liegen oder die Übergangslücke vollständig schließen.")
        plan = await self._plans.create(plan)
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="economic_plan",
                entity_id=plan.id,
                action="economic_plan.created",
                payload_diff={"plan_year": plan.plan_year, "period": f"{period_start} - {period_end}"},
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
        computation_basis = await begin_computation(self._session, self._org_id, plan, 'compute')
        inp, result = await self._compute_engine(plan, inputs)

        await self._lines.delete_for_plan(plan.id)
        await self._write_lines(plan, result)

        plan.snapshot_payload = {**build_plan_snapshot_payload(inp, result),
            **self._adopted_snapshot_inputs(plan, inputs), "financial_input_coverage": self._coverage}
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
        await record_computation(self._session, self._org_id, self._user_id, plan, 'compute', computation_basis)
        return plan, result

    # ------------------------------------------------------------------
    # finalize (atomic snapshot + hash)
    # ------------------------------------------------------------------
    async def finalize(
        self, plan_id: uuid.UUID, inputs: PlanInputs
    ) -> EconomicPlan:
        plan = await self._get_draft_or_404(plan_id)
        computation_basis = await begin_computation(self._session, self._org_id, plan, 'finalize')
        inp, result = await self._compute_engine(plan, inputs)

        payload = {**build_plan_snapshot_payload(inp, result), **self._adopted_snapshot_inputs(plan, inputs)}
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
        await record_computation(self._session, self._org_id, self._user_id, plan, 'finalize', computation_basis)
        return plan

    async def create_correction_run(self, plan_id: uuid.UUID) -> EconomicPlan:
        scope = FinancialScopeService(self._session, self._org_id, self._user_id)
        original = await self._plans.get_by_id(plan_id)
        if original is None:
            raise HTTPException(404, "Financial original not found")
        await scope.enter(original, capability=ScopeCapability.APPROVE)
        await scope.lock_inputs()
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
        await scope.prepare_new(new_plan)
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
        scope = FinancialScopeService(self._session, self._org_id, self._user_id)
        plan = await self._plans.get_by_id(plan_id)
        if plan is None:
            raise HTTPException(404, "Financial draft not found")
        await scope.enter(plan)
        await scope.lock_inputs()
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
