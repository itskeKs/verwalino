"""Resolved Hausgeld dates through the real API and owner-notice PDF."""
from calendar import monthrange
import os
from pathlib import Path

from playwright.async_api import expect
from pytest_bdd import given, when, then, parsers, scenarios

from tests.settlement_period_support import create, period_property
from tests.settlement_period_support import period_data_cleanup  # noqa: F401
from tests.financial_coverage_helpers import attest_fixture
from tests.test_reserve_ledger import pdf_text

scenarios(str(Path(__file__).resolve().parents[3] / 'features/economic_plan.feature'))


def month_params(month):
    year, number = map(int, month.split('-'))
    return {'period_start': f'{month}-01', 'period_end': f'{month}-{monthrange(year, number)[1]}'}


@given('a finalized plan with annual Hausgeld of 1000 euros for one owner')
def annual_plan(world, period_data_cleanup):
    world.actor = world.administrator_of('Verwaltung Alpha')
    data = world.run(period_property(world.actor, month=1))
    plan = world.run(create(world.actor, '/api/economic-plans', {
        'property_id': data['property']['id'], 'plan_year': 2026}))
    inputs = {'planned_costs': [{'category_code': 'GARTENPFLEGE', 'label': 'Garten',
        'planned_amount': '1000.00'}], 'reserve_contribution_total': '0.00'}
    world.run(attest_fixture(world.actor, 'economic_plan', plan['id'], inputs))
    answer = world.act('POST', f"/api/economic-plans/{plan['id']}/finalize", json=inputs)
    assert answer.status_code == 200, world.describe_response()
    world.data['hausgeld'] = {**data, 'plan': answer.json()}


@when("the manager reads the owner's full-year ledger for 2026")
def annual_owner_ledger(world):
    data = world.data['hausgeld']
    answer = world.act('GET', f"/api/payments/mietkonto/owner/{data['owner']['id']}", params={
        'period_start': '2026-01-01', 'period_end': '2026-12-31',
        'economic_plan_id': data['plan']['id']})
    assert answer.status_code == 200, world.describe_response()


@then('the total expected Hausgeld is 1000 euros')
def annual_hausgeld_total(world):
    assert world.response.json()['soll_total'] == '1000.00'


@then('the first four monthly installments are 83.34 euros and the remaining eight are 83.33 euros')
def annual_hausgeld_installments(world):
    assert [(item['period_year'], item['period_month'], item['soll'])
        for item in world.response.json()['open_items']] == [
            (2026, month, '83.34' if month <= 4 else '83.33') for month in range(1, 13)]


@given(parsers.parse('a finalized plan with monthly Hausgeld of 600 euros and due day {day}'))
def resolved_plan(world, period_data_cleanup, day):
    world.actor = world.administrator_of('Verwaltung Alpha')
    data = world.run(period_property(world.actor, month=1))
    body = {'property_id': data['property']['id'], 'plan_year': 2026}
    if day != 'default':
        body['monthly_due_day'] = int(day)
    plan = world.run(create(world.actor, '/api/economic-plans', body))
    inputs = {'planned_costs': [{'category_code': 'GARTENPFLEGE', 'label': 'Garten',
        'planned_amount': '7200.00'}], 'reserve_contribution_total': '0.00'}
    world.run(attest_fixture(world.actor, 'economic_plan', plan['id'], inputs))
    answer = world.act('POST', f"/api/economic-plans/{plan['id']}/finalize", json=inputs)
    assert answer.status_code == 200, world.describe_response()
    world.data['hausgeld'] = {**data, 'plan': answer.json()}


@when(parsers.parse("the manager reads the owner's {month} ledger on {cutoff}"))
def owner_ledger(world, month, cutoff):
    data = world.data['hausgeld']
    answer = world.act('GET', f"/api/payments/mietkonto/owner/{data['owner']['id']}", params={
        **month_params(month), 'economic_plan_id': data['plan']['id'], 'as_of': cutoff})
    assert answer.status_code == 200, world.describe_response()


@then(parsers.parse('the 600 euros are not yet due and the due date is {due}'))
def not_due(world, due):
    result = world.response.json()
    assert result['balance'] == result['not_yet_due_balance'] == '600.00'
    assert result['due_balance'] == '0.00'
    assert result['open_items'][0]['due_date'] == due
    assert result['open_items'][0]['is_due'] is False


@then(parsers.parse('the 600 euros are due with the same due date {due}'))
def due_now(world, due):
    result = world.response.json()
    assert result['balance'] == result['due_balance'] == '600.00'
    assert result['not_yet_due_balance'] == '0.00'
    assert result['open_items'][0]['due_date'] == due
    assert result['open_items'][0]['is_due'] is True


