"""Per-key weight vectors (section 2) + time x key chaining (section 3.2).

For ONE cost position the engine builds, for every (unit, time-slice) cell of
the whole property, the EXACT ungerounded share, then rounds the whole set
ONCE via distribute_amount (ADR-0005 reihenfolge-disziplin).  This module
produces those cells; settlement.py assembles them into the addressee's bill
and the vacancy attribution.

Distribution universe (section 3.2 "Reihenfolge der Verteilungs-Universen"):
  1. key-share of each UNIT over the whole property (universe = all
     participating units), exact: w_i / sum(w).
  2. time split of that unit-share across the unit's tenant/vacancy slices,
     exact: x time_factor(slice vs service period).
  -> cell exact factor f_{i,s} = (w_i / sum(w)) * time_factor_{i,s}.
The complete source cover is balanced independently of the selected statement.
Sources may cross settlement-period boundaries: each run selects only its share.
Consumption weights always describe the full property period; interim readings
split only that unit's share, with days used between missing interim boundaries.
Adjacent windows select cumulative cents, preserving tenant/vacancy reconciliation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from app.engine.periods import (
    CoverPeriod,
    KIND_TENANT,
    overlap_days,
    time_factor,
)
from app.engine.rounding import DistributionError, distribute_amount, day_share_for_period
from app.engine.types import (
    APPORTIONABLE_AMOUNT_REQUIRED,
    CostPositionInput,
    DIRECT_KEYS,
    UnitInput,
    WEIGHTED_KEYS,
)


@dataclass(frozen=True)
class Cell:
    """One (unit, time-slice) result cell for a position after rounding."""

    unit_id: uuid.UUID
    cover: CoverPeriod
    exact: Decimal  # ungerounded share (key x time)
    amount: Decimal  # final cent amount (after the single Largest-Remainder pass)
    # The key-base numerator/denominator and time fraction for the rechenweg.
    key_base_share: Decimal | None
    key_base_total: Decimal | None
    time_days: int
    time_total_days: int


def _unit_weight(unit: UnitInput, key: str) -> Decimal:
    """The exact weight of `unit` for a weighted distribution key (section 2.2).

    Raises EngineValidationError-style DistributionError if the required base
    quantity is missing (NULL) -- NEVER a silent 0 (that would mis-distribute
    cost unnoticed, section 2.6 #2).
    """
    if key == "MEA":
        val = unit.mea
    elif key == "WOHNFLAECHE":
        val = unit.area
    elif key == "BEHEIZTE_FLAECHE":
        val = unit.heated_area
    elif key == "EINHEIT":
        return Decimal(1)
    elif key == "PERSONENZAHL":
        val = None if unit.person_count is None else Decimal(unit.person_count)
    elif key == "VERBRAUCH":
        val = unit.consumption
    else:
        raise DistributionError(f"Unknown weighted key: {key}")

    if val is None:
        raise DistributionError(
            f"Unit {unit.number} ({unit.id}) is missing the base quantity for "
            f"key {key}; cannot distribute (escalate -- no silent 0). "
            f"section 2.6"
        )
    if val < 0:
        raise DistributionError(
            f"Unit {unit.number} ({unit.id}) has negative base for key {key}: {val}"
        )
    return val


def effective_total(position: CostPositionInput) -> Decimal:
    """The Mieter-relevant total to distribute for a position (section 6.2).

    - UMLAGEFAEHIG / MANUELL_PRUEFEN(bestaetigt) / EINHEIT etc: gross_amount.
    - GEMISCHT_PRUEFEN / TEILWEISE_PRUEFEN (CO2): apportionable_tenant_amount
      (the explicitly herausgerechnete Mieteranteil) -- REQUIRED, else escalate.
    - EXTERNE_ABRECHNUNG: sum of the per-unit external tenant amounts.
    - MANUELLER_BETRAG: sum of the per-unit manual amounts.
    """
    key = position.tenant_distribution_key
    appo = position.tenant_apportionable

    if key == "EXTERNE_ABRECHNUNG":
        return sum((e.tenant_amount for e in position.external_amounts), Decimal(0))
    if key == "MANUELLER_BETRAG":
        return sum((m.amount for m in position.manual_amounts), Decimal(0))

    if appo in APPORTIONABLE_AMOUNT_REQUIRED:
        if position.apportionable_tenant_amount is None:
            raise DistributionError(
                f"Position {position.category_code} ({position.cost_entry_id}) "
                f"has tenant_apportionable={appo} but no apportionable_tenant_amount; "
                f"the umzulegender Mieteranteil is undefined (escalate -- do not "
                f"silently apportion gross). section 6.2"
            )
        return position.apportionable_tenant_amount

    return position.gross_amount


def is_billable_to_tenant(position: CostPositionInput) -> bool:
    """Whether a position is apportioned to the tenant at all (section 6.2).

    NICHT_UMLAGEFAEHIG never appears in a Mieter-BK (invariant I-F). For
    MANUELL_PRUEFEN the position is only billed if it carries a positive
    effective amount that the clerk consciously set as apportionable; here we
    treat a missing/None gross with that flag as not-billable. All other
    apportionability flags map to a defined effective total via effective_total.
    """
    return position.tenant_apportionable != "NICHT_UMLAGEFAEHIG"


def distribute_position(
    position: CostPositionInput,
    units: list[UnitInput],
    covers_by_unit: dict[uuid.UUID, list[CoverPeriod]],
    *,
    period_start: date | None = None,
    period_end: date | None = None,
    allocation_period: tuple[date, date] | None = None,
) -> list[Cell]:
    """Distribute one position over all (unit, slice) cells, rounded once.

    Returns one Cell per (participating unit, time-slice). Direct-amount keys
    (EXTERNE_ABRECHNUNG / MANUELLER_BETRAG) assign the per-unit amount and then
    split it by time across that unit's slices; weighted keys build the
    key-share x time factor for every cell. The single Largest-Remainder pass
    runs over the complete source cover so sum(amounts) == effective_total
    exactly. Optional inclusive run bounds then select each cell's day share.
    """
    key = position.tenant_distribution_key
    if key is None:
        raise DistributionError(
            f"Position {position.category_code} ({position.cost_entry_id}) has no "
            f"tenant_distribution_key; cannot distribute."
        )

    units_by_id = {unit.id: unit for unit in units}
    total = effective_total(position)
    sp_from = position.service_period_from
    sp_to = position.service_period_to
    if key == "VERBRAUCH" and allocation_period is not None:
        # Delimit the source once to the property year, then use annual unit
        # quantities and the complete property-year occupant cover.
        total = day_share_for_period(total, sp_from, sp_to, *allocation_period)
        sp_from, sp_to = allocation_period

    # Build (cover, exact-weight, base_share, base_total) per cell.
    cell_specs: list[tuple[uuid.UUID, CoverPeriod, Decimal, Decimal | None, Decimal | None]] = []

    if key == "EXTERNE_ABRECHNUNG":
        # The external statement (Messdienst) already delivers a per-(unit,
        # occupancy period) tenant share. Assign each value DIRECTLY to the
        # matching cover slice -- NO key distribution and NO time re-split
        # (that is the whole point of EXTERNE_ABRECHNUNG; section 2.5/5.2).
        _check_direct_sum(position, key, total)
        for ext in position.external_amounts:
            covers = covers_by_unit.get(ext.unit_id, [])
            if ext.period_from is None or ext.period_to is None:
                # Undated provider amounts describe the selected run. Later
                # tenancies outside it must not make that assignment ambiguous.
                covers = [c for c in covers if overlap_days(
                    c.start, c.end, period_start or sp_from, period_end or sp_to,
                )]
            cover = _match_slice(
                covers,
                ext.period_from,
                ext.period_to,
            )
            if cover is None:
                raise DistributionError(
                    f"Position {position.category_code} ({position.cost_entry_id}): "
                    f"external amount for unit {ext.unit_id} period "
                    f"[{ext.period_from}..{ext.period_to}] matches no occupancy "
                    f"slice -- escalate. section 5.2"
                )
            cell_specs.append((ext.unit_id, cover, ext.tenant_amount, ext.tenant_amount, None))
    elif key == "MANUELLER_BETRAG":
        # A deliberately clerk-set per-unit amount (Sonderfall). Split it by
        # time across that unit's slices (single-tenant unit -> whole amount
        # to that tenant; a mid-period change splits proportionally by days).
        _check_direct_sum(position, key, total)
        per_unit = _direct_per_unit_amounts(position, key)
        for unit in units:
            amt = per_unit.get(unit.id)
            if amt is None:
                continue
            for cover in covers_by_unit.get(unit.id, []):
                f = time_factor(sp_from, sp_to, cover.start, cover.end)
                if f == 0:
                    continue
                weight = amt * f  # exact contribution of this cell
                cell_specs.append((unit.id, cover, weight, amt, None))
    elif key in WEIGHTED_KEYS:
        participating = [u for u in units if _participates(u, key)]
        weights = {u.id: _unit_weight(u, key) for u in participating}
        weight_sum = sum(weights.values(), Decimal(0))
        if weight_sum == 0:
            raise DistributionError(
                f"Position {position.category_code} ({position.cost_entry_id}): "
                f"sum of weights for key {key} is zero (no participating unit "
                f"with a positive base) -- escalate. section 2.6"
            )
        for unit in participating:
            w = weights[unit.id]
            key_share = w / weight_sum
            unit_covers = covers_by_unit.get(unit.id, [])
            quantities = [sum((quantity * time_factor(a, b, c.start, c.end)
                              for a, b, quantity in unit.consumption_periods), Decimal(0))
                          for c in unit_covers] if key == "VERBRAUCH" else []
            quantity_total = sum(quantities, Decimal(0))
            for index, cover in enumerate(unit_covers):
                f = time_factor(sp_from, sp_to, cover.start, cover.end)
                if f == 0:
                    continue
                if key == "VERBRAUCH" and unit.consumption_periods and quantity_total:
                    f = quantities[index] / quantity_total
                weight = key_share * f  # exact factor of this cell
                base_share = w * f if key == "VERBRAUCH" and unit.consumption_periods else w
                cell_specs.append((unit.id, cover, weight, base_share, weight_sum))
    else:
        raise DistributionError(f"Unsupported distribution key: {key}")

    if not cell_specs:
        return []

    # Single Largest-Remainder pass over all cells (ADR-0005).
    cell_weights = [spec[2] for spec in cell_specs]
    rounded = distribute_amount(total, cell_weights)

    sp_total = _sp_total_days(sp_from, sp_to)
    cells: list[Cell] = []
    for (unit_id, cover, exact_weight, base_share, base_total), amount in zip(
        cell_specs, rounded
    ):
        source_cover = cover
        start = max(cover.start, sp_from, period_start or sp_from)
        end = min(cover.end, sp_to, period_end or sp_to)
        if start > end:
            continue
        cover = replace(cover, start=start, end=end)
        if key != "EXTERNE_ABRECHNUNG":
            source_start, source_end = max(source_cover.start, sp_from), min(source_cover.end, sp_to)
            amount = day_share_for_period(amount, source_start, source_end, start, end)
            selection_factor = time_factor(source_start, source_end, start, end)
            exact_weight *= selection_factor
            unit = units_by_id[unit_id]
            if key == "VERBRAUCH" and unit.consumption_periods:
                base_share *= selection_factor
        # time_days is the days of the SERVICE PERIOD this slice covered (the
        # numerator of the time factor), NOT the full slice length -- so the
        # rechenweg reads "<billed days>/<service-period days>" (BGH; matters
        # when the service period is shorter than the slice, e.g. a Dezember-
        # Nachberechnung in a full-year tenancy).
        billed_days = overlap_days(sp_from, sp_to, cover.start, cover.end)
        cells.append(
            Cell(
                unit_id=unit_id,
                cover=cover,
                exact=exact_weight if key in DIRECT_KEYS else (total * exact_weight),
                amount=amount,
                key_base_share=base_share,
                key_base_total=base_total,
                time_days=billed_days,
                time_total_days=sp_total,
            )
        )
    return cells


def _sp_total_days(sp_from, sp_to) -> int:
    from app.engine.periods import day_count

    return day_count(sp_from, sp_to)


def _participates(unit: UnitInput, key: str) -> bool:
    """Whether a unit participates in a weighted key.

    A unit participates if it has the required base quantity. For area/heated
    keys a unit with NULL area (e.g. a Stellplatz with no Wohnflaeche) does NOT
    participate (weight 0 by absence), rather than triggering a hard error --
    EXCEPT we still escalate if NO unit participates (handled by the caller).
    For EINHEIT every unit participates. For MEA, a NULL mea means the unit is
    not in the MEA universe.

    Distinction vs _unit_weight's hard error: _unit_weight errors when a unit
    that we DECIDED participates lacks its base. Here we decide participation.
    A unit explicitly carrying the base participates; one lacking it is simply
    out of this key's universe (section 2.2 "Stellplaetze ... Gewicht 0").
    """
    if key == "EINHEIT":
        return True
    if key == "MEA":
        return unit.mea is not None and unit.mea > 0
    if key == "WOHNFLAECHE":
        return unit.area is not None and unit.area > 0
    if key == "BEHEIZTE_FLAECHE":
        return unit.heated_area is not None and unit.heated_area > 0
    if key == "PERSONENZAHL":
        return unit.person_count is not None and unit.person_count > 0
    if key == "VERBRAUCH":
        return unit.consumption is not None and unit.consumption > 0
    return False


def _direct_items(
    position: CostPositionInput, key: str
) -> list[tuple[uuid.UUID, Decimal]]:
    if key == "EXTERNE_ABRECHNUNG":
        return [(e.unit_id, e.tenant_amount) for e in position.external_amounts]
    return [(m.unit_id, m.amount) for m in position.manual_amounts]


def _direct_per_unit_amounts(
    position: CostPositionInput, key: str
) -> dict[uuid.UUID, Decimal]:
    """Aggregate the per-unit direct amounts (MANUELLER_BETRAG)."""
    per_unit: dict[uuid.UUID, Decimal] = {}
    for unit_id, amt in _direct_items(position, key):
        per_unit[unit_id] = per_unit.get(unit_id, Decimal(0)) + amt
    return per_unit


def _check_direct_sum(
    position: CostPositionInput, key: str, total: Decimal
) -> None:
    """Plausibility check for direct-amount keys (section 2.5).

    Sum of per-unit/per-slice amounts must equal the position's effective
    total (== that sum, by effective_total) -- here we re-assert the amounts
    are present and consistent, and that they are cent-exact, so the single
    rounding pass is idempotent and the ausgewiesene Gesamtsumme is consistent.
    Escalates on an empty amount set.
    """
    items = _direct_items(position, key)
    if not items:
        raise DistributionError(
            f"Position {position.category_code} ({position.cost_entry_id}) uses key "
            f"{key} but carries no per-unit amounts -- escalate. section 2.5"
        )
    s = sum((amt for _, amt in items), Decimal(0))
    if s != total:
        raise DistributionError(
            f"Position {position.category_code} ({position.cost_entry_id}): sum of "
            f"per-unit {key} amounts ({s}) != position effective total ({total}) "
            f"-- escalate. section 2.5"
        )


def _match_slice(
    covers: list[CoverPeriod],
    period_from,
    period_to,
) -> CoverPeriod | None:
    """Find the tenant cover slice an external amount belongs to.

    If the external amount carries no occupancy period (period_from/_to None),
    it applies to the unit as a whole; we attach it to the single tenant slice
    (or escalate via None if the unit has multiple tenant slices and no period
    is given -- ambiguous). With a period given, the slice with the largest
    day-overlap wins (the Messdienst's Zwischenablesung aligns to the change).
    """
    tenant_slices = [c for c in covers if c.kind == KIND_TENANT]
    if not tenant_slices:
        return None
    if period_from is None or period_to is None:
        # No period -> only unambiguous if exactly one tenant slice.
        return tenant_slices[0] if len(tenant_slices) == 1 else None
    best = None
    best_ov = 0
    for c in tenant_slices:
        ov = overlap_days(c.start, c.end, period_from, period_to)
        if ov > best_ov:
            best_ov = ov
            best = c
    return best
