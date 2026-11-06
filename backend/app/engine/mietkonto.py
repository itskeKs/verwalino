"""M6 Mietkonto engine -- a layer of PURE functions (Soll/Ist/Saldo, derive).

m6-zahlungen-mietkonto.md section 2/3/6; ADR-0009 (Soll abgeleitet), ADR-0010.

HARD RULE (same as the rest of app/engine, SDD section 16):
  This module contains ONLY pure computation. It MUST NOT import from
  app.models, app.repositories, app.services, app.api, sqlalchemy, fastapi,
  or perform any DB / HTTP / file I/O. Everything in and out is Decimal,
  date, and frozen dataclasses.

  This separation is what makes the M6 arithmetic testable in isolation
  (Golden tests + property-based invariants, m6 section 7) and is the
  precondition for the deterministic DERIVED bridge into the M3/M4 abrechnung
  (derive_advance_total, section 6) WITHOUT touching the M3/M4 engine.

Three responsibilities, mirroring m6 section 2/3/6:

  1. Soll-Generierung (section 2) -- monthly scale (consistent with ADR-0007
     E4-3b), tag-exact partial months at lease start/end:
       tenant_expected_charges(lease, period) -> [ExpectedCharge]
       owner_expected_charges(owner_advance_lines, period) -> [ExpectedCharge]
     The Soll is DERIVED from contract/plan, never persisted (ADR-0009).

  2. Ist / Saldo (section 3) -- group Soll vs the booked, non-reversed payments
     per (period, type); offen = Soll - zugeordnetes Ist; running balance:
       account_statement(expected_charges, payments) -> MietkontoResult

  3. Abrechnungs-Anbindung (section 6) -- the DERIVED advance-payment sum, a
     pure filter+sum used by the M3/M4 service to fill advance_payments_total /
     paid_advances. Storno Variante A (section 6.6):
       derive_advance_total(payments, filter) -> Decimal

Money is ALWAYS Decimal, never float (architecture.md I4, ADR-0005). Soll/Ist
summation uses final cent amounts (m6 §3.1). Constant-rate partial months keep
the Largest-Remainder split (ADR-0005). Effective-dated rates accumulate exact
rate-days per month/component before a single final cent rounding.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from app.engine.fristen import FederalState, owner_due_date, public_holidays, third_working_day

from app.engine.periods import day_count, owner_at, build_owner_cover
from app.engine.types import OwnerPeriodInput
from app.engine.rounding import CENT, annual_advance_share_for_period, distribute_amount, monthly_advance_share_for_period

# Payment-type vocabulary (m6 section 1.3). Kept here as the engine's view of
# the controlled vocabulary; the DB CHECK in migration 0016 is the source of
# truth. The engine never invents a type.
PT_KALTMIETE = "KALTMIETE"
PT_NK_VORAUSZAHLUNG = "NK_VORAUSZAHLUNG"
PT_HK_VORAUSZAHLUNG = "HK_VORAUSZAHLUNG"
_TENANT_ADVANCE_TYPES = frozenset({PT_NK_VORAUSZAHLUNG, PT_HK_VORAUSZAHLUNG})
PT_HAUSGELD_VORSCHUSS = "HAUSGELD_VORSCHUSS"
PT_RUECKLAGENANTEIL = "RUECKLAGENANTEIL"
PT_SONDERZAHLUNG = "SONDERZAHLUNG"
PT_NACHZAHLUNG = "NACHZAHLUNG"
PT_KAUTION = "KAUTION"


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT)


def _months_in_period(period_start: date, period_end: date) -> list[tuple[int, int]]:
    """The (year, month) pairs the period touches, in chronological order.

    A period that begins/ends mid-month still yields that whole calendar month
    (the Soll for it is then computed tag-exact in tenant_expected_charges).
    """
    months: list[tuple[int, int]] = []
    y, m = period_start.year, period_start.month
    while (y, m) <= (period_end.year, period_end.month):
        months.append((y, m))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return months


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    """Tag-inclusive [first, last] day of a calendar month."""
    first = date(year, month, 1)
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    last = date.fromordinal(nxt.toordinal() - 1)
    return first, last


# ===========================================================================
# Inputs (frozen copies; the service maps ORM rows to these at the read-edge).
# ===========================================================================
@dataclass(frozen=True)
class RentChangeInput:
    """Full monthly rates effective from valid_from (inclusive), without I/O."""

    valid_from: date
    base_rent: Decimal
    advance_payment: Decimal
    heating_advance_payment: Decimal = Decimal('0.00')

    def __post_init__(self) -> None:
        for name in ("base_rent", "advance_payment", "heating_advance_payment"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be Decimal")
            if not value.is_finite() or value < 0 or value % CENT != 0:
                raise ValueError(f"{name} must be finite, non-negative and cent-exact")


@dataclass(frozen=True)
class LeaseChargeInput:
    """Frozen copy of a lease_agreement row for Soll generation (m6 section 2.1).

    valid_to is TAG-INCLUSIVE (last occupied day) -- the service converts the
    half-open DB bound via app.core.temporal.db_exclusive_to_engine_inclusive
    (ADR-0006), exactly like the M3 read-edge. None = open-ended.
    """

    lease_agreement_id: uuid.UUID
    valid_from: date
    valid_to: date | None
    base_rent: Decimal
    advance_payment: Decimal
    rent_changes: tuple[RentChangeInput, ...] = ()
    heating_advance_payment: Decimal = Decimal('0.00')


@dataclass(frozen=True)
class OwnerAdvanceChargeInput:
    """Frozen copy of an OWNER_ADVANCE economic_plan_line (m6 section 2.2).

    monthly_amount is the M4-computed monatsbasierte Hausgeld rate (ADR-0007
    E4-3b). One line per (owner, unit); the owner Soll per month is the SUM
    over the owner's units (exact, no re-rounding -- m6 section 2.2).
    """

    owner_profile_id: uuid.UUID
    unit_id: uuid.UUID | None
    monthly_amount: Decimal


@dataclass(frozen=True)
class PaymentInput:
    """Frozen copy of a payment row (m6 section 1.2) for Ist / derivation.

    Only the fields the pure arithmetic needs. `is_booked` and `is_reversed`
    are pre-resolved by the service from status / reversed_by_id so the engine
    has no notion of the DB lifecycle (it just filters on the two booleans).
    """

    payment_id: uuid.UUID
    payment_type: str
    direction: str  # 'IN' | 'OUT'
    amount: Decimal  # always positive; direction carries the sign
    period_year: int | None
    period_month: int | None
    lease_agreement_id: uuid.UUID | None = None
    tenant_profile_id: uuid.UUID | None = None
    owner_profile_id: uuid.UUID | None = None
    is_booked: bool = True
    is_reversed: bool = False  # True if reversed_by_id IS NOT NULL
    is_reversal: bool = False  # True if reverses_id IS NOT NULL (a Storno row)
    value_date: date | None = None

    @property
    def signed_amount(self) -> Decimal:
        """+amount for IN, -amount for OUT (for net Ist / balance, m6 §3)."""
        return self.amount if self.direction == "IN" else -self.amount


@dataclass(frozen=True)
class DepositResult:
    """Contractual deposit and active lifetime receipts/refunds, separate from rent."""

    expected_amount: Decimal
    paid_amount: Decimal
    refunded_amount: Decimal
    held_amount: Decimal


def deposit_statement(expected_amount: Decimal, payments: list[PaymentInput]) -> DepositResult:
    """Summarize one lease's deposit without interest or repayment assumptions."""
    active = [p for p in payments if p.payment_type == PT_KAUTION
              and p.is_booked and not p.is_reversed and not p.is_reversal]
    paid = sum((p.amount for p in active if p.direction == "IN"), Decimal("0.00"))
    refunded = sum((p.amount for p in active if p.direction == "OUT"), Decimal("0.00"))
    return DepositResult(_money(expected_amount), _money(paid), _money(refunded), _money(paid - refunded))


