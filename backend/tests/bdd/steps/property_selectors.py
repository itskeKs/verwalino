"""Native selector geometry and keyboard use with real long property names."""

import os
from pathlib import Path

import pytest
from playwright.async_api import expect
from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.browser_setup import create
from tests.bdd.steps.records import create_bank_account, create_person, create_property

LONG_NAME = 'WEG Wohnpark Friedrich-Wilhelm-Höfe mit Tiefgarage und Gemeinschaftsgarten'
SHORT_NAME = 'Kurzweg 1'
SCREENS = {
    'Tasks': ('/aufgaben', 'Objektfilter'),
    'Letters': ('/briefe', 'Objekt *'),
    'Reports': ('/berichte', 'Objekt *'),
    'Costs': ('/kosten', 'Objektfilter'),
    'CostForm': ('/kosten', 'Objekt *'),
    'Levies': ('/sonderumlagen', 'WEG-Objekt *'),
    'Meetings': ('/versammlungen', None),
    'Resolutions': ('/beschluss-sammlung', None),
    'Payments': ('/zahlungen', 'Bankkontofilter'),
    'Person': (None, 'Objekt *'),
}
HEADINGS = {
    'Tasks': 'Aufgaben', 'Letters': 'Briefe', 'Reports': 'Berichte', 'Costs': 'Kostenpositionen',
    'CostForm': 'Kostenpositionen',
    'Levies': 'Sonderumlagen', 'Meetings': 'Eigentuemerversammlungen',
    'Resolutions': 'Beschluss-Sammlung', 'Payments': 'Zahlungen', 'Person': 'Selector Contact',
}


def property_selector(page, screen):
    label = SCREENS[screen][1]
    return page.get_by_role('combobox', name=label, exact=True) if label else page.get_by_role('main').locator('select').first


@given('properties with a long and a short name are available')
def selector_properties(world, property_selector_records):
    if not property_selector_records:
        property_selector_records['properties'] = [
            create_property(world, 'Verwaltung Alpha', name) for name in (LONG_NAME, SHORT_NAME)
        ]
    world.data['selector_properties'] = property_selector_records['properties']


@pytest.fixture(scope='module')
def property_selector_records():
    # These scenarios only read/select records; share them to retain hosted quota headroom.
    return {}


@when(parsers.parse('the manager opens {screen} at {width:d} pixels wide'))
def open_selector_page(browser_world, property_selector_records, screen, width):
    world = browser_world.world
    path = SCREENS[screen][0]
    if screen == 'Person':
        if 'person' not in property_selector_records:
            property_selector_records['person'] = create_person(world, 'Verwaltung Alpha', 'Selector Contact')
        person = property_selector_records['person']
        path = f'/personen/{person.id}'
    if screen == 'Payments':
        properties = world.data['selector_properties']
        if 'accounts' not in property_selector_records:
            property_selector_records['accounts'] = [
                create_bank_account(world, 'Verwaltung Alpha', label, property_id=home.id)
                for home, label in zip(properties, (
                    'Hausgeldkonto Wohnpark Friedrich-Wilhelm-Höfe mit Tiefgarage und Gemeinschaftsgarten – laufende Bewirtschaftung und Erhaltungsrücklage',
                    'Giro'))]
        accounts = property_selector_records['accounts']
        world.data['selector_properties'] = accounts
        world.data['selector_labels'] = [f'{account.name} ({home.name})' for account, home in zip(accounts, properties)]
    else:
        world.data['selector_labels'] = [record.name for record in world.data['selector_properties']]
    if screen in ('Costs', 'CostForm'):
        categories = world.act('GET', '/api/cost-categories')
        assert categories.status_code == 200, world.describe_response()
        if 'costs' not in property_selector_records:
            category = next(item for item in categories.json() if item['code'] == 'GARTENPFLEGE')
            property_selector_records['costs'] = {
                home.id: create(world, '/api/cost-entries', {
                    'property_id': home.id, 'cost_category_id': category['id'],
                    'cost_category_plan_version': category['plan_version'],
                    'invoice_date': '2026-12-31', 'service_period_from': '2026-01-01',
                    'service_period_to': '2026-12-31', 'gross_amount': '1250.00',
                    'tenant_apportionable': 'UMLAGEFAEHIG',
                })['id'] for home in property_selector_records['properties']
            }
        world.data['selector_costs'] = property_selector_records['costs']
    if screen == 'CostForm':
        longest = max(categories.json(), key=lambda item: len(item['code'] + ' ' + item['label']))
        category_label = f'{longest["code"]} {longest["label"]}'

    async def open_page():
        await browser_world.page.set_viewport_size({'width': width, 'height': 900})
        await browser_world.visit(path)
        await expect(browser_world.page.get_by_role('heading', name=HEADINGS[screen], exact=True)).to_be_visible()
        if screen == 'CostForm':
            await browser_world.page.get_by_role('button', name='+ Neue Kostenposition', exact=True).click()
            category = browser_world.page.get_by_role('combobox', name='Kategorie *', exact=True)
            await expect(category.locator(f'option[value="{longest["id"]}"]')).to_have_text(category_label)
        selector = property_selector(browser_world.page, screen)
        option = selector.locator(f'option[value="{world.data["selector_properties"][0].id}"]')
        await expect(option).to_have_text(world.data['selector_labels'][0])
        await expect(selector).to_be_enabled()

    browser_world.world.data['selector_screen'] = screen
    browser_world.run(open_page())


