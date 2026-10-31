"""SettlementRun service -- create draft / compute / finalize (atomic) / correct.

m3-abrechnungs-engine.md section 10.3.

Boundary discipline (SDD section 16): the service loads ORM rows, maps them to
the engine's frozen dataclasses, calls the PURE engine, and persists the result
(settlement_line + snapshot_payload + hash).  The engine never sees the DB; the
service never re-implements billing math.

finalize() is ONE atomic transaction (analogous to cost_entry_service.confirm):
  compute -> snapshot_payload -> snapshot_sha256 -> (re)write settlement_line ->
  status='finalized' + finalized_at/by + total_cost/balance -> audit_event.
The ADR-0003 trigger freezes the run from the moment of commit.

Provisional data sources (clearly marked, [OFFEN/§18]):
  - external/manual per-unit amounts and per-unit consumption/person counts are
    NOT stored in their own M3 table (that storage decision is O3-1/O3-5, left
    to a later fachliche round).  They are supplied as STRUCTURED INPUT at
    compute time (and then frozen into the immutable snapshot).  If a position
    needs such data and it is absent, the engine ESCALATES (no silent estimate).
"""

from __future__ import annotations

import uuid
import json
from calendar import monthrange
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from fractions import Fraction
from types import SimpleNamespace

from fastapi import HTTPException
from app.core.rbac import ScopeCapability
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.temporal import db_exclusive_to_engine_inclusive
from app.engine.canonical import canonical_json, sha256_hex
from app.engine.periods import validate_settlement_period, period_for_date, build_unit_cover, property_period_windows
from app.engine.rounding import DistributionError
from app.engine.mietkonto import _money
from app.services.lease_rent_change_service import LeaseRentChangeService
from app.services.heating_input_service import HeatingConfig, HeatingSliceOverride, HeatingInputService, property_period_run
from app.engine.settlement import EngineValidationError, compute_bk_settlement
from app.engine.snapshot import build_snapshot_payload
from app.engine.meter_consumption import MeterReadingInput, MeterConsumptionError, split_consumption
from app.repositories.meter_repo import MeterRepository, MeterReadingRepository
from app.services.render_names import (
    DISPLAY_NAMES_KEY,
    freeze_block,
    resolve_settlement_values,
)
from app.engine.types import (
    CostPositionInput,
    EngineResult,
    ExternalAmountInput,
    LeasePeriodInput,
    ManualAmountInput,
    OwnerPeriodInput,
    PeriodOverlapError,
    PropertyInput,
    SettlementInput,
    UnitInput,
)
from app.models.audit_event import AuditEvent
from app.services.financial_scope_service import FinancialScopeService
from app.services.financial_computation_service import begin_computation, record_computation
from app.services.financial_input_policy import FinancialInputPolicy, rental_values, incomplete
from app.models.property import Property
from app.models.settlement_line import SettlementLine
from app.models.settlement_run import SettlementRun
from app.repositories.audit_repo import AuditRepository
from app.repositories.settlement_repo import (
    SettlementLineRepository,
    SettlementRunRepository,
)

ENGINE_VERSION = "m3.1.0"


class SettlementPeriodConflict(HTTPException):
    def __init__(self, message="Abrechnungszeitraum überschneidet eine finalisierte Abrechnung dieses Mietvertrags."):
        super().__init__(409, {
            "code": "settlement_period_conflict",
            "message": message,
        })


@dataclass
class ExternalAmountEntry:
    """A per-(cost_entry, unit, occupancy period) external tenant amount."""

    cost_entry_id: uuid.UUID
    unit_id: uuid.UUID
    tenant_amount: Decimal
    period_from: date | None = None
    period_to: date | None = None


@dataclass
class ManualAmountEntry:
    cost_entry_id: uuid.UUID
    unit_id: uuid.UUID
    amount: Decimal


@dataclass
class UnitMetric:
    """Per-unit consumption / person count for VERBRAUCH / PERSONENZAHL."""

    unit_id: uuid.UUID
    consumption: Decimal | None = None
    person_count: int | None = None


@dataclass
class ComputeExtras:
    """Structured extra inputs for one compute (provisional source, O3-1/O3-5)."""

    external_amounts: list[ExternalAmountEntry] = field(default_factory=list)
    manual_amounts: list[ManualAmountEntry] = field(default_factory=list)
    unit_metrics: list[UnitMetric] = field(default_factory=list)
    heating_inputs: list[HeatingConfig] = field(default_factory=list)


def decode_compute_extras(payload: dict) -> ComputeExtras:
    """Restore typed user intent; automatic meter values are never cached here."""
    def day(value):
        return date.fromisoformat(value) if value is not None else None

    return ComputeExtras(
        external_amounts=[ExternalAmountEntry(uuid.UUID(e['cost_entry_id']),uuid.UUID(e['unit_id']),
            Decimal(e['tenant_amount']),day(e.get('period_from')),day(e.get('period_to')))
            for e in payload.get('external_amounts',[])],
        manual_amounts=[ManualAmountEntry(uuid.UUID(e['cost_entry_id']),uuid.UUID(e['unit_id']),Decimal(e['amount']))
            for e in payload.get('manual_amounts',[])],
        unit_metrics=[UnitMetric(uuid.UUID(e['unit_id']),Decimal(e['consumption']) if e.get('consumption') is not None else None,e.get('person_count'))
            for e in payload.get('unit_metrics',[])],
        heating_inputs=[HeatingConfig(uuid.UUID(e['cost_entry_id']),e['kind'],Decimal(e['consumption_fraction']),
            e['base_key'],e['consumption_recorded'],e['mandatory_seventy_percent'],e['meter_type'],
            [HeatingSliceOverride(uuid.UUID(r['unit_id']),day(r['period_from']),day(r['period_to']),
                Decimal(r['consumption']) if r.get('consumption') is not None else None,
                Decimal(r['base_quantity']) if r.get('base_quantity') is not None else None) for r in e.get('slices',[])])
            for e in payload.get('heating_inputs',[])],
    )