# ===========================================================================
# Soll (expected charges) -- DERIVED, monthly scale, tag-exact partial months.
# ===========================================================================
@dataclass(frozen=True)
class ExpectedCharge:
    """One monthly Soll position (m6 section 2.3).

    For a tenant: split rent, operating advances and heating advances so
    each advance credits its matching statement component (section 6.2). For an owner: the
    HAUSGELD_VORSCHUSS monthly Soll. `days_occupied`/`days_in_month` document a
    tag-exact partial month (full month => equal); they are nachrichtlich, the
    amount is already the final cent value.
    """

    period_year: int
    period_month: int
    payment_type: str
    amount: Decimal
    lease_agreement_id: uuid.UUID | None = None
    owner_profile_id: uuid.UUID | None = None
    days_occupied: int | None = None
    days_in_month: int | None = None
    due_date: date | None = None


def special_levy_expected_charges(
    allocations: list[tuple[date, Decimal]], owner_profile_id: uuid.UUID,
    period_start: date, period_end: date,
) -> list[ExpectedCharge]:
    """Frozen one-off obligations, in their due month, without daily proration."""
    return [ExpectedCharge(period_year=due.year, period_month=due.month,
        payment_type="SONDERZAHLUNG", amount=amount, owner_profile_id=owner_profile_id, due_date=due)
        for due, amount in allocations if period_start <= due <= period_end]


