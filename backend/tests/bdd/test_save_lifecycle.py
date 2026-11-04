"""Visible save receipts and retained content on the named master-data pages."""
import asyncio
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import expect
from pytest_bdd import given, parsers, scenarios, then, when

from tests.bdd.steps.browser_common import button, field
from tests.bdd.steps.browser_setup import create
from tests.bdd.report import artifact_directory, attach_artifact

scenarios(str(Path(__file__).resolve().parents[3] / 'features/save_lifecycle.feature'))


@given('the rental property has many units, contacts, accounts and past leases')
def representative_lists(world):
    d = world.data
    for index in range(40):
        create(world, '/api/units', {'property_id': d['property']['id'], 'number': f'Nebenhaus {index + 1}', 'usage_type': 'WOHNUNG'})
        person = create(world, '/api/persons', {'first_name': 'Zusätzlicher Kontakt', 'last_name': f'Sonnenhof {index + 1:02d}'})
        domain = create(world, '/api/person-data-domains', {'person_id': person['id'], 'reason_code': 'INITIAL_SETUP'}, keyed=True)
        create(world, f"/api/person-data-domains/{domain['id']}/grants", {'user_id': d['user_id'], 'capabilities': ['read', 'prepare', 'export'], 'reason_code': 'TEAM_ACCESS'}, keyed=True)
        create(world, '/api/bank-accounts', {'property_id': d['property']['id'], 'accounting_scope_id': d['scope']['id'], 'account_type': 'LAUFEND', 'label': f'Nebenkonto Gartenflügel {index + 1}'})
    for year in range(2020, 2025):
        create(world, '/api/leases', {'unit_id': d['units'][0]['id'], 'tenant_profile_id': d['tenants'][0]['id'], 'valid_from': f'{year}-01-01', 'valid_to': f'{year}-12-31', 'base_rent': '1234.56', 'advance_payment': '150.00'})

    content = 'Buchungstag;Wertstellung;Betrag;Name;Verwendungszweck\n' + ''.join(
        f'05.03.2026;05.03.2026;25,00;Testdienst {index};Nebeneingang Gartenflügel {index}\n' for index in range(30))
    response = world.act('POST', f"/api/bank-accounts/{d['account']['id']}/imports", params={'dry_run': 'false'}, files={'file': ('more-receipts.csv', content.encode(), 'text/csv')})
    assert response.status_code == 200, response.text


