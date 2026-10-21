"""Pure engine tests for the M7 dunning module.

No DB, no I/O -- tests the pure functions in app.engine.dunning against
hand-computed expected values. Covers:
  - Overdue detection with various grace periods
  - Escalation level computation (1 -> 2 -> 3 -> capped)
  - Fee calculation (defaults + custom)
  - Dunning assembly (golden test: arrears -> notice with correct amounts)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.engine.dunning import (
    DEFAULT_FEES,
    MAX_LEVEL,
    OverdueItem,
    assemble_dunning,
    dunning_fee,
    find_overdue_items,
    next_dunning_level,
)
from app.engine.mietkonto import OpenItem


# ---------------------------------------------------------------------------
# find_overdue_items
# ---------------------------------------------------------------------------
class TestFindOverdueItems:
    def _make_item(self, year: int, month: int, ptype: str,
                    soll: str, ist: str, open_amt: str) -> OpenItem:
        return OpenItem(
            period_year=year, period_month=month, payment_type=ptype,
            soll=Decimal(soll), ist=Decimal(ist), open_amount=Decimal(open_amt),
        )

    def test_arrears_only(self):
        """Only items with open_amount > 0 are overdue."""
        items = [
            self._make_item(2025, 3, "KALTMIETE", "500.00", "0.00", "500.00"),
            self._make_item(2025, 3, "NK_VORAUSZAHLUNG", "100.00", "50.00", "50.00"),
            self._make_item(2025, 1, "KALTMIETE", "500.00", "500.00", "0.00"),    # balanced
            self._make_item(2025, 2, "KALTMIETE", "500.00", "600.00", "-100.00"), # credit
        ]
        overdue = find_overdue_items(items, date(2025, 7, 1))
        assert len(overdue) == 2
        assert all(it.open_amount > 0 for it in overdue)

    def test_grace_period_excludes_recent(self):
        """Grace period pushes the deadline forward."""
        items = [
            self._make_item(2025, 6, "KALTMIETE", "500.00", "0.00", "500.00"),
        ]
        # Due June 1; grace 30 days -> deadline July 1; ref June 15 -> not overdue
        assert len(find_overdue_items(items, date(2025, 6, 15), grace_days=30)) == 0
        # ref July 2 -> past deadline -> overdue
        assert len(find_overdue_items(items, date(2025, 7, 2), grace_days=30)) == 1

    def test_sorted_output(self):
        """Output is sorted by (year, month, type)."""
        items = [
            self._make_item(2025, 5, "NK_VORAUSZAHLUNG", "100.00", "0.00", "100.00"),
            self._make_item(2025, 3, "KALTMIETE", "500.00", "0.00", "500.00"),
            self._make_item(2025, 4, "KALTMIETE", "500.00", "0.00", "500.00"),
        ]
        overdue = find_overdue_items(items, date(2025, 7, 1))
        keys = [(it.period_year, it.period_month, it.payment_type) for it in overdue]
        assert keys == sorted(keys)

    def test_empty_input(self):
        assert find_overdue_items([], date(2025, 7, 1)) == []


# ---------------------------------------------------------------------------
# next_dunning_level
# ---------------------------------------------------------------------------
class TestNextDunningLevel:
    def test_escalation_chain(self):
        assert next_dunning_level(0) == 1
        assert next_dunning_level(1) == 2
        assert next_dunning_level(2) == 3

    def test_capped_at_max(self):
        assert next_dunning_level(3) == MAX_LEVEL
        assert next_dunning_level(100) == MAX_LEVEL


# ---------------------------------------------------------------------------
# dunning_fee
# ---------------------------------------------------------------------------
class TestDunningFee:
    def test_defaults(self):
        assert dunning_fee(1) == Decimal("0.00")
        assert dunning_fee(2) == Decimal("5.00")
        assert dunning_fee(3) == Decimal("10.00")

    def test_unknown_level_returns_zero(self):
        assert dunning_fee(99) == Decimal("0.00")

    def test_custom_overrides(self):
        custom = {1: Decimal("1.00"), 2: Decimal("3.50")}
        assert dunning_fee(1, custom) == Decimal("1.00")
        assert dunning_fee(2, custom) == Decimal("3.50")
        # Level 3 not in custom -> 0.00
        assert dunning_fee(3, custom) == Decimal("0.00")


# ---------------------------------------------------------------------------
# assemble_dunning
# ---------------------------------------------------------------------------
class TestAssembleDunning:
    def _items_2_months(self) -> list[OverdueItem]:
        return [
            OverdueItem(2025, 3, "KALTMIETE", Decimal("500.00"), Decimal("0.00"), Decimal("500.00")),
            OverdueItem(2025, 3, "NK_VORAUSZAHLUNG", Decimal("100.00"), Decimal("0.00"), Decimal("100.00")),
            OverdueItem(2025, 4, "KALTMIETE", Decimal("500.00"), Decimal("0.00"), Decimal("500.00")),
            OverdueItem(2025, 4, "NK_VORAUSZAHLUNG", Decimal("100.00"), Decimal("0.00"), Decimal("100.00")),
        ]

    def test_golden_level1(self):
        """Golden: 2 months arrears, no prior -> level 1, fee 0, total = principal."""
        result = assemble_dunning(self._items_2_months(), 0, date(2025, 7, 1))
        assert result is not None
        assert result.level == 1
        assert result.principal_amount == Decimal("1200.00")
        assert result.fee_amount == Decimal("0.00")
        assert result.total_amount == Decimal("1200.00")
        assert result.due_date == date(2025, 7, 15)
        assert len(result.overdue_items) == 4

    def test_level2_with_fee(self):
        items = [OverdueItem(2025, 3, "KALTMIETE", Decimal("500.00"), Decimal("0.00"), Decimal("500.00"))]
        result = assemble_dunning(items, 1, date(2025, 7, 15))
        assert result is not None
        assert result.level == 2
        assert result.fee_amount == Decimal("5.00")
        assert result.total_amount == Decimal("505.00")

    def test_level3_with_fee(self):
        items = [OverdueItem(2025, 3, "KALTMIETE", Decimal("500.00"), Decimal("0.00"), Decimal("500.00"))]
        result = assemble_dunning(items, 2, date(2025, 8, 1))
        assert result is not None
        assert result.level == 3
        assert result.fee_amount == Decimal("10.00")
        assert result.total_amount == Decimal("510.00")

    def test_empty_returns_none(self):
        assert assemble_dunning([], 0, date(2025, 7, 1)) is None

    def test_override_fee(self):
        items = [OverdueItem(2025, 3, "KALTMIETE", Decimal("500.00"), Decimal("0.00"), Decimal("500.00"))]
        result = assemble_dunning(items, 0, date(2025, 7, 1), override_fee=Decimal("7.77"))
        assert result is not None
        assert result.fee_amount == Decimal("7.77")
        assert result.total_amount == Decimal("507.77")

    def test_override_level(self):
        items = [OverdueItem(2025, 3, "KALTMIETE", Decimal("500.00"), Decimal("0.00"), Decimal("500.00"))]
        result = assemble_dunning(items, 0, date(2025, 7, 1), override_level=3)
        assert result is not None
        assert result.level == 3
        assert result.fee_amount == Decimal("10.00")

    def test_snapshot_serializable(self):
        items = [OverdueItem(2025, 3, "KALTMIETE", Decimal("500.00"), Decimal("0.00"), Decimal("500.00"))]
        result = assemble_dunning(items, 0, date(2025, 7, 1))
        assert result is not None
        snap = result.items_snapshot()
        assert len(snap) == 1
        assert snap[0]["period_year"] == 2025
        assert snap[0]["open_amount"] == "500.00"
        # JSON-serializable (str values)
        import json
        json.dumps(snap)

    def test_due_days_parameter(self):
        items = [OverdueItem(2025, 3, "KALTMIETE", Decimal("500.00"), Decimal("0.00"), Decimal("500.00"))]
        result = assemble_dunning(items, 0, date(2025, 7, 1), due_days=30)
        assert result is not None
        assert result.due_date == date(2025, 7, 31)

    def test_decimal_discipline_no_float(self):
        """All amounts must be Decimal, never float."""
        items = [OverdueItem(2025, 3, "KALTMIETE", Decimal("333.33"), Decimal("0.00"), Decimal("333.33"))]
        result = assemble_dunning(items, 0, date(2025, 7, 1))
        assert result is not None
        assert isinstance(result.principal_amount, Decimal)
        assert isinstance(result.fee_amount, Decimal)
        assert isinstance(result.total_amount, Decimal)


def test_t35_interest_700_sixty_calendar_days():
    item = OverdueItem(
        period_year=2026, period_month=1, payment_type="RENT",
        soll=Decimal("700.00"), ist=Decimal("0.00"),
        open_amount=Decimal("700.00"), due_date=date(2026, 1, 1),
    )
    result = assemble_dunning(
        [item], 0, date(2026, 3, 2), base_interest_rate=Decimal("2.50"),
    )
    assert result.interest_amount == Decimal("8.63")
    assert result.total_amount == Decimal("708.63")
    assert result.interest_snapshot == {
        "base_interest_rate": "2.50", "annual_rate": "7.50",
        "reference_date": "2026-03-02", "items": [{
            "payment_type": "RENT", "period_year": 2026, "period_month": 1,
            "due_date": "2026-01-01", "days": 60,
            "principal": "700.00", "interest": "8.63",
        }],
    }


def _t35_item(amount="700.00", due=date(2026, 1, 1)):
    return OverdueItem(2026, 1, "SONDERZAHLUNG", Decimal(amount), Decimal("0"), Decimal(amount), due)


@pytest.mark.parametrize("base,expected", [("-0.88", "4.74"), ("2.50123456789", "8.63"), ("-5.00", "0.00")])
def test_t35_exact_rates_without_intermediate_rounding(base, expected):
    result = assemble_dunning([_t35_item()], 0, date(2026, 3, 2), base_interest_rate=Decimal(base))
    assert result.interest_amount == Decimal(expected)
    assert result.interest_snapshot["base_interest_rate"] == base
    assert Decimal(result.interest_snapshot["annual_rate"]) == Decimal(base) + Decimal("5")


def test_t35_calendar_days_independent_of_grace_and_separate_dues():
    items = [_t35_item(due=date(2026, 1, 15)), _t35_item(due=date(2026, 2, 25)), _t35_item(due=date(2026, 3, 3))]
    result = assemble_dunning(items, 2, date(2026, 3, 2), base_interest_rate=Decimal("2.50"))
    assert [i["days"] for i in result.interest_snapshot["items"]] == [46, 5, 0]
    assert result.interest_amount == Decimal("7.34")
    assert result.total_amount == Decimal("2117.34")
    assert sum(Decimal(i["interest"]) for i in result.interest_snapshot["items"]) == result.interest_amount


def test_t35_half_up_and_sum_of_rounded_positions():
    # 36.50 * 5% / 365 = 0.005 per day: each item rounds UP, never bankers' rounding.
    result = assemble_dunning([_t35_item("36.50"), _t35_item("36.50")], 0, date(2026, 1, 2), base_interest_rate=Decimal("0"))
    assert result.interest_amount == Decimal("0.02")
    assert result.total_amount == Decimal("73.02")


def test_t35_does_not_round_percentage_before_calculation():
    result = assemble_dunning([_t35_item("1000000.00")], 0, date(2026, 1, 2), base_interest_rate=Decimal("2.50123456789"))
    assert result.interest_snapshot["annual_rate"] == "7.50123456789"
    assert result.interest_amount == Decimal("205.51")


@pytest.mark.parametrize("rate", [2.5, "2.50", Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), Decimal("1e101")])
def test_t35_engine_rejects_inexact_nonfinite_and_unbounded_rates(rate):
    with pytest.raises(ValueError):
        assemble_dunning([_t35_item()], 0, date(2026, 3, 2), base_interest_rate=rate)


@pytest.mark.parametrize("amount,fee,base", [
    ("1000000000000", "0", None), ("999999999999.99", "0.01", None),
    ("999999999999.99", "0", Decimal("2.50")),
    ("700", "NaN", None), ("700", "Infinity", None),
])
def test_t35_money_overflow_or_nonfinite_rejected(amount, fee, base):
    with pytest.raises(ValueError):
        assemble_dunning([_t35_item(amount)], 0, date(2026, 3, 2), override_fee=Decimal(fee), base_interest_rate=base)


def test_t35_legacy_and_zero_rate_are_distinct():
    legacy = assemble_dunning([_t35_item()], 0, date(2026, 3, 2))
    assert legacy.interest_amount == Decimal("0.00")
    assert legacy.interest_snapshot is None
    with_rate = assemble_dunning([_t35_item()], 0, date(2026, 3, 2), base_interest_rate=Decimal("0"))
    assert with_rate.interest_amount == Decimal("5.75")


def test_t35_is_independent_of_callers_decimal_precision_and_rounding():
    from decimal import ROUND_DOWN, localcontext
    with localcontext() as ctx:
        ctx.prec = 6
        ctx.rounding = ROUND_DOWN
        result = assemble_dunning([_t35_item()], 0, date(2026, 3, 2), base_interest_rate=Decimal("2.50"))
        assert result.interest_amount == Decimal("8.63")
        assert result.total_amount == Decimal("708.63")
        assert dunning_fee(1, {1: Decimal("999999999999.99")}) == Decimal("999999999999.99")


def test_t35_grace_only_filters_and_does_not_subtract_interest_days():
    item = OpenItem(2026, 1, 'KALTMIETE', Decimal('700.00'), Decimal('0.00'), Decimal('700.00'), due_date=date(2026, 1, 1))
    overdue = find_overdue_items([item], date(2026, 3, 2), grace_days=30)
    result = assemble_dunning(overdue, 0, date(2026, 3, 2), base_interest_rate=Decimal('2.50'))
    assert result.interest_snapshot['items'][0]['days'] == 60
    assert result.interest_amount == Decimal('8.63')
