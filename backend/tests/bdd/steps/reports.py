"""Property report journeys through the HTTP API."""
from decimal import Decimal
import csv
import io

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
    world.data['report_account'] = account
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


@given('a booked deposit of 2250 euros and a tenant backpayment of 75 euros')
def deposit_and_backpayment(world):
    deposit_account = bank_account(world, deposit=True)
    for kind, amount, account in [('KAUTION', '2250.00', deposit_account),
                                  ('NACHZAHLUNG', '75.00', world.data['report_account'])]:
        payment = create(world, '/api/payments', {
            'bank_account_id': account['id'], 'lease_agreement_id': world.data['leases'][0]['id'],
            'tenant_profile_id': world.data['tenants'][0]['id'], 'payment_type': kind,
            'direction': 'IN', 'amount': amount, 'value_date': '2026-06-01',
            'period_year': 2026, 'period_month': 6,
        })
        response = world.act('POST', f"/api/payments/{payment['id']}/book")
        assert response.status_code == 200, world.describe_response()


@then('the year overview and CSV show 1459.56 euros income excluding the deposit')
def deposit_excluded_from_headline(world):
    response = world.act('GET', '/api/reports/property-year', params=world.data['report_query'])
    assert response.status_code == 200, world.describe_response()
    body = response.json()
    assert body['income_total'] == '1459.56'
    assert body['balance'] == '1359.53'
    assert next(row['amount'] for row in body['income'] if row['payment_type'] == 'KAUTION') == '2250.00'
    response = world.act('GET', '/api/reports/property-year.csv', params=world.data['report_query'])
    assert response.status_code == 200, world.describe_response()
    rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
    assert next(row[-1] for row in rows if row[2] == 'Einnahmen gesamt') == '1459,56'
    assert next(row[-1] for row in rows if row[3] == 'KAUTION') == '2250,00'


@then('the deposit stays separate and the backpayment is included in levies')
def deposit_and_backpayment_mapping(world):
    summary = world.data['anlage_v']
    assert next(row['amount'] for row in summary['lines'] if row['key'] == 'levies') == '225.00'
    assert [(row['code'], row['amount']) for row in summary['unmapped']] == [('KAUTION', '2250.00')]
    response = world.act('GET', '/api/reports/property-year/anlage-v.csv', params=world.data['report_query'])
    assert response.status_code == 200, world.describe_response()
    rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
    assert next(row[-1] for row in rows if row[4] == 'Jahresübersicht gesamt' and row[2] == 'Einnahmen') == '1459,56'


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
        assert mapped + Decimal(reconciliation['unmapped_total']) == Decimal(total)
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


@given('the journey 5 rental receipts through 31 October 2026 and a separate 2250 euro deposit')
def journey_five_receipts(world, seed_data):
    rental(world, seed_data, count=2)
    account = bank_account(world)
    deposit_account = bank_account(world, deposit=True)
    for tenant, months, rent, advance in [(0, range(1, 11), '750.00', '150.00'),
                                          (1, range(3, 11), '600.00', '120.00')]:
        for month in months:
            for kind, amount in [('KALTMIETE', '650.00' if tenant == 0 and month == 4 else rent),
                                 ('NK_VORAUSZAHLUNG', advance)]:
                book_report_payment(world, account, kind, amount, tenant=tenant, month=month)
    book_report_payment(world, deposit_account, 'KAUTION', '2250.00')
    world.data['report_query'] = {'property_id': world.data['property']['id'], 'year': 2026,
                                 'accounting_scope_id': world.data['scope']['id']}


def book_report_payment(world, account, kind, amount, *, tenant=0, month=1):
    payment = create(world, '/api/payments', {
        'bank_account_id': account['id'], 'lease_agreement_id': world.data['leases'][tenant]['id'],
        'tenant_profile_id': world.data['tenants'][tenant]['id'], 'payment_type': kind,
        'direction': 'IN', 'amount': amount, 'value_date': f'2026-{month:02d}-01',
        'period_year': 2026, 'period_month': month,
    })
    response = world.act('POST', f"/api/payments/{payment['id']}/book")
    assert response.status_code == 200, world.describe_response()


@then('the overview shows 14660 euros income and the separate deposit on desktop and at 360 px')
@browser_step
async def journey_five_headline(browser_world):
    import os
    from pathlib import Path

    page = browser_world.page
    overview = page.get_by_role('region', name='Jahresübersicht', exact=True)
    await expect(overview).to_be_visible()
    await expect(page.get_by_role('region', name='Anlage V', exact=True).get_by_role('table', name='Abgleich mit der Jahresübersicht')).to_be_visible()
    for width in (1280, 360):
        await page.set_viewport_size({'width': width, 'height': 1000})
        if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1':
            directory = Path(__file__).resolve().parents[4] / 'docs/assets/design'
            await page.screenshot(path=str(directory / f'reports-income-{width}.png'), full_page=True, animations='disabled')
    for width in (1280, 360):
        await page.set_viewport_size({'width': width, 'height': 1000})
        await expect(overview.locator('dl')).to_contain_text('14.660,00 EUR')
        await expect(overview.get_by_role('table', name='Einnahmen', exact=True)).to_contain_text('2.250,00 EUR')
        panel = page.get_by_role('region', name='Anlage V', exact=True)
        await expect(panel.get_by_role('table', name='Anlage V Einnahmen')).to_contain_text('12.200,00 €')
        await expect(panel.get_by_role('table', name='Anlage V Einnahmen')).to_contain_text('2.460,00 €')
        await expect(panel.get_by_role('table', name='Abgleich mit der Jahresübersicht')).to_contain_text('14.660,00 €')
        assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')


@then('the downloaded overview and Anlage V CSV both show 14660 euros income')
@browser_step
async def journey_five_csv(browser_world):
    page = browser_world.page
    for name, label, column in [('CSV herunterladen', 'Einnahmen gesamt', 2),
                                ('Anlage V als CSV herunterladen', 'Jahresübersicht gesamt', 4)]:
        async with page.expect_download() as pending:
            await page.get_by_role('button' if column == 2 else 'link', name=name, exact=True).click()
        download = await pending.value
        assert await download.failure() is None
        from pathlib import Path
        rows = list(csv.reader(io.StringIO(Path(await download.path()).read_text(encoding='utf-8-sig')), delimiter=';'))
        assert next(row[-1] for row in rows if row[column] == label) == '14660,00'