@then('the property selector and surrounding controls fit before and after keyboard selection')
def selectors_fit(browser_world):
    browser_world.run(check_selector(browser_world))


async def check_selector(browser):
    page = browser.page
    screen = browser.world.data['selector_screen']
    selector = property_selector(page, screen)
    main = page.get_by_role('main')
    failures = []
    measurements = []
    choices = zip(('long', 'short'), browser.world.data['selector_properties'], browser.world.data['selector_labels'])
    for state, record, label in [('empty', None, ''), *choices]:
        # Reach the selector through the actual tab order, including after rerenders.
        for _ in range(80):
            if await selector.evaluate('element => element === document.activeElement'):
                break
            await page.keyboard.press('Tab')
        await expect(selector).to_be_focused()
        if record:
            option = selector.locator(f'option[value="{record.id}"]')
            await expect(option).to_have_text(label)
            # Native typeahead works across macOS and Linux Chromium keyboard conventions.
            # Leaving and returning clears the previous selection's typeahead buffer.
            await page.keyboard.press('Shift+Tab')
            await page.keyboard.press('Tab')
            await expect(selector).to_be_focused()
            await page.keyboard.type(label)
            await expect(selector).to_have_value(record.id)
        else:
            await expect(selector).to_have_value('')
        await expect(selector).to_be_enabled()
        if screen in ('Costs', 'CostForm'):
            await expect(main.get_by_role('button', name='CSV herunterladen', exact=True)).to_be_enabled()
            for property_id, cost_id in browser.world.data['selector_costs'].items():
                details = main.locator(f'a[href="/kosten/{cost_id}"]')
                if screen == 'Costs' and record and property_id != record.id:
                    await expect(details).to_have_count(0)
                else:
                    await expect(details).to_be_visible()
        alerts = await main.get_by_role('alert').all_text_contents()
        assert not alerts, f'{screen} {state} alerts: {alerts}'
        await expect(page.locator('vite-error-overlay')).to_have_count(0)
        geometry = await main.evaluate('''main => {
            const bounds = main.getBoundingClientRect();
            const outside = [...main.querySelectorAll('input, select, textarea, button, label')]
                .filter(element => element.getClientRects().length)
                .filter(element => {
                    const rect = element.getBoundingClientRect();
                    return rect.left < bounds.left - 0.5 || rect.right > bounds.right + 0.5;
                }).map(element => element.getAttribute('aria-label') || element.textContent);
            return {scroll: main.scrollWidth, client: main.clientWidth, outside};
        }''')
        focus = await selector.evaluate('''element => {
            const rect = element.getBoundingClientRect();
            const panel = element.closest('.card, main').getBoundingClientRect();
            const style = getComputedStyle(element);
            // The form-grid shadow extends 3 px; native outlines need 2 px.
            const halo = style.boxShadow !== 'none' ? 3 : 2;
            return {left: rect.left - halo, right: rect.right + halo,
                panelLeft: panel.left, panelRight: panel.right,
                visible: style.boxShadow !== 'none' || style.outlineStyle !== 'none'};
        }''')
        measurements.append(f'{screen} {page.viewport_size["width"]} {state}: {geometry}; focus={focus}')
        if geometry['scroll'] > geometry['client'] or geometry['outside']:
            failures.append(measurements[-1])
        if not focus['visible'] or focus['left'] < focus['panelLeft'] or focus['right'] > focus['panelRight']:
            failures.append(f'Focus does not fit: {measurements[-1]}')
        if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1' and state != 'short':
            directory = Path(__file__).resolve().parents[4] / 'docs/assets/design/property-selectors'
            directory.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(directory / f'after-{screen.lower()}-{state}-{page.viewport_size["width"]}.png'), animations='disabled')
    print('\n'.join(measurements))
    assert not failures, '\n'.join(failures)


