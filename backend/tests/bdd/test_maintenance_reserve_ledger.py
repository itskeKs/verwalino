"""Bind the maintenance reserve happy path through the HTTP API."""
from pathlib import Path

from pytest_bdd import given, when, then, scenarios

from tests.bdd.steps.browser_setup import create
from tests.bdd.steps.records import create_person
from tests.financial_coverage_helpers import attest_fixture

scenarios(str(Path(__file__).resolve().parents[3] / 'features/maintenance_reserve_ledger.feature'))


@given('a WEG with a finalized reserve plan and a booked reserve contribution', target_fixture='reserve_scenario')
def reserve_scenario(world, seed_data):
    world.actor = world.administrator_of('Verwaltung Alpha')
    world.actor_organization = 'Verwaltung Alpha'
    client_a_admin = world.actor
    user = str(seed_data['user_a_admin_id'])
    home = create(world, '/api/properties', {'name': 'WEG Rücklagenbuch mit langer Objektbezeichnung',
        'management_type': 'WEG', 'mea_denominator': 1000,
        'data_audience': {'label': 'Reserve acceptance', 'reason': 'Synthetic reserve journey',
            'grants': [{'user_id': user, 'capabilities': ['read', 'prepare', 'approve', 'export'], 'reason': 'Synthetic reserve journey'}]}}, keyed=True)
    unit = create(world, '/api/units', {'property_id': home['id'], 'number': 'Wohnung 1', 'usage_type': 'WOHNUNG', 'mea': '1000', 'area': '100'})
    person = create_person(world, 'Verwaltung Alpha', 'Mustereigentümer mit langem Nachnamen')
    owner = create(world, '/api/owner-profiles', {'person_id': person.id})
    create(world, '/api/ownership', {'unit_id': unit['id'], 'owner_profile_id': owner['id'], 'valid_from': '2020-01-01'})
    scope = create(world, '/api/accounting-scopes', {'property_id': home['id'], 'scope_type': 'WEG',
        'label': 'WEG Rücklagenkreis', 'valid_from': '2025-01-01', 'whole_property': True}, keyed=True)
    create(world, f"/api/accounting-scopes/{scope['id']}/grants", {'user_id': user,
        'capabilities': ['read', 'prepare', 'approve', 'confirm', 'export'], 'reason': 'Synthetic reserve journey'}, keyed=True)
    account = create(world, '/api/bank-accounts', {'property_id': home['id'], 'accounting_scope_id': scope['id'],
        'account_type': 'LAUFEND', 'label': 'WEG Girokonto'})
    plan = create(world, '/api/economic-plans', {'property_id': home['id'], 'accounting_scope_id': scope['id'],
        'plan_year': 2025, 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    plan_values = {'planned_costs': [], 'reserve_contribution_total': '6000.00'}
    world.run(attest_fixture(client_a_admin, 'economic_plan', plan['id'], plan_values))
    response = world.act('POST', f"/api/economic-plans/{plan['id']}/finalize", json=plan_values)
    assert response.status_code == 200, response.text
    run = create(world, '/api/weg-settlements', {'property_id': home['id'], 'accounting_scope_id': scope['id'],
        'economic_plan_id': plan['id'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    w = {'run': run['id'], 'owner_a': owner['id'], 'bank_giro': account['id']}
    response = world.send(client_a_admin, 'POST', '/api/payments', json={
        'bank_account_id': w['bank_giro'], 'direction': 'IN', 'amount': '6000.00',
        'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENANTEIL',
        'owner_profile_id': w['owner_a'], 'period_year': 2025, 'period_month': 2})
    assert response.status_code == 201, response.text
    response = world.send(client_a_admin, 'POST', f"/api/payments/{response.json()['id']}/book")
    assert response.status_code == 200, response.text
    return w


@when('I review its reserve ledger and record the imported opening balance')
def review(world, reserve_scenario):
    client_a_admin = world.actor
    w = reserve_scenario
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    response = world.look(client_a_admin, path)
    assert response.status_code == 200, response.text
    assert response.json()['planned_contributions_total'] == '6000.00'
    assert response.json()['contributions_total'] == '6000.00'
    response = world.send(client_a_admin, 'POST', path + '/overrides', json={
        'component': 'OPENING', 'amount': '20000.00', 'reason': 'Imported opening confirmed against bank records'})
    assert response.status_code == 201, response.text


@when('I finalize the WEG settlement with the reviewed ledger')
def finalize(world, reserve_scenario):
    client_a_admin = world.actor
    w = reserve_scenario
    values = {'paid_advances': [{'owner_profile_id': w[key], 'paid_total': '0.00'} for key in ('owner_a',)]}
    world.run(attest_fixture(client_a_admin, 'settlement', w['run'], values))
    response = world.send(client_a_admin, 'POST', f"/api/weg-settlements/{w['run']}/finalize", json=values)
    assert response.status_code == 200, response.text


@then('the reserve closing balance includes the cash contribution')
def closing(world, reserve_scenario):
    client_a_admin = world.actor
    w = reserve_scenario
    response = world.look(client_a_admin, f"/api/weg-settlements/{w['run']}/reserve-ledger")
    assert response.status_code == 200, response.text
    assert response.json()['closing_balance'] == '26000.00'
    assert response.json()['frozen'] is True
    w['frozen'] = response.json()


@then('the finalized reserve ledger remains unchanged')
def frozen(world, reserve_scenario):
    client_a_admin = world.actor
    w = reserve_scenario
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    response = world.send(client_a_admin, 'POST', path + '/overrides', json={
        'component': 'ADJUSTMENT', 'amount': '1.00', 'reason': 'Must not change a frozen year'})
    assert response.status_code == 409, response.text
    assert world.look(client_a_admin, path).json() == w['frozen']
