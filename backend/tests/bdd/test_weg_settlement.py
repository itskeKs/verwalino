"""The issue #204 hand calculation through real HTTP and PDF output."""
from decimal import Decimal
from pathlib import Path

import pytest
from pytest_bdd import given, when, then, scenario, scenarios, parsers

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
            await b.select('Eigentümer', w['owners'][0])
            await b.page.get_by_label('Betrag *', exact=True).fill('600.00')
            await b.page.get_by_label('Wertstellungsdatum *', exact=True).fill('2026-01-05')
            await save_and_read('/api/payments')
            await expect(b.page.get_by_role('button', name='Buchen', exact=True)).to_be_visible()
            await expect(b.page.get_by_role('heading', name='Zahlung Entwurf')).to_be_visible()
            await expect(b.page.get_by_role('row', name='Betrag 600,00 €', exact=True)).to_be_visible()

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


@given("Anna-Katharina Freifrau von Hohenzollern-Sigmaringen has Anna's WEG plan and costs", target_fixture='weg_balance')
def long_owner_balance(world, seed_data):
    return create_balance_world(world, seed_data, owner_name='Anna-Katharina Freifrau von Hohenzollern-Sigmaringen')


def create_balance_world(world, seed_data, *, cost_period_end='2026-12-31', owner_name='Anna Berg', second_owner='Berta Vollständig',
                         scope_start='2026-01-01', monthly_due_day=1, actual_cost='10666.67',
                         planned_cost='12000.00', planned_reserve='0.00'):
    world.actor = world.administrator_of('Verwaltung Alpha')
    world.actor_organization = 'Verwaltung Alpha'
    user = str(seed_data['user_a_admin_id'])
    home = create(world, '/api/properties', {'name': 'WEG Gartenstraße 5',
        'management_type': 'WEG', 'mea_denominator': 1000,
        'data_audience': {'label': 'Gartenstraße', 'reason': 'Synthetic settlement',
            'grants': [{'user_id': user, 'capabilities': ['read', 'prepare', 'approve', 'export'],
                        'reason': 'Synthetic settlement'}]}}, keyed=True)
    owners, units = [], []
    for number, mea, name in [('1', '600', owner_name), ('2', '400', second_owner)]:
        unit = create(world, '/api/units', {'property_id': home['id'], 'number': number,
            'usage_type': 'WOHNUNG', 'mea': mea, 'area': '100'})
        units.append(unit['id'])
        person = create_person(world, 'Verwaltung Alpha', name)
        owner = create(world, '/api/owner-profiles', {'person_id': person.id})
        create(world, '/api/ownership', {'unit_id': unit['id'], 'owner_profile_id': owner['id'],
            'valid_from': '2020-01-01'})
        owners.append(owner['id'])
    scope = create(world, '/api/accounting-scopes', {'property_id': home['id'], 'scope_type': 'WEG',
        'label': 'WEG Gartenstraße', 'valid_from': scope_start, 'whole_property': True}, keyed=True)
    create(world, f"/api/accounting-scopes/{scope['id']}/grants", {'user_id': user,
        'capabilities': ['read', 'prepare', 'approve', 'confirm', 'export'], 'reason': 'Synthetic settlement'}, keyed=True)
    basis = {'property_id': home['id'], 'accounting_scope_id': scope['id']}
    account = create(world, '/api/bank-accounts', {**basis, 'account_type': 'LAUFEND', 'label': 'WEG Giro'})
    category = next(row for row in world.act('GET', '/api/cost-categories').json() if row['code'] == 'GARTENPFLEGE')
    cost = create(world, '/api/cost-entries', {**basis, 'cost_category_id': category['id'],
        'cost_category_plan_version': category['plan_version'], 'document_number': 'RE-GARTEN',
        'invoice_date': '2026-02-01', 'service_period_from': '2026-01-01',
        'service_period_to': cost_period_end, 'gross_amount': actual_cost,
        'owner_distribution_key': 'MEA',
        'tenant_apportionable': 'NICHT_UMLAGEFAEHIG', 'source': 'MANUAL'})
    assert world.act('POST', f"/api/cost-entries/{cost['id']}/confirm").status_code == 200
    plan = create(world, '/api/economic-plans', {**basis, 'plan_year': 2026,
        'period_start': '2026-01-01', 'period_end': '2026-12-31', 'monthly_due_day': monthly_due_day})
    values = {'planned_costs': [{'category_code': 'GARTENPFLEGE', 'label': 'Bewirtschaftung',
        'planned_amount': planned_cost, 'cost_group': 'UMLAGEFAEHIG'}], 'reserve_contribution_total': planned_reserve}
    world.run(attest_fixture(world.actor, 'economic_plan', plan['id'], values))
    response = world.act('POST', f"/api/economic-plans/{plan['id']}/finalize", json=values)
    assert response.status_code == 200, response.text
    run = create(world, '/api/weg-settlements', {**basis, 'economic_plan_id': plan['id'],
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    world.data['levy_basis'] = basis
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
    record_sale_of_unit_2(world, w)
    return w


def record_sale_of_unit_2(world, w):
    """Berta's ownership of unit 2 ends 30.06.2026, Clara owns it from 01.07.2026."""
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
        'soll_total': '2400.00', 'ist_total': '1900.00', 'balance': '500.00'}
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
        'owner_cost_total': '0.00', 'plan_soll': '2400.00', 'abrechnungsspitze': '-2400.00',
        'paid': '1900.00', 'account_balance': '500.00', 'payment_amount': '-1900.00'}
    assert owner['ownership_periods'] == [{'unit_number': '2', 'valid_from': '2026-07-01', 'valid_to': '2026-12-31'}]
    originals = {row['owner_profile_id']: row for row in result['owner_results'] if row['owner_profile_id'] != w['successor']}
    for oid, cost, plan, spitze in zip(w['owners'][:2], ('6400.00', '4266.67'), ('7200.00', '2400.00'), ('-800.00', '1866.67')):
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
                await expect(b.page.locator("aside")).to_have_count(0)
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


@given('Karl Weiß owns unit 2 until June and Lena Roth owns it from July', target_fixture='dated_owners')
def dated_owners(world, seed_data):
    w = create_balance_world(world, seed_data, second_owner='Karl Weiß', scope_start='2020-01-01')
    old = world.act('GET', f"/api/ownership?unit_id={w['units'][1]}").json()[0]
    response = world.act('PATCH', f"/api/ownership/{old['id']}", json={'valid_to': '2026-06-30'})
    assert response.status_code == 200, response.text
    person = create_person(world, 'Verwaltung Alpha', 'Lena Roth')
    buyer = create(world, '/api/owner-profiles', {'person_id': person.id})
    create(world, '/api/ownership', {'unit_id': w['units'][1], 'owner_profile_id': buyer['id'],
        'valid_from': '2026-07-01'})
    w['buyer'] = buyer['id']
    return w