@then(parsers.parse('an owner notice for {month} on {cutoff} names the due date {due}'))
def owner_notice(world, month, cutoff, due):
    data = world.data['hausgeld']
    answer = world.act('POST', '/api/dunning/owner', json={**month_params(month),
        'owner_profile_id': data['owner']['id'], 'economic_plan_id': data['plan']['id'],
        'reference_date': cutoff})
    assert answer.status_code == 201, world.describe_response()
    notice = answer.json()
    assert notice['included_items'][0]['due_date'] == due
    answer = world.act('POST', f"/api/dunning/{notice['id']}/issue")
    assert answer.status_code == 200, world.describe_response()
    answer = world.act('GET', f"/api/dunning/{notice['id']}/pdf")
    assert answer.status_code == 200, world.describe_response()
    assert '.'.join(reversed(due.split('-'))) in pdf_text(answer.content)


@when(parsers.parse('the manager records a due day and opens the owner ledger at {width:d} pixels'))
def open_owner_browser(browser_world, width, tmp_path):
    b = browser_world
    data = b.world.data['hausgeld']
    directory = Path(__file__).resolve().parents[3] / 'docs/assets/design/hausgeld-due' if os.getenv('VERWALINO_CAPTURE_DESIGN') == '1' else tmp_path
    directory.mkdir(parents=True, exist_ok=True)

    async def run():
        page = b.page
        await page.set_viewport_size({'width': width, 'height': 1000})
        await b.visit('/wirtschaftsplaene')
        await page.get_by_role('button', name='+ Neuer Wirtschaftsplan', exact=True).click()
        await b.select('WEG-Objekt *', data['property']['id'])
        due = page.get_by_label('Fälligkeitstag (monatl.)', exact=True)
        await expect(due).to_have_value('1')
        await due.fill('29')
        assert not await due.evaluate('el => el.checkValidity()')
        await due.fill('28')
        assert await due.evaluate('el => el.checkValidity()')
        await due.fill('3')
        await page.screenshot(path=str(directory / f'after-create-{width}.png'), full_page=True)
        await page.get_by_role('button', name='Anlegen', exact=True).click()
        await expect(page.get_by_text('Fälligkeit: jeweils zum 3. des Monats', exact=True)).to_be_visible()
        await b.visit(f"/wirtschaftsplaene/{data['plan']['id']}")
        await expect(page.get_by_text('Fälligkeit: jeweils zum 3. des Monats', exact=True)).to_be_visible()
        await page.screenshot(path=str(directory / f'after-plan-{width}.png'), full_page=True)
        await b.visit('/mietkonto')
        await b.select('Ansicht', 'owner')
        await b.select('Eigentümer *', data['owner']['id'])
        await b.select('Wirtschaftsplan (für Soll)', data['plan']['id'])
        await page.get_by_label('Zeitraum von *', exact=True).fill('2026-01-01')
        await page.get_by_label('Zeitraum bis *', exact=True).fill('2026-12-31')
        await page.get_by_label('Fällig am *', exact=True).fill('2026-07-02')
        await page.get_by_role('button', name='Abrufen', exact=True).click()
        await b.row('Saldo', '7.200,00 €')
        await b.row('davon fällig', '3.600,00 € (Rückstand)')
        await b.row('noch nicht fällig', '3.600,00 €')
        row = page.get_by_role('row').filter(has=page.get_by_text('2026/07', exact=True))
        await expect(row).to_contain_text('noch nicht fällig')
        await expect(row).to_contain_text('Fällig am 03.07.2026')
        await page.get_by_role('heading', name='Kontoauszug', exact=True).evaluate("el => el.scrollIntoView({block:'start'})")
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path=str(directory / f'after-ledger-{width}.png'), full_page=True)
    b.run(run())


@then('the full-year balance stays 7200 euros while July changes from future to due')
def owner_browser_due(browser_world):
    b = browser_world

    async def run():
        page = b.page
        await page.get_by_label('Fällig am *', exact=True).fill('2026-07-04')
        await expect(page.get_by_role('cell', name='Saldo', exact=True)).to_have_count(0)
        await page.get_by_role('button', name='Abrufen', exact=True).click()
        await b.row('Saldo', '7.200,00 €')
        await b.row('davon fällig', '4.200,00 € (Rückstand)')
        await b.row('noch nicht fällig', '3.000,00 €')
        row = page.get_by_role('row').filter(has=page.get_by_text('2026/07', exact=True))
        await expect(row.get_by_text('fällig', exact=True)).to_be_visible()
        await expect(row).to_contain_text('Fällig am 03.07.2026')
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    b.run(run())
