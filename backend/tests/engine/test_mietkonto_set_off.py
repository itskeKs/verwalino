"""A surplus of one position of a lease settles the oldest due open position of its calendar year and type (#806).

Owner decision of 2026-11-23: a surplus is set off within the calendar year and within the position type; nothing is
set off across the year end or between rent and advances. Every expected value is derived by hand in the comment of
its test. The fixtures give every position of a month the same due date, the third of the month.
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.engine.dunning import assemble_dunning, find_overdue_items
from app.engine.mietkonto import (
    ExpectedCharge,
    LeaseChargeInput,
    PaymentInput,
    PT_HK_VORAUSZAHLUNG,
    PT_KALTMIETE,
    PT_KAUTION,
    PT_NACHZAHLUNG,
    PT_NK_VORAUSZAHLUNG,
    account_statement,
    tenant_expected_charges,
)

pytestmark = pytest.mark.no_db

LEASE = uuid.UUID(int=806)
AFTER_2025 = date(2026, 1, 15)
RATES = ((date(2025, 1, 1), Decimal('2.00')),)  # base rate 2.00 % + 5 points = 7.00 % a year


def owes(month, amount, kind=PT_KALTMIETE, year=2025):
    return ExpectedCharge(year, month, kind, Decimal(amount), lease_agreement_id=LEASE, due_date=date(year, month, 3))


def paid(month, amount, kind=PT_KALTMIETE, *, on=None, direction='IN', year=2025, **flags):
    return PaymentInput(uuid.uuid4(), kind, direction, Decimal(amount), year, month, lease_agreement_id=LEASE,
                        value_date=on, **flags)


def statement(charges, payments, as_of=AFTER_2025, **kwargs):
    return account_statement(charges, payments, as_of=as_of, set_off_surplus=True, **kwargs)


def positions(result):
    """Open amount per (month, type)."""
    return {(item.period_month, item.payment_type): item.open_amount for item in result.open_items}


def notice_principal(result, reference=AFTER_2025):
    overdue = find_overdue_items(list(result.open_items), reference, current_year=reference.year)
    return sum((item.open_amount for item in overdue), Decimal('0.00'))


def year_charges(rent, advance):
    lease = LeaseChargeInput(LEASE, date(2025, 1, 1), None, Decimal(rent), Decimal(advance))
    return tenant_expected_charges(lease, date(2025, 1, 1), date(2025, 12, 31))


# --- the two examples of the issue ---------------------------------------------------------------------------------

def test_a_june_rent_paid_twice_over_settles_the_oldest_position_and_the_notice_demands_what_is_open():
    # Owed: 12 x (500 + 100) = 7200. Paid: 1000 for the rent of June. Open: 7200 - 1000 = 6200.
    # June's rent needs 500, so 500 are left; they settle the oldest due position, the rent of January (500).
    result = statement(year_charges('500', '100'), [paid(6, '1000.00')])
    assert (result.soll_total, result.ist_total, result.balance) == (Decimal('7200.00'), Decimal('1000.00'), Decimal('6200.00'))
    open_now = positions(result)
    assert (open_now[(1, PT_KALTMIETE)], open_now[(6, PT_KALTMIETE)]) == (Decimal('0.00'), Decimal('0.00'))
    # Ten other months of rent (500) and twelve advances (100): 5000 + 1200.
    assert sum(open_now.values()) == Decimal('6200.00') == notice_principal(result)
    assert min(open_now.values()) == Decimal('0.00')  # nothing stays negative
    # The advance of June is not settled before the older January rent: 500 settled exactly one position.
    assert open_now[(6, PT_NK_VORAUSZAHLUNG)] == Decimal('100.00')
    # June's rent now holds what it needs, January's rent what moved to it; both add up to the 1000 paid.
    june, january = (next(i for i in result.open_items if (i.period_month, i.payment_type) == (m, PT_KALTMIETE)) for m in (6, 1))
    assert (june.soll, june.ist, january.soll, january.ist) == (Decimal('500.00'),) * 4


def test_a_rent_of_850_paid_with_1000_for_june_leaves_the_oldest_month_short_by_the_rest():
    # Owed: 12 x 850 = 10200 (no advance). Paid 1000 for June. Open: 10200 - 1000 = 9200.
    # June needs 850, the surplus of 150 settles part of the oldest position: January stays open with 850 - 150 = 700.
    result = statement(year_charges('850', '0'), [paid(6, '1000.00')])
    open_now = positions(result)
    assert (open_now[(1, PT_KALTMIETE)], open_now[(6, PT_KALTMIETE)], open_now[(2, PT_KALTMIETE)]) == (
        Decimal('700.00'), Decimal('0.00'), Decimal('850.00'))
    assert notice_principal(result) == sum(open_now.values()) == result.balance == Decimal('9200.00')


# --- which position a surplus settles ------------------------------------------------------------------------------

def test_a_surplus_settles_the_oldest_positions_one_after_the_other_across_months():
    # January to March unpaid (600 each); April paid 1700 for 600 owed: 1100 are left.
    # 1100 settle January (600) completely, then 500 of the 600 of February; March stays open.
    charges = [owes(m, '600') for m in (1, 2, 3, 4)]
    result = statement(charges, [paid(4, '1700.00')])
    assert positions(result) == {(1, PT_KALTMIETE): Decimal('0.00'), (2, PT_KALTMIETE): Decimal('100.00'),
                                 (3, PT_KALTMIETE): Decimal('600.00'), (4, PT_KALTMIETE): Decimal('0.00')}
    assert result.balance == Decimal('700.00') == notice_principal(result)  # 2400 owed - 1700 paid


def test_a_surplus_of_rent_settles_no_advance_even_of_an_earlier_month():
    # May's operating advance (100) and June's (100) are unpaid, May's rent is paid; June's rent was paid 650 for 500
    # owed: 150 are left. No rent of the year is open, and an advance is no rent: the 150 stay a credit of June's rent,
    # both advances stay open. Owed 1200 - paid 1150 = 50 in all, yet 200 are open (the credit is not set off).
    charges = [owes(5, '500'), owes(5, '100', PT_NK_VORAUSZAHLUNG), owes(6, '500'), owes(6, '100', PT_NK_VORAUSZAHLUNG)]
    result = statement(charges, [paid(5, '500.00'), paid(6, '650.00')])
    assert positions(result) == {(5, PT_KALTMIETE): Decimal('0.00'), (5, PT_NK_VORAUSZAHLUNG): Decimal('100.00'),
                                 (6, PT_KALTMIETE): Decimal('-150.00'), (6, PT_NK_VORAUSZAHLUNG): Decimal('100.00')}
    assert result.balance == Decimal('50.00')
    assert notice_principal(result) == Decimal('200.00')


def test_a_surplus_of_an_advance_settles_no_rent_but_the_older_advance_of_its_type():
    # The other way round: June's operating advance was paid 250 for 100 owed (150 left), April's rent is unpaid (500)
    # and so is March's operating advance (100). The advance is no rent: April's rent stays open with its 500. The 150
    # settle March's operating advance (the oldest of its type, 100); the other 50 stay a credit of June.
    charges = [owes(3, '100', PT_NK_VORAUSZAHLUNG), owes(4, '500'), owes(6, '100', PT_NK_VORAUSZAHLUNG)]
    result = statement(charges, [paid(6, '250.00', PT_NK_VORAUSZAHLUNG)])
    assert positions(result) == {(3, PT_NK_VORAUSZAHLUNG): Decimal('0.00'), (4, PT_KALTMIETE): Decimal('500.00'),
                                 (6, PT_NK_VORAUSZAHLUNG): Decimal('-50.00')}
    assert result.balance == Decimal('450.00')  # 700 owed - 250 paid
    assert notice_principal(result) == Decimal('500.00')


def test_a_surplus_of_the_operating_advance_does_not_settle_the_heating_advance_and_the_reverse():
    # January's operating advance (100) and heating advance (50) are unpaid, February's are paid 160 and 70 (60 and 20
    # over 100 and 50). The 60 settle January's operating advance (100 - 60 = 40 open), the 20 January's heating
    # advance (50 - 20 = 30 open); each stays within its own type. Owed 300 - paid 230 = 70 = 40 + 30.
    charges = [owes(1, '100', PT_NK_VORAUSZAHLUNG), owes(1, '50', PT_HK_VORAUSZAHLUNG),
               owes(2, '100', PT_NK_VORAUSZAHLUNG), owes(2, '50', PT_HK_VORAUSZAHLUNG)]
    result = statement(charges, [paid(2, '160.00', PT_NK_VORAUSZAHLUNG), paid(2, '70.00', PT_HK_VORAUSZAHLUNG)])
    assert positions(result) == {(1, PT_HK_VORAUSZAHLUNG): Decimal('30.00'), (1, PT_NK_VORAUSZAHLUNG): Decimal('40.00'),
                                 (2, PT_HK_VORAUSZAHLUNG): Decimal('0.00'), (2, PT_NK_VORAUSZAHLUNG): Decimal('0.00')}
    assert result.balance == Decimal('70.00') == notice_principal(result)
    # Only the operating advance was paid beyond its Soll (160 for 100); January's heating advance is no operating advance.
    result = statement(charges, [paid(2, '160.00', PT_NK_VORAUSZAHLUNG)])
    assert positions(result)[(1, PT_HK_VORAUSZAHLUNG)] == Decimal('50.00')
    assert positions(result)[(1, PT_NK_VORAUSZAHLUNG)] == Decimal('40.00')


def test_a_surplus_that_settles_a_position_only_in_part_leaves_the_rest_open():
    # March rent 600 unpaid, April rent 600 paid 750: 150 are left and settle 150 of March's 600.
    result = statement([owes(3, '600'), owes(4, '600')], [paid(4, '750.00')])
    items = {item.period_month: item for item in result.open_items}
    assert (items[3].ist, items[3].open_amount, items[4].ist, items[4].open_amount) == (
        Decimal('150.00'), Decimal('450.00'), Decimal('600.00'), Decimal('0.00'))


def test_the_surplus_of_several_positions_is_used_in_the_order_of_the_money_that_arrived():
    # January (600) is unpaid. February was paid 700 on 10 March and March 1000 on 5 March (600 owed each): 100 are
    # left from the payment of 10 March, 400 from the payment of 5 March. The older money settles first: 400 (5 March),
    # then 100 (10 March), together 500 of January's 600; 100 stay open. 1800 owed - 1700 paid = 100.
    charges = [owes(1, '600'), owes(2, '600'), owes(3, '600')]
    payments = [paid(2, '700.00', on=date(2025, 3, 10)), paid(3, '1000.00', on=date(2025, 3, 5))]
    result = statement(charges, payments, include_payment_dates=True)
    january = result.open_items[0]
    assert (january.open_amount, january.payment_changes) == (
        Decimal('100.00'), ((date(2025, 3, 5), Decimal('400.00')), (date(2025, 3, 10), Decimal('100.00'))))
    assert result.balance == Decimal('100.00') == notice_principal(result)


# --- due dates -----------------------------------------------------------------------------------------------------

def test_a_surplus_settles_only_positions_that_are_due_at_the_reference_date():
    # Rent 600 for January to June; June was paid 3000 in advance (2400 left). On 31 March January to March are due
    # (the third of the month), April to June are not: the 2400 settle the 1800 of January to March, and the other
    # 600 stay a credit; April and May are not settled early and do not become arrears early. Owed 3600 - paid 3000
    # = 600 in all, none of it due.
    charges = [owes(m, '600') for m in range(1, 7)]
    payments = [paid(6, '3000.00')]
    result = statement(charges, payments, as_of=date(2025, 3, 31))
    assert positions(result) == {(1, PT_KALTMIETE): Decimal('0.00'), (2, PT_KALTMIETE): Decimal('0.00'),
                                 (3, PT_KALTMIETE): Decimal('0.00'), (4, PT_KALTMIETE): Decimal('600.00'),
                                 (5, PT_KALTMIETE): Decimal('600.00'), (6, PT_KALTMIETE): Decimal('-600.00')}
    assert (result.due_balance, result.not_yet_due_balance, result.balance) == (
        Decimal('0.00'), Decimal('600.00'), Decimal('600.00'))
    assert notice_principal(result, date(2025, 3, 31)) == Decimal('0.00')
    # On 30 April April is due as well and takes the other 600; May (due the third of May) is still not due.
    later = statement(charges, payments, as_of=date(2025, 4, 30))
    assert positions(later) == {(1, PT_KALTMIETE): Decimal('0.00'), (2, PT_KALTMIETE): Decimal('0.00'),
                                (3, PT_KALTMIETE): Decimal('0.00'), (4, PT_KALTMIETE): Decimal('0.00'),
                                (5, PT_KALTMIETE): Decimal('600.00'), (6, PT_KALTMIETE): Decimal('0.00')}
    assert (later.due_balance, later.not_yet_due_balance) == (Decimal('0.00'), Decimal('600.00'))


def test_a_position_that_is_not_yet_due_is_not_settled_early_and_nothing_due_means_no_set_off():
    # January and February unpaid (600 each), March paid 1600 for 600 owed (1000 left). On 20 January only January is
    # due (the third): it takes 600. February falls due on 3 February and is not settled early; the other 400 stay a
    # credit of March. Owed 1800 - paid 1600 = 200 in all: nothing due, 200 not yet due (February 600 less 400 credit).
    charges = [owes(m, '600') for m in (1, 2, 3)]
    payments = [paid(3, '1600.00')]
    result = statement(charges, payments, as_of=date(2025, 1, 20))
    assert positions(result) == {(1, PT_KALTMIETE): Decimal('0.00'), (2, PT_KALTMIETE): Decimal('600.00'),
                                 (3, PT_KALTMIETE): Decimal('-400.00')}
    assert (result.due_balance, result.not_yet_due_balance) == (Decimal('0.00'), Decimal('200.00'))
    assert notice_principal(result, date(2025, 1, 20)) == Decimal('0.00')
    # On 2 January nothing is due yet: nothing is settled and the whole surplus of 1000 stays a credit of March.
    early = statement(charges, payments, as_of=date(2025, 1, 2))
    assert positions(early) == {(1, PT_KALTMIETE): Decimal('600.00'), (2, PT_KALTMIETE): Decimal('600.00'),
                                (3, PT_KALTMIETE): Decimal('-1000.00')}
    assert (early.due_balance, notice_principal(early, date(2025, 1, 2))) == (Decimal('0.00'), Decimal('0.00'))


# --- which types take part -------------------------------------------------------------------------------------------

def test_a_nachzahlung_is_no_surplus_and_settles_no_rent():
    # Rent 500 for January to March; February and March are paid, January is not. The tenant also paid a
    # NACHZAHLUNG of 300 for March: the balance of a settlement, which has no Soll in this statement. It is no money
    # paid beyond a rent: January stays open with its 500, and the 300 stay a credit of their own. Owed 1500, paid
    # 1000 as rent: the notice of 15 April demands 500 (the review of #885 saw 200: the 300 counted twice).
    charges = [owes(m, '500') for m in (1, 2, 3)]
    payments = [paid(2, '500.00'), paid(3, '500.00'), paid(3, '300.00', PT_NACHZAHLUNG)]
    result = statement(charges, payments, as_of=date(2025, 4, 15))
    assert positions(result) == {(1, PT_KALTMIETE): Decimal('500.00'), (2, PT_KALTMIETE): Decimal('0.00'),
                                 (3, PT_KALTMIETE): Decimal('0.00'), (3, PT_NACHZAHLUNG): Decimal('-300.00')}
    assert notice_principal(result, date(2025, 4, 15)) == Decimal('500.00')


@pytest.mark.parametrize('kind', [PT_NACHZAHLUNG, PT_KAUTION])
def test_a_payment_of_a_type_without_a_soll_neither_feeds_the_pool_nor_is_settled_by_it(kind):
    # Rent 500 for January and February, January unpaid. February was paid 800 (300 over its rent) and 300 came in
    # of the other type for February. The 300 of rent settle January (500 - 300 = 200); the 300 of the other type
    # have no Soll, feed nothing and stay a credit of -300.
    charges = [owes(1, '500'), owes(2, '500')]
    result = statement(charges, [paid(2, '800.00'), paid(2, '300.00', kind)])
    assert positions(result) == {(1, PT_KALTMIETE): Decimal('200.00'), (2, PT_KALTMIETE): Decimal('0.00'),
                                 (2, kind): Decimal('-300.00')}
    # Of the other type 200 went out alone for January: no Soll, so that position owes 200 back (due on 1 January,
    # before the rent). The 300 of rent do not settle it: they settle January's rent (500 - 300 = 200), and the 200
    # owed back stay open.
    owed_back = statement(charges, [paid(2, '800.00'), paid(1, '200.00', kind, direction='OUT')])
    assert positions(owed_back) == {(1, PT_KALTMIETE): Decimal('200.00'), (1, kind): Decimal('200.00'),
                                    (2, PT_KALTMIETE): Decimal('0.00')}


# --- sign: refunds and reversals -----------------------------------------------------------------------------------

def test_a_refund_reduces_what_was_paid_and_with_it_the_surplus():
    # January and February unpaid (600 each), March paid 1000 and 300 paid back for it: 700 net, 100 left over 600.
    # The 100 settle January: 500 open. Open in all: 1800 owed - 700 paid = 1100.
    charges = [owes(m, '600') for m in (1, 2, 3)]
    result = statement(charges, [paid(3, '1000.00'), paid(3, '300.00', direction='OUT')])
    assert positions(result) == {(1, PT_KALTMIETE): Decimal('500.00'), (2, PT_KALTMIETE): Decimal('600.00'),
                                 (3, PT_KALTMIETE): Decimal('0.00')}
    assert result.balance == Decimal('1100.00') == notice_principal(result)


def test_a_surplus_that_a_refund_takes_back_settles_nothing_and_nothing_is_negative():
    # March paid 1000, 400 paid back: 600 net for 600 owed, no surplus. January and February stay as they are.
    charges = [owes(m, '600') for m in (1, 2, 3)]
    result = statement(charges, [paid(3, '1000.00'), paid(3, '400.00', direction='OUT')])
    assert positions(result) == {(1, PT_KALTMIETE): Decimal('600.00'), (2, PT_KALTMIETE): Decimal('600.00'),
                                 (3, PT_KALTMIETE): Decimal('0.00')}
    assert result.balance == Decimal('1200.00') == notice_principal(result)  # 1800 owed - 600 paid


def test_a_refund_of_the_dated_surplus_takes_back_the_latest_money_first():
    # March paid 700 on 5 March and 300 on 20 March (600 owed: 100 left from the first, 300 from the second);
    # 350 paid back on 25 March leave 100 + 300 - 350 = 50 net, which is 50 of the older 100 (the refund takes the
    # latest money back first: 300, then 50 of the 100). The 50 settle January (600) on 5 March.
    charges = [owes(1, '600'), owes(3, '600')]
    payments = [paid(3, '700.00', on=date(2025, 3, 5)), paid(3, '300.00', on=date(2025, 3, 20)),
                paid(3, '350.00', on=date(2025, 3, 25), direction='OUT')]
    result = statement(charges, payments, include_payment_dates=True)
    assert (result.open_items[0].open_amount, result.open_items[0].payment_changes) == (
        Decimal('550.00'), ((date(2025, 3, 5), Decimal('50.00')),))


def test_a_reversed_payment_and_its_reversal_leave_no_surplus():
    charges = [owes(1, '600'), owes(2, '600')]
    payments = [paid(2, '1000.00', is_reversed=True), paid(2, '1000.00', direction='OUT', is_reversal=True)]
    assert positions(statement(charges, payments)) == {(1, PT_KALTMIETE): Decimal('600.00'), (2, PT_KALTMIETE): Decimal('600.00')}


def test_a_refund_without_a_charge_is_a_position_that_a_surplus_of_its_type_settles():
    # 200 were paid back as rent for May, which was never owed: the 200 are owed back (a position due on 1 May).
    # June's rent was paid 800 for 600 (200 left): they settle that position, both are rent of 2025.
    charges = [owes(6, '600')]
    result = statement(charges, [paid(5, '200.00', direction='OUT'), paid(6, '800.00')])
    assert positions(result) == {(5, PT_KALTMIETE): Decimal('0.00'), (6, PT_KALTMIETE): Decimal('0.00')}
    assert result.balance == Decimal('0.00')
    # The same refund of an operating advance is no rent: June's rent surplus does not settle it.
    other = statement(charges, [paid(5, '200.00', PT_NK_VORAUSZAHLUNG, direction='OUT'), paid(6, '800.00')])
    assert positions(other) == {(5, PT_NK_VORAUSZAHLUNG): Decimal('200.00'), (6, PT_KALTMIETE): Decimal('-200.00')}


# --- a surplus larger than the debt ----------------------------------------------------------------------------------

def test_a_surplus_larger_than_the_whole_debt_offers_no_notice_and_the_rest_stays_visible_as_credit():
    # January unpaid (600); February paid 2000 for 600 owed: 1400 left. 600 settle January, 800 stay a credit.
    result = statement([owes(1, '600'), owes(2, '600')], [paid(2, '2000.00')])
    assert positions(result) == {(1, PT_KALTMIETE): Decimal('0.00'), (2, PT_KALTMIETE): Decimal('-800.00')}
    assert result.balance == Decimal('-800.00')  # 1200 owed - 2000 paid
    assert find_overdue_items(list(result.open_items), AFTER_2025, current_year=2026) == []
    assert assemble_dunning([], 0, AFTER_2025) is None


# --- interest --------------------------------------------------------------------------------------------------------

def interest_notice(payments, charges=None):
    charges = charges or [owes(3, '600'), owes(4, '600')]
    reference = date(2025, 5, 1)
    result = statement(charges, payments, as_of=reference, include_payment_dates=True)
    overdue = find_overdue_items(list(result.open_items), reference, current_year=2025)
    return assemble_dunning(overdue, 0, reference, rate_periods=RATES)


def test_interest_runs_on_what_is_left_and_on_a_settled_position_only_until_the_money_arrived():
    # March rent 600 is unpaid since its due date 3 March; April was paid 1000 on 10 April (400 left over 600),
    # which settles 400 of March: 200 stay open. Interest 7.00 % a year (2.00 % base + 5 points), as of 1 May:
    #   600 from 4 March to 9 April (28 + 9 = 37 days): 600 x 7 x 37 / 36500 = 4.2575
    #   200 from 10 April to 1 May (21 + 1 = 22 days):  200 x 7 x 22 / 36500 = 0.8438
    # together 5.1013, rounded 5.10 (not 600 x 7 x 59 / 36500 = 6.79 on the whole, not 200 x 7 x 59 / 36500 = 2.26).
    notice = interest_notice([paid(4, '1000.00', on=date(2025, 4, 10))])
    assert (notice.principal_amount, notice.interest_amount, notice.total_amount) == (
        Decimal('200.00'), Decimal('5.10'), Decimal('205.10'))
    item = notice.interest_snapshot['items'][0]
    assert [(s['start'], s['end'], s['principal'], s['interest']) for s in item['segments']] == [
        ('2025-03-04', '2025-04-09', '600.00', '4.26'), ('2025-04-10', '2025-05-01', '200.00', '0.84')]


def test_a_surplus_that_arrived_before_the_position_was_due_causes_no_interest_on_what_it_settled():
    # The same, but April was paid 1000 already on 20 February, before March fell due: the 400 were there on the
    # due date, so only the 200 left are late: 200 x 7 x 59 / 36500 (4 March to 1 May) = 2.2630, rounded 2.26.
    notice = interest_notice([paid(4, '1000.00', on=date(2025, 2, 20))])
    assert (notice.principal_amount, notice.interest_amount) == (Decimal('200.00'), Decimal('2.26'))


def test_without_dates_a_set_off_still_lowers_the_principal():
    # The statement without payment dates (no interest is computed) settles the same amounts.
    result = statement([owes(3, '600'), owes(4, '600')], [paid(4, '1000.00', on=date(2025, 4, 10))])
    assert (positions(result)[(3, PT_KALTMIETE)], result.open_items[0].payment_changes) == (Decimal('200.00'), ())


# --- what does not change --------------------------------------------------------------------------------------------

def test_payments_of_other_scopes_take_part_like_any_payment_and_stay_summed_up():
    # June: 100 paid into this scope's account, 900 into another scope's account (500 owed): 500 left over; they
    # settle January (500). The statement is the same with and without the mark of the other scope.
    charges = [owes(1, '500'), owes(6, '500')]
    own, elsewhere = paid(6, '100.00'), paid(6, '900.00', other_scope=True)
    result = statement(charges, [own, elsewhere])
    assert positions(result) == {(1, PT_KALTMIETE): Decimal('0.00'), (6, PT_KALTMIETE): Decimal('0.00')}
    assert result.other_scope_ist == Decimal('900.00')
    assert replace(result, other_scope_ist=None) == statement(charges, [own, replace(elsewhere, other_scope=False)])


def test_totals_never_change_with_the_set_off_and_a_statement_without_it_keeps_the_credit_on_its_position():
    charges = [owes(m, '600') for m in (1, 2, 3)]
    payments = [paid(3, '1500.00')]
    plain = account_statement(charges, payments, as_of=AFTER_2025)
    settled = statement(charges, payments)
    assert (settled.soll_total, settled.ist_total, settled.balance, settled.due_balance + settled.not_yet_due_balance) == (
        plain.soll_total, plain.ist_total, plain.balance, plain.balance)
    # Owner statements and the dashboard do not ask for the set-off: the credit of March stays on March.
    assert [item.open_amount for item in plain.open_items] == [Decimal('600.00'), Decimal('600.00'), Decimal('-900.00')]
    # Set off, the 900 left over 600 owed settle January (600) and 300 of February: 300 are open in all (1800 - 1500).
    assert [item.open_amount for item in settled.open_items] == [Decimal('0.00'), Decimal('300.00'), Decimal('0.00')]


def test_the_set_off_needs_the_reference_date():
    with pytest.raises(ValueError):
        account_statement([owes(1, '600')], [], set_off_surplus=True)
