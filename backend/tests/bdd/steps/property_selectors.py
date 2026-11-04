"""Native selector geometry and keyboard use with real long property names."""

import os
from pathlib import Path

import pytest
from playwright.async_api import expect
from pytest_bdd import given, parsers, then, when

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
    if screen == 'CostForm':
        categories = world.act('GET', '/api/cost-categories')
        assert categories.status_code == 200, world.describe_response()
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
