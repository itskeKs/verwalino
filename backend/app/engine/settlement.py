"""Orchestrates one BK_MIETER computation -> EngineResult (pure).

Flow (m3-abrechnungs-engine.md section 3.2 / section 7):
  1. validate inputs fail-loud (section 2.6) -- escalate rather than emit a
     plausible-but-wrong bill.
  2. build each unit's tenant/vacancy time cover over the settlement period.
  3. for every billable position: distribute over all (unit, slice) cells;
     pick the target lease's cell(s) as the tenant's COST line; record vacancy
     cells as owner attribution (so no cent disappears, section 3.3 / I-D).
  4. total_cost = sum of the tenant's rounded position shares (already cent-
     exact per position -> add without re-rounding, section 4.3).
  5. balance = total_cost - advance_payments_total.
  6. assemble result lines + full computation trace + canonical snapshot payload.

The output is a pure data structure; the service layer persists it (settlement_
line + snapshot_payload + hash) in one finalize transaction.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.engine.distribution import (
    distribute_position,
    effective_total,
    is_billable_to_tenant,
)
from app.engine.periods import (
    CoverPeriod,
    KIND_TENANT,
    KIND_VACANCY,
    build_unit_cover,
    day_count,
)
from app.engine.rounding import CENT
from app.engine.types import (
    ALL_KEYS,
    ComputationStep,
    CostPositionInput,
    EngineResult,
    ResultLine,
    SettlementInput,
    VacancyShare,
)


class EngineValidationError(ValueError):
    """A fail-loud validation error before/while computing (section 2.6).

    Carries a structured message with position/unit context so the caller can
    surface exactly what is wrong instead of producing a partial snapshot.
    """


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT)


def _validate(inp: SettlementInput) -> None:
    """Pre-computation validation (section 2.6). Escalate, never guess."""
    if inp.period_end < inp.period_start:
        raise EngineValidationError(
            f"period_end ({inp.period_end}) < period_start ({inp.period_start})"
        )

    leases_by_id = {l.lease_agreement_id: l for l in inp.lease_periods}
    if inp.target_lease_agreement_id not in leases_by_id:
        raise EngineValidationError(
            f"target_lease_agreement_id {inp.target_lease_agreement_id} is not among "
            f"the provided lease periods"
        )

    unit_ids = {u.id for u in inp.units}
    target = leases_by_id[inp.target_lease_agreement_id]
    if target.unit_id not in unit_ids:
        raise EngineValidationError(
            f"target lease's unit {target.unit_id} is not among the provided units"
        )

    for pos in inp.cost_positions:
        # Position must overlap the settlement period (else it does not belong
        # here -- section 2.6 #1).
        if day_count(
            max(pos.service_period_from, inp.period_start),
            min(pos.service_period_to, inp.period_end),
        ) == 0:
            raise EngineValidationError(
                f"Cost position {pos.category_code} ({pos.cost_entry_id}) service "
                f"period [{pos.service_period_from}..{pos.service_period_to}] does not "
                f"overlap settlement period [{inp.period_start}..{inp.period_end}]"
            )
        if is_billable_to_tenant(pos):
            key = pos.tenant_distribution_key
            if key is None or key not in ALL_KEYS:
                raise EngineValidationError(
                    f"Cost position {pos.category_code} ({pos.cost_entry_id}) has "
                    f"invalid/absent tenant_distribution_key: {key}"
                )


def compute_bk_settlement(inp: SettlementInput) -> EngineResult:
    """Compute the BK_MIETER result for the target lease."""
    _validate(inp)

    leases_by_id = {l.lease_agreement_id: l for l in inp.lease_periods}
    target_lease = leases_by_id[inp.target_lease_agreement_id]

    leases = list(inp.lease_periods)
    owner_periods = list(inp.owner_periods)
    units = list(inp.units)

    # 2. Build every unit's tenant/vacancy cover (the full distribution
    #    universe must be consistent even though we only bill one lease).
    covers_by_unit: dict[uuid.UUID, list[CoverPeriod]] = {}
    for unit in units:
        covers_by_unit[unit.id] = build_unit_cover(
            unit.id, inp.period_start, inp.period_end, leases, owner_periods
        )

    cost_lines: list[ResultLine] = []
    computation: list[ComputationStep] = []
    vacancy_attr: list[VacancyShare] = []
    total_cost = Decimal("0.00")

    # Process positions in a deterministic order (by category_code then id) so
    # the snapshot and sort_order are reproducible.
    positions = sorted(
        inp.cost_positions, key=lambda p: (p.category_code, str(p.cost_entry_id))
    )

    sort_order = 0
    for pos in positions:
        if not is_billable_to_tenant(pos):
            # NICHT_UMLAGEFAEHIG never appears in a Mieter-BK (invariant I-F).
            continue

        cells = distribute_position(pos, units, covers_by_unit)
        if not cells:
            continue

        eff_total = effective_total(pos)

        # Tenant's own cells (target lease) -> the COST line for this position.
        target_cells = [
            c
            for c in cells
            if c.cover.kind == KIND_TENANT
            and c.cover.lease_agreement_id == inp.target_lease_agreement_id
        ]
        # Vacancy cells -> owner attribution (no cent disappears).
        for c in cells:
            if c.cover.kind == KIND_VACANCY:
                vacancy_attr.append(
                    VacancyShare(
                        cost_entry_id=pos.cost_entry_id,
                        unit_id=c.unit_id,
                        owner_profile_id=c.cover.owner_profile_id,
                        days=c.time_days,
                        amount=c.amount,
                    )
                )

        if not target_cells:
            # The position does not touch the target tenant (other unit or
            # other tenant period). Not an error -- just no line for this run.
            continue

        amount = sum((c.amount for c in target_cells), Decimal("0.00"))
        amount = _money(amount)
        total_cost += amount

        # The rechenweg uses the representative target cell (single tenant slice
        # is the normal case; if multiple, days are summed, share is the sum).
        rep = target_cells[0]
        tenant_days = sum(c.time_days for c in target_cells)
        note = _position_note(pos)

        cost_lines.append(
            ResultLine(
                line_type="COST",
                label=pos.label,
                amount=amount,
                sort_order=sort_order,
                cost_entry_id=pos.cost_entry_id,
                category_id=pos.category_id,
                category_code=pos.category_code,
                distribution_key=pos.tenant_distribution_key,
                total_amount=_money(eff_total),
                distribution_base_total=rep.key_base_total,
                distribution_base_share=rep.key_base_share,
                time_share_days=tenant_days,
                time_share_total_days=rep.time_total_days,
                note=note,
            )
        )
        computation.append(
            ComputationStep(
                cost_entry_id=pos.cost_entry_id,
                category_code=pos.category_code,
                distribution_key=pos.tenant_distribution_key or "",
                total_amount=_money(eff_total),
                key_base_total=rep.key_base_total,
                key_base_share=rep.key_base_share,
                time_share_days=tenant_days,
                time_share_total_days=rep.time_total_days,
                exact_share=sum((c.exact for c in target_cells), Decimal(0)),
                rounded_amount=amount,
                note=note,
            )
        )
        sort_order += 1

    # 4./5. Soll - Vorauszahlung -> Saldo (section 7).
    advance = _money(inp.advance_payments_total)
    balance = _money(total_cost - advance)

    # Advance-payment + balance lines (BGH: Abzug der Vorauszahlungen + Saldo).
    lines: list[ResultLine] = list(cost_lines)
    lines.append(
        ResultLine(
            line_type="ADVANCE_PAYMENT",
            label="Geleistete Vorauszahlungen",
            amount=advance,
            sort_order=sort_order,
            note=f"Quelle: {inp.advance_payments_source}",
        )
    )
    sort_order += 1
    lines.append(
        ResultLine(
            line_type="BALANCE",
            label="Nachzahlung" if balance > 0 else "Guthaben",
            amount=balance,
            sort_order=sort_order,
            note=(
                "Nachzahlung (Mieter zahlt)"
                if balance > 0
                else "Guthaben (Mieter erhaelt zurueck)"
                if balance < 0
                else "ausgeglichen"
            ),
        )
    )

    return EngineResult(
        lines=tuple(lines),
        computation=tuple(computation),
        total_cost=_money(total_cost),
        advance_payments_total=advance,
        balance=balance,
        vacancy_attribution=tuple(vacancy_attr),
    )


def _position_note(pos: CostPositionInput) -> str:
    """A short, prueffest note for a position's line (section 5/8)."""
    if pos.tenant_distribution_key == "EXTERNE_ABRECHNUNG":
        return "extern abgerechnet (Messdienst)"
    if pos.tenant_apportionable == "TEILWEISE_PRUEFEN":
        return "nur Mieteranteil (CO2)"
    if pos.tenant_apportionable == "GEMISCHT_PRUEFEN":
        return "nur umlagefaehiger Anteil"
    return ""
