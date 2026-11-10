"""Bind the responsive dunning notice journey to the shared browser steps."""
from pathlib import Path
import re

from playwright.async_api import expect
from pytest_bdd import scenarios, given, when, then, parsers

from tests.bdd.steps.browser_common import browser_step, button, field, submit
from tests.bdd.test_weg_settlement import create_balance_world
from tests.bdd.test_weg_settlement import dated_owners as create_dated_owners

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "journeys" / "dunning_notice.feature"))


@given('Karl Weiß owns unit 2 until June and Lena Roth owns it from July', target_fixture='dated_owners')
def dated_owners(world, seed_data):
    return create_dated_owners(world, seed_data)


async def create_and_open(browser, kind):
    pattern = re.compile(r'/api/dunning/[0-9a-f-]+$')
    async with browser.page.expect_response(lambda response: response.request.method == 'GET' and pattern.search(response.url)) as first_read:
        notice = await submit(browser, 'Mahnung erstellen', f'/api/dunning/{kind}')
    response = await first_read.value
    assert response.status == 200, f'First draft detail read: {response.status} {await response.text()}'
    assert (await response.json())['id'] == notice['id']
    await expect(button(browser.page, 'Mahnung ausgeben')).to_be_visible()
    await expect(browser.page.get_by_text('Entwurf', exact=True).first).to_be_visible()
    return notice


@when("the manager creates Karl's and Lena's notices for the 2026 plan as of 15 January 2027")
def create_dated_owner_drafts(browser_world, dated_owners):
    b, w = browser_world, dated_owners

    async def create_both():
        notices = []
        for owner, months in [(w['owners'][1], list(range(1, 7))), (w['buyer'], list(range(7, 13)))]:
            await b.visit('/mahnungen')
            await button(b.page, '+ Neue Mahnung').click()
            await field(b.page, 'Empfaengertyp *').select_option('owner')
            await b.select('Eigentuemer *', owner)
            await b.select('WEG-Objekt *', w['property'])
            await b.select('Wirtschaftsplan', w['plan'])
            for label, value in [('Zeitraum von *', '2026-01-01'), ('Zeitraum bis *', '2026-12-31'), ('Stichtag', '2027-01-15')]:
                await field(b.page, label).fill(value)
            await b.page.get_by_label('Abrechnungskreis suchen', exact=True).fill('WEG Gartenstraße')
            await b.select('Abrechnungskreis', w['scope'])
            notice = await create_and_open(b, 'owner')
            assert notice['owner_profile_id'] == owner
            assert notice['principal_amount'] == '2400.00'
            assert [row['period_month'] for row in notice['included_items']] == months
            for width in (1280, 360):
                await b.page.set_viewport_size({'width': width, 'height': 900})
                await b.row('Hauptforderung (Rueckstand)', '2.400,00 €')
                table = b.page.locator('.card').filter(has=b.page.get_by_role('heading', name='Rueckstandsaufstellung', exact=True)).get_by_role('table')
                await expect(table.locator('tbody tr')).to_have_count(6)
                for month in months:
                    await expect(table.get_by_role('cell', name=f'2026/{month:02}', exact=True)).to_be_visible()
            notices.append(notice)
            await b.page.set_viewport_size({'width': 1440, 'height': 1000})
        return notices

    w['drafts'] = b.run(create_both())


@then('each draft opens immediately with only its six monthly advances and no duplicate')
def dated_drafts_without_duplicates(world, dated_owners):
    w = dated_owners
    for notice in w['drafts']:
        rows = world.act('GET', '/api/dunning', params={'owner_profile_id': notice['owner_profile_id']})
        assert rows.status_code == 200, rows.text
        assert [row['id'] for row in rows.json()] == [notice['id']]


@given("Anna Berg's 2026 plan charges 600 EUR in July due on 3 July", target_fixture='july_plan')
def july_plan(world, seed_data):
    return create_balance_world(world, seed_data, monthly_due_day=3)


