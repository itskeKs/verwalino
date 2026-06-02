"""Golden tests for the M4 WEG engine -- hand-computed Soll results, exact cents.

m4-weg-abrechnung.md section 9.1. Each test asserts NOT only end totals but the
individual per-owner line amounts, key bases, subtotals, the Abrechnungsspitze
and the reserve development against the hand computation, so a compensating
double error cannot slip through (same discipline as the M3 Golden tests).

2025 is a non-leap year (365 days): Jan-Jun = 181 days, Jul-Dec = 184.

Pure-function tests -- no DB, no server.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.engine.canonical import canonical_sha256
from app.engine.snapshot import build_weg_snapshot_payload
from app.engine.types import (
    OwnerAdvanceInput,
    OwnerPeriodInput,
    PropertyInput,
    ReserveInput,
    UnitInput,
    WegSettlementInput,
    WegCostPositionInput,
)
from app.engine.weg import WegValidationError, compute_weg_jahresabrechnung

YEAR_START = date(2025, 1, 1)
YEAR_END = date(2025, 12, 31)


def _U(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def _owner_result(result, owner_id):
    return next(o for o in result.owners if o.owner_profile_id == owner_id)


def _cost_line(owner_result, category_code):
    return next(
        ln for ln in owner_result.cost_lines if ln.category_code == category_code
    )


# ===========================================================================
# Golden 1: trivial MEA distribution (m4 section 9.1 #1)
#   3 units MEA 500/300/200 (of 1000); Verwalterkosten 3000.00 NICHT_UMLAGEFAEHIG
#   MEA, full year, no change. Soll: 1500/900/600.
# ===========================================================================
class TestGolden1MeaTrivial:
    def _scenario(self):
        u1, u2, u3 = _U(11), _U(12), _U(13)
        o1, o2, o3 = _U(21), _U(22), _U(23)
        units = (
            UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("500")),
            UnitInput(id=u2, number="2", usage_type="WOHNUNG", mea=Decimal("300")),
            UnitInput(id=u3, number="3", usage_type="WOHNUNG", mea=Decimal("200")),
        )
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=o1, valid_from=date(2000, 1, 1), valid_to=None),
            OwnerPeriodInput(unit_id=u2, owner_profile_id=o2, valid_from=date(2000, 1, 1), valid_to=None),
            OwnerPeriodInput(unit_id=u3, owner_profile_id=o3, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50),
                category_id=_U(99),
                category_code="WEG_VERWALTERKOSTEN",
                label="Verwalterkosten",
                gross_amount=Decimal("3000.00"),
                service_period_from=YEAR_START,
                service_period_to=YEAR_END,
                owner_distribution_key="MEA",
                cost_group="NICHT_UMLAGEFAEHIG",
            ),
        )
        # No plan advances -> Spitze = owner_cost_total - 0.
        return WegSettlementInput(
            period_start=YEAR_START,
            period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG Haus", management_type="WEG"),
            units=units,
            owner_periods=owners,
            cost_positions=pos,
            plan_advances=(),
        )

    def test_mea_shares(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        o1 = _owner_result(r, _U(21))
        o2 = _owner_result(r, _U(22))
        o3 = _owner_result(r, _U(23))
        # 3000 * 500/1000 = 1500 ; * 300/1000 = 900 ; * 200/1000 = 600
        assert _cost_line(o1, "WEG_VERWALTERKOSTEN").amount == Decimal("1500.00")
        assert _cost_line(o2, "WEG_VERWALTERKOSTEN").amount == Decimal("900.00")
        assert _cost_line(o3, "WEG_VERWALTERKOSTEN").amount == Decimal("600.00")
        # Key base recorded (numerator/denominator).
        ln = _cost_line(o1, "WEG_VERWALTERKOSTEN")
        assert ln.distribution_base_share == Decimal("500")
        assert ln.distribution_base_total == Decimal("1000")
        assert ln.total_amount == Decimal("3000.00")
        # NICHT_UMLAGEFAEHIG subtotal carries it; umlagefaehig + reserve = 0.
        assert o1.subtotal_nicht_umlagefaehig == Decimal("1500.00")
        assert o1.subtotal_umlagefaehig == Decimal("0.00")
        assert o1.subtotal_reserve == Decimal("0.00")
        # OWNER_COST_TOTAL = umlagefaehig + nicht (no reserve).
        assert o1.owner_cost_total == Decimal("1500.00")

    def test_sum_equals_total(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        s = sum((o.owner_cost_total for o in r.owners), Decimal("0.00"))
        assert s == Decimal("3000.00")
        assert r.cost_total == Decimal("3000.00")


# ===========================================================================
# Golden 2: Abrechnungsspitze (m4 section 9.1 #2) -- THE core
#   2 units MEA 600/400; Ist gesamt 10.000,00 (MEA -> 6000/4000);
#   plan Soll-Vorschuesse 5500/4200.
#   Spitze A = 6000 - 5500 = +500.00 (Nachschuss); B = 4000 - 4200 = -200.00.
# ===========================================================================
class TestGolden2Abrechnungsspitze:
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
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50),
                category_id=_U(99),
                category_code="HEIZUNG",
                label="Bewirtschaftung",
                gross_amount=Decimal("10000.00"),
                service_period_from=YEAR_START,
                service_period_to=YEAR_END,
                owner_distribution_key="MEA",
                cost_group="UMLAGEFAEHIG",
            ),
        )
        plan = (
            OwnerAdvanceInput(owner_profile_id=oa, plan_advance_total=Decimal("5500.00")),
            OwnerAdvanceInput(owner_profile_id=ob, plan_advance_total=Decimal("4200.00")),
        )
        return WegSettlementInput(
            period_start=YEAR_START,
            period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG Haus", management_type="WEG"),
            units=units,
            owner_periods=owners,
            cost_positions=pos,
            plan_advances=plan,
        )

    def test_spitze_exact(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        oa = _owner_result(r, _U(21))
        ob = _owner_result(r, _U(22))
        assert oa.owner_cost_total == Decimal("6000.00")
        assert ob.owner_cost_total == Decimal("4000.00")
        assert oa.plan_advance == Decimal("5500.00")
        assert ob.plan_advance == Decimal("4200.00")
        # The beschlussrelevante Spitze = Ist - Soll (NOT Ist - gezahlt).
        assert oa.abrechnungsspitze == Decimal("500.00")  # Nachschuss
        assert ob.abrechnungsspitze == Decimal("-200.00")  # Guthaben

    def test_spitze_total_no_drift(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        # Σ Spitzen == Σ Ist - Σ Soll = 10000 - 9700 = 300.
        assert r.cost_total == Decimal("10000.00")
        assert r.plan_advances_total == Decimal("9700.00")
        assert r.abrechnungsspitze_total == Decimal("300.00")
        s = sum((o.abrechnungsspitze for o in r.owners), Decimal("0.00"))
        assert s == Decimal("300.00")

    def test_paid_advances_kept_separate_from_spitze(self):
        # A säumiger owner (paid less than Soll) must NOT change the Spitze.
        inp = self._scenario()
        inp = WegSettlementInput(
            period_start=inp.period_start,
            period_end=inp.period_end,
            property=inp.property,
            units=inp.units,
            owner_periods=inp.owner_periods,
            cost_positions=inp.cost_positions,
            plan_advances=inp.plan_advances,
            paid_advances=(
                OwnerAdvanceInput(owner_profile_id=_U(21), plan_advance_total=Decimal("0.00")),
                OwnerAdvanceInput(owner_profile_id=_U(22), plan_advance_total=Decimal("4200.00")),
            ),
        )
        r = compute_weg_jahresabrechnung(inp)
        oa = _owner_result(r, _U(21))
        # Spitze unchanged (Ist - Soll), paid only nachrichtlich.
        assert oa.abrechnungsspitze == Decimal("500.00")
        assert oa.paid_advances == Decimal("0.00")


# ===========================================================================
# Golden 3: Trennung umlagefaehig / nicht / Ruecklage (m4 section 9.1 #3)
#   1 unit (MEA 1000, sole owner) so the split is unambiguous.
#   Gartenpflege 1000 UMLAGEFAEHIG; Verwalter 500 NICHT_UMLAGEFAEHIG;
#   Erhaltungsruecklage-Zufuehrung 2000 reserve_relevant.
#   Soll: subtotal_uml=1000, subtotal_nicht=500, subtotal_reserve=2000,
#         OWNER_COST_TOTAL = 1500 (reserve NOT included).
# ===========================================================================
class TestGolden3ThreeSpheres:
    def _scenario(self):
        u1 = _U(11)
        o1 = _U(21)
        units = (UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("1000")),)
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=o1, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(91), category_code="GARTENPFLEGE",
                label="Gartenpflege", gross_amount=Decimal("1000.00"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="UMLAGEFAEHIG",
            ),
            WegCostPositionInput(
                cost_entry_id=_U(51), category_id=_U(92), category_code="WEG_VERWALTERKOSTEN",
                label="Verwalter", gross_amount=Decimal("500.00"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="NICHT_UMLAGEFAEHIG",
            ),
            WegCostPositionInput(
                cost_entry_id=_U(52), category_id=_U(93), category_code="ERHALTUNGSRUECKLAGE",
                label="Zufuehrung Ruecklage", gross_amount=Decimal("2000.00"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="NICHT_UMLAGEFAEHIG",
                reserve_relevant=True,
            ),
        )
        return WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, cost_positions=pos,
            plan_advances=(OwnerAdvanceInput(owner_profile_id=o1, plan_advance_total=Decimal("1400.00")),),
        )

    def test_three_subtotals(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        o1 = _owner_result(r, _U(21))
        assert o1.subtotal_umlagefaehig == Decimal("1000.00")
        assert o1.subtotal_nicht_umlagefaehig == Decimal("500.00")
        assert o1.subtotal_reserve == Decimal("2000.00")

    def test_reserve_not_in_owner_cost_total_or_spitze(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        o1 = _owner_result(r, _U(21))
        # OWNER_COST_TOTAL = 1000 + 500 = 1500 (reserve 2000 NOT counted).
        assert o1.owner_cost_total == Decimal("1500.00")
        # Spitze = 1500 - 1400 = 100 (reserve must not push this to 2100).
        assert o1.abrechnungsspitze == Decimal("100.00")
        # The reserve contribution line is RESERVE_CONTRIBUTION, not COST.
        res_line = _cost_line(o1, "ERHALTUNGSRUECKLAGE")
        assert res_line.line_type == "RESERVE_CONTRIBUTION"

    def test_cost_total_excludes_reserve(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        assert r.cost_total == Decimal("1500.00")
        assert r.reserve_contributions_total == Decimal("2000.00")


# ===========================================================================
# Golden 4: Reserve development (m4 section 9.1 #4)
#   opening 20000, contribution 6000 (a reserve cost position), withdrawal 8000
#   -> closing 18000. Invariant + escalation on inconsistency (the engine
#   DERIVES closing, so an inconsistent closing cannot be supplied; we instead
#   verify the development math and that a wrong opening/withdrawal is reflected).
# ===========================================================================
class TestGolden4ReserveDevelopment:
    def _scenario(self, opening, withdrawal, contribution):
        u1 = _U(11)
        o1 = _U(21)
        units = (UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("1000")),)
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=o1, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(52), category_id=_U(93), category_code="ERHALTUNGSRUECKLAGE",
                label="Zufuehrung", gross_amount=contribution,
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="NICHT_UMLAGEFAEHIG",
                reserve_relevant=True,
            ),
        )
        return WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, cost_positions=pos, plan_advances=(),
            reserve=ReserveInput(opening_balance=opening, withdrawals_total=withdrawal),
        )

    def test_development(self):
        r = compute_weg_jahresabrechnung(
            self._scenario(Decimal("20000.00"), Decimal("8000.00"), Decimal("6000.00"))
        )
        dev = r.reserve
        assert dev.opening_balance == Decimal("20000.00")
        assert dev.contributions_total == Decimal("6000.00")
        assert dev.withdrawals_total == Decimal("8000.00")
        # closing = 20000 + 6000 - 8000 = 18000.
        assert dev.closing_balance == Decimal("18000.00")

    def test_invariant_holds_for_random_like_values(self):
        r = compute_weg_jahresabrechnung(
            self._scenario(Decimal("1234.56"), Decimal("789.10"), Decimal("321.00"))
        )
        dev = r.reserve
        assert dev.closing_balance == (
            dev.opening_balance + dev.contributions_total - dev.withdrawals_total
        )


# ===========================================================================
# Golden 5: Eigentuemerwechsel unterjaehrig tagegenau (m4 section 9.1 #5)
#   Unit verkauft 30.06.->01.07. (2025); 1 MEA cost position full year.
#   Verkaeufer 181/365, Kaeufer 184/365 of the unit's share; Σ = unit share.
# ===========================================================================
class TestGolden5OwnerChangeDayExact:
    def _scenario(self):
        # Two units MEA 500/500 of 1000. A deliberately "krummer" cost so the
        # unit-1 share is NOT a clean multiple of 365 -> the day-exact split
        # genuinely exercises Largest-Remainder at the owner change.
        # Cost 1000.00 -> each unit 500.00. Unit 1 changes owner mid-year.
        u1, u2 = _U(11), _U(12)
        seller, buyer, o2 = _U(21), _U(22), _U(23)
        units = (
            UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("500")),
            UnitInput(id=u2, number="2", usage_type="WOHNUNG", mea=Decimal("500")),
        )
        owners = (
            # Seller owns unit 1 until 30.06 (inclusive, engine tag-inclusive).
            OwnerPeriodInput(unit_id=u1, owner_profile_id=seller, valid_from=date(2000, 1, 1), valid_to=date(2025, 6, 30)),
            # Buyer owns unit 1 from 01.07 onward.
            OwnerPeriodInput(unit_id=u1, owner_profile_id=buyer, valid_from=date(2025, 7, 1), valid_to=None),
            OwnerPeriodInput(unit_id=u2, owner_profile_id=o2, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(99), category_code="GRUNDSTEUER",
                label="Grundsteuer", gross_amount=Decimal("1000.00"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="UMLAGEFAEHIG",
            ),
        )
        return WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, cost_positions=pos, plan_advances=(),
        )

    def test_seller_buyer_day_exact(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        seller = _owner_result(r, _U(21))
        buyer = _owner_result(r, _U(22))
        o2 = _owner_result(r, _U(23))
        # Unit 1 share = 1000 * 500/1000 = 500.00.
        # Seller 500 * 181/365 = 247.945205... ; Buyer 500 * 184/365 = 252.0547...
        # The single Largest-Remainder pass runs over ALL cells (seller, buyer,
        # o2) so Σ = 1000.00 exactly. Cell exact shares:
        #   seller = 1000 * (0.5 * 181/365) = 247.945205...
        #   buyer  = 1000 * (0.5 * 184/365) = 252.054794...
        #   o2     = 1000 * 0.5             = 500.000000
        # floors: 247.94 / 252.05 / 500.00 = 999.99 ; 1 cent remnant ->
        # largest fractional remnant is buyer (.054... > seller .945? no:
        # frac(seller)=.945205, frac(buyer)=.054794, frac(o2)=.0) -> seller wins.
        # seller 247.95 ; buyer 252.05 ; o2 500.00 ; Σ = 1000.00.
        assert seller.owner_cost_total == Decimal("247.95")
        assert buyer.owner_cost_total == Decimal("252.05")
        assert o2.owner_cost_total == Decimal("500.00")
        # No phantom day: seller + buyer == unit 1 share (500.00).
        assert seller.owner_cost_total + buyer.owner_cost_total == Decimal("500.00")

    def test_property_total_consistent(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        s = sum((o.owner_cost_total for o in r.owners), Decimal("0.00"))
        assert s == Decimal("1000.00")


# ===========================================================================
# Golden 6: Miteigentum / Bruchteilseigentum (m4 section 9.1 #6)
#   Unit with two co-owners mea_share 0.6 / 0.4, MEA cost position.
#   Unit share split exactly 0.6/0.4, Largest-Remainder summengenau.
# ===========================================================================
class TestGolden6CoOwnership:
    def _scenario(self):
        # One unit MEA 1000 (sole in WEG) cost 1000.00 -> co-owners 600/400.
        u1 = _U(11)
        co_a, co_b = _U(21), _U(22)
        units = (UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("1000")),)
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=co_a, valid_from=date(2000, 1, 1), valid_to=None, mea_share=Decimal("0.6")),
            OwnerPeriodInput(unit_id=u1, owner_profile_id=co_b, valid_from=date(2000, 1, 1), valid_to=None, mea_share=Decimal("0.4")),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(99), category_code="GRUNDSTEUER",
                label="Grundsteuer", gross_amount=Decimal("1000.00"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="UMLAGEFAEHIG",
            ),
        )
        return WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, cost_positions=pos, plan_advances=(),
        )

    def test_co_owner_split(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        a = _owner_result(r, _U(21))
        b = _owner_result(r, _U(22))
        # 1000 * 0.6 = 600.00 ; * 0.4 = 400.00.
        assert a.owner_cost_total == Decimal("600.00")
        assert b.owner_cost_total == Decimal("400.00")
        assert a.owner_cost_total + b.owner_cost_total == Decimal("1000.00")

    def test_co_owner_split_summengenau_odd(self):
        # An odd amount that does not divide cleanly: 100.01 * 0.6 = 60.006 -> 60.01
        # 100.01 * 0.4 = 40.004 -> 40.00 ; Σ = 100.01 (Largest-Remainder).
        inp = self._scenario()
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(99), category_code="GRUNDSTEUER",
                label="Grundsteuer", gross_amount=Decimal("100.01"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="UMLAGEFAEHIG",
            ),
        )
        inp = WegSettlementInput(
            period_start=inp.period_start, period_end=inp.period_end,
            property=inp.property, units=inp.units, owner_periods=inp.owner_periods,
            cost_positions=pos, plan_advances=(),
        )
        r = compute_weg_jahresabrechnung(inp)
        a = _owner_result(r, _U(21))
        b = _owner_result(r, _U(22))
        assert a.owner_cost_total + b.owner_cost_total == Decimal("100.01")
        # 60.006 has the larger fractional remnant (.006 vs .004) -> gets the cent.
        assert a.owner_cost_total == Decimal("60.01")
        assert b.owner_cost_total == Decimal("40.00")


# ===========================================================================
# Golden 9 (engine part): snapshot/hash determinism (m4 section 9.1 #9)
#   Scenario 2 finalized twice (identical inputs) -> identical sha256.
# ===========================================================================
class TestGolden9SnapshotDeterminism:
    def test_hash_stable(self):
        s2 = TestGolden2Abrechnungsspitze()
        inp = s2._scenario()
        r1 = compute_weg_jahresabrechnung(inp)
        r2 = compute_weg_jahresabrechnung(inp)
        p1 = build_weg_snapshot_payload(inp, r1)
        p2 = build_weg_snapshot_payload(inp, r2)
        assert canonical_sha256(p1) == canonical_sha256(p2)


# ===========================================================================
# Fail-loud validation (m4 section 10.1)
# ===========================================================================
class TestWegFailLoud:
    def test_ownership_gap_escalates(self):
        u1 = _U(11)
        o1 = _U(21)
        units = (UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("1000")),)
        # Owner only covers half the year -> a gap in [01.07..31.12].
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=o1, valid_from=YEAR_START, valid_to=date(2025, 6, 30)),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(99), category_code="GRUNDSTEUER",
                label="x", gross_amount=Decimal("100.00"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="UMLAGEFAEHIG",
            ),
        )
        inp = WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, cost_positions=pos, plan_advances=(),
        )
        with pytest.raises(WegValidationError, match="gap|owner"):
            compute_weg_jahresabrechnung(inp)

    def test_missing_mea_escalates(self):
        from app.engine.rounding import DistributionError

        u1 = _U(11)
        o1 = _U(21)
        # Unit has no MEA -> no participating unit for the MEA key.
        units = (UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=None),)
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=o1, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(99), category_code="GRUNDSTEUER",
                label="x", gross_amount=Decimal("100.00"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="UMLAGEFAEHIG",
            ),
        )
        inp = WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, cost_positions=pos, plan_advances=(),
        )
        with pytest.raises(DistributionError):
            compute_weg_jahresabrechnung(inp)

    def test_invalid_owner_key_escalates(self):
        u1 = _U(11)
        o1 = _U(21)
        units = (UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("1000")),)
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=o1, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(99), category_code="X",
                label="x", gross_amount=Decimal("100.00"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="FANTASIE", cost_group="UMLAGEFAEHIG",
            ),
        )
        inp = WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, cost_positions=pos, plan_advances=(),
        )
        with pytest.raises(WegValidationError, match="owner_distribution_key"):
            compute_weg_jahresabrechnung(inp)
