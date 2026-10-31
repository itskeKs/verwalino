"""Golden tests for the M6 Mietkonto engine -- hand-computed, exact cents.

m6-zahlungen-mietkonto.md section 7.1. Each test asserts NOT only the end total
but the individual offene Posten / Salden / DERIVED sums against the hand
computation, so a compensating double error cannot slip through (same
discipline as the M3/M4 Golden tests).

2025 is a non-leap year (365 days). March has 31 days; a lease from the 15th is
occupied 17 of 31 days (15..31 inclusive).

Pure-function tests -- no DB, no server.
"""

from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError, replace
from datetime import date
from decimal import Decimal
from itertools import permutations

import pytest

from app.engine import mietkonto
from app.engine.mietkonto import (
    AdvanceFilter,
    ExpectedCharge,
    LeaseChargeInput,
    OwnerAdvanceChargeInput,
    PaymentInput,
    PT_HAUSGELD_VORSCHUSS,
    PT_KALTMIETE,
    PT_NK_VORAUSZAHLUNG,
    account_statement,
    derive_advance_total,
    owner_expected_charges,
    tenant_expected_charges,
)

YEAR_START = date(2025, 1, 1)
YEAR_END = date(2025, 12, 31)


@pytest.mark.parametrize("month,amount,first_end,lease_end,frozen,expected", [
    (None, "1200.00", date(2025, 3, 31), date(2025, 9, 30), "300.00", "900.00"),
    (3, "100.00", date(2025, 3, 14), date(2025, 3, 20), "45.16", "54.84"),
])
def test_late_lease_end_credits_the_frozen_remainder(month, amount, first_end, lease_end, frozen, expected):
    payment = PaymentInput(uuid.UUID(int=1), PT_NK_VORAUSZAHLUNG, "IN", Decimal(amount),
        2025, month, lease_agreement_id=uuid.UUID(int=2))
    filt = AdvanceFilter(frozenset({PT_NK_VORAUSZAHLUNG}), 2025, payment.lease_agreement_id)
    from datetime import timedelta

    assert mietkonto.derive_advance_total_for_period([payment], filt, first_end + timedelta(days=1),
        lease_end, lease_start=YEAR_START, lease_end=lease_end,
        finalized_credits=(mietkonto.AdvanceCredit(2025, month, YEAR_START, first_end, Decimal(frozen)),),
    ) == Decimal(expected)


@pytest.mark.parametrize("start,end,credits,expected", [
    (date(2025, 4, 1), date(2025, 6, 30), [(YEAR_START, date(2025, 3, 31), "300.00"),
        (date(2025, 7, 1), date(2025, 9, 30), "500.01")], "399.99"),
    (YEAR_START, date(2025, 3, 31), [(date(2025, 4, 1), date(2025, 9, 30), "900.00")], "300.00"),
])
def test_later_frozen_statements_preserve_the_remainder_during_reordering_and_correction(start, end, credits, expected):
    payment = PaymentInput(uuid.UUID(int=1), PT_NK_VORAUSZAHLUNG, "IN", Decimal("1200.00"),
        2025, None, lease_agreement_id=uuid.UUID(int=2))
    filt = AdvanceFilter(frozenset({PT_NK_VORAUSZAHLUNG}), 2025, payment.lease_agreement_id)
    frozen = tuple(mietkonto.AdvanceCredit(2025, None, a, b, Decimal(amount)) for a, b, amount in credits)
    assert mietkonto.derive_advance_total_for_period([payment], filt, start, end,
        lease_start=YEAR_START, lease_end=date(2025, 9, 30), finalized_credits=frozen) == Decimal(expected)