def _split_partial_month(monthly_amount: Decimal, days_occupied: int, days_in_month: int) -> Decimal:
    """Tag-exact partial-month share of `monthly_amount` (m6 section 2.1).

    The split routes through distribute_amount (ADR-0005 Largest-Remainder)
    over weights [days_occupied, days_not_occupied], taking the occupied share.
    This guarantees that, over the months of a split (e.g. a mid-month move-in),
    the occupied + the (theoretical) unoccupied parts sum EXACTLY to the full
    monthly amount -- no cent invented or lost (consistent with M3 day-split).

    A full month (days_occupied == days_in_month) returns monthly_amount
    unchanged (distribute over a single positive weight).
    """
    monthly_amount = _money(monthly_amount)
    if days_occupied >= days_in_month:
        return monthly_amount
    if days_occupied <= 0:
        return Decimal("0.00")
    days_rest = days_in_month - days_occupied
    occupied_share, _rest = distribute_amount(
        monthly_amount, [Decimal(days_occupied), Decimal(days_rest)]
    )
    return occupied_share


def _sum_rate_segments(segments: list[tuple[Decimal, int]], days_in_month: int) -> Decimal:
    """Round once after summing rate-days; preserve constant-rate day splits."""
    first_rate = segments[0][0]
    if all(rate == first_rate for rate, _days in segments):
        return _split_partial_month(first_rate, sum(days for _rate, days in segments), days_in_month)
    numerator = sum((rate * days for rate, days in segments), Decimal(0))
    return _money(numerator / Decimal(days_in_month))


