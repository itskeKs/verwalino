"""Property report journeys through the HTTP API."""
from decimal import Decimal

from pytest_bdd import given, parsers, then, when
from playwright.async_api import expect

from tests.bdd.steps.browser_common import browser_step, button, field
from tests.bdd.steps.browser_setup import bank_account, create, rental
from tests.bdd.steps.records import PROPERTY, record_of


@when(parsers.parse('the user opens the Anlage V {view} for the property of "{organization}"'))
def open_foreign_anlage_v(world, view, organization):
    home = record_of(world, organization, PROPERTY)
    assert view in {'summary', 'download'}
    world.act('GET', '/api/reports/property-year/anlage-v' + ('.csv' if view == 'download' else ''),
              params={'property_id': home.id, 'year': 2026})


@given('a rental property with booked rent, advances and confirmed water costs')
def rental_year(world, seed_data):
    rental(world, seed_data)
    account = bank_account(world)
    for kind, amount in [('KALTMIETE', '1234.56'), ('NK_VORAUSZAHLUNG', '150.00')]:
        payment = create(world, '/api/payments', {
            'bank_account_id': account['id'],
            'lease_agreement_id': world.data['leases'][0]['id'],
            'tenant_profile_id': world.data['tenants'][0]['id'],
            'payment_type': kind, 'direction': 'IN', 'amount': amount,
            'value_date': '2026-06-01', 'period_year': 2026, 'period_month': 6,
        })
        response = world.act('POST', f"/api/payments/{payment['id']}/book")
        assert response.status_code == 200, world.describe_response()
    response = world.act('GET', '/api/cost-categories')
    assert response.status_code == 200, world.describe_response()
    category = next(row for row in response.json() if row['code'] == 'KALTWASSER')
    cost = create(world, '/api/cost-entries', {
        'property_id': world.data['property']['id'], 'accounting_scope_id': world.data['scope']['id'],
        'cost_category_id': category['id'], 'cost_category_plan_version': category['plan_version'],
        'invoice_date': '2026-06-01', 'service_period_from': '2026-01-01',
        'service_period_to': '2026-12-31', 'gross_amount': '100.03',
        'tenant_apportionable': 'UMLAGEFAEHIG', 'source': 'MANUAL',
    })
    response = world.act('POST', f"/api/cost-entries/{cost['id']}/confirm")
    assert response.status_code == 200, world.describe_response()
    world.data['report_query'] = {'property_id': world.data['property']['id'], 'year': 2026,
                                 'accounting_scope_id': world.data['scope']['id']}


@when('the landlord opens the Anlage V summary for that year')
def open_anlage_v(world):
    response = world.act('GET', '/api/reports/property-year/anlage-v', params=world.data['report_query'])
    assert response.status_code == 200, world.describe_response()
    world.data['anlage_v'] = response.json()


@then('rent, advances and water costs appear on their Anlage V lines')
def mapped_lines(world):
    lines = {row['key']: row['amount'] for row in world.data['anlage_v']['lines']}
    assert (lines['rent'], lines['levies'], lines['water']) == ('1234.56', '150.00', '100.03')


@then('mapped and unassigned amounts match the property year overview')
def matching_totals(world):
    response = world.act('GET', '/api/reports/property-year', params=world.data['report_query'])
    assert response.status_code == 200, world.describe_response()
    overview = response.json()
    summary = world.data['anlage_v']
    for reconciliation, total in zip(summary['reconciliation'], [overview['income_total'], overview['expense_total']]):
        section = reconciliation['section']
        mapped = sum((Decimal(row['amount']) for row in summary['lines'] if row['section'] == section), Decimal(0))
        unmapped = sum((Decimal(row['amount']) for row in summary['unmapped'] if row['section'] == section), Decimal(0))
        assert mapped + unmapped == Decimal(total)
        assert reconciliation['difference'] == '0.00'


@when('the landlord displays the property year overview in the browser')
@browser_step
async def display_year(browser_world):
    await browser_world.visit('/berichte')
    await browser_world.select('Objekt *', browser_world.world.data['property']['id'])
    await field(browser_world.page, 'Jahr *').fill('2026')
    await browser_world.scope()
    await button(browser_world.page, 'Übersicht anzeigen').click()


@then('the Anlage V mapping and reconciliation are readable on wide and narrow screens')
@browser_step
async def readable_mapping(browser_world):
    page = browser_world.page
    panel = page.get_by_role('region', name='Anlage V', exact=True)
    for width in (1280, 360):
        await page.set_viewport_size({'width': width, 'height': 900})
        await expect(panel.get_by_role('heading', name='Anlage V', exact=True)).to_be_visible()
        await expect(panel.get_by_role('table', name='Anlage V Einnahmen').get_by_role('cell', name='1.234,56 €', exact=True)).to_be_visible()
        await expect(panel.get_by_role('table', name='Anlage V Werbungskosten').get_by_role('cell', name='100,03 €', exact=True)).to_be_visible()
        await expect(panel.get_by_text('Keine nicht zugeordneten Beträge vorhanden.', exact=True)).to_be_visible()
        reconciliation = panel.get_by_role('table', name='Abgleich mit der Jahresübersicht')
        assert await reconciliation.get_by_role('cell', name='0,00 €', exact=True).count() == 4
        bounds = await panel.evaluate('(node) => ({left: node.getBoundingClientRect().left, right: node.getBoundingClientRect().right})')
        assert bounds['left'] >= 0 and bounds['right'] <= width


@then('the landlord can download the Anlage V CSV')
@browser_step
async def download_mapping(browser_world):
    page = browser_world.page
    async with page.expect_download() as pending:
        await page.get_by_role('link', name='Anlage V als CSV herunterladen', exact=True).click()
    download = await pending.value
    assert download.suggested_filename == 'anlage-v-2026.csv'
    assert await download.failure() is None
