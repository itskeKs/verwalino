"""Visible save receipts and retained content on the named master-data pages."""
import asyncio
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, expect
from pytest_bdd import given, parsers, scenarios, then, when

from tests.bank_browser_trace import PATH as BANK_LIST_PATH
from tests.bdd.steps.browser_common import button, field, german_date
from tests.bdd.steps.browser_setup import create
from tests.bdd.report import artifact_directory, attach_artifact

scenarios(
    str(Path(__file__).resolve().parents[3] / 'features/save_lifecycle.feature'),
    str(Path(__file__).resolve().parents[3] / 'features/journeys/post_import_assignment.feature'),
)


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

    # Ordinary outgoing rows keep the list long. The separate incoming
    # receipts in this scenario exercise rent matching and assignment.
    content = 'Buchungstag;Wertstellung;Betrag;Name;Verwendungszweck\n' + ''.join(
        f'05.03.2026;05.03.2026;-25,00;Testdienst {index};Nebenausgabe Gartenflügel {index}\n' for index in range(30))
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

        try:
            element = await retained.element_handle()
        except PlaywrightTimeoutError:
            if refresh_path == BANK_LIST_PATH and b.bank_trace:
                await b.bank_trace.table_timed_out()
            raise
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
        await expect(page.get_by_role('cell', name=german_date(end), exact=True)).to_be_visible()
        await capture('unit')

        await b.visit('/bankkonten')
        row = page.get_by_role('row').filter(has=page.locator(f"a[href='/bankkonten/{d['account']['id']}/umsaetze']"))
        await button(row, 'Bankkonto bearbeiten').click()
        await field(page, 'Bezeichnung').fill('Mietkonto Gartenflügel')
        await save(button(page, 'Änderungen speichern'), '/api/bank-accounts', 'Bankkonto gespeichert.', page.get_by_role('table', include_hidden=True))
        await expect(row.get_by_text('Mietkonto Gartenflügel', exact=True)).to_be_visible()
        await capture('accounts')

        b.observe_bank_save(request.config, width)
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


@when(parsers.parse('the manager imports sixty receipts and assigns one lower in the list at {width:d} pixels'))
def assign_imported_statement(browser_world, width, request):
    b, d = browser_world, browser_world.world.data
    directory = artifact_directory(request.node)

    async def run():
        page = b.page
        await page.set_viewport_size({'width': width, 'height': 900})
        await b.visit(f"/bankkonten/{d['account']['id']}/umsaetze?import=1")
        content = 'Buchungstag;Wertstellung;Betrag;Name;Verwendungszweck\n' + ''.join(
            f"05.03.2026;05.03.2026;700,00;Synthetischer Absender;Langer Auszug {index:02d}\n" for index in range(60))
        await field(page, 'Kontoauszug').set_input_files({'name': 'langer-auszug.csv', 'mimeType': 'text/csv', 'buffer': content.encode()})
        await button(page, 'Vorschau erstellen').click()
        await expect(page.get_by_role('heading', name='Importvorschau', exact=True)).to_be_visible()
        await button(page, 'Übernehmen').click()
        await expect(page.get_by_role('status').filter(has_text='60 neue Umsätze importiert.')).to_be_visible()
        rows = page.get_by_role('row').filter(has_text='Langer Auszug')
        await expect(rows.nth(35)).to_be_visible()
        purpose = await rows.nth(35).locator('td').nth(4).inner_text()
        row = page.get_by_role('row').filter(has=page.get_by_role('cell', name=purpose, exact=True))
        action = button(row, 'Aufteilen')
        await action.scroll_into_view_if_needed()
        before = await page.locator('main').evaluate('(element) => element.scrollTop')
        assert before > 1000, 'the chosen receipt must be below the first viewport'
        await action.click()
        dialog = page.get_by_role('dialog', name='Umsatz aufteilen', exact=True)
        await expect(dialog.get_by_label('Mietvertrag', exact=True).first.locator(f"option[value='{d['leases'][0]['id']}']")).to_have_count(1)
        while await dialog.get_by_role('button', name='Zeile entfernen', exact=True).count() > 1:
            await dialog.get_by_role('button', name='Zeile entfernen', exact=True).last.click()
        await b.select('Zahlungsart', 'KALTMIETE', dialog)
        await field(dialog, 'Betrag (EUR)').fill('700,00')
        await b.select('Mietvertrag', d['leases'][0]['id'], dialog)
        await button(dialog, 'Zuordnung buchen').click()
        await expect(dialog).to_have_count(0)
        await expect(row).to_have_count(0)
        after = await page.locator('main').evaluate('(element) => element.scrollTop')
        assert abs(after - before) <= 2, f'statement position changed: {before} -> {after}'
        assert '?import=1' in page.url
        if directory:
            image = directory / f'assignment-after-import-{width}.png'
            await page.screenshot(path=str(image), full_page=True)
            attach_artifact(request.node, image)
        await expect(page.get_by_role('status').filter(has_text='Umsatz zugeordnet.')).to_be_in_viewport()
        d['import_assignment_visible'] = True
    b.run(run())


