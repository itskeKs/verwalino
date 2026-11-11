"""Persist personal appearance through the real settings and navigation."""
import os
from pathlib import Path

from playwright.async_api import expect
from pytest_bdd import given, then, when

from tests.bdd.steps.browser_common import browser_step
from tests.bdd.steps.records import create_property

ROOT = Path(__file__).resolve().parents[4]


@given('the personal design settings are open')
def personal_settings(world, browser_world, request):
    client = world.administrator_of('Verwaltung Alpha')
    before = world.look(client, '/api/auth/preferences')
    assert before.status_code == 200
    assert world.send(client, 'PATCH', '/api/auth/preferences', json={'design_theme': 'klar'}).status_code == 200
    request.addfinalizer(lambda: world.run(client.patch('/api/auth/preferences', json=before.json())))
    browser_world.run(browser_world.visit('/einstellungen'))


@when('the user saves Warm and reloads the settings')
@browser_step
async def save_warm(browser_world):
    page = browser_world.page
    await expect(page.get_by_role('radio', name='Warm', exact=False)).to_be_enabled()
    await page.get_by_role('radio', name='Warm', exact=False).check()
    await expect(page.locator('.theme-save-row').get_by_role('status')).to_have_text('Noch nicht gespeichert.')
    await page.get_by_role('button', name='Design speichern', exact=True).click()
    await expect(page.locator('.theme-save-row').get_by_role('status')).to_have_text('Design für Ihr Benutzerkonto gespeichert.')
    await page.reload()


@then('Warm is selected and the object navigation works')
@browser_step
async def warm_persists(browser_world):
    page = browser_world.page
    await expect(page.get_by_role('radio', name='Warm', exact=False)).to_be_checked()
    await expect(page.locator('.app-layout')).to_have_attribute('data-design-theme', 'warm')
    await page.get_by_role('link', name='Objekte', exact=True).click()
    await expect(page.get_by_role('heading', name='Objekte', exact=True)).to_be_visible()

    nav = page.get_by_role('navigation', name='Hauptnavigation')
    more = nav.get_by_text('Weitere Bereiche', exact=True)
    panel = nav.locator('.navigation-groups')
    for width in (1024, 1280):
        await page.set_viewport_size({'width': width, 'height': 900})
        await more.click()
        await expect(panel).to_be_in_viewport(ratio=1)
        await more.click()
    await more.click()
    await nav.locator('.navigation-group > summary').filter(has_text='Zahlungen').click()
    await nav.get_by_role('link', name='Bankkonten', exact=True).click()
    await expect(page.get_by_role('heading', name='Bankkonten', exact=True)).to_be_visible()


@when('the user opens the settings on a narrow screen and saves Klar')
@browser_step
async def save_klar_on_phone(browser_world):
    page = browser_world.page
    await page.set_viewport_size({'width': 360, 'height': 844})
    await password_targets(page)
    await page.locator('.topbar-settings').click()
    await expect(page.get_by_role('radio', name='Klar', exact=False)).to_be_enabled()
    await page.get_by_role('radio', name='Klar', exact=False).check()
    await page.get_by_role('button', name='Design speichern', exact=True).click()
    await expect(page.locator('.theme-save-row').get_by_role('status')).to_have_text('Design für Ihr Benutzerkonto gespeichert.')


@then('Klar is selected and the mobile navigation is usable')
@browser_step
async def mobile_navigation(browser_world):
    page = browser_world.page
    await expect(page.get_by_role('radio', name='Klar', exact=False)).to_be_checked()
    await expect(page.locator('.app-layout')).to_have_attribute('data-design-theme', 'klar')
    geometry = await page.locator('.mobile-shortcuts').evaluate('element => ({top: element.getBoundingClientRect().top, heights: [...element.querySelectorAll("a")].map(a => a.getBoundingClientRect().height), overflow: document.documentElement.scrollWidth > innerWidth})')
    assert geometry['top'] < 150 and all(height >= 44 for height in geometry['heights'])
    assert not geometry['overflow']
    await password_targets(page)
    await page.get_by_role('button', name='Navigation umschalten', exact=True).click()
    dialog = page.get_by_role('dialog')
    await expect(dialog).to_be_visible()
    if await dialog.locator('.navigation-more').get_attribute('open') is None:
        await dialog.get_by_text('Weitere Bereiche', exact=True).click()
    await dialog.locator('.navigation-group > summary').filter(has_text='Zahlungen').click()
    await dialog.get_by_role('link', name='Bankkonten', exact=True).click()
    await expect(dialog).not_to_be_visible()
    await expect(page.get_by_role('heading', name='Bankkonten', exact=True)).to_be_visible()