@when(parsers.parse("the manager submits Anna's July notice as of 2 July 2026 at {width:d} px"))
def submit_early_owner_notice(browser_world, july_plan, width):
    b, w = browser_world, july_plan
    w['selections'] = {
        'Eigentuemer *': w['owners'][0], 'WEG-Objekt *': w['property'], 'Wirtschaftsplan': w['plan'],
        'Zeitraum von *': '2026-07-01', 'Zeitraum bis *': '2026-07-31', 'Stichtag': '2026-07-02',
    }

    async def submit_early():
        await b.page.set_viewport_size({'width': width, 'height': 800})
        await b.visit('/mahnungen')
        await button(b.page, '+ Neue Mahnung').click()
        await field(b.page, 'Empfaengertyp *').select_option('owner')
        for label, value in w['selections'].items():
            if label in {'Eigentuemer *', 'WEG-Objekt *', 'Wirtschaftsplan'}:
                await b.select(label, value)
            else:
                await field(b.page, label).fill(value)
        await b.page.get_by_label('Abrechnungskreis suchen', exact=True).fill('WEG Gartenstraße')
        await b.select('Abrechnungskreis', w['scope'])
        await button(b.page, 'Mahnung erstellen').scroll_into_view_if_needed()
        return await submit(b, 'Mahnung erstellen', '/api/dunning/owner', status=422)

    w['refusal'] = b.run(submit_early())


@then('the German refusal is fully in view without manual scrolling, the selections remain and no draft is created')
def early_owner_notice_is_refused(browser_world, world, july_plan):
    b, w = browser_world, july_plan

    detail = 'Für den ausgewählten Eigentümer gibt es im gewählten Zeitraum zum Stichtag keine überfälligen Beträge.'
    assert w['refusal'] == {'detail': detail}

    async def check_form():
        await expect(b.page.get_by_text(detail, exact=True)).to_be_in_viewport(ratio=1)
        await expect(b.page.get_by_role('combobox', name='Empfaengertyp *', exact=True)).to_have_value('owner')
        await expect(b.page.get_by_role('combobox', name='Abrechnungskreis', exact=True)).to_have_value(w['scope'])
        for label, value in w['selections'].items():
            control = (b.page.get_by_role('combobox', name=label, exact=True)
                       if label in {'Eigentuemer *', 'WEG-Objekt *', 'Wirtschaftsplan'} else field(b.page, label))
            await expect(control).to_have_value(value)

    rows = world.act('GET', '/api/dunning', params={'owner_profile_id': w['owners'][0]})
    assert rows.status_code == 200 and rows.json() == [], rows.text
    b.run(check_form())


@when('the manager changes only the reference date to 4 July 2026 and resubmits')
def retry_owner_notice(browser_world, july_plan):
    b = browser_world

    async def retry():
        await field(b.page, 'Stichtag').fill('2026-07-04')
        return await create_and_open(b, 'owner')

    july_plan['draft'] = b.run(retry())


@then('exactly one owner draft opens with 600 EUR principal for the charge due on 3 July')
def july_owner_draft_is_correct(browser_world, world, july_plan):
    notice = july_plan['draft']
    browser_world.run(browser_world.row('Hauptforderung (Rueckstand)', '600,00 €'))
    rows = world.act('GET', '/api/dunning', params={'owner_profile_id': july_plan['owners'][0]})
    assert rows.status_code == 200 and [row['id'] for row in rows.json()] == [notice['id']], rows.text
    draft = world.act('GET', f"/api/dunning/{notice['id']}")
    assert draft.status_code == 200, draft.text
    saved = draft.json()
    assert (saved['status'], saved['principal_amount']) == ('draft', '600.00')
    item, = saved['included_items']
    assert (item['due_date'], item['open_amount']) == ('2026-07-03', '600.00')


