"""Golden tests for the M4 Wirtschaftsplan + Vermoegensbericht engines.

m4-weg-abrechnung.md section 9.1 #8 (plan -> monthly rates) and section 6
(Vermoegensbericht). Hand-computed, exact cents.

Pure-function tests -- no DB, no server.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest

from app.engine import weg as weg_engine
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

pytestmark = pytest.mark.no_db

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
# Golden 8c: a plan shorter than twelve months charges ITS WHOLE amount (#602).
#   One instalment per calendar month the period touches (at most twelve); the
#   largest-remainder cents go to the first instalments; the instalments sum to
#   the annual advance. Expected values are hand-computed from the domain:
#   a plan of 3,000.00 over July..September is 3 x 1,000.00, never 3 x 250.00.
# ===========================================================================
def _short_plan(start, end, amount, *, reserve="0.00"):
    unit, owner = _U(11), _U(21)
    return WirtschaftsplanInput(
        plan_year=start.year, period_start=start, period_end=end,
        property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
        units=(UnitInput(id=unit, number="1", usage_type="WOHNUNG", mea=Decimal("1000")),),
        owner_periods=(OwnerPeriodInput(unit_id=unit, owner_profile_id=owner,
                                        valid_from=date(2000, 1, 1), valid_to=None),),
        planned_costs=(PlannedCostInput(category_id=_U(91), category_code="HEIZUNG", label="x",
                                        planned_amount=Decimal(amount), distribution_key="MEA",
                                        cost_group="UMLAGEFAEHIG"),),
        reserve_contribution_total=Decimal(reserve),
    )


def _rates(*parts):
    """('142.86', 5), ('142.85', 2) -> five and two instalments."""
    return tuple(Decimal(amount) for amount, count in parts for _ in range(count))


@pytest.mark.parametrize("start,end,amount,rates", [
    # twelve months keep today's instalments
    (date(2025, 1, 1), date(2025, 12, 31), "12000.00", _rates(("1000.00", 12))),
    (date(2025, 1, 1), date(2025, 12, 31), "1000.00", _rates(("83.34", 4), ("83.33", 8))),
    # one month charges the whole amount at once
    (date(2025, 7, 1), date(2025, 7, 31), "3000.00", _rates(("3000.00", 1))),
    # the issue: three months of 3,000.00
    (date(2025, 7, 1), date(2025, 9, 30), "3000.00", _rates(("1000.00", 3))),
    # cents of an uneven amount go to the first instalments
    (date(2025, 7, 1), date(2025, 9, 30), "1000.00", _rates(("333.34", 1), ("333.33", 2))),
    (date(2025, 1, 1), date(2025, 5, 31), "1000.01", _rates(("200.01", 1), ("200.00", 4))),
    (date(2025, 1, 1), date(2025, 7, 31), "1000.00", _rates(("142.86", 5), ("142.85", 2))),
    (date(2025, 1, 1), date(2025, 11, 30), "1000.00", _rates(("90.91", 10), ("90.90", 1))),
    # a year end inside the plan
    (date(2025, 11, 1), date(2026, 1, 31), "2500.00", _rates(("833.34", 1), ("833.33", 2))),
    # a first and a last month that are only partly covered still count as whole months
    (date(2025, 7, 15), date(2025, 9, 10), "3000.00", _rates(("1000.00", 3))),
    # twelve months from the 15th touch thirteen calendar months: twelve instalments
    (date(2025, 7, 15), date(2026, 7, 14), "12000.00", _rates(("1000.00", 12))),
    # a zero plan is zero in every instalment, not an error and not a missing plan
    (date(2025, 7, 1), date(2025, 9, 30), "0.00", _rates(("0.00", 3))),
])
def test_a_short_plan_spreads_its_whole_amount_over_its_months(start, end, amount, rates):
    owner = compute_wirtschaftsplan(_short_plan(start, end, amount)).owners[0]
    assert owner.annual_advance == Decimal(amount)
    assert owner.monthly_rates == rates
    assert owner.monthly_amount == rates[0]
    assert sum(owner.monthly_rates, Decimal("0.00")) == Decimal(amount)


def test_a_short_plan_with_several_positions_units_and_a_reserve_loses_no_cent():
    # Planned costs 5,000.00 + 2,500.00 and reserve 1,500.01 = 9,000.01 over four months
    # (September..December), MEA 600/400: 5,400.006 and 3,600.004 -> the one spare cent goes
    # to the larger remainder (owner A); 5,400.01 / 4 = 1,350.0025 -> the cent to the first month.
    units = (UnitInput(id=_U(11), number="1", usage_type="WOHNUNG", mea=Decimal("600")),
             UnitInput(id=_U(12), number="2", usage_type="WOHNUNG", mea=Decimal("400")))
    owners = (OwnerPeriodInput(unit_id=_U(11), owner_profile_id=_U(21), valid_from=date(2000, 1, 1), valid_to=None),
              OwnerPeriodInput(unit_id=_U(12), owner_profile_id=_U(22), valid_from=date(2000, 1, 1), valid_to=None))
    planned = (
        PlannedCostInput(category_id=_U(91), category_code="HEIZUNG", label="Heizung",
                         planned_amount=Decimal("5000.00"), distribution_key="MEA", cost_group="UMLAGEFAEHIG"),
        PlannedCostInput(category_id=_U(92), category_code="VERWALTUNG", label="Verwaltung",
                         planned_amount=Decimal("2500.00"), distribution_key="MEA",
                         cost_group="NICHT_UMLAGEFAEHIG"),
    )
    result = compute_wirtschaftsplan(WirtschaftsplanInput(
        plan_year=2025, period_start=date(2025, 9, 1), period_end=date(2025, 12, 31),
        property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
        units=units, owner_periods=owners, planned_costs=planned,
        reserve_contribution_total=Decimal("1500.01")))
    a, b = _plan_owner(result, _U(21)), _plan_owner(result, _U(22))
    assert result.total_advance_volume == Decimal("9000.01")
    assert (a.annual_advance, b.annual_advance) == (Decimal("5400.01"), Decimal("3600.00"))
    assert a.monthly_rates == _rates(("1350.01", 1), ("1350.00", 3))
    assert b.monthly_rates == _rates(("900.00", 4))
    assert sum((r for o in result.owners for r in o.monthly_rates), Decimal("0.00")) == Decimal("9000.01")


def test_the_plan_engine_still_refuses_a_negative_planned_amount_in_a_short_plan():
    with pytest.raises(WegValidationError):
        compute_wirtschaftsplan(_short_plan(date(2025, 7, 1), date(2025, 9, 30), "-3000.00"))


# ===========================================================================
# Co-owner shares (#678): the plan splits a unit by the shares of the owners active at
# period_start, so it refuses them under the settlement's rule (#404): the sum is 1 or 100
# within the stored rounding. Hand values, 2025: unit 1 (500 MEA) belongs to Anna alone,
# unit 2 (500 MEA) to `unit2_owners`; a plan of 1,000.00 gives each unit 500.00.
# ===========================================================================
BERND, CARLA, DORA = _U(31), _U(32), _U(33)


def _co_owned_plan(unit2_owners, *, start=YEAR_START):
    """Two units of 500 MEA: unit 1 owned by Anna alone, unit 2 by `unit2_owners`.

    `unit2_owners` is a list of (owner id, share, valid_from, valid_to); shares are
    strings or None, as they are stored in ownership_history.mea_share.
    """
    u1, u2 = _U(11), _U(12)
    owners = [OwnerPeriodInput(unit_id=u1, owner_profile_id=_U(21), valid_from=date(2000, 1, 1), valid_to=None)]
    for owner, share, valid_from, valid_to in unit2_owners:
        owners.append(OwnerPeriodInput(unit_id=u2, owner_profile_id=owner, valid_from=valid_from,
                                       valid_to=valid_to, mea_share=None if share is None else Decimal(share)))
    return WirtschaftsplanInput(
        plan_year=start.year, period_start=start, period_end=YEAR_END,
        property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
        units=(UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("500")),
               UnitInput(id=u2, number="2", usage_type="WOHNUNG", mea=Decimal("500"))),
        owner_periods=tuple(owners),
        planned_costs=(PlannedCostInput(category_id=_U(91), category_code="HEIZUNG", label="x",
                                        planned_amount=Decimal("1000.00"), distribution_key="MEA",
                                        cost_group="UMLAGEFAEHIG"),),
    )


def _whole_year(owner, share):
    return (owner, share, date(2000, 1, 1), None)


def _advances(result):
    return {o.owner_profile_id: (o.annual_advance, o.monthly_rates) for o in result.owners}


class TestPlanCoOwnerShares:
    @pytest.mark.parametrize("shares,total", [
        (("50", "30"), "80"),
        (("33.33", "33.33", "33.33"), "99.99"),
        (("1", "1"), "2"),
        (("0.6", "0.3"), "0.9"),
        (("50", "60"), "110"),
    ])
    def test_shares_adding_up_to_neither_one_nor_hundred_are_refused(self, shares, total):
        owners = [_whole_year(o, s) for o, s in zip((BERND, CARLA, DORA), shares)]
        with pytest.raises(weg_engine.WegShareSumError) as caught:
            compute_wirtschaftsplan(_co_owned_plan(owners))
        exc = caught.value
        assert isinstance(exc, WegValidationError)
        assert (exc.unit_id, exc.unit_number) == (_U(12), "2")
        assert (exc.span_start, exc.span_end) == (YEAR_START, YEAR_START)
        assert exc.share_sum == Decimal(total)

    @pytest.mark.parametrize("shares", [
        ("60", "40"), ("0.6", "0.4"), ("50", "50"), ("0.5", "0.5"),
        ("0.333333", "0.666667"), ("33.333333", "66.666667"),
        ("0.333333", "0.333333", "0.333333"), ("33.333333", "33.333333", "33.333333"),
        ("20", "30", "50"),
    ])
    def test_shares_adding_up_to_one_or_hundred_within_stored_rounding_are_accepted(self, shares):
        owners = [_whole_year(o, s) for o, s in zip((BERND, CARLA, DORA), shares)]
        r = compute_wirtschaftsplan(_co_owned_plan(owners))
        advances = _advances(r)
        assert sum((a for a, _ in advances.values()), Decimal("0.00")) == Decimal("1000.00")
        assert advances[_U(21)][0] == Decimal("500.00")
        total_share = sum((Decimal(s) for s in shares), Decimal(0))
        for owner, share in zip((BERND, CARLA, DORA), shares):
            exact = Fraction(500) * Fraction(Decimal(share)) / Fraction(total_share)
            assert abs(Fraction(advances[owner][0]) - exact) < Fraction(1, 100)

    def test_percent_and_fractions_give_the_same_split(self):
        halves = compute_wirtschaftsplan(_co_owned_plan([_whole_year(BERND, "0.5"), _whole_year(CARLA, "0.5")]))
        percent = compute_wirtschaftsplan(_co_owned_plan([_whole_year(BERND, "50"), _whole_year(CARLA, "50")]))
        assert _advances(halves) == _advances(percent)
        assert _advances(percent)[BERND][0] == _advances(percent)[CARLA][0] == Decimal("250.00")

    @pytest.mark.parametrize("share", [None, "1", "100", "80", "0.3", "0"])
    def test_a_sole_owner_is_not_checked_whatever_the_share(self, share):
        r = compute_wirtschaftsplan(_co_owned_plan([_whole_year(BERND, share)]))
        assert _advances(r)[BERND][0] == Decimal("500.00")

    def test_only_the_owners_active_at_period_start_are_counted(self):
        # Carla (0.3) ended before the plan started; Dora (0.3) starts after it. At 01.01.2025
        # Bernd 0.5 and Eva 0.5 are the co-owners: they add up to 1, Carla and Dora are not looked at.
        eva = _U(34)
        r = compute_wirtschaftsplan(_co_owned_plan([
            _whole_year(BERND, "0.5"),
            (CARLA, "0.3", date(2000, 1, 1), date(2024, 12, 31)),
            (eva, "0.5", date(2024, 6, 1), None),
            (DORA, "0.3", date(2025, 7, 1), None),
        ]))
        assert set(_advances(r)) == {_U(21), BERND, eva}
        assert _advances(r)[BERND][0] == _advances(r)[eva][0] == Decimal("250.00")

    def test_the_owners_of_the_day_the_plan_starts_are_the_ones_counted(self):
        # A plan starting 01.07.2025: Carla (0.5 until 30.06.) is gone, Bernd 0.5 and Dora 0.3 from 01.07.
        with pytest.raises(weg_engine.WegShareSumError) as caught:
            compute_wirtschaftsplan(_co_owned_plan([
                _whole_year(BERND, "0.5"),
                (CARLA, "0.5", date(2000, 1, 1), date(2025, 6, 30)),
                (DORA, "0.3", date(2025, 7, 1), None),
            ], start=date(2025, 7, 1)))
        assert (caught.value.span_start, caught.value.span_end, caught.value.share_sum) == (
            date(2025, 7, 1), date(2025, 7, 1), Decimal("0.8"))

    @pytest.mark.parametrize("shares,message", [
        ((None, "0.5"), "non-positive mea_share"),
        (("0.5", None), "non-positive mea_share"),
        (("0.5", "0"), "non-positive mea_share"),
        (("1.5", "-0.5"), "non-positive mea_share"),
        ((None, None), "total mea_share is 0"),
    ])
    def test_missing_or_non_positive_shares_keep_their_own_error(self, shares, message):
        with pytest.raises(WegValidationError, match=message) as caught:
            compute_wirtschaftsplan(_co_owned_plan([_whole_year(BERND, shares[0]), _whole_year(CARLA, shares[1])]))
        assert not isinstance(caught.value, weg_engine.WegShareSumError)

    def test_the_plan_reads_the_scales_and_the_tolerance_of_the_settlement(self, monkeypatch):
        owners = [_whole_year(BERND, "500"), _whole_year(CARLA, "500")]
        with pytest.raises(weg_engine.WegShareSumError):
            compute_wirtschaftsplan(_co_owned_plan(owners))
        monkeypatch.setattr(weg_engine, "SHARE_SUM_SCALES", (Decimal(1), Decimal(100), Decimal(1000)))
        assert _advances(compute_wirtschaftsplan(_co_owned_plan(owners)))[BERND][0] == Decimal("250.00")
        near = [_whole_year(BERND, "0.5"), _whole_year(CARLA, "0.50001")]
        with pytest.raises(weg_engine.WegShareSumError):
            compute_wirtschaftsplan(_co_owned_plan(near))
        monkeypatch.setattr(weg_engine, "SHARE_ROUNDING_NOISE", Decimal("0.00001"))
        assert BERND in _advances(compute_wirtschaftsplan(_co_owned_plan(near)))


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

    def test_reserve_notes_describe_the_ledger_without_stage_wording(self):
        lines = {line.line_type: line for line in build_vermoegensbericht(self._scenario()).lines}
        assert lines["RESERVE_OPENING"].note == "Anfangsbestand laut Rücklagenbuch"
        assert lines["RESERVE_WITHDRAWAL"].note == "Entnahmen laut Rücklagenbuch"

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
