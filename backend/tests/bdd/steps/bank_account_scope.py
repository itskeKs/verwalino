"""An account saved without an Abrechnungskreis is named by the refusal and repaired in the UI (#671)."""
import hashlib

from playwright.async_api import expect
from pytest_bdd import given, then, when, parsers

from tests.bdd.steps.browser_common import browser_step
from tests.bdd.steps.browser_setup import create
from tests.bdd.steps.special_levy import levy_payload, prepare_levy_form
from tests.settlement_period_support import period_data_cleanup as period_data_cleanup  # noqa: F401


@given('the Laufendes Konto of Gartenstraße 5 was saved without an Abrechnungskreis')
def account_without_scope(world, period_data_cleanup):
    # An organization with classified finance answers no unscoped statement read, so the records of this
    # scenario are released at its end for the features that run after it.
    label = f"Girokonto {hashlib.sha256(world.data['scenario'].encode()).hexdigest()[:8]}"
    world.data['account'] = create(world, '/api/bank-accounts', {
        'property_id': world.data['property']['id'], 'account_type': 'LAUFEND', 'label': label})


@when('the manager tries to create the roof repair levy due on 1 July 2026')
def try_roof_levy(world):
    world.act('POST', '/api/special-levies', json={**levy_payload(world), 'resolution_id': world.data['levy_resolution']['id']})


@then('the manager is told which account needs an Abrechnungskreis')
def refusal_names_account(world):
    assert world.response.status_code == 409, world.describe_response()
    message = world.response.json()['detail']['message']
    assert f"„{world.data['account']['label']}“" in message and 'Bankkonten' in message and 'Abrechnungskreis' in message, message


@when('the administrator assigns the Abrechnungskreis of Gartenstraße 5 to the Laufendes Konto')
def assign_scope(world):
    answer = world.act('POST', f"/api/accounting-scopes/{world.data['scope']['id']}/bindings", json={
        'resource_type': 'bank_account', 'resource_id': world.data['account']['id'], 'reason': 'Konto der Eigentümergemeinschaft'})
    assert answer.status_code == 201, world.describe_response()


@when(parsers.parse('the manager tries to create the roof repair levy in the browser at {width:d} pixels'))
@browser_step
async def browser_try_levy(browser_world, width):
    browser = browser_world
    form = await prepare_levy_form(browser, width)
    await browser.select('Beschluss *', browser.world.data['levy_resolution']['id'], form)
    async with browser.page.expect_response(lambda r: r.request.method == 'POST' and '/api/special-levies' in r.url) as pending:
        await form.get_by_role('button', name='Sonderumlage anlegen', exact=True).click()
    assert (await pending.value).status == 409


@then('the browser shows one refusal that names the Laufendes Konto and the assignment')
@browser_step
async def browser_refusal(browser_world):
    alerts = browser_world.page.get_by_role('alert')
    await expect(alerts).to_have_count(1)
    await expect(alerts).to_contain_text(f"Das Bankkonto „{browser_world.world.data['account']['label']}“ hat noch keinen Abrechnungskreis.")
    await expect(alerts).to_contain_text('unter „Bankkonten“ einen Abrechnungskreis zu')


@when('the administrator follows the overview counter and assigns the Abrechnungskreis in the browser')
@browser_step
async def browser_assign(browser_world):
    browser = browser_world
    page = browser.page
    await browser.visit('/berechtigungen')
    await page.get_by_role('link', name='Konten ohne Abrechnungskreis', exact=True).click()
    await expect(page.get_by_role('heading', name='Bankkonten', exact=True)).to_be_visible()
    await expect(page.get_by_role('checkbox', name='Nur Konten ohne Abrechnungskreis')).to_be_checked()
    row = page.get_by_role('row').filter(has=page.get_by_role('cell', name=browser.world.data['account']['label']))
    await expect(row.get_by_text('Nicht zugeordnet', exact=True)).to_be_visible()
    await row.get_by_role('button', name='Abrechnungskreis zuordnen', exact=True).click()
    dialog = page.get_by_role('dialog')
    await dialog.get_by_label('Abrechnungskreis suchen', exact=True).fill(browser.world.data['scope']['label'])
    await browser.select('Abrechnungskreis', browser.world.data['scope']['id'], dialog)
    await dialog.get_by_label('Begründung *', exact=True).fill('Konto der Eigentümergemeinschaft')
    async with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/bindings')) as pending:
        await dialog.get_by_role('button', name='Zuordnung speichern', exact=True).click()
    assert (await pending.value).status == 201
    await expect(page.get_by_role('status').filter(has_text='Abrechnungskreis zugeordnet.')).to_be_visible()


@then('the roof repair levy shows the owner shares of 720 euros and 480 euros')
@browser_step
async def browser_levy_shares(browser_world):
    page = browser_world.page
    await expect(page.get_by_role('cell', name='720,00 €', exact=True)).to_be_visible()
    await expect(page.get_by_role('cell', name='480,00 €', exact=True)).to_be_visible()
