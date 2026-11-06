"""Real keyboard, focus geometry and breakpoint checks for the app shell."""
import os
from pathlib import Path

from playwright.async_api import expect
from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.browser_setup import create

async def capture(browser, tmp_path, name):
    await browser.page.screenshot(path=str(tmp_path / f"{name}.png"), full_page=True)
    if os.environ.get("VERWALINO_CAPTURE_DESIGN") == "1":
        destination = Path(__file__).resolve().parents[4] / "docs/assets/design" / f"{name}.png"
        await browser.page.screenshot(path=str(destination), full_page=True)


async def visible_focus(page):
    focused = page.locator(":focus")
    await expect(focused).to_be_visible()
    geometry = await focused.evaluate("""e => {
        const r = e.getBoundingClientRect();
        return {name: e.getAttribute('aria-label') || e.textContent, left: r.left, top: r.top,
                right: r.right, bottom: r.bottom, width: innerWidth, height: innerHeight};
    }""")
    # Native scroll offsets are rounded while link bounds retain subpixel precision.
    assert (geometry["left"] >= -1 and geometry["top"] >= -1
            and geometry["right"] <= geometry["width"] + 1 and geometry["bottom"] <= geometry["height"] + 1), (
        f"Keyboard focus is outside the viewport: {geometry}"
    )


@given(parsers.parse("the signed-in manager opens the navigation dashboard at {width:d} pixels"))
def dashboard(browser_world, seed_data, width, tmp_path):
    b, world = browser_world, browser_world.world
    world.actor = world.administrator_of("Verwaltung Alpha")
    world.actor_organization = "Verwaltung Alpha"
    create(world, "/api/properties", {
        "name": "Mietobjekt am langen Sonnenhof mit Vorderhaus und Seitenflügel",
        "management_type": "MIET", "street": "Sonnenweg 5", "zip_code": "12345", "city": "Teststadt",
        "data_audience": {"label": "Navigation check", "reason": "Synthetic navigation check", "grants": [{
            "user_id": str(seed_data["user_a_admin_id"]), "capabilities": ["read", "prepare", "approve", "export"],
            "reason": "Synthetic navigation check",
        }]},
    }, keyed=True)

    async def run():
        await b.page.set_viewport_size({"width": width, "height": 900})
        await b.visit("/objekte")
        await expect(b.page.get_by_role("link", name="Mietobjekt am langen Sonnenhof mit Vorderhaus und Seitenflügel", exact=True).first).to_be_visible()
        await capture(b, tmp_path, f"list-objekte-{'360' if width == 360 else 'desktop'}")
        await b.visit("/")
        await expect(b.page.get_by_role("heading", level=1)).to_be_visible()
        await expect(b.page.get_by_role("button", name="Aufgaben erneut laden", exact=True)).to_be_enabled()
        await expect(b.page.get_by_text("Lade…", exact=True)).to_have_count(0)
        await capture(b, tmp_path, f"navigation-dashboard-{width}")
    b.run(run())


@then("Tab skips the closed navigation and focuses a visible control")
def closed_keyboard(browser_world, tmp_path):
    b = browser_world

    async def run():
        await b.page.keyboard.press("Tab")
        await capture(b, tmp_path, "navigation-closed-focus-360")
        await visible_focus(b.page)
        await expect(b.page.get_by_role("button", name="Navigation umschalten", exact=True)).to_be_focused()
        assert not await b.page.get_by_role("link", name="Dashboard", exact=True).count()
    b.run(run())


@when("the manager opens navigation and presses Escape")
def escape(browser_world, tmp_path):
    b = browser_world

    async def run():
        await b.page.get_by_role("button", name="Navigation umschalten", exact=True).click()
        await expect(b.page.get_by_role("link", name="Dashboard", exact=True)).to_be_in_viewport(ratio=1)
        await capture(b, tmp_path, "navigation-open-360")
        await b.page.keyboard.press("Escape")
    b.run(run())


@then("navigation is closed and its toggle has focus")
def closed_after_escape(browser_world, tmp_path):
    async def run():
        page = browser_world.page
        await expect(page.get_by_role("button", name="Navigation umschalten", exact=True)).to_be_focused()
        await expect(page.get_by_role("link", name="Dashboard", exact=True)).to_have_count(0)
        await capture(browser_world, tmp_path, "navigation-escape-360")
    browser_world.run(run())