# `db_exclusive_to_engine_inclusive` is imported at the top from the canonical
# single-source-of-truth module (app.core.temporal, ADR-0006): the [)<->inclusive
# rule has exactly ONE definition, shared by the engine settlement read-edge (the
# two call sites in _load_engine_input below) AND the M1 API write/display edges
# (leases.py / ownership.py). Storage stays HALF-OPEN `[valid_from, valid_to)`
# (valid_to = first NON-occupied day); the engine (ADR-0004 / periods.py) is
# TAG-INCLUSIVE [from, to] (valid_to = last occupied day). The conversion = stored
# exclusive bound - 1 day; NULL stays open-ended. Only the [)-EXCLUDE temporal
# columns (lease_agreement, ownership_history) pass through; service_period_* (no
# EXCLUDE, fachlich inclusive) and the UI-supplied settlement-period bounds are NOT
# converted. Without it the engine would re-count the shared boundary day of two
# adjacent records and raise "Overlapping lease periods" (the live 500 on a
# DB-legal Auszug-D / Einzug-D adjacency).


class SettlementService:
    def __init__(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        self._session = session
        self._org_id = org_id
        self._user_id = user_id
        self._runs = SettlementRunRepository(session, org_id)
        self._lines = SettlementLineRepository(session, org_id)
        self._audit = AuditRepository(session, org_id)
        self._meter_sources: list[dict] = []

    async def advance_proposal(self, run_id: uuid.UUID, *, accept_from: date | None = None):
        scope = FinancialScopeService(self._session, self._org_id, self._user_id)
        # Serialize with corrections, ACL/source changes and T23 writes. Re-read
        # under the lock so a concurrent correction cannot accept a stale run.
        await scope.lock_inputs()
        run = await self._runs.get_by_id(run_id)
        if run is None:
            raise HTTPException(404, "Settlement run not found")
        await scope.enter(run, capability=ScopeCapability.READ if accept_from is None else ScopeCapability.PREPARE)
        if run.status != "finalized" or run.superseded_by_id is not None or run.run_type != "BK_MIETER":
            raise HTTPException(409, "Advance proposal requires an active finalized BK settlement")
        proposal = self._advance_proposal(run)
        if accept_from is None:
            return proposal
        return await LeaseRentChangeService(self._session, self._org_id, self._user_id).create(
            run.lease_agreement_id, accept_from, Decimal(proposal["monthly_amount"]),
        )

    @staticmethod
    def _advance_proposal(run: SettlementRun) -> dict:
        """Use only frozen inclusive engine dates and finalized tenant costs."""
        try:
            payload = run.snapshot_payload
            if payload["run_type"] != "BK_MIETER" or run.lease_agreement_id is None:
                raise ValueError("Not a tenant settlement")
            period_start = date.fromisoformat(payload["period"]["start"])
            period_end = date.fromisoformat(payload["period"]["end"])
            if (period_start, period_end) != (run.period_start, run.period_end):
                raise ValueError("Inconsistent frozen period")
            leases = [lease for lease in payload["inputs"]["lease_periods"]
                      if lease["lease_agreement_id"] == str(run.lease_agreement_id)]
            if len(leases) != 1:
                raise ValueError("Missing unique frozen tenancy")
            lease = leases[0]
            start = max(period_start, date.fromisoformat(lease["from"]))
            end = min(period_end, date.fromisoformat(lease["to"]) if lease["to"] is not None else period_end)
            if not isinstance(payload["result"]["total_cost"], str):
                raise ValueError("Invalid frozen monetary representation")
            cost = Decimal(payload["result"]["total_cost"])
            if not cost.is_finite() or cost < 0 or cost != run.total_cost or start > end:
                raise ValueError("Invalid finalized cost or tenancy")
            # Calendar weights remain exact even across different partial
            # months. Only the DTO renders a repeating fraction as a Decimal.
            month_weight = Fraction(0)
            year, month = start.year, start.month
            while (year, month) <= (end.year, end.month):
                days = monthrange(year, month)[1]
                first = start.day if (year, month) == (start.year, start.month) else 1
                last = end.day if (year, month) == (end.year, end.month) else days
                month_weight += Fraction(last - first + 1, days)
                year, month = (year + 1, 1) if month == 12 else (year, month + 1)
            with localcontext() as context:
                context.prec = 28
                context.rounding = ROUND_HALF_UP
                months = Decimal(month_weight.numerator) / Decimal(month_weight.denominator)
                amount = _money(cost * Decimal(month_weight.denominator) / Decimal(month_weight.numerator))
            if amount >= Decimal("1000000000000"):
                raise ValueError("Proposed advance exceeds rental amount storage")
        except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError) as exc:
            raise HTTPException(409, "Frozen settlement lacks a valid advance proposal basis") from exc
        return {
            "lease_agreement_id": str(run.lease_agreement_id),
            "tenant_cost": f"{cost:.2f}", "months": format(months, "f"),
            "monthly_amount": f"{amount:.2f}",
            "period_start": period_start, "period_end": period_end,
        }

    # ------------------------------------------------------------------
    # create draft
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_period(start: date, end: date) -> None:
        try:
            validate_settlement_period(start, end)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @staticmethod
    def _lease_period(lease, start: date, end: date) -> tuple[date, date]:
        """Intersect inclusive run dates with the stored half-open lease span."""
        last = db_exclusive_to_engine_inclusive(lease.valid_to)
        clipped = max(start, lease.valid_from), min(end, last) if last is not None else end
        if clipped[0] > clipped[1]:
            raise SettlementService._outside_lease()
        return clipped

    @staticmethod
    def _outside_lease() -> HTTPException:
        return HTTPException(422, {
            "code": "settlement_period_outside_lease",
            "message": "Der Abrechnungszeitraum muss innerhalb der Mietdauer liegen.",
        })

    async def create_draft(
        self,
        property_id: uuid.UUID,
        lease_agreement_id: uuid.UUID,
        period_start: date,
        period_end: date,
        advance_payments_total: Decimal | None = None,
        management_mandate_id: uuid.UUID | None = None,
        accounting_scope_id: uuid.UUID | None = None,
    ) -> SettlementRun:
        """Create a draft BK_MIETER run for one lease/addressee."""
        from app.repositories.lease_agreement_repo import LeaseAgreementRepository

        lease_repo = LeaseAgreementRepository(self._session, self._org_id)
        lease = await lease_repo.get_by_id(lease_agreement_id)
        if lease is None:
            raise HTTPException(status_code=404, detail="Lease agreement not found")
        if lease.unit_id is None:
            raise HTTPException(status_code=422, detail="Lease has no unit")

        run = SettlementRun(
            organization_id=self._org_id,
            property_id=property_id,
            management_mandate_id=management_mandate_id,
            run_type="BK_MIETER",
            period_start=period_start,
            period_end=period_end,
            unit_id=lease.unit_id,
            lease_agreement_id=lease_agreement_id,
            tenant_profile_id=lease.tenant_profile_id,
            advance_payments_total=advance_payments_total,
            advance_payments_source="MANUAL",
            status="draft",
            engine_version=ENGINE_VERSION,
        )
        await FinancialScopeService(self._session, self._org_id, self._user_id).prepare_new(run, accounting_scope_id)
        # prepare_new holds the existing organization financial-input lock.
        self._validate_period(period_start, period_end)
        clipped = self._lease_period(lease, period_start, period_end)
        if clipped != (period_start, period_end):
            from app.repositories.property_repo import PropertyRepository

            prop = await PropertyRepository(self._session, self._org_id).get_by_id(property_id)
            if prop is None or (period_start, period_end) != period_for_date(prop.fiscal_year_start_month, period_start):
                raise self._outside_lease()
        run.period_start, run.period_end = period_start, period_end = clipped
        if await self._runs.has_conflicting_period(run):
            raise SettlementPeriodConflict()
        run = await self._runs.create(run)

        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="settlement_run",
                entity_id=run.id,
                action="settlement.created",
                payload_diff={
                    "run_type": "BK_MIETER",
                    "lease_agreement_id": str(lease_agreement_id),
                    "period": f"{period_start} - {period_end}",
                },
            )
        )
        return run

    async def create_batch(
        self,
        property_id: uuid.UUID,
        period_start: date,
        period_end: date,
        accounting_scope_id: uuid.UUID | None = None,
    ) -> tuple[list[SettlementRun], list[tuple[SettlementRun, str | None]], list[tuple[uuid.UUID, dict[str, str]]]]:
        """Create missing lease drafts in the ambient, serialized transaction."""
        scope = FinancialScopeService(self._session, self._org_id, self._user_id)
        # Validate even an empty property/period before returning an empty batch.
        context = SettlementRun(
            property_id=property_id, run_type="BK_MIETER",
            period_start=period_start, period_end=period_end,
        )
        sphere = await scope.prepare_new(context, accounting_scope_id)
        self._validate_period(period_start, period_end)
        if not await self._session.scalar(text(
            "SELECT property_financial_source_access(:org,:property)"
        ), {"org": self._org_id, "property": property_id}):
            raise HTTPException(404, "Complete property sources not accessible")
        scope_id = sphere.id if sphere is not None else None
        leases = await self._runs.leases_for_period(property_id, period_start, period_end)
        existing = await self._runs.active_bk_for_period(
            property_id, period_start, period_end, scope_id,
        )
        by_period = {(run.lease_agreement_id, run.period_start, run.period_end): run for run in existing}
        created, skipped, errors = [], [], []
        for lease in leases:
            start, end = self._lease_period(lease, period_start, period_end)
            existing_run = by_period.get((lease.id, start, end))
            reason = None
            if existing_run is None:
                existing_run = next((run for run in existing
                    if run.lease_agreement_id == lease.id and run.status == "finalized"
                    and run.period_start <= start and run.period_end >= end), None)
                if existing_run is not None:
                    reason = "Eine finalisierte Abrechnung deckt den gekürzten Mietzeitraum bereits ab."
            if existing_run is not None:
                skipped.append((existing_run, reason))
            else:
                overlapping = next((run for run in existing
                    if run.lease_agreement_id == lease.id
                    and run.period_start <= end and run.period_end >= start), None)
                if overlapping is not None:
                    conflict = SettlementPeriodConflict(
                        "Abrechnungszeitraum überschneidet einen bestehenden Entwurf dieses Mietvertrags."
                    ) if overlapping.status == "draft" else SettlementPeriodConflict()
                    errors.append((lease.id, conflict.detail))
                    continue
                try:
                    created.append(await self.create_draft(
                        property_id, lease.id, start, end,
                        accounting_scope_id=scope_id,
                    ))
                except SettlementPeriodConflict as exc:
                    errors.append((lease.id, exc.detail))
        if errors and not created and not skipped:
            raise HTTPException(409, errors[0][1])
        return created, skipped, errors

    async def set_advance_payments(
        self, run_id: uuid.UUID, amount: Decimal
    ) -> SettlementRun:
        """Set the manual advance-payment total on a draft run (section 7.2)."""
        run = await self._get_draft_or_404(run_id)
        run.advance_payments_total = amount
        run.advance_payments_source = "MANUAL"
        await self._runs.update(run)
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="settlement_run",
                entity_id=run.id,
                action="settlement.advance_payments_set",
                payload_diff={"advance_payments_total": str(amount)},
            )
        )
        return run

    async def set_advance_payments_derived(
        self, run_id: uuid.UUID, period_year: int
    ) -> SettlementRun:
        """Derive the BK advance-payment total from booked NK payments (M6 §6.2).

        Minimal-additive bridge (advance_payments_source MANUAL -> DERIVED): the
        M3 engine is UNCHANGED -- it still reads run.advance_payments_total; this
        only fills that value from the booked, non-reversed NK_VORAUSZAHLUNG
        payments of THIS lease + settlement period. The reference year must
        belong to the period, including for cross-year periods. The pure
        derivation is bit-identical to the MANUAL value (I6-E). MANUAL stays the
        default/fallback; this path is opt-in. E6-8: derived fresh on demand;
        finalize then freezes whatever value is current into the snapshot.
        """
        from app.services.payment_service import PaymentService

        run = await self._get_draft_or_404(run_id)
        if not run.period_start.year <= period_year <= run.period_end.year:
            raise HTTPException(422, "DERIVED reference year must belong to the settlement period")
        if run.lease_agreement_id is None:
            raise HTTPException(
                status_code=422,
                detail="DERIVED advance payments require a lease addressee on the run",
            )
        await FinancialScopeService(self._session, self._org_id, self._user_id).enter(run, computation=True)
        pay_svc = PaymentService(self._session, self._org_id, self._user_id)
        total = await pay_svc.derive_nk_advance_total_for_period(
            run.lease_agreement_id, run.period_start, run.period_end
        )
        run.advance_payments_total = total
        run.advance_payments_source = "DERIVED"
        await self._runs.update(run)
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="settlement_run",
                entity_id=run.id,
                action="settlement.advance_payments_derived",
                payload_diff={
                    "advance_payments_total": str(total),
                    "advance_payments_source": "DERIVED",
                    "period_year": str(period_year),
                },
            )
        )
        return run

    # ------------------------------------------------------------------
    # compute (draft, repeatable) and preview
    # ------------------------------------------------------------------
    async def compute(
        self,
        run_id: uuid.UUID,
        extras: ComputeExtras | None = None,
    ) -> tuple[SettlementRun, EngineResult]:
        """Run the engine on a draft, (re)write its lines + snapshot_payload.

        Repeatable: each call deletes the previous draft lines and re-writes
        them. Does NOT finalize (status stays 'draft', snapshot_sha256 stays
        NULL until finalize). Escalates (422) on any engine validation error
        rather than persisting a partial/wrong result.
        """
        run = await self._get_draft_or_404(run_id)
        computation_basis = await begin_computation(self._session, self._org_id, run, 'compute')
        inp, result = await self._compute_engine(run, extras)

        # Rewrite the draft lines from the fresh result.
        await self._lines.delete_for_run(run.id)
        await self._write_lines(run, result)

        run.snapshot_payload = {**self._snapshot_with_meter_sources(inp, result), "financial_input_coverage": self._coverage}
        run.total_cost = result.total_cost
        run.balance = result.balance
        await self._runs.update(run)

        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="settlement_run",
                entity_id=run.id,
                action="settlement.computed",
                payload_diff={
                    "total_cost": str(result.total_cost),
                    "balance": str(result.balance),
                },
            )
        )
        await record_computation(self._session, self._org_id, self._user_id, run, 'compute', computation_basis)
        return run, result

    async def preview(
        self,
        run_id: uuid.UUID,
        extras: ComputeExtras | None = None,
    ) -> EngineResult:
        """Compute the engine result WITHOUT persisting (read-only preview)."""
        run = await self._get_draft_or_404(run_id)
        _inp, result = await self._compute_engine(run, extras)
        return result

    # ------------------------------------------------------------------
    # finalize (atomic snapshot + hash)
    # ------------------------------------------------------------------
    async def finalize(
        self,
        run_id: uuid.UUID,
        extras: ComputeExtras | None = None,
    ) -> SettlementRun:
        """Finalize a run -- atomic snapshot + hash (section 10.3).

        One transaction (session.begin already active from get_db):
          engine final compute -> snapshot_payload -> snapshot_sha256 ->
          (re)write settlement_line -> status='finalized', finalized_at/by,
          total_cost/balance -> audit_event. The ADR-0003 trigger freezes the
          run from commit. Any error -> rollback (no half-finalized run).
        """
        run = await self._get_draft_or_404(run_id)
        computation_basis = await begin_computation(self._session, self._org_id, run, 'finalize')
        if await self._runs.has_conflicting_period(run):
            raise SettlementPeriodConflict()
        inp, result = await self._compute_engine(run, extras)

        payload = self._snapshot_with_meter_sources(inp, result)
        payload["financial_input_coverage"] = self._coverage
        # Anzeige-Namen einfrieren (ADR-0012): noch als Entwurf (OLD.status =
        # 'draft', der ADR-0003-Trigger lässt das Schreiben zu) werden die für
        # das PDF nötigen Klartext-Namen in den Snapshot geschrieben -- VOR dem
        # Hash, damit sie Teil der eingefrorenen, gehashten Wahrheit sind. Ein
        # späteres DSGVO-Anonymisieren der Person verändert das finalisierte
        # Dokument dann nicht mehr (der Renderer nutzt die eingefrorenen Namen).
        payload[DISPLAY_NAMES_KEY] = freeze_block(
            await resolve_settlement_values(self._session, self._org_id, run)
        )
        payload_bytes = canonical_json(payload)
        sha = sha256_hex(payload_bytes)

        # Re-write lines from the final result.
        await self._lines.delete_for_run(run.id)
        await self._write_lines(run, result)

        now = datetime.now(timezone.utc)
        run.snapshot_payload = payload
        run.snapshot_sha256 = sha
        run.total_cost = result.total_cost
        run.balance = result.balance
        run.status = "finalized"
        run.finalized_at = now
        run.finalized_by = self._user_id
        await self._runs.update(run)

        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="settlement_run",
                entity_id=run.id,
                action="settlement.finalized",
                payload_diff={
                    "snapshot_sha256": sha,
                    "total_cost": str(result.total_cost),
                    "balance": str(result.balance),
                },
            )
        )
        await record_computation(self._session, self._org_id, self._user_id, run, 'finalize', computation_basis)
        return run

    # ------------------------------------------------------------------
    # correction = new run (never a mutation; section 1.6)
    # ------------------------------------------------------------------
    async def create_correction_run(self, run_id: uuid.UUID) -> SettlementRun:
        """Storno/Neuberechnung: a NEW draft run; link the old via superseded_by_id.

        The finalized original stays frozen; the only allowed mutation on it is
        setting superseded_by_id (the ADR-0003 trigger permits exactly this).
        """
        scope = FinancialScopeService(self._session, self._org_id, self._user_id)
        original = await self._runs.get_by_id(run_id)
        if original is None:
            raise HTTPException(404, "Financial original not found")
        await scope.enter(original, capability=ScopeCapability.APPROVE)
        await scope.lock_inputs()
        original = await self._runs.get_by_id_for_update(run_id)
        if original is None:
            raise HTTPException(status_code=404, detail="Settlement run not found")
        if original.status != "finalized":
            raise HTTPException(
                status_code=409,
                detail="Only finalized runs are corrected via a new run; edit a draft directly",
            )
        if original.superseded_by_id is not None:
            raise HTTPException(status_code=409, detail="This run has already been superseded")

        new_run = SettlementRun(
            organization_id=self._org_id,
            property_id=original.property_id,
            management_mandate_id=original.management_mandate_id,
            run_type=original.run_type,
            economic_plan_id=original.economic_plan_id,
            period_start=original.period_start,
            period_end=original.period_end,
            unit_id=original.unit_id,
            lease_agreement_id=original.lease_agreement_id,
            tenant_profile_id=original.tenant_profile_id,
            advance_payments_total=original.advance_payments_total,
            advance_payments_source=original.advance_payments_source,
            status="draft",
            engine_version=ENGINE_VERSION,
        )
        await scope.prepare_new(new_run)
        new_run = await self._runs.create(new_run)

        # The single allowed mutation on the finalized original.
        original.superseded_by_id = new_run.id
        await self._runs.update(original)

        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="settlement_run",
                entity_id=original.id,
                action="settlement.superseded",
                payload_diff={"superseded_by_id": str(new_run.id)},
            )
        )
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="settlement_run",
                entity_id=new_run.id,
                action="settlement.created",
                payload_diff={"correction_of": str(original.id)},
            )
        )
        return new_run

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    async def _get_draft_or_404(self, run_id: uuid.UUID) -> SettlementRun:
        scope = FinancialScopeService(self._session, self._org_id, self._user_id)
        run = await self._runs.get_by_id(run_id)
        if run is None:
            raise HTTPException(404, "Financial draft not found")
        await scope.enter(run)
        await scope.lock_inputs()
        run = await self._runs.get_by_id_for_update(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Settlement run not found")
        if run.status != "draft":
            raise HTTPException(
                status_code=409,
                detail=f"Run is '{run.status}'; only draft runs can be (re)computed/finalized",
            )
        return run

    async def consumption_suggestions(self, run_id: uuid.UUID):
        scope = FinancialScopeService(self._session, self._org_id, self._user_id)
        await scope.lock_inputs()
        run = await self._runs.get_by_id(run_id)
        if run is None or run.run_type != "BK_MIETER":
            raise HTTPException(404, "Settlement run not found")
        await scope.enter(run, capability=ScopeCapability.READ, computation=True)
        if run.status != "draft":
            raise HTTPException(409, "Zähler-Vorbelegung ist nur für Entwürfe verfügbar; finalisierte Eingaben bleiben eingefroren.")
        units = await self._runs.units_for_property(run.property_id)
        allocation_run = await property_period_run(self._session, self._org_id, run)
        entries = await self._runs.active_cost_entries_for_period(run.property_id, allocation_run.period_start, allocation_run.period_end)
        return await self._consumption_candidates(run, units, entries)

    def _snapshot_with_meter_sources(self, inp, result):
        payload = build_snapshot_payload(inp, result)
        if self._meter_sources:
            payload["inputs"]["meter_consumption_sources"] = self._meter_sources
        payload['extra_inputs'] = self._extra_payload
        if result.heating_costs:
            for allocation in payload['heating_costs']:
                for row in allocation['rows']:
                    row['meter_source'] = self._heating_sources[row['slice_id']]
        return payload

    async def heating_suggestions(self, run_id, cost_entry_id, meter_type, base_key):
        scope = FinancialScopeService(self._session,self._org_id,self._user_id)
        await scope.lock_inputs()
        run = await self._runs.get_by_id(run_id)
        if run is None or run.run_type != 'BK_MIETER':
            raise HTTPException(404,'Settlement run not found')
        await scope.enter(run,capability=ScopeCapability.READ,computation=True)
        if run.status != 'draft':
            raise HTTPException(409,'Heizkosten-Vorbelegung nur für Entwürfe')
        allocation_run = await property_period_run(self._session, self._org_id, run)
        entries = await self._runs.active_cost_entries_for_period(run.property_id,allocation_run.period_start,allocation_run.period_end)
        found = next(((entry,category) for entry,category in entries if entry.id==cost_entry_id),None)
        if found is None or found[1] is None or found[1].code not in {'HEIZUNG_ZENTRAL','WARMWASSER_ZENTRAL'}:
            raise HTTPException(404,'Heating cost source not found')
        units = await self._runs.units_for_property(run.property_id)
        return await HeatingInputService(self._session,self._org_id,self._runs).candidates(run,found[0],units,meter_type,base_key)

    async def _consumption_candidates(self, run, units, entries):
        kinds = {category.code for entry, category in entries
                 if entry.tenant_distribution_key == "VERBRAUCH"
                 and entry.tenant_apportionable != "NICHT_UMLAGEFAEHIG" and category is not None}
        if not kinds:
            return []
        run = await property_period_run(self._session, self._org_id, run)
        meters = await MeterRepository(self._session, self._org_id).list_by_property(run.property_id)
        kind = next(iter(kinds)) if len(kinds) == 1 else None
        by_unit = {unit.id: [meter for meter in meters
                   if meter.unit_id == unit.id and meter.meter_type.strip().upper() == kind]
                   for unit in units}
        meter_ids = [rows[0].id for rows in by_unit.values() if len(rows) == 1]
        readings = await MeterReadingRepository(self._session, self._org_id).list_boundary_readings(
            meter_ids, run.period_start, run.period_end, include_interim=True)
        by_meter = {}
        for row in readings:
            by_meter.setdefault(row.meter_id, []).append(MeterReadingInput(row.id, row.reading_date, row.value))

        def boundary(row):
            return {"id": str(row.id), "reading_date": row.reading_date.isoformat(), "value": f"{row.value:.4f}"}

        leases = [LeasePeriodInput(l.id, l.unit_id, l.tenant_profile_id, l.valid_from,
                  db_exclusive_to_engine_inclusive(l.valid_to))
                  for l in await self._runs.leases_for_property(run.property_id)]
        candidates = []
        self._consumption_periods = {}
        for unit in units:
            item = {"unit_id": str(unit.id), "unit_label": unit.number,
                    "consumption": None, "meter_id": None, "start_reading": None,
                    "end_reading": None, "reason": None}
            matches = by_unit[unit.id]
            if kind is None:
                item["reason"] = "Handeingabe erforderlich: Unterschiedliche Verbrauchsarten sind nicht automatisch kombinierbar."
            elif len(matches) != 1:
                item["reason"] = "Handeingabe erforderlich: Kein eindeutig passender Einheitszähler; Zuordnung oder Zählerwechsel prüfen."
            else:
                meter = matches[0]
                try:
                    covers = build_unit_cover(unit.id, run.period_start, run.period_end, leases, [])
                    split = split_consumption(by_meter.get(meter.id, ()), run.period_start, run.period_end, covers)
                    measured, periods = split.measured, split.periods
                except MeterConsumptionError as exc:
                    item["reason"] = str(exc)
                else:
                    if len(covers) > 1:
                        self._consumption_periods[unit.id] = periods
                        item["occupant_consumption"] = [
                            {"from": a.isoformat(), "to": b.isoformat(), "consumption": str(q)} for a,b,q in periods]
                        item["interim_readings"] = [boundary(r) for r in by_meter[meter.id]
                                                   if run.period_start < r.reading_date < run.period_end]
                    item.update(consumption=f"{measured.consumption:.4f}", meter_id=str(meter.id),
                                start_reading=boundary(measured.start_reading), end_reading=boundary(measured.end_reading),
                                reason=split.reason)
            candidates.append(item)
        return candidates

    async def _compute_engine(
        self, run: SettlementRun, extras: ComputeExtras | None
    ) -> tuple[SettlementInput, EngineResult]:
        self._coverage = await FinancialInputPolicy(self._session, self._org_id, self._user_id).require(run, "settlement", rental_values(run))
        if extras is None:
            extras = decode_compute_extras((run.snapshot_payload or {}).get('extra_inputs',{}))
        self._extra_payload = json.loads(json.dumps(asdict(extras),default=str))
        self._heating_sources = {}
        inp = await self._load_engine_input(run, extras)
        try:
            result = compute_bk_settlement(inp)
        except (EngineValidationError, DistributionError, PeriodOverlapError) as exc:
            # Escalate fail-loud as a 422 (do not persist a wrong/partial run).
            # PeriodOverlapError: a genuine (EXCLUDE-bypassing) lease overlap is a
            # 4xx data problem, not a 500 server fault.
            raise HTTPException(status_code=422, detail=f"Berechnung nicht moeglich: {exc}")
        return inp, result

    async def _load_engine_input(
        self, run: SettlementRun, extras: ComputeExtras
    ) -> SettlementInput:
        """Load ORM rows and map them to the engine's frozen dataclasses."""
        if run.lease_agreement_id is None:
            raise HTTPException(status_code=422, detail="Run has no addressee lease")

        # Property (frozen copy).
        from app.repositories.property_repo import PropertyRepository

        prop_repo = PropertyRepository(self._session, self._org_id)
        prop: Property | None = await prop_repo.get_by_id(run.property_id)
        if prop is None:
            raise HTTPException(status_code=404, detail="Property not found")

        windows = property_period_windows(run.period_start, run.period_end, prop.fiscal_year_start_month)
        if len(windows) > 1:
            parts, sources = [], []
            first_start = period_for_date(prop.fiscal_year_start_month, run.period_start)[0]
            last_end = period_for_date(prop.fiscal_year_start_month, run.period_end)[1]
            entries = await self._runs.active_cost_entries_for_period(run.property_id, first_start, last_end)
            if not {c.cost_entry_id for c in extras.heating_inputs} <= {e.id for e,_ in entries}:
                raise HTTPException(422, 'Heizkosten: unbekannte aktive Kostenquelle')
            for start, end in windows:
                year_start, year_end = period_for_date(prop.fiscal_year_start_month, start)
                ids = {e.id for e,_ in entries if e.service_period_from <= year_end and e.service_period_to >= year_start}
                heating = [replace(c, slices=[s for s in c.slices
                    if s.period_from >= year_start and s.period_to <= year_end])
                    for c in extras.heating_inputs if c.cost_entry_id in ids]
                context = SimpleNamespace(property_id=run.property_id, lease_agreement_id=run.lease_agreement_id,
                    period_start=start, period_end=end, advance_payments_total=Decimal(0),
                    advance_payments_source=run.advance_payments_source)
                parts.append(await self._load_engine_input(context, replace(extras, heating_inputs=heating)))
                sources.extend(self._meter_sources)
            self._meter_sources = sources
            return replace(parts[0], period_start=run.period_start, period_end=run.period_end,
                advance_payments_total=run.advance_payments_total, allocation_inputs=tuple(parts))

        units = await self._runs.units_for_property(run.property_id)
        leases = await self._runs.leases_for_property(run.property_id)
        ownership = await self._runs.ownership_for_property(run.property_id)
        allocation_run = await property_period_run(self._session, self._org_id, run)
        entries = await self._runs.active_cost_entries_for_period(
            run.property_id, min(allocation_run.period_start, run.period_start),
            max(allocation_run.period_end, run.period_end)
        )
        # Annual consumption weights do not widen invoice service periods.
        # Sources outside this statement need no manual quantities or extras.
        configured_heating = {c.cost_entry_id for c in extras.heating_inputs}
        entries = [(e,c) for e,c in entries if e.id in configured_heating
                   or (e.service_period_from <= run.period_end and e.service_period_to >= run.period_start)]

        heating_configs = {c.cost_entry_id:c for c in extras.heating_inputs}
        if len(heating_configs)!=len(extras.heating_inputs) or not set(heating_configs)<= {e.id for e,_ in entries}:
            raise HTTPException(422,'Heizkosten: doppelte oder unbekannte aktive Kostenquelle')
        generic_entries = [(e,c) for e,c in entries if e.id not in heating_configs]

        # Per-unit metric overrides (consumption / person count).
        metric_by_unit = {m.unit_id: m for m in extras.unit_metrics}
        self._meter_sources = []
        needs_consumption = any(entry.tenant_distribution_key == "VERBRAUCH"
                                and entry.tenant_apportionable != "NICHT_UMLAGEFAEHIG"
                                for entry, _category in generic_entries)
        consumptions = {}
        self._consumption_periods = {}
        if needs_consumption:
            missing = [unit for unit in units if metric_by_unit.get(unit.id) is None
                       or metric_by_unit[unit.id].consumption is None]
            suggestions = {row["unit_id"]: row for row in
                           await self._consumption_candidates(run, missing, generic_entries)} if missing else {}
            for unit in units:
                metric = metric_by_unit.get(unit.id)
                if metric is not None and metric.consumption is not None:
                    # Manual unit quantities describe the property year,
                    # including when this lease's statement is clipped.
                    value = metric.consumption
                    if not value.is_finite() or value < 0:
                        raise HTTPException(422, f"Handeingabe VERBRAUCH für Einheit {unit.number} muss endlich und nichtnegativ sein.")
                else:
                    source = suggestions.get(str(unit.id))
                    if source is None or source["consumption"] is None:
                        reason = source["reason"] if source else "Handeingabe erforderlich: Verbrauchsbasis fehlt."
                        raise HTTPException(422, f"VERBRAUCH für Einheit {unit.number}: {reason}")
                    value = Decimal(source["consumption"])
                    self._meter_sources.append(source)
                consumptions[unit.id] = value

        def unit_input(u) -> UnitInput:
            m = metric_by_unit.get(u.id)
            return UnitInput(
                id=u.id,
                number=u.number,
                usage_type=u.usage_type,
                area=u.area,
                heated_area=u.heated_area,
                mea=u.mea,
                person_count=m.person_count if m else None,
                consumption=consumptions.get(u.id, m.consumption if m else None),
                consumption_periods=self._consumption_periods.get(u.id, ()),
            )

        unit_inputs = tuple(unit_input(u) for u in units)

        # Read-edge conversion of the half-open [) DB upper bound to the engine's
        # tag-inclusive last-occupied-day (ADR-0004 #6 / m3 section 3.4). Only the
        # EXCLUDE temporal tables (lease_agreement, ownership_history) carry [)
        # semantics; service_period_* and the settlement-period bounds are already
        # inclusive and are passed through unchanged below.
        lease_inputs = tuple(
            LeasePeriodInput(
                lease_agreement_id=l.id,
                unit_id=l.unit_id,
                tenant_profile_id=l.tenant_profile_id,
                valid_from=l.valid_from,
                valid_to=db_exclusive_to_engine_inclusive(l.valid_to),
                advance_payment=l.advance_payment,
            )
            for l in leases
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

        # Group extra amounts per cost_entry.
        ext_by_entry: dict[uuid.UUID, list[ExternalAmountEntry]] = {}
        for e in extras.external_amounts:
            ext_by_entry.setdefault(e.cost_entry_id, []).append(e)
        man_by_entry: dict[uuid.UUID, list[ManualAmountEntry]] = {}
        for m in extras.manual_amounts:
            man_by_entry.setdefault(m.cost_entry_id, []).append(m)

        position_inputs = []
        for entry, category in entries:
            code = category.code if category is not None else "UNBEKANNT"
            label = category.label if category is not None else code
            ext = tuple(
                ExternalAmountInput(
                    unit_id=e.unit_id,
                    tenant_amount=e.tenant_amount,
                    period_from=e.period_from,
                    period_to=e.period_to,
                )
                for e in ext_by_entry.get(entry.id, [])
            )
            man = tuple(
                ManualAmountInput(unit_id=m.unit_id, amount=m.amount)
                for m in man_by_entry.get(entry.id, [])
            )
            heating = None
            if entry.id in heating_configs:
                if ext or man:
                    raise HTTPException(422,'Heizkosten intern und externe/manuelle Geldbeträge dürfen nicht kombiniert werden')
                heating, sources = await HeatingInputService(self._session,self._org_id,self._runs).resolve(
                    run,entry,category,units,heating_configs[entry.id])
                self._heating_sources.update(sources)
            position_inputs.append(
                CostPositionInput(
                    cost_entry_id=entry.id,
                    category_id=entry.cost_category_id,
                    category_code=code,
                    label=label,
                    gross_amount=entry.gross_amount,
                    service_period_from=entry.service_period_from,
                    service_period_to=entry.service_period_to,
                    tenant_distribution_key=entry.tenant_distribution_key,
                    tenant_apportionable=entry.tenant_apportionable,
                    apportionable_tenant_amount=entry.apportionable_tenant_amount,
                    external_amounts=ext,
                    manual_amounts=man,
                    heating=heating,
                )
            )

        return SettlementInput(
            period_start=run.period_start,
            period_end=run.period_end,
            property=PropertyInput(
                id=prop.id, name=prop.name, management_type=prop.management_type,
                fiscal_year_start_month=prop.fiscal_year_start_month
            ),
            target_lease_agreement_id=run.lease_agreement_id,
            units=unit_inputs,
            lease_periods=lease_inputs,
            owner_periods=owner_inputs,
            cost_positions=tuple(position_inputs),
            advance_payments_total=run.advance_payments_total,
            advance_payments_source=run.advance_payments_source,
            engine_version=ENGINE_VERSION,
        )

    async def _write_lines(self, run: SettlementRun, result: EngineResult) -> None:
        for ln in result.lines:
            self._session.add(
                SettlementLine(
                    organization_id=self._org_id,
                    settlement_run_id=run.id,
                    line_type=ln.line_type,
                    cost_category_id=ln.category_id,
                    cost_category_code=ln.category_code,
                    label=ln.label,
                    distribution_key=ln.distribution_key,
                    total_amount=ln.total_amount,
                    distribution_base_total=ln.distribution_base_total,
                    distribution_base_share=ln.distribution_base_share,
                    time_share_days=ln.time_share_days,
                    time_share_total_days=ln.time_share_total_days,
                    amount=ln.amount,
                    sort_order=ln.sort_order,
                    note=ln.note,
                )
            )
        await self._session.flush()
