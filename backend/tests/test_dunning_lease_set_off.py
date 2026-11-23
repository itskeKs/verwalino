"""A surplus settles the oldest due open position of the whole lease, and at the reference date of the reading (#806).

Owner decision of 2026-11-22. The pure arithmetic is pinned in tests/engine/test_mietkonto_lease_set_off.py; here the
statement service, the dunning notice and the deadline monitor read real leases through the API. Every expected value
is derived by hand in the comment of its test.

Two of the leases lie in the year 2098 on purpose: the reference date of a notice or of the deadline monitor is then
later than any day on which these tests can run, so a statement read at today's date instead of the reference date
sees no position as due and settles nothing.
"""
from __future__ import annotations

import re
from decimal import Decimal

import pytest

from tests.test_rent_changes import api_client, rent_db as rent_db, rent_world as rent_world  # noqa: F401

pytestmark = pytest.mark.asyncio

NOTHING_OVERDUE = (422, 'No overdue items found for this tenant in the given period')


async def new_lease(api, w, *, valid_from, rent, number):
    unit = await api.post('/api/units', json={'property_id': str(w['hidden_property']), 'number': number, 'usage_type': 'WOHNUNG'})
    assert unit.status_code == 201, unit.text
    lease = await api.post('/api/leases', json={
        'unit_id': unit.json()['id'], 'tenant_profile_id': str(w['tenant']), 'valid_from': valid_from,
        'base_rent': rent, 'advance_payment': '0.00'})
    assert lease.status_code == 201, lease.text
    account = await api.post('/api/bank-accounts', json={
        'property_id': str(w['hidden_property']), 'account_type': 'LAUFEND', 'label': f'Konto {number}',
        'iban': 'DE36302501010002042407'})
    assert account.status_code == 201, account.text
    return lease.json()['id'], account.json()['id']


async def pay(api, w, lease, account, amount, year, month, value_date):
    payment = await api.post('/api/payments', json={
        'bank_account_id': str(account), 'direction': 'IN', 'amount': amount, 'value_date': value_date,
        'payment_type': 'KALTMIETE', 'tenant_profile_id': str(w['tenant']), 'lease_agreement_id': str(lease),
        'period_year': year, 'period_month': month})
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


async def test_a_surplus_of_the_next_year_settles_the_rent_that_the_old_year_still_owes(rent_world):
    w = rent_world
    async with api_client(w) as api:
        # The lease starts on 1 December 2025 at 500.00 rent. December is unpaid: a notice over 2025 as of 15 January
        # 2026 demands 500.00.
        lease, account = await new_lease(api, w, valid_from='2025-12-01', rent='500.00', number='Year end')
        before = await notice(api, w, lease, '2025-01-01', '2025-12-31', '2026-01-15')
        assert (before.status_code, before.json()['principal_amount']) == (201, '500.00')
        # 1000.00 are paid for January 2026 (500.00 owed). The 500.00 over settle December, so the lease owes 1000.00 -
        # 1000.00 = 0.00 over both months, and there is nothing a notice over 2025 could demand. (The old reading, that
        # only looked at the months of the notice, still demanded the 500.00.)
        await pay(api, w, lease, account, '1000.00', 2026, 1, '2026-01-05')
        after = await notice(api, w, lease, '2025-01-01', '2025-12-31', '2026-01-15')
        assert (after.status_code, after.json()['detail']) == NOTHING_OVERDUE
        january = await notice(api, w, lease, '2026-01-01', '2026-01-31', '2026-01-15')
        assert (january.status_code, january.json()['detail']) == NOTHING_OVERDUE
        # The rent account of 2025 lists December as settled; that of January lists January's own 500.00.
        december = await api.get(f'/api/payments/mietkonto/lease/{lease}', params={
            'period_start': '2025-01-01', 'period_end': '2025-12-31', 'as_of': '2026-01-15'})
        row = next(row for row in december.json()['open_items'] if row['payment_type'] == 'KALTMIETE')
        assert (row['soll'], row['ist'], row['open_amount']) == ('500.00', '500.00', '0.00')
        assert Decimal(december.json()['due_balance']) == 0


async def test_a_notice_is_read_at_its_reference_date_and_not_at_today(rent_world):
    w = rent_world
    async with api_client(w) as api:
        # Rent 600.00 from 1 January 2098. April was paid 1800.00 (600.00 owed): 1200.00 over. Reference date 30 April
        # 2098: January to April are due. The 1200.00 settle January and February, the oldest; March is open with
        # 600.00 (a notice for January to March), April is paid.
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
