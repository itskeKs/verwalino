"""Browser step adapter and shared UI operations; assertions use visible content."""
from functools import wraps
from urllib.parse import urlsplit

from playwright.async_api import expect


def browser_step(function):
    """pytest-bdd steps are synchronous; drive Playwright's own session loop."""
    @wraps(function)
    def run(browser_world, *args, **kwargs):
        return browser_world.run(function(browser_world, *args, **kwargs))
    return run


def button(scope, name):
    return scope.get_by_role("button", name=name, exact=True)


def field(scope, name):
    return scope.get_by_label(name, exact=True)


async def submit(browser, name, path, *, scope=None, status=201):
    page = browser.page
    async with page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == path) as pending:
        await button(scope or page, name).click()
    answer = await pending.value
    assert answer.status == status, f"{path}: {answer.status} {await answer.text()}"
    return await answer.json()


async def ledger(browser, *, start="2026-01-01", end="2026-12-31"):
    await browser.visit("/mietkonto")
    await browser.select("Mietvertrag *", browser.world.data["leases"][0]["id"])
    await browser.scope()
    await field(browser.page, "Zeitraum von *").fill(start)
    await field(browser.page, "Zeitraum bis *").fill(end)
    await button(browser.page, "Abrufen").click()
    await expect(browser.page.get_by_role("cell", name="Soll gesamt", exact=True)).to_be_visible()


async def ledger_row(browser, period, kind, expected):
    page = browser.page
    row = page.get_by_role("row").filter(has=page.get_by_role("cell", name=period, exact=True)).filter(
        has=page.get_by_role("cell", name=kind, exact=True))
    await expect(row.get_by_role("cell")).to_have_text([period, kind, *[f"{value} EUR" for value in expected]])
