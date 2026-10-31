"""Cash reserve development, cent rounding and deterministic planning."""
from datetime import date
from decimal import Decimal as D

import pytest

from app.engine.reserve_ledger import money, reserve_totals, planned_reserve_entries, hausgeld_reserve_share


def test_commercial_rounding_and_signed_override_preserve_the_balance():
    assert money(D("1000.005")) == D("1000.01")
    totals = reserve_totals(D("20000"), D("6000"), D("8000"), D("-0.005"))
    assert totals["closing_balance"] == "17999.99"
    assert totals["overdrawn"] is False


def test_overdrawn_cash_reserve_is_flagged_without_clipping():
    assert reserve_totals(D("1"), D("0"), D("2"), D("0")) == {
        "opening_balance": "1.00", "contributions_total": "0.00",
        "withdrawals_total": "2.00", "overrides_total": "0.00",
        "late_cash_total": "0.00",
        "closing_balance": "-1.00", "overdrawn": True,
    }


def test_plan_is_split_by_frozen_owner_unit_weights_then_months():
    cells = [("u2", "o2", D("1")), ("u1", "o1", D("1")), ("u3", "o3", D("1"))]
    rows = planned_reserve_entries(D("1000"), cells, date(2025, 4, 1))
    assert rows == planned_reserve_entries(D("1000"), list(reversed(cells)), date(2025, 4, 1))
    assert len(rows) == 36
    assert sum((D(row["amount"]) for row in rows), D(0)) == D("1000.00")
    assert sum((D(row["amount"]) for row in rows if row["unit_id"] == "u1"), D(0)) == D("333.34")
    assert rows[-1]["date"] == "2026-03-01"


@pytest.mark.parametrize("amount", [D("NaN"), D("Infinity")])
def test_nonfinite_money_is_rejected(amount):
    with pytest.raises(ValueError):
        money(amount)


def test_random_owner_changes_keep_unit_ratios_and_cash_rounding_exact():
    from fractions import Fraction
    from random import Random
    rng = Random(36)
    for sequence in range(400):
        # Units can have different cent-exact plan ratios and several co-owners.
        planned, owners, history = [], [], {}
        for unit in range(rng.randint(1, 8)):
            reserve = rng.randint(0, 100000)
            advance = reserve + rng.randint(1, 100000)
            planned.append({'unit_id': unit, 'amount': str(D(reserve) / 100)})
            owners.extend([{'unit_id': unit, 'annual_advance': str(D(advance // 2) / 100)},
                           {'unit_id': unit, 'annual_advance': str(D(advance - advance // 2) / 100)}])
            for change in range(rng.randint(2, 8)):
                history[f'{sequence}-{unit}-{change}'] = unit
        expected_total = actual_total = D(0)
        for _ in range(rng.randint(5, 30)):
            payer = rng.choice(list(history))  # includes former owners and successors
            unit = history[payer]
            amount = rng.randint(1, 1000000) * rng.choice([-1, 1])
            reserve = Fraction(planned[unit]['amount'])
            advance = sum(Fraction(o['annual_advance']) for o in owners if o['unit_id'] == unit)
            exact_cents = Fraction(amount) * reserve / advance
            rounded = (abs(exact_cents.numerator) * 2 + exact_cents.denominator) // (2 * exact_cents.denominator)
            expected = D(rounded if amount > 0 else -rounded) / 100
            actual = hausgeld_reserve_share(D(amount) / 100, {unit}, planned, owners)
            assert actual == expected
            assert hausgeld_reserve_share(-D(amount) / 100, {unit}, planned, owners) == -actual
            expected_total += expected
            actual_total += actual
        assert actual_total == expected_total


def test_late_cash_is_a_separate_signed_balance_component():
    totals = reserve_totals(D('26000'), D('0'), D('0'), D('0'), D('999'))
    assert totals['closing_balance'] == '26999.00'
    assert totals['contributions_total'] == '0.00'
    assert totals['late_cash_total'] == '999.00'
