"""Automated twin of the full-year rent account check in acceptance journey 2."""
import os
from pathlib import Path

from playwright.async_api import expect
from pytest_bdd import given, parsers, scenarios, then, when

from tests.bdd.steps.browser_common import button, field, ledger
from tests.bdd.steps.browser_setup import bank_account, rental

scenarios(str(Path(__file__).resolve().parents[3] / 'features/journeys/rent_account_due.feature'))


@given("Anna's full-year rent account has 100 euros unpaid through October")
def rent_account(world, seed_data):
    rental(world, seed_data)
    lease = world.data['leases'][0]
    response = world.act('PATCH', f"/api/leases/{lease['id']}", json={'base_rent': '750.00'})
    assert response.status_code == 200, world.describe_response()
    account = bank_account(world)
    for month in range(1, 11):
        for kind, amount in [('KALTMIETE', '650.00' if month == 4 else '750.00'),
                             ('NK_VORAUSZAHLUNG', '150.00')]:
            response = world.act('POST', '/api/payments', json={
                'bank_account_id': account['id'], 'direction': 'IN', 'amount': amount,
                'value_date': f'2026-{month:02d}-05', 'payment_type': kind,
                'tenant_profile_id': world.data['tenants'][0]['id'],
                'lease_agreement_id': lease['id'], 'period_year': 2026, 'period_month': month})
            assert response.status_code == 201, world.describe_response()
            response = world.act('POST', f"/api/payments/{response.json()['id']}/book")
            assert response.status_code == 200, world.describe_response()


@when(parsers.parse('the manager opens the full-year rent account at {width:d} px with cutoff 2026-11-01'))
def open_account(browser_world, width, tmp_path):
    async def run():
        await browser_world.page.set_viewport_size({'width': width, 'height': 1000})
        await ledger(browser_world)
        await browser_world.page.screenshot(path=str(tmp_path / f'initial-{width}.png'), full_page=True)
        await field(browser_world.page, 'Fällig am *').fill('2026-11-01')
        await button(browser_world.page, 'Abrufen').click()
    browser_world.run(run())


@then('the account keeps 1900 euros balance with 100 euros due and 1800 euros not yet due')
def november_account(browser_world, tmp_path):
    async def run():
        page = browser_world.page
        await browser_world.row('Saldo', '1900.00 EUR')
        await browser_world.row('davon fällig', '100.00 EUR (Rückstand)')
        await browser_world.row('noch nicht fällig', '1800.00 EUR')
        saldo = page.get_by_role('row').filter(has=page.get_by_role('cell', name='Saldo', exact=True))
        await expect(saldo).not_to_contain_text('Rückstand')
        await expect(field(page, 'Zeitraum bis *')).to_have_value('2026-12-31')
        assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        assert await field(page, 'Fällig am *').evaluate('e => {const r = e.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth}')
        directory = Path(__file__).resolve().parents[3] / 'docs/assets/design/rent-account-due' if os.getenv('VERWALINO_CAPTURE_DESIGN') == '1' else tmp_path
        directory.mkdir(parents=True, exist_ok=True)
        await field(page, 'Fällig am *').evaluate("e => e.scrollIntoView({block: 'start'})")
        await page.screenshot(path=str(directory / f'after-{page.viewport_size["width"]}.png'), full_page=True)
    browser_world.run(run())


@when('the manager changes the cutoff to 2026-11-04')
def change_cutoff(browser_world):
    async def run():
        page = browser_world.page
        await field(page, 'Fällig am *').fill('2026-11-04')
        await expect(page.get_by_role('cell', name='Saldo', exact=True)).to_have_count(0)
        await button(page, 'Abrufen').click()
    browser_world.run(run())


@then('the account keeps 1900 euros balance with 1000 euros due and 900 euros not yet due')
def on_due_date(browser_world):
    async def run():
        await browser_world.row('Saldo', '1900.00 EUR')
        await browser_world.row('davon fällig', '1000.00 EUR (Rückstand)')
        await browser_world.row('noch nicht fällig', '900.00 EUR')
    browser_world.run(run())
