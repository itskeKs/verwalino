"""Prove the reserve-funded repair journey through the API and generated PDF."""
from pathlib import Path

from pytest_bdd import given, when, then, scenarios

from tests.bdd.steps.browser_setup import create
from tests.bdd.steps.records import create_person
from tests.financial_coverage_helpers import attest_fixture
from tests.test_reserve_ledger import pdf_text

scenarios(str(Path(__file__).resolve().parents[3] / 'features/reserve_funded_costs.feature'))


@given('a WEG with 600 and 400 MEA and 7000 euros of costs including a 1000 euro repair',
       target_fixture='funded_repair')
def funded_repair(world, seed_data):
    world.actor = world.administrator_of('Verwaltung Alpha')
    world.actor_organization = 'Verwaltung Alpha'
    user = str(seed_data['user_a_admin_id'])
    home = create(world, '/api/properties', {'name': 'WEG Dachreparatur',
        'management_type': 'WEG', 'mea_denominator': 1000,
        'data_audience': {'label': 'Dachreparatur', 'reason': 'Synthetic reserve financing',
            'grants': [{'user_id': user, 'capabilities': ['read', 'prepare', 'approve', 'export'],
                        'reason': 'Synthetic reserve financing'}]}}, keyed=True)
    owners = []
    for number, mea, name in [('1', '600', 'Anton Dach'), ('2', '400', 'Berta Dach')]:
        unit = create(world, '/api/units', {'property_id': home['id'], 'number': number,
            'usage_type': 'WOHNUNG', 'mea': mea, 'area': '100'})
        person = create_person(world, 'Verwaltung Alpha', name)
        owner = create(world, '/api/owner-profiles', {'person_id': person.id})
        create(world, '/api/ownership', {'unit_id': unit['id'], 'owner_profile_id': owner['id'],
            'valid_from': '2020-01-01'})
        owners.append(owner['id'])
    scope = create(world, '/api/accounting-scopes', {'property_id': home['id'], 'scope_type': 'WEG',
        'label': 'WEG Dach', 'valid_from': '2025-01-01', 'whole_property': True}, keyed=True)
    create(world, f"/api/accounting-scopes/{scope['id']}/grants", {'user_id': user,
        'capabilities': ['read', 'prepare', 'approve', 'confirm', 'export'],
        'reason': 'Synthetic reserve financing'}, keyed=True)
    basis = {'property_id': home['id'], 'accounting_scope_id': scope['id']}
    account = create(world, '/api/bank-accounts', {**basis, 'account_type': 'LAUFEND', 'label': 'WEG Giro'})
    categories = {row['code']: row for row in world.act('GET', '/api/cost-categories').json()}
    for code, amount in [('GARTENPFLEGE', '6000.00'), ('INSTANDSETZUNG', '1000.00')]:
        category = categories[code]
        cost = create(world, '/api/cost-entries', {**basis, 'cost_category_id': category['id'],
            'cost_category_plan_version': category['plan_version'], 'document_number': f'RE-{code}',
            'invoice_date': '2025-02-01', 'service_period_from': '2025-01-01',
            'service_period_to': '2025-12-31', 'gross_amount': amount,
            'owner_distribution_key': 'MEA', 'tenant_apportionable': 'NICHT_UMLAGEFAEHIG', 'source': 'MANUAL'})
        assert world.act('POST', f"/api/cost-entries/{cost['id']}/confirm").status_code == 200
    plan = create(world, '/api/economic-plans', {**basis, 'plan_year': 2025,
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    values = {'planned_costs': [{'category_code': 'GARTENPFLEGE', 'label': 'Bewirtschaftung',
        'planned_amount': '7000.00', 'cost_group': 'UMLAGEFAEHIG'}], 'reserve_contribution_total': '0.00'}
    world.run(attest_fixture(world.actor, 'economic_plan', plan['id'], values))
    assert world.act('POST', f"/api/economic-plans/{plan['id']}/finalize", json=values).status_code == 200
    run = create(world, '/api/weg-settlements', {**basis, 'economic_plan_id': plan['id'],
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    return {'run': run['id'], 'property': home['id'], 'bank_giro': account['id'],
            'owners': owners, 'cost': cost['id']}


@when('I book the repair payout from reserves with its receipt and resolution')
def book_repair(world, funded_repair):
    from tests.financial_coverage_helpers import reserve_evidence
    w = funded_repair
    evidence = world.run(reserve_evidence(world.actor, {**w, 'ce_garten': w['cost']}))
    payout = create(world, '/api/payments', {'bank_account_id': w['bank_giro'], 'direction': 'OUT',
        'amount': '1000.00', 'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENENTNAHME', **evidence})
    response = world.act('POST', f"/api/payments/{payout['id']}/book")
    assert response.status_code == 200, response.text
    w['payout'] = payout['id']


@when('I finalize the annual settlement for the funded repair')
def finalize_repair(world, funded_repair):
    w = funded_repair
    values = {'paid_advances': [{'owner_profile_id': owner, 'paid_total': '0.00'} for owner in w['owners']],
              'reserve_opening_balance': '5000.00', 'reserve_override_reason': 'Reconciled opening bank balance'}
    world.run(attest_fixture(world.actor, 'settlement', w['run'], values))
    response = world.act('POST', f"/api/weg-settlements/{w['run']}/finalize", json=values)
    assert response.status_code == 200, response.text
    w['statement'] = response.json()


@then('the owners are charged 3600 and 2400 euros')
def funded_owner_shares(funded_repair):
    w = funded_repair
    assert w['statement']['total_cost'] == '6000.00'
    for owner, amount in zip(w['owners'], ('3600.00', '2400.00')):
        line = next(row for row in w['statement']['lines']
                    if row['line_type'] == 'OWNER_COST_TOTAL' and row['addressee_owner_profile_id'] == owner)
        assert line['amount'] == amount


@then('the reserve ledger retains the 1000 euro withdrawal')
def funded_withdrawal(world, funded_repair):
    w = funded_repair
    ledger = world.look(world.actor, f"/api/weg-settlements/{w['run']}/reserve-ledger").json()
    assert ledger['frozen'] is True
    assert ledger['withdrawals_total'] == '1000.00'
    assert ledger['closing_balance'] == '4000.00'
    row = next(row for row in ledger['entries'] if row.get('payment_id') == w['payout'])
    assert row['cost_entry_id'] == w['cost'] and row['register_entry_id']


@then('the annual settlement PDF agrees with the cost shares and withdrawal')
def funded_pdf(world, funded_repair):
    response = world.look(world.actor, f"/api/weg-settlements/{funded_repair['run']}/pdf")
    assert response.status_code == 200, response.text
    content = pdf_text(response.content)
    for amount in ('3.600,00', '2.400,00', '1.000,00'):
        assert amount in content
