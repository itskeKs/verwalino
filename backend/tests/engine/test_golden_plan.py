"""Golden tests for the M4 Wirtschaftsplan + Vermoegensbericht engines.

m4-weg-abrechnung.md section 9.1 #8 (plan -> monthly rates) and section 6
(Vermoegensbericht). Hand-computed, exact cents.

Pure-function tests -- no DB, no server.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.engine.types import (
    AssetItemInput,
    BankBalanceInput,
    OwnerPeriodInput,
    PlannedCostInput,
    PropertyInput,
    ReserveInput,
    UnitInput,
    VermoegensberichtInput,
    WirtschaftsplanInput,
)
from app.engine.vermoegensbericht import build_vermoegensbericht
from app.engine.weg import WegValidationError
from app.engine.wirtschaftsplan import compute_wirtschaftsplan

YEAR_START = date(2025, 1, 1)
YEAR_END = date(2025, 12, 31)


def _U(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def _plan_owner(result, owner_id):
    return next(o for o in result.owners if o.owner_profile_id == owner_id)


# ===========================================================================
# Golden 8: Wirtschaftsplan -> 12 monthly rates (m4 section 9.1 #8)
#   geplante Kosten 11.999,00 + Ruecklagenzufuehrung 1,00 = 12.000,00;
#   MEA 600/400 -> Jahresvorschuss 7200/4800 -> Monatsraten 600,00/400,00.
# ===========================================================================
class TestGolden8PlanMonthlyClean:
    def _scenario(self):
        u1, u2 = _U(11), _U(12)
        oa, ob = _U(21), _U(22)
        units = (
            UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("600")),
            UnitInput(id=u2, number="2", usage_type="WOHNUNG", mea=Decimal("400")),
        )
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=oa, valid_from=date(2000, 1, 1), valid_to=None),
            OwnerPeriodInput(unit_id=u2, owner_profile_id=ob, valid_from=date(2000, 1, 1), valid_to=None),
        )
        planned = (
            PlannedCostInput(
                category_id=_U(91), category_code="HEIZUNG", label="Bewirtschaftung",
                planned_amount=Decimal("11999.00"), distribution_key="MEA",
                cost_group="UMLAGEFAEHIG",
            ),
        )
        return WirtschaftsplanInput(
            plan_year=2025, period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, planned_costs=planned,
            reserve_contribution_total=Decimal("1.00"),
        )

    def test_annual_and_monthly(self):
        r = compute_wirtschaftsplan(self._scenario())
        # Total volume 12000. Owner A 600/1000 = 7200; B 400/1000 = 4800.
        assert r.total_advance_volume == Decimal("12000.00")
        a = _plan_owner(r, _U(21))
        b = _plan_owner(r, _U(22))
        assert a.annual_advance == Decimal("7200.00")
        assert b.annual_advance == Decimal("4800.00")
        # Monthly: 7200/12 = 600.00 ; 4800/12 = 400.00.
        assert a.monthly_amount == Decimal("600.00")
        assert b.monthly_amount == Decimal("400.00")
        assert sum(a.monthly_rates, Decimal("0.00")) == Decimal("7200.00")
        assert sum(b.monthly_rates, Decimal("0.00")) == Decimal("4800.00")
        assert len(a.monthly_rates) == 12

    def test_subtotals(self):
        r = compute_wirtschaftsplan(self._scenario())
        assert r.subtotal_umlagefaehig == Decimal("11999.00")
        assert r.subtotal_nicht_umlagefaehig == Decimal("0.00")
        assert r.reserve_contribution_total == Decimal("1.00")


# ===========================================================================
# Golden 8b: "krummer" Jahresvorschuss 7201.00 -> 12 rates summengenau.
#   7201/12 = 600.0833... -> 600.08 * 12 = 7200.96, remainder 4 cents to the
#   first 4 months -> 4x 600.09 + 8x 600.08, Σ = 7201.00.
# ===========================================================================
class TestGolden8bPlanMonthlyOdd:
    def _scenario(self):
        u1 = _U(11)
        oa = _U(21)
        units = (UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("1000")),)
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=oa, valid_from=date(2000, 1, 1), valid_to=None),
        )
        planned = (
            PlannedCostInput(
                category_id=_U(91), category_code="HEIZUNG", label="x",
                planned_amount=Decimal("7201.00"), distribution_key="MEA",
                cost_group="UMLAGEFAEHIG",
            ),
        )
        return WirtschaftsplanInput(
            plan_year=2025, period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, planned_costs=planned,
        )

    def test_rates_sum_exact(self):
        r = compute_wirtschaftsplan(self._scenario())
        a = _plan_owner(r, _U(21))
        assert a.annual_advance == Decimal("7201.00")
        assert sum(a.monthly_rates, Decimal("0.00")) == Decimal("7201.00")
        # 4 months get the extra cent (600.09), 8 months 600.08.
        rates = sorted(a.monthly_rates)
        assert rates.count(Decimal("600.08")) == 8
        assert rates.count(Decimal("600.09")) == 4


# ===========================================================================
# Vermoegensbericht (m4 section 6): reserve development automatic + manual.
# ===========================================================================
class TestVermoegensbericht:
    def _scenario(self):
        return VermoegensberichtInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            reserve=ReserveInput(
                opening_balance=Decimal("20000.00"),
                withdrawals_total=Decimal("8000.00"),
            ),
            reserve_contributions_total=Decimal("6000.00"),
            bank_balances=(
                BankBalanceInput(
                    bank_account_id=_U(31), label="Girokonto",
                    account_type="LAUFEND", balance=Decimal("15000.00"),
                ),
                BankBalanceInput(
                    bank_account_id=_U(32), label="Ruecklagenkonto",
                    account_type="RUECKLAGE", balance=Decimal("18000.00"),
                ),
            ),
            receivables=(
                AssetItemInput(label="Hausgeldrueckstand Mueller", amount=Decimal("500.00")),
            ),
            payables=(
                AssetItemInput(label="offene Handwerkerrechnung", amount=Decimal("1200.00")),
            ),
        )

    def test_reserve_development_automatic(self):
        r = build_vermoegensbericht(self._scenario())
        dev = r.reserve
        assert dev.opening_balance == Decimal("20000.00")
        assert dev.contributions_total == Decimal("6000.00")
        assert dev.withdrawals_total == Decimal("8000.00")
        assert dev.closing_balance == Decimal("18000.00")

    def test_manual_lines_flagged(self):
        r = build_vermoegensbericht(self._scenario())
        bank = [ln for ln in r.lines if ln.line_type == "BANK_BALANCE"]
        assert len(bank) == 2
        assert all(ln.is_manual for ln in bank)
        recv = [ln for ln in r.lines if ln.line_type == "RECEIVABLE"]
        pay = [ln for ln in r.lines if ln.line_type == "PAYABLE"]
        assert recv[0].is_manual and pay[0].is_manual
        # Reserve closing is NOT manual (it is derived).
        closing = next(ln for ln in r.lines if ln.line_type == "RESERVE_CLOSING")
        assert closing.is_manual is False
        assert closing.amount == Decimal("18000.00")

    def test_totals(self):
        r = build_vermoegensbericht(self._scenario())
        assert r.bank_balance_total == Decimal("33000.00")
        assert r.receivables_total == Decimal("500.00")
        assert r.payables_total == Decimal("1200.00")


class TestPlanFailLoud:
    def test_negative_reserve_escalates(self):
        u1 = _U(11)
        units = (UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("1000")),)
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=_U(21), valid_from=date(2000, 1, 1), valid_to=None),
        )
        inp = WirtschaftsplanInput(
            plan_year=2025, period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, planned_costs=(),
            reserve_contribution_total=Decimal("-1.00"),
        )
        with pytest.raises(WegValidationError, match="reserve"):
            compute_wirtschaftsplan(inp)