def tenant_expected_charges(
    lease: LeaseChargeInput,
    period_start: date,
    period_end: date,
    *, federal_state: FederalState | None = None,
) -> list[ExpectedCharge]:
    """Monthly tenant Soll over [period_start, period_end] (m6 section 2.1).

    Rent and operating charges per occupied month, plus HK_VORAUSZAHLUNG
    when the heating rate is positive. The lease is clamped to its own
    tag-inclusive [valid_from, valid_to]; a month the lease does not touch
    yields no charge. A month the lease only partially covers is tag-exact
    (section 2.1, provisional assumption F6-4: tagegenau, consistent with M3).

    Rent changes are full rates effective on their date (inclusive), regardless
    of input order. Base rates apply before the first change. Each component's
    rate-days are summed before its single monthly rounding.

    Deterministic order: chronological months, KALTMIETE, then NK, then HK per month.
    """
    changes = sorted(lease.rent_changes, key=lambda change: change.valid_from)
    for previous, current in zip(changes, changes[1:]):
        if previous.valid_from == current.valid_from:
            raise ValueError(f"Duplicate rent change date: {current.valid_from}")

    charges: list[ExpectedCharge] = []
    lease_end = lease.valid_to  # tag-inclusive; None = open-ended
    for year, month in _months_in_period(period_start, period_end):
        m_first, m_last = _month_bounds(year, month)
        # The occupied span = month ∩ lease ∩ settlement period.
        occ_start = max(m_first, lease.valid_from, period_start)
        occ_end = m_last if lease_end is None else min(m_last, lease_end)
        occ_end = min(occ_end, period_end)
        days_occupied = day_count(occ_start, occ_end)
        if days_occupied <= 0:
            continue
        days_in_month = day_count(m_first, m_last)
        rate = lease
        segment_start = occ_start
        rent_segments: list[tuple[Decimal, int]] = []
        nk_segments: list[tuple[Decimal, int]] = []
        hk_segments: list[tuple[Decimal, int]] = []
        for change in changes:
            if change.valid_from <= occ_start:
                rate = change
                continue
            if change.valid_from > occ_end:
                break
            # The previous rate ends immediately before the change's first day.
            days = change.valid_from.toordinal() - segment_start.toordinal()
            rent_segments.append((rate.base_rent, days))
            nk_segments.append((rate.advance_payment, days))
            hk_segments.append((rate.heating_advance_payment, days))
            segment_start = change.valid_from
            rate = change
        days = day_count(segment_start, occ_end)
        rent_segments.append((rate.base_rent, days))
        nk_segments.append((rate.advance_payment, days))
        hk_segments.append((rate.heating_advance_payment, days))
        holidays = public_holidays(year, federal_state)
        if month == 12:
            holidays |= public_holidays(year + 1, federal_state)
        due = third_working_day(year, month, holidays, start_date=max(m_first, lease.valid_from))
        rent = _sum_rate_segments(rent_segments, days_in_month)
        nk = _sum_rate_segments(nk_segments, days_in_month)
        hk = _sum_rate_segments(hk_segments, days_in_month)
        charges.append(
            ExpectedCharge(
                period_year=year,
                period_month=month,
                payment_type=PT_KALTMIETE,
                due_date=due,
                amount=rent,
                lease_agreement_id=lease.lease_agreement_id,
                days_occupied=days_occupied,
                days_in_month=days_in_month,
            )
        )
        charges.append(
            ExpectedCharge(
                period_year=year,
                period_month=month,
                payment_type=PT_NK_VORAUSZAHLUNG,
                due_date=due,
                amount=nk,
                lease_agreement_id=lease.lease_agreement_id,
                days_occupied=days_occupied,
                days_in_month=days_in_month,
            )
        )
        if any(amount > 0 for amount, days in hk_segments if days > 0):
            charges.append(ExpectedCharge(period_year=year, period_month=month,
                payment_type=PT_HK_VORAUSZAHLUNG, amount=hk,
                lease_agreement_id=lease.lease_agreement_id,
                days_occupied=days_occupied, days_in_month=days_in_month))
    return charges


