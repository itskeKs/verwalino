"""Meldungen: the frame entry with its count, the list, and who is told."""
import re

from playwright.async_api import expect
from pytest_bdd import then

from tests.bdd.steps.browser_common import sign_in_context
from tests.bdd.steps.browser_declaration_principal import CLERK, expect_waiting_status_page


def meldungen_entry(page):
    return page.get_by_role("link", name="Meldungen")


def meldungen_rows(page):
    return page.get_by_role("list", name="Meldungen").get_by_role("listitem")


async def expect_count_equals_rows(page):
    """On the list: the number in the frame entry is the number of rows (the database of a local run keeps other tests' rows)."""
    badge = meldungen_entry(page).locator(".topbar-count")
    await expect(badge).to_have_text(re.compile(r"^\d+$"))
    count = int(await badge.inner_text())
    await expect(meldungen_rows(page)).to_have_count(count)
    return count


@then("the clerk finds the import under Meldungen on another page and follows it to its status page")
def clerk_follows_the_meldung(browser_world):
    async def run():
        job = browser_world.world.data["job"]
        context, page = await sign_in_context(browser_world, *CLERK)
        try:
            await page.goto("/objekte")
            await expect(meldungen_entry(page)).to_have_attribute("href", "/meldungen")
            await meldungen_entry(page).click()
            await expect(page.get_by_role("heading", name="Meldungen", exact=True)).to_be_visible()
            assert await expect_count_equals_rows(page) >= 1
            row = meldungen_rows(page).filter(has=page.locator(f'a[href="/teilungserklaerung/import/{job}"]'))
            await expect(row).to_have_count(1)
            await expect(row).to_contain_text("Teilungserklärung einlesen")
            await expect(row).to_contain_text("Neues Objekt: synthetic.pdf")
            await expect(row).to_contain_text("Wartet auf Verarbeitung")
            await expect(row).to_contain_text("Gestartet:")
            await row.get_by_role("link").click()
            await page.wait_for_url(f"**/teilungserklaerung/import/{job}")
            await expect_waiting_status_page(page)
        finally:
            await context.close()
    browser_world.run(run())


@then("the clerk's Meldungen are readable at 360 pixels")
def clerk_meldungen_on_a_phone(browser_world):
    async def run():
        job = browser_world.world.data["job"]
        context, page = await sign_in_context(browser_world, *CLERK)
        try:
            await page.set_viewport_size({"width": 360, "height": 800})
            await page.goto("/meldungen")
            # The entry is reachable on every page of the phone frame, with its count.
            await expect(meldungen_entry(page)).to_be_visible()
            assert await expect_count_equals_rows(page) >= 1
            row = meldungen_rows(page).filter(has=page.locator(f'a[href="/teilungserklaerung/import/{job}"]'))
            await expect(row).to_contain_text("Neues Objekt: synthetic.pdf")
            await expect(row).to_contain_text("Wartet auf Verarbeitung")
            assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
            for widths in await page.locator("main").evaluate_all(
                    "nodes => nodes.map(node => ({visible: node.clientWidth, content: node.scrollWidth}))"):
                assert widths["content"] <= widths["visible"] + 1, widths
        finally:
            await context.close()
    browser_world.run(run())


@then("the administrator has no Meldung of that import")
def administrator_has_none(browser_world):
    async def run():
        job = browser_world.world.data["job"]
        page = browser_world.page
        await browser_world.visit("/objekte")
        await meldungen_entry(page).click()
        await expect(page.get_by_role("heading", name="Meldungen", exact=True)).to_be_visible()
        await expect_count_equals_rows(page)
        await expect(page.locator(f'a[href="/teilungserklaerung/import/{job}"]')).to_have_count(0)
    browser_world.run(run())
