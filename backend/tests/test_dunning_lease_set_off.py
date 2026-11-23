"""A surplus settles the oldest due open position of its calendar year and type, read at the reference date (#806).

Owner decision of 2026-11-23. The pure arithmetic is pinned in tests/engine/test_mietkonto_lease_set_off.py; here the
statement service, the dunning notice, the rent account and the deadline monitor read real leases through the API.
Every expected value is derived by hand in the comment of its test.

Two of the leases lie in the year 2098 on purpose: the reference date of a notice or of the deadline monitor is then
later than any day on which these tests can run, so a statement read at today's date instead of the reference date
sees no position as due and settles nothing.
"""
from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal

import pytest

from sqlalchemy import text

from app.core.db import get_tenant_session
from app.services.payment_service import PaymentService
from tests.test_rent_changes import api_client, rent_db as rent_db, rent_world as rent_world  # noqa: F401

pytestmark = pytest.mark.asyncio

NOTHING_OVERDUE = (422, 'No overdue items found for this tenant in the given period')


async def new_lease(api, w, *, valid_from, rent, number, advance='0.00'):
    unit = await api.post('/api/units', json={'property_id': str(w['hidden_property']), 'number': number, 'usage_type': 'WOHNUNG'})
    assert unit.status_code == 201, unit.text
    lease = await api.post('/api/leases', json={
        'unit_id': unit.json()['id'], 'tenant_profile_id': str(w['tenant']), 'valid_from': valid_from,
        'base_rent': rent, 'advance_payment': advance})
    assert lease.status_code == 201, lease.text
    account = await api.post('/api/bank-accounts', json={
        'property_id': str(w['hidden_property']), 'account_type': 'LAUFEND', 'label': f'Konto {number}',
        'iban': 'DE36302501010002042407'})
    assert account.status_code == 201, account.text
    return lease.json()['id'], account.json()['id']


async def pay(api, w, lease, account, amount, year, month, value_date, kind='KALTMIETE'):
    """A booked payment (rent unless told otherwise); a year of None books it without an assigned period."""
    period = {} if year is None else {'period_year': year, 'period_month': month}
    payment = await api.post('/api/payments', json={
        'bank_account_id': str(account), 'direction': 'IN', 'amount': amount, 'value_date': value_date,
        'payment_type': kind, 'tenant_profile_id': str(w['tenant']), 'lease_agreement_id': str(lease),
        **period})
    assert payment.status_code == 201, payment.text
    booked = await api.post(f"/api/payments/{payment.json()['id']}/book")
    assert booked.status_code == 200, booked.text


async def notice(api, w, lease, first, last, reference):
    return await api.post('/api/dunning/tenant', json={
        'tenant_profile_id': str(w['tenant']), 'lease_agreement_id': str(lease), 'period_start': first,
        'period_end': last, 'reference_date': reference})


async def overdue_count(api, reference):
    """The overdue open positions of the organization as the deadline monitor reports them."""
    response = await api.get('/api/fristen', params={'reference_date': reference})
    assert response.status_code == 200, response.text
    detail = next(row['detail'] for row in response.json() if row['kind'] == 'OFFENE_POSTEN')
    return int(re.match(r'(\d+) ', detail).group(1))


async def rent_account(api, lease, first, last, reference):
    response = await api.get(f'/api/payments/mietkonto/lease/{lease}', params={
        'period_start': first, 'period_end': last, 'as_of': reference})
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_surplus_of_the_next_year_does_not_settle_the_rent_that_the_old_year_still_owes(rent_world):
    w = rent_world
    async with api_client(w) as api:
        # The lease starts on 1 December 2025 at 500.00 rent. December is unpaid: a notice over 2025 as of 15 January
        # 2026 demands 500.00.
        lease, account = await new_lease(api, w, valid_from='2025-12-01', rent='500.00', number='Year end')
        before = await notice(api, w, lease, '2025-01-01', '2025-12-31', '2026-01-15')
        assert (before.status_code, before.json()['principal_amount']) == (201, '500.00')
        # 1000.00 are paid for January 2026 (500.00 owed). The 500.00 over are not set off across the year end: they
        # stay a credit of January, December stays open and is demanded. (The manager rebooks such a payment.)
        await pay(api, w, lease, account, '1000.00', 2026, 1, '2026-01-05')
        after = await notice(api, w, lease, '2025-01-01', '2025-12-31', '2026-01-15')
        assert (after.status_code, after.json()['principal_amount']) == (201, '500.00')
        january = await notice(api, w, lease, '2026-01-01', '2026-01-31', '2026-01-15')
        assert (january.status_code, january.json()['detail']) == NOTHING_OVERDUE
        # The rent account of 2025 lists December as open with its 500.00; that of 2026 lists January's 1000.00 and the
        # credit of 500.00. 2026 owes 12 x 500.00 = 6000.00 and holds 1000.00: balance 5000.00, of which only January
        # is due on 15 January (-500.00) and the other 11 months, 5500.00, are not.
        old = await rent_account(api, lease, '2025-01-01', '2025-12-31', '2026-01-15')
        row = next(row for row in old['open_items'] if row['payment_type'] == 'KALTMIETE')
        assert (row['soll'], row['ist'], row['open_amount']) == ('500.00', '0.00', '500.00')
        assert (old['balance'], old['due_balance']) == ('500.00', '500.00')
        new = await rent_account(api, lease, '2026-01-01', '2026-12-31', '2026-01-15')
        row = next(row for row in new['open_items'] if row['payment_type'] == 'KALTMIETE' and row['period_month'] == 1)
        assert (row['soll'], row['ist'], row['open_amount']) == ('500.00', '1000.00', '-500.00')
        assert (new['balance'], new['due_balance'], new['not_yet_due_balance']) == ('5000.00', '-500.00', '5500.00')


