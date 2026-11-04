"""The issue #204 hand calculation through real HTTP and PDF output."""
from pathlib import Path

import pytest
from pytest_bdd import given, when, then, scenario, scenarios

from tests.bdd.steps.browser_setup import create
from tests.bdd.steps.records import create_person
from tests.financial_coverage_helpers import attest_fixture
from tests.test_reserve_ledger import pdf_text
from tests.bdd.world import target_url

FEATURE = str(Path(__file__).resolve().parents[3] / 'features/weg_settlement.feature')


@pytest.mark.skipif(bool(target_url()), reason='ASGI response-start observation needs the in-process app and test DB')
@scenario(FEATURE, 'Saved WEG drafts are readable as soon as their success response starts')
def test_saved_weg_drafts_are_readable_as_soon_as_their_success_response_starts():
    pass


scenarios(FEATURE)


@when('I save ten 600 euro Hausgeld drafts dated 5 January 2026 and a new plan and annual settlement')
def immediately_readable_saves(world, weg_balance, app_rw_session, seed_data):
    from sqlalchemy import text
    from tests.write_response_support import create_visible_draft

    w = weg_balance
    world.run(app_rw_session.execute(text("SELECT set_config('app.current_org_id', :org, true)"),
                                     {'org': str(seed_data['org_a_id'])}))
    world.run(app_rw_session.execute(text("SELECT set_config('app.current_user_id', :actor, true)"),
                                     {'actor': str(seed_data['user_a_admin_id'])}))
    for _ in range(10):
        world.run(create_visible_draft(world.actor, app_rw_session, '/api/payments', {
            'bank_account_id': w['bank'],
            'owner_profile_id': w['owners'][0], 'direction': 'IN', 'amount': '600.00',
            'value_date': '2026-01-05', 'payment_type': 'HAUSGELD_VORSCHUSS'}, 'payment'))
    period = {'property_id': w['property'], 'accounting_scope_id': w['scope'],
              'period_start': '2026-01-01', 'period_end': '2026-12-31'}
    world.run(create_visible_draft(world.actor, app_rw_session, '/api/economic-plans',
                                  {**period, 'plan_year': 2026}, 'economic_plan'))
    world.run(create_visible_draft(world.actor, app_rw_session, '/api/weg-settlements',
                                  {**period, 'economic_plan_id': w['plan']}, 'settlement_run'))
    w['immediate_saves'] = 12


@then('every new detail is readable before its save response is cleaned up')
def all_saves_readable(weg_balance):
    assert weg_balance['immediate_saves'] == 12


@when('I save ten Hausgeld payments and a plan and annual settlement through the UI')
def saved_details_browser(browser_world, world, weg_balance):
    import re
    from playwright.async_api import expect

    b, w = browser_world, weg_balance
    details = []

    async def save_and_read(prefix):
        pattern = re.compile(rf'{prefix}/[0-9a-f-]+$')
        async with b.page.expect_response(lambda response: response.request.method == 'GET'
                                         and pattern.search(response.url)) as first_detail:
            await b.page.get_by_role('button', name='Anlegen', exact=True).click()
        response = await first_detail.value
        details.append(response.status)
        assert response.status == 200, await response.text()
        return (await response.json())['id']

    async def payments_and_plan():
        for _ in range(10):
            await b.visit('/zahlungen')
            await b.page.get_by_role('button', name='+ Neue Zahlung', exact=True).click()
            await b.select('Bankkonto *', w['bank'])
            await b.select('Zahlungstyp *', 'HAUSGELD_VORSCHUSS')
            await b.select('Eigentuemer', w['owners'][0])
            await b.page.get_by_label('Betrag *', exact=True).fill('600.00')
            await b.page.get_by_label('Wertstellungsdatum *', exact=True).fill('2026-01-05')
            await save_and_read('/api/payments')
            await expect(b.page.get_by_role('button', name='Buchen', exact=True)).to_be_visible()
            await expect(b.page.get_by_role('heading', name='Zahlung Entwurf')).to_be_visible()
            await expect(b.page.get_by_role('row', name='Betrag 600.00 EUR', exact=True)).to_be_visible()

        await b.visit('/wirtschaftsplaene')
        await b.page.get_by_role('button', name='+ Neuer Wirtschaftsplan', exact=True).click()
        await b.select('WEG-Objekt *', w['property'])
        await b.page.get_by_label('Wirtschaftsjahr *', exact=True).fill('2026')
        await b.select('Abrechnungskreis', w['scope'])
        await b.page.get_by_label('Zeitraum von *', exact=True).fill('2026-01-01')
        await b.page.get_by_label('Zeitraum bis *', exact=True).fill('2026-12-31')
        plan = await save_and_read('/api/economic-plans')
        await expect(b.page.get_by_role('heading', name='Wirtschaftsplan 2026 Entwurf')).to_be_visible()
        return plan

    plan = b.run(payments_and_plan())
    # Finalization is a prerequisite for the settlement, not the UI behavior
    # under test. Reuse the existing domain fixture and real API.
    values = {'planned_costs': [{'category_code': 'GARTENPFLEGE', 'label': 'Bewirtschaftung',
        'planned_amount': '12000.00', 'cost_group': 'UMLAGEFAEHIG'}], 'reserve_contribution_total': '0.00'}
    world.run(attest_fixture(world.actor, 'economic_plan', plan, values))
    finalized = world.act('POST', f'/api/economic-plans/{plan}/finalize', json=values)
    assert finalized.status_code == 200, finalized.text

    async def settlement():
        await b.visit('/weg-abrechnungen')
        await b.page.get_by_role('button', name='+ Neue WEG-Abrechnung', exact=True).click()
        await b.select('WEG-Objekt *', w['property'])
        await b.select('Wirtschaftsplan (Soll) *', plan)
        await save_and_read('/api/weg-settlements')
        await expect(b.page.get_by_role('heading', name='WEG-Jahresabrechnung Entwurf')).to_be_visible()

    b.run(settlement())
    w['first_ui_details'] = details


