"""The issue #204 hand calculation through real HTTP and PDF output."""
from pathlib import Path

from pytest_bdd import given, when, then, scenarios

from tests.bdd.steps.browser_setup import create
from tests.bdd.steps.records import create_person
from tests.financial_coverage_helpers import attest_fixture
from tests.test_reserve_ledger import pdf_text

scenarios(str(Path(__file__).resolve().parents[3] / 'features/weg_settlement.feature'))


@given("Anna Berg's WEG plan is 7200 euros and her cost share is 6400 euros", target_fixture='weg_balance')
def weg_balance(world, seed_data):
    world.actor = world.administrator_of('Verwaltung Alpha')
    world.actor_organization = 'Verwaltung Alpha'
    user = str(seed_data['user_a_admin_id'])
    home = create(world, '/api/properties', {'name': 'WEG Gartenstraße 5',
        'management_type': 'WEG', 'mea_denominator': 1000,
        'data_audience': {'label': 'Gartenstraße', 'reason': 'Synthetic settlement',
            'grants': [{'user_id': user, 'capabilities': ['read', 'prepare', 'approve', 'export'],
                        'reason': 'Synthetic settlement'}]}}, keyed=True)
    owners = []
    for number, mea, name in [('1', '600', 'Anna Berg'), ('2', '400', 'Berta Vollständig')]:
        unit = create(world, '/api/units', {'property_id': home['id'], 'number': number,
            'usage_type': 'WOHNUNG', 'mea': mea, 'area': '100'})
        person = create_person(world, 'Verwaltung Alpha', name)
        owner = create(world, '/api/owner-profiles', {'person_id': person.id})
        create(world, '/api/ownership', {'unit_id': unit['id'], 'owner_profile_id': owner['id'],
            'valid_from': '2020-01-01'})
        owners.append(owner['id'])
    scope = create(world, '/api/accounting-scopes', {'property_id': home['id'], 'scope_type': 'WEG',
        'label': 'WEG Gartenstraße', 'valid_from': '2026-01-01', 'whole_property': True}, keyed=True)
    create(world, f"/api/accounting-scopes/{scope['id']}/grants", {'user_id': user,
        'capabilities': ['read', 'prepare', 'approve', 'confirm', 'export'], 'reason': 'Synthetic settlement'}, keyed=True)
    basis = {'property_id': home['id'], 'accounting_scope_id': scope['id']}
    account = create(world, '/api/bank-accounts', {**basis, 'account_type': 'LAUFEND', 'label': 'WEG Giro'})
    category = next(row for row in world.act('GET', '/api/cost-categories').json() if row['code'] == 'GARTENPFLEGE')
    cost = create(world, '/api/cost-entries', {**basis, 'cost_category_id': category['id'],
        'cost_category_plan_version': category['plan_version'], 'document_number': 'RE-GARTEN',
        'invoice_date': '2026-02-01', 'service_period_from': '2026-01-01',
        'service_period_to': '2026-12-31', 'gross_amount': '10666.67',
        'owner_distribution_key': 'MEA',
        'tenant_apportionable': 'NICHT_UMLAGEFAEHIG', 'source': 'MANUAL'})
    assert world.act('POST', f"/api/cost-entries/{cost['id']}/confirm").status_code == 200
    plan = create(world, '/api/economic-plans', {**basis, 'plan_year': 2026,
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    values = {'planned_costs': [{'category_code': 'GARTENPFLEGE', 'label': 'Bewirtschaftung',
        'planned_amount': '12000.00', 'cost_group': 'UMLAGEFAEHIG'}], 'reserve_contribution_total': '0.00'}
    world.run(attest_fixture(world.actor, 'economic_plan', plan['id'], values))
    response = world.act('POST', f"/api/economic-plans/{plan['id']}/finalize", json=values)
    assert response.status_code == 200, response.text
    run = create(world, '/api/weg-settlements', {**basis, 'economic_plan_id': plan['id'],
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    return {'run': run['id'], 'property': home['id'], 'bank': account['id'], 'owners': owners}


@given('Anna has booked 6600 euros of Hausgeld and the second owner has paid in full')
def paid_hausgeld(world, weg_balance):
    w = weg_balance
    for owner, amount in zip(w['owners'], ('6600.00', '4800.00')):
        payment = create(world, '/api/payments', {'bank_account_id': w['bank'], 'direction': 'IN',
            'owner_profile_id': owner, 'amount': amount, 'value_date': '2026-12-01',
            'payment_type': 'HAUSGELD_VORSCHUSS', 'period_year': 2026})
        response = world.act('POST', f"/api/payments/{payment['id']}/book")
        assert response.status_code == 200, response.text
    w['payments_before'] = world.act('GET', '/api/payments').json()


@when('I finalize their WEG annual settlement')
def finalize_balance(world, weg_balance):
    w = weg_balance
    # A manual figure cannot invent a payment or change the resulting balance.
    values = {'paid_advances': [{'owner_profile_id': owner, 'paid_total': '99999.00'} for owner in w['owners']]}
    world.run(attest_fixture(world.actor, 'settlement', w['run'], values))
    response = world.act('POST', f"/api/weg-settlements/{w['run']}/finalize", json=values)
    assert response.status_code == 200, response.text
    w['statement'] = response.json()


@then("Anna's resolved Spitze is minus 800 euros and her Hausgeld arrears are 600 euros")
def anna_spitze(weg_balance):
    w = weg_balance
    owner = next(row for row in w['statement']['owner_results'] if row['owner_profile_id'] == w['owners'][0])
    assert owner['plan_soll'] == '7200.00' and owner['paid'] == '6600.00'
    assert owner['abrechnungsspitze'] == '-800.00' and owner['account_balance'] == '600.00'
    w['anna'] = owner


@then("Anna's resulting payment is a 200 euro credit")
def anna_payment(weg_balance):
    assert weg_balance['anna']['payment_amount'] == '-200.00'


@then("the fully paid owner's payment equals the resolved Spitze")
def full_payment(weg_balance):
    owner = next(row for row in weg_balance['statement']['owner_results'] if row['owner_profile_id'] == weg_balance['owners'][1])
    assert owner['account_balance'] == '0.00'
    assert owner['payment_amount'] == owner['abrechnungsspitze']


@then('the PDF explains the plan, booked payments, arrears and resulting payment')
def balance_pdf(world, weg_balance):
    response = world.look(world.actor, f"/api/weg-settlements/{weg_balance['run']}/pdf")
    assert response.status_code == 200, response.text
    content = pdf_text(response.content)
    for expected in ('Abrechnungsspitze (beschlossen, § 28 Abs. 2 WEG)', 'Hausgeld-Soll laut Wirtschaftsplan',
                     'Hausgeld gezahlt', 'Rückstand / Guthaben aus Hausgeld', 'Zahlbetrag',
                     '-800,00', '7.200,00', '6.600,00', '600,00', '-200,00'):
        assert expected in content


@then('finalization has not changed the booked Hausgeld payments')
def unchanged_payments(world, weg_balance):
    assert world.act('GET', '/api/payments').json() == weg_balance['payments_before']


@then('the Einzelabrechnung shows all four amounts and the payment without horizontal scrolling')
def balance_browser(browser_world, weg_balance):
    import os
    from playwright.async_api import expect
    b = browser_world
    console_errors = []
    async def check():
        b.page.on('console', lambda message: console_errors.append(message.text) if message.type == 'error' else None)
        await b.visit(f"/weg-abrechnungen/{weg_balance['run']}")
        for width in (1280, 360):
            await b.page.set_viewport_size({'width': width, 'height': 1000})
            if width == 360:
                await b.page.wait_for_function("document.querySelector('aside').getBoundingClientRect().right <= 0")
            region = b.page.get_by_role('region', name='Hausgeld-Abgleich · Anna Berg', exact=True)
            await expect(region).to_be_visible()
            for label, value in (
                ('Abrechnungsspitze (beschlossen, § 28 Abs. 2 WEG)', '-800,00'),
                ('Hausgeld-Soll laut Wirtschaftsplan', '7.200,00'), ('Hausgeld gezahlt', '6.600,00'),
                ('Rückstand / Guthaben aus Hausgeld', '600,00'), ('Zahlbetrag', '-200,00')):
                row = region.locator('dl > div').filter(has=b.page.get_by_text(label, exact=True))
                await expect(row).to_contain_text(value)
                assert await row.evaluate('(el) => el.scrollWidth <= el.clientWidth')
            await expect(region).to_contain_text('Guthaben zugunsten des Eigentümers')
            assert await b.page.locator('main').evaluate('(el) => el.scrollWidth <= el.clientWidth'), 'Main content overflows'
            await region.scroll_into_view_if_needed()
            if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1':
                directory = Path(__file__).resolve().parents[3] / 'docs/assets/design/weg-owner-balance'
                directory.mkdir(parents=True, exist_ok=True)
                await b.page.screenshot(path=str(directory / f'after-{width}.png'))
        async with b.page.expect_popup() as opened:
            await b.page.get_by_role('button', name='PDF herunterladen', exact=True).click()
        popup = await opened.value
        await popup.wait_for_load_state()
        assert f"/{weg_balance['run']}/pdf" in popup.url
        await popup.close()
        assert not console_errors, console_errors
    b.run(check())
