"""The set-off of a surplus reads the complete calendar years a period touches; the period only selects what is shown (#806).

Owner decision of 2026-11-23: a surplus is set off within the calendar year and within the position type, the oldest
due open position first. The statement of a lease therefore reads every position of the years the period touches, a
month the period starts or ends in in full, and lists only the positions of the months the period names, with the
Soll the period itself has (day-accurate in an edge month, as every statement shows it). Nothing is set off across the
year end or between rent and advances. Every expected value is derived by hand in the comment of its test.

`read` does what `PaymentService.tenant_statement_in_current_scope` does: the Soll of the lease for the complete years,
the payments assigned to those years (a payment without a year to the year of its value date), the period and its own
charges for the listing. Rent and advance are due on the third working day of the month; the reference date, 15 January
2026, is after all of 2025 and after the due date of January 2026.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.engine import mietkonto
from app.engine.dunning import assemble_dunning, find_overdue_items
from app.engine.mietkonto import (
    ExpectedCharge,
    LeaseChargeInput,
    PaymentInput,
    PT_KALTMIETE,
    PT_NK_VORAUSZAHLUNG,
    account_statement,
    tenant_expected_charges,
)

pytestmark = pytest.mark.no_db

LEASE = uuid.UUID(int=806)
REFERENCE = date(2026, 1, 15)
YEAR_2025 = (date(2025, 1, 1), date(2025, 12, 31))
RATES = ((date(2025, 1, 1), Decimal('2.00')),)  # base rate 2.00 % + 5 points = 7.00 % a year


def lease(rent, advance='0', start=date(2025, 1, 1), end=None):
    return LeaseChargeInput(LEASE, start, end, Decimal(rent), Decimal(advance))


def paid(month, amount, *, year=2025, kind=PT_KALTMIETE, on=None, **flags):
    return PaymentInput(uuid.uuid4(), kind, 'IN', Decimal(amount), year, month, lease_agreement_id=LEASE,
                        value_date=on, **flags)


def read(contract, period, payments, reference=REFERENCE, **kwargs):
    """The statement of a period, read as the service reads it: the complete years, only the period shown."""
    first, last = period[0].year, period[1].year
    charges = tenant_expected_charges(contract, date(first, 1, 1), date(last, 12, 31))
    counted = [p for p in payments
               if first <= (p.period_year or (p.value_date.year if p.value_date else first)) <= last]
    return account_statement(charges, counted, as_of=reference, set_off_surplus=True, period=period,
                             period_charges=tenant_expected_charges(contract, *period), **kwargs)


def overdue(result, reference=REFERENCE):
    return find_overdue_items(list(result.open_items), reference, current_year=reference.year)


def demanded(result, reference=REFERENCE):
    return sum((item.open_amount for item in overdue(result, reference)), Decimal('0.00'))


def month_of(result, month, kind=PT_KALTMIETE):
    return next(item for item in result.open_items if (item.period_month, item.payment_type) == (month, kind))


def rent_rows(result):
    """(year, month, Soll, Ist, open) of the rent positions listed (a lease without an advance lists 0,00 advances)."""
    return [(i.period_year, i.period_month, i.soll, i.ist, i.open_amount) for i in result.open_items
            if i.payment_type == PT_KALTMIETE]


def agrees(result):
    """The rows, the balance and the due balance of a statement tell one story."""
    rows = sum((item.open_amount for item in result.open_items), Decimal('0.00'))
    return rows == result.balance == result.due_balance + result.not_yet_due_balance == result.soll_total - result.ist_total


# --- the positions the statement reads --------------------------------------------------------------------------------

def test_the_types_that_take_part_are_the_types_the_soll_is_built_for():
    # One source of truth: a type that `tenant_expected_charges` charges takes part, and no other one does.
    heating = LeaseChargeInput(LEASE, date(2025, 1, 1), None, Decimal('500'), Decimal('100'),
                               heating_advance_payment=Decimal('50'))
    charged = {charge.payment_type for charge in tenant_expected_charges(heating, *YEAR_2025)}
    assert charged == mietkonto.TENANT_SOLL_TYPES


def test_a_period_that_ends_inside_a_month_reads_that_month_in_full():
    # Rent 500, January unpaid, February paid 500. Period 1 January to 14 February, read on 15 April. For the set-off
    # February's rent is 500 for the whole month, whatever day the period ends on: the 500 paid are exactly its rent, no
    # surplus, January's 500 stay open and are demanded. (Reading 14 of 28 days = 250 made 250 of the payment a surplus
    # that settled January: 250 demanded.) The row of February shows what a statement of that period always showed: its
    # 14 days, 14 x 500 / 28 = 250, against the 500 paid. March and April are not part of the period and not listed.
    result = read(lease('500'), (date(2025, 1, 1), date(2025, 2, 14)), [paid(2, '500.00')], reference=date(2025, 4, 15))
    assert rent_rows(result) == [
        (2025, 1, Decimal('500.00'), Decimal('0.00'), Decimal('500.00')),
        (2025, 2, Decimal('250.00'), Decimal('500.00'), Decimal('-250.00'))]
    assert demanded(result, date(2025, 4, 15)) == Decimal('500.00')
    assert (result.soll_total, result.ist_total, result.balance) == (Decimal('750.00'), Decimal('500.00'), Decimal('250.00'))
    assert agrees(result)


def test_a_period_that_starts_inside_a_month_reads_that_month_in_full():
    # Rent 500, January and February paid, March unpaid. Period 20 February to 31 March, read on 15 April. For the set-off
    # February's rent is 500: no surplus, March's 500 are demanded. (Reading the 9 days of 28 = 160.71 of the period made
    # a surplus of 339.29 that settled March: 160.71 demanded.) February's row shows the 9 days, 9 x 500 / 28 = 160.71.
    payments = [paid(1, '500.00'), paid(2, '500.00')]
    result = read(lease('500'), (date(2025, 2, 20), date(2025, 3, 31)), payments, reference=date(2025, 4, 15))
    assert month_of(result, 2).soll == Decimal('160.71')
    assert demanded(result, date(2025, 4, 15)) == Decimal('500.00')
    assert agrees(result)


def test_the_deadline_monitors_period_up_to_the_reference_date_does_not_shorten_its_last_month():
    # Rent 600 from January; January paid 500 (100 short), February to April paid 600 each. The deadline monitor reads
    # from the lease start to its reference date, 15 April. For the set-off April's rent is 600 for the whole month and
    # paid exactly: one overdue position, January's 100. (Reading 15 of 30 days = 300 made 300 of April's payment a
    # surplus that settled January: nothing overdue.) April's row shows its 15 days, 300, against the 600 paid.
    payments = [paid(1, '500.00'), paid(2, '600.00'), paid(3, '600.00'), paid(4, '600.00')]
    reference = date(2025, 4, 15)
    result = read(lease('600'), (date(2025, 1, 1), reference), payments, reference=reference)
    assert [(i.period_month, i.open_amount) for i in overdue(result, reference)] == [(1, Decimal('100.00'))]
    assert month_of(result, 4).soll == Decimal('300.00')


def test_a_month_the_lease_has_ended_in_before_the_period_starts_is_shown_only_when_money_was_paid_on_it():
    # The lease ends on 10 February; rent 500: February is 10 x 500 / 28 = 178.57 for the set-off. A period from 20 February
    # has no charge in February, so the row of February shows no Soll: not at all when nothing was paid on it, as a credit
    # of what was paid when something was (178.57 paid: open -178.57).
    contract = lease('500', end=date(2025, 2, 10))
    period = (date(2025, 2, 20), date(2025, 3, 31))
    assert rent_rows(read(contract, period, [])) == []
    paid_february = read(contract, period, [paid(2, '178.57')])
    assert rent_rows(paid_february) == [(2025, 2, Decimal('0.00'), Decimal('178.57'), Decimal('-178.57'))]


def test_the_period_shows_a_joint_advance_as_the_statement_reads_it():
    # April owes an operating advance of 150 and a heating advance of 90; the tenant paid 240 as one joint advance. The
    # statement reads both as one joint advance of 240, in the year and in the period's own Soll: one row, 240 owed and
    # 240 paid, nothing open. (The period's charges were left as two components: 150 shown against 240 paid.)
    contract = LeaseChargeInput(LEASE, date(2025, 1, 1), None, Decimal('0'), Decimal('150'),
                                heating_advance_payment=Decimal('90'))
    joint = paid(4, '240.00', kind=PT_NK_VORAUSZAHLUNG, combined_advance=True)
    result = read(contract, (date(2025, 4, 1), date(2025, 4, 30)), [joint])
    assert [(i.period_month, i.payment_type, i.soll, i.ist, i.open_amount) for i in result.open_items
            if i.payment_type != PT_KALTMIETE] == [
        (4, PT_NK_VORAUSZAHLUNG, Decimal('240.00'), Decimal('240.00'), Decimal('0.00'))]
    assert agrees(result)


# --- the whole year, whatever part of it is asked for -----------------------------------------------------------------

def test_notices_over_parts_of_a_year_add_up_to_the_notice_over_the_year():
    # 500 rent + 100 advance a month; 1000 paid as rent for June 2025; reference 15 January 2026.
    # June's rent needs 500: 500 are left and settle the oldest due open RENT of 2025, January's (due 6 January).
    # The advance is no rent, so January's advance (100) stays open.
    #   January to May: January's rent is settled (0), January's advance 100, February to May 4 x 600 = 2400: 2500.
    #   June to December: June's rent is paid (its own 500), June's advance 100, July to December 6 x 600 = 3600: 3700.
    #   The year: 12 x 600 = 7200 owed - 1000 paid = 6200 = 2500 + 3700.
    contract, june = lease('500', '100'), [paid(6, '1000.00')]
    first = read(contract, (date(2025, 1, 1), date(2025, 5, 31)), june)
    second = read(contract, (date(2025, 6, 1), date(2025, 12, 31)), june)
    year = read(contract, YEAR_2025, june)
    assert (demanded(first), demanded(second), demanded(year)) == (Decimal('2500.00'), Decimal('3700.00'), Decimal('6200.00'))
    # Rows, Soll, Ist and the balance of each part tell one story: January to May owe 5 x 600 = 3000 and hold the 500
    # that moved to January (2500 open); June to December owe 7 x 600 = 4200 and hold June's own 500 (3700 open).
    assert (first.soll_total, first.ist_total, first.balance, first.due_balance) == (
        Decimal('3000.00'), Decimal('500.00'), Decimal('2500.00'), Decimal('2500.00'))
    assert (second.soll_total, second.ist_total, second.balance, second.due_balance) == (
        Decimal('4200.00'), Decimal('500.00'), Decimal('3700.00'), Decimal('3700.00'))
    assert all(agrees(result) for result in (first, second, year))
    assert (year.soll_total, year.ist_total, year.balance) == (Decimal('7200.00'), Decimal('1000.00'), Decimal('6200.00'))


def test_notices_over_parts_of_a_year_bear_the_interest_of_the_notice_over_the_year():
    # Rent 600 due on the 3rd of January to June 2025. June was paid 1100 on 20 June (500 over its 600): they settle
    # January's rent, the oldest due open one. January is then open with 100 from 20 June; February to May are open
    # with 600. Interest 7.00 % a year (2.00 % base + 5 points) as of 30 June, per position:
    #   January: 600 from 4 January to 19 June (28 + 28 + 31 + 30 + 31 + 19 = 167 days): 600 x 7 x 167 / 36500 = 19.2164
    #            100 from 20 June to 30 June (11 days):                                   100 x 7 x 11 / 36500 = 0.2110
    #            together 19.4274, rounded 19.43
    #   February: 4 February to 30 June, 25 + 31 + 30 + 31 + 30 = 147 days: 600 x 7 x 147 / 36500 = 16.9151 = 16.92
    #   March: 119 days (28 + 30 + 31 + 30): 13.6932 = 13.69; April: 88 days (27 + 31 + 30): 10.1260 = 10.13
    #   May: 58 days (28 + 30): 6.6740 = 6.67
    # January to March: 100 + 600 + 600 = 1300 and 19.43 + 16.92 + 13.69 = 50.04; April to June: 1200 and
    # 10.13 + 6.67 = 16.80; the whole half year: 2500 and 66.84 = 50.04 + 16.80.
    reference = date(2025, 6, 30)
    charges = [ExpectedCharge(2025, m, PT_KALTMIETE, Decimal('600'), lease_agreement_id=LEASE,
                              due_date=date(2025, m, 3)) for m in range(1, 7)]
    payments = [paid(6, '1100.00', on=date(2025, 6, 20))]

    def notice(period):
        result = account_statement(charges, payments, as_of=reference, include_payment_dates=True,
                                   set_off_surplus=True, period=period)
        return assemble_dunning(overdue(result, reference), 0, reference, rate_periods=RATES)

    first, second = notice((date(2025, 1, 1), date(2025, 3, 31))), notice((date(2025, 4, 1), date(2025, 6, 30)))
    whole = notice((date(2025, 1, 1), date(2025, 6, 30)))
    assert [(n.principal_amount, n.interest_amount) for n in (first, second, whole)] == [
        (Decimal('1300.00'), Decimal('50.04')), (Decimal('1200.00'), Decimal('16.80')),
        (Decimal('2500.00'), Decimal('66.84'))]


def test_a_period_inside_a_year_lists_positions_that_add_up_to_its_balance():
    # Rent 500 from January 2025; January to May unpaid, June paid 1000 (500 over). A view of January and February: the
    # 500 of June settle January (the oldest due open rent of 2025). Rows: January 500 owed, 500 held, open 0;
    # February 500 open. Soll 1000, Ist 500, balance 500 = the rows = the due balance (what the old reading showed
    # for the same view: Ist 0 and a credit of 500 that was gone).
    result = read(lease('500'), (date(2025, 1, 1), date(2025, 2, 28)), [paid(6, '1000.00')])
    assert rent_rows(result) == [
        (2025, 1, Decimal('500.00'), Decimal('500.00'), Decimal('0.00')),
        (2025, 2, Decimal('500.00'), Decimal('0.00'), Decimal('500.00'))]
    assert (result.soll_total, result.ist_total, result.balance, result.due_balance) == (
        Decimal('1000.00'), Decimal('500.00'), Decimal('500.00'), Decimal('500.00'))
    assert agrees(result)


def test_a_year_depends_on_no_month_outside_that_year():
    # Rent 500 since 2019, nothing paid before 2025, all of 2025 paid in full except June, which was paid 1000: the 500
    # over find no open rent in 2025 and stay a credit of June, whatever 2019 to 2024 owe. The 2025 view is the same
    # as that of a lease that starts in 2025: June -500, the other months 0, Saldo and Rueckstand -500.
    payments = [paid(m, '1000.00' if m == 6 else '500.00') for m in range(1, 13)]
    old = read(lease('500', start=date(2019, 1, 1)), YEAR_2025, payments)
    new = read(lease('500'), YEAR_2025, payments)
    assert old == new
    assert [i.open_amount for i in old.open_items if i.open_amount] == [Decimal('-500.00')]
    assert month_of(old, 6).open_amount == Decimal('-500.00')
    assert (old.balance, old.due_balance, old.not_yet_due_balance) == (Decimal('-500.00'), Decimal('-500.00'), Decimal('0.00'))
    assert agrees(old)


# --- nothing is set off across the year end ---------------------------------------------------------------------------

def test_a_surplus_of_the_next_year_does_not_settle_the_rent_that_the_old_year_still_owes():
    # The lease starts on 1 December 2025 at 500 rent. December is unpaid; 1000 were paid for January 2026 (500 owed).
    # The 500 over are not set off across the year end: December stays open, the credit stays on January. The lease owes
    # 1000 - 1000 = 0 in all, yet a notice over 2025 demands December's 500 (the manager rebooks such a payment).
    contract, january = lease('500', start=date(2025, 12, 1)), [paid(1, '1000.00', year=2026)]
    old_year = read(contract, YEAR_2025, january)
    assert (old_year.balance, old_year.due_balance, demanded(old_year)) == (Decimal('500.00'), Decimal('500.00'), Decimal('500.00'))
    new_year = read(contract, (date(2026, 1, 1), date(2026, 1, 31)), january)
    # January was paid 1000 for 500 owed: its row keeps the credit of 500, nothing of 2026 is open to settle.
    assert (month_of(new_year, 1).ist, month_of(new_year, 1).open_amount, new_year.balance, demanded(new_year)) == (
        Decimal('1000.00'), Decimal('-500.00'), Decimal('-500.00'), Decimal('0.00'))
    # A period that names both years lists both rows and demands December's 500 although the balance is 0.
    both = read(contract, (date(2025, 12, 1), date(2026, 1, 31)), january)
    assert (both.balance, both.due_balance, demanded(both)) == (Decimal('0.00'), Decimal('0.00'), Decimal('500.00'))
    assert agrees(both)


def test_a_surplus_larger_than_the_debt_of_its_year_stays_a_credit_and_the_old_year_stays_open():
    # Rent 500 from January 2025; 2025 is paid in full except December (500 open). January 2026 was paid 2000 (1500
    # over its 500). December of 2025 is not settled by money of 2026, and the 1500 stay a credit: nothing is open in
    # 2026 to take them. Owed 13 x 500 = 6500, paid 11 x 500 + 2000 = 7500: balance -1000 = 500 - 1500.
    payments = [paid(m, '500.00') for m in range(1, 12)] + [paid(1, '2000.00', year=2026)]
    result = read(lease('500'), (date(2025, 1, 1), date(2026, 1, 31)), payments)
    assert [(i.period_year, i.period_month, i.open_amount) for i in result.open_items if i.open_amount] == [
        (2025, 12, Decimal('500.00')), (2026, 1, Decimal('-1500.00'))]
    assert (result.balance, demanded(result)) == (Decimal('-1000.00'), Decimal('500.00'))
    assert agrees(result)


# --- the set-off needs a position of its own type, at the reference date -----------------------------------------------

def test_a_payment_made_ahead_for_a_later_month_meets_its_own_soll_and_is_no_surplus():
    # Rent 600; January unpaid, February and March paid; April's rent was paid ahead on 28 March (600 for 600 owed).
    # Read on 31 March for January to March: April belongs to the year and is charged, so the April payment is exactly
    # April's rent and January's 600 stay open and are demanded.
    payments = [paid(2, '600.00'), paid(3, '600.00'), paid(4, '600.00', on=date(2025, 3, 28))]
    result = read(lease('600'), (date(2025, 1, 1), date(2025, 3, 31)), payments, reference=date(2025, 3, 31))
    assert demanded(result, date(2025, 3, 31)) == Decimal('600.00')
    assert {item.period_month for item in result.open_items} == {1, 2, 3}  # April is not part of the period


def test_a_position_that_is_not_due_at_the_reference_date_is_not_settled_by_the_surplus_of_another_period():
    # Rent 600; June paid 3000 ahead (2400 over); reference 31 March. Due: January to March (1800), April to June not
    # yet. The 2400 settle the 1800 of January to March; the other 600 stay a credit (June's), April and May are not
    # settled early. A notice for April to June has nothing overdue.
    reference = date(2025, 3, 31)
    april_to_june = read(lease('600'), (date(2025, 4, 1), date(2025, 6, 30)), [paid(6, '3000.00')], reference=reference)
    assert [(i.period_month, i.open_amount) for i in april_to_june.open_items if i.payment_type == PT_KALTMIETE] == [
        (4, Decimal('600.00')), (5, Decimal('600.00')), (6, Decimal('-600.00'))]
    # Nothing of April to June is due on 31 March: 600 + 600 - 600 = 600 not yet due.
    assert (april_to_june.due_balance, april_to_june.not_yet_due_balance) == (Decimal('0.00'), Decimal('600.00'))
    assert agrees(april_to_june)
    assert demanded(april_to_june, reference) == Decimal('0.00')


def test_a_surplus_of_rent_settles_no_advance_of_the_year():
    # 500 rent + 100 advance a month, 2025. December's advance is unpaid, everything else is paid, June's rent twice over
    # (1000 for 500). The 500 over find no open rent of 2025 and stay a credit of June's rent; an advance is no rent, so
    # December's advance stays open with its 100 although the lease owes 12 x 600 = 7200 and received
    # 13 x 500 + 11 x 100 = 7600 (balance -400).
    payments = [paid(m, '500.00') for m in range(1, 13)] + [paid(6, '500.00')]
    payments += [paid(m, '100.00', kind=PT_NK_VORAUSZAHLUNG) for m in range(1, 12)]
    result = read(lease('500', '100'), YEAR_2025, payments)
    assert [(i.period_month, i.payment_type, i.open_amount) for i in result.open_items if i.open_amount] == [
        (6, PT_KALTMIETE, Decimal('-500.00')), (12, PT_NK_VORAUSZAHLUNG, Decimal('100.00'))]
    assert demanded(result) == Decimal('100.00')
    assert result.balance == Decimal('-400.00')


# --- other scopes ----------------------------------------------------------------------------------------------------

def test_other_scope_money_is_counted_for_the_positions_shown_only():
    # June: 100 paid into this scope's account, 900 into another scope's account (500 owed): 500 over settle January.
    # The note of the other scope's money belongs to the period that holds June.
    contract = lease('500')
    payments = [paid(6, '100.00'), paid(6, '900.00', other_scope=True)]
    january_to_may = read(contract, (date(2025, 1, 1), date(2025, 5, 31)), payments)
    june_to_december = read(contract, (date(2025, 6, 1), date(2025, 12, 31)), payments)
    assert (january_to_may.other_scope_ist, june_to_december.other_scope_ist) == (None, Decimal('900.00'))
    assert demanded(january_to_may) == Decimal('2000.00')  # 5 x 500 - the 500 that settled January


# --- a payment without an assigned year -------------------------------------------------------------------------------

def unassigned(amount, on):
    return PaymentInput(uuid.uuid4(), PT_KALTMIETE, 'IN', Decimal(amount), None, None, lease_agreement_id=LEASE,
                        value_date=on)


def test_a_payment_without_a_year_counts_in_the_year_of_its_value_date():
    # Rent 500 from January 2025. 500 without an assigned year were paid on 2 June 2025: they count in 2025 and settle
    # January, the oldest due open rent of 2025. A view of January to May: 5 x 500 - 500 = 2000 demanded; the position
    # of the payment itself holds nothing any more.
    result = read(lease('500'), (date(2025, 1, 1), date(2025, 5, 31)), [unassigned('500.00', date(2025, 6, 2))])
    assert demanded(result) == Decimal('2000.00')
    assert [(i.ist, i.open_amount) for i in result.open_items if i.period_year is None] == [(Decimal('0.00'), Decimal('0.00'))]
    assert agrees(result)


def test_a_payment_without_a_year_does_not_cross_the_year_end():
    # The lease starts on 1 December 2025 at 500 rent: December and January 2026 are unpaid. 700 without a year were paid
    # on 20 January 2026: they count in 2026 and settle January (500); the other 200 stay a credit. December of 2025
    # stays open and is demanded: 500. Soll 1000, Ist 700, balance 300 = 500 + 0 - 200.
    reference = date(2026, 1, 31)
    result = read(lease('500', start=date(2025, 12, 1)), (date(2025, 12, 1), date(2026, 1, 31)),
                  [unassigned('700.00', date(2026, 1, 20))], reference=reference)
    assert [(i.period_year, i.period_month, i.open_amount) for i in result.open_items if i.payment_type == PT_KALTMIETE] == [
        (None, None, Decimal('-200.00')), (2025, 12, Decimal('500.00')), (2026, 1, Decimal('0.00'))]
    assert (result.balance, demanded(result, reference)) == (Decimal('300.00'), Decimal('500.00'))


def test_a_payment_without_a_year_and_without_a_value_date_counts_in_no_year():
    result = read(lease('500'), (date(2025, 1, 1), date(2025, 5, 31)), [unassigned('500.00', None)])
    assert demanded(result) == Decimal('2500.00')


# A booking without a year counts in the year of its value date for the set-off, and a period lists it only when the period
# holds its value date (as every statement before the set-off read it); bookings of different years or of one period and
# of another are different positions. So split windows add up to the whole, whatever the year of the booking.

def refunded(amount, on):
    return PaymentInput(uuid.uuid4(), PT_KALTMIETE, 'OUT', Decimal(amount), None, None, lease_agreement_id=LEASE,
                        value_date=on)


def without_year(result):
    return [(i.ist, i.open_amount) for i in result.open_items if i.period_year is None]


JANUARY_TO_MAY = (date(2025, 1, 1), date(2025, 5, 31))
JUNE_TO_DECEMBER = (date(2025, 6, 1), date(2025, 12, 31))


def test_a_refund_without_a_year_is_demanded_only_by_the_period_that_holds_its_value_date():
    # Rent 500 a month in 2025, every month paid, 300 paid back without an assigned year on 10 June 2025, read on 15
    # January 2026. The whole year demands the 300; January to May does not hold 10 June: 0; June to December does: 300.
    # (Listed with every period of its year the two halves demanded 600 together.) The rows of each view add up.
    payments = [paid(month, '500.00') for month in range(1, 13)] + [refunded('300.00', date(2025, 6, 10))]
    views = [read(lease('500'), period, payments) for period in (YEAR_2025, JANUARY_TO_MAY, JUNE_TO_DECEMBER)]
    assert [demanded(view) for view in views] == [Decimal('300.00'), Decimal('0.00'), Decimal('300.00')]
    assert [view.balance for view in views] == [Decimal('300.00'), Decimal('0.00'), Decimal('300.00')]
    assert [without_year(view) for view in views] == [
        [(Decimal('-300.00'), Decimal('300.00'))], [], [(Decimal('-300.00'), Decimal('300.00'))]]
    assert all(agrees(view) for view in views)


def test_an_incoming_payment_without_a_year_is_listed_once_by_its_value_date():
    # The same lease, every month paid, and 300 more received without an assigned year on 10 June 2025: nothing is open
    # in 2025 that they could settle, so they stay a credit of 300 of their own position. That position is listed with
    # the year and with June to December, not with January to May: the two halves list it once.
    payments = [paid(month, '500.00') for month in range(1, 13)] + [unassigned('300.00', date(2025, 6, 10))]
    views = [read(lease('500'), period, payments) for period in (YEAR_2025, JANUARY_TO_MAY, JUNE_TO_DECEMBER)]
    assert [without_year(view) for view in views] == [
        [(Decimal('300.00'), Decimal('-300.00'))], [], [(Decimal('300.00'), Decimal('-300.00'))]]
    assert [view.balance for view in views] == [Decimal('-300.00'), Decimal('0.00'), Decimal('-300.00')]
    assert [demanded(view) for view in views] == [Decimal('0.00')] * 3
    assert all(agrees(view) for view in views)


def test_bookings_without_a_year_of_one_year_but_of_two_periods_are_two_positions():
    # Refunds without a year of 100 on 10 January and 200 on 10 June 2025, every month paid. January to May holds the
    # first only, June to December the second only, the year both as one position (as a statement over the year always
    # showed them): 100, 200 and 300 demanded; the halves add up to the year.
    payments = [paid(month, '500.00') for month in range(1, 13)]
    payments += [refunded('100.00', date(2025, 1, 10)), refunded('200.00', date(2025, 6, 10))]
    views = [read(lease('500'), period, payments) for period in (YEAR_2025, JANUARY_TO_MAY, JUNE_TO_DECEMBER)]
    assert [demanded(view) for view in views] == [Decimal('300.00'), Decimal('100.00'), Decimal('200.00')]
    assert [without_year(view) for view in views] == [
        [(Decimal('-300.00'), Decimal('300.00'))], [(Decimal('-100.00'), Decimal('100.00'))],
        [(Decimal('-200.00'), Decimal('200.00'))]]
    assert all(agrees(view) for view in views)


def test_bookings_without_a_year_of_different_years_are_two_positions():
    # Rent 500 a month in 2025 and 2026, every month paid, and 100 received without an assigned year on 10 June of each
    # year: a credit of 100 per year, two positions of their own (not one of 200). A view of 2026 lists the second only.
    payments = [paid(month, '500.00', year=year) for year in (2025, 2026) for month in range(1, 13)]
    payments += [unassigned('100.00', date(2025, 6, 10)), unassigned('100.00', date(2026, 6, 10))]
    both = read(lease('500'), (date(2025, 1, 1), date(2026, 12, 31)), payments, reference=date(2027, 1, 15))
    second = read(lease('500'), (date(2026, 1, 1), date(2026, 12, 31)), payments, reference=date(2027, 1, 15))
    assert without_year(both) == [(Decimal('100.00'), Decimal('-100.00'))] * 2
    assert without_year(second) == [(Decimal('100.00'), Decimal('-100.00'))]
    assert (both.balance, second.balance) == (Decimal('-200.00'), Decimal('-100.00'))
    assert agrees(both) and agrees(second)


def test_a_refund_without_a_year_of_a_later_year_does_not_touch_the_surplus_of_an_earlier_one():
    # Rent 500 from 2024, every month paid, June 2024 paid 1000: a surplus of 500 that finds no open rent in 2024 and
    # stays a credit of June 2024. 300 are paid back without a year on 10 March 2026. Whatever period is read, the
    # refund is demanded in full (2024 to 2026 and 2026) or not read at all (2024), and the credit of 2024 stays.
    payments = [paid(month, '1000.00' if (year, month) == (2024, 6) else '500.00', year=year)
                for year in (2024, 2025, 2026) for month in range(1, 13)]
    payments.append(refunded('300.00', date(2026, 3, 10)))
    reference = date(2027, 1, 15)
    contract = lease('500', start=date(2024, 1, 1))
    views = [read(contract, (date(first, 1, 1), date(last, 12, 31)), payments, reference=reference)
             for first, last in ((2024, 2026), (2026, 2026), (2024, 2024))]
    assert [demanded(view, reference) for view in views] == [Decimal('300.00'), Decimal('300.00'), Decimal('0.00')]
    june_2024 = [month_of(view, 6).open_amount for view in (views[0], views[2])]
    assert june_2024 == [Decimal('-500.00')] * 2
    assert all(agrees(view) for view in views)


# --- interest --------------------------------------------------------------------------------------------------------

def test_interest_on_a_position_of_the_period_ends_when_the_money_of_another_month_arrived():
    # Rent 600 due on the third of the month; January and February paid, March open, April paid 1000 on 10 April
    # (400 over its rent). A notice for March alone, as of 1 May: the 400 of April settle March (the oldest open),
    # 200 stay open. Interest 7.00 % a year (2.00 % base + 5 points):
    #   600 from 4 March to 9 April (28 + 9 = 37 days): 600 x 7 x 37 / 36500 = 4.2575
    #   200 from 10 April to 1 May (21 + 1 = 22 days):  200 x 7 x 22 / 36500 = 0.8438
    # together 5.1013, rounded 5.10 (the old reading demanded 600 and charged 600 x 7 x 59 / 36500 = 6.79).
    reference = date(2025, 5, 1)
    charges = [ExpectedCharge(2025, m, PT_KALTMIETE, Decimal('600'), lease_agreement_id=LEASE,
                              due_date=date(2025, m, 3)) for m in (1, 2, 3, 4)]
    payments = [paid(1, '600.00'), paid(2, '600.00'), paid(4, '1000.00', on=date(2025, 4, 10))]
    result = account_statement(charges, payments, as_of=reference, include_payment_dates=True, set_off_surplus=True,
                               period=(date(2025, 3, 1), date(2025, 3, 31)))
    notice = assemble_dunning(overdue(result, reference), 0, reference, rate_periods=RATES)
    assert (notice.principal_amount, notice.interest_amount) == (Decimal('200.00'), Decimal('5.10'))
    assert [(s['start'], s['end'], s['principal'], s['interest']) for s in notice.interest_snapshot['items'][0]['segments']] == [
        ('2025-03-04', '2025-04-09', '600.00', '4.26'), ('2025-04-10', '2025-05-01', '200.00', '0.84')]