async def tab_to_task_control(page, control):
    for _ in range(30):
        if await control.evaluate('element => element === document.activeElement'):
            break
        await page.keyboard.press('Tab')
    await expect(control).to_be_focused()


@when(parsers.parse('the Tasks {mode} dialog is opened at {width:d} pixels wide'))
def open_task_dialog(browser_world, mode, width):
    world = browser_world.world
    title = f'Selector dialog {mode} {width}'
    values = {'title': title, 'note': 'Fenster und Gemeinschaftsgarten prüfen.', 'due_date': '2026-12-15'}
    if mode == 'Edit':
        create(world, '/api/tasks', values)
    world.data.update(task_dialog_mode=mode, task_dialog_values=values)

    async def open_dialog():
        page = browser_world.page
        await page.set_viewport_size({'width': width, 'height': 800 if width == 360 else 900})
        await browser_world.visit('/aufgaben')
        if mode == 'Create':
            await page.get_by_role('button', name='Aufgabe anlegen', exact=True).click()
        else:
            row = page.get_by_role('listitem').filter(has=page.get_by_role('heading', name=title, exact=True))
            await row.get_by_role('button', name='Bearbeiten', exact=True).click()
        await expect(page.get_by_role('dialog').get_by_role('combobox', name='Objekt', exact=True)).to_be_enabled()
    browser_world.run(open_dialog())


@then('the task dialog contains its controls and preserves save and cancel behavior')
def task_dialog_fits(browser_world):
    browser_world.run(check_task_dialog(browser_world))


