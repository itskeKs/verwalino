"""Connect the pure T45 allocator to BK tenant/vacancy cells (§9b).

Grundkosten use area/volume × occupied days; full-period consumption is split
by interim readings or, when missing, by days within the unit. Vacancy retains gross owner cost.
"""
from dataclasses import replace
from decimal import Decimal

from app.engine.distribution import Cell
from app.engine.heizkosten import calculate_heating_costs
from app.engine.periods import CoverPeriod, KIND_TENANT, KIND_VACANCY, day_count, time_factor
from app.engine.rounding import day_share_for_period, DistributionError
from app.engine.types import (
    APPORTIONABLE_AMOUNT_REQUIRED, CostPositionInput, HeatingAllocationResult,
    HeatingCostPoolInput, HeatingUnitInput,
)


def distribute_heating_position(pos: CostPositionInput, *, period_start=None, period_end=None):
    heating = pos.heating
    if heating is None:
        raise DistributionError('Missing heating configuration')
    total = pos.gross_amount
    if pos.tenant_apportionable in APPORTIONABLE_AMOUNT_REQUIRED:
        if pos.apportionable_tenant_amount is None:
            raise DistributionError('Heating requires an explicit apportionable tenant amount')
        total = pos.apportionable_tenant_amount
    if not heating.slices:
        raise DistributionError('Invalid heating service-period cover')
    start = max(pos.service_period_from, min(s.period_from for s in heating.slices))
    end = min(pos.service_period_to, max(s.period_to for s in heating.slices))
    overlap = day_count(start, end)
    if overlap <= 0:
        raise DistributionError('Invalid heating service-period cover')
    # Annual slices set each unit's weight. Only source-period occupants may
    # share that unit's pool, just as for ordinary consumption sources.
    slices = []
    for unit_id in dict.fromkeys(s.unit_id for s in heating.slices):
        annual = [s for s in heating.slices if s.unit_id == unit_id]
        selected = [s for s in annual if max(start, s.period_from) <= min(end, s.period_to)]
        quantities = [(s.consumption * time_factor(s.period_from, s.period_to, start, end)
                       if s.consumption is not None else Decimal(0)) for s in selected]
        annual_total = sum((s.consumption or Decimal(0) for s in annual), Decimal(0))
        source_total = sum(quantities, Decimal(0))
        delimited = len(selected) != len(annual) or any(
            s.period_from < start or s.period_to > end for s in selected)
        for s, quantity in zip(selected, quantities):
            a, b = max(start, s.period_from), min(end, s.period_to)
            consumption = s.consumption
            if consumption is not None and delimited:
                fraction = quantity / source_total if source_total else Decimal(day_count(a, b)) / overlap
                consumption = annual_total * fraction
            slices.append(replace(s, period_from=a, period_to=b, consumption=consumption))
    total = day_share_for_period(total, pos.service_period_from, pos.service_period_to, start, end)
    pool = HeatingCostPoolInput(
        heating.kind, total, heating.consumption_fraction, heating.base_key,
        heating.consumption_recorded, heating.mandatory_seventy_percent,
        tuple(HeatingUnitInput(s.id, s.base_quantity * day_count(s.period_from,s.period_to), s.consumption)
              for s in slices),
    )
    try:
        result = calculate_heating_costs((pool,), context='TENANT').pools[0]
    except ValueError as exc:
        raise DistributionError(str(exc)) from exc
    rows = {row.unit_id:row for row in result.rows}
    cells = []
    statement_amounts = []
    for s in slices:
        row = rows[s.id]
        amount = row.amount_due if s.lease_agreement_id is not None else row.gross_amount
        cover = CoverPeriod(s.unit_id, KIND_TENANT if s.lease_agreement_id else KIND_VACANCY,
            s.period_from, s.period_to, lease_agreement_id=s.lease_agreement_id,
            owner_profile_id=s.owner_profile_id)
        start = max(cover.start, period_start or cover.start)
        end = min(cover.end, period_end or cover.end)
        if start > end:
            continue
        # Select already-balanced occupant cents, never reallocate the pool.
        amount = day_share_for_period(amount, cover.start, cover.end, start, end)
        statement_amounts.append((s.id, start, end, amount))
        cover = replace(cover, start=start, end=end)
        # Two separate bases live in the frozen heating trace, not in a false
        # single numerator/denominator in the legacy relational COST line.
        cells.append(Cell(s.unit_id,cover,amount,amount,None,None,cover.days,overlap))
    return cells, HeatingAllocationResult(pos.cost_entry_id,result,tuple(slices),tuple(statement_amounts))
