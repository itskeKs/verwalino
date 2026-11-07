"""Real sign-in feedback and correction at desktop and phone widths."""
import os
from pathlib import Path

from playwright.async_api import expect
from pytest_bdd import given, parsers, scenarios, then, when

from tests.bdd.world import admin_credentials

scenarios(str(Path(__file__).resolve().parents[3] / "features/public_auth_design.feature"))


@given(parsers.parse("the public login is open at {width:d} pixels"))
def public_login(browser_world, width):
    async def run():
        page = browser_world.page
        await browser_world.context.clear_cookies()
        await page.set_viewport_size({"width": width, "height": 900})
        await browser_world.visit("/login")
        await expect(page.get_by_role("heading", name="Anmelden", exact=True)).to_be_visible()
        await expect(page.get_by_label("E-Mail", exact=True)).to_be_focused()
    browser_world.run(run())


@when("the user submits an incorrect password")
def incorrect_password(browser_world):
    async def run():
        page = browser_world.page
        email, _ = admin_credentials()
        await page.get_by_label("E-Mail", exact=True).fill(email)
        await page.get_by_label("Passwort", exact=True).fill("Incorrect-password-387")
        await page.get_by_role("button", name="Anmelden", exact=True).click()
    browser_world.run(run())


@then("the sign-in error is focused and the email remains available")
def sign_in_error(browser_world):
    async def run():
        page = browser_world.page
        error = page.get_by_role("alert")
        await expect(error).to_be_visible()
        await expect(error).to_be_focused()
        await expect(error).to_be_in_viewport(ratio=1)
        await expect(page.get_by_label("E-Mail", exact=True)).to_have_value(admin_credentials()[0])
        sizes = await page.evaluate("({content: document.documentElement.scrollWidth, viewport: innerWidth})")
        assert sizes["content"] <= sizes["viewport"], sizes
        if os.environ.get("VERWALINO_CAPTURE_DESIGN") == "1":
            destination = Path(__file__).resolve().parents[3] / "docs/assets/design"
            await page.screenshot(path=str(destination / f"login-error-{page.viewport_size['width']}.png"))
    browser_world.run(run())


@when("the user signs in with the correct password")
def corrected_password(browser_world):
    async def run():
        page = browser_world.page
        await page.get_by_label("Passwort", exact=True).fill(admin_credentials()[1])
        await page.get_by_role("button", name="Anmelden", exact=True).click()
    browser_world.run(run())


@then("the authenticated overview is available")
def authenticated_overview(browser_world):
    async def run():
        page = browser_world.page
        await expect(page.get_by_role("heading", name="Guten Tag", exact=True)).to_be_visible()
        await expect(page.get_by_role("button", name="Abmelden", exact=True)).to_be_visible()
    browser_world.run(run())
