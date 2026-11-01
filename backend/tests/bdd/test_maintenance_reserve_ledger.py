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
    w = {'run': run['id'], 'owner_a': owner['id'], 'bank_giro': account['id'],
         'property': home['id'], 'scope': scope['id']}
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


@when('I prepare the next WEG year and asset report')
def next_year(world, reserve_scenario):
    w = reserve_scenario
    common = {'property_id': w['property'], 'accounting_scope_id': w['scope'],
              'period_start': '2026-01-01', 'period_end': '2026-12-31'}
    plan = create(world, '/api/economic-plans', {**common, 'plan_year': 2026})
    values = {'planned_costs': [], 'reserve_contribution_total': '6000.00'}
    world.run(attest_fixture(world.actor, 'economic_plan', plan['id'], values))
    response = world.act('POST', f"/api/economic-plans/{plan['id']}/finalize", json=values)
    assert response.status_code == 200, response.text
    w['next_run'] = create(world, '/api/weg-settlements', {**common, 'economic_plan_id': plan['id']})['id']
    w['next_report'] = create(world, '/api/weg-settlements/asset-reports', common)['id']


@then('a late receipt is shown separately with its original date on both reserve pages')
def visible_late_cash(world, reserve_scenario, browser_world, request):
    from playwright.async_api import expect
    from tests.bdd.report import artifact_directory, attach_artifact
    w = reserve_scenario
    directory = artifact_directory(request.node)

    async def inspect(stage):
        page = browser_world.page
        for width in (1280, 360):
            await page.set_viewport_size({'width': width, 'height': 1000})
            for prefix, path in [('weg', f"/weg-abrechnungen/{w['next_run']}"),
                                 ('asset', f"/vermoegensberichte/{w['next_report']}")]:
                await browser_world.visit(path)
                panel = page.get_by_role('region', name='Rücklagenbuch', exact=True)
                await expect(panel.get_by_text('Anfangsbestand', exact=True)).to_be_visible()
                label = panel.get_by_text('Nachträgliche Buchungen zum abgeschlossenen Jahr', exact=True)
                if stage == 'after':
                    await expect(label).to_be_visible()
                    await expect(panel.get_by_text('31.12.2025', exact=True)).to_be_visible()
                    await expect(panel.get_by_text('26.999,00', exact=False)).to_be_visible()
                    await expect(panel.locator(f'a[href="/zahlungen/{w["late_payment"]}"]')).to_be_visible()
                    await expect(panel.get_by_text('Eigentümer: Mustereigentümer mit langem Nachnamen', exact=True).first).to_be_visible()
                else:
                    await expect(label).to_have_count(0)
                reason = page.get_by_label('Begründung der Rücklagenwerte', exact=True)
                await expect(reason).to_be_visible()
                assert await reason.evaluate("(el) => !!el.closest('.form-grid') && el.clientHeight >= 60")
                assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                if directory:
                    # Capture the shared panel with all dated rows at each width.
                    await panel.scroll_into_view_if_needed()
                    filename = directory / f'{prefix}-{stage}-{width}.png'
                    await page.screenshot(path=str(filename), full_page=False)
                    attach_artifact(request.node, filename)
                    if stage == 'after':
                        await reason.scroll_into_view_if_needed()
                        filename = directory / f'{prefix}-form-{width}.png'
                        await page.screenshot(path=str(filename), full_page=False)
                        attach_artifact(request.node, filename)
                table = panel.locator('table')
                assert await table.evaluate('(table) => table.scrollWidth <= table.parentElement.clientWidth')
                assert await table.locator('td').evaluate_all('(cells) => cells.every(cell => cell.scrollWidth <= cell.clientWidth)')

    browser_world.run(inspect('before'))
    cash = create(world, '/api/payments', {'bank_account_id': w['bank_giro'], 'direction': 'IN',
        'amount': '999.00', 'value_date': '2025-12-31', 'payment_type': 'RUECKLAGENANTEIL',
        'owner_profile_id': w['owner_a'], 'period_year': 2025, 'period_month': 12})
    response = world.act('POST', f"/api/payments/{cash['id']}/book")
    assert response.status_code == 200, response.text
    w['late_payment'] = cash['id']
    browser_world.run(inspect('after'))

    meeting = create(world, '/api/meetings', {'property_id': w['property'], 'meeting_date': '2026-02-01',
        'meeting_type': 'ORDENTLICH'})
    response = world.act('POST', f"/api/meetings/{meeting['id']}/hold")
    assert response.status_code == 200, response.text
    resolution = create(world, '/api/meetings/resolutions', {'meeting_id': meeting['id'],
        'wording': 'Gartenreparatur aus der Erhaltungsrücklage', 'result': 'ANGENOMMEN',
        'votes_yes': 1, 'votes_no': 0, 'votes_abstain': 0})
    entry = create(world, f"/api/meetings/resolutions/{resolution['id']}/register", {})

    async def inspect_register():
        page = browser_world.page
        for width in (1280, 360):
            await page.set_viewport_size({'width': width, 'height': 1000})
            await browser_world.visit(f"/beschluss-sammlung?property_id={w['property']}#beschluss-{entry['id']}")
            row = page.locator(f'#beschluss-{entry["id"]}')
            await expect(row).to_contain_text('Gartenreparatur aus der Erhaltungsrücklage')
            await expect(row).to_be_in_viewport()

    browser_world.run(inspect_register())
