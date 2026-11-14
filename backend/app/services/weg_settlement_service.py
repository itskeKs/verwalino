"""WEG-Jahresabrechnung + Vermoegensbericht service (m4 section 10.3).

Both are settlement_run run_types (E4-1, same hull as BK_MIETER). This service
adds the WEG compute/finalize paths alongside the M3 BK path:

  - WEG_JAHRESABRECHNUNG: ONE objektweite run per WEG (E4-4); per-owner lines via
    addressee_owner_profile_id. The SOLL advances come from the linked
    economic_plan (its OWNER_ADVANCE lines, aggregated per owner) and are FROZEN
    into the settlement snapshot (m4 section 1.5). [F4-1] economic_plan_id is
    required for a WEG run in M4 (provisional; fachlich abzunehmen).
  - VERMOEGENSBERICHT: reserve development automatic + manual bank/receivables/
    payables (ADR-0008).

Boundary discipline (SDD section 16): load ORM -> map to engine dataclasses ->
PURE engine -> persist (settlement_line + snapshot + hash). The read-edge for
ownership_history uses db_exclusive_to_engine_inclusive (ADR-0006), exactly as
the M3 BK path.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import NoReturn

from fastapi import HTTPException

from app.services.document_addressees import document_addressees
from app.engine.periods import validate_settlement_period
from app.core.rbac import ScopeCapability
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.temporal import db_exclusive_to_engine_inclusive
from app.engine.canonical import canonical_json, sha256_hex
from app.engine.rounding import DistributionError
from app.engine.reserve_ledger import planned_reserve_per_owner
from app.engine.snapshot import (
    build_asset_report_payload,
    build_weg_snapshot_payload,
)
from app.services.render_names import (
    DISPLAY_NAMES_KEY,
    freeze_block,
    resolve_vermoegensbericht_values,
    resolve_weg_values,
)
from app.engine.types import (
    AssetItemInput,
    BankBalanceInput,
    OwnerAdvanceInput,
    OwnerPeriodInput,
    PeriodOverlapError,
    PropertyInput,
    ReserveInput,
    UnitInput,
    VermoegensberichtInput,
    WegCostPositionInput,
    WegResult,
    WegSettlementInput,
)
from app.engine.vermoegensbericht import AssetReportResult, build_vermoegensbericht
from app.engine.weg import WegValidationError, compute_weg_jahresabrechnung
from app.models.audit_event import AuditEvent
from app.services.financial_scope_service import FinancialScopeService
from app.services.financial_computation_service import begin_computation, record_computation
from app.services.financial_input_policy import FinancialInputPolicy, incomplete
from app.models.settlement_line import SettlementLine
from app.models.settlement_run import SettlementRun
from app.repositories.audit_repo import AuditRepository
from app.repositories.coverage_declaration_repo import CoverageDeclarationRepository
from app.repositories.bank_account_repo import BankAccountRepository
from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
from app.repositories.economic_plan_repo import EconomicPlanRepository
from app.repositories.settlement_repo import (
    SettlementLineRepository,
    SettlementRunRepository,
)

from app.services.reserve_ledger_service import ReserveLedgerService
from app.services.payment_service import dated_plan_soll

ENGINE_VERSION = "m4.8.0"

DELETE_MESSAGES = {
    'SETTLEMENT_DELETE_SOURCE_ACCESS_REQUIRED': 'Zum Löschen dieses Entwurfs benötigen Sie Zugriff auf alle Finanzquellen des Objekts.',
    'SETTLEMENT_DELETE_NOT_DRAFT': 'Nur nicht ersetzte Entwürfe können gelöscht werden.',
    'SETTLEMENT_DELETE_HAS_BOOKINGS': 'Entwürfe mit Buchungen können nicht gelöscht werden.',
    'SETTLEMENT_DELETE_HAS_LEDGER': 'Entwürfe mit Rücklagenbuch-Einträgen können nicht gelöscht werden.',
    'SETTLEMENT_DELETE_SCOPE_DEPENDENCY': 'Dieser Entwurf besitzt eine unveränderbare Zuordnung zu einem Abrechnungskreis und kann nicht gelöscht werden.',
    'SETTLEMENT_DELETE_HAS_EVIDENCE': 'Dieser Entwurf wird von aufbewahrungspflichtigen Nachweisen verwendet und kann nicht gelöscht werden.',
}


@dataclass
class ManualOwnerAdvance:
    """A manually-captured actually-paid advance of one owner (nachrichtlich)."""

    owner_profile_id: uuid.UUID
    paid_total: Decimal


@dataclass
class WegComputeExtras:
    """Structured extra inputs for one WEG compute (m4 section 2.4 / 3.3)."""

    reserve_opening_balance: Decimal | None = None
    reserve_withdrawals_total: Decimal | None = None
    paid_advances: list[ManualOwnerAdvance] | None = None
    reserve_override_reason: str = ""


@dataclass
class BankBalanceEntry:
    bank_account_id: uuid.UUID
    label: str
    account_type: str
    balance: Decimal


@dataclass
class AssetItemEntry:
    label: str
    amount: Decimal


@dataclass
class AssetReportInputs:
    """Report inputs, with optional reasoned reserve target overrides."""

    reserve_opening_balance: Decimal | None = None
    reserve_withdrawals_total: Decimal | None = None
    reserve_contributions_total: Decimal | None = None
    reserve_override_reason: str = ""
    bank_balances: list[BankBalanceEntry] | None = None
    receivables: list[AssetItemEntry] | None = None
    payables: list[AssetItemEntry] | None = None


class WegSettlementService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID):
        self._session = session
        self._org_id = org_id
        self._user_id = user_id
        self._runs = SettlementRunRepository(session, org_id)
        self._lines = SettlementLineRepository(session, org_id)
        self._plans = EconomicPlanRepository(session, org_id)
        self._audit = AuditRepository(session, org_id)

    async def delete_restriction(self, run_id: uuid.UUID) -> dict[str, str] | None:
        code = await self._runs.weg_delete_reason(run_id)
        if code == 'NOT_FOUND':
            raise HTTPException(404, 'WEG settlement run not found')
        return {'code': code, 'message': DELETE_MESSAGES[code]} if code is not None else None

    async def delete_empty_draft(self, run_id: uuid.UUID, *, asset_report=False) -> None:
        scope = FinancialScopeService(self._session, self._org_id, self._user_id)
        await scope.lock_inputs()
        run = await self._runs.get_by_id_for_update(run_id)
        if run is None or run.run_type not in ('WEG_JAHRESABRECHNUNG', 'VERMOEGENSBERICHT') or (
            asset_report and run.run_type != 'VERMOEGENSBERICHT'
        ):
            raise HTTPException(404, 'WEG settlement run not found')
        await scope.enter(run, capability=ScopeCapability.PREPARE)
        restriction = await self.delete_restriction(run_id)
        if restriction is not None:
            raise HTTPException(409, restriction)
        await self._runs.delete(run)
        await self._audit.create(AuditEvent(actor_user_id=self._user_id, entity_type='settlement_run',
            entity_id=run_id, action='settlement.draft.deleted', payload_diff={
                'run_type': run.run_type, 'property_id': str(run.property_id),
                'period_start': run.period_start.isoformat(), 'period_end': run.period_end.isoformat()}))

    async def _plan_transition_run(self, run_id):
        scope = FinancialScopeService(self._session, self._org_id, self._user_id)
        await scope.lock_inputs()
        run = await self._runs.get_by_id_for_update(run_id)
        if run is None or run.run_type != 'WEG_JAHRESABRECHNUNG':
            raise HTTPException(404, 'WEG settlement not found')
        scope_id = await scope.enter(run, capability=ScopeCapability.PREPARE, computation=True)
        if run.status != 'draft' or run.superseded_by_id is not None:
            raise HTTPException(409, 'Select a plan successor only for a current draft')
        return run, scope, scope_id

    async def _current_plan_successor(self, run, scope, scope_id, original_id):
        """Every frozen hop retains the original obligation context and access."""
        context_fields = ('property_id', 'management_mandate_id', 'period_start',
                          'period_end', 'plan_year', 'monthly_due_day')
        original_context = None
        seen = set()
        target = original_id
        while target is not None and target not in seen and len(seen) < 100:
            seen.add(target)
            plan = await self._plans.get_by_id(target)
            if plan is None:
                raise HTTPException(404, 'Complete plan successor chain not accessible')
            plan_scope = await scope.enter(plan, computation=True)
            if plan_scope != scope_id:
                raise HTTPException(409, 'Plan successor must retain accounting scope')
            context = tuple(getattr(plan, field) for field in context_fields)
            if (plan.status != 'finalized' or plan.property_id != run.property_id
                    or plan.management_mandate_id != run.management_mandate_id
                    or plan.period_start != run.period_start or plan.period_end != run.period_end
                    or original_context is not None and context != original_context):
                raise HTTPException(409, 'Finalized same-context plan successor required')
            original_context = context
            if plan.superseded_by_id is None:
                return plan
            target = plan.superseded_by_id
        raise HTTPException(409, 'Complete bounded plan successor chain required')

    async def plan_successor_preview(self, run_id):
        run, scope, scope_id = await self._plan_transition_run(run_id)
        plan = await self._current_plan_successor(run, scope, scope_id, run.economic_plan_id)
        return {'run_id': run.id, 'current_plan_id': run.economic_plan_id,
                'successor_id': plan.id if plan.id != run.economic_plan_id else None,
                'plan_year': plan.plan_year, 'period_start': plan.period_start,
                'period_end': plan.period_end, 'monthly_due_day': plan.monthly_due_day}

    async def select_plan_successor(self, run_id, expected_plan_id, successor_id, reason):
        run, scope, scope_id = await self._plan_transition_run(run_id)
        if run.economic_plan_id not in (expected_plan_id, successor_id):
            raise HTTPException(409, 'Linked plan changed; review the current successor')
        plan = await self._current_plan_successor(run, scope, scope_id, expected_plan_id)
        if plan.id != successor_id or expected_plan_id == successor_id:
            raise HTTPException(409, 'Select the reviewed current plan successor')
        if run.economic_plan_id == successor_id:
            # A lost reply must not erase a computation performed after selection.
            return run
        run.economic_plan_id = successor_id
        await self._lines.delete_for_run(run.id)
        run.snapshot_payload = None
        run.snapshot_sha256 = None
        run.total_cost = None
        run.balance = None
        await self._runs.update(run)
        await self._audit.create(AuditEvent(actor_user_id=self._user_id,
            entity_type='settlement_run', entity_id=run.id,
            action='settlement.plan_successor_selected', payload_diff={
                'previous_plan_id': str(expected_plan_id), 'economic_plan_id': str(successor_id),
                'reason': reason}))
        return run

    # ==================================================================
    # WEG-Jahresabrechnung
    # ==================================================================
    async def create_weg_draft(
        self,
        property_id: uuid.UUID,
        economic_plan_id: uuid.UUID,
        period_start: date,
        period_end: date,
        management_mandate_id: uuid.UUID | None = None,
        accounting_scope_id: uuid.UUID | None = None,
    ) -> SettlementRun:
        """Create a draft objektweite WEG_JAHRESABRECHNUNG run (E4-4).

        [F4-1] economic_plan_id is required (the Soll source). Validates the plan
        exists and belongs to the same property.
        """
        plan = await self._plans.get_by_id(economic_plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Economic plan not found")
        await FinancialScopeService(self._session, self._org_id, self._user_id).enter(plan, capability=ScopeCapability.PREPARE)
        if plan.property_id != property_id:
            raise HTTPException(
                status_code=422,
                detail="Economic plan belongs to a different property",
            )

        run = SettlementRun(
            organization_id=self._org_id,
            property_id=property_id,
            management_mandate_id=management_mandate_id,
            run_type="WEG_JAHRESABRECHNUNG",
            period_start=period_start,
            period_end=period_end,
            # objektweit: unit/lease/tenant NULL (E4-4).
            unit_id=None,
            lease_agreement_id=None,
            tenant_profile_id=None,
            economic_plan_id=economic_plan_id,
            advance_payments_source="MANUAL",
            status="draft",
            engine_version=ENGINE_VERSION,
        )
        sphere = await FinancialScopeService(self._session, self._org_id, self._user_id).prepare_new(run, accounting_scope_id)
        await ReserveLedgerService(self._session, self._org_id, self._user_id).require_period_creation(
            run, sphere.id if sphere is not None else None)
        try:
            validate_settlement_period(period_start, period_end)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if await self._runs.has_conflicting_period(run, sphere.id if sphere is not None else None):
            raise HTTPException(409, {
                "code": "settlement_period_conflict",
                "message": "Abrechnungszeitraum überschneidet eine finalisierte WEG-Abrechnung dieses Objekts.",
            })
        run = await self._runs.create(run)
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id, entity_type="settlement_run",
                entity_id=run.id, action="settlement.weg.created",
                payload_diff={"economic_plan_id": str(economic_plan_id),
                              "period": f"{period_start} - {period_end}"},
            )
        )
        return run

    async def compute_weg(
        self, run_id: uuid.UUID, extras: WegComputeExtras | None = None
    ) -> tuple[SettlementRun, WegResult]:
        run = await self._get_weg_draft_or_404(run_id)
        computation_basis = await begin_computation(self._session, self._org_id, run, 'compute')
        extras = extras or WegComputeExtras()
        coverage = await FinancialInputPolicy(self._session, self._org_id, self._user_id).require(run, "settlement", extras)
        inp, result = await self._compute_weg_engine(run, extras)

        await self._lines.delete_for_run(run.id)
        await self._write_weg_lines(run, result)

        run.engine_version = ENGINE_VERSION
        run.snapshot_payload = {**build_weg_snapshot_payload(inp, result), "financial_input_coverage": coverage}
        # objektweit totals: total_cost = Σ Ist (excl. reserve); balance = Σ Spitze.
        run.total_cost = result.cost_total
        run.balance = result.abrechnungsspitze_total
        await self._runs.update(run)

        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id, entity_type="settlement_run",
                entity_id=run.id, action="settlement.weg.computed",
                payload_diff={"cost_total": str(result.cost_total),
                              "abrechnungsspitze_total": str(result.abrechnungsspitze_total)},
            )
        )
        await record_computation(self._session, self._org_id, self._user_id, run, 'compute', computation_basis)
        return run, result

    async def finalize_weg(
        self, run_id: uuid.UUID, extras: WegComputeExtras | None = None
    ) -> SettlementRun:
        run = await self._get_weg_draft_or_404(run_id)
        scope_id = await FinancialScopeService(self._session, self._org_id, self._user_id).enter(run)
        computation_basis = await begin_computation(self._session, self._org_id, run, 'finalize')
        if await self._runs.has_conflicting_period(run, scope_id):
            raise HTTPException(409, {
                "code": "settlement_period_conflict",
                "message": "Abrechnungszeitraum überschneidet eine finalisierte WEG-Abrechnung dieses Objekts.",
            })
        extras = extras or WegComputeExtras()
        coverage = await FinancialInputPolicy(self._session, self._org_id, self._user_id).require(run, "settlement", extras)
        inp, result = await self._compute_weg_engine(run, extras)

        payload = build_weg_snapshot_payload(inp, result)
        payload["financial_input_coverage"] = coverage
        # Eigentümer-Anzeige-Namen einfrieren (ADR-0012), vor dem Hash -- s.
        # settlement_service.finalize. Noch OLD.status='draft', Schreiben erlaubt.
        payload[DISPLAY_NAMES_KEY] = freeze_block(
            await resolve_weg_values(self._session, self._org_id, run)
        )
        sha = sha256_hex(canonical_json(payload))

        await self._lines.delete_for_run(run.id)
        await self._write_weg_lines(run, result)

        now = datetime.now(timezone.utc)
        run.engine_version = ENGINE_VERSION
        run.snapshot_payload = payload
        run.snapshot_sha256 = sha
        run.total_cost = result.cost_total
        run.balance = result.abrechnungsspitze_total
        run.status = "finalized"
        run.finalized_at = now
        run.finalized_by = self._user_id
        await self._runs.update(run)

        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id, entity_type="settlement_run",
                entity_id=run.id, action="settlement.weg.finalized",
                payload_diff={"snapshot_sha256": sha,
                              "abrechnungsspitze_total": str(result.abrechnungsspitze_total)},
            )
        )
        await record_computation(self._session, self._org_id, self._user_id, run, 'finalize', computation_basis)
        return run

    # ==================================================================
    # Vermoegensbericht
    # ==================================================================
    async def create_asset_report_draft(
        self,
        property_id: uuid.UUID,
        period_start: date,
        period_end: date,
        management_mandate_id: uuid.UUID | None = None,
        accounting_scope_id: uuid.UUID | None = None,
    ) -> SettlementRun:
        run = SettlementRun(
            organization_id=self._org_id,
            property_id=property_id,
            management_mandate_id=management_mandate_id,
            run_type="VERMOEGENSBERICHT",
            period_start=period_start,
            period_end=period_end,
            advance_payments_source="MANUAL",
            status="draft",
            engine_version=ENGINE_VERSION,
        )
        sphere = await FinancialScopeService(self._session, self._org_id, self._user_id).prepare_new(run, accounting_scope_id)
        await ReserveLedgerService(self._session, self._org_id, self._user_id).require_period_creation(
            run, sphere.id if sphere is not None else None)
        run = await self._runs.create(run)
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id, entity_type="settlement_run",
                entity_id=run.id, action="asset_report.created",
                payload_diff={"period": f"{period_start} - {period_end}"},
            )
        )
        return run

    async def compute_asset_report(
        self, run_id: uuid.UUID, inputs: AssetReportInputs
    ) -> tuple[SettlementRun, AssetReportResult]:
        run = await self._get_report_draft_or_404(run_id)
        await self._resolve_report_bank_accounts(inputs)
        computation_basis = await begin_computation(self._session, self._org_id, run, 'compute')
        coverage = await FinancialInputPolicy(self._session, self._org_id, self._user_id).require(run, "settlement", inputs)
        inp, result = await self._compute_report_engine(run, inputs)

        await self._lines.delete_for_run(run.id)
        self._write_report_lines(run, result)

        run.engine_version = ENGINE_VERSION
        run.snapshot_payload = {**build_asset_report_payload(inp, result), "financial_input_coverage": coverage,
            "addressees": await document_addressees(self._session, self._org_id, run.property_id, run.period_end)}
        await self._runs.update(run)
        await self._session.flush()
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id, entity_type="settlement_run",
                entity_id=run.id, action="asset_report.computed",
                payload_diff={"reserve_closing": str(result.reserve.closing_balance)},
            )
        )
        await record_computation(self._session, self._org_id, self._user_id, run, 'compute', computation_basis)
        return run, result

    async def finalize_asset_report(
        self, run_id: uuid.UUID, inputs: AssetReportInputs
    ) -> SettlementRun:
        run = await self._get_report_draft_or_404(run_id)
        await self._resolve_report_bank_accounts(inputs)
        computation_basis = await begin_computation(self._session, self._org_id, run, 'finalize')
        coverage = await FinancialInputPolicy(self._session, self._org_id, self._user_id).require(run, "settlement", inputs)
        inp, result = await self._compute_report_engine(run, inputs)

        payload = build_asset_report_payload(inp, result)
        payload["addressees"] = await document_addressees(self._session, self._org_id, run.property_id, run.period_end)
        payload["financial_input_coverage"] = coverage
        # Freeze display data and dated recipients before the integrity hash.
        payload[DISPLAY_NAMES_KEY] = freeze_block(
            await resolve_vermoegensbericht_values(self._session, self._org_id, run)
        )
        sha = sha256_hex(canonical_json(payload))

        await self._lines.delete_for_run(run.id)
        self._write_report_lines(run, result)

        now = datetime.now(timezone.utc)
        run.engine_version = ENGINE_VERSION
        run.snapshot_payload = payload
        run.snapshot_sha256 = sha
        run.status = "finalized"
        run.finalized_at = now
        run.finalized_by = self._user_id
        await self._runs.update(run)
        await self._session.flush()

        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id, entity_type="settlement_run",
                entity_id=run.id, action="asset_report.finalized",
                payload_diff={"snapshot_sha256": sha,
                              "reserve_closing": str(result.reserve.closing_balance)},
            )
        )
        await record_computation(self._session, self._org_id, self._user_id, run, 'finalize', computation_basis)
        return run

    # ------------------------------------------------------------------
    # internals -- WEG
    # ------------------------------------------------------------------
    async def _get_weg_draft_or_404(self, run_id: uuid.UUID) -> SettlementRun:
        scope = FinancialScopeService(self._session, self._org_id, self._user_id)
        run = await self._runs.get_by_id(run_id)
        if run is None:
            raise HTTPException(404, "Financial draft not found")
        await scope.enter(run)
        await scope.lock_inputs()
        run = await self._runs.get_by_id_for_update(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Settlement run not found")
        if run.run_type != "WEG_JAHRESABRECHNUNG":
            raise HTTPException(status_code=422, detail="Run is not a WEG-Jahresabrechnung")
        if run.status != "draft":
            raise HTTPException(
                status_code=409,
                detail=f"Run is '{run.status}'; only draft runs can be (re)computed/finalized",
            )
        return run

    async def _get_report_draft_or_404(self, run_id: uuid.UUID) -> SettlementRun:
        scope = FinancialScopeService(self._session, self._org_id, self._user_id)
        run = await self._runs.get_by_id(run_id)
        if run is None:
            raise HTTPException(404, "Financial draft not found")
        await scope.enter(run)
        await scope.lock_inputs()
        run = await self._runs.get_by_id_for_update(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Settlement run not found")
        if run.run_type != "VERMOEGENSBERICHT":
            raise HTTPException(status_code=422, detail="Run is not a Vermoegensbericht")
        if run.status != "draft":
            raise HTTPException(
                status_code=409,
                detail=f"Run is '{run.status}'; only draft runs can be (re)computed/finalized",
            )
        return run

    async def _compute_weg_engine(
        self, run: SettlementRun, extras: WegComputeExtras
    ) -> tuple[WegSettlementInput, WegResult]:
        inp = await self._load_weg_input(run, extras)
        try:
            result = compute_weg_jahresabrechnung(inp)
        except (WegValidationError, DistributionError, PeriodOverlapError) as exc:
            raise HTTPException(status_code=422, detail=f"WEG-Berechnung nicht moeglich: {exc}")
        return inp, result

    def _plan_reserve_per_owner(self, rows: list[dict]) -> tuple[OwnerAdvanceInput, ...]:
        """Sum canonical reserve plan rows without another owner distribution (#401)."""
        return tuple(OwnerAdvanceInput(owner_profile_id=uuid.UUID(oid), plan_advance_total=amount)
                     for oid, amount in sorted(planned_reserve_per_owner(rows).items()))

    async def reject_paid_amount(self, run_id: uuid.UUID, owner_id: uuid.UUID, *, missing: bool, capability=ScopeCapability.PREPARE) -> NoReturn:
        """Resolve an input error only within the authorized draft and owner roster."""
        run = await self._get_weg_draft_or_404(run_id)
        await FinancialScopeService(self._session, self._org_id, self._user_id).enter(
            run, capability=capability, computation=True)
        owners = await CoverageDeclarationRepository(self._session, self._org_id).owner_ids(run)
        if str(owner_id) not in owners:
            raise HTTPException(404, 'Eigentümer der Abrechnung nicht gefunden.')
        name = 'den Eigentümer'
        for owner, person in await self._runs.owner_profiles_for_property(run.property_id):
            if owner.id == owner_id and person is not None:
                name = ' '.join(part for part in (person.first_name, person.last_name) if part) or name
                break
        detail = {
            'code': 'financial_inputs_incomplete' if missing else 'invalid_decimal',
            'field': 'paid_total', 'owner_profile_id': str(owner_id),
            'message': (f'Bitte geben Sie das tatsächlich gezahlte Hausgeld für {name} an, auch bei 0,00 €.'
                        if missing else f'Bitte geben Sie einen gültigen Hausgeldbetrag für {name} an.'),
        }
        if missing:
            detail['blockers'] = [{'code': 'owner_payment_coverage_missing', 'component': 'paid_advances'}]
        raise HTTPException(422, detail)

    async def derive_paid_advances(
        self, economic_plan_id: uuid.UUID, period_year: int,
        period_start: date | None = None, period_end: date | None = None,
    ) -> list["ManualOwnerAdvance"]:
        """Read net booked Hausgeld by assigned owner, object and period.

        Reuse the existing advance request shape for the coverage workflow and
        the engine. Plan owners and owners active within the period both belong
        to the roster, including a successor with no OWNER_ADVANCE plan line.
        These payments affect only account_balance/payment_amount; the Spitze
        remains actual costs plus planned reserve Soll minus the dated plan advance.
        """
        from app.services.payment_service import PaymentService

        plan = await self._plans.get_by_id(economic_plan_id)
        if plan is None:
            raise HTTPException(404, "Economic plan not found")
        await FinancialScopeService(self._session, self._org_id, self._user_id).enter(plan, computation=True)
        start = period_start if period_start is not None else plan.period_start
        end = period_end if period_end is not None else plan.period_end
        if not start.year <= period_year <= end.year:
            raise HTTPException(422, "Reference year must belong to the settlement period")
        pay_svc = PaymentService(self._session, self._org_id, self._user_id)
        subject = SimpleNamespace(property_id=plan.property_id, period_start=start, period_end=end,
                                  economic_plan_id=plan.id, run_type='WEG_JAHRESABRECHNUNG')
        owner_ids = await CoverageDeclarationRepository(self._session, self._org_id).owner_ids(subject)
        # Decision 1(b) caps reserve coverage with the same dated Soll the settlement shows.
        shares = await pay_svc.owner_plan_charge_totals(plan, start, end)
        plan_soll = dated_plan_soll(shares)
        result: list[ManualOwnerAdvance] = []
        for owner_id in sorted(owner_ids, key=str):
            total = await pay_svc.derive_hausgeld_total_for_period(
                uuid.UUID(owner_id), start, end, property_id=plan.property_id,
                plan_soll=plan_soll.get(uuid.UUID(owner_id)))
            result.append(
                ManualOwnerAdvance(owner_profile_id=uuid.UUID(owner_id), paid_total=total)
            )
        return result

    async def _load_weg_input(
        self, run: SettlementRun, extras: WegComputeExtras
    ) -> WegSettlementInput:
        from app.repositories.property_repo import PropertyRepository

        if run.economic_plan_id is None:
            # [F4-1] M4: a WEG run requires a linked plan (the Soll source).
            raise HTTPException(
                status_code=422,
                detail="WEG-Jahresabrechnung requires a linked economic_plan (Soll-Quelle, [F4-1])",
            )

        prop_repo = PropertyRepository(self._session, self._org_id)
        prop = await prop_repo.get_by_id(run.property_id)
        if prop is None:
            raise HTTPException(status_code=404, detail="Property not found")

        units = await self._runs.units_for_property(run.property_id)
        ownership = await self._runs.ownership_for_property(run.property_id)
        entries = await self._runs.active_cost_entries_for_period(
            run.property_id, run.period_start, run.period_end
        )

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

        funded = await ReserveLedgerRepository(self._session, self._org_id).funded_amounts(
            [entry.id for entry, category in entries if category is not None and not category.reserve_relevant]
        )
        position_inputs = []
        for entry, category in entries:
            if category is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"Cost entry {entry.id} has no cost_category; cannot derive "
                           f"owner cost_group for the WEG run (escalate).",
                )
            # The owner sphere reads owner_distribution_key (m4 section 4.2).
            owner_key = entry.owner_distribution_key or category.owner_distribution_key_default
            position_inputs.append(
                WegCostPositionInput(
                    cost_entry_id=entry.id,
                    category_id=entry.cost_category_id,
                    category_code=category.code,
                    label=category.label,
                    gross_amount=entry.gross_amount,
                    service_period_from=entry.service_period_from,
                    service_period_to=entry.service_period_to,
                    owner_distribution_key=owner_key,
                    cost_group=category.cost_group,
                    reserve_relevant=category.reserve_relevant,
                    reserve_funded_amount=funded.get(entry.id, Decimal('0.00')),
                )
            )

        plan = await self._plans.get_by_id(run.economic_plan_id)
        if plan is None:
            raise HTTPException(404, "Economic plan not found")
        reserves = ReserveLedgerService(self._session, self._org_id, self._user_id)
        ledger = await reserves.read(run)
        if not ledger['frozen']:
            await reserves.require_contribution_backing(run)
        if any(row['kind'] == 'UNASSIGNED' for row in ledger['entries']):
            raise HTTPException(422, {'code': 'RESERVE_ASSIGNMENT_REQUIRED', 'message': 'Bitte klären Sie alle Zahlungen ohne Planbezug vor der Berechnung.'})
        if ledger.get('unlinked_payment_ids'):
            raise HTTPException(422, {'code': 'RESERVE_PAYOUT_EVIDENCE_REQUIRED', 'message': 'Bitte verknüpfen Sie alle Rücklagenentnahmen mit Kostenbeleg und Beschluss.'})
        paid_reserve = {}
        for row in ledger['entries']:
            if row['kind'] == 'CONTRIBUTION' and row.get('owner_profile_id'):
                oid = uuid.UUID(row['owner_profile_id'])
                paid_reserve[oid] = paid_reserve.get(oid, Decimal('0.00')) + Decimal(row['amount'])
        expected_owners = await CoverageDeclarationRepository(self._session, self._org_id).owner_ids(run)
        # The Soll follows dated ownership: the Hausgeld ledger's month rule
        # (owner_plan_charge_totals) decides who owes which plan month (#402).
        from app.services.payment_service import PaymentService
        pay_svc = PaymentService(self._session, self._org_id, self._user_id)
        shares = await pay_svc.owner_plan_charge_totals(plan, run.period_start, run.period_end)
        plan_advances = [
            OwnerAdvanceInput(owner_profile_id=owner_id, plan_advance_total=amount)
            for owner_id, amount in sorted(dated_plan_soll(shares).items(), key=lambda item: str(item[0]))
        ]
        provided_owners = [str(p.owner_profile_id) for p in extras.paid_advances]
        if set(provided_owners) != expected_owners or len(provided_owners) != len(set(provided_owners)):
            incomplete("owner_payment_coverage_missing", "paid_advances")
        # The payment amount reads booked ledger truth, never a manual declaration.
        booked = await self.derive_paid_advances(run.economic_plan_id, run.period_start.year, run.period_start, run.period_end)
        paid = tuple(
            OwnerAdvanceInput(owner_profile_id=p.owner_profile_id, plan_advance_total=p.paid_total)
            for p in booked
        )

        return WegSettlementInput(
            period_start=run.period_start,
            period_end=run.period_end,
            property=PropertyInput(id=prop.id, name=prop.name, management_type=prop.management_type),
            units=unit_inputs,
            owner_periods=owner_inputs,
            cost_positions=tuple(p for p in position_inputs if not p.reserve_relevant),
            plan_advances=tuple(plan_advances),
            plan_reserve=self._plan_reserve_per_owner(await reserves.repo.plan_rows(plan)),
            reserve=ReserveInput(
                opening_balance=Decimal(ledger['opening_balance']),
                withdrawals_total=Decimal(ledger['withdrawals_total']),
                contributions_total=Decimal(ledger['contributions_total']),
                overrides_total=Decimal(ledger['overrides_total']),
                late_cash_total=Decimal(ledger.get('late_cash_total', '0.00')),
            ),
            paid_advances=paid,
            reserve_ledger=ledger,
            reserve_paid_contributions=tuple(OwnerAdvanceInput(owner_profile_id=oid, plan_advance_total=amt)
                                             for oid, amt in paid_reserve.items()),
            economic_plan_id=run.economic_plan_id,
            engine_version=ENGINE_VERSION,
        )

    async def _write_weg_lines(self, run: SettlementRun, result: WegResult) -> None:
        sort = 0
        for owner in sorted(result.owners, key=lambda o: str(o.owner_profile_id)):
            # Per-cost-position lines for this owner.
            for ln in owner.cost_lines:
                self._session.add(self._line(run, owner.owner_profile_id, ln.line_type, ln.label,
                                             ln.amount, sort, cost_category_id=ln.category_id,
                                             cost_category_code=ln.category_code,
                                             distribution_key=ln.distribution_key,
                                             total_amount=ln.total_amount,
                                             base_total=ln.distribution_base_total,
                                             base_share=ln.distribution_base_share,
                                             time_days=ln.time_share_days,
                                             time_total=ln.time_share_total_days,
                                             note=ln.note))
                sort += 1
            # Subtotals (umlagefaehig / nicht / reserve) + cost total + plan + Spitze.
            self._session.add(self._line(run, owner.owner_profile_id, "SUBTOTAL",
                                         "Summe umlagefaehige Kosten", owner.subtotal_umlagefaehig,
                                         sort, note="umlagefaehig + gemischt")); sort += 1
            self._session.add(self._line(run, owner.owner_profile_id, "SUBTOTAL",
                                         "Summe nicht umlagefaehige Kosten",
                                         owner.subtotal_nicht_umlagefaehig, sort,
                                         note="nicht umlagefaehig")); sort += 1
            self._session.add(self._line(run, owner.owner_profile_id, "OWNER_COST_TOTAL",
                                         "Summe anteilige Kosten (Ist)", owner.owner_cost_total,
                                         sort, note="umlagefaehig + nicht umlagefaehig (ohne Ruecklage)")); sort += 1
            if owner.plan_reserve_soll != 0:
                self._session.add(self._line(run, owner.owner_profile_id, "RESERVE_SOLL",
                                             "Ruecklagenzufuehrung laut Wirtschaftsplan (Soll)",
                                             owner.plan_reserve_soll, sort,
                                             note="Soll-Position laut Wirtschaftsplan")); sort += 1
            self._session.add(self._line(run, owner.owner_profile_id, "PLAN_ADVANCE",
                                         "Soll-Vorschuesse (Wirtschaftsplan)", owner.plan_advance,
                                         sort, note="beschlossenes Soll")); sort += 1
            self._session.add(self._line(run, owner.owner_profile_id, "ABRECHNUNGSSPITZE",
                                         "Abrechnungsspitze (Nachschuss/Guthaben)" ,
                                         owner.abrechnungsspitze, sort,
                                         note="Kosten + Ruecklage-Soll - Vorschuss-Soll (WEMoG, beschlussrelevant)")); sort += 1
            if owner.subtotal_reserve != Decimal("0.00"):
                self._session.add(self._line(run, owner.owner_profile_id, "RESERVE_CONTRIBUTION",
                                             "Zufuehrung Erhaltungsruecklage (Ihr Anteil)",
                                             owner.subtotal_reserve, sort,
                                             note=owner.reserve_notice or "Vermögensbildung (keine Kostenumlage)")); sort += 1
            if owner.paid_advances is not None:
                self._session.add(self._line(run, owner.owner_profile_id, "ADVANCE_PAYMENT",
                                             "(nachrichtlich) geleistete Vorschuesse",
                                             owner.paid_advances, sort,
                                             note="Zahlungsstand, nicht beschlussrelevant")); sort += 1

        # Object-wide reserve development lines (addressee NULL).
        dev = result.reserve
        self._session.add(self._line(run, None, "RESERVE_OPENING",
                                     "Erhaltungsruecklage Anfangsbestand", dev.opening_balance, sort,
                                     note="Anfangsbestand laut Rücklagenbuch")); sort += 1
        if dev.late_cash_total:
            self._session.add(self._line(run, None, 'RESERVE_LATE_CASH', 'Nachträgliche Buchungen zum abgeschlossenen Jahr',
                                         dev.late_cash_total, sort, note='Rücklagenbuch · ursprüngliche Wertstellung')); sort += 1
        if dev.overrides_total:
            self._session.add(self._line(run, None, 'RESERVE_ADJUSTMENT', 'Begründete Rücklagenkorrekturen',
                                         dev.overrides_total, sort, note='Rücklagenbuch')); sort += 1
        self._session.add(self._line(run, None, "RESERVE_CONTRIBUTION",
                                     "Zufuehrung Erhaltungsruecklage (gesamt)", dev.contributions_total,
                                     sort)); sort += 1
        self._session.add(self._line(run, None, "RESERVE_WITHDRAWAL",
                                     "Entnahme Erhaltungsruecklage", dev.withdrawals_total, sort,
                                     note="Entnahme laut Rücklagenbuch")); sort += 1
        self._session.add(self._line(run, None, "RESERVE_CLOSING",
                                     "Erhaltungsruecklage Endbestand", dev.closing_balance, sort,
                                     note="Anfang + Zuführung − Entnahme ± Korrekturen + nachträgliche Buchungen")); sort += 1
        await self._session.flush()

    def _line(self, run, owner_id, line_type, label, amount, sort_order, *,
              cost_category_id=None, cost_category_code=None, distribution_key=None,
              total_amount=None, base_total=None, base_share=None, time_days=None,
              time_total=None, note=None) -> SettlementLine:
        return SettlementLine(
            organization_id=self._org_id,
            settlement_run_id=run.id,
            line_type=line_type,
            addressee_owner_profile_id=owner_id,
            cost_category_id=cost_category_id,
            cost_category_code=cost_category_code,
            label=label,
            distribution_key=distribution_key,
            total_amount=total_amount,
            distribution_base_total=base_total,
            distribution_base_share=base_share,
            time_share_days=time_days,
            time_share_total_days=time_total,
            amount=amount,
            sort_order=sort_order,
            note=note,
        )

    # ------------------------------------------------------------------
    # internals -- Vermoegensbericht
    # ------------------------------------------------------------------
    async def _resolve_report_bank_accounts(self, inputs: AssetReportInputs) -> None:
        accounts = BankAccountRepository(self._session, self._org_id)
        for balance in inputs.bank_balances or ():
            if await accounts.get_by_id(balance.bank_account_id) is None:
                raise HTTPException(404, "Bank account not found")

    async def _compute_report_engine(
        self, run: SettlementRun, inputs: AssetReportInputs
    ) -> tuple[VermoegensberichtInput, AssetReportResult]:
        from app.repositories.property_repo import PropertyRepository  # noqa: F401

        reserves = ReserveLedgerService(self._session, self._org_id, self._user_id)
        ledger = await reserves.read(run)
        if not ledger['frozen']:
            await reserves.require_contribution_backing(run)
        if any(row['kind'] == 'UNASSIGNED' for row in ledger['entries']):
            raise HTTPException(422, {'code': 'RESERVE_ASSIGNMENT_REQUIRED', 'message': 'Bitte klären Sie alle Zahlungen ohne Planbezug vor der Berechnung.'})
        if ledger.get('unlinked_payment_ids'):
            raise HTTPException(422, {'code': 'RESERVE_PAYOUT_EVIDENCE_REQUIRED', 'message': 'Bitte verknüpfen Sie alle Rücklagenentnahmen mit Kostenbeleg und Beschluss.'})
        # Freeze the Soll the reserve ledger was decided with, in the annual run's shape (#402).
        plans = await reserves.repo.plans(run)
        if run.economic_plan_id:
            plans = [p for p in plans if p.id == run.economic_plan_id]
        plan_advances: tuple[OwnerAdvanceInput, ...] = ()
        if len(plans) == 1:
            from app.services.payment_service import PaymentService
            shares = await PaymentService(self._session, self._org_id, self._user_id).owner_plan_charge_totals(
                plans[0], run.period_start, run.period_end)
            plan_advances = tuple(
                OwnerAdvanceInput(owner_profile_id=owner_id, plan_advance_total=amount)
                for owner_id, amount in sorted(dated_plan_soll(shares).items(), key=lambda item: str(item[0])))
        inp = VermoegensberichtInput(
            period_start=run.period_start,
            period_end=run.period_end,
            property=PropertyInput(id=run.property_id, name="", management_type=""),
            reserve=ReserveInput(
                opening_balance=Decimal(ledger['opening_balance']),
                withdrawals_total=Decimal(ledger['withdrawals_total']),
                overrides_total=Decimal(ledger['overrides_total']),
                late_cash_total=Decimal(ledger.get('late_cash_total', '0.00')),
            ),
            reserve_contributions_total=Decimal(ledger["contributions_total"]),
            reserve_ledger=ledger,
            plan_advances=plan_advances,
            bank_balances=tuple(
                BankBalanceInput(
                    bank_account_id=b.bank_account_id, label=b.label,
                    account_type=b.account_type, balance=b.balance,
                )
                for b in inputs.bank_balances
            ),
            receivables=tuple(
                AssetItemInput(label=r.label, amount=r.amount) for r in inputs.receivables
            ),
            payables=tuple(
                AssetItemInput(label=p.label, amount=p.amount) for p in inputs.payables
            ),
            engine_version=ENGINE_VERSION,
        )
        try:
            result = build_vermoegensbericht(inp)
        except (WegValidationError, DistributionError) as exc:
            raise HTTPException(status_code=422, detail=f"Vermoegensbericht nicht moeglich: {exc}")
        return inp, result

    def _write_report_lines(self, run: SettlementRun, result: AssetReportResult) -> None:
        for ln in result.lines:
            self._session.add(
                SettlementLine(
                    organization_id=self._org_id,
                    settlement_run_id=run.id,
                    line_type=ln.line_type,
                    label=ln.label,
                    amount=ln.amount,
                    sort_order=ln.sort_order,
                    note=ln.note,
                )
            )