@when('the manager creates an ordinary January tenant notice as of 2 March 2026')
def create_ordinary_tenant(browser_world):
    b = browser_world

    async def create_notice():
        await b.visit('/mahnungen')
        await button(b.page, '+ Neue Mahnung').click()
        await b.select('Mieter *', b.world.data['tenants'][0]['id'])
        await b.select('Mietvertrag *', b.world.data['leases'][0]['id'])
        for label, value in [('Zeitraum von *', '2026-01-01'), ('Zeitraum bis *', '2026-01-31'), ('Stichtag', '2026-03-02')]:
            await field(b.page, label).fill(value)
        await b.scope()
        return await create_and_open(b, 'tenant')

    b.world.data['immediate_tenant_draft'] = b.run(create_notice())


@then('the tenant draft opens immediately with 700 EUR principal and no duplicate')
def tenant_draft_without_duplicate(browser_world, world):
    notice = world.data['immediate_tenant_draft']
    assert notice['principal_amount'] == '700.00'
    assert notice['tenant_profile_id'] == world.data['tenants'][0]['id']
    browser_world.run(browser_world.row('Hauptforderung (Rueckstand)', '700,00 €'))
    rows = world.act('GET', '/api/dunning', params={'tenant_profile_id': notice['tenant_profile_id']})
    assert rows.status_code == 200 and [row['id'] for row in rows.json()] == [notice['id']], rows.text


@when(parsers.parse('the manager submits a June tenant notice without an accounting scope at {width:d} px'))
@browser_step
async def submit_without_scope(browser_world, width):
    b = browser_world
    data = b.world.data
    await b.page.set_viewport_size({'width': width, 'height': 800})
    await b.visit('/mahnungen')
    await button(b.page, '+ Neue Mahnung').click()
    await b.select('Mieter *', data['tenants'][0]['id'])
    await b.select('Mietvertrag *', data['leases'][0]['id'])
    dates = {'Zeitraum von *': '2026-06-01', 'Zeitraum bis *': '2026-06-30', 'Stichtag': '2026-07-15'}
    for label, value in dates.items():
        await field(b.page, label).fill(value)
    data['dunning_fields'] = {
        'Empfaengertyp *': 'tenant',
        'Mieter *': data['tenants'][0]['id'],
        'Mietvertrag *': data['leases'][0]['id'],
        **dates,
        'Karenzzeit (Tage)': await field(b.page, 'Karenzzeit (Tage)').input_value(),
        'Mahngebuehr (Override)': await field(b.page, 'Mahngebuehr (Override)').input_value(),
        'Abrechnungskreis': '',
    }
    await expect(field(b.page, 'Abrechnungskreis')).to_have_value('')
    await button(b.page, 'Mahnung erstellen').scroll_into_view_if_needed()
    await submit(b, 'Mahnung erstellen', '/api/dunning/tenant', status=409)


@then('the form explains the accounting scope requirement in German and no draft exists')
def scope_refusal_without_draft(browser_world, world):
    rows = world.act('GET', '/api/dunning', params={'tenant_profile_id': world.data['tenants'][0]['id']})
    assert rows.status_code == 200 and rows.json() == [], rows.text
    browser_world.run(expect(scope_refusal(browser_world.page)).to_be_visible())


def scope_refusal(page):
    return page.get_by_text(
        'Wählen Sie einen eindeutigen Abrechnungskreis und prüfen Sie noch nicht zugeordnete Daten, '
        'bevor Sie die Zahlungen berechnen.', exact=True)


@then('the explanation is fully in view without manual scrolling and the form keeps its values')
@browser_step
async def visible_scope_refusal(browser_world):
    b = browser_world
    await expect(scope_refusal(b.page)).to_be_in_viewport(ratio=1)
    for label, value in b.world.data['dunning_fields'].items():
        control = (
            b.page.get_by_role('combobox', name=label, exact=True)
            if label in {'Empfaengertyp *', 'Mieter *', 'Mietvertrag *', 'Abrechnungskreis'}
            else field(b.page, label)
        )
        await expect(control).to_have_value(value)


@when('the manager selects the first accounting scope and submits the same notice')
@browser_step
async def retry_with_scope(browser_world):
    await browser_world.scope()
    browser_world.world.data['scoped_tenant_draft'] = await create_and_open(browser_world, 'tenant')


