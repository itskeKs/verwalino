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
  5. balance = total_cost - operating advances - heating advances.
  6. assemble result lines + full computation trace + canonical snapshot payload.

The output is a pure data structure; the service layer persists it (settlement_
line + snapshot_payload + hash) in one finalize transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
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
    period_for_date,
    property_period_windows,
    time_factor,
)
from app.engine.heating_settlement import distribute_heating_position
from app.engine.rounding import CENT, day_share_for_period
from app.engine.types import (
    ALL_KEYS,
    HEATING_CATEGORY_CODES,
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
        start, end = ((period_for_date(inp.property.fiscal_year_start_month, inp.period_start)[0],
                       period_for_date(inp.property.fiscal_year_start_month, inp.period_end)[1])
                      if pos.tenant_distribution_key == "VERBRAUCH" or pos.heating is not None
                      else (inp.period_start, inp.period_end))
        if day_count(
            max(pos.service_period_from, start),
            min(pos.service_period_to, end),
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
    windows = property_period_windows(inp.period_start, inp.period_end, inp.property.fiscal_year_start_month)
    if len(windows) > 1 and not inp.allocation_inputs:
        parts = []
        for start, end in windows:
            year_start, year_end = period_for_date(inp.property.fiscal_year_start_month, start)
            positions = []
            for pos in inp.cost_positions:
                a, b = ((year_start, year_end) if pos.tenant_distribution_key == "VERBRAUCH" or pos.heating
                        else (start, end))
                if max(a, pos.service_period_from) > min(b, pos.service_period_to):
                    continue
                if pos.heating:
                    slices = tuple(replace(s, period_from=max(s.period_from, year_start),
                        period_to=min(s.period_to, year_end), consumption=(s.consumption * time_factor(
                            s.period_from, s.period_to, year_start, year_end) if s.consumption is not None else None))
                        for s in pos.heating.slices if max(s.period_from, year_start) <= min(s.period_to, year_end))
                    pos = replace(pos, heating=replace(pos.heating, slices=slices))
                positions.append(pos)
            parts.append(replace(inp, period_start=start, period_end=end,
                cost_positions=tuple(positions), advance_payments_total=Decimal(0), heating_advance_payments_total=Decimal(0)))
        inp = replace(inp, allocation_inputs=tuple(parts))
    if inp.allocation_inputs:
        # Provider amounts describe this selected statement, so crossing a
        # fiscal boundary must not replay the same external amount twice.
        parts, external_seen = [], set()
        for part in inp.allocation_inputs:
            positions = []
            for pos in part.cost_positions:
                if pos.tenant_distribution_key == "EXTERNE_ABRECHNUNG" and pos.heating is None:
                    if pos.cost_entry_id in external_seen:
                        continue
                    external_seen.add(pos.cost_entry_id)
                positions.append(pos)
            parts.append(replace(part, cost_positions=tuple(positions)))
        inp = replace(inp, allocation_inputs=tuple(parts))
        results = tuple(compute_bk_settlement(part) for part in inp.allocation_inputs)
        cost_lines = [replace(line, sort_order=i) for i, line in enumerate(
            line for result in results for line in result.lines if line.line_type == "COST")]
        return _finish_result(inp, cost_lines,
            [step for result in results for step in result.computation],
            [cell for result in results for cell in result.vacancy_attribution],
            [pool for result in results for pool in result.heating_costs],
            sum((result.total_cost for result in results), Decimal(0)), results)

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
    heating_costs = []

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

        if pos.heating is not None:
            cells, heating_result = distribute_heating_position(pos, period_start=inp.period_start, period_end=inp.period_end)
            heating_costs.append(heating_result)
        else:
            # Balance the complete source once, independent of the run window.
            # Keep all lease/vacancy boundaries so short runs select the same
            # cent allocation as a full-service-period calculation.
            allocation_period = (period_for_date(inp.property.fiscal_year_start_month, inp.period_start)
                                 if pos.tenant_distribution_key == "VERBRAUCH" else None)
            cover_start, cover_end = pos.service_period_from, pos.service_period_to
            if allocation_period is not None:
                cover_start = max(cover_start, allocation_period[0])
                cover_end = min(cover_end, allocation_period[1])
            source_covers = {u.id: build_unit_cover(
                u.id, cover_start, cover_end, leases, owner_periods,
            ) for u in units}
            cells = distribute_position(pos, units, source_covers,
                period_start=inp.period_start, period_end=inp.period_end, allocation_period=allocation_period)
        if not cells:
            continue

        eff_total = heating_result.pool.total_amount if pos.heating is not None else effective_total(pos)
        if pos.heating is None and pos.tenant_distribution_key == "VERBRAUCH":
            eff_total = day_share_for_period(eff_total, pos.service_period_from, pos.service_period_to,
                *period_for_date(inp.property.fiscal_year_start_month, inp.period_start))

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
        measured_split = pos.tenant_distribution_key == "VERBRAUCH" and any(
            u.id == rep.unit_id and u.consumption_periods for u in units)
        base_share = sum((c.key_base_share for c in target_cells), Decimal(0)) if measured_split else rep.key_base_share
        time_days = None if measured_split else tenant_days
        time_total = None if measured_split else rep.time_total_days
        note = "Verbrauchsaufteilung innerhalb der Einheit: Zwischenablesungen, sonst Tage" if measured_split else _position_note(pos)

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
                distribution_base_share=base_share,
                time_share_days=time_days,
                time_share_total_days=time_total,
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
                key_base_share=base_share,
                time_share_days=time_days,
                time_share_total_days=time_total,
                exact_share=sum((c.exact for c in target_cells), Decimal(0)),
                rounded_amount=amount,
                note=note,
            )
        )
        sort_order += 1

    return _finish_result(inp, cost_lines, computation, vacancy_attr, heating_costs, total_cost)