@then('all first detail requests succeeded and every payment offered booking')
def first_ui_details(weg_balance):
    assert weg_balance['first_ui_details'] == [200] * 12


@given("Anna Berg's WEG plan is 7200 euros and her cost share is 6400 euros", target_fixture='weg_balance')
def weg_balance(world, seed_data):
    return create_balance_world(world, seed_data)


def create_balance_world(world, seed_data, *, cost_period_end='2026-12-31'):
    world.actor = world.administrator_of('Verwaltung Alpha')
    world.actor_organization = 'Verwaltung Alpha'
    user = str(seed_data['user_a_admin_id'])
    home = create(world, '/api/properties', {'name': 'WEG Gartenstraße 5',
        'management_type': 'WEG', 'mea_denominator': 1000,
        'data_audience': {'label': 'Gartenstraße', 'reason': 'Synthetic settlement',
            'grants': [{'user_id': user, 'capabilities': ['read', 'prepare', 'approve', 'export'],
                        'reason': 'Synthetic settlement'}]}}, keyed=True)
    owners, units = [], []
    for number, mea, name in [('1', '600', 'Anna Berg'), ('2', '400', 'Berta Vollständig')]:
        unit = create(world, '/api/units', {'property_id': home['id'], 'number': number,
            'usage_type': 'WOHNUNG', 'mea': mea, 'area': '100'})
        units.append(unit['id'])
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
        'service_period_to': cost_period_end, 'gross_amount': '10666.67',
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
    return {'run': run['id'], 'property': home['id'], 'bank': account['id'], 'owners': owners,
            'units': units, 'plan': plan['id'], 'scope': scope['id']}


def owner_ledger(world, w, owner):
    response = world.look(world.actor, f'/api/payments/mietkonto/owner/{owner}?'
        f"period_start=2026-01-01&period_end=2026-12-31&economic_plan_id={w['plan']}&accounting_scope_id={w['scope']}")
    assert response.status_code == 200, response.text
    return response.json()


@given('Anna has booked 6600 euros of Hausgeld without an assigned year')
def yearless_hausgeld(world, weg_balance):
    w = weg_balance
    for owner, amount in zip(w['owners'], ('6600.00', '4800.00')):
        payment = create(world, '/api/payments', {'bank_account_id': w['bank'], 'direction': 'IN',
            'owner_profile_id': owner, 'amount': amount, 'value_date': '2026-12-01',
            'payment_type': 'HAUSGELD_VORSCHUSS'})
        response = world.act('POST', f"/api/payments/{payment['id']}/book")
        assert response.status_code == 200, response.text
    w['ledger_before'] = owner_ledger(world, w, w['owners'][0])
    assert {key: w['ledger_before'][key] for key in ('soll_total', 'ist_total', 'balance')} == {
        'soll_total': '7200.00', 'ist_total': '6600.00', 'balance': '600.00'}
    w['payments_before'] = world.act('GET', '/api/payments').json()


@given('a successor owns unit 2 from July after the plan was finalized and all costs belong to January through June',
       target_fixture='weg_balance')