def owner_expected_charges(
    advance_lines: list[OwnerAdvanceChargeInput],
    owner_profile_id: uuid.UUID,
    period_start: date,
    period_end: date,
    *, owner_periods: list[OwnerPeriodInput] | None = None,
    plan_start: date | None = None,
    monthly_due_day: int | None = None,
    federal_state: FederalState | None = None,
) -> list[ExpectedCharge]:
    """Monthly owner Hausgeld Soll over the period (m6 section 2.2).

    The frozen monthly volume stays with its unit. Dated ownership assigns it
    to that month's owners, splitting a transfer month by inclusive owner-days
    and concurrent successors by positive mea_share. Continuing co-owners retain
    their frozen cents; only departed owners' rates pass to successors. Ambiguous
    transfers fail instead of moving other shares. Largest-remainder rounding
    preserves the unit total. Without history, retain the frozen owner amounts.

    [F6-5] no finalized plan => caller passes no advance_lines => empty Soll
    (the Hausgeld-Konto then shows only Ist, no Soll abgleich); the engine does
    not invent a Soll.
    """
    charges: list[ExpectedCharge] = []
    inherited_rates: dict[uuid.UUID, dict[uuid.UUID, Decimal]] = {}
    for year, month in _months_in_period(plan_start or period_start, period_end):
        m_first, m_last = _month_bounds(year, month)
        days_in_month = day_count(m_first, m_last)
        monthly_total = Decimal("0.00")
        if owner_periods is None:
            monthly_total = sum((ln.monthly_amount for ln in advance_lines
                if ln.owner_profile_id == owner_profile_id), Decimal("0.00"))
        else:
            # Only a unit's own frozen advance funds its dated successors.
            unit_totals: dict[uuid.UUID, Decimal] = {}
            for ln in advance_lines:
                if ln.unit_id is None:
                    raise ValueError("Hausgeld plan line has no unit")
                unit_totals[ln.unit_id] = unit_totals.get(ln.unit_id, Decimal(0)) + ln.monthly_amount
            for unit, total in sorted(unit_totals.items(), key=lambda pair: str(pair[0])):
                if not total:
                    continue
                previous = inherited_rates.setdefault(unit,
                    {ln.owner_profile_id: ln.monthly_amount for ln in advance_lines if ln.unit_id == unit})
                cover = build_owner_cover(unit, m_first, m_last, owner_periods)
                boundaries = sorted({m_first, *(p.start for p in cover),
                    *(date.fromordinal(p.end.toordinal() + 1) for p in cover if p.end < m_last)})
                weights: dict[uuid.UUID, Decimal] = {}
                for i, start in enumerate(boundaries):
                    end = boundaries[i + 1].toordinal() - 1 if i + 1 < len(boundaries) else m_last.toordinal()
                    owners = owner_at(owner_periods, unit, start)
                    if len(owners) > 1 and any(o.mea_share is None or o.mea_share <= 0 for o in owners):
                        raise ValueError("Co-ownership requires positive shares for Hausgeld")
                    active = {o.owner_profile_id for o in owners}
                    rates = {oid: amount for oid, amount in previous.items() if oid in active}
                    departed = set(previous) - active
                    transferred = sum((previous[oid] for oid in departed), Decimal(0))
                    successors = [o for o in owners if o.owner_profile_id not in previous]
                    # A sole remaining co-owner is an unambiguous full buyer.
                    if departed and not successors and len(owners) == 1:
                        successors = owners
                    if bool(departed) != bool(successors):
                        raise ValueError("Ownership does not identify successors of the frozen advances")
                    if len(departed) > 1 and len(successors) > 1:
                        raise ValueError("Several concurrent transfers do not identify individual successors")
                    if successors:
                        parts = distribute_amount(transferred,
                            [o.mea_share if len(successors) > 1 else Decimal(1) for o in successors])
                        for op, amount in zip(successors, parts):
                            rates[op.owner_profile_id] = rates.get(op.owner_profile_id, Decimal(0)) + amount
                    previous = rates
                    inherited_rates[unit] = rates
                    for oid, rate in rates.items():
                        weights[oid] = weights.get(oid, Decimal(0)) + rate * (end - start.toordinal() + 1)
                ids = sorted(weights, key=str)
                amounts = distribute_amount(total, [weights[oid] for oid in ids])
                monthly_total += dict(zip(ids, amounts)).get(owner_profile_id, Decimal(0))
        if monthly_total and (year, month) >= (period_start.year, period_start.month):
            charges.append(ExpectedCharge(period_year=year, period_month=month,
                payment_type=PT_HAUSGELD_VORSCHUSS, amount=monthly_total,
                owner_profile_id=owner_profile_id, days_occupied=days_in_month, days_in_month=days_in_month,
                due_date=owner_due_date(year, month, monthly_due_day, federal_state=federal_state)))
    return charges


# ===========================================================================
# Ist / Saldo / offene Posten (m6 section 3).
# ===========================================================================
@dataclass(frozen=True)
class OpenItem:
    """The DERIVED saldo per (period, type, optional due date) -- m6 section 1.4 / 3.

    open = Soll - zugeordnetes Ist.
      open > 0 -> Rueckstand (owes); open < 0 -> Guthaben; 0 -> ausgeglichen.
    NOT persisted (ADR-0009): purely computed and returned read-only.
    """

    period_year: int
    period_month: int | None
    payment_type: str
    soll: Decimal
    ist: Decimal
    open_amount: Decimal  # soll - ist
    due_date: date | None = None
    is_due: bool | None = None
    payment_changes: tuple[tuple[date, Decimal], ...] = ()

    @property
    def is_arrears(self) -> bool:
        return self.open_amount > 0

    @property
    def is_credit(self) -> bool:
        return self.open_amount < 0


