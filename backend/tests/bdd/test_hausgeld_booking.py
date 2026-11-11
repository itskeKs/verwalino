"""Booking the monthly Hausgeld of a finalized plan through the real API (#186)."""
from decimal import Decimal
from pathlib import Path

from pytest_bdd import given, scenarios, then, when

from tests.bdd.steps.browser_setup import create
from tests.bdd.steps.records import create_person
from tests.bdd.test_weg_settlement import create_balance_world

scenarios(str(Path(__file__).resolve().parents[3] / 'features/hausgeld_booking.feature'))


def offer(world, plan):
    answer = world.act('GET', f'/api/economic-plans/{plan}/hausgeld-booking')
    assert answer.status_code == 200, world.describe_response()
    return answer.json()


@given('Gartenstraße 5 has a finalized plan for 2026 with a monthly Hausgeld of 600 euros for Anna Berg',
       target_fixture='hausgeld')
def anna_plan(world, seed_data):
    return create_balance_world(world, seed_data)


@given('the second flat of Gartenstraße 5 belongs to Karl Weiß until 30 June 2026 and to Lena Roth from 1 July 2026',
       target_fixture='hausgeld')
def karl_and_lena(world, seed_data):
    w = create_balance_world(world, seed_data, second_owner='Karl Weiß', scope_start='2020-01-01')
    old = world.act('GET', f"/api/ownership?unit_id={w['units'][1]}").json()[0]
    answer = world.act('PATCH', f"/api/ownership/{old['id']}", json={'valid_to': '2026-06-30'})
    assert answer.status_code == 200, world.describe_response()
    person = create_person(world, 'Verwaltung Alpha', 'Lena Roth')
    buyer = create(world, '/api/owner-profiles', {'person_id': person.id})
    create(world, '/api/ownership', {'unit_id': w['units'][1], 'owner_profile_id': buyer['id'],
        'valid_from': '2026-07-01'})
    w['buyer'] = buyer['id']
    return w


@when('the manager books all twelve months of Anna Berg from the bank account with January on 5 January')
def book_year(world, hausgeld):
    anna = hausgeld['owners'][0]
    rows = [{'owner_profile_id': anna, 'period_year': 2026, 'period_month': month, 'amount': '600.00',
             'value_date': '2026-01-05' if month == 1 else f'2026-{month:02d}-01'} for month in range(1, 13)]
    answer = world.act('POST', f"/api/economic-plans/{hausgeld['plan']}/hausgeld-booking", json={
        'bank_account_id': hausgeld['bank'], 'rows': rows})
    assert answer.status_code == 201, world.describe_response()


def booked_of_anna(world, hausgeld):
    answer = world.act('GET', '/api/payments', params={'owner_profile_id': hausgeld['owners'][0],
        'payment_type': 'HAUSGELD_VORSCHUSS', 'status': 'booked'})
    assert answer.status_code == 200, world.describe_response()
    return answer.json()


@then('twelve booked payments of Anna Berg for 2026 exist with a total of 7200 euros')
def twelve_payments(world, hausgeld):
    payments = booked_of_anna(world, hausgeld)
    assert sorted((p['period_year'], p['period_month']) for p in payments) == [(2026, m) for m in range(1, 13)]
    assert sum(Decimal(p['amount']) for p in payments) == Decimal('7200.00')


@then('the January payment has the value date 5 January 2026')
def january_value_date(world, hausgeld):
    january = next(p for p in booked_of_anna(world, hausgeld) if p['period_month'] == 1)
    assert january['value_date'] == '2026-01-05'


@then('the Hausgeld booking offers no open month of Anna Berg any more')
def nothing_open(world, hausgeld):
    anna = [row for row in offer(world, hausgeld['plan']) if row['owner_profile_id'] == hausgeld['owners'][0]]
    assert len(anna) == 12 and all(row['booked'] for row in anna)


@when('the manager opens the Hausgeld booking of the plan', target_fixture='offered')
def open_booking(world, hausgeld):
    return offer(world, hausgeld['plan'])


@then('Karl Weiß is offered January to June and Lena Roth July to December, 400 euros each')
def transfer_offer(offered, hausgeld):
    karl, lena = hausgeld['owners'][1], hausgeld['buyer']
    months = {owner: {row['period_month']: row['amount'] for row in offered if row['owner_profile_id'] == owner}
              for owner in (hausgeld['owners'][0], karl, lena)}
    assert months[karl] == {month: '400.00' for month in range(1, 7)}
    assert months[lena] == {month: '400.00' for month in range(7, 13)}
    assert months[hausgeld['owners'][0]] == {month: '600.00' for month in range(1, 13)}
    assert not any(row['booked'] for row in offered)