@given('a synthetic property for the design table')
def design_table_property(world):
    create_property(world, 'Verwaltung Alpha', 'Musterhaus an der Musterstraße 12')


async def capture_screen(page, path, width, whole=False):
    """Save one screenshot and say whether the main area overflows horizontally.

    The app shell scrolls inside <main> at the viewport height, so a full-page shot is only
    the viewport and the page itself never grows wider; a whole screen enlarges the
    viewport to its content before the shot, and the width is read from <main>."""
    main = page.locator('main')
    await page.wait_for_load_state('networkidle')
    if whole:
        hidden = await main.evaluate('e => e.scrollHeight - e.clientHeight')
        await page.set_viewport_size({'width': width, 'height': 900 + hidden})
        await page.evaluate('() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))')
    await page.screenshot(path=str(path))
    geometry = await main.evaluate('e => ({width: e.clientWidth, content: e.scrollWidth})')
    if whole:
        await page.set_viewport_size({'width': width, 'height': 900})
    return geometry['content'] > geometry['width'] + 1


@when('the main screens are captured in both designs at 1280 and 360 pixels')
@browser_step
async def capture_design_table(browser_world, world, tmp_path):
    """Refresh the DESIGN.md theme table; with VERWALINO_CAPTURE_DESIGN unset the
    captures land in the temporary directory, so the scenario also runs as a test."""
    page = browser_world.page
    # The API World loop cannot run inside the browser loop; the authenticated
    # context shares the session cookie, so it speaks to the app directly.
    api = browser_world.context.request
    before = await api.get('/api/auth/preferences')
    assert before.ok, f'GET /api/auth/preferences answered {before.status}'
    original = await before.json()
    directory = ROOT / 'docs/assets/design' if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1' else tmp_path
    overflows = world.data['design_table_overflows'] = []
    screens = (
        ('dashboard', '/', page.get_by_role('region', name='Ihr Bestand', exact=True), False),
        ('properties', '/objekte', page.get_by_role('heading', name='Objekte', exact=True), False),
        ('settings', '/einstellungen', page.get_by_role('radio', name='Klar', exact=False), True),
    )
    try:
        for theme in ('klar', 'warm'):
            answer = await api.patch('/api/auth/preferences', data={'design_theme': theme})
            assert answer.ok, f'PATCH /api/auth/preferences answered {answer.status}'
            for width in (1280, 360):
                await page.set_viewport_size({'width': width, 'height': 900})
                for name, path, ready, whole in screens:
                    await browser_world.visit(path)
                    await expect(ready).to_be_visible()
                    await expect(page.locator('.app-layout')).to_have_attribute('data-design-theme', theme)
                    if name == 'dashboard':
                        await expect(page.get_by_text('Aufgaben werden geladen…', exact=True)).to_be_hidden()
                        await expect(page.locator('.dashboard-portfolio-count')).not_to_contain_text('…')
                    if name == 'properties':
                        await expect(page.get_by_role('link', name='Musterhaus an der Musterstraße 12', exact=True)).to_be_visible()
                        await page.get_by_role('button', name='+ Neues Objekt', exact=True).click()
                        await expect(page.get_by_role('heading', name='Neues Objekt anlegen', exact=True)).to_be_visible()
                        if await capture_screen(page, directory / f'themes-{theme}-form-{width}.png', width):
                            overflows.append(f'{theme}/form/{width}')
                        await page.get_by_role('button', name='Abbrechen', exact=True).click()
                    if await capture_screen(page, directory / f'themes-{theme}-{name}-{width}.png', width, whole):
                        overflows.append(f'{theme}/{name}/{width}')
    finally:
        await api.patch('/api/auth/preferences', data=original)


@then('every captured screen fits its viewport')
def design_table_fits(world):
    overflows = world.data.get('design_table_overflows')
    assert overflows == [], f'Horizontal overflow: {overflows}'


async def password_targets(page):
    await page.get_by_role('button', name='Passwort ändern', exact=True).click()
    dialog = page.get_by_role('dialog')
    await expect(dialog).to_be_visible()
    for button in await dialog.get_by_role('button').all():
        bounds = await button.bounding_box()
        assert bounds and bounds['width'] >= 44 and bounds['height'] >= 44
    await dialog.get_by_role('button', name='Schließen', exact=True).click()