async def test_a_period_that_ends_inside_a_month_does_not_shorten_that_month(rent_world):
    w = rent_world
    async with api_client(w) as api:
        # Rent 500.00 from 1 January 2025; January is unpaid and February is paid 500.00 on 3 February. A notice for 1
        # January to 14 February as of 15 April demands January's 500.00: February's rent is 500.00 for the whole
        # month, so its payment is no surplus. (Charging 14 of 28 days, 250.00, made 250.00 of it a surplus that
        # settled January: 250.00 demanded.)
        lease, account = await new_lease(api, w, valid_from='2025-01-01', rent='500.00', number='Edge')
        await pay(api, w, lease, account, '500.00', 2025, 2, '2025-02-03')
        answer = await notice(api, w, lease, '2025-01-01', '2025-02-14', '2025-04-15')
        assert answer.status_code == 201, answer.text
        assert answer.json()['principal_amount'] == '500.00'
        assert [(row['period_month'], row['open_amount']) for row in answer.json()['included_items']] == [(1, '500.00')]


async def test_a_payment_without_an_assigned_year_counts_in_the_year_of_its_value_date(rent_world):
    w = rent_world
    async with api_client(w) as api:
        # Rent 500.00 from 1 January 2024, nothing paid. 500.00 were paid on 10 June 2024 without a month: they count in
        # 2024 and settle January 2024, the oldest due open rent of that year, whichever period is asked for. A notice
        # for January to March 2024 as of 15 January 2025 demands February and March, 2 x 500.00 = 1000.00, although
        # the payment's value date lies outside that period (read only in the period of its value date it demanded
        # 1500.00).
        lease, account = await new_lease(api, w, valid_from='2024-01-01', rent='500.00', number='Unassigned')
        await pay(api, w, lease, account, '500.00', None, None, '2024-06-10')
        answer = await notice(api, w, lease, '2024-01-01', '2024-03-31', '2025-01-15')
        assert answer.status_code == 201, answer.text
        assert answer.json()['principal_amount'] == '1000.00'
        # A payment of 2025 counts in 2025: the notice over the old year is not reduced by it.
        await pay(api, w, lease, account, '500.00', None, None, '2025-01-10')
        again = await notice(api, w, lease, '2024-01-01', '2024-03-31', '2025-01-15')
        assert again.json()['principal_amount'] == '1000.00'


async def test_the_rent_account_of_a_year_agrees_with_itself_and_depends_on_no_other_year(rent_world):
    w = rent_world
    async with api_client(w) as api:
        # Rent 500.00 from 1 January 2024, nothing paid for 2024. 2025 is paid in full, June 1000.00: the 500.00 over find no
        # open rent in 2025 and stay a credit of June, whatever 2024 owes. The view of 2025: June open -500.00, the
        # other months 0.00, the due balance -500.00 and the balance -500.00. (The surplus had settled January 2024, a
        # month this view cannot show: all rows 0.00, due balance 0.00, balance -500.00.)
        lease, account = await new_lease(api, w, valid_from='2024-01-01', rent='500.00', number='Account')
        for month in range(1, 13):
            await pay(api, w, lease, account, '1000.00' if month == 6 else '500.00', 2025, month, f'2025-{month:02d}-02')
        year = await rent_account(api, lease, '2025-01-01', '2025-12-31', '2026-01-15')
        assert [(row['period_month'], row['open_amount']) for row in year['open_items']
                if row['payment_type'] == 'KALTMIETE' and Decimal(row['open_amount'])] == [(6, '-500.00')]
        assert (year['balance'], year['due_balance'], year['not_yet_due_balance']) == ('-500.00', '-500.00', '0.00')
        assert sum(Decimal(row['open_amount']) for row in year['open_items']) == Decimal(year['balance'])
        # A notice for 2024 demands what 2024 owes in rent, 12 x 500.00: nothing of 2025 reaches it.
        old = await notice(api, w, lease, '2024-01-01', '2024-12-31', '2026-01-15')
        assert old.json()['principal_amount'] == '6000.00'