def _finish_result(inp, cost_lines, computation, vacancy_attr, heating_costs, total_cost, period_results=()):
    # 4./5. Soll - Vorauszahlung -> Saldo (section 7), once per statement.
    sort_order = len(cost_lines)
    advance = _money(inp.advance_payments_total)
    heating_advance = _money(inp.heating_advance_payments_total)
    allocated_ids = {line.cost_entry_id for line in cost_lines}
    heating_ids = {pos.cost_entry_id for window in (inp.allocation_inputs or (inp,))
        for pos in window.cost_positions
        if pos.cost_entry_id in allocated_ids
        and (pos.heating is not None or pos.category_code in HEATING_CATEGORY_CODES)}
    heating_cost = (sum((result.heating_cost_total for result in period_results), Decimal('0.00'))
        if period_results else sum((line.amount for line in cost_lines if line.cost_entry_id in heating_ids), Decimal('0.00')))
    balance = _money(total_cost - advance - heating_advance)

    split_advances = bool(heating_ids) or heating_advance != 0

    # Advance-payment + balance lines (BGH: Abzug der Vorauszahlungen + Saldo).
    lines: list[ResultLine] = list(cost_lines)
    lines.append(
        ResultLine(
            line_type="ADVANCE_PAYMENT",
            label="Betriebskosten-Vorauszahlungen" if split_advances else "Geleistete Vorauszahlungen",
            amount=advance,
            sort_order=sort_order,
            note=f"Quelle: {inp.advance_payments_source}",
        )
    )
    sort_order += 1
    if split_advances:
        lines.append(ResultLine(line_type='SUBTOTAL', label='Betriebskosten abzüglich Vorauszahlungen',
            amount=_money(total_cost - heating_cost - advance), sort_order=sort_order,
            note=f'Quelle: {inp.advance_payments_source}'))
        sort_order += 1
        lines.append(ResultLine(line_type='ADVANCE_PAYMENT', label='Heizkosten-Vorauszahlungen',
            amount=heating_advance, sort_order=sort_order, note=f'Quelle: {inp.heating_advance_payments_source}'))
        sort_order += 1
        lines.append(ResultLine(line_type='SUBTOTAL', label='Heizkosten abzüglich Vorauszahlungen',
            amount=_money(heating_cost - heating_advance), sort_order=sort_order,
            note=f'Quelle: {inp.heating_advance_payments_source}'))
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

    if inp.uncredited_advance_payments_total:
        amount = format(inp.uncredited_advance_payments_total, ",.2f").translate(str.maketrans(",.", ".,"))
        lines.append(ResultLine(line_type="SUBTOTAL", amount=Decimal("0.00"), sort_order=sort_order + 1,
            label=f"Nicht angerechnet: Zahlungen außerhalb des Mietverhältnisses: {amount} €"))

    return EngineResult(
        lines=tuple(lines),
        computation=tuple(computation),
        total_cost=_money(total_cost),
        advance_payments_total=advance,
        heating_advance_payments_total=heating_advance,
        heating_cost_total=_money(heating_cost),
        balance=balance,
        vacancy_attribution=tuple(vacancy_attr),
        heating_costs=tuple(heating_costs),
        period_results=period_results,
        period_inputs=inp.allocation_inputs,
    )


def _position_note(pos: CostPositionInput) -> str:
    """A short, prueffest note for a position's line (section 5/8)."""
    if pos.heating is not None:
        return "HeizkostenV intern: Grund-/Verbrauchsanteil; Grundkosten zeitanteilig (§9b), keine Verbrauchsschätzung"
    if pos.tenant_distribution_key == "EXTERNE_ABRECHNUNG":
        return "extern abgerechnet (Messdienst)"
    if pos.tenant_apportionable == "TEILWEISE_PRUEFEN":
        return "nur Mieteranteil (CO2)"
    if pos.tenant_apportionable == "GEMISCHT_PRUEFEN":
        return "nur umlagefaehiger Anteil"
    return ""
