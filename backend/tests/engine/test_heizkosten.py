"""T45: HeizkostenV §§7/8/12 with separate, already delimited cost pools."""
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from uuid import UUID

import pytest

A, B, C = UUID(int=1), UUID(int=2), UUID(int=3)
D = Decimal


def unit(identity=A, base="40", consumption="100"):
    from app.engine.types import HeatingUnitInput
    return HeatingUnitInput(identity, D(base), None if consumption is None else D(consumption))


def pool(**overrides):
    from app.engine.types import HeatingCostPoolInput
    return HeatingCostPoolInput(**{
        "kind": "HEATING", "total_amount": D("1000.00"),
        "consumption_fraction": D("0.70"), "base_key": "AREA",
        "consumption_recorded": True, "mandatory_seventy_percent": False,
        "units": (unit(), unit(B, "60", "300")), **overrides,
    })


def calculate(*pools, context="TENANT"):
    from app.engine.heizkosten import calculate_heating_costs
    return calculate_heating_costs(tuple(pools), context=context)


def test_heizkosten_separate_heating_and_hot_water_with_exact_expected_amounts():
    water = pool(kind="HOT_WATER", total_amount=D("400.00"), consumption_fraction=D("0.50"),
                 units=(unit(A, "40", "3"), unit(B, "60", "1")))
    result = calculate(pool(), water)
    heat, water_result = result.pools
    assert (heat.base_cost_total, heat.consumption_cost_total) == (D("300.00"), D("700.00"))
    assert [(r.base_amount, r.consumption_amount, r.amount_due) for r in heat.rows] == [
        (D("120.00"), D("175.00"), D("295.00")),
        (D("180.00"), D("525.00"), D("705.00")),
    ]
    assert [(r.base_amount, r.consumption_amount, r.amount_due) for r in water_result.rows] == [
        (D("80.00"), D("150.00"), D("230.00")),
        (D("120.00"), D("50.00"), D("170.00")),
    ]
    assert result.gross_total == D("1400.00")
    assert result.reduction_total == D("0.00")
    assert result.amount_due == D("1400.00")


@pytest.mark.parametrize("fraction", ["0.50", "0.60", "0.70"])
def test_heizkosten_allows_statutory_consumption_fraction_range(fraction):
    result = calculate(pool(consumption_fraction=D(fraction)))
    assert result.amount_due == D("1000.00")
    assert result.pools[0].base_cost_total == D("1000.00") * (1 - D(fraction))


@pytest.mark.parametrize("fraction", ["0.49", "0.71", "-0.1", "0", "1", "NaN", "Infinity"])
def test_heizkosten_rejects_invalid_consumption_fraction(fraction):
    with pytest.raises(ValueError):
        calculate(pool(consumption_fraction=D(fraction)))


def test_heizkosten_missing_capture_reduces_only_affected_pool_not_other_users_or_water():
    missing = pool(consumption_recorded=False, units=(unit(A, "40", None), unit(B, "60", None)))
    water = pool(kind="HOT_WATER", total_amount=D("400.00"), consumption_fraction=D("0.50"),
                 units=(unit(A, "40", "3"), unit(B, "60", "1")))
    result = calculate(missing, water)
    heat = result.pools[0]
    assert heat.method == "NON_CONSUMPTION"
    assert [(r.base_amount, r.consumption_amount, r.reduction_amount, r.amount_due) for r in heat.rows] == [
        (D("400.00"), D("0.00"), D("60.00"), D("340.00")),
        (D("600.00"), D("0.00"), D("90.00"), D("510.00")),
    ]
    assert all(r.reduction_reason == "HeizkostenV §12(1): 15%" for r in heat.rows)
    assert [r.amount_due for r in result.pools[1].rows] == [D("230.00"), D("170.00")]
    assert result.gross_total == D("1400.00")
    assert result.reduction_total == D("150.00")
    assert result.amount_due == D("1250.00")


def test_heizkosten_missing_hot_water_capture_is_independent_of_heating():
    water = pool(kind="HOT_WATER", total_amount=D("400.00"), consumption_fraction=D("0.50"),
                 consumption_recorded=False, units=(unit(A, "40", None), unit(B, "60", None)))
    result = calculate(pool(), water)
    assert [r.reduction_amount for r in result.pools[0].rows] == [D("0.00"), D("0.00")]
    assert [r.amount_due for r in result.pools[1].rows] == [D("136.00"), D("204.00")]
    assert result.reduction_total == D("60.00")
    assert result.amount_due == D("1340.00")