@dataclass(frozen=True)
class MietkontoResult:
    """Soll/Ist/Saldo over a period (m6 section 3.1).

    `open_items` is one row per (period_year, period_month, payment_type), with
    separate due dates for frozen one-off obligations. Totals are cent-exact
    sums (no re-round).
    `balance` = soll_total - ist_total (>0 = owes; <0 = overpaid/credit).
    """

    open_items: tuple[OpenItem, ...]
    soll_total: Decimal
    ist_total: Decimal
    balance: Decimal
    due_balance: Decimal | None = None
    not_yet_due_balance: Decimal | None = None


def _ist_key(p: PaymentInput) -> tuple[int | None, int | None, str]:
    return (p.period_year, p.period_month, p.payment_type)


def account_statement(
    expected_charges: list[ExpectedCharge],
    payments: list[PaymentInput],
    *, as_of: date | None = None, include_payment_dates: bool = False,
) -> MietkontoResult:
    """Soll vs Ist per (period, type) -> offene Posten + Saldo (m6 section 3.1).

    Ist counts ONLY booked, non-reversed, non-reversal payments (Storno
    Variante A, section 6.6) and uses the SIGNED amount (an OUT reduces Ist --
    e.g. a Guthaben-Rueckzahlung). Grouping key: (period_year, period_month,
    payment_type). A payment with period_month=None groups under that type for
    the (year, None) bucket (a Pauschalzahlung for the whole period).

    Dated one-off charges retain their due dates; each monthly payment bucket
    is applied once, oldest due first. Recurring monthly charges are unchanged.
    Deterministic order: by (year, month-or-0, payment_type, due_date).
    With as_of, partition the signed open amounts by their existing due dates,
    inclusive of the cutoff. Undated buckets fall back to their period start;
    bookings without a period are due. No payment allocation or total changes.
    """
    # Aggregate Soll per key.
    soll_by_key: dict[tuple[int | None, int | None, str], Decimal] = {}
    dated_soll: dict[tuple[int | None, int | None, str], dict[date, Decimal]] = {}
    for c in expected_charges:
        key = (c.period_year, c.period_month, c.payment_type)
        soll_by_key[key] = soll_by_key.get(key, Decimal("0.00")) + _money(c.amount)
        if c.due_date is not None:
            dates = dated_soll.setdefault(key, {})
            dates[c.due_date] = dates.get(c.due_date, Decimal("0.00")) + _money(c.amount)

    # Dunning can retain the dates of the same allocations for interest.
    # Neither the allocation order nor the resulting open balances change.
    dated_ist: dict[tuple[int | None, int | None, str], dict[date, Decimal]] = {}
    # Aggregate Ist per key (booked, not reversed, not a reversal-of -> net).
    ist_by_key: dict[tuple[int | None, int | None, str], Decimal] = {}
    for p in payments:
        if not p.is_booked or p.is_reversed or p.is_reversal:
            continue
        key = _ist_key(p)
        if include_payment_dates and p.value_date is not None:
            daily = dated_ist.setdefault(key, {})
            daily[p.value_date] = daily.get(p.value_date, Decimal("0")) + p.signed_amount
        ist_by_key[key] = ist_by_key.get(key, Decimal("0.00")) + p.signed_amount

    all_keys = set(soll_by_key) | set(ist_by_key)
    items: list[OpenItem] = []
    soll_total = Decimal("0.00")
    ist_total = Decimal("0.00")
    for key in sorted(all_keys, key=lambda k: (k[0] or 0, k[1] or 0, k[2])):
        soll = _money(soll_by_key.get(key, Decimal("0.00")))
        ist = _money(ist_by_key.get(key, Decimal("0.00")))
        soll_total += soll
        ist_total += ist
        dates = dated_soll.get(key)
        changes = sorted(dated_ist.get(key, {}).items())
        if dates:
            # Payments have a month, not a levy ID: consume that month's money
            # oldest due first, exactly once. Future levies must not accelerate
            # arrears, and surplus credit remains on the last dated position.
            remaining = ist
            dated_amounts = sorted(dates.items())
            undated = soll - sum(dates.values(), Decimal("0.00"))
            if undated:
                fallback = date(key[0], key[1] or 1, 1)
                dates = {**dates, fallback: dates.get(fallback, Decimal("0.00")) + undated}
                dated_amounts = sorted(dates.items())
            for index, (due, amount) in enumerate(dated_amounts):
                last = index == len(dated_amounts) - 1
                paid = remaining if last else min(remaining, amount)
                remaining -= paid
                # Apply the same oldest-due-first allocation to each day's
                # cumulative Ist. Pass only the unallocated deltas to the next row.
                prior = paid - sum((delta for _, delta in changes), Decimal("0")) + remaining
                allocated = prior if last else min(prior, amount)
                paid_changes = []
                remainder_changes = []
                for day, delta in changes:
                    prior += delta
                    next_allocated = prior if last else min(prior, amount)
                    change = next_allocated - allocated
                    if change:
                        paid_changes.append((day, change))
                    if delta != change:
                        remainder_changes.append((day, delta - change))
                    allocated = next_allocated
                changes = remainder_changes
                items.append(OpenItem(period_year=key[0], period_month=key[1], payment_type=key[2],
                    soll=amount, ist=paid, open_amount=_money(amount - paid), due_date=due,
                    payment_changes=tuple(paid_changes)))
        else:
            items.append(OpenItem(period_year=key[0], period_month=key[1], payment_type=key[2],
                soll=soll, ist=ist, open_amount=_money(soll - ist), payment_changes=tuple(changes)))
    due_balance = None
    not_yet_due_balance = None
    if as_of is not None:
        items = [replace(item, is_due=item.period_year is None or (item.due_date or
            date(item.period_year, item.period_month or 1, 1)) <= as_of) for item in items]
        due_balance = sum((item.open_amount for item in items if item.is_due), Decimal('0.00'))
        not_yet_due_balance = _money(soll_total - ist_total - due_balance)
    return MietkontoResult(
        open_items=tuple(items),
        soll_total=_money(soll_total),
        ist_total=_money(ist_total),
        balance=_money(soll_total - ist_total),
        due_balance=due_balance,
        not_yet_due_balance=not_yet_due_balance,
    )


