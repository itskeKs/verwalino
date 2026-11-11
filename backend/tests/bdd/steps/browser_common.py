"""Browser step adapter and shared UI operations; assertions use visible content."""
import asyncio
from datetime import date
from decimal import Decimal
import json
from functools import wraps
from urllib.parse import urlsplit

from playwright.async_api import expect

from tests.bdd.browser import VIEWPORT


def euro(amount):
    """Money as the UI shows it (DESIGN.md formats): 1.234,56 €, sign first."""
    value = Decimal(str(amount)).quantize(Decimal("0.01"))
    grouped = f"{abs(value):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{'-' if value < 0 else ''}{grouped} €"


def german_date(iso):
    """A plain ISO date as the UI shows it (DESIGN.md formats): TT.MM.JJJJ."""
    return date.fromisoformat(iso).strftime("%d.%m.%Y")


def browser_step(function):
    """pytest-bdd steps are synchronous; drive Playwright's own session loop."""
    @wraps(function)
    def run(browser_world, *args, **kwargs):
        return browser_world.run(function(browser_world, *args, **kwargs))
    return run


async def sign_in_context(browser_world, email, password):
    """A second authenticated browser context for another user of the organization."""
    session = browser_world.session
    context = await session.browser.new_context(
        base_url=session.base_url, viewport=VIEWPORT, service_workers="block",
        locale="de-DE", timezone_id="Europe/Berlin")
    origin = urlsplit(session.base_url)

    async def local_only(route):
        # Same guard as the runner's own context: only the app origin may be called.
        url = urlsplit(route.request.url)
        if (url.scheme, url.netloc) == (origin.scheme, origin.netloc) or url.scheme in ("data", "blob"):
            await route.continue_()
        else:
            browser_world.errors.append(f"External request: {route.request.url}")
            await route.abort()

    await context.route("**/*", local_only)
    page = await context.new_page()
    page.set_default_timeout(10000)
    page.on("pageerror", lambda error: browser_world.errors.append(str(error)))
    await page.goto("/login")
    await page.get_by_label("E-Mail", exact=True).fill(email)
    await page.get_by_label("Passwort", exact=True).fill(password)
    await page.get_by_role("button", name="Anmelden", exact=True).click()
    await expect(page.get_by_role("link", name="Objekte", exact=True)).to_be_visible()
    return context, page


async def assert_fits_mobile(page):
    """The closed-drawer phone layout shows the main content without sideways scroll."""
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.wait_for_function(
        "() => { const aside = document.querySelector('aside'); return !aside || aside.getBoundingClientRect().right <= 1; }")
    # A page may nest a second <main>; every one of them must fit.
    for widths in await page.locator("main").evaluate_all(
            "nodes => nodes.map(node => ({visible: node.clientWidth, content: node.scrollWidth}))"):
        assert widths["content"] <= widths["visible"] + 1, widths


def lose_first_committed_reply(page, state, path, detail):
    """Route handler: the first committed POST to ``path`` answers 503 after saving, then removes itself."""
    state["detail"] = detail
    async def handler(route):
        request = route.request
        if request.method != "POST" or urlsplit(request.url).path != path:
            await route.continue_()
            return
        state["key"] = request.headers.get("idempotency-key")
        state["body"] = request.post_data_json
        response = await route.fetch()
        assert response.status in (200, 201), await response.text()
        state["result"] = await response.json()
        await route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": detail}))
        await page.unroute(f"**{path}", handler)
    return handler


async def expect_reply_replay(page, state, path, button_name, *, status=201, notice=None):
    """After the lost reply, the same button retries with the identical command."""
    await expect(notice or page.get_by_role("alert").filter(has_text=state["detail"])).to_be_visible()
    async with page.expect_response(
            lambda response: response.request.method == "POST" and urlsplit(response.url).path == path) as pending:
        await button(page, button_name).click()
    answer = await pending.value
    assert answer.status == status, await answer.text()
    retried = await answer.json()
    assert state["key"], "the command must carry an idempotency key"
    assert answer.request.headers.get("idempotency-key") == state["key"], "the retry must reuse the command key"
    assert answer.request.post_data_json == state["body"], "the retry must send the identical command"
    return retried


def wait_scope_usable(world, scope_id):
    """A fresh grant must answer prepare-capable before a scoped financial write."""
    for _ in range(50):
        answer = world.act("GET", f"/api/accounting-scopes/{scope_id}?capability=prepare")
        if answer.status_code == 200:
            return
        world.run(asyncio.sleep(0.1))
    raise AssertionError(f"scope {scope_id} did not become prepare-capable: {world.describe_response()}")


def signed_in_client(world, email, password):
    """An API client signed in as another user, closed by the caller."""
    from tests.conftest import _api_test_client
    client = _api_test_client()
    answer = world.send(client, "POST", "/api/auth/login", json={"email": email, "password": password})
    assert answer.status_code == 200, world.describe_response()
    return client


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
    row = page.get_by_role("row").filter(has=page.get_by_text(period, exact=True)).filter(
        has=page.get_by_role("cell", name=kind, exact=True))
    cells = row.get_by_role("cell")
    await expect(cells).to_have_count(5)
    await expect(cells.nth(0).get_by_text(period, exact=True)).to_be_visible()
    for index, value in enumerate([kind, *[euro(value) for value in expected]], start=1):
        await expect(cells.nth(index)).to_have_text(value)