@then('exactly one tenant draft exists and opens with 750 EUR principal')
def scoped_tenant_principal(browser_world, world):
    browser_world.run(browser_world.row('Hauptforderung (Rueckstand)', '750,00 €'))
    notice = world.data['scoped_tenant_draft']
    assert notice['principal_amount'] == '750.00'
    rows = world.act('GET', '/api/dunning', params={'tenant_profile_id': world.data['tenants'][0]['id']})
    assert rows.status_code == 200 and [row['id'] for row in rows.json()] == [notice['id']], rows.text


@given("the standard first dunning fee is 2.50 EUR without automatic interest")
def set_nonzero_fee(world, reset_dunning_settings):
    response = world.act("PUT", "/api/dunning/settings", json={
        "fee_level_1": "2.50", "fee_level_2": "2.50", "fee_level_3": "2.50",
        "default_interest_enabled": False,
    })
    assert response.status_code == 200, world.describe_response()


async def open_fee_form(browser, width):
    await browser.page.set_viewport_size({"width": width, "height": 900})
    await browser.visit("/mahnungen")
    await button(browser.page, "+ Neue Mahnung").click()


async def submit_fee(browser, kind, fee, tmp_path):
    page = browser.page
    await field(page, "Mahngebuehr (Override)").fill(fee)
    async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(f"/api/dunning/{kind}")) as pending:
        await button(page, "Mahnung erstellen").click()
    response = await pending.value
    if response.status == 201:
        await browser.row("Mahngebühr", "0,00 €")
        await page.get_by_role("cell", name="Mahngebühr", exact=True).scroll_into_view_if_needed()
    else:
        await expect(page.get_by_role("alert")).to_be_visible()
    await page.screenshot(path=str(tmp_path / f"fee-{kind}-{page.viewport_size['width']}.png"), full_page=True)
    assert response.status == 201, await response.text()
    browser.world.data["zero_fee_notice"] = (await response.json())["id"]


@when(parsers.parse('the manager creates the January tenant notice with "{fee}" fee at {width:d} px'))
@browser_step
async def zero_fee_tenant(browser_world, fee, width, tmp_path):
    b = browser_world
    await open_fee_form(b, width)
    await b.select("Mieter *", b.world.data["tenants"][0]["id"])
    await b.select("Mietvertrag *", b.world.data["leases"][0]["id"])
    for label, value in [("Zeitraum von *", "2026-01-01"), ("Zeitraum bis *", "2026-01-31"), ("Stichtag", "2026-03-02")]:
        await field(b.page, label).fill(value)
    await b.scope()
    await submit_fee(b, "tenant", fee, tmp_path)


@when(parsers.parse("""the manager creates Anna's July owner notice with "{fee}" fee at {width:d} px"""))
@browser_step
async def zero_fee_owner(browser_world, july_plan, fee, width, tmp_path):
    b, w = browser_world, july_plan
    await open_fee_form(b, width)
    await field(b.page, "Empfaengertyp *").select_option("owner")
    await b.select("Eigentuemer *", w["owners"][0])
    await b.select("WEG-Objekt *", w["property"])
    await b.select("Wirtschaftsplan", w["plan"])
    for label, value in [("Zeitraum von *", "2026-07-01"), ("Zeitraum bis *", "2026-07-31"), ("Stichtag", "2026-07-04")]:
        await field(b.page, label).fill(value)
    await field(b.page, "Abrechnungskreis suchen").fill("WEG Gartenstraße")
    await b.select("Abrechnungskreis", w["scope"])
    await submit_fee(b, "owner", fee, tmp_path)


@then(parsers.parse("the notice has {principal:d} EUR principal, no fee, no interest and {total:d} EUR total"))
def zero_fee_saved(world, principal, total):
    response = world.act("GET", f"/api/dunning/{world.data['zero_fee_notice']}")
    assert response.status_code == 200, world.describe_response()
    notice = response.json()
    assert (notice["principal_amount"], notice["fee_amount"], notice["interest_amount"], notice["total_amount"]) == (
        f"{principal}.00", "0.00", "0.00", f"{total}.00",
    )