# ===========================================================================
# Abrechnungs-Anbindung -- the DERIVED advance-payment sum (m6 section 6.1).
# ===========================================================================
@dataclass(frozen=True)
class AdvanceFilter:
    """The disjoint filter that selects payments for ONE abrechnungs size.

    types: the payment_type(s) that feed this size (e.g. {NK_VORAUSZAHLUNG} for
    the BK advance, {HAUSGELD_VORSCHUSS} for the WEG paid_advances). Keeping
    them disjoint per size is what makes double-counting impossible (I6-C).
    """

    types: frozenset[str]
    year: int
    lease_agreement_id: uuid.UUID | None = None
    owner_profile_id: uuid.UUID | None = None

    def matches(self, payment: PaymentInput) -> bool:
        return (
            payment.is_booked and not payment.is_reversed and not payment.is_reversal
            and payment.payment_type in self.types and payment.period_year == self.year
            and (self.lease_agreement_id is None or payment.lease_agreement_id == self.lease_agreement_id)
            and (self.owner_profile_id is None or payment.owner_profile_id == self.owner_profile_id)
        )


def _sum_advances(payments: list[PaymentInput]) -> Decimal:
    return _money(sum((
        p.signed_amount if p.payment_type in _TENANT_ADVANCE_TYPES else p.amount
        for p in payments
    ), Decimal("0.00")))


def derive_advance_total(
    payments: list[PaymentInput],
    filt: AdvanceFilter,
) -> Decimal:
    """Σ amount of booked, non-reversed payments matching `filt` (m6 §6.1).

    Storno Variante A (section 6.6): a payment counts iff it is booked, NOT
    reversed (reversed_by_id IS NULL) and NOT itself a reversal row
    (reverses_id IS NULL). A reversed original + its Storno therefore BOTH drop
    out -> net null, no sign juggling.

    The filter is period- and addressee-exact (section 6.2/6.3): period_year
    must equal filt.year; if a lease/owner is given, it must match. This is the
    SAME value the MANUAL path would carry, so feeding it through the unchanged
    M3/M4 engine yields a bit-identical result (m6 §6.7 / I6-E).

    Tenant advance refunds reduce the corresponding NK or HK total. Hausgeld
    uses gross amounts here, with direction applied separately by its period
    derivation.
    """
    return _sum_advances([p for p in payments if filt.matches(p)])


