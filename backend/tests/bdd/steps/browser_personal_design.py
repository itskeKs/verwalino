"""Persist personal appearance through the real settings and navigation."""
from playwright.async_api import expect
from pytest_bdd import given, then, when

from tests.bdd.steps.browser_common import browser_step


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
    await expect(page.get_by_role('status')).to_have_text('Noch nicht gespeichert.')
    await page.get_by_role('button', name='Design speichern', exact=True).click()
    await expect(page.get_by_role('status')).to_have_text('Design für Ihr Benutzerkonto gespeichert.')
    await page.reload()


@then('Warm is selected and the object navigation works')
@browser_step
async def warm_persists(browser_world):
    page = browser_world.page
    await expect(page.get_by_role('radio', name='Warm', exact=False)).to_be_checked()
    await expect(page.locator('.app-layout')).to_have_attribute('data-design-theme', 'warm')
    await page.get_by_role('link', name='Objekte', exact=True).click()
    await expect(page.get_by_role('heading', name='Objekte', exact=True)).to_be_visible()


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
    await expect(page.get_by_role('status')).to_have_text('Design für Ihr Benutzerkonto gespeichert.')


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
    await dialog.get_by_text('Zahlungen', exact=True).first.click()
    await dialog.get_by_role('link', name='Bankkonten', exact=True).click()
    await expect(dialog).not_to_be_visible()
    await expect(page.get_by_role('heading', name='Bankkonten', exact=True)).to_be_visible()


async def password_targets(page):
    await page.get_by_role('button', name='Passwort ändern', exact=True).click()
    dialog = page.get_by_role('dialog')
    await expect(dialog).to_be_visible()
    for button in await dialog.get_by_role('button').all():
        bounds = await button.bounding_box()
        assert bounds and bounds['width'] >= 44 and bounds['height'] >= 44
    await dialog.get_by_role('button', name='Schließen', exact=True).click()
