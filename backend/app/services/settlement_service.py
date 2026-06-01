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
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.canonical import canonical_json, sha256_hex
from app.engine.rounding import DistributionError
from app.engine.settlement import EngineValidationError, compute_bk_settlement
from app.engine.snapshot import build_snapshot_payload
from app.engine.types import (
    CostPositionInput,
    EngineResult,
    ExternalAmountInput,
    LeasePeriodInput,
    ManualAmountInput,
    OwnerPeriodInput,
    PropertyInput,
    SettlementInput,
    UnitInput,
)
from app.models.audit_event import AuditEvent
from app.models.property import Property
from app.models.settlement_line import SettlementLine
from app.models.settlement_run import SettlementRun
from app.repositories.audit_repo import AuditRepository
from app.repositories.settlement_repo import (
    SettlementLineRepository,
    SettlementRunRepository,
)

ENGINE_VERSION = "m3.1.0"


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

    # ------------------------------------------------------------------
    # create draft
    # ------------------------------------------------------------------
    async def create_draft(
        self,
        property_id: uuid.UUID,
        lease_agreement_id: uuid.UUID,
        period_start: date,
        period_end: date,
        advance_payments_total: Decimal | None = None,
        management_mandate_id: uuid.UUID | None = None,
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
        inp, result = await self._compute_engine(run, extras)

        # Rewrite the draft lines from the fresh result.
        await self._lines.delete_for_run(run.id)
        await self._write_lines(run, result)

        run.snapshot_payload = build_snapshot_payload(inp, result)
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
        inp, result = await self._compute_engine(run, extras)

        payload = build_snapshot_payload(inp, result)
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
        return run

    # ------------------------------------------------------------------
    # correction = new run (never a mutation; section 1.6)
    # ------------------------------------------------------------------
    async def create_correction_run(self, run_id: uuid.UUID) -> SettlementRun:
        """Storno/Neuberechnung: a NEW draft run; link the old via superseded_by_id.

        The finalized original stays frozen; the only allowed mutation on it is
        setting superseded_by_id (the ADR-0003 trigger permits exactly this).
        """
        original = await self._runs.get_by_id(run_id)
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
        run = await self._runs.get_by_id(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Settlement run not found")
        if run.status != "draft":
            raise HTTPException(
                status_code=409,
                detail=f"Run is '{run.status}'; only draft runs can be (re)computed/finalized",
            )
        return run

    async def _compute_engine(
        self, run: SettlementRun, extras: ComputeExtras | None
    ) -> tuple[SettlementInput, EngineResult]:
        inp = await self._load_engine_input(run, extras or ComputeExtras())
        try:
            result = compute_bk_settlement(inp)
        except (EngineValidationError, DistributionError) as exc:
            # Escalate fail-loud as a 422 (do not persist a wrong/partial run).
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

        units = await self._runs.units_for_property(run.property_id)
        leases = await self._runs.leases_for_property(run.property_id)
        ownership = await self._runs.ownership_for_property(run.property_id)
        entries = await self._runs.active_cost_entries_for_period(
            run.property_id, run.period_start, run.period_end
        )

        # Per-unit metric overrides (consumption / person count).
        metric_by_unit = {m.unit_id: m for m in extras.unit_metrics}

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
                consumption=m.consumption if m else None,
            )

        unit_inputs = tuple(unit_input(u) for u in units)

        lease_inputs = tuple(
            LeasePeriodInput(
                lease_agreement_id=l.id,
                unit_id=l.unit_id,
                tenant_profile_id=l.tenant_profile_id,
                valid_from=l.valid_from,
                valid_to=l.valid_to,
                advance_payment=l.advance_payment,
            )
            for l in leases
        )
        owner_inputs = tuple(
            OwnerPeriodInput(
                unit_id=o.unit_id,
                owner_profile_id=o.owner_profile_id,
                valid_from=o.valid_from,
                valid_to=o.valid_to,
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
                )
            )

        return SettlementInput(
            period_start=run.period_start,
            period_end=run.period_end,
            property=PropertyInput(
                id=prop.id, name=prop.name, management_type=prop.management_type
            ),
            target_lease_agreement_id=run.lease_agreement_id,
            units=unit_inputs,
            lease_periods=lease_inputs,
            owner_periods=owner_inputs,
            cost_positions=tuple(position_inputs),
            advance_payments_total=run.advance_payments_total or Decimal("0.00"),
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
