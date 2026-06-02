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