async def check_task_dialog(browser):
    page = browser.page
    dialog = page.get_by_role('dialog')
    mode = browser.world.data['task_dialog_mode']
    values = browser.world.data['task_dialog_values']
    selector = dialog.get_by_role('combobox', name='Objekt', exact=True)
    title = dialog.get_by_label('Titel *', exact=True)
    note = dialog.get_by_role('textbox', name='Notiz', exact=True)
    due = dialog.get_by_label('Fällig am', exact=True)
    assigned = dialog.get_by_role('combobox', name='Zuständig', exact=True)
    initial_assignee = await assigned.input_value()
    assert initial_assignee
    for control, value in ((title, values['title']), (note, values['note']), (due, values['due_date'])):
        await expect(control).to_have_value(value if mode == 'Edit' else '')
        await control.fill(value)
    failures = []
    properties = browser.world.data['selector_properties']
    for state, home in [('empty', None), ('long', properties[0]), ('short', properties[1])]:
        await tab_to_task_control(page, selector)
        if home:
            await expect(selector.locator(f'option[value="{home.id}"]')).to_have_text(home.name)
            await page.keyboard.press('Shift+Tab')
            await page.keyboard.press('Tab')
            await page.keyboard.type(home.name)
        await expect(selector).to_have_value(home.id if home else '')
        await expect(selector).to_be_focused()
        for control, value in ((title, values['title']), (note, values['note']),
                               (due, values['due_date']), (assigned, initial_assignee)):
            await expect(control).to_have_value(value)
        bounds = await dialog.evaluate('''dialog => {
            const panel = dialog.getBoundingClientRect();
            const select = dialog.querySelector('select[aria-label="Objekt"]');
            const rect = select.getBoundingClientRect();
            const style = getComputedStyle(select);
            const halo = style.boxShadow !== 'none' ? 3 :
                Math.max(0, parseFloat(style.outlineWidth) + parseFloat(style.outlineOffset));
            return {client: dialog.clientWidth, scroll: dialog.scrollWidth,
                left: panel.left, right: panel.right, viewport: innerWidth,
                selectorLeft: rect.left, selectorRight: rect.right, selectorWidth: rect.width,
                focusVisible: style.boxShadow !== 'none' || style.outlineStyle !== 'none',
                focusFits: rect.left - halo >= panel.left && rect.right + halo <= panel.right,
                outside: [...dialog.querySelectorAll('input, select, textarea, label, button, p')]
                    .filter(element => element.getClientRects().length)
                    .filter(element => {
                        const r = element.getBoundingClientRect();
                        return r.left < panel.left - 0.5 || r.right > panel.right + 0.5;
                    }).map(element => element.getAttribute('aria-label') || element.tagName)};
        }''')
        fits = (bounds['scroll'] <= bounds['client'] and not bounds['outside']
                and bounds['left'] >= 0 and bounds['right'] <= bounds['viewport']
                and bounds['focusVisible'] and bounds['focusFits'])
        print(f'Task dialog {mode} {page.viewport_size["width"]} {state}: {bounds}')
        if not fits:
            failures.append(f'{mode} {state}: {bounds}')
        if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1' and state == 'long':
            directory = Path(__file__).resolve().parents[4] / 'docs/assets/design/property-selectors'
            phase = 'after' if fits else 'before'
            await page.screenshot(path=str(directory / f'task-dialog-{mode.lower()}-{phase}-{page.viewport_size["width"]}.png'), animations='disabled')
    await tab_to_task_control(page, dialog.get_by_role('button', name='Abbrechen', exact=True))
    await page.keyboard.press('Enter')
    await expect(dialog).to_have_count(0)
    row = page.get_by_role('listitem').filter(has=page.get_by_role('heading', name=values['title'], exact=True))
    if mode == 'Create':
        await expect(row).to_have_count(0)
        await page.get_by_role('button', name='Aufgabe anlegen', exact=True).click()
    else:
        await row.get_by_role('button', name='Bearbeiten', exact=True).click()
    await expect(selector).to_have_value('')
    for control, value in ((title, values['title']), (note, values['note']), (due, values['due_date'])):
        await expect(control).to_have_value(value if mode == 'Edit' else '')
    saved_title = values['title'] + ' saved'
    await title.fill(saved_title)
    await note.fill(values['note'])
    await due.fill(values['due_date'])
    await tab_to_task_control(page, selector)
    await page.keyboard.type(properties[0].name)
    await expect(selector).to_have_value(properties[0].id)
    await tab_to_task_control(page, dialog.get_by_role('button', name='Speichern', exact=True))
    await page.keyboard.press('Enter')
    await expect(dialog).to_have_count(0)
    saved = page.get_by_role('listitem').filter(has=page.get_by_role('heading', name=saved_title, exact=True))
    await expect(saved).to_be_visible()
    await page.reload()
    await saved.get_by_role('button', name='Bearbeiten', exact=True).click()
    for control, value in ((title, saved_title), (note, values['note']), (due, values['due_date']),
                           (assigned, initial_assignee), (selector, properties[0].id)):
        await expect(control).to_have_value(value)
    await dialog.get_by_role('button', name='Abbrechen', exact=True).click()
    await expect(dialog).to_have_count(0)
    assert not failures, '\n'.join(failures)


@when(parsers.parse('the manager starts a new property at {width:d} pixels wide'))
def open_property_form(browser_world, width):
    async def open_form():
        await browser_world.page.set_viewport_size({'width': width, 'height': 900})
        await browser_world.visit('/objekte')
        await browser_world.page.get_by_role('button', name='+ Neues Objekt', exact=True).click()
        await expect(browser_world.page.get_by_role('heading', name='Neues Objekt anlegen')).to_be_visible()
    browser_world.run(open_form())


@then('its controls fit the established responsive columns')
def property_form_columns(browser_world):
    async def check_form():
        page = browser_world.page
        form = page.locator('form.form-grid')
        bounds = await form.evaluate('''form => {
            const controls = [...form.querySelectorAll('input, select')].map(element => element.getBoundingClientRect());
            const rect = form.getBoundingClientRect();
            return {fits: controls.every(control => control.left >= rect.left && control.right <= rect.right + 0.5),
                sameRow: Math.abs(controls[0].top - controls[1].top) < 1,
                sameColumn: Math.abs(controls[0].left - controls[1].left) < 1};
        }''')
        assert bounds['fits'], bounds
        assert bounds['sameRow'] == (page.viewport_size['width'] == 1280), bounds
        assert bounds['sameColumn'] == (page.viewport_size['width'] == 360), bounds
        if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1':
            directory = Path(__file__).resolve().parents[4] / 'docs/assets/design'
            suffix = 'desktop' if page.viewport_size['width'] == 1280 else '360'
            await page.screenshot(path=str(directory / f'form-objekt-{suffix}.png'), animations='disabled')
    browser_world.run(check_form())