def _U(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def _pay(
    pid: int,
    ptype: str,
    amount: str,
    *,
    year: int = 2025,
    month: int | None = None,
    lease=None,
    owner=None,
    direction: str = "IN",
    is_booked: bool = True,
    is_reversed: bool = False,
    is_reversal: bool = False,
) -> PaymentInput:
    return PaymentInput(
        payment_id=_U(pid),
        payment_type=ptype,
        direction=direction,
        amount=Decimal(amount),
        period_year=year,
        period_month=month,
        lease_agreement_id=lease,
        owner_profile_id=owner,
        is_booked=is_booked,
        is_reversed=is_reversed,
        is_reversal=is_reversal,
    )


@pytest.mark.parametrize("ptype,addressee", [("NK_VORAUSZAHLUNG", "lease"), ("HAUSGELD_VORSCHUSS", "owner")])
@pytest.mark.parametrize("cut,expected", [(14, "245.16"), (31, "300.00")])
def test_month_assigned_advances_reconcile_adjacent_periods(ptype, addressee, cut, expected):
    from datetime import timedelta

    payments = [_pay(m, ptype, "100.00", month=m, **{addressee: _U(50)}) for m in range(1, 13)]
    # Drafts, reversed payments, other types and addressees cannot enter either share.
    payments += [_pay(20, ptype, "999.99", month=3, is_booked=False, **{addressee: _U(50)}),
                 _pay(21, ptype, "999.99", month=3, is_reversed=True, **{addressee: _U(50)})]
    filt = AdvanceFilter(types=frozenset({ptype}), year=2025,
                         **{("lease_agreement_id" if addressee == "lease" else "owner_profile_id"): _U(50)})
    boundary = date(2025, 3, cut)
    first = mietkonto.derive_advance_total_for_period(payments, filt, date(2025, 1, 1), boundary)
    second = mietkonto.derive_advance_total_for_period(payments, filt, boundary + timedelta(days=1), date(2025, 12, 31))
    assert first == Decimal(expected)
    assert first + second == Decimal("1200.00")


@pytest.mark.parametrize("ptype,addressee", [("NK_VORAUSZAHLUNG", "lease"), ("HAUSGELD_VORSCHUSS", "owner")])
@pytest.mark.parametrize("start,end,months,amount,expected", [
    (date(2025, 3, 15), None, list(range(3, 13)), "100.00", "1000.00"),
    (date(2025, 7, 1), None, [None], "600.00", "600.00"),
    (date(2020, 1, 1), date(2025, 3, 14), [1, 2, 3], "100.00", "300.00"),
    (date(2025, 3, 15), None, [3], "54.84", "54.84"),
])
def test_advance_lease_boundaries_preserve_the_booked_amount(ptype, addressee, start, end, months, amount, expected):
    payments = [_pay(i, ptype, amount, month=m, **{addressee: _U(50)}) for i, m in enumerate(months)]
    filt = AdvanceFilter(types=frozenset({ptype}), year=2025,
                         **{("lease_agreement_id" if addressee == "lease" else "owner_profile_id"): _U(50)})
    assert mietkonto.derive_advance_total_for_period(
        payments, filt, max(start, YEAR_START), min(end or YEAR_END, YEAR_END),
        lease_start=start, lease_end=end,
    ) == Decimal(expected)


# ===========================================================================
# Golden 1: Soll-Ist je Mietvertrag, voll bezahlt (m6 section 7.1 #1)
#   Kaltmiete 700 + NK 150 = 850/Monat, 12 Monate, 12 Zahlungen a 850.
#   Jahres-Soll 10.200; Ist 10.200; Saldo 0; offene Posten ausgeglichen.
# ===========================================================================
class TestGolden1FullYearPaid:
    def test_soll_components_and_balance(self):
        lease = _U(21)
        lc = LeaseChargeInput(lease, YEAR_START, None, Decimal("700.00"), Decimal("150.00"))
        charges = tenant_expected_charges(lc, YEAR_START, YEAR_END)

        # 24 charges (12 KALTMIETE + 12 NK), each 700 / 150.
        assert len(charges) == 24
        kalt = [c for c in charges if c.payment_type == PT_KALTMIETE]
        nk = [c for c in charges if c.payment_type == PT_NK_VORAUSZAHLUNG]
        assert all(c.amount == Decimal("700.00") for c in kalt)
        assert all(c.amount == Decimal("150.00") for c in nk)
        assert sum((c.amount for c in kalt), Decimal("0")) == Decimal("8400.00")
        assert sum((c.amount for c in nk), Decimal("0")) == Decimal("1800.00")

        # 12 payments of 850 each, split into the two components (700 + 150).
        payments = []
        pid = 100
        for m in range(1, 13):
            payments.append(_pay(pid, PT_KALTMIETE, "700.00", month=m, lease=lease))
            payments.append(_pay(pid + 1, PT_NK_VORAUSZAHLUNG, "150.00", month=m, lease=lease))
            pid += 2

        result = account_statement(charges, payments)
        # Jahres-Soll 10.200, Ist 10.200, Saldo 0.
        assert result.soll_total == Decimal("10200.00")
        assert result.ist_total == Decimal("10200.00")
        assert result.balance == Decimal("0.00")
        # Every (month, type) open item is exactly 0 (ausgeglichen).
        assert all(item.open_amount == Decimal("0.00") for item in result.open_items)
        assert len(result.open_items) == 24


# ===========================================================================
# Golden 2: Teilzahlung -> offener Posten (m6 section 7.1 #2)
#   ein Monat 850 Soll, Zahlung 600 -> offener Posten 250 (offen > 0).
# ===========================================================================
class TestGolden2Partial:
    def test_partial_leaves_open_item(self):
        lease = _U(21)
        # One month of Soll: 700 KALTMIETE + 150 NK = 850.
        charges = [
            ExpectedCharge(2025, 6, PT_KALTMIETE, Decimal("700.00"), lease_agreement_id=lease),
            ExpectedCharge(2025, 6, PT_NK_VORAUSZAHLUNG, Decimal("150.00"), lease_agreement_id=lease),
        ]
        # Tenant pays only 600 against KALTMIETE.
        payments = [_pay(100, PT_KALTMIETE, "600.00", month=6, lease=lease)]
        result = account_statement(charges, payments)

        assert result.soll_total == Decimal("850.00")
        assert result.ist_total == Decimal("600.00")
        assert result.balance == Decimal("250.00")  # owes 250

        kalt_item = next(i for i in result.open_items if i.payment_type == PT_KALTMIETE)
        nk_item = next(i for i in result.open_items if i.payment_type == PT_NK_VORAUSZAHLUNG)
        assert kalt_item.open_amount == Decimal("100.00")  # 700 - 600
        assert kalt_item.is_arrears
        assert nk_item.open_amount == Decimal("150.00")  # 150 - 0
        assert nk_item.is_arrears


# ===========================================================================
# Golden 3: Ueberzahlung -> Guthaben (m6 section 7.1 #3)
#   ein Monat 850 Soll, Zahlung 1000 -> offener Posten -150 (Guthaben).
# ===========================================================================
class TestGolden3Overpayment:
    def test_overpayment_is_credit(self):
        lease = _U(21)
        charges = [
            ExpectedCharge(2025, 6, PT_KALTMIETE, Decimal("850.00"), lease_agreement_id=lease),
        ]
        payments = [_pay(100, PT_KALTMIETE, "1000.00", month=6, lease=lease)]
        result = account_statement(charges, payments)

        assert result.soll_total == Decimal("850.00")
        assert result.ist_total == Decimal("1000.00")
        assert result.balance == Decimal("-150.00")  # credit
        item = result.open_items[0]
        assert item.open_amount == Decimal("-150.00")
        assert item.is_credit


# ===========================================================================
# Golden 4: Unterjaehriger Mietbeginn -- tagegenauer Anschnitt (m6 §7.1 #4)
#   Lease ab 15.03., NK 150/Monat; Maerz anteilig 17/31 Tage.
#   Maerz-NK = 150 * 17/31 = 82.258.. -> 82.26 (ADR-0005); ganze Monate ab April voll.
# ===========================================================================
class TestGolden4PartialStart:
    def test_march_is_day_exact(self):
        lease = _U(21)
        lc = LeaseChargeInput(lease, date(2025, 3, 15), None, Decimal("700.00"), Decimal("150.00"))
        charges = tenant_expected_charges(lc, YEAR_START, YEAR_END)

        # Jan/Feb: no charge (lease starts 15.03). First month is March.
        nk = sorted(
            (c for c in charges if c.payment_type == PT_NK_VORAUSZAHLUNG),
            key=lambda c: c.period_month,
        )
        assert nk[0].period_month == 3
        # March: 17 of 31 days, day-exact share of 150.00.
        assert nk[0].days_occupied == 17
        assert nk[0].days_in_month == 31
        assert nk[0].amount == Decimal("82.26")  # 150 * 17/31 = 82.258.. -> 82.26
        # April..December are full months at 150.00 (9 full months: Apr..Dec).
        full = nk[1:]
        assert len(full) == 9
        assert all(c.amount == Decimal("150.00") for c in full)

        # KALTMIETE March = 700 * 17/31 = 383.87.. -> 383.87.
        kalt_march = next(
            c for c in charges if c.payment_type == PT_KALTMIETE and c.period_month == 3
        )
        assert kalt_march.amount == Decimal("383.87")  # 700*17/31 = 383.871 -> 383.87

    def test_no_charge_before_start(self):
        lease = _U(21)
        lc = LeaseChargeInput(lease, date(2025, 3, 15), None, Decimal("700.00"), Decimal("150.00"))
        charges = tenant_expected_charges(lc, YEAR_START, YEAR_END)
        months = {c.period_month for c in charges}
        assert 1 not in months and 2 not in months


# ===========================================================================
# Golden 5: DERIVED-NK-Vorauszahlung == MANUAL (m6 section 7.1 #5, Exit-Kern)
#   Mieter zahlt 12x 700 KALTMIETE + 12x 150 NK, period_year=2025, ein Lease.
#   DERIVED NK-Summe == 1800.00; die KALTMIETE-Zahlungen (8400) fliessen NICHT.
#   This is the direct image of the M6 exit + the sharpest Kaltmiete != NK test.
# ===========================================================================
class TestGolden5DerivedEqualsManual:
    def _scenario(self):
        lease = _U(21)
        payments = []
        pid = 100
        for m in range(1, 13):
            payments.append(_pay(pid, PT_KALTMIETE, "700.00", month=m, lease=lease))
            payments.append(_pay(pid + 1, PT_NK_VORAUSZAHLUNG, "150.00", month=m, lease=lease))
            pid += 2
        return lease, payments

    def test_derived_nk_sum_equals_manual_value(self):
        lease, payments = self._scenario()
        filt = AdvanceFilter(
            types=frozenset({PT_NK_VORAUSZAHLUNG}), year=2025, lease_agreement_id=lease
        )
        derived = derive_advance_total(payments, filt)
        # MANUAL value the clerk would have entered: 12 * 150 = 1800.00.
        manual = Decimal("1800.00")
        assert derived == manual

    def test_kaltmiete_does_not_flow_into_nk(self):
        lease, payments = self._scenario()
        filt = AdvanceFilter(
            types=frozenset({PT_NK_VORAUSZAHLUNG}), year=2025, lease_agreement_id=lease
        )
        derived = derive_advance_total(payments, filt)
        # The 12 * 700 = 8400 KALTMIETE must NOT be in the NK sum.
        assert derived == Decimal("1800.00")
        assert derived != Decimal("10200.00")  # would be the wrong total incl. rent

    def test_other_lease_payments_excluded(self):
        lease, payments = self._scenario()
        other_lease = _U(99)
        # Add NK payments for a DIFFERENT lease -- must not leak into this sum.
        payments = payments + [
            _pay(900, PT_NK_VORAUSZAHLUNG, "150.00", month=m, lease=other_lease)
            for m in range(1, 13)
        ]
        filt = AdvanceFilter(
            types=frozenset({PT_NK_VORAUSZAHLUNG}), year=2025, lease_agreement_id=lease
        )
        assert derive_advance_total(payments, filt) == Decimal("1800.00")

    def test_other_year_payments_excluded(self):
        lease, payments = self._scenario()
        payments = payments + [
            _pay(800, PT_NK_VORAUSZAHLUNG, "150.00", year=2024, month=m, lease=lease)
            for m in range(1, 13)
        ]
        filt = AdvanceFilter(
            types=frozenset({PT_NK_VORAUSZAHLUNG}), year=2025, lease_agreement_id=lease
        )
        assert derive_advance_total(payments, filt) == Decimal("1800.00")


# ===========================================================================
# Golden 6: Hausgeld-Soll-Ist (the M4 Spitze invariance lives in the WEG engine
#   test; here we check only the M6 part: owner Soll generation + Ist abgleich +
#   the DERIVED HAUSGELD_VORSCHUSS sum). m6 section 7.1 #6 / section 2.2.
#   Plan-Soll-Vorschuss 5500/Jahr (monthly_amount from finalized plan), Ist 5000.
# ===========================================================================
class TestGolden6HausgeldSollIst:
    def test_owner_monthly_soll_sums_over_units(self):
        owner = _U(31)
        # Plan: two units of this owner, monthly_amount 300 + 158.34 = 458.34/mo.
        # (e.g. annual 5500.08 split monthly -- here we use a clean per-unit sum.)
        lines = [
            OwnerAdvanceChargeInput(owner, _U(11), Decimal("300.00")),
            OwnerAdvanceChargeInput(owner, _U(12), Decimal("158.34")),
        ]
        charges = owner_expected_charges(lines, owner, YEAR_START, YEAR_END)
        assert len(charges) == 12
        assert all(c.amount == Decimal("458.34") for c in charges)
        assert all(c.payment_type == PT_HAUSGELD_VORSCHUSS for c in charges)
        # 12 * 458.34 = 5500.08 (exact, no re-rounding).
        assert sum((c.amount for c in charges), Decimal("0")) == Decimal("5500.08")

    def test_derived_hausgeld_sum_per_owner(self):
        owner_a = _U(31)
        owner_b = _U(32)
        # Owner A paid 5000 (10 * 500), Owner B paid 6000 -- B must not leak.
        payments = [
            _pay(100 + m, PT_HAUSGELD_VORSCHUSS, "500.00", month=m, owner=owner_a)
            for m in range(1, 11)
        ] + [
            _pay(300 + m, PT_HAUSGELD_VORSCHUSS, "500.00", month=m, owner=owner_b)
            for m in range(1, 13)
        ]
        filt = AdvanceFilter(
            types=frozenset({PT_HAUSGELD_VORSCHUSS}), year=2025, owner_profile_id=owner_a
        )
        assert derive_advance_total(payments, filt) == Decimal("5000.00")

    def test_no_finalized_plan_means_empty_soll(self):
        # [F6-5] no advance_lines -> no owner Soll (Hausgeld-Konto shows only Ist).
        owner = _U(31)
        assert owner_expected_charges([], owner, YEAR_START, YEAR_END) == []


# ===========================================================================
# Golden 7: Storno fliesst nicht in DERIVED (m6 section 7.1 #7)
#   Mieter zahlt NK 150, dann Storno (gegenlaeufig), dann korrekte 160.
#   DERIVED-NK-Summe = 160.00 (Original + Storno netto null).
# ===========================================================================
class TestGolden7StornoNetNull:
    def test_storno_pair_nets_to_zero(self):
        lease = _U(21)
        # Original 150 (now reversed) + its Storno row (reversal) + correct 160.
        original = _pay(100, PT_NK_VORAUSZAHLUNG, "150.00", month=6, lease=lease, is_reversed=True)
        storno = _pay(
            101, PT_NK_VORAUSZAHLUNG, "150.00", month=6, lease=lease,
            direction="OUT", is_reversal=True,
        )
        correct = _pay(102, PT_NK_VORAUSZAHLUNG, "160.00", month=6, lease=lease)
        payments = [original, storno, correct]

        filt = AdvanceFilter(
            types=frozenset({PT_NK_VORAUSZAHLUNG}), year=2025, lease_agreement_id=lease
        )
        # Only the correct 160 survives (Variante A drops original + storno).
        assert derive_advance_total(payments, filt) == Decimal("160.00")

    def test_storno_pair_nets_to_zero_in_account_statement(self):
        lease = _U(21)
        charges = [
            ExpectedCharge(2025, 6, PT_NK_VORAUSZAHLUNG, Decimal("160.00"), lease_agreement_id=lease),
        ]
        original = _pay(100, PT_NK_VORAUSZAHLUNG, "150.00", month=6, lease=lease, is_reversed=True)
        storno = _pay(
            101, PT_NK_VORAUSZAHLUNG, "150.00", month=6, lease=lease,
            direction="OUT", is_reversal=True,
        )
        correct = _pay(102, PT_NK_VORAUSZAHLUNG, "160.00", month=6, lease=lease)
        result = account_statement(charges, [original, storno, correct])
        # Ist == 160 (the reversed pair is excluded), so balance is exactly 0.
        assert result.ist_total == Decimal("160.00")
        assert result.balance == Decimal("0.00")


# ===========================================================================
# Extra: draft payments do NOT count as Ist (only booked, m6 section 5).
# ===========================================================================
class TestDraftNotCounted:
    def test_draft_payment_excluded_from_ist_and_derived(self):
        lease = _U(21)
        booked = _pay(100, PT_NK_VORAUSZAHLUNG, "150.00", month=6, lease=lease)
        draft = _pay(101, PT_NK_VORAUSZAHLUNG, "999.00", month=6, lease=lease, is_booked=False)
        filt = AdvanceFilter(
            types=frozenset({PT_NK_VORAUSZAHLUNG}), year=2025, lease_agreement_id=lease
        )
        assert derive_advance_total([booked, draft], filt) == Decimal("150.00")

        charges = [ExpectedCharge(2025, 6, PT_NK_VORAUSZAHLUNG, Decimal("150.00"), lease_agreement_id=lease)]
        result = account_statement(charges, [booked, draft])
        assert result.ist_total == Decimal("150.00")


# T23: effective-dated full rates; round each monthly component only once.
class TestGoldenRentChanges:
    @staticmethod
    def change(day, rent="750.00", nk="150.00"):
        return mietkonto.RentChangeInput(day, Decimal(rent), Decimal(nk))

    @staticmethod
    def lease(changes=(), start=date(2026, 1, 1), end=None):
        return LeaseChargeInput(
            _U(21), start, end, Decimal("700.00"), Decimal("150.00"), changes
        )

    def test_march_fifteenth_is_day_exact(self):
        lc = self.lease((self.change(date(2026, 3, 15)),))
        charges = tenant_expected_charges(lc, date(2026, 1, 1), date(2026, 4, 30))
        assert [(c.period_month, c.payment_type, c.amount) for c in charges] == [
            (month, kind, Decimal(amount))
            for month, rent in [(1, "700.00"), (2, "700.00"), (3, "727.42"), (4, "750.00")]
            for kind, amount in [(PT_KALTMIETE, rent), (PT_NK_VORAUSZAHLUNG, "150.00")]
        ]
        assert all(c.lease_agreement_id == lc.lease_agreement_id for c in charges)
        assert all(c.period_year == 2026 and c.days_occupied == c.days_in_month for c in charges)
        assert lc.base_rent == Decimal("700.00")
        assert lc.advance_payment == Decimal("150.00")

    def test_unsorted_history_is_deterministic_and_not_mutated(self):
        changes = (
            self.change(date(2027, 1, 1), "999.00", "999.00"),
            self.change(date(2026, 3, 20), "0.00", "0.00"),
            self.change(date(2025, 12, 1), "600.00", "120.00"),
            self.change(date(2026, 3, 10), "900.00", "180.00"),
        )
        # March: 9*600 + 10*900 + 12*0 = 14400; NK numerator = 2880.
        for history in permutations(changes):
            lc = self.lease(history)
            charges = tenant_expected_charges(lc, date(2026, 3, 1), date(2026, 3, 31))
            assert [c.amount for c in charges] == [Decimal("464.52"), Decimal("92.90")]
            assert lc.rent_changes is history
            assert lc.rent_changes == history
        with pytest.raises(FrozenInstanceError):
            changes[0].base_rent = Decimal("1.00")
        with pytest.raises(FrozenInstanceError):
            lc.rent_changes = ()

    @pytest.mark.parametrize("day", [date(2025, 1, 1), date(2026, 3, 15), date(2027, 1, 1)])
    def test_duplicate_dates_are_rejected_even_outside_period(self, day):
        lc = self.lease((self.change(day), self.change(day, "800.00")))
        with pytest.raises(ValueError, match="[Dd]uplicate|[Dd]oppelt"):
            tenant_expected_charges(lc, date(2026, 3, 1), date(2026, 3, 31))

    @pytest.mark.parametrize(
        "start,end,period_start,period_end,rent,nk,days",
        [
            (date(2026, 3, 10), date(2026, 3, 20), date(2026, 1, 1), date(2026, 12, 31), "258.06", "53.23", 11),
            (date(2026, 1, 1), None, date(2026, 3, 12), date(2026, 3, 18), "164.52", "33.87", 7),
            (date(2026, 3, 15), date(2026, 3, 15), date(2026, 3, 1), date(2026, 3, 31), "24.19", "4.84", 1),
        ],
    )
    def test_lease_and_period_bounds(self, start, end, period_start, period_end, rent, nk, days):
        lc = self.lease((self.change(date(2026, 3, 15)),), start, end)
        charges = tenant_expected_charges(lc, period_start, period_end)
        assert [c.amount for c in charges] == [Decimal(rent), Decimal(nk)]
        assert all(c.days_occupied == days and c.days_in_month == 31 for c in charges)

    @pytest.mark.parametrize(
        "start,end,expected",
        [
            (date(2026, 2, 1), date(2026, 2, 28), ["700.00", "150.00"]),
            (date(2026, 3, 1), date(2026, 3, 1), ["24.19", "5.81"]),
            (date(2026, 3, 31), date(2026, 3, 31), ["25.81", "6.45"]),
            (date(2026, 4, 1), date(2026, 4, 30), ["900.00", "210.00"]),
        ],
    )
    def test_first_and_last_day_and_next_month(self, start, end, expected):
        lc = self.lease((
            self.change(date(2026, 3, 1), "750.00", "180.00"),
            self.change(date(2026, 3, 31), "800.00", "200.00"),
            self.change(date(2026, 4, 1), "900.00", "210.00"),
        ))
        assert [c.amount for c in tenant_expected_charges(lc, start, end)] == [Decimal(a) for a in expected]

    def test_leap_february_change_on_twenty_ninth(self):
        lc = self.lease((
            self.change(date(2024, 2, 15), "750.00", "180.00"),
            self.change(date(2024, 2, 29), "800.00", "200.00"),
        ), start=date(2024, 1, 1))
        charges = tenant_expected_charges(lc, date(2024, 2, 1), date(2024, 2, 29))
        assert [c.amount for c in charges] == [Decimal("727.59"), Decimal("166.21")]
        assert all(c.days_occupied == c.days_in_month == 29 for c in charges)

    def test_outside_lease_and_reversed_period(self):
        lc = self.lease((self.change(date(2026, 3, 15)),), date(2026, 3, 10), date(2026, 3, 20))
        for start, end in [
            (date(2026, 1, 1), date(2026, 3, 9)),
            (date(2026, 3, 21), date(2026, 12, 31)),
            (date(2026, 3, 20), date(2026, 3, 10)),
        ]:
            assert tenant_expected_charges(lc, start, end) == []

    def test_empty_history_keeps_old_positional_constructor(self):
        old = LeaseChargeInput(_U(21), YEAR_START, None, Decimal("700.00"), Decimal("150.00"))
        assert old.rent_changes == ()
        explicit = replace(old, rent_changes=())
        assert tenant_expected_charges(old, YEAR_START, YEAR_END) == tenant_expected_charges(explicit, YEAR_START, YEAR_END)

    def test_redundant_changes_preserve_constant_rate_half_cent_split(self):
        lc = replace(self.lease(), base_rent=Decimal("0.01"), advance_payment=Decimal("0.03"))
        changed = replace(lc, rent_changes=(
            self.change(date(2026, 2, 7), "0.01", "0.03"),
            self.change(date(2026, 2, 10), "0.01", "0.03"),
        ))
        start, end = date(2026, 2, 1), date(2026, 2, 14)
        original = tenant_expected_charges(lc, start, end)
        assert [c.amount for c in original] == [Decimal("0.01"), Decimal("0.02")]
        assert tenant_expected_charges(changed, start, end) == original

    def test_cent_fragments_sum_before_rounding_and_match_daily_oracle(self):
        changes = (
            self.change(date(2026, 3, 11), "0.05", "0.01"),
            self.change(date(2026, 3, 21), "0.02", "0.01"),
        )
        lc = replace(self.lease(changes), base_rent=Decimal("0.02"), advance_payment=Decimal("0.01"))
        charges = tenant_expected_charges(lc, date(2026, 3, 1), date(2026, 3, 31))
        # Segment rounding gives rent 0.04 and NK 0.00, instead of 0.03 / 0.01.
        assert [c.amount for c in charges] == [Decimal("0.03"), Decimal("0.01")]
        for day in range(2, 31):
            if day in (11, 21):
                continue
            varied = replace(lc, rent_changes=changes + (self.change(date(2026, 3, day), "0.01", "0.01"),))
            numerators = [Decimal(0), Decimal(0)]
            for occupied_day in range(1, 32):
                rate = lc
                for change in sorted(varied.rent_changes, key=lambda c: c.valid_from):
                    if change.valid_from <= date(2026, 3, occupied_day):
                        rate = change
                numerators[0] += rate.base_rent
                numerators[1] += rate.advance_payment
            actual = tenant_expected_charges(varied, date(2026, 3, 1), date(2026, 3, 31))
            assert [c.amount for c in actual] == [(n / 31).quantize(Decimal("0.01")) for n in numerators]

    @pytest.mark.parametrize("field", ["base_rent", "advance_payment"])
    @pytest.mark.parametrize("invalid", [1.5, "1.00", Decimal("-0.01"), Decimal("NaN"), Decimal("Infinity"), Decimal("0.001")])
    def test_rate_validation(self, field, invalid):
        values = dict(valid_from=date(2026, 3, 15), base_rent=Decimal("750.00"), advance_payment=Decimal("150.00"))
        values[field] = invalid
        with pytest.raises((TypeError, ValueError)):
            mietkonto.RentChangeInput(**values)