@dataclass(frozen=True)
class AdvanceCredit:
    """Frozen credit for one assigned year/month bucket."""

    year: int
    month: int | None
    period_start: date
    period_end: date
    amount: Decimal


def tenant_refund_buckets(
    payments: list[PaymentInput], frozen: list[AdvanceCredit],
) -> set[tuple[int, int | None]]:
    """Find signed buckets in one scoped input, including frozen refund effects."""
    return {
        (p.period_year, p.period_month) for p in payments
        if p.payment_type in _TENANT_ADVANCE_TYPES and p.direction == "OUT" and p.period_year is not None
        and p.is_booked and not p.is_reversal and not p.is_reversed
    } | {(credit.year, credit.month) for credit in frozen if credit.amount < 0}


def derive_advance_credits_for_period(
    payments: list[PaymentInput],
    filt: AdvanceFilter,
    period_start: date,
    period_end: date,
    *, lease_start: date | None = None, lease_end: date | None = None,
    finalized_credits: tuple[AdvanceCredit, ...] = (),
) -> tuple[AdvanceCredit, ...]:
    """Select stable month shares, substituting already frozen credits after edits.

    Replace the current expected shares of other finalized windows with what
    their snapshots actually credited. Open drafts may share an outstanding
    adjustment until one finalizes; the final window receives the remainder.
    With fixed lease dates and payments each substitution is zero.
    """
    if not period_start.year <= filt.year <= period_end.year:
        return ()
    active = [p for p in payments if filt.matches(p)]
    frozen = [credit for credit in finalized_credits if credit.year == filt.year
              and (credit.period_end < period_start or credit.period_start > period_end)]
    signed_buckets = tenant_refund_buckets(active, frozen) if filt.types & _TENANT_ADVANCE_TYPES else set()
    credits = []
    for month in [*range(1, 13), None]:
        total = _sum_advances([p for p in active if p.period_month == month])
        signed = (filt.year, month) in signed_buckets
        if not total and not signed:
            continue

        def share(start, end):
            args = (total, filt.year, start, end)
            span = {"lease_start": lease_start, "lease_end": lease_end}
            if month is None:
                return annual_advance_share_for_period(*args, **span)
            return monthly_advance_share_for_period(total, filt.year, month, start, end, **span)

        amount = share(period_start, period_end)
        for previous in frozen:
            if previous.month == month:
                amount += share(previous.period_start, previous.period_end) - previous.amount
        # A refund can exhaust a bucket and still leave a frozen credit to offset.
        if total or amount:
            credit = amount if signed else max(Decimal("0.00"), amount)
            credits.append(AdvanceCredit(filt.year, month, period_start, period_end, credit))
    return tuple(credits)


def derive_advance_total_for_period(
    payments: list[PaymentInput], filt: AdvanceFilter, period_start: date, period_end: date,
    *, lease_start: date | None = None, lease_end: date | None = None,
    finalized_credits: tuple[AdvanceCredit, ...] = (),
) -> Decimal:
    """Credit booked advances over occupied lease months (all months for Hausgeld)."""
    return sum((c.amount for c in derive_advance_credits_for_period(payments, filt, period_start, period_end,
        lease_start=lease_start, lease_end=lease_end, finalized_credits=finalized_credits)), Decimal("0.00"))


def uncredited_advance_total(
    payments: list[PaymentInput], filt: AdvanceFilter, lease_start: date, lease_end: date | None,
    *, period_start: date | None = None, period_end: date | None = None,
) -> Decimal:
    """Place outside-lease bookings on the statement containing that lease boundary."""
    year_start, year_end = date(filt.year, 1, 1), date(filt.year, 12, 31)
    start, end = period_start or date.min, period_end or date.max
    result = Decimal("0.00")
    for month in [*range(1, 13), None]:
        bucket = [p for p in payments if p.period_month == month]
        excluded = derive_advance_total(bucket, filt) - derive_advance_total_for_period(
            bucket, filt, year_start, year_end, lease_start=lease_start, lease_end=lease_end)
        if not excluded:
            continue
        _, bucket_end = _month_bounds(filt.year, month) if month is not None else (year_start, year_end)
        boundary = lease_start if bucket_end < lease_start else lease_end
        if boundary is not None and start <= boundary <= end:
            result += excluded
    return result