async def test_a_notice_is_read_at_its_reference_date_and_not_at_today(rent_world):
    w = rent_world
    async with api_client(w) as api:
        # Rent 600.00 from 1 January 2098. April was paid 1800.00 (600.00 owed): 1200.00 over. Reference date 30 April
        # 2098: January to April are due. The 1200.00 settle January and February, the oldest rent of 2098; March is
        # open with 600.00 (a notice for January to March), April is paid.
        lease, account = await new_lease(api, w, valid_from='2098-01-01', rent='600.00', number='Future')
        await pay(api, w, lease, account, '1800.00', 2098, 4, '2098-04-02')
        answer = await notice(api, w, lease, '2098-01-01', '2098-03-31', '2098-04-30')
        assert answer.status_code == 201, answer.text
        assert answer.json()['principal_amount'] == '600.00'
        assert [(row['period_month'], row['open_amount']) for row in answer.json()['included_items']] == [(3, '600.00')]
        # Read at today's date nothing of 2098 would be due and nothing would be settled: January to March, 1800.00.


async def test_the_deadline_monitor_counts_the_positions_left_open_at_its_reference_date(rent_world):
    w = rent_world
    async with api_client(w) as api:
        lease, account = await new_lease(api, w, valid_from='2098-01-01', rent='600.00', number='Monitor')
        reference = '2098-04-30'
        # January to April are due and unpaid: four positions more than without this lease.
        unpaid = await overdue_count(api, reference)
        # April is paid 1800.00 for 600.00 owed: April is settled, and the 1200.00 over settle January and February.
        # Only March stays open: three positions fewer. Read at today's date no 2098 position is due, nothing is set
        # off, and only April's own 600.00 would settle a position: one fewer.
        await pay(api, w, lease, account, '1800.00', 2098, 4, '2098-04-02')
        assert unpaid - await overdue_count(api, reference) == 3


async def test_the_deadline_monitor_reads_its_last_month_in_full(rent_world):
    w = rent_world
    async with api_client(w) as api:
        # Rent 600.00 from 1 January 2098; January is paid 500.00 (100.00 short), February to April 600.00 each. The
        # monitor reads from the lease start to its reference date, 15 April 2098, in the middle of April. April's rent
        # is 600.00 for the whole month and paid exactly: one overdue position more than without the lease, January's
        # 100.00. (Charging 15 of 30 days, 300.00, made 300.00 of April's payment a surplus that settled January: no
        # position more.)
        reference = '2098-04-15'
        before = await overdue_count(api, reference)
        lease, account = await new_lease(api, w, valid_from='2098-01-01', rent='600.00', number='Monitor edge')
        await pay(api, w, lease, account, '500.00', 2098, 1, '2098-01-05')
        for month in (2, 3, 4):
            await pay(api, w, lease, account, '600.00', 2098, month, f'2098-{month:02d}-05')
        assert await overdue_count(api, reference) - before == 1


async def settlement_advances(w, lease):
    """The advance sum the operating-cost settlement credits for 2025: what `set_advance_payments_derived` stores."""
    async with get_tenant_session(w['org']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(w['admin'])})
        return await PaymentService(db, w['org'], w['admin']).derive_nk_advance_total_for_period(
            uuid.UUID(lease), date(2025, 1, 1), date(2025, 12, 31))


async def book_2025(api, w, lease, account, *, rent, advance, reference='2026-01-15'):
    """Book rent and advance of 2025 as {month: amount} and read the rent account and a notice for the year."""
    for month, amount in rent.items():
        await pay(api, w, lease, account, amount, 2025, month, f'2025-{month:02d}-02')
    for month, amount in advance.items():
        await pay(api, w, lease, account, amount, 2025, month, f'2025-{month:02d}-03', kind='NK_VORAUSZAHLUNG')
    return (await rent_account(api, lease, '2025-01-01', '2025-12-31', reference),
            await notice(api, w, lease, '2025-01-01', '2025-12-31', reference))