@then("the manager can navigate and dismiss the drawer using the keyboard and backdrop")
def phone_controls(browser_world, tmp_path):
    b = browser_world

    async def run():
        page = b.page
        toggle = page.get_by_role("button", name="Navigation umschalten", exact=True)
        await page.keyboard.press("Tab")
        await expect(toggle).to_be_focused()
        await page.keyboard.press("Enter")
        dialog = page.get_by_role("dialog", name="Navigation", exact=True)
        await expect(dialog).to_be_visible()
        await expect(dialog).to_be_in_viewport(ratio=1)
        await visible_focus(page)
        await capture(b, tmp_path, "navigation-open-360")
        # Traverse past both ends of all navigation entries, including admin links.
        for key in ("Tab", "Shift+Tab"):
            for _ in range(await dialog.get_by_role("link").count() + 3):
                await page.keyboard.press(key)
                assert await page.locator(":focus").evaluate("e => !!e.closest('[role=dialog]')")
                await visible_focus(page)
        close = dialog.get_by_role("button", name="Navigation schließen", exact=True)
        await close.focus()
        await page.keyboard.press("Enter")
        await expect(toggle).to_be_focused()
        await page.keyboard.press("Enter")
        await dialog.get_by_role("link", name="Objekte", exact=True).focus()
        await page.keyboard.press("Enter")
        await expect(page).to_have_url("/objekte")
        await expect(page.get_by_role("heading", name="Objekte", exact=True)).to_be_visible()
        await expect(page.get_by_role("link", name="Mietobjekt am langen Sonnenhof mit Vorderhaus und Seitenflügel", exact=True).first).to_be_visible()
        await expect(toggle).to_be_focused()
        await capture(b, tmp_path, "list-objekte-360")
        await toggle.press("Enter")
        await expect(dialog).to_be_visible()
        await page.mouse.click(340, 100)
        await expect(dialog).to_have_count(0)
        await expect(toggle).to_be_focused()
    b.run(run())


@then("static navigation remains accessible when resizing across 768 pixels")
def resizing(browser_world, tmp_path):
    b = browser_world

    async def desktop_navigation():
        page = b.page
        await expect(page.get_by_role("dialog")).to_have_count(0)
        link = page.get_by_role("link", name="Objekte", exact=True)
        await expect(link).to_be_visible()
        await link.focus()
        await visible_focus(page)
        await page.keyboard.press("Enter")
        await expect(page).to_have_url("/objekte")
        await expect(page.get_by_role("heading", name="Objekte", exact=True)).to_be_visible()
        await expect(page.get_by_role("link", name="Mietobjekt am langen Sonnenhof mit Vorderhaus und Seitenflügel", exact=True).first).to_be_visible()

    async def run():
        page = b.page
        await desktop_navigation()
        await capture(b, tmp_path, "list-objekte-desktop")
        await page.set_viewport_size({"width": 767, "height": 900})
        toggle = page.get_by_role("button", name="Navigation umschalten", exact=True)
        await expect(toggle).to_be_focused()
        await expect(page.get_by_role("link", name="Dashboard", exact=True)).to_have_count(0)
        await toggle.press("Enter")
        await expect(page.get_by_role("dialog", name="Navigation", exact=True)).to_be_visible()
        await page.set_viewport_size({"width": 768, "height": 900})
        await expect(page.get_by_role("dialog")).to_have_count(0)
        await visible_focus(page)
        await expect(page.locator("nav a:focus")).to_have_count(1)
        await desktop_navigation()
        await page.set_viewport_size({"width": 360, "height": 900})
        await expect(toggle).to_be_focused()
        await expect(page.get_by_role("dialog")).to_have_count(0)
        await toggle.press("Enter")
        await expect(page.get_by_role("dialog", name="Navigation", exact=True)).to_be_visible()
        await page.keyboard.press("Escape")
        await expect(toggle).to_be_focused()
        await page.set_viewport_size({"width": 1280, "height": 900})
        await expect(page.locator('nav a[aria-current="page"]')).to_be_focused()
        await visible_focus(page)
        await desktop_navigation()
    b.run(run())


@when(parsers.parse('the manager closes phone navigation through "{method}"'))
def close_before_resize(browser_world, method):
    async def run():
        page = browser_world.page
        toggle = page.get_by_role("button", name="Navigation umschalten", exact=True)
        await page.keyboard.press("Tab")
        await expect(toggle).to_be_focused()
        await page.keyboard.press("Enter")
        dialog = page.get_by_role("dialog", name="Navigation", exact=True)
        await expect(dialog).to_be_in_viewport(ratio=1)
        if method == "Escape":
            await page.keyboard.press("Escape")
        else:
            await dialog.get_by_role("link", name=method, exact=True).focus()
            await page.keyboard.press("Enter")
            await expect(page).to_have_url("/objekte")
        await expect(dialog).to_have_count(0)
        await expect(toggle).to_be_focused()
    browser_world.run(run())


@then(parsers.parse("widening to {width:d} pixels moves focus to the active desktop navigation link"))
def closed_toggle_resize(browser_world, width, tmp_path):
    async def run():
        page = browser_world.page
        await page.set_viewport_size({"width": width, "height": 900})
        await expect(page.get_by_role("button", name="Navigation umschalten", exact=True)).to_be_hidden()
        await capture(browser_world, tmp_path, f"navigation-closed-resize-{width}")
        await expect(page.locator('nav a[aria-current="page"]')).to_be_focused()
        await visible_focus(page)
    browser_world.run(run())