@then('the assignment is confirmed in the viewport at the retained statement position')
def imported_assignment_visible(world):
    assert world.data['import_assignment_visible'] is True


@given('another private contact is available for editing')
def private_followup_contact(world):
    d = world.data
    person = create(world, '/api/persons', {'first_name': 'Bruno', 'last_name': 'Vertraulich', 'email': 'bruno@example.invalid', 'iban': 'DE89370400440532013000'})
    domain = create(world, '/api/person-data-domains', {'person_id': person['id'], 'reason_code': 'INITIAL_SETUP'}, keyed=True)
    create(world, f"/api/person-data-domains/{domain['id']}/grants", {'user_id': d['user_id'], 'capabilities': ['read', 'prepare', 'export'], 'reason_code': 'TEAM_ACCESS'}, keyed=True)
    d['private_followup'] = person


@when(parsers.parse('the manager opens follow-up editors during delayed refreshes at {width:d} pixels'))
def followup_editors(browser_world, width, request):
    b, d = browser_world, browser_world.world.data
    directory = artifact_directory(request.node)
    results = {}

    async def capture(name):
        if directory:
            image = directory / f'{name}-{width}.png'
            await b.page.screenshot(path=str(image), full_page=True)
            attach_artifact(request.node, image)

    async def run():
        page = b.page
        await page.set_viewport_size({'width': width, 'height': 900})
        await b.visit('/personen')
        row = page.get_by_role('row').filter(has=page.locator(f"a[href='/personen/{d['people'][0].id}']"))
        other = page.get_by_role('row').filter(has=page.locator(f"a[href='/personen/{d['private_followup']['id']}']"))
        await expect(row).to_be_visible()
        # Earlier scenarios retain contacts, so the follow-up contact may be on a later page.
        rows = page.locator('tbody tr')
        while await other.count() == 0:
            loaded = await rows.count()
            await button(page, 'Weitere laden').click()
            await expect(rows.nth(loaded)).to_be_attached()
        await button(row, 'Bearbeiten').click()
        await field(page, 'Telefon').fill('030 5551234')
        ready, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        pattern = re.compile(r'/api/persons/page(?:\?.*)?$')

        async def denied_refresh(route):
            if route.request.method != 'GET':
                await route.fallback()
                return
            await route.fetch()
            ready.set()
            await release.wait()
            try:
                await route.fulfill(status=401, content_type='application/json', body='{"detail":"Zugriff auf Kontakte entzogen"}')
            finally:
                completed.set()

        await page.route(pattern, denied_refresh)
        try:
            await button(page, 'Speichern').click()
            await asyncio.wait_for(ready.wait(), timeout=10)
            await button(other, 'Bearbeiten').click()
            await expect(field(page, 'E-Mail')).to_have_value('bruno@example.invalid')
        finally:
            release.set()
            if ready.is_set():
                await asyncio.wait_for(completed.wait(), timeout=10)
            await page.unroute(pattern, denied_refresh)
        await expect(page.get_by_text('ApiError: Zugriff auf Kontakte entzogen', exact=True)).to_be_visible()
        results['private_editor_closed'] = await field(page, 'E-Mail').count() == 0
        await capture('persons-access-loss')

        await b.visit('/bankkonten')
        bank_row = page.get_by_role('row', include_hidden=True).filter(has=page.locator(f"a[href='/bankkonten/{d['account']['id']}/umsaetze']"))
        await button(bank_row, 'Bankkonto bearbeiten').click()
        await field(page, 'Bezeichnung').fill('Bestätigtes Konto Gartenflügel')
        ready, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        pattern = re.compile(r'/api/bank-accounts(?:\?.*)?$')

        async def delayed_refresh(route):
            if route.request.method != 'GET':
                await route.fallback()
                return
            response = await route.fetch()
            ready.set()
            await release.wait()
            try:
                await route.fulfill(response=response)
            finally:
                completed.set()

        await page.route(pattern, delayed_refresh)
        try:
            await button(page, 'Änderungen speichern').click()
            await asyncio.wait_for(ready.wait(), timeout=10)
            await button(bank_row, 'Bankkonto bearbeiten').click()
        finally:
            release.set()
            if ready.is_set():
                await asyncio.wait_for(completed.wait(), timeout=10)
            await page.unroute(pattern, delayed_refresh)
        await expect(bank_row.get_by_text('Bestätigtes Konto Gartenflügel', exact=True)).to_have_count(1)
        await field(page, 'Bank').fill('Neue Testbank')
        await button(page, 'Änderungen speichern').click()
        await expect(page.get_by_role('dialog', name='Bankkonto bearbeiten', exact=True)).to_have_count(0)
        await expect(bank_row.get_by_role('cell', name='Neue Testbank', exact=True)).to_be_visible()
        results['canonical_name_retained'] = await bank_row.get_by_text('Bestätigtes Konto Gartenflügel', exact=True).count() == 1
        await capture('accounts-followup')
    b.run(run())
    d['followup_editor_results'] = results


@then('lost contact access closes the private form and the account retains its confirmed name')
def followup_editor_results(world):
    assert world.data['followup_editor_results'] == {'private_editor_closed': True, 'canonical_name_retained': True}
