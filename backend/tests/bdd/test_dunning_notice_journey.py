"""Bind the responsive dunning notice journey to the shared browser steps."""
from pathlib import Path
import re

from playwright.async_api import expect
from pytest_bdd import scenarios, given, when, then

from tests.bdd.steps.browser_common import button, field, submit
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
            for label, value in [('Zeitraum von *', '2026-01-01'), ('Zeitraum bis *', '2026-12-31'), ('Stichtag', '2027-01-15'), ('Basiszinssatz (%)', '2.50')]:
                await field(b.page, label).fill(value)
            await b.page.get_by_label('Abrechnungskreis suchen', exact=True).fill('WEG Gartenstraße')
            await b.select('Abrechnungskreis', w['scope'])
            notice = await create_and_open(b, 'owner')
            assert notice['owner_profile_id'] == owner
            assert notice['principal_amount'] == '2400.00'
            assert [row['period_month'] for row in notice['included_items']] == months
            for width in (1280, 360):
                await b.page.set_viewport_size({'width': width, 'height': 900})
                await b.row('Hauptforderung (Rueckstand)', '2400.00 EUR')
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


@when('the manager creates an ordinary January tenant notice as of 2 March 2026')
def create_ordinary_tenant(browser_world):
    b = browser_world

    async def create_notice():
        await b.visit('/mahnungen')
        await button(b.page, '+ Neue Mahnung').click()
        await b.select('Mieter *', b.world.data['tenants'][0]['id'])
        await b.select('Mietvertrag *', b.world.data['leases'][0]['id'])
        for label, value in [('Zeitraum von *', '2026-01-01'), ('Zeitraum bis *', '2026-01-31'), ('Stichtag', '2026-03-02'), ('Basiszinssatz (%)', '2.50')]:
            await field(b.page, label).fill(value)
        await b.scope()
        return await create_and_open(b, 'tenant')

    b.world.data['immediate_tenant_draft'] = b.run(create_notice())


@then('the tenant draft opens immediately with 700 EUR principal and no duplicate')
def tenant_draft_without_duplicate(browser_world, world):
    notice = world.data['immediate_tenant_draft']
    assert notice['principal_amount'] == '700.00'
    assert notice['tenant_profile_id'] == world.data['tenants'][0]['id']
    browser_world.run(browser_world.row('Hauptforderung (Rueckstand)', '700.00 EUR'))
    rows = world.act('GET', '/api/dunning', params={'tenant_profile_id': notice['tenant_profile_id']})
    assert rows.status_code == 200 and [row['id'] for row in rows.json()] == [notice['id']], rows.text
