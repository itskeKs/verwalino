"""Cash reserve development, cent rounding and deterministic planning."""
from datetime import date
from decimal import Decimal as D

import pytest

from app.engine.reserve_ledger import money, reserve_totals, planned_reserve_entries


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



def test_late_cash_is_a_separate_signed_balance_component():
    totals = reserve_totals(D('26000'), D('0'), D('0'), D('0'), D('999'))
    assert totals['closing_balance'] == '26999.00'
    assert totals['contributions_total'] == '0.00'
    assert totals['late_cash_total'] == '999.00'



def test_asset_report_lists_late_cash_after_opening_balance():
    from app.engine.vermoegensbericht import build_vermoegensbericht
    from app.engine.types import VermoegensberichtInput, ReserveInput, PropertyInput
    result = build_vermoegensbericht(VermoegensberichtInput(
        period_start=date(2026, 1, 1), period_end=date(2026, 12, 31),
        property=PropertyInput(id='synthetic', name='Synthetic WEG', management_type='WEG'),
        reserve=ReserveInput(opening_balance=D('100'), withdrawals_total=D('0'), late_cash_total=D('10')),
        reserve_contributions_total=D('0'), bank_balances=(), receivables=(), payables=()))
    lines = sorted(result.lines, key=lambda row: row.sort_order)
    assert lines[0].line_type == 'RESERVE_OPENING'
    assert lines[1].label == 'Nachträgliche Buchungen zum abgeschlossenen Jahr'


def test_asset_report_selects_late_cash_and_adjustments_by_structured_type():
    from dataclasses import replace
    from types import SimpleNamespace
    from app.engine.vermoegensbericht import build_vermoegensbericht
    from app.engine.types import VermoegensberichtInput, ReserveInput, PropertyInput
    from app.services.vermoegensbericht_pdf import build_vermoegensbericht_html
    result = build_vermoegensbericht(VermoegensberichtInput(
        period_start=date(2026, 1, 1), period_end=date(2026, 12, 31),
        property=PropertyInput(id='synthetic', name='Synthetic WEG', management_type='WEG'),
        reserve=ReserveInput(opening_balance=D('26000'), withdrawals_total=D('0'), late_cash_total=D('999'), overrides_total=D('-99')),
        reserve_contributions_total=D('0'), bank_balances=(), receivables=(), payables=()))
    # Changing human-readable notes must not change financial row selection.
    lines = [replace(line, note='Reviewed cash component') for line in result.lines]
    run = SimpleNamespace(period_start=date(2026, 1, 1), period_end=date(2026, 12, 31), snapshot_sha256=None)
    html = build_vermoegensbericht_html(run, lines)
    assert 'Nachträgliche Buchungen zum abgeschlossenen Jahr' in html
    assert '999,00 EUR' in html and '-99,00 EUR' in html and '26.900,00 EUR' in html
    closing = next(line for line in result.lines if line.line_type == 'RESERVE_CLOSING')
    assert 'Korrekturen' in closing.note and 'Nachträgliche Buchungen' in closing.note
