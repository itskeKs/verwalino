"""Golden tests -- hand-computed Soll results, exact cents (section 9.1).

Each test is a fully hand-checked scenario; it asserts NOT only the end total
but the individual settlement-line amounts, key bases and time shares against
the hand computation, so a compensating double error cannot slip through.

The arithmetic for every value is shown inline in comments so a reviewer (or
auditor) can re-derive each cent by hand.  2025 is a non-leap year (365 days):
Jan-Jun = 181 days, Jul-Dec = 184; Jan-Mar = 90, Apr-Jul = 122, Aug-Dec = 153.

Pure-function tests -- no DB, no server.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.engine.canonical import canonical_sha256
from app.engine.settlement import compute_bk_settlement
from app.engine.snapshot import build_snapshot_payload
from app.engine.types import (
    CostPositionInput,
    ExternalAmountInput,
    LeasePeriodInput,
    OwnerPeriodInput,
    PropertyInput,
    SettlementInput,
    UnitInput,
)

YEAR_START = date(2025, 1, 1)
YEAR_END = date(2025, 12, 31)


def _U(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def _cost_lines(result):
    return {ln.category_code: ln for ln in result.lines if ln.line_type == "COST"}


def _line(result, line_type):
    return next(ln for ln in result.lines if ln.line_type == line_type)


# ===========================================================================
# Golden 1: trivial Flaeche -- 1200 over 75/50/25 -> 600/400/200
# ===========================================================================
class TestGolden1FlaecheTrivial:
    def _scenario(self, target):
        prop = _U(1)
        u1, u2, u3 = _U(11), _U(12), _U(13)
        l1, l2, l3 = _U(21), _U(22), _U(23)
        own = _U(31)
        units = tuple(
            UnitInput(id=u, number=str(i + 1), usage_type="WOHNUNG", area=a, mea=a)
            for i, (u, a) in enumerate(
                [(u1, Decimal("75")), (u2, Decimal("50")), (u3, Decimal("25"))]
            )
        )
        leases = tuple(
            LeasePeriodInput(
                lease_agreement_id=l,
                unit_id=u,
                tenant_profile_id=_U(40 + i),
                valid_from=YEAR_START,
                valid_to=YEAR_END,
            )
            for i, (l, u) in enumerate([(l1, u1), (l2, u2), (l3, u3)])
        )
        owners = tuple(
            OwnerPeriodInput(unit_id=u, owner_profile_id=own, valid_from=date(2000, 1, 1), valid_to=None)
            for u in (u1, u2, u3)
        )
        pos = (
            CostPositionInput(
                cost_entry_id=_U(50),
                category_id=_U(99),
                category_code="GRUNDSTEUER",
                label="Grundsteuer",
                gross_amount=Decimal("1200.00"),
                service_period_from=YEAR_START,
                service_period_to=YEAR_END,
                tenant_distribution_key="WOHNFLAECHE",
                tenant_apportionable="UMLAGEFAEHIG",
            ),
        )
        return SettlementInput(
            period_start=YEAR_START,
            period_end=YEAR_END,
            property=PropertyInput(id=prop, name="Haus", management_type="MIET"),
            target_lease_agreement_id=target,
            units=units,
            lease_periods=leases,
            owner_periods=owners,
            cost_positions=pos,
            advance_payments_total=Decimal("0.00"),
        )

    def test_three_units(self):
        # 1200 * 75/150 = 600 ; * 50/150 = 400 ; * 25/150 = 200.
        r1 = compute_bk_settlement(self._scenario(_U(21)))
        r2 = compute_bk_settlement(self._scenario(_U(22)))
        r3 = compute_bk_settlement(self._scenario(_U(23)))
        assert _cost_lines(r1)["GRUNDSTEUER"].amount == Decimal("600.00")
        assert _cost_lines(r2)["GRUNDSTEUER"].amount == Decimal("400.00")
        assert _cost_lines(r3)["GRUNDSTEUER"].amount == Decimal("200.00")
        # Key base recorded for the rechenweg.
        ln = _cost_lines(r1)["GRUNDSTEUER"]
        assert ln.distribution_base_total == Decimal("150.000000")
        assert ln.distribution_base_share == Decimal("75.000000")
        assert ln.total_amount == Decimal("1200.00")
        # sum of the three tenant shares == G
        assert (
            _cost_lines(r1)["GRUNDSTEUER"].amount
            + _cost_lines(r2)["GRUNDSTEUER"].amount
            + _cost_lines(r3)["GRUNDSTEUER"].amount
        ) == Decimal("1200.00")


# ===========================================================================
# Golden 3: unterjaehriger Mieterwechsel (the core ADR-0004 case)
# ===========================================================================
class TestGolden3TenantChange:
    def _scenario(self, target, advance):
        prop = _U(1)
        unit = _U(11)
        la, lb = _U(21), _U(22)
        own = _U(31)
        units = (
            UnitInput(id=unit, number="1", usage_type="WOHNUNG", area=Decimal("100"), mea=Decimal("100")),
        )
        leases = (
            LeasePeriodInput(
                lease_agreement_id=la,
                unit_id=unit,
                tenant_profile_id=_U(41),
                valid_from=date(2025, 1, 1),
                valid_to=date(2025, 6, 30),
                tenant_name="Mieter A",
            ),
            LeasePeriodInput(
                lease_agreement_id=lb,
                unit_id=unit,
                tenant_profile_id=_U(42),
                valid_from=date(2025, 7, 1),
                valid_to=date(2025, 12, 31),
                tenant_name="Mieter B",
            ),
        )
        owners = (
            OwnerPeriodInput(unit_id=unit, owner_profile_id=own, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            CostPositionInput(
                cost_entry_id=_U(50),
                category_id=_U(99),
                category_code="GRUNDSTEUER",
                label="Grundsteuer",
                gross_amount=Decimal("1200.00"),
                service_period_from=YEAR_START,
                service_period_to=YEAR_END,
                tenant_distribution_key="WOHNFLAECHE",
                tenant_apportionable="UMLAGEFAEHIG",
            ),
        )
        return SettlementInput(
            period_start=YEAR_START,
            period_end=YEAR_END,
            property=PropertyInput(id=prop, name="Haus", management_type="MIET"),
            target_lease_agreement_id=target,
            units=units,
            lease_periods=leases,
            owner_periods=owners,
            cost_positions=pos,
            advance_payments_total=advance,
        )

    def test_tenant_a_and_b_day_exact(self):
        # A: 1200 * 181/365 = 595.0684... -> 595.07
        # B: 1200 * 184/365 = 604.9315... -> 604.93 ; A + B == 1200.00 exactly.
        rA = compute_bk_settlement(self._scenario(_U(21), Decimal("600.00")))
        rB = compute_bk_settlement(self._scenario(_U(22), Decimal("600.00")))
        a = _cost_lines(rA)["GRUNDSTEUER"]
        b = _cost_lines(rB)["GRUNDSTEUER"]
        assert a.amount == Decimal("595.07")
        assert a.time_share_days == 181
        assert a.time_share_total_days == 365
        assert b.amount == Decimal("604.93")
        assert b.time_share_days == 184
        assert a.amount + b.amount == Decimal("1200.00")
        # Soll - Vorauszahlung: A 595.07 - 600.00 = -4.93 (Guthaben)
        assert rA.balance == Decimal("-4.93")
        assert _line(rA, "BALANCE").label == "Guthaben"
        # B 604.93 - 600.00 = 4.93 (Nachzahlung)
        assert rB.balance == Decimal("4.93")
        assert _line(rB, "BALANCE").label == "Nachzahlung"

    def test_no_vacancy_between_back_to_back_leases(self):
        rA = compute_bk_settlement(self._scenario(_U(21), Decimal("0.00")))
        # Auszug 30.06 / Einzug 01.07 -> no vacancy at all (section 3.4).
        assert rA.vacancy_attribution == ()


# ===========================================================================
# Golden 4: Leerstand -> Eigentuemer
# ===========================================================================
class TestGolden4Vacancy:
    def _scenario(self, target):
        prop = _U(1)
        unit = _U(11)
        la, lb = _U(21), _U(22)
        own = _U(31)
        units = (
            UnitInput(id=unit, number="1", usage_type="WOHNUNG", area=Decimal("100"), mea=Decimal("100")),
        )
        # A: 01.01-31.03 (90), Vacancy 01.04-31.05 (61), B: 01.06-31.12 (214).
        leases = (
            LeasePeriodInput(
                lease_agreement_id=la,
                unit_id=unit,
                tenant_profile_id=_U(41),
                valid_from=date(2025, 1, 1),
                valid_to=date(2025, 3, 31),
            ),
            LeasePeriodInput(
                lease_agreement_id=lb,
                unit_id=unit,
                tenant_profile_id=_U(42),
                valid_from=date(2025, 6, 1),
                valid_to=date(2025, 12, 31),
            ),
        )
        owners = (
            OwnerPeriodInput(unit_id=unit, owner_profile_id=own, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            CostPositionInput(
                cost_entry_id=_U(50),
                category_id=_U(99),
                category_code="GRUNDSTEUER",
                label="Grundsteuer",
                gross_amount=Decimal("1200.00"),
                service_period_from=YEAR_START,
                service_period_to=YEAR_END,
                tenant_distribution_key="WOHNFLAECHE",
                tenant_apportionable="UMLAGEFAEHIG",
            ),
        )
        return SettlementInput(
            period_start=YEAR_START,
            period_end=YEAR_END,
            property=PropertyInput(id=prop, name="Haus", management_type="MIET"),
            target_lease_agreement_id=target,
            units=units,
            lease_periods=leases,
            owner_periods=owners,
            cost_positions=pos,
            advance_payments_total=Decimal("0.00"),
        )

    def test_vacancy_goes_to_owner_not_tenant(self):
        # A: 1200 * 90/365 = 295.89 ; B: 1200 * 214/365 = 703.56 ;
        # vacancy: 1200 * 61/365 = 200.55 -> owner. Sum = 1200.00.
        rA = compute_bk_settlement(self._scenario(_U(21)))
        rB = compute_bk_settlement(self._scenario(_U(22)))
        assert _cost_lines(rA)["GRUNDSTEUER"].amount == Decimal("295.89")
        assert _cost_lines(rA)["GRUNDSTEUER"].time_share_days == 90
        assert _cost_lines(rB)["GRUNDSTEUER"].amount == Decimal("703.56")
        assert _cost_lines(rB)["GRUNDSTEUER"].time_share_days == 214
        # Vacancy attributed to the owner, 61 days, not in either tenant bill.
        own = _U(31)
        vac = [v for v in rA.vacancy_attribution if v.cost_entry_id == _U(50)]
        assert len(vac) == 1
        assert vac[0].days == 61
        assert vac[0].owner_profile_id == own
        assert vac[0].amount == Decimal("200.55")
        # Completeness: tenant A + tenant B + vacancy == G.
        assert (
            _cost_lines(rA)["GRUNDSTEUER"].amount
            + _cost_lines(rB)["GRUNDSTEUER"].amount
            + vac[0].amount
        ) == Decimal("1200.00")


# ===========================================================================
# Golden 6: Leistungszeitraum ueber Jahresgrenze (belongs by service_period)
# ===========================================================================
class TestGolden6CrossYear:
    def test_december_2025_service_in_2025_settlement(self):
        # An invoice dated Jan 2026 but with service period Dec 2025 belongs in
        # the 2025 settlement (via service_period, not invoice_date; SDD 14).
        # Here the position's service period is fully Dec 2025 -> fully inside
        # the 2025 settlement; the single tenant bears 100%.
        prop = _U(1)
        unit = _U(11)
        la = _U(21)
        own = _U(31)
        units = (
            UnitInput(id=unit, number="1", usage_type="WOHNUNG", area=Decimal("100"), mea=Decimal("100")),
        )
        leases = (
            LeasePeriodInput(
                lease_agreement_id=la,
                unit_id=unit,
                tenant_profile_id=_U(41),
                valid_from=date(2024, 1, 1),
                valid_to=None,
            ),
        )
        owners = (
            OwnerPeriodInput(unit_id=unit, owner_profile_id=own, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            CostPositionInput(
                cost_entry_id=_U(50),
                category_id=_U(99),
                category_code="HEIZUNG_NACH",
                label="Heizung Nachberechnung Dez 2025",
                gross_amount=Decimal("300.00"),
                service_period_from=date(2025, 12, 1),
                service_period_to=date(2025, 12, 31),
                tenant_distribution_key="WOHNFLAECHE",
                tenant_apportionable="UMLAGEFAEHIG",
            ),
        )
        inp = SettlementInput(
            period_start=YEAR_START,
            period_end=YEAR_END,
            property=PropertyInput(id=prop, name="Haus", management_type="MIET"),
            target_lease_agreement_id=la,
            units=units,
            lease_periods=leases,
            owner_periods=owners,
            cost_positions=pos,
            advance_payments_total=Decimal("0.00"),
        )
        r = compute_bk_settlement(inp)
        ln = _cost_lines(r)["HEIZUNG_NACH"]
        # service period 31 days, tenant occupies all of it -> full 300.00.
        assert ln.amount == Decimal("300.00")
        assert ln.time_share_days == 31
        assert ln.time_share_total_days == 31


# ===========================================================================
# Golden 7: externe Abrechnung mit Zwischenablesung bei Mieterwechsel
# ===========================================================================
class TestGolden7ExternalWithMidYearReading:
    def test_external_amounts_assigned_per_occupancy_not_time_split(self):
        # Heating external: A's slice = 240.00, B's slice = 360.00 (Zwischen-
        # ablesung at the change). These are assigned DIRECTLY -- NOT re-split
        # by days (that is the point of EXTERNE_ABRECHNUNG, section 5.2).
        prop = _U(1)
        unit = _U(11)
        la, lb = _U(21), _U(22)
        own = _U(31)
        units = (
            UnitInput(id=unit, number="1", usage_type="WOHNUNG", area=Decimal("100"), mea=Decimal("100")),
        )
        leases = (
            LeasePeriodInput(
                lease_agreement_id=la,
                unit_id=unit,
                tenant_profile_id=_U(41),
                valid_from=date(2025, 1, 1),
                valid_to=date(2025, 6, 30),
            ),
            LeasePeriodInput(
                lease_agreement_id=lb,
                unit_id=unit,
                tenant_profile_id=_U(42),
                valid_from=date(2025, 7, 1),
                valid_to=date(2025, 12, 31),
            ),
        )
        owners = (
            OwnerPeriodInput(unit_id=unit, owner_profile_id=own, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            CostPositionInput(
                cost_entry_id=_U(50),
                category_id=_U(99),
                category_code="HEIZUNG",
                label="Heizung/Warmwasser",
                gross_amount=Decimal("600.00"),
                service_period_from=YEAR_START,
                service_period_to=YEAR_END,
                tenant_distribution_key="EXTERNE_ABRECHNUNG",
                tenant_apportionable="UMLAGEFAEHIG_EXTERN_PRUEFEN",
                external_amounts=(
                    ExternalAmountInput(
                        unit_id=unit,
                        tenant_amount=Decimal("240.00"),
                        period_from=date(2025, 1, 1),
                        period_to=date(2025, 6, 30),
                    ),
                    ExternalAmountInput(
                        unit_id=unit,
                        tenant_amount=Decimal("360.00"),
                        period_from=date(2025, 7, 1),
                        period_to=date(2025, 12, 31),
                    ),
                ),
            ),
        )

        def run(t):
            return compute_bk_settlement(
                SettlementInput(
                    period_start=YEAR_START,
                    period_end=YEAR_END,
                    property=PropertyInput(id=prop, name="Haus", management_type="MIET"),
                    target_lease_agreement_id=t,
                    units=units,
                    lease_periods=leases,
                    owner_periods=owners,
                    cost_positions=pos,
                    advance_payments_total=Decimal("0.00"),
                )
            )

        rA = run(la)
        rB = run(lb)
        # A bears exactly its external slice (240.00), NOT 600*181/365.
        assert _cost_lines(rA)["HEIZUNG"].amount == Decimal("240.00")
        assert _cost_lines(rB)["HEIZUNG"].amount == Decimal("360.00")
        assert _cost_lines(rA)["HEIZUNG"].note == "extern abgerechnet (Messdienst)"
        assert _cost_lines(rA)["HEIZUNG"].amount + _cost_lines(rB)["HEIZUNG"].amount == Decimal("600.00")


# ===========================================================================
# Golden 5 + 8: Mischobjekt (exit) + snapshot/hash determinism
# ===========================================================================
class TestGolden5ExitScenario:
    """The DIRECT image of the M3 exit criterion (section 9.1 #5).

    Mischobjekt, 4 units:
      U1 WOHNUNG area 80, mea 300, consumption 30 -- tenant A (Jan-Jun) / B (Jul-Dec)
      U2 WOHNUNG area 60, mea 250, consumption 20 -- owner-occupied all year (no lease)
      U3 WOHNUNG area 50, mea 200, consumption 10 -- C (Jan-Mar), VACANCY (Apr-Jul), D (Aug-Dec)
      U4 GEWERBE  area 100, mea 250, consumption 40 -- tenant E all year
    sum(area) = 290, sum(mea) = 1000, sum(consumption) = 100.

    Positions (different keys + CO2 + Hausmeister + non-apportionable):
      GRUNDSTEUER 2900 WOHNFLAECHE; MUELL 480 EINHEIT; WASSER 600 VERBRAUCH;
      HEIZUNG 950 EXTERNE_ABRECHNUNG (per occupancy slice);
      CO2 (eff 350) WOHNFLAECHE TEILWEISE_PRUEFEN;
      HAUSMEISTER (eff 720) WOHNFLAECHE GEMISCHT_PRUEFEN;
      VERWALTERKOSTEN 1200 MEA NICHT_UMLAGEFAEHIG (must NOT appear).
    """

    def _scenario(self, target, advance):
        prop = _U(1)
        u1, u2, u3, u4 = _U(11), _U(12), _U(13), _U(14)
        lA, lB, lC, lD, lE = _U(21), _U(22), _U(23), _U(24), _U(25)
        own = _U(31)
        units = (
            UnitInput(id=u1, number="1", usage_type="WOHNUNG", area=Decimal("80"), mea=Decimal("300"), consumption=Decimal("30")),
            UnitInput(id=u2, number="2", usage_type="WOHNUNG", area=Decimal("60"), mea=Decimal("250"), consumption=Decimal("20")),
            UnitInput(id=u3, number="3", usage_type="WOHNUNG", area=Decimal("50"), mea=Decimal("200"), consumption=Decimal("10")),
            UnitInput(id=u4, number="4", usage_type="GEWERBE", area=Decimal("100"), mea=Decimal("250"), consumption=Decimal("40")),
        )
        leases = (
            LeasePeriodInput(lease_agreement_id=lA, unit_id=u1, tenant_profile_id=_U(41), valid_from=date(2025, 1, 1), valid_to=date(2025, 6, 30), tenant_name="Mieter A"),
            LeasePeriodInput(lease_agreement_id=lB, unit_id=u1, tenant_profile_id=_U(42), valid_from=date(2025, 7, 1), valid_to=date(2025, 12, 31), tenant_name="Mieter B"),
            LeasePeriodInput(lease_agreement_id=lC, unit_id=u3, tenant_profile_id=_U(43), valid_from=date(2025, 1, 1), valid_to=date(2025, 3, 31), tenant_name="Mieter C"),
            LeasePeriodInput(lease_agreement_id=lD, unit_id=u3, tenant_profile_id=_U(44), valid_from=date(2025, 8, 1), valid_to=date(2025, 12, 31), tenant_name="Mieter D"),
            LeasePeriodInput(lease_agreement_id=lE, unit_id=u4, tenant_profile_id=_U(45), valid_from=date(2025, 1, 1), valid_to=date(2025, 12, 31), tenant_name="Gewerbe E"),
        )
        owners = tuple(
            OwnerPeriodInput(unit_id=u, owner_profile_id=own, valid_from=date(2000, 1, 1), valid_to=None)
            for u in (u1, u2, u3, u4)
        )
        cat = _U(99)
        positions = (
            CostPositionInput(cost_entry_id=_U(50), category_id=cat, category_code="GRUNDSTEUER", label="Grundsteuer", gross_amount=Decimal("2900.00"), service_period_from=YEAR_START, service_period_to=YEAR_END, tenant_distribution_key="WOHNFLAECHE", tenant_apportionable="UMLAGEFAEHIG"),
            CostPositionInput(cost_entry_id=_U(51), category_id=cat, category_code="MUELL", label="Muellabfuhr", gross_amount=Decimal("480.00"), service_period_from=YEAR_START, service_period_to=YEAR_END, tenant_distribution_key="EINHEIT", tenant_apportionable="UMLAGEFAEHIG"),
            CostPositionInput(cost_entry_id=_U(52), category_id=cat, category_code="WASSER", label="Kaltwasser", gross_amount=Decimal("600.00"), service_period_from=YEAR_START, service_period_to=YEAR_END, tenant_distribution_key="VERBRAUCH", tenant_apportionable="UMLAGEFAEHIG"),
            CostPositionInput(
                cost_entry_id=_U(53), category_id=cat, category_code="HEIZUNG", label="Heizung/Warmwasser", gross_amount=Decimal("950.00"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                tenant_distribution_key="EXTERNE_ABRECHNUNG", tenant_apportionable="UMLAGEFAEHIG_EXTERN_PRUEFEN",
                external_amounts=(
                    ExternalAmountInput(unit_id=u1, tenant_amount=Decimal("210.00"), period_from=date(2025, 1, 1), period_to=date(2025, 6, 30)),
                    ExternalAmountInput(unit_id=u1, tenant_amount=Decimal("190.00"), period_from=date(2025, 7, 1), period_to=date(2025, 12, 31)),
                    ExternalAmountInput(unit_id=u3, tenant_amount=Decimal("100.00"), period_from=date(2025, 1, 1), period_to=date(2025, 3, 31)),
                    ExternalAmountInput(unit_id=u3, tenant_amount=Decimal("150.00"), period_from=date(2025, 8, 1), period_to=date(2025, 12, 31)),
                    ExternalAmountInput(unit_id=u4, tenant_amount=Decimal("300.00"), period_from=date(2025, 1, 1), period_to=date(2025, 12, 31)),
                ),
            ),
            CostPositionInput(cost_entry_id=_U(54), category_id=cat, category_code="CO2", label="CO2-Kosten (Mieteranteil)", gross_amount=Decimal("500.00"), service_period_from=YEAR_START, service_period_to=YEAR_END, tenant_distribution_key="WOHNFLAECHE", tenant_apportionable="TEILWEISE_PRUEFEN", apportionable_tenant_amount=Decimal("350.00")),
            CostPositionInput(cost_entry_id=_U(55), category_id=cat, category_code="HAUSMEISTER", label="Hausmeister (umlagefaehiger Anteil)", gross_amount=Decimal("1000.00"), service_period_from=YEAR_START, service_period_to=YEAR_END, tenant_distribution_key="WOHNFLAECHE", tenant_apportionable="GEMISCHT_PRUEFEN", apportionable_tenant_amount=Decimal("720.00")),
            CostPositionInput(cost_entry_id=_U(56), category_id=cat, category_code="VERWALTERKOSTEN", label="WEG-Verwalter", gross_amount=Decimal("1200.00"), service_period_from=YEAR_START, service_period_to=YEAR_END, tenant_distribution_key="MEA", tenant_apportionable="NICHT_UMLAGEFAEHIG"),
        )
        return SettlementInput(
            period_start=YEAR_START,
            period_end=YEAR_END,
            property=PropertyInput(id=prop, name="Mischhaus", management_type="MISCH"),
            target_lease_agreement_id=target,
            units=units,
            lease_periods=leases,
            owner_periods=owners,
            cost_positions=positions,
            advance_payments_total=advance,
        )

    def test_tenant_a_full_bill(self):
        # Tenant A = U1, Jan-Jun (181/365). Hand computation:
        #   GRUNDSTEUER U1 = 2900*80/290 = 800.00; A = 800*181/365 = 396.71
        #   MUELL U1 = 480/4 = 120.00; A = 120*181/365 = 59.51
        #   WASSER U1 = 600*30/100 = 180.00; A = 180*181/365 = 89.26
        #   HEIZUNG A (external direct) = 210.00
        #   CO2 U1 = 350*80/290 = 96.5517...; A = *181/365 = 47.88
        #   HAUSMEISTER U1 = 720*80/290 = 198.6206...; A = *181/365 = 98.49
        #   VERWALTERKOSTEN: NOT apportionable -> absent.
        #   total = 396.71+59.51+89.26+210.00+47.88+98.49 = 901.85
        r = compute_bk_settlement(self._scenario(_U(21), Decimal("700.00")))
        cl = _cost_lines(r)
        assert "VERWALTERKOSTEN" not in cl  # I-F
        assert cl["GRUNDSTEUER"].amount == Decimal("396.71")
        assert cl["MUELL"].amount == Decimal("59.51")
        assert cl["WASSER"].amount == Decimal("89.26")
        assert cl["HEIZUNG"].amount == Decimal("210.00")
        assert cl["CO2"].amount == Decimal("47.88")
        assert cl["HAUSMEISTER"].amount == Decimal("98.49")
        assert r.total_cost == Decimal("901.85")
        # Soll - Vorauszahlung 700 -> 201.85 Nachzahlung.
        assert r.balance == Decimal("201.85")
        assert _line(r, "BALANCE").label == "Nachzahlung"
        # CO2 / Hausmeister notes (BGH hint on partial apportionment).
        assert cl["CO2"].note == "nur Mieteranteil (CO2)"
        assert cl["HAUSMEISTER"].note == "nur umlagefaehiger Anteil"
        assert cl["CO2"].total_amount == Decimal("350.00")  # effective, not gross 500

    def test_gewerbe_no_vorwegabzug(self):
        # M3-A5: Gewerbe E participates by the chosen key like any unit; no
        # automatic Vorwegabzug. U4 GRUNDSTEUER = 2900*100/290 = 1000.00.
        r = compute_bk_settlement(self._scenario(_U(25), Decimal("1500.00")))
        assert _cost_lines(r)["GRUNDSTEUER"].amount == Decimal("1000.00")
        assert _cost_lines(r)["WASSER"].amount == Decimal("240.00")  # 600*40/100

    def test_vacancy_unit_owner_bears_gap(self):
        # U3: C (90 days) + D (153 days) + vacancy (122 days -> owner) = full
        # U3 share. For GRUNDSTEUER U3 full = 2900*50/290 = 500.00:
        #   C = 500*90/365 = 123.29 ; D = 500*153/365 = 209.59 ;
        #   vacancy = 500*122/365 = 167.12 -> owner. 123.29+209.59+167.12 = 500.00
        rC = compute_bk_settlement(self._scenario(_U(23), Decimal("200.00")))
        rD = compute_bk_settlement(self._scenario(_U(24), Decimal("300.00")))
        assert _cost_lines(rC)["GRUNDSTEUER"].amount == Decimal("123.29")
        assert _cost_lines(rD)["GRUNDSTEUER"].amount == Decimal("209.59")
        gs_vac = [
            v
            for v in rC.vacancy_attribution
            if v.cost_entry_id == _U(50) and v.unit_id == _U(13)
        ]
        assert len(gs_vac) == 1
        assert gs_vac[0].days == 122
        assert gs_vac[0].amount == Decimal("167.12")
        assert gs_vac[0].owner_profile_id == _U(31)
        assert (
            _cost_lines(rC)["GRUNDSTEUER"].amount
            + _cost_lines(rD)["GRUNDSTEUER"].amount
            + gs_vac[0].amount
        ) == Decimal("500.00")

    def test_snapshot_hash_deterministic(self):
        # Golden 8: finalize the same inputs twice -> identical hash.
        inp = self._scenario(_U(21), Decimal("700.00"))
        r1 = compute_bk_settlement(inp)
        r2 = compute_bk_settlement(inp)
        h1 = canonical_sha256(build_snapshot_payload(inp, r1))
        h2 = canonical_sha256(build_snapshot_payload(inp, r2))
        assert h1 == h2
        assert len(h1) == 64
