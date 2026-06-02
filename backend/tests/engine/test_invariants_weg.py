"""Property-based invariants for the M4 WEG engine (m4 section 9.2, I4-A..I4-H).

The objective, model-independent measuring stick: these hold for ANY valid
input, not just the hand-picked Golden scenarios. Hypothesis generates
adversarial inputs.

Pure-function tests -- no DB, no server.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings, strategies as st

from app.engine.rounding import CENT
from app.engine.periods import build_owner_cover, time_factor
from app.engine.types import (
    OwnerAdvanceInput,
    OwnerPeriodInput,
    PlannedCostInput,
    PropertyInput,
    ReserveInput,
    UnitInput,
    WegSettlementInput,
    WegCostPositionInput,
    WirtschaftsplanInput,
)
from app.engine.weg import compute_weg_jahresabrechnung
from app.engine.wirtschaftsplan import compute_wirtschaftsplan

YEAR_START = date(2025, 1, 1)
YEAR_END = date(2025, 12, 31)


def _U(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def money_amounts(min_eur=0, max_eur=100000):
    return st.integers(min_value=min_eur * 100, max_value=max_eur * 100).map(
        lambda c: (Decimal(c) / 100).quantize(CENT)
    )


def mea_values():
    return st.integers(min_value=1, max_value=10000).map(lambda x: Decimal(x))


# ===========================================================================
# I4-A: Σ Eigentuemeranteile == Gesamtkosten (the most fundamental, ADR-0005).
# ===========================================================================
class TestI4A_SumEqualsTotal:
    @settings(max_examples=200)
    @given(
        meas=st.lists(mea_values(), min_size=1, max_size=8),
        cost=money_amounts(min_eur=1),
    )
    def test_owner_shares_sum_to_cost(self, meas, cost):
        n = len(meas)
        units = tuple(
            UnitInput(id=_U(10 + i), number=str(i), usage_type="WOHNUNG", mea=m)
            for i, m in enumerate(meas)
        )
        owners = tuple(
            OwnerPeriodInput(
                unit_id=_U(10 + i),
                owner_profile_id=_U(100 + i),
                valid_from=date(2000, 1, 1),
                valid_to=None,
            )
            for i in range(n)
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50),
                category_id=_U(99),
                category_code="GRUNDSTEUER",
                label="x",
                gross_amount=cost,
                service_period_from=YEAR_START,
                service_period_to=YEAR_END,
                owner_distribution_key="MEA",
                cost_group="UMLAGEFAEHIG",
            ),
        )
        inp = WegSettlementInput(
            period_start=YEAR_START,
            period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units,
            owner_periods=owners,
            cost_positions=pos,
            plan_advances=(),
        )
        r = compute_weg_jahresabrechnung(inp)
        s = sum((o.owner_cost_total for o in r.owners), Decimal("0.00"))
        assert s == cost
        assert r.cost_total == cost


# ===========================================================================
# I4-C: Abrechnungsspitze == Ist - Soll (per owner AND Σ, no drift).
# ===========================================================================
class TestI4C_SpitzeIstMinusSoll:
    @settings(max_examples=200)
    @given(
        meas=st.lists(mea_values(), min_size=1, max_size=6),
        cost=money_amounts(min_eur=1),
        soll_seed=st.lists(money_amounts(min_eur=0, max_eur=50000), min_size=1, max_size=6),
    )
    def test_spitze_equals_ist_minus_soll(self, meas, cost, soll_seed):
        n = len(meas)
        units = tuple(
            UnitInput(id=_U(10 + i), number=str(i), usage_type="WOHNUNG", mea=m)
            for i, m in enumerate(meas)
        )
        owners = tuple(
            OwnerPeriodInput(unit_id=_U(10 + i), owner_profile_id=_U(100 + i), valid_from=date(2000, 1, 1), valid_to=None)
            for i in range(n)
        )
        plan = tuple(
            OwnerAdvanceInput(owner_profile_id=_U(100 + i), plan_advance_total=soll_seed[i % len(soll_seed)])
            for i in range(n)
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(99), category_code="HEIZUNG", label="x",
                gross_amount=cost, service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="UMLAGEFAEHIG",
            ),
        )
        inp = WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, cost_positions=pos, plan_advances=plan,
        )
        r = compute_weg_jahresabrechnung(inp)
        # Per owner: Spitze == owner_cost_total - plan_advance, exactly.
        for o in r.owners:
            assert o.abrechnungsspitze == o.owner_cost_total - o.plan_advance
        # Σ Spitzen == Σ Ist - Σ Soll, no drift over owners.
        sum_spitze = sum((o.abrechnungsspitze for o in r.owners), Decimal("0.00"))
        assert sum_spitze == r.cost_total - r.plan_advances_total
        assert r.abrechnungsspitze_total == sum_spitze


# ===========================================================================
# I4-D: Reserve development consistent; reserve never in OWNER_COST_TOTAL.
# ===========================================================================
class TestI4D_ReserveConsistent:
    @settings(max_examples=150)
    @given(
        opening=money_amounts(min_eur=0, max_eur=100000),
        withdrawal=money_amounts(min_eur=0, max_eur=50000),
        contribution=money_amounts(min_eur=0, max_eur=50000),
        cost=money_amounts(min_eur=1, max_eur=50000),
    )
    def test_reserve_development_and_isolation(self, opening, withdrawal, contribution, cost):
        u1 = _U(11)
        o1 = _U(21)
        units = (UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("1000")),)
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=o1, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(91), category_code="GARTENPFLEGE", label="x",
                gross_amount=cost, service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="UMLAGEFAEHIG",
            ),
            WegCostPositionInput(
                cost_entry_id=_U(51), category_id=_U(93), category_code="ERHALTUNGSRUECKLAGE", label="r",
                gross_amount=contribution, service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="NICHT_UMLAGEFAEHIG", reserve_relevant=True,
            ),
        )
        inp = WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, cost_positions=pos, plan_advances=(),
            reserve=ReserveInput(opening_balance=opening, withdrawals_total=withdrawal),
        )
        r = compute_weg_jahresabrechnung(inp)
        dev = r.reserve
        # Development invariant.
        assert dev.closing_balance == dev.opening_balance + dev.contributions_total - dev.withdrawals_total
        assert dev.contributions_total == contribution
        # The reserve contribution is NEVER in owner_cost_total (only the cost).
        o1r = r.owners[0]
        assert o1r.owner_cost_total == cost
        assert o1r.subtotal_reserve == contribution
        # cost_total excludes the reserve.
        assert r.cost_total == cost


# ===========================================================================
# I4-E: owner time factors over a unit decomposition sum to exactly 1.
# ===========================================================================
class TestI4E_OwnerTimeFactorsSumToOne:
    @settings(max_examples=200)
    @given(split_day=st.integers(min_value=1, max_value=363))
    def test_owner_change_factors_sum_to_one(self, split_day):
        u1 = _U(11)
        seller, buyer = _U(21), _U(22)
        change = YEAR_START + timedelta(days=split_day)
        last_seller_day = change - timedelta(days=1)
        owners = [
            OwnerPeriodInput(unit_id=u1, owner_profile_id=seller, valid_from=YEAR_START, valid_to=last_seller_day),
            OwnerPeriodInput(unit_id=u1, owner_profile_id=buyer, valid_from=change, valid_to=None),
        ]
        cover = build_owner_cover(u1, YEAR_START, YEAR_END, owners)
        total = sum(
            (time_factor(YEAR_START, YEAR_END, s.start, s.end) for s in cover),
            Decimal(0),
        )
        assert total == Decimal(1)


# ===========================================================================
# I4-G: plan monthly rates sum exactly to the annual advance per owner.
# ===========================================================================
class TestI4G_PlanMonthlyRatesSumExact:
    @settings(max_examples=200)
    @given(
        meas=st.lists(mea_values(), min_size=1, max_size=6),
        cost=money_amounts(min_eur=1, max_eur=200000),
        reserve=money_amounts(min_eur=0, max_eur=50000),
    )
    def test_monthly_rates_sum_to_annual(self, meas, cost, reserve):
        n = len(meas)
        units = tuple(
            UnitInput(id=_U(10 + i), number=str(i), usage_type="WOHNUNG", mea=m)
            for i, m in enumerate(meas)
        )
        owners = tuple(
            OwnerPeriodInput(unit_id=_U(10 + i), owner_profile_id=_U(100 + i), valid_from=date(2000, 1, 1), valid_to=None)
            for i in range(n)
        )
        planned = (
            PlannedCostInput(
                category_id=_U(91), category_code="HEIZUNG", label="x",
                planned_amount=cost, distribution_key="MEA", cost_group="UMLAGEFAEHIG",
            ),
        )
        inp = WirtschaftsplanInput(
            plan_year=2025, period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, planned_costs=planned,
            reserve_contribution_total=reserve,
        )
        r = compute_wirtschaftsplan(inp)
        for o in r.owners:
            assert len(o.monthly_rates) == 12
            assert sum(o.monthly_rates, Decimal("0.00")) == o.annual_advance
        # Σ annual advances == total volume (no cent drift over owners).
        s = sum((o.annual_advance for o in r.owners), Decimal("0.00"))
        assert s == r.total_advance_volume
        assert r.total_advance_volume == cost + reserve


# ===========================================================================
# I4-H: determinism (same input -> identical canonical payload + hash).
# ===========================================================================
class TestI4H_Determinism:
    @settings(max_examples=80)
    @given(
        meas=st.lists(mea_values(), min_size=1, max_size=5),
        cost=money_amounts(min_eur=1),
    )
    def test_payload_hash_stable(self, meas, cost):
        from app.engine.canonical import canonical_sha256
        from app.engine.snapshot import build_weg_snapshot_payload

        n = len(meas)
        units = tuple(
            UnitInput(id=_U(10 + i), number=str(i), usage_type="WOHNUNG", mea=m)
            for i, m in enumerate(meas)
        )
        owners = tuple(
            OwnerPeriodInput(unit_id=_U(10 + i), owner_profile_id=_U(100 + i), valid_from=date(2000, 1, 1), valid_to=None)
            for i in range(n)
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(99), category_code="GRUNDSTEUER", label="x",
                gross_amount=cost, service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="UMLAGEFAEHIG",
            ),
        )
        inp = WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, cost_positions=pos, plan_advances=(),
        )
        r1 = compute_weg_jahresabrechnung(inp)
        r2 = compute_weg_jahresabrechnung(inp)
        h1 = canonical_sha256(build_weg_snapshot_payload(inp, r1))
        h2 = canonical_sha256(build_weg_snapshot_payload(inp, r2))
        assert h1 == h2