def payment_only_successor(world, seed_data):
    w = create_balance_world(world, seed_data, cost_period_end='2026-06-30')
    response = world.act('GET', f"/api/ownership?unit_id={w['units'][1]}")
    assert response.status_code == 200, response.text
    old = next(row for row in response.json() if row['owner_profile_id'] == w['owners'][1])
    response = world.act('PATCH', f"/api/ownership/{old['id']}", json={'valid_to': '2026-06-30'})
    assert response.status_code == 200, response.text
    person = create_person(world, 'Verwaltung Alpha', 'Clara Nachfolgerin')
    owner = create(world, '/api/owner-profiles', {'person_id': person.id})
    create(world, '/api/ownership', {'unit_id': w['units'][1], 'owner_profile_id': owner['id'],
        'valid_from': '2026-07-01'})
    w['successor'] = owner['id']
    w['owners'].append(owner['id'])
    return w


@given('the successor has booked 1900 euros for December 2026 with a January 2027 value date')
def cross_year_successor_payment(world, weg_balance):
    w = weg_balance
    payment = create(world, '/api/payments', {'bank_account_id': w['bank'], 'direction': 'IN',
        'owner_profile_id': w['successor'], 'amount': '1900.00', 'value_date': '2027-01-05',
        'payment_type': 'HAUSGELD_VORSCHUSS', 'period_year': 2026, 'period_month': 12})
    response = world.act('POST', f"/api/payments/{payment['id']}/book")
    assert response.status_code == 200, response.text
    derived = world.act('POST', f"/api/weg-settlements/{w['run']}/paid-advances-derived")
    assert derived.status_code == 200, derived.text
    assert next(row['paid_total'] for row in derived.json() if row['owner_profile_id'] == w['successor']) == '1900.00'
    w['ledger_before'] = owner_ledger(world, w, w['successor'])
    assert {key: w['ledger_before'][key] for key in ('soll_total', 'ist_total', 'balance')} == {
        'soll_total': '0.00', 'ist_total': '1900.00', 'balance': '-1900.00'}
    w['payments_before'] = world.act('GET', '/api/payments').json()


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


@then("the owner's ledger is unchanged by finalization")
def unchanged_owner_ledger(world, weg_balance):
    w = weg_balance
    assert owner_ledger(world, w, w.get('successor', w['owners'][0])) == w['ledger_before']


@then("the finalized statement and PDF show the successor's 1900 euro credit with unit and ownership dates")
def successor_credit(world, weg_balance):
    w = weg_balance
    result = world.look(world.actor, f"/api/weg-settlements/{w['run']}").json()
    owner = next((row for row in result['owner_results'] if row['owner_profile_id'] == w['successor']), None)
    assert owner is not None, 'The finalized statement omitted the successor with a booked payment'
    assert {key: owner[key] for key in ('owner_cost_total', 'plan_soll', 'abrechnungsspitze',
                                      'paid', 'account_balance', 'payment_amount')} == {
        'owner_cost_total': '0.00', 'plan_soll': '0.00', 'abrechnungsspitze': '0.00',
        'paid': '1900.00', 'account_balance': '-1900.00', 'payment_amount': '-1900.00'}
    assert owner['ownership_periods'] == [{'unit_number': '2', 'valid_from': '2026-07-01', 'valid_to': '2026-12-31'}]
    originals = {row['owner_profile_id']: row for row in result['owner_results'] if row['owner_profile_id'] != w['successor']}
    for oid, cost, plan, spitze in zip(w['owners'][:2], ('6400.00', '4266.67'), ('7200.00', '4800.00'), ('-800.00', '-533.33')):
        assert originals[oid]['owner_cost_total'] == cost
        assert originals[oid]['plan_soll'] == plan and originals[oid]['abrechnungsspitze'] == spitze
        assert originals[oid]['paid'] == '0.00'
    response = world.look(world.actor, f"/api/weg-settlements/{w['run']}/pdf")
    assert response.status_code == 200, response.text
    content = pdf_text(response.content)
    assert 'Clara Nachfolgerin' in content
    block = content.split('Clara Nachfolgerin', 1)[1]
    end = min((block.index(name) for name in ('Anna Berg', 'Berta Vollständig') if name in block), default=len(block))
    block = block[:end]
    for expected in ('Hausgeld gezahlt', '1.900,00', '-1.900,00', 'Zahlbetrag', 'Einheit 2: 01.07.2026 bis 31.12.2026'):
        assert expected in block


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
