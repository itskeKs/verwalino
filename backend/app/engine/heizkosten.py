"""Pure HeizkostenV allocation for separately established §7/§8 cost pools.

A missing reading is an error. Only explicit non-consumption accounting selects
an area/base fallback and §12(1)'s 15% tenant reduction. This is not a §9a
estimate, an exception assessment, or certification of a lawful cost pool.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, localcontext
from uuid import UUID

from app.engine.rounding import CENT, distribute_amount
from app.engine.types import (
    HeatingCostPoolInput,
    HeatingCostResult,
    HeatingPoolResult,
    HeatingUnitInput,
    HeatingUnitResult,
)

ZERO = Decimal("0.00")
REDUCTION = Decimal("0.15")


def _quantity(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative Decimal")
    if value >= Decimal("1000000000000000000"):
        raise ValueError(f"{name} exceeds the supported range")


def _validate_pool(pool: HeatingCostPoolInput) -> tuple[HeatingUnitInput, ...]:
    if pool.kind not in {"HEATING", "HOT_WATER"}:
        raise ValueError("Separate HEATING and HOT_WATER pools required")
    if pool.base_key not in {"AREA", "HEATED_AREA", "VOLUME"}:
        raise ValueError("Unknown heating base key")
    if pool.kind == "HOT_WATER" and pool.base_key != "AREA":
        raise ValueError("Hot-water base costs require residential/use area (§8)")
    _quantity(pool.total_amount, "Total cost")
    if pool.total_amount >= Decimal("1000000000000") or pool.total_amount.quantize(CENT) != pool.total_amount:
        raise ValueError("Total cost must fit NUMERIC(14,2) without rounding")
    _quantity(pool.consumption_fraction, "Consumption fraction")
    if not Decimal("0.50") <= pool.consumption_fraction <= Decimal("0.70"):
        raise ValueError("Consumption fraction must be 50–70%, base fraction 30–50%")
    if type(pool.consumption_recorded) is not bool or type(pool.mandatory_seventy_percent) is not bool:
        raise ValueError("Capture and building-condition flags must be explicit booleans")
    if pool.mandatory_seventy_percent and (pool.kind != "HEATING" or pool.consumption_fraction != Decimal("0.70")):
        raise ValueError("Assessed §7 building condition requires 70% heating consumption")
    if not isinstance(pool.units, tuple) or not pool.units:
        raise ValueError("At least one immutable unit input required")
    identities = set()
    for unit in pool.units:
        if not isinstance(unit, HeatingUnitInput) or not isinstance(unit.unit_id, UUID) or unit.unit_id in identities:
            raise ValueError("Unique UUID unit identities required")
        identities.add(unit.unit_id)
        _quantity(unit.base_quantity, "Base quantity")
        if pool.consumption_recorded:
            _quantity(unit.consumption, "Recorded consumption")
        elif unit.consumption is not None:
            raise ValueError("Non-consumption accounting must not silently ignore supplied readings")
    if sum((u.base_quantity for u in pool.units), ZERO) == 0:
        raise ValueError("Base quantities must have a positive total")
    if pool.consumption_recorded and pool.total_amount > 0 and sum((u.consumption for u in pool.units), ZERO) == 0:
        raise ValueError("Positive cost cannot be consumption-distributed over zero recorded consumption")
    return tuple(sorted(pool.units, key=lambda unit: unit.unit_id.int))


def _allocate_pool(pool: HeatingCostPoolInput, context: str) -> HeatingPoolResult:
    units = _validate_pool(pool)
    bases = [u.base_quantity for u in units]
    if pool.consumption_recorded:
        base_total, consumption_total = distribute_amount(
            pool.total_amount, [1 - pool.consumption_fraction, pool.consumption_fraction],
        )
        base_amounts = distribute_amount(base_total, bases)
        # A zero-cost pool may legitimately have recorded zero usage everywhere.
        consumption_amounts = (distribute_amount(consumption_total, [u.consumption for u in units])
                               if pool.total_amount else [ZERO] * len(units))
    else:
        base_total, consumption_total = pool.total_amount, ZERO
        base_amounts = distribute_amount(base_total, bases)
        consumption_amounts = [ZERO] * len(units)
    reduced = not pool.consumption_recorded and context == "TENANT"
    rows = []
    for unit, base, consumption in zip(units, base_amounts, consumption_amounts, strict=True):
        gross = base + consumption
        # §12 is an individual user's right, not a cost redistributed to others.
        deduction = (gross * REDUCTION).quantize(CENT, rounding=ROUND_HALF_UP) if reduced else ZERO
        rows.append(HeatingUnitResult(
            unit.unit_id, unit.base_quantity, unit.consumption, base, consumption,
            gross, deduction, gross - deduction,
            "HeizkostenV §12(1): 15%" if reduced else None,
        ))
    return HeatingPoolResult(
        pool.kind, pool.base_key, pool.consumption_fraction, pool.consumption_recorded,
        "CONSUMPTION" if pool.consumption_recorded else "NON_CONSUMPTION",
        pool.total_amount, base_total, consumption_total,
        sum((r.reduction_amount for r in rows), ZERO), sum((r.amount_due for r in rows), ZERO), tuple(rows),
    )


def calculate_heating_costs(pools: tuple[HeatingCostPoolInput, ...], *, context: str) -> HeatingCostResult:
    """Allocate already separated costs; context must explicitly select TENANT/WEG_OWNER.

    The caller assesses applicable exceptions and supplies correct pool totals,
    base quantities and comparable consumption units. No meter estimation,
    fuel conversion, combined-plant separation or additional 3% rights occurs.
    """
    if context not in {"TENANT", "WEG_OWNER"}:
        raise ValueError("Explicit TENANT or WEG_OWNER context required")
    if not isinstance(pools, tuple) or not pools or any(not isinstance(p, HeatingCostPoolInput) for p in pools):
        raise ValueError("At least one immutable heating/hot-water cost pool required")
    if len({p.kind for p in pools}) != len(pools):
        raise ValueError("Duplicate cost pool would double-bill the same component")
    with localcontext() as decimal_context:
        decimal_context.prec = 50
        results = tuple(_allocate_pool(p, context) for p in sorted(pools, key=lambda p: p.kind))
        return HeatingCostResult(
            results, sum((p.total_amount for p in results), ZERO),
            sum((p.reduction_total for p in results), ZERO), sum((p.amount_due for p in results), ZERO),
        )