@when(parsers.parse('the manager saves units, people, accounts and an assignment at {width:d} pixels'))
def save_named_pages(browser_world, width, request):
    b, d = browser_world, browser_world.world.data
    results = []
    positions = []
    directory = artifact_directory(request.node)

    async def save(action, refresh_path, receipt, retained):
        release, requested, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        pattern = re.compile(re.escape(refresh_path) + r"(?:\?.*)?$")

        async def pause_refresh(route):
            if route.request.method == 'GET' and urlsplit(route.request.url).path == refresh_path:
                response = await route.fetch()
                requested.set()
                await release.wait()
                try:
                    await route.fulfill(response=response)
                finally:
                    completed.set()
            else:
                await route.fallback()

        element = await retained.element_handle()
        await b.page.route(pattern, pause_refresh)
        try:
            await action.scroll_into_view_if_needed()
            scroll = await b.page.evaluate('({page: window.scrollY, main: document.querySelector("main").scrollTop})')
            await action.click()
            await asyncio.wait_for(requested.wait(), timeout=10)
            await expect(b.page.get_by_role('status').filter(has_text=receipt)).to_be_in_viewport()
            assert await element.evaluate('(element) => element.isConnected'), 'saved page content was unmounted'
            current = await b.page.evaluate('({page: window.scrollY, main: document.querySelector("main").scrollTop})')
            for key in scroll:
                if scroll[key] > 100:
                    assert current[key] > 100, f'{receipt}: scroll jumped to the top'
            results.append(receipt)
            positions.append(scroll)
        finally:
            release.set()
            if requested.is_set():
                await asyncio.wait_for(completed.wait(), timeout=10)
            await b.page.unroute(pattern, pause_refresh)

    async def capture(name):
        # Each caller has awaited the new visible value, so this checks the completed refresh too.
        current = await b.page.evaluate('({page: window.scrollY, main: document.querySelector("main").scrollTop})')
        for key, previous in positions[-1].items():
            if previous > 100:
                assert current[key] > 100, f'{name}: completed refresh jumped to the top'
        if directory:
            image = directory / f'after-{name}-{width}.png'
            await b.page.screenshot(path=str(image), full_page=True)
            attach_artifact(request.node, image)

    async def run():
        page = b.page
        await page.set_viewport_size({'width': width, 'height': 900})
        prop, unit = d['property']['id'], d['units'][0]['id']
        await b.visit(f'/objekte/{prop}')
        await button(page, '+ Neue Einheit').click()
        await field(page, 'Nummer *').fill('Wohnung Gartenflügel')
        await field(page, 'Lage').fill('Hinterhaus mit langem Durchgang zum Garten')
        await save(button(page, 'Speichern'), f'/api/properties/{prop}', 'Einheit gespeichert.', page.get_by_role('heading', name=d['property']['name'], exact=True))
        await expect(page.get_by_role('link', name='Wohnung Gartenflügel', exact=True)).to_be_visible()
        await capture('property')

        await b.visit('/personen')
        row = page.get_by_role('row').filter(has=page.get_by_role('link', name=d['people'][0].name, exact=True))
        await button(row, 'Bearbeiten').click()
        await field(page, 'Telefon').fill('030 123456789')
        await save(button(page, 'Speichern'), '/api/persons/page', 'Person gespeichert.', page.get_by_role('table', include_hidden=True))
        await expect(row.get_by_role('cell', name='030 123456789', exact=True)).to_be_visible()
        await capture('persons')

        await b.visit(f"/personen/{d['people'][0].id}")
        await save(button(page, 'Als Eigentuemer kennzeichnen'), f"/api/persons/{d['people'][0].id}", 'Eigentümerprofil gespeichert.', page.get_by_role('heading', name='Kontaktdaten', exact=True))
        await expect(page.get_by_text('Eigentuemer', exact=True)).to_be_visible()
        await expect(button(page, 'Als Eigentuemer kennzeichnen')).to_have_count(0)
        await capture('person')

        await b.visit(f'/objekte/{prop}/einheiten/{unit}')
        await button(page, 'Bearbeiten / beenden').first.click()
        year = (await field(page, 'Vertragsbeginn *').input_value())[:4]
        end = f'{year}-12-30'
        await field(page, 'Vertragsende').fill(end)
        await save(button(page, 'Speichern'), f'/api/units/{unit}', 'Mietvertrag gespeichert.', page.get_by_role('heading', name='Mietvertraege', exact=True))
        await expect(page.get_by_role('cell', name=end, exact=True)).to_be_visible()
        await capture('unit')

        await b.visit('/bankkonten')
        row = page.get_by_role('row').filter(has=page.locator(f"a[href='/bankkonten/{d['account']['id']}/umsaetze']"))
        await button(row, 'Bankkonto bearbeiten').click()
        await field(page, 'Bezeichnung').fill('Mietkonto Gartenflügel')
        await save(button(page, 'Änderungen speichern'), '/api/bank-accounts', 'Bankkonto gespeichert.', page.get_by_role('table', include_hidden=True))
        await expect(row.get_by_text('Mietkonto Gartenflügel', exact=True)).to_be_visible()
        await capture('accounts')

        await b.visit(f"/bankkonten/{d['account']['id']}/umsaetze")
        await field(page, 'Status').select_option('')
        row = page.get_by_role('row').filter(has=page.get_by_text('Warmmiete Januar 2026', exact=True))
        await save(button(row, 'Vorschlag übernehmen'), '/api/bank-transactions', 'Umsatz zugeordnet.', page.get_by_role('table', include_hidden=True))
        await expect(row).to_contain_text('Zugeordnet')
        await expect(button(row, 'Vorschlag übernehmen')).to_have_count(0)
        await capture('assignment')
    b.run(run())
    d['save_receipts'] = results


@then('each saved page confirms the write and shows the new values without reloading')
def saved_pages(world):
    assert len(world.data['save_receipts']) == 6
