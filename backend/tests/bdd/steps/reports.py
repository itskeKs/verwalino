"""Property report journeys through the HTTP API."""
from decimal import Decimal

from pytest_bdd import given, parsers, then, when
from playwright.async_api import expect

from tests.bdd.steps.browser_common import browser_step, button, field
from tests.bdd.steps.browser_setup import bank_account, create, rental
from tests.bdd.steps.records import PROPERTY, record_of


@given('Gartenstraße 5 has a finalized asset report for 2026 with a declared bank balance of 500 euros')
def recipient_asset_report(world, seed_data):
    user = str(seed_data['user_a_admin_id'])
    home = create(world, '/api/properties', {
        'name': 'Gartenstraße 5', 'management_type': 'WEG',
        'data_audience': {'label': 'Gartenstraße 5', 'reason': 'Synthetic recipient check',
                          'grants': [{'user_id': user, 'capabilities': ['read', 'prepare', 'approve', 'export'],
                                      'reason': 'Synthetic recipient check'}]},
    }, keyed=True)
    scope = create(world, '/api/accounting-scopes', {
        'property_id': home['id'], 'scope_type': 'WEG', 'label': 'WEG Gartenstraße 5',
        'valid_from': '2026-01-01', 'valid_to': '2026-12-31', 'whole_property': True,
    }, keyed=True)
    create(world, f"/api/accounting-scopes/{scope['id']}/grants", {
        'user_id': user, 'capabilities': ['read', 'prepare', 'approve', 'confirm', 'export'],
        'reason': 'Synthetic recipient check',
    }, keyed=True)
    bank = create(world, '/api/bank-accounts', {
        'property_id': home['id'], 'accounting_scope_id': scope['id'],
        'account_type': 'LAUFEND', 'label': 'Girokonto',
    })
    report = create(world, '/api/weg-settlements/asset-reports', {
        'property_id': home['id'], 'accounting_scope_id': scope['id'],
        'period_start': '2026-01-01', 'period_end': '2026-12-31',
    })
    values = {'reserve_opening_balance': '0.00', 'reserve_override_reason': 'Synthetic opening balance checked',
              'receivables': [], 'payables': [],
              'bank_balances': [{'bank_account_id': bank['id'], 'label': 'Girokonto',
                                 'account_type': 'LAUFEND', 'balance': '500.00'}]}
    create(world, '/api/financial-input-coverage', {
        'subject_kind': 'settlement', 'subject_id': report['id'], 'values': values,
        'confirmed_complete': True, 'reason': 'Synthetic period records checked', 'source_document_ids': [],
    })
    response = world.act('POST', f"/api/weg-settlements/asset-reports/{report['id']}/finalize", json=values)
    assert response.status_code == 200, world.describe_response()
    world.data['recipient_report'] = response.json()


@when('the administrator downloads that asset report twice')
def download_recipient_asset_report(world):
    path = f"/api/weg-settlements/asset-reports/{world.data['recipient_report']['id']}/pdf"
    responses = [world.act('GET', path) for _ in range(2)]
    assert all(response.status_code == 200 for response in responses), world.describe_response()
    assert responses[0].content == responses[1].content
    world.data['recipient_pdf'] = responses[0].content


@then('the report explains administrator statements without development notes')
def recipient_explanations(world):
    from io import BytesIO
    import pdfplumber
    from tests.test_pdf_rendering import AUTHOR_MARKERS

    with pdfplumber.open(BytesIO(world.data['recipient_pdf'])) as pdf:
        text = ' '.join(' '.join(page.extract_text().split()) for page in pdf.pages)
    for marker in AUTHOR_MARKERS:
        assert marker not in text, marker
    for phrase in ('Gartenstraße 5', '31.12.2026', '500,00 EUR', '(Verwalter-Angabe)',
                   'manuelle Angaben der Verwaltung', 'kein automatischer Buchungssaldo'):
        assert phrase in text, phrase


@then('downloading leaves the finalized report unchanged')
def recipient_original_unchanged(world):
    original = world.data['recipient_report']
    response = world.act('GET', f"/api/weg-settlements/{original['id']}")
    assert response.status_code == 200, world.describe_response()
    assert response.json() == original


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
