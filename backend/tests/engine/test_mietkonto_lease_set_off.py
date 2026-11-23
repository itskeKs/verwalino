"""The set-off of a surplus reads the whole lease; a requested period only selects what is shown (#806).

Owner decision of 2026-11-22: a surplus settles "the oldest due open position of the lease", and the principal of a
notice "never exceeds what the lease owes in total". So every charge of the lease that is due at the reference date
and every payment of it take part, whichever period a statement, an open-items view or a notice asks for; the period
only selects which of the positions left open are shown or demanded. Every expected value is derived by hand in the
comment of its test.

The fixtures use the real Soll of a lease (`tenant_lease_charges`): rent and advance are due on the third working day
of the month and the reference date, 15 January 2026, is after all of 2025 and after the due date of January 2026.
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
    account_statement,
    tenant_expected_charges,
)

pytestmark = pytest.mark.no_db

LEASE = uuid.UUID(int=806)
REFERENCE = date(2026, 1, 15)
YEAR_2025 = (date(2025, 1, 1), date(2025, 12, 31))
RATES = ((date(2025, 1, 1), Decimal('2.00')),)  # base rate 2.00 % + 5 points = 7.00 % a year


def lease(rent, advance='0', start=date(2025, 1, 1)):
    return LeaseChargeInput(LEASE, start, None, Decimal(rent), Decimal(advance))


def paid(month, amount, *, year=2025, kind=PT_KALTMIETE, on=None, **flags):
    return PaymentInput(uuid.uuid4(), kind, 'IN', Decimal(amount), year, month, lease_agreement_id=LEASE,
                        value_date=on, **flags)


def read(contract, period, payments, reference=REFERENCE, **kwargs):
    """The statement of a period, read as the service reads it: the whole lease, only the period shown."""
    months = {(p.period_year, p.period_month) for p in payments if p.period_month is not None}
    charges = mietkonto.tenant_lease_charges(contract, *period, reference, payment_months=months)
    return account_statement(charges, payments, as_of=reference, set_off_surplus=True, period=period, **kwargs)


def demanded(result, reference=REFERENCE):
    overdue = find_overdue_items(list(result.open_items), reference, current_year=reference.year)
    return sum((item.open_amount for item in overdue), Decimal('0.00'))


def month_of(result, month, kind=PT_KALTMIETE):
    return next(item for item in result.open_items if (item.period_month, item.payment_type) == (month, kind))


# --- the Soll the statement reads -------------------------------------------------------------------------------------

def test_the_types_that_take_part_are_the_types_the_soll_is_built_for():
    # One source of truth: a type that `tenant_expected_charges` charges takes part, and no other one does.
    heating = LeaseChargeInput(LEASE, date(2025, 1, 1), None, Decimal('500'), Decimal('100'),
                               heating_advance_payment=Decimal('50'))
    charged = {charge.payment_type for charge in tenant_expected_charges(heating, *YEAR_2025)}
    assert charged == mietkonto.TENANT_SOLL_TYPES


def test_the_lease_charges_are_the_charges_of_the_whole_lease_through_the_month_of_the_reference_date():
    contract = lease('500', '100')
    # A period of whole months, read on 15 July: the lease from its start through July, one charge list.
    assert mietkonto.tenant_lease_charges(contract, date(2025, 5, 1), date(2025, 6, 30), date(2025, 7, 15)) == \
        tenant_expected_charges(contract, date(2025, 1, 1), date(2025, 7, 31))
    # A reference date inside the period adds nothing; a period after the reference date is read as asked.
    assert mietkonto.tenant_lease_charges(contract, date(2025, 5, 1), date(2025, 6, 30), date(2025, 6, 10)) == \
        tenant_expected_charges(contract, date(2025, 1, 1), date(2025, 6, 30))
    assert mietkonto.tenant_lease_charges(contract, date(2025, 5, 1), date(2025, 6, 30), date(2025, 3, 1)) == \
        tenant_expected_charges(contract, date(2025, 1, 1), date(2025, 6, 30))


def test_a_period_that_starts_or_ends_inside_a_month_keeps_the_partial_month_it_always_had():
    contract = lease('930')  # 30 a day in March (31 days), 31 a day in April (30 days)
    charges = mietkonto.tenant_lease_charges(contract, date(2025, 3, 15), date(2025, 4, 14), date(2025, 4, 14))
    rent = {c.period_month: c for c in charges if c.payment_type == PT_KALTMIETE}
    # January and February are whole months before the period; March and April are the period's: 17 days of March
    # (15th to 31st) = 17 x 30 = 510 and 14 days of April (1st to 14th) = 14 x 31 = 434, exactly as a statement of
    # that period always charged them.
    assert [(m, rent[m].days_occupied, rent[m].amount) for m in (1, 2, 3, 4)] == [
        (1, 31, Decimal('930.00')), (2, 28, Decimal('930.00')), (3, 17, Decimal('510.00')), (4, 14, Decimal('434.00'))]
    assert [c for c in charges if c.period_month in (3, 4)] == \
        tenant_expected_charges(contract, date(2025, 3, 15), date(2025, 4, 14))


def test_months_beyond_the_reference_date_that_a_payment_was_assigned_to_are_charged_once_each():
    contract = lease('600')
    charges = mietkonto.tenant_lease_charges(contract, date(2025, 1, 1), date(2025, 3, 31), date(2025, 3, 31),
                                             payment_months=[(2025, 9), (2025, 9), (2025, 2), (2026, 1)])
    rent = [c for c in charges if c.payment_type == PT_KALTMIETE]
    assert [(c.period_year, c.period_month, c.amount) for c in rent] == [
        (2025, 1, Decimal('600.00')), (2025, 2, Decimal('600.00')), (2025, 3, Decimal('600.00')),
        (2025, 9, Decimal('600.00')), (2026, 1, Decimal('600.00'))]


def test_a_period_that_ends_before_it_starts_has_no_charges_and_the_lease_is_not_read_beyond_it():
    assert mietkonto.tenant_lease_charges(lease('500'), date(2025, 5, 1), date(2025, 4, 1), REFERENCE,
                                          payment_months=[(2025, 9)]) == []


# --- the whole lease, whatever period is asked for --------------------------------------------------------------------

def test_two_notices_over_halves_of_the_year_demand_what_one_notice_over_the_year_demands():
    # 500 rent + 100 advance a month; 1000 paid as rent for June 2025; reference 15 January 2026.
    # June's rent needs 500: 500 are left over and settle the oldest due open position of the lease, the rent of January.
    #   January to May: 5 x 600 = 3000, less the January rent settled (500) = 2500 (the old reading demanded 3000).
    #   June to December: June's rent is paid (500 + the other 500 that moved to January), June's advance is open:
    #   7 x 600 = 4200 - 500 paid for June's rent = 3700 (the old reading demanded 3200).
    #   Over the year: 12 x 600 = 7200 - 1000 = 6200 = 2500 + 3700.
    contract, june = lease('500', '100'), [paid(6, '1000.00')]
    first = read(contract, (date(2025, 1, 1), date(2025, 5, 31)), june)
    second = read(contract, (date(2025, 6, 1), date(2025, 12, 31)), june)
    year = read(contract, YEAR_2025, june)
    assert (demanded(first), demanded(second), demanded(year)) == (Decimal('2500.00'), Decimal('3700.00'), Decimal('6200.00'))
    # Soll, Ist and the balance stay what was charged and paid for the months asked for (nothing was paid for January to
    # May: 3000 owed, 0 paid, balance 3000); the due balance adds up the positions left open (2500).
    assert (first.soll_total, first.ist_total, first.balance) == (Decimal('3000.00'), Decimal('0.00'), Decimal('3000.00'))
    assert (first.due_balance, first.not_yet_due_balance) == (Decimal('2500.00'), Decimal('0.00'))
    # June to December: 4200 owed, the 1000 paid for June: balance 3200; 3700 are left open, because 500 of the 1000
    # left June to settle January: June's rent holds its own 500 only, the advance of June is open.
    assert (second.soll_total, second.ist_total, second.balance) == (Decimal('4200.00'), Decimal('1000.00'), Decimal('3200.00'))
    assert (second.due_balance, second.not_yet_due_balance) == (Decimal('3700.00'), Decimal('0.00'))
    assert (month_of(second, 6).ist, month_of(second, 6).open_amount) == (Decimal('500.00'), Decimal('0.00'))


def test_a_surplus_of_the_next_year_settles_the_rent_that_the_old_year_still_owes():
    # The lease starts on 1 December 2025 at 500 rent. December is unpaid; 1000 were paid for January 2026 (500 owed).
    # The 500 over settle December: the lease owes 1000 - 1000 = 0 over both months.
    contract, january = lease('500', start=date(2025, 12, 1)), [paid(1, '1000.00', year=2026)]
    old_year = read(contract, YEAR_2025, january)
    # Nothing was paid for 2025 (balance 500), yet nothing of December is open any more.
    assert (old_year.balance, old_year.due_balance, demanded(old_year)) == (Decimal('500.00'), Decimal('0.00'), Decimal('0.00'))
    assert find_overdue_items(list(old_year.open_items), REFERENCE, current_year=2026) == []
    assert assemble_dunning([], 0, REFERENCE) is None  # nothing to offer a notice for
    new_year = read(contract, (date(2026, 1, 1), date(2026, 1, 31)), january)
    # January was paid 1000 for 500 owed (balance -500); its row holds its own 500, the other 500 left it for December.
    assert (month_of(new_year, 1).ist, new_year.balance, new_year.due_balance, demanded(new_year)) == (
        Decimal('500.00'), Decimal('-500.00'), Decimal('0.00'), Decimal('0.00'))


def test_a_surplus_from_before_the_period_settles_positions_inside_it():
    # Rent 500 from 1 March 2025; March paid 1200 (700 over its rent); reference 15 July 2025: March to July are due.
    # The 700 settle April (500), the oldest due open position, and 200 of May. Period May to June: May 500 - 200 = 300
    # open, June 500: 800 are demanded (the old reading, which saw no surplus in the period, demanded 1000).
    contract, march = lease('500', start=date(2025, 3, 1)), [paid(3, '1200.00')]
    result = read(contract, (date(2025, 5, 1), date(2025, 6, 30)), march, reference=date(2025, 7, 15))
    assert (month_of(result, 5).open_amount, month_of(result, 6).open_amount) == (Decimal('300.00'), Decimal('500.00'))
    assert demanded(result, date(2025, 7, 15)) == Decimal('800.00')
    # Nothing was paid for May and June: Soll 1000, Ist 0; the due balance adds up what is left open.
    assert (result.soll_total, result.ist_total, result.due_balance) == (Decimal('1000.00'), Decimal('0.00'), Decimal('800.00'))


def test_a_surplus_inside_the_period_settles_positions_before_it():
    # The same lease; June paid 1200 (700 over), March to May unpaid (500 each). The 700 settle March (500) and 200 of
    # April. A notice for June alone has nothing to demand: its rent is paid and the surplus left it; one for March to
    # May demands April 300 + May 500 = 800 (the old reading demanded 1500 and showed a credit of 700 for June).
    contract, june = lease('500', start=date(2025, 3, 1)), [paid(6, '1200.00')]
    reference = date(2025, 7, 15)
    only_june = read(contract, (date(2025, 6, 1), date(2025, 6, 30)), june, reference=reference)
    assert (demanded(only_june, reference), month_of(only_june, 6).ist, month_of(only_june, 6).open_amount) == (
        Decimal('0.00'), Decimal('500.00'), Decimal('0.00'))
    spring = read(contract, (date(2025, 3, 1), date(2025, 5, 31)), june, reference=reference)
    assert demanded(spring, reference) == Decimal('800.00')


def test_a_payment_made_ahead_for_a_later_month_meets_its_own_soll_and_is_no_surplus():
    # Rent 600; January unpaid, February and March paid; April's rent was paid ahead on 28 March (600 for 600 owed).
    # Read on 31 March for January to March: the April payment is exactly April's rent, no surplus, so January's 600 stay
    # open. (Without April's Soll the 600 would look like a surplus and settle January.)
    contract = lease('600')
    payments = [paid(2, '600.00'), paid(3, '600.00'), paid(4, '600.00', on=date(2025, 3, 28))]
    result = read(contract, (date(2025, 1, 1), date(2025, 3, 31)), payments, reference=date(2025, 3, 31))
    assert demanded(result, date(2025, 3, 31)) == Decimal('600.00')
    assert {item.period_month for item in result.open_items} == {1, 2, 3}  # April is not part of the period


def test_a_position_that_is_not_due_at_the_reference_date_is_not_settled_by_the_surplus_of_another_period():
    # Rent 600; June paid 3000 ahead (2400 over); reference 31 March. Due: January to March (1800), April to
    # June not yet. The 2400 settle the 1800 of January to March; the other 600 stay a credit (June's), April and May
    # are not settled early. A notice for April to June has nothing overdue.
    contract = lease('600')
    reference = date(2025, 3, 31)
    april_to_june = read(contract, (date(2025, 4, 1), date(2025, 6, 30)), [paid(6, '3000.00')], reference=reference)
    assert [(i.period_month, i.open_amount) for i in april_to_june.open_items if i.payment_type == PT_KALTMIETE] == [
        (4, Decimal('600.00')), (5, Decimal('600.00')), (6, Decimal('-600.00'))]
    assert (april_to_june.due_balance, april_to_june.not_yet_due_balance) == (Decimal('0.00'), Decimal('600.00'))
    assert demanded(april_to_june, reference) == Decimal('0.00')


def test_other_scope_money_is_counted_for_the_positions_shown_only():
    # June: 100 paid into this scope's account, 900 into another scope's account (500 owed): 500 over settle January.
    # The note of the other scope's money belongs to the period that holds June.
    contract = lease('500')
    payments = [paid(6, '100.00'), paid(6, '900.00', other_scope=True)]
    january_to_may = read(contract, (date(2025, 1, 1), date(2025, 5, 31)), payments)
    june_to_december = read(contract, (date(2025, 6, 1), date(2025, 12, 31)), payments)
    assert (january_to_may.other_scope_ist, june_to_december.other_scope_ist) == (None, Decimal('900.00'))
    assert demanded(january_to_may) == Decimal('2000.00')  # 5 x 500 - the 500 that settled January


def test_a_booking_without_a_period_is_read_with_the_period_the_service_chose_it_for():
    contract = lease('500')
    payments = [PaymentInput(uuid.uuid4(), PT_KALTMIETE, 'IN', Decimal('500.00'), None, None, lease_agreement_id=LEASE,
                             value_date=date(2025, 6, 2))]
    result = read(contract, (date(2025, 1, 1), date(2025, 5, 31)), payments)
    # 500 without a month are a credit position of their own (shown in the period the service chose for them) and
    # settle January: 5 x 500 - 500 = 2000.
    assert demanded(result) == Decimal('2000.00')
    assert (result.soll_total, result.ist_total) == (Decimal('2500.00'), Decimal('500.00'))


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
    overdue = find_overdue_items(list(result.open_items), reference, current_year=2025)
    notice = assemble_dunning(overdue, 0, reference, rate_periods=RATES)
    assert (notice.principal_amount, notice.interest_amount) == (Decimal('200.00'), Decimal('5.10'))
    assert [(s['start'], s['end'], s['principal'], s['interest']) for s in notice.interest_snapshot['items'][0]['segments']] == [
        ('2025-03-04', '2025-04-09', '600.00', '4.26'), ('2025-04-10', '2025-05-01', '200.00', '0.84')]