def test_heizkosten_weg_owner_is_not_given_tenant_reduction():
    missing = pool(consumption_recorded=False, units=(unit(A, "40", None), unit(B, "60", None)))
    result = calculate(missing, context="WEG_OWNER")
    assert result.reduction_total == D("0.00")
    assert result.amount_due == D("1000.00")
    assert all(r.reduction_reason is None for r in result.pools[0].rows)


def test_heizkosten_cent_rounding_and_stable_order():
    source = pool(total_amount=D("0.05"), consumption_fraction=D("0.50"),
                  units=(unit(C, "1", "1"), unit(A, "1", "1"), unit(B, "1", "1")))
    result = calculate(source)
    assert [r.unit_id for r in result.pools[0].rows] == [A, B, C]
    assert [r.amount_due for r in result.pools[0].rows] == [D("0.02"), D("0.02"), D("0.01")]
    assert result == calculate(replace(source, units=tuple(reversed(source.units))))
    assert result.gross_total == result.amount_due == D("0.05")


def test_heizkosten_reduction_rounds_per_user_and_retains_every_cent():
    result = calculate(pool(total_amount=D("0.10"), consumption_recorded=False, units=(unit(A, "1", None),)))
    assert result.reduction_total == D("0.02")
    assert result.amount_due == D("0.08")
    assert result.amount_due + result.reduction_total == result.gross_total


def test_heizkosten_zero_cost_and_recorded_zero_consumption_are_explicit():
    result = calculate(pool(total_amount=D("0.00"), units=(unit(A, "1", "0"),)))
    assert result.gross_total == result.amount_due == D("0.00")
    with pytest.raises(ValueError):
        calculate(pool(units=(unit(A, "1", "0"),)))


def test_heizkosten_missing_single_reading_is_not_silently_estimated_or_zeroed():
    with pytest.raises(ValueError):
        calculate(pool(units=(unit(A, "40", None), unit(B, "60", "300"))))
    with pytest.raises(ValueError):
        calculate(pool(consumption_recorded=False))


@pytest.mark.parametrize("key", ["AREA", "HEATED_AREA", "VOLUME"])
def test_heizkosten_heating_base_keys_are_explicit(key):
    assert calculate(pool(base_key=key)).amount_due == D("1000.00")


def test_heizkosten_hot_water_uses_area_not_heated_volume():
    with pytest.raises(ValueError):
        calculate(pool(kind="HOT_WATER", base_key="VOLUME"))


def test_heizkosten_special_building_condition_requires_seventy_percent():
    assert calculate(pool(mandatory_seventy_percent=True)).amount_due == D("1000.00")
    with pytest.raises(ValueError):
        calculate(pool(mandatory_seventy_percent=True, consumption_fraction=D("0.60")))


@pytest.mark.parametrize("field,value", [
    ("total_amount", D("-0.01")), ("total_amount", D("1.001")),
    ("total_amount", D("NaN")), ("total_amount", D("Infinity")),
    ("total_amount", 1000.0), ("consumption_fraction", 0.7),
    ("base_key", "ASSUMED"), ("kind", "COMBINED"), ("consumption_recorded", 1),
])
def test_heizkosten_rejects_invalid_pool_values(field, value):
    with pytest.raises(ValueError):
        calculate(pool(**{field: value}))


@pytest.mark.parametrize("base,consumption", [
    (D("-1"), D("1")), (D("NaN"), D("1")), (40.0, D("1")),
    (D("40"), D("-1")), (D("40"), D("Infinity")), (D("40"), 100.0),
])
def test_heizkosten_rejects_invalid_unit_quantities(base, consumption):
    with pytest.raises(ValueError):
        calculate(pool(units=(replace(unit(), base_quantity=base, consumption=consumption),)))


def test_heizkosten_rejects_ambiguous_or_empty_universes():
    for pools in [(), (pool(), pool()), (pool(units=()),), (pool(units=(unit(), unit())),),
                  (pool(units=(unit(A, "0", "1"),)),)]:
        with pytest.raises(ValueError):
            calculate(*pools)
    with pytest.raises(ValueError):
        calculate(pool(), context="UNKNOWN")


def test_heizkosten_deterministic_conservation_over_many_costs_and_weights():
    for cents in range(1, 101):
        source = pool(total_amount=D(cents) / 100, consumption_fraction=D("0.63"),
                      units=(unit(A, "13", "5"), unit(B, "29", "17"), unit(C, "7", "2")))
        result = calculate(source)
        assert sum((r.gross_amount for r in result.pools[0].rows), D(0)) == source.total_amount
        assert result.amount_due + result.reduction_total == result.gross_total
        assert result == calculate(source)
        with pytest.raises(FrozenInstanceError):
            result.amount_due = D("99")