async def test_a_rent_surplus_settles_no_advance_and_the_settlement_still_credits_the_advances_booked(rent_world):
    w = rent_world
    async with api_client(w) as api:
        # Rent 500.00 + advance 100.00, 2025. January's rent is unpaid and 600.00 were paid as June's advance (500.00
        # over its 100.00); everything else is paid. The 500.00 of the advance are no rent: they find no open advance of
        # 2025 and stay a credit of June's advance, January's rent stays open and a notice demands it. (The whole-lease
        # reading let the advance settle January's rent: no notice, yet the settlement credited the 600.00 as advances
        # and paid 500.00 back at 1200.00 costs, and January's rent was never collected.)
        lease, account = await new_lease(api, w, valid_from='2025-01-01', rent='500.00', number='Settle 1', advance='100.00')
        advances = {month: '600.00' if month == 6 else '100.00' for month in range(1, 13)}
        account_2025, answer = await book_2025(api, w, lease, account,
                                               rent={month: '500.00' for month in range(2, 13)}, advance=advances)
        assert answer.status_code == 201, answer.text
        assert answer.json()['principal_amount'] == '500.00'
        assert [(row['period_month'], row['payment_type']) for row in answer.json()['included_items']] == [(1, 'KALTMIETE')]
        assert sum(Decimal(row['open_amount']) for row in account_2025['open_items']) == Decimal(account_2025['balance'])
        # The settlement reads the booked advance payments by type, as on main: 11 x 100.00 + 600.00 = 1700.00.
        credited = await settlement_advances(w, lease)
        assert credited == Decimal('1700.00')
        # The tenant's debt by hand: owed 12 x 500.00 rent + the costs C of the settlement; paid 11 x 500.00 rent and
        # 1700.00 advances. With costs of 1200.00: 6000.00 + 1200.00 - 5500.00 - 1700.00 = 0.00. The notice demands the
        # rent that is open (+500.00) and the settlement pays back the advances that exceed the costs (1200.00 -
        # 1700.00 = -500.00): together 0.00, nothing lost and nothing counted twice. The rent account agrees: it owes
        # 7200.00 and holds 7200.00.
        costs = Decimal('1200.00')
        assert Decimal(answer.json()['principal_amount']) + (costs - credited) == Decimal('0.00') == (
            Decimal('6000.00') + costs - Decimal('5500.00') - Decimal('1700.00'))
        assert account_2025['balance'] == '0.00'


async def test_a_rent_surplus_leaves_the_unpaid_advance_open_where_the_settlement_does_not_credit_it(rent_world):
    w = rent_world
    async with api_client(w) as api:
        # The same lease. June's rent was paid 1000.00 (500.00 over), June's advance is unpaid, everything else is paid.
        # The 500.00 of rent find no open rent of 2025 and stay a credit of June's rent; June's advance stays open with its
        # 100.00, exactly as the settlement sees it: it credits the 11 x 100.00 = 1100.00 advances that were booked and
        # not the unpaid one. (The whole-lease reading showed June's advance as paid while the settlement did not credit
        # it, and charged the 100.00 again.)
        lease, account = await new_lease(api, w, valid_from='2025-01-01', rent='500.00', number='Settle 2', advance='100.00')
        rent = {month: '1000.00' if month == 6 else '500.00' for month in range(1, 13)}
        account_2025, answer = await book_2025(api, w, lease, account, rent=rent,
                                               advance={month: '100.00' for month in range(1, 13) if month != 6})
        assert answer.status_code == 201, answer.text
        assert answer.json()['principal_amount'] == '100.00'
        assert [(row['period_month'], row['payment_type']) for row in answer.json()['included_items']] == [(6, 'NK_VORAUSZAHLUNG')]
        assert await settlement_advances(w, lease) == Decimal('1100.00')
        # The tenant's debt by hand: owed 7200.00 (6000.00 rent + 1200.00 advances), paid 7600.00 (6500.00 rent + 1100.00
        # advances): 400.00 overpaid in all. The rent account shows exactly that: June's unpaid advance (+100.00) and
        # the credit of June's rent (-500.00) add up to -400.00, the balance. The 500.00 credit are the manager's to
        # rebook or to refund; the settlement's 1100.00 are the advances that were booked, unchanged.
        credit = next(row for row in account_2025['open_items'] if row['period_month'] == 6 and row['payment_type'] == 'KALTMIETE')
        assert (credit['open_amount'], account_2025['balance']) == ('-500.00', '-400.00')
        assert Decimal('100.00') + Decimal(credit['open_amount']) == Decimal(account_2025['balance'])
