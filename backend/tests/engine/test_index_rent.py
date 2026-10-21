"""T44: pure index-rent calculation and §557b calendar boundaries."""
from datetime import date
from decimal import Decimal

import pytest


def calculate(**overrides):
    from app.engine.index_rent import calculate_index_rent
    return calculate_index_rent(**{
        "base_rent": Decimal("600.00"), "advance_payment": Decimal("150.00"),
        "old_index": Decimal("110"), "new_index": Decimal("115"),
        "previous_valid_from": date(2026, 1, 1),
        "declaration_received_on": date(2026, 11, 15), **overrides,
    })


def test_index_rent_reference_amount_and_unchanged_advance():
    result = calculate()
    assert result.base_rent == Decimal("627.27")
    assert result.previous_base_rent == Decimal("600.00")
    assert result.advance_payment == Decimal("150.00")
    assert result.valid_from == date(2027, 1, 1)
    assert result.previous_valid_from == date(2026, 1, 1)


def test_index_rent_allows_decrease_and_rounds_half_up():
    assert calculate(old_index=Decimal("115"), new_index=Decimal("110")).base_rent == Decimal("573.91")
    assert calculate(base_rent=Decimal("1.00"), old_index=Decimal("200"), new_index=Decimal("201")).base_rent == Decimal("1.01")


@pytest.mark.parametrize("receipt,expected", [
    (date(2026, 11, 1), date(2027, 1, 1)),
    (date(2026, 11, 30), date(2027, 1, 1)),
    (date(2026, 12, 31), date(2027, 2, 1)),
    (date(2028, 2, 29), date(2028, 4, 1)),
])
def test_index_rent_second_following_month(receipt, expected):
    assert calculate(declaration_received_on=receipt).valid_from == expected


def test_index_rent_requires_full_year_not_365_days():
    with pytest.raises(ValueError, match="year"):
        calculate(previous_valid_from=date(2026, 1, 2))
    assert calculate(previous_valid_from=date(2026, 1, 1)).valid_from == date(2027, 1, 1)
    with pytest.raises(ValueError, match="year"):
        calculate(previous_valid_from=date(2027, 3, 2), declaration_received_on=date(2028, 1, 31))
    assert calculate(previous_valid_from=date(2027, 3, 1), declaration_received_on=date(2028, 2, 1)).valid_from == date(2028, 4, 1)


@pytest.mark.parametrize("field,value", [
    ("old_index", Decimal("0")), ("new_index", Decimal("-1")),
    ("new_index", Decimal("NaN")), ("old_index", Decimal("Infinity")),
    ("base_rent", Decimal("-0.01")), ("advance_payment", Decimal("NaN")),
    ("base_rent", Decimal("600.001")), ("old_index", 110.0),
    ("new_index", Decimal("115.0000001")), ("base_rent", 600.0),
])
def test_index_rent_rejects_invalid_decimal_inputs(field, value):
    with pytest.raises(ValueError):
        calculate(**{field: value})


def test_index_rent_rejects_unchanged_index_and_overflow():
    with pytest.raises(ValueError):
        calculate(new_index=Decimal("110"))
    with pytest.raises(ValueError):
        calculate(base_rent=Decimal("999999999999.99"))
    with pytest.raises(ValueError):
        calculate(declaration_received_on=date(9999, 12, 1))
