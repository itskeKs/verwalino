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


def changed_unit_covers(change=date(2026, 10, 1)):
    from datetime import timedelta
    from app.engine.periods import build_unit_cover
    from app.engine.types import LeasePeriodInput

    unit = uuid4()
    return build_unit_cover(unit, START, END, [
        LeasePeriodInput(uuid4(), unit, uuid4(), START, change - timedelta(days=1)),
        LeasePeriodInput(uuid4(), unit, uuid4(), change, None),
    ], [])


@pytest.mark.parametrize("day", [date(2026, 9, 30), date(2026, 10, 1)])
def test_either_date_at_the_lease_boundary_denotes_the_same_counter_value(day):
    from app.engine.meter_consumption import split_consumption

    result = split_consumption([reading(START, "0"), reading(day, "600"), reading(END, "1000")],
                               START, END, changed_unit_covers())
    assert [q for _, _, q in result.periods] == [Decimal(600), Decimal(400)]
    assert result.reason is None


@pytest.mark.parametrize("days", [[], [date(2026, 9, 29)]])
def test_missing_exact_interim_boundary_has_an_explicit_day_fallback_reason(days):
    from app.engine.meter_consumption import split_consumption

    result = split_consumption([reading(START, "0"), reading(END, "1000"),
                                *[reading(day, "600") for day in days]], START, END, changed_unit_covers())
    assert +result.periods[0][2] == Decimal(1000) * 273 / 365
    assert sum(q for _, _, q in result.periods) == Decimal(1000)
    assert "Tagen" in result.reason


@pytest.mark.parametrize("second_day", [date(2026, 9, 30), date(2026, 10, 1)])
def test_multiple_readings_at_the_same_boundary_refuse_an_arbitrary_split(second_day):
    from app.engine.meter_consumption import split_consumption

    with pytest.raises(MeterConsumptionError, match="Zwischenablesung.*mehrdeutig"):
        split_consumption([reading(START, "0"), reading(END, "1000"),
                           reading(date(2026, 9, 30), "600"), reading(second_day, "601")],
                          START, END, changed_unit_covers())


def test_december_interim_reading_cannot_replace_a_missing_period_end_reading():
    from app.engine.meter_consumption import split_consumption

    with pytest.raises(MeterConsumptionError, match="Perioden.*Handeingabe|Handeingabe.*Perioden"):
        split_consumption([reading(START, "0"), reading(date(2026, 12, 14), "950")],
                          START, END, changed_unit_covers(date(2026, 12, 15)))


@pytest.mark.parametrize("lease_start,lease_end,rows", [
    (date(2026, 1, 5), None, [(date(2026, 1, 5), "10"), (END, "1000")]),
    (START, date(2026, 12, 20), [(START, "0"), (date(2026, 12, 20), "990")]),
])
def test_vacancy_boundary_readings_can_supply_the_period_quantity(lease_start, lease_end, rows):
    from app.engine.meter_consumption import split_consumption
    from app.engine.periods import build_unit_cover
    from app.engine.types import LeasePeriodInput

    unit = uuid4()
    covers = build_unit_cover(unit, START, END, [
        LeasePeriodInput(uuid4(), unit, uuid4(), lease_start, lease_end),
    ], [])
    result = split_consumption([reading(day, value) for day, value in rows], START, END, covers)
    assert result.measured.consumption == Decimal(990)
    assert sum(q for _, _, q in result.periods) == Decimal(990)
    assert result.reason is None


@pytest.mark.parametrize("change,interim,value", [
    (date(2026, 1, 2), date(2026, 1, 2), "5"),
    (date(2026, 12, 31), date(2026, 12, 30), "995"),
])
def test_period_endpoint_readings_do_not_compete_with_interim_readings(change, interim, value):
    from app.engine.meter_consumption import split_consumption

    result = split_consumption([reading(START, "0"), reading(interim, value), reading(END, "1000")],
                               START, END, changed_unit_covers(change))
    assert [q for _, _, q in result.periods] == [Decimal(value), Decimal(1000) - Decimal(value)]
    assert result.reason is None


@pytest.mark.parametrize("second_day", [date(2026, 9, 30), date(2026, 10, 1)])
def test_consistent_interim_duplicates_have_one_counter_value(second_day):
    from app.engine.meter_consumption import split_consumption

    result = split_consumption([reading(START, "0"), reading(END, "1000"),
                               reading(date(2026, 9, 30), "600"), reading(second_day, "600")],
                              START, END, changed_unit_covers())
    assert [q for _, _, q in result.periods] == [Decimal(600), Decimal(400)]
    assert result.reason is None


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


def test_consistent_period_boundary_duplicates_have_one_counter_value():
    rows = (reading(START, "10"), reading(START, "10"), reading(END, "40"))
    assert derive_consumption(rows, START, END).consumption == Decimal(30)


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
