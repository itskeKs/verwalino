"""Connect the pure T45 allocator to BK tenant/vacancy cells (§9b).

Grundkosten use area/volume × occupied days; consumption uses measured slice
quantities, never a time-based estimate. Vacancy retains gross owner cost.
"""
from decimal import Decimal

from app.engine.distribution import Cell
from app.engine.heizkosten import calculate_heating_costs
from app.engine.periods import CoverPeriod, KIND_TENANT, KIND_VACANCY, day_count
from app.engine.rounding import distribute_amount, DistributionError
from app.engine.types import (
    APPORTIONABLE_AMOUNT_REQUIRED, CostPositionInput, HeatingAllocationResult,
    HeatingCostPoolInput, HeatingUnitInput,
)


def distribute_heating_position(pos: CostPositionInput):
    heating = pos.heating
    if heating is None:
        raise DistributionError('Missing heating configuration')
    total = pos.gross_amount
    if pos.tenant_apportionable in APPORTIONABLE_AMOUNT_REQUIRED:
        if pos.apportionable_tenant_amount is None:
            raise DistributionError('Heating requires an explicit apportionable tenant amount')
        total = pos.apportionable_tenant_amount
    service_days = day_count(pos.service_period_from, pos.service_period_to)
    # Each unit's full cover represents the SAME selected source period.
    first_unit = heating.slices[0].unit_id if heating.slices else None
    overlap = sum(day_count(s.period_from, s.period_to) for s in heating.slices if s.unit_id == first_unit)
    if not 0 < overlap <= service_days:
        raise DistributionError('Invalid heating service-period cover')
    total = distribute_amount(total, [Decimal(overlap), Decimal(service_days-overlap)])[0]
    pool = HeatingCostPoolInput(
        heating.kind, total, heating.consumption_fraction, heating.base_key,
        heating.consumption_recorded, heating.mandatory_seventy_percent,
        tuple(HeatingUnitInput(s.id, s.base_quantity * day_count(s.period_from,s.period_to), s.consumption)
              for s in heating.slices),
    )
    try:
        result = calculate_heating_costs((pool,), context='TENANT').pools[0]
    except ValueError as exc:
        raise DistributionError(str(exc)) from exc
    rows = {row.unit_id:row for row in result.rows}
    cells = []
    for s in heating.slices:
        row = rows[s.id]
        amount = row.amount_due if s.lease_agreement_id is not None else row.gross_amount
        cover = CoverPeriod(s.unit_id, KIND_TENANT if s.lease_agreement_id else KIND_VACANCY,
            s.period_from, s.period_to, lease_agreement_id=s.lease_agreement_id,
            owner_profile_id=s.owner_profile_id)
        # Two separate bases live in the frozen heating trace, not in a false
        # single numerator/denominator in the legacy relational COST line.
        cells.append(Cell(s.unit_id,cover,amount,amount,None,None,cover.days,overlap))
    return cells, HeatingAllocationResult(pos.cost_entry_id,result,heating.slices)