@when('I finalize the asset report for their whole year')
def dated_asset_report(world, dated_owners):
    w = dated_owners
    report = create(world, '/api/weg-settlements/asset-reports', {'property_id': w['property'],
        'accounting_scope_id': w['scope'], 'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    values = {'bank_balances': [{'bank_account_id': w['bank'], 'label': 'Giro',
        'account_type': 'LAUFEND', 'balance': '0.00'}], 'receivables': [], 'payables': []}
    world.run(attest_fixture(world.actor, 'settlement', report['id'], values))
    response = world.act('POST', f"/api/weg-settlements/asset-reports/{report['id']}/finalize", json=values)
    assert response.status_code == 200, response.text
    w['report'] = report['id']
    previous = create(world, '/api/weg-settlements/asset-reports', {'property_id': w['property'],
        'accounting_scope_id': w['scope'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    world.run(attest_fixture(world.actor, 'settlement', previous['id'], values))
    response = world.act('POST', f"/api/weg-settlements/asset-reports/{previous['id']}/finalize", json=values)
    assert response.status_code == 200, response.text
    w['previous_report'] = previous['id']
    plan = create(world, '/api/economic-plans', {'property_id': w['property'], 'accounting_scope_id': w['scope'],
        'plan_year': 2027, 'period_start': '2027-01-01', 'period_end': '2027-12-31'})
    plan_values = {'planned_costs': [{'category_code': 'GARTENPFLEGE', 'label': 'Bewirtschaftung',
        'planned_amount': '12000.00', 'cost_group': 'UMLAGEFAEHIG'}], 'reserve_contribution_total': '0.00'}
    world.run(attest_fixture(world.actor, 'economic_plan', plan['id'], plan_values))
    response = world.act('POST', f"/api/economic-plans/{plan['id']}/finalize", json=plan_values)
    assert response.status_code == 200, response.text
    w['next_plan'] = plan['id']

    import os
    directory = os.environ.get('VERWALINO_207_SCREENSHOTS')
    if directory:
        Path(directory).mkdir(parents=True, exist_ok=True)
        for label, path in [('plan', f"economic-plans/{w['plan']}"),
                            ('report', f"weg-settlements/asset-reports/{w['report']}")]:
            Path(directory, label + '.pdf').write_bytes(world.look(world.actor, '/api/' + path + '/pdf').content)


@then('the plan names Karl at the year start and the report names Lena at the year end')
def dated_document_recipients(world, dated_owners):
    w = dated_owners
    for path, name, key_date in [(f"economic-plans/{w['plan']}", 'Karl Weiß', '2026-01-01'),
                                  (f"weg-settlements/asset-reports/{w['report']}", 'Lena Roth', '2026-12-31'),
                                  (f"economic-plans/{w['next_plan']}", 'Lena Roth', '2027-01-01'),
                                  (f"weg-settlements/asset-reports/{w['previous_report']}", 'Karl Weiß', '2025-12-31')]:
        data = world.look(world.actor, '/api/' + path.replace('weg-settlements/asset-reports/', 'weg-settlements/')).json()
        recipients = [a for a in data['addressees'] if a['unit_id'] == w['units'][1]]
        assert len(recipients) == 1
        assert recipients[0]['owner_name'] == name and recipients[0]['key_date'] == key_date
        content = pdf_text(world.look(world.actor, f'/api/{path}/pdf').content)
        assert name in content
        assert 'Eigentümer am ' + '.'.join(reversed(key_date.split('-'))) in content
        assert ('Lena Roth' if name == 'Karl Weiß' else 'Karl Weiß') not in content


@then('Karl and Lena each owe six monthly Hausgeld advances of 400 euros')
def dated_owner_ledgers(world, dated_owners):
    w = dated_owners
    for owner, months in [(w['owners'][1], list(range(1, 7))), (w['buyer'], list(range(7, 13)))]:
        ledger = owner_ledger(world, w, owner)
        assert ledger['soll_total'] == '2400.00'
        assert [r['period_month'] for r in ledger['open_items']] == months
        assert all(r['soll'] == '400.00' for r in ledger['open_items'])


@then('their issued dunning notices each request only their six unpaid advances')
def dated_owner_dunning(world, dated_owners):
    w = dated_owners
    for owner, months in [(w['owners'][1], list(range(1, 7))), (w['buyer'], list(range(7, 13)))]:
        notice = create(world, '/api/dunning/owner', {
            'owner_profile_id': owner, 'economic_plan_id': w['plan'],
            'accounting_scope_id': w['scope'], 'period_start': '2026-01-01',
            'period_end': '2026-12-31', 'reference_date': '2027-01-15'})
        assert notice['principal_amount'] == '2400.00'
        assert [r['period_month'] for r in notice['included_items']] == months
        assert all(r['open_amount'] == '400.00' for r in notice['included_items'])
        issued = world.act('POST', f"/api/dunning/{notice['id']}/issue")
        assert issued.status_code == 200, issued.text
        assert issued.json()['status'] == 'issued'
        assert issued.json()['principal_amount'] == '2400.00'


@then('the annual settlement still lists both ownership periods')
def dated_annual_periods(world, dated_owners):
    w = dated_owners
    values = {'paid_advances': [{'owner_profile_id': oid, 'paid_total': '0.00'} for oid in [*w['owners'], w['buyer']]]}
    world.run(attest_fixture(world.actor, 'settlement', w['run'], values))
    response = world.act('POST', f"/api/weg-settlements/{w['run']}/finalize", json=values)
    assert response.status_code == 200, response.text
    results = {o['owner_profile_id']: o for o in response.json()['owner_results']}
    for oid, start, end in [(w['owners'][1], '2026-01-01', '2026-06-30'),
                             (w['buyer'], '2026-07-01', '2026-12-31')]:
        assert results[oid]['ownership_periods'] == [{'unit_number': '2', 'valid_from': start, 'valid_to': end}]


@given('Anna owns the whole unit until June and Bernd owns it from July with a 12000 euro plan and 12000 euro costs',
       target_fixture='dated_soll')
def dated_plan_soll_world(world, seed_data):
    user = str(seed_data['user_a_admin_id'])
    world.actor = world.administrator_of('Verwaltung Alpha')
    world.actor_organization = 'Verwaltung Alpha'
    home = create(world, '/api/properties', {'name': 'WEG Lindweg 1',
        'management_type': 'WEG', 'mea_denominator': 1000,
        'data_audience': {'label': 'Lindweg', 'reason': 'Synthetic settlement',
            'grants': [{'user_id': user, 'capabilities': ['read', 'prepare', 'approve', 'export'],
                        'reason': 'Synthetic settlement'}]}}, keyed=True)
    unit = create(world, '/api/units', {'property_id': home['id'], 'number': '1',
        'usage_type': 'WOHNUNG', 'mea': '1000', 'area': '100'})
    anna_person = create_person(world, 'Verwaltung Alpha', 'Anna Alt')
    anna = create(world, '/api/owner-profiles', {'person_id': anna_person.id})
    create(world, '/api/ownership', {'unit_id': unit['id'], 'owner_profile_id': anna['id'],
        'valid_from': '2020-01-01'})
    scope = create(world, '/api/accounting-scopes', {'property_id': home['id'], 'scope_type': 'WEG',
        'label': 'WEG Lindweg', 'valid_from': '2026-01-01', 'whole_property': True}, keyed=True)
    create(world, f"/api/accounting-scopes/{scope['id']}/grants", {'user_id': user,
        'capabilities': ['read', 'prepare', 'approve', 'confirm', 'export'], 'reason': 'Synthetic settlement'}, keyed=True)
    basis = {'property_id': home['id'], 'accounting_scope_id': scope['id']}
    account = create(world, '/api/bank-accounts', {**basis, 'account_type': 'LAUFEND', 'label': 'WEG Giro'})
    category = next(row for row in world.act('GET', '/api/cost-categories').json() if row['code'] == 'GARTENPFLEGE')
    cost = create(world, '/api/cost-entries', {**basis, 'cost_category_id': category['id'],
        'cost_category_plan_version': category['plan_version'], 'document_number': 'RE-LINDWEG',
        'invoice_date': '2026-02-01', 'service_period_from': '2026-01-01',
        'service_period_to': '2026-12-31', 'gross_amount': '12000.00',
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
    old = world.act('GET', f"/api/ownership?unit_id={unit['id']}").json()[0]
    response = world.act('PATCH', f"/api/ownership/{old['id']}", json={'valid_to': '2026-06-30'})
    assert response.status_code == 200, response.text
    bernd_person = create_person(world, 'Verwaltung Alpha', 'Bernd Neu')
    bernd = create(world, '/api/owner-profiles', {'person_id': bernd_person.id})
    create(world, '/api/ownership', {'unit_id': unit['id'], 'owner_profile_id': bernd['id'],
        'valid_from': '2026-07-01'})
    run = create(world, '/api/weg-settlements', {**basis, 'economic_plan_id': plan['id'],
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    return {'run': run['id'], 'property': home['id'], 'bank': account['id'], 'anna': anna['id'],
            'bernd': bernd['id'], 'owners': [anna['id'], bernd['id']], 'plan': plan['id'], 'scope': scope['id']}


@when('I compute their WEG annual settlement')
def compute_dated_soll(world, dated_soll):
    w = dated_soll
    values = {'paid_advances': [{'owner_profile_id': oid, 'paid_total': '0.00'} for oid in w['owners']]}
    world.run(attest_fixture(world.actor, 'settlement', w['run'], values))
    response = world.act('POST', f"/api/weg-settlements/{w['run']}/compute", json=values)
    assert response.status_code == 200, response.text
    w['statement'] = response.json()


@then("Anna's Hausgeld Soll is 6000 euros and her Abrechnungsspitze is minus 49,32 euros")
def anna_dated_soll(world, dated_soll):
    owner = next(o for o in dated_soll['statement']['owner_results']
                 if o['owner_profile_id'] == dated_soll['anna'])
    assert (owner['owner_cost_total'], owner['plan_soll'], owner['abrechnungsspitze']) == (
        '5950.68', '6000.00', '-49.32')


@then("Bernd's Hausgeld Soll is 6000 euros and his Abrechnungsspitze is 49,32 euros")
def bernd_dated_soll(world, dated_soll):
    owner = next(o for o in dated_soll['statement']['owner_results']
                 if o['owner_profile_id'] == dated_soll['bernd'])
    assert (owner['owner_cost_total'], owner['plan_soll'], owner['abrechnungsspitze']) == (
        '6049.32', '6000.00', '49.32')


@then('both document pages show their dated addressees at desktop and phone widths')
def dated_document_browser(browser_world, dated_owners):
    import os
    from playwright.async_api import expect
    b, w = browser_world, dated_owners
    async def check():
        for label, path, name, day in [('plan', f"/wirtschaftsplaene/{w['plan']}", 'Karl Weiß', '01.01.2026'),
                                        ('report', f"/vermoegensberichte/{w['report']}", 'Lena Roth', '31.12.2026')]:
            for width in (1280, 360):
                await b.page.set_viewport_size({'width': width, 'height': 1000})
                await b.visit('/wirtschaftsplaene' if label == 'plan' else '/vermoegensberichte')
                await b.page.locator(f'a[href="{path}"]').click()
                await expect(b.page.get_by_role('heading', name='Übersicht', exact=True)).to_be_visible()
                await b.page.wait_for_timeout(400)
                directory = os.environ.get('VERWALINO_207_SCREENSHOTS')
                if directory:
                    Path(directory).mkdir(parents=True, exist_ok=True)
                    await b.page.screenshot(path=str(Path(directory) / f'{label}-{width}.png'), full_page=True)
                region = b.page.get_by_role('region', name='Dokumentempfänger')
                await expect(region).to_contain_text(name)
                await expect(region).to_contain_text('Eigentümer am ' + day)
                assert await region.evaluate('(e) => e.scrollWidth <= e.clientWidth')
    b.run(check())
@when(parsers.parse('I prepare their {state} WEG annual settlement'))
def prepare_long_statement(world, weg_balance, state):
    assert state in ('computed', 'finalized')
    values = {'paid_advances': [{'owner_profile_id': owner, 'paid_total': amount}
                              for owner, amount in zip(weg_balance['owners'], ('6600.00', '4800.00'))]}
    world.run(attest_fixture(world.actor, 'settlement', weg_balance['run'], values))
    action = 'compute' if state == 'computed' else 'finalize'
    response = world.act('POST', f"/api/weg-settlements/{weg_balance['run']}/{action}", json=values)
    assert response.status_code == 200, response.text
    weg_balance.update(statement=response.json(), display_state=state)


@then("the long owner's statement and whole snapshot hash fit desktop and a 360 pixel phone")
def long_statement_browser(browser_world, world, weg_balance):
    import os
    from playwright.async_api import expect

    b, w = browser_world, weg_balance
    name = 'Anna-Katharina Freifrau von Hohenzollern-Sigmaringen'
    failures = []
    console_errors = []

    async def check():
        b.page.on('console', lambda message: console_errors.append(message.text) if message.type == 'error' else None)
        await b.visit(f"/weg-abrechnungen/{w['run']}")
        for width in (1280, 360):
            await b.page.set_viewport_size({'width': width, 'height': 1000})
            if width == 360:
                await expect(b.page.locator("aside")).to_have_count(0)
            heading = b.page.get_by_role('heading', name=name, exact=True)
            await expect(heading).to_be_visible()
            card = heading.locator('..')
            await expect(card.get_by_role('cell', name='6.400,00 €', exact=True).first).to_be_visible()
            await expect(card.get_by_role('region', name=f'Hausgeld-Abgleich · {name}')).to_contain_text('7.200,00')
            if w['statement']['snapshot_sha256']:
                digest = b.page.locator('code').filter(has_text=w['statement']['snapshot_sha256'])
                await expect(digest).to_be_visible()
                assert await digest.evaluate('''el => {
                    const range = document.createRange();
                    range.selectNodeContents(el);
                    const card = el.closest('.card').getBoundingClientRect();
                    return Array.from(range.getClientRects()).every(r => r.left >= card.left && r.right <= card.right);
                }'''), 'Snapshot hash extends beyond its card'
            if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1':
                directory = Path(__file__).resolve().parents[3] / 'docs/assets/design/weg-statements'
                directory.mkdir(parents=True, exist_ok=True)
                if w['display_state'] == 'finalized':
                    await digest.locator('..').locator('..').screenshot(path=str(directory / f'hash-after-{width}.png'))
                height = await card.evaluate('el => Math.ceil(el.getBoundingClientRect().height) + 200')
                await b.page.set_viewport_size({'width': width, 'height': max(1000, height)})
                await card.screenshot(path=str(directory / f"{w['display_state']}-after-{width}.png"))
                await b.page.set_viewport_size({'width': width, 'height': 1000})
            if not await b.page.locator('main').evaluate('(el) => el.scrollWidth <= el.clientWidth'):
                failures.append(f'{width}: main content overflows')
            for table in await card.locator('table').all():
                if not await table.locator('..').evaluate("el => ['auto', 'scroll'].includes(getComputedStyle(el).overflowX)"):
                    failures.append(f'{width}: statement has no horizontal scroll container')
                amount = table.get_by_role('cell', name='6.400,00 €', exact=True).first
                if await amount.evaluate('''el => new Set(Array.from(el.firstChild.textContent, (_, i) => {
                    const range = document.createRange();
                    range.setStart(el.firstChild, i); range.setEnd(el.firstChild, i + 1);
                    return range.getBoundingClientRect().top;
                })).size > 1'''):
                    failures.append(f'{width}: amount breaks across lines')
                await table.locator('..').evaluate('el => { el.scrollLeft = el.scrollWidth; }')
                assert await amount.evaluate('''el => {
                    const cell = el.getBoundingClientRect(), container = el.closest('table').parentElement.getBoundingClientRect();
                    return cell.left >= container.left && cell.right <= container.right + 1;
                }'''), 'Full amount cannot be reached inside the statement card'
            if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1' and width == 360:
                await b.page.set_viewport_size({'width': width, 'height': max(1000, height)})
                await card.screenshot(path=str(directory / f"{w['display_state']}-amount-after-{width}.png"))
                await b.page.set_viewport_size({'width': width, 'height': 1000})
        assert not failures, failures
        assert not console_errors and not b.errors, (console_errors, b.errors)

    b.run(check())
    assert world.look(world.actor, f"/api/weg-settlements/{w['run']}").json() == w['statement']


@given('Anna, Karl and Lena have booked their twelve, six and six monthly Hausgeld payments')
def dated_paid_hausgeld(world, dated_owners):
    w = dated_owners
    for owner, months, amount in [(w['owners'][0], range(1, 13), '600.00'),
                                  (w['owners'][1], range(1, 7), '400.00'),
                                  (w['buyer'], range(7, 13), '400.00')]:
        for month in months:
            payment = create(world, '/api/payments', {'bank_account_id': w['bank'], 'direction': 'IN',
                'owner_profile_id': owner, 'amount': amount, 'value_date': f'2026-{month:02d}-05',
                'payment_type': 'HAUSGELD_VORSCHUSS', 'period_year': 2026, 'period_month': month})
            response = world.act('POST', f"/api/payments/{payment['id']}/book")
            assert response.status_code == 200, response.text


@when('I take over their paid Hausgeld from the booked figures')
def take_over_dated_payments(world, dated_owners):
    w = dated_owners
    response = world.act('POST', f"/api/weg-settlements/{w['run']}/paid-advances-derived")
    assert response.status_code == 200, response.text
    w['derived'] = response.json()


@then('Anna, Karl and Lena have paid 7200, 2400 and 2400 euros')
def exact_dated_payments(world, dated_owners):
    w = dated_owners
    assert {r['owner_profile_id']: r['paid_total'] for r in w['derived']} == {
        w['owners'][0]: '7200.00', w['owners'][1]: '2400.00', w['buyer']: '2400.00'}
    assert set(world.act('GET', f"/api/weg-settlements/{w['run']}/input-owners").json()) == {
        row['owner_profile_id'] for row in w['derived']}


@then("clearing Lena's paid amount asks for her missing Hausgeld in German")
def missing_lena_payment(world, dated_owners):
    w = dated_owners
    values = {'paid_advances': w['derived']}
    world.run(attest_fixture(world.actor, 'settlement', w['run'], values))
    body = {'paid_advances': [{**row, 'paid_total': ''} if row['owner_profile_id'] == w['buyer'] else row
                             for row in w['derived']]}
    response = world.act('POST', f"/api/weg-settlements/{w['run']}/compute", json=body)
    assert response.status_code == 422, response.text
    detail = response.json()['detail']
    assert isinstance(detail, dict), detail
    assert detail['code'] == 'financial_inputs_incomplete'
    assert detail['blockers'][0]['code'] == 'owner_payment_coverage_missing'
    assert detail['owner_profile_id'] == w['buyer'] and detail['field'] == 'paid_total'
    assert 'Lena Roth' in detail['message'] and 'Hausgeld' in detail['message']


@then("the paid Hausgeld prefill and Lena's missing amount are visible on desktop and phone")
def paid_hausgeld_browser(browser_world, world, dated_owners, tmp_path):
    import os
    from playwright.async_api import expect
    b, w = browser_world, dated_owners
    # Coverage is a prerequisite; the UI action under test is prefill and compute.
    values = {'paid_advances': [{'owner_profile_id': oid, 'paid_total': amount} for oid, amount in
        [(w['owners'][0], '7200.00'), (w['owners'][1], '2400.00'), (w['buyer'], '2400.00')]]}
    world.run(attest_fixture(world.actor, 'settlement', w['run'], values))

    async def check():
        for width in (1280, 360):
            await b.page.set_viewport_size({'width': width, 'height': 1000})
            await b.visit(f"/weg-abrechnungen/{w['run']}")
            await b.page.get_by_role('button', name='Aus Zahlenstand übernehmen').click()
            for name, amount in [('Anna Berg', '7200.00'), ('Karl Weiß', '2400.00'), ('Lena Roth', '2400.00')]:
                await expect(b.page.get_by_role('textbox', name=f'Tatsächlich gezahltes Hausgeld · {name}')).to_have_value(amount)
            await b.page.get_by_role('textbox', name='Tatsächlich gezahltes Hausgeld · Lena Roth').fill('')
            await b.page.get_by_role('button', name='Berechnen', exact=True).click()
            alert = b.page.locator('.card').filter(has_text='Fehler:')
            await expect(alert).to_be_visible()
            await alert.scroll_into_view_if_needed()
            directory = Path(os.environ.get('VERWALINO_183_SCREENSHOTS', str(tmp_path)))
            directory.mkdir(parents=True, exist_ok=True)
            await b.page.screenshot(path=str(directory / f'paid-hausgeld-{width}.png'), full_page=True)
        await expect(alert).to_contain_text('Lena Roth')
        await expect(alert).to_contain_text('Hausgeld')
    b.run(check())


@given('the WEG plan has 7000 euros of costs and 6000 euros of reserve for owners with 600 and 400 MEA',
       target_fixture='weg_balance')
def planned_reserve_balance(world, seed_data):
    return create_balance_world(world, seed_data, actual_cost='7000.00',
                                planned_cost='7000.00', planned_reserve='6000.00')


@given('a WEG with one unit and one owner has Gebäudeversicherung of 3650 euros for July 2024 through June 2025',
       target_fixture='cross_year_weg')
def cross_year_insurance_weg(world, seed_data):
    user = str(seed_data['user_a_admin_id'])
    world.actor = world.administrator_of('Verwaltung Alpha')
    world.actor_organization = 'Verwaltung Alpha'
    home = create(world, '/api/properties', {'name': 'WEG Bergstraße 3',
        'management_type': 'WEG', 'mea_denominator': 1000,
        'data_audience': {'label': 'Bergstraße', 'reason': 'Synthetic settlement',
            'grants': [{'user_id': user, 'capabilities': ['read', 'prepare', 'approve', 'export'],
                        'reason': 'Synthetic settlement'}]}}, keyed=True)
    unit = create(world, '/api/units', {'property_id': home['id'], 'number': '1',
        'usage_type': 'WOHNUNG', 'mea': '1000', 'area': '100'})
    person = create_person(world, 'Verwaltung Alpha', 'Theo Einfach')
    owner = create(world, '/api/owner-profiles', {'person_id': person.id})
    create(world, '/api/ownership', {'unit_id': unit['id'], 'owner_profile_id': owner['id'],
        'valid_from': '2020-01-01'})
    scope = create(world, '/api/accounting-scopes', {'property_id': home['id'], 'scope_type': 'WEG',
        'label': 'WEG Bergstraße', 'valid_from': '2024-01-01', 'whole_property': True}, keyed=True)
    create(world, f"/api/accounting-scopes/{scope['id']}/grants", {'user_id': user,
        'capabilities': ['read', 'prepare', 'approve', 'confirm', 'export'], 'reason': 'Synthetic settlement'}, keyed=True)
    basis = {'property_id': home['id'], 'accounting_scope_id': scope['id']}
    category = next(row for row in world.act('GET', '/api/cost-categories').json()
                    if row['code'] == 'GEBAEUDEVERSICHERUNG')
    cost = create(world, '/api/cost-entries', {**basis, 'cost_category_id': category['id'],
        'cost_category_plan_version': category['plan_version'], 'document_number': 'RE-VERSICHERUNG',
        'invoice_date': '2024-07-01', 'service_period_from': '2024-07-01',
        'service_period_to': '2025-06-30', 'gross_amount': '3650.00',
        'owner_distribution_key': 'MEA',
        'tenant_apportionable': 'NICHT_UMLAGEFAEHIG', 'source': 'MANUAL'})
    assert world.act('POST', f"/api/cost-entries/{cost['id']}/confirm").status_code == 200
    runs = {}
    for year in (2024, 2025):
        plan = create(world, '/api/economic-plans', {**basis, 'plan_year': year,
            'period_start': f'{year}-01-01', 'period_end': f'{year}-12-31'})
        values = {'planned_costs': [{'category_code': 'GEBAEUDEVERSICHERUNG', 'label': 'Gebäudeversicherung',
            'planned_amount': '3650.00', 'cost_group': 'UMLAGEFAEHIG'}], 'reserve_contribution_total': '0.00'}
        world.run(attest_fixture(world.actor, 'economic_plan', plan['id'], values))
        response = world.act('POST', f"/api/economic-plans/{plan['id']}/finalize", json=values)
        assert response.status_code == 200, response.text
        run = create(world, '/api/weg-settlements', {**basis, 'economic_plan_id': plan['id'],
            'period_start': f'{year}-01-01', 'period_end': f'{year}-12-31'})
        runs[year] = run['id']
    return {'owner': owner['id'], 'runs': runs}


@when('I compute the WEG annual settlements for 2024 and 2025')
def compute_cross_year_settlements(world, cross_year_weg):
    w = cross_year_weg
    for year, run_id in w['runs'].items():
        values = {'paid_advances': [{'owner_profile_id': w['owner'], 'paid_total': '0.00'}]}
        world.run(attest_fixture(world.actor, 'settlement', run_id, values))
        response = world.act('POST', f"/api/weg-settlements/{run_id}/compute", json=values)
        assert response.status_code == 200, response.text
        w[f'statement_{year}'] = response.json()


@then('the 2024 settlement charges 1840 euros of Gesamtkosten and the 2025 settlement charges 1810 euros')
def cross_year_total_costs(world, cross_year_weg):
    assert cross_year_weg['statement_2024']['total_cost'] == '1840.00'
    assert cross_year_weg['statement_2025']['total_cost'] == '1810.00'


@then("the owner's share is 1840 euros in the 2024 settlement and 1810 euros in the 2025 settlement")
def cross_year_owner_share(world, cross_year_weg):
    w = cross_year_weg
    for year, amount in ((2024, '1840.00'), (2025, '1810.00')):
        owner = next(row for row in w[f'statement_{year}']['owner_results']
                     if row['owner_profile_id'] == w['owner'])
        assert owner['owner_cost_total'] == amount


@then('both settlements together charge exactly 3650 euros')
def cross_year_sum_exact(world, cross_year_weg):
    total = sum((Decimal(cross_year_weg[f'statement_{year}']['total_cost']) for year in (2024, 2025)),
                Decimal('0.00'))
    assert total == Decimal('3650.00')


@then('both owners have zero Spitze with reserve Soll of 3600 and 2400 euros and Hausgeld Soll of 7800 and 5200 euros')
def planned_reserve_has_no_spitze(world, weg_balance):
    w = weg_balance
    for owner_id, reserve, advance in zip(w['owners'], ('3600.00', '2400.00'), ('7800.00', '5200.00')):
        result = next(o for o in w['statement']['owner_results'] if o['owner_profile_id'] == owner_id)
        assert result['abrechnungsspitze'] == '0.00'
        assert result['plan_soll'] == advance
        line = next(ln for ln in w['statement']['lines']
                    if ln['line_type'] == 'RESERVE_SOLL' and ln['addressee_owner_profile_id'] == owner_id)
        assert line['amount'] == reserve
    response = world.act('GET', f"/api/weg-settlements/{w['run']}/pdf")
    assert response.status_code == 200
    assert 'zuzueglich Ruecklagenzufuehrung laut Wirtschaftsplan (Soll)' in pdf_text(response.content)


@given(parsers.parse('Anna owns unit 1 alone and Bernd and Carla own unit 2 with shares of {bernd_share} and {carla_share}, '
                     'both units of 500 MEA with 1000 euros of costs by MEA'), target_fixture='co_owned')
def co_owned_units_world(world, seed_data, bernd_share, carla_share):
    user = str(seed_data['user_a_admin_id'])
    world.actor = world.administrator_of('Verwaltung Alpha')
    world.actor_organization = 'Verwaltung Alpha'
    home = create(world, '/api/properties', {'name': 'WEG Birkenweg 7',
        'management_type': 'WEG', 'mea_denominator': 1000,
        'data_audience': {'label': 'Birkenweg', 'reason': 'Synthetic settlement',
            'grants': [{'user_id': user, 'capabilities': ['read', 'prepare', 'approve', 'export'],
                        'reason': 'Synthetic settlement'}]}}, keyed=True)
    units = {number: create(world, '/api/units', {'property_id': home['id'], 'number': number,
        'usage_type': 'WOHNUNG', 'mea': '500', 'area': '100'}) for number in ('1', '2')}
    owners, entries = {}, {}
    for name, number, share in (('Anna Alt', '1', None), ('Bernd Bunt', '2', bernd_share),
                                ('Carla Clever', '2', carla_share)):
        person = create_person(world, 'Verwaltung Alpha', name)
        owner = create(world, '/api/owner-profiles', {'person_id': person.id})
        body = {'unit_id': units[number]['id'], 'owner_profile_id': owner['id'], 'valid_from': '2020-01-01'}
        entries[name.split()[0]] = create(world, '/api/ownership', {**body, 'mea_share': share} if share else body)
        owners[name.split()[0]] = owner['id']
    scope = create(world, '/api/accounting-scopes', {'property_id': home['id'], 'scope_type': 'WEG',
        'label': 'WEG Birkenweg', 'valid_from': '2026-01-01', 'whole_property': True}, keyed=True)
    create(world, f"/api/accounting-scopes/{scope['id']}/grants", {'user_id': user,
        'capabilities': ['read', 'prepare', 'approve', 'confirm', 'export'], 'reason': 'Synthetic settlement'}, keyed=True)
    basis = {'property_id': home['id'], 'accounting_scope_id': scope['id']}
    category = next(row for row in world.act('GET', '/api/cost-categories').json() if row['code'] == 'GARTENPFLEGE')
    cost = create(world, '/api/cost-entries', {**basis, 'cost_category_id': category['id'],
        'cost_category_plan_version': category['plan_version'], 'document_number': 'RE-BIRKENWEG',
        'invoice_date': '2026-02-01', 'service_period_from': '2026-01-01',
        'service_period_to': '2026-12-31', 'gross_amount': '1000.00',
        'owner_distribution_key': 'MEA',
        'tenant_apportionable': 'NICHT_UMLAGEFAEHIG', 'source': 'MANUAL'})
    assert world.act('POST', f"/api/cost-entries/{cost['id']}/confirm").status_code == 200
    plan = create(world, '/api/economic-plans', {**basis, 'plan_year': 2026,
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    values = {'planned_costs': [{'category_code': 'GARTENPFLEGE', 'label': 'Bewirtschaftung',
        'planned_amount': '1000.00', 'cost_group': 'UMLAGEFAEHIG'}], 'reserve_contribution_total': '0.00'}
    world.run(attest_fixture(world.actor, 'economic_plan', plan['id'], values))
    response = world.act('POST', f"/api/economic-plans/{plan['id']}/finalize", json=values)
    assert response.status_code == 200, response.text
    run = create(world, '/api/weg-settlements', {**basis, 'economic_plan_id': plan['id'],
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    world.data['levy_basis'] = basis
    return {'run': run['id'], 'owners': owners, 'plan': plan['id'], 'entries': entries, 'unit_2': units['2']['id']}


def compute_co_owned(world, w):
    values = {'paid_advances': [{'owner_profile_id': oid, 'paid_total': '0.00'} for oid in w['owners'].values()]}
    world.run(attest_fixture(world.actor, 'settlement', w['run'], values))
    return world.act('POST', f"/api/weg-settlements/{w['run']}/compute", json=values)


@when('I compute the WEG annual settlement of the co-owned units')
def compute_co_owned_units(world, co_owned):
    response = compute_co_owned(world, co_owned)
    assert response.status_code == 200, response.text
    co_owned['statement'] = response.json()


@when('I try to compute the WEG annual settlement of the co-owned units')
def try_compute_co_owned_units(world, co_owned):
    co_owned['response'] = compute_co_owned(world, co_owned)


@then('the calculation is refused in German, names unit 2 and points to correcting its shares')
def co_owned_refusal_points_to_correction(co_owned):
    response = co_owned['response']
    assert response.status_code == 422, response.text
    detail = response.json()['detail']
    assert (detail['code'], detail['unit_number'], detail['share_sum']) == ('weg_co_owner_shares_mismatch', '2', '80')
    assert 'Einheit 2' in detail['message'] and '„Korrigieren“' in detail['message']


@when("I correct Carla's share of unit 2 to 50")
def correct_carlas_share(world, co_owned):
    entry = co_owned['entries']['Carla']
    response = world.act('POST', f"/api/ownership/{entry['id']}/correction",
                         json={'valid_from': entry['valid_from'], 'mea_share': '50'})
    assert response.status_code == 201, response.text
    co_owned['replacement'] = response.json()


@then("the history of unit 2 shows Carla's first entry as corrected, with who corrected it and when")
def history_shows_the_correction(world, co_owned):
    response = world.act('GET', '/api/ownership', params={'unit_id': co_owned['unit_2']})
    assert response.status_code == 200, response.text
    rows = {row['id']: row for row in response.json()}
    first, replacement = rows[co_owned['entries']['Carla']['id']], rows[co_owned['replacement']['id']]
    assert Decimal(first['mea_share']) == 30 and first['corrected_at'] and first['corrected_by_name']
    assert Decimal(replacement['mea_share']) == 50 and replacement['corrected_at'] is None
    assert len(rows) == 3  # Bernd, Carla's first entry and its replacement


@then("Anna's cost share is 500 euros and Bernd's and Carla's are 250 euros each")
def co_owned_cost_shares(co_owned):
    costs = {name: next(o for o in co_owned['statement']['owner_results']
                        if o['owner_profile_id'] == oid)['owner_cost_total']
             for name, oid in co_owned['owners'].items()}
    assert costs == {'Anna': '500.00', 'Bernd': '250.00', 'Carla': '250.00'}


@then('the calculation is refused in German because the shares of unit 2 do not add up')
def co_owned_refusal(co_owned):
    response = co_owned['response']
    assert response.status_code == 422, response.text
    detail = response.json()['detail']
    assert (detail['code'], detail['unit_number'], detail['share_sum']) == ('weg_co_owner_shares_mismatch', '2', '0.9')
    assert 'Einheit 2' in detail['message'] and 'statt 1 oder 100' in detail['message']


@then("the plan of the same owners still gave Bernd 333.33 euros and Carla 166.67 euros of unit 2's 500 euros")
def co_owned_plan_still_normalises(world, co_owned):
    page = world.act('GET', f"/api/economic-plans/{co_owned['plan']}").json()
    advances = {line['owner_profile_id']: line['planned_amount']
                for line in page['lines'] if line['line_type'] == 'OWNER_ADVANCE'}
    assert advances == {co_owned['owners']['Anna']: '500.00', co_owned['owners']['Bernd']: '333.33',
                        co_owned['owners']['Carla']: '166.67'}



# ---------------------------------------------------------------------------
# #405: a special levy is an additional Soll of the owner
# ---------------------------------------------------------------------------
MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
          'November', 'December')


def iso_day(text):
    day, month, year = text.split()
    return f'{int(year):04d}-{MONTHS.index(month) + 1:02d}-{int(day):02d}'


def book_payment(world, w, owner, amount, payment_type, value_date, **period):
    payment = create(world, '/api/payments', {'bank_account_id': w['bank'], 'direction': 'IN',
        'owner_profile_id': owner, 'amount': amount, 'value_date': value_date,
        'payment_type': payment_type, **period})
    response = world.act('POST', f"/api/payments/{payment['id']}/book")
    assert response.status_code == 200, response.text


def owner_row(statement, owner):
    return next(row for row in statement['owner_results'] if row['owner_profile_id'] == owner)


def levy_figure(statement, owner, key='levy_soll'):
    """The owner's levy figure; a statement without levy figures reports none, which is zero."""
    return Decimal(owner_row(statement, owner).get(key) or '0.00')


def levy_soll(statement, owner):
    return levy_figure(statement, owner)


@given('the WEG plan has 7000 euros of costs and the roof repair cost another 5000 euros', target_fixture='weg_balance')
def roof_repair_balance(world, seed_data):
    return create_balance_world(world, seed_data, actual_cost='12000.00', planned_cost='7000.00')


@given('Anna and Berta have paid their Hausgeld in full')
def hausgeld_in_full(world, weg_balance):
    w = weg_balance
    for owner, amount in zip(w['owners'], ('4200.00', '2800.00')):
        book_payment(world, w, owner, amount, 'HAUSGELD_VORSCHUSS', '2026-12-01', period_year=2026)


@given(parsers.parse('a resolved special levy of {amount:d} euros is due on {due}'))
def resolved_levy(world, amount, due):
    basis = world.data['levy_basis']
    meeting = create(world, '/api/meetings', {'property_id': basis['property_id'], 'meeting_date': '2026-02-10',
        'meeting_type': 'ORDENTLICH', 'location': 'Gemeinschaftsraum'})
    agenda = create(world, f"/api/meetings/{meeting['id']}/agenda", {'meeting_id': meeting['id'], 'title': 'Dachreparatur'})
    assert world.act('POST', f"/api/meetings/{meeting['id']}/hold").status_code == 200
    resolution = create(world, '/api/meetings/resolutions', {'meeting_id': meeting['id'], 'agenda_item_id': agenda['id'],
        'wording': f'Dachreparatur durch Sonderumlage von {amount} € finanzieren.',
        'votes_yes': 2, 'votes_no': 0, 'votes_abstain': 0, 'result': 'ANGENOMMEN'})
    world.data['levy'] = create(world, '/api/special-levies', {**basis, 'amount': f'{amount}.00',
        'due_date': iso_day(due), 'resolution_id': resolution['id']})


@given(parsers.parse('Anna has paid {anna:d} euros of the levy and Berta {berta:d} euros'))
def levy_payments(world, weg_balance, anna, berta):
    w = weg_balance
    for owner, amount in zip(w['owners'], (anna, berta)):
        if amount:
            book_payment(world, w, owner, f'{amount}.00', 'SONDERZAHLUNG', '2026-03-05', period_year=2026, period_month=3)


@given("Berta's ownership entry was corrected to start on 1 June 2019")
def berta_entry_corrected(world, weg_balance):
    w = weg_balance
    rows = world.act('GET', '/api/ownership', params={'unit_id': w['units'][1]}).json()
    entry = next(row for row in rows if row['owner_profile_id'] == w['owners'][1])
    response = world.act('POST', f"/api/ownership/{entry['id']}/correction",
                         json={'valid_from': '2019-06-01', 'mea_share': entry['mea_share']})
    assert response.status_code == 201, response.text


@when('I compute the WEG annual settlement with the levy')
def compute_with_levy(world, weg_balance):
    w = weg_balance
    values = {'paid_advances': [{'owner_profile_id': owner, 'paid_total': '0.00'} for owner in w['owners']]}
    world.run(attest_fixture(world.actor, 'settlement', w['run'], values))
    response = world.act('POST', f"/api/weg-settlements/{w['run']}/compute", json=values)
    assert response.status_code == 200, response.text
    w['statement'] = response.json()


@then(parsers.parse("Anna's Spitze is {anna} euros and Berta's is {berta} euros"))
def levy_spitzen(weg_balance, anna, berta):
    w = weg_balance
    assert [owner_row(w['statement'], owner)['abrechnungsspitze'] for owner in w['owners']] == [
        f'{Decimal(anna):.2f}', f'{Decimal(berta):.2f}']
    # The owners' Spitzen add up to the run's total: costs - plan Soll - levies.
    assert Decimal(w['statement']['balance']) == Decimal(anna) + Decimal(berta)


@then(parsers.parse('their levy Soll is {anna_soll} and {berta_soll} euros and paid {anna_paid} and {berta_paid} euros'))
def levy_figures(weg_balance, anna_soll, berta_soll, anna_paid, berta_paid):
    w = weg_balance
    assert [levy_figure(w['statement'], owner) for owner in w['owners']] == [Decimal(anna_soll), Decimal(berta_soll)]
    assert [levy_figure(w['statement'], owner, 'levy_paid') for owner in w['owners']] == [
        Decimal(anna_paid), Decimal(berta_paid)]


@then(parsers.parse("Anna's payment is {anna} euros and Berta's is {berta} euros"))
def levy_payment_amounts(weg_balance, anna, berta):
    w = weg_balance
    assert [owner_row(w['statement'], owner)['payment_amount'] for owner in w['owners']] == [
        f'{Decimal(anna):.2f}', f'{Decimal(berta):.2f}']


@then('the PDF shows the levy Soll and payment of Anna and Berta')
def levy_pdf(world, weg_balance):
    w = weg_balance
    w['pdf'] = world.look(world.actor, f"/api/weg-settlements/{w['run']}/pdf").content
    content = pdf_text(w['pdf'])
    for expected in ('Sonderumlage-Soll', 'Sonderumlage gezahlt', 'Rückstand / Guthaben aus Sonderumlage',
                     '3.000,00', '2.000,00', 'Sonderumlage: Soll aus den beschlossenen Sonderumlagen'):
        assert expected in content
    w['frozen'] = world.look(world.actor, f"/api/weg-settlements/{w['run']}").json()


@when('Anna pays another 100 euros of the levy')
def levy_late_payment(world, weg_balance):
    w = weg_balance
    book_payment(world, w, w['owners'][0], '100.00', 'SONDERZAHLUNG', '2026-04-02', period_year=2026, period_month=4)


@then('the finalized statement, its digest and its PDF are unchanged')
def levy_statement_frozen(world, weg_balance):
    w = weg_balance
    reloaded = world.look(world.actor, f"/api/weg-settlements/{w['run']}").json()
    assert reloaded['owner_results'] == w['frozen']['owner_results']
    assert reloaded['snapshot_sha256'] == w['frozen']['snapshot_sha256']
    assert world.look(world.actor, f"/api/weg-settlements/{w['run']}/pdf").content == w['pdf']


@then(parsers.parse('the levy Soll is 3000 euros for Anna, {berta:d} euros for Berta and {clara:d} euros for her successor Clara'))
def levy_follows_the_due_date_owner(weg_balance, berta, clara):
    w = weg_balance
    anna_id, berta_id, clara_id = w['owners']
    assert [levy_soll(w['statement'], owner) for owner in (anna_id, berta_id, clara_id)] == [
        Decimal('3000.00'), Decimal(berta), Decimal(clara)]


@then('the levy Soll is 500 euros for Anna and 250 euros each for Bernd and Carla')
def co_owners_share_the_levy(co_owned):
    soll = {name: levy_soll(co_owned['statement'], owner) for name, owner in co_owned['owners'].items()}
    assert soll == {'Anna': 500, 'Bernd': 250, 'Carla': 250}


@then('the levy Soll is 3000 euros for Anna and 2000 euros once for Berta')
def corrected_entry_counts_once(weg_balance):
    w = weg_balance
    assert [levy_soll(w['statement'], owner) for owner in w['owners']] == [Decimal('3000.00'), Decimal('2000.00')]
    assert sum(levy_soll(w['statement'], owner) for owner in w['owners']) == Decimal('5000.00')


@then("Anna's statement shows the special levy block without horizontal scrolling")
def levy_block_browser(browser_world, weg_balance):
    import os
    from playwright.async_api import expect

    b = browser_world
    console_errors = []

    async def check():
        b.page.on('console', lambda message: console_errors.append(message.text) if message.type == 'error' else None)
        await b.visit(f"/weg-abrechnungen/{weg_balance['run']}")
        for width in (1280, 360):
            await b.page.set_viewport_size({'width': width, 'height': 1000})
            region = b.page.get_by_role('region', name='Hausgeld-Abgleich · Anna Berg', exact=True)
            await expect(region).to_be_visible()
            for label, value in (
                ('Abrechnungsspitze (beschlossen, § 28 Abs. 2 WEG)', '0,00'), ('Sonderumlage-Soll', '3.000,00'),
                ('Sonderumlage gezahlt', '3.000,00'), ('Rückstand / Guthaben aus Sonderumlage', '0,00'),
                ('Zahlbetrag', '0,00')):
                row = region.locator('dl > div').filter(has=b.page.get_by_text(label, exact=True))
                await expect(row).to_contain_text(value)
                assert await row.evaluate('(el) => el.scrollWidth <= el.clientWidth')
            assert await b.page.locator('main').evaluate('(el) => el.scrollWidth <= el.clientWidth'), 'Main content overflows'
            if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1':
                directory = Path(__file__).resolve().parents[3] / 'docs/assets/design/weg-special-levy'
                directory.mkdir(parents=True, exist_ok=True)
                card = region.locator('..')
                height = await card.evaluate('el => Math.ceil(el.getBoundingClientRect().height) + 200')
                await b.page.set_viewport_size({'width': width, 'height': max(1000, height)})
                await card.screenshot(path=str(directory / f'after-{width}.png'))
                await b.page.set_viewport_size({'width': width, 'height': 1000})
        assert not console_errors and not b.errors, (console_errors, b.errors)

    b.run(check())


@given('the sale of unit 2 from Berta to Clara from July is recorded afterwards')
def sale_recorded_after_the_levy(world, weg_balance):
    record_sale_of_unit_2(world, weg_balance)


@given('Clara has paid 2000 euros of the levy in October')
def successor_levy_payment(world, weg_balance):
    w = weg_balance
    book_payment(world, w, w['successor'], '2000.00', 'SONDERZAHLUNG', '2026-10-05', period_year=2026, period_month=10)


@when('I try to compute the WEG annual settlement with the levy')
def try_compute_with_levy(world, weg_balance):
    w = weg_balance
    values = {'paid_advances': [{'owner_profile_id': owner, 'paid_total': '0.00'} for owner in w['owners']]}
    world.run(attest_fixture(world.actor, 'settlement', w['run'], values))
    w['response'] = world.act('POST', f"/api/weg-settlements/{w['run']}/compute", json=values)


@then('the calculation is refused in German, naming the 5.000,00 euro levy, unit 2 and 1 October 2026')
def levy_owner_refusal(world, weg_balance):
    w = weg_balance
    response = w['response']
    assert response.status_code == 422, response.text
    detail = response.json()['detail']
    assert {key: detail[key] for key in ('code', 'special_levy_id', 'unit_id', 'unit_number', 'due_date')} == {
        'code': 'weg_levy_owner_mismatch', 'special_levy_id': world.data['levy']['id'], 'unit_id': w['units'][1],
        'unit_number': '2', 'due_date': '2026-10-01'}
    for expected in ('5.000,00 €', 'Einheit 2', '01.10.2026', 'vor der Erfassung des Eigentümerwechsels'):
        assert expected in detail['message']
