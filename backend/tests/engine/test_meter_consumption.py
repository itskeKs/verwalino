"""T31: exact, unambiguous boundary readings; no IO or estimated consumption."""
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest

from app.engine.meter_consumption import MeterReadingInput, MeterConsumptionError, derive_consumption

START, END = date(2026, 1, 1), date(2026, 12, 31)


def reading(day, value):
    return MeterReadingInput(id=uuid4(), reading_date=day, value=Decimal(value))


def test_exact_consumption_and_original_boundary_identity():
    first, last = reading(START, "100.0000"), reading(END, "250.1250")
    result = derive_consumption((last, first), START, END)
    assert result.consumption == Decimal("150.1250")
    assert result.start_reading is first
    assert result.end_reading is last
    with pytest.raises(FrozenInstanceError):
        result.consumption = Decimal("0")


def test_nearest_pair_is_selected_without_mutating_the_sequence():
    first, last = reading(START, "10"), reading(END, "40")
    rows = [reading(date(2026, 1, 4), "12"), last, reading(date(2026, 12, 30), "35"), first]
    original = rows.copy()
    assert derive_consumption(rows, START, END).consumption == Decimal("30")
    assert rows == original


def test_window_includes_both_thirty_first_days():
    rows = (reading(date(2025, 12, 1), "10"), reading(date(2027, 1, 31), "40"))
    assert derive_consumption(rows, START, END).consumption == Decimal("30")


@pytest.mark.parametrize("rows", [(), (reading(START, "10"),),
    (reading(date(2025, 11, 30), "10"), reading(END, "40")),
    (reading(START, "10"), reading(date(2027, 2, 1), "40")),
])
def test_missing_or_out_of_window_basis_requires_manual_input(rows):
    with pytest.raises(MeterConsumptionError, match="Handeingabe"):
        derive_consumption(rows, START, END)


def test_equidistant_dates_are_not_arbitrarily_prioritized():
    rows = (reading(date(2025, 12, 31), "9"), reading(date(2026, 1, 2), "10"), reading(END, "40"))
    with pytest.raises(MeterConsumptionError, match="Handeingabe"):
        derive_consumption(rows, START, END)


def test_duplicate_boundary_day_is_ambiguous_even_for_equal_values():
    rows = (reading(START, "10"), reading(START, "10"), reading(END, "40"))
    with pytest.raises(MeterConsumptionError, match="Handeingabe"):
        derive_consumption(rows, START, END)


def test_one_reading_cannot_supply_both_boundaries():
    with pytest.raises(MeterConsumptionError, match="Handeingabe"):
        derive_consumption((reading(START, "10"),), START, START)


def test_decreasing_counter_never_invents_a_rollover():
    with pytest.raises(MeterConsumptionError, match="Handeingabe"):
        derive_consumption((reading(START, "40"), reading(END, "10")), START, END)


def test_measured_zero_is_not_missing():
    assert derive_consumption((reading(START, "10.0000"), reading(END, "10.0000")), START, END).consumption == Decimal("0.0000")


def test_fourth_decimal_at_maximum_is_exact_even_with_low_ambient_precision():
    rows = (reading(START, "9999999999.9998"), reading(END, "9999999999.9999"))
    with localcontext() as ctx:
        ctx.prec = 6
        assert derive_consumption(rows, START, END).consumption == Decimal("0.0001")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "1.00001", "10000000000"])
def test_invalid_selected_value_cannot_be_replaced_by_a_more_distant_candidate(value):
    rows = (reading(START, value), reading(date(2026, 1, 2), "10"), reading(END, "40"))
    with pytest.raises(MeterConsumptionError, match="Handeingabe"):
        derive_consumption(rows, START, END)


@pytest.mark.parametrize("value", [1.5, "10", None])
def test_untyped_values_are_not_coerced_or_rounded(value):
    rows = (MeterReadingInput(uuid4(), START, value), reading(END, "40"))
    with pytest.raises(MeterConsumptionError, match="Handeingabe"):
        derive_consumption(rows, START, END)


def test_inverted_period_is_rejected():
    with pytest.raises(MeterConsumptionError, match="Handeingabe"):
        derive_consumption((reading(START, "10"), reading(END, "40")), END, START)
