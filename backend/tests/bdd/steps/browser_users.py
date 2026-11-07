"""Responsive, labelled user creation through the existing browser harness."""
from urllib.parse import urlsplit

from playwright.async_api import expect
from pytest_bdd import then, when

EMAIL = "team-audit@example.invalid"
NAME = "Katharina Muster-Beispielhausen"
PASSWORD = "SyntheticPassword123!"


def creation_form(page):
    return page.locator("form").filter(has=page.get_by_role("heading", name="Neuen Benutzer anlegen", exact=True))


async def fill_form(form):
    await form.locator('input[type="email"]').fill(EMAIL)
    await form.locator('input[type="text"]').fill(NAME)
    await form.locator('input[type="password"]').fill(PASSWORD)
    await form.locator('input[type="email"]').focus()
    for control in ('input[type="text"]', 'input[type="password"]', "select"):
        await form.page.keyboard.press("Tab")
        await expect(form.locator(control)).to_be_focused()
    await form.locator("select").press("l")
    await expect(form.locator("select")).to_have_value("READ_ONLY")


@when("the administrator fills the new-user form on desktop and phone")
def fill_responsive_form(browser_world, tmp_path):
    async def run():
        page = browser_world.page
        await browser_world.visit("/users")
        form = creation_form(page)
        await fill_form(form)
        failures = []
        for width in (1280, 360):
            await page.set_viewport_size({"width": width, "height": 900})
            if width == 360:
                await page.wait_for_function("() => { const aside = document.querySelector('aside'); return !aside || aside.getBoundingClientRect().right <= 0; }")
            await form.locator("select").focus()
            for selector, value in (("input[type=email]", EMAIL), ("input[type=text]", NAME),
                                    ("input[type=password]", PASSWORD), ("select", "READ_ONLY")):
                await expect(form.locator(selector)).to_have_value(value)
            await expect(form.get_by_role("button", name="Benutzer anlegen", exact=True)).to_be_enabled()
            await form.scroll_into_view_if_needed()
            await page.screenshot(path=str(tmp_path / f"user-creation-{width}.png"))
            geometry = await page.locator("main").evaluate("e => ({width: e.clientWidth, content: e.scrollWidth})")
            if geometry["content"] != geometry["width"]:
                failures.append(f"{width}px main overflows: {geometry}")
            for selector, label in (("input[type=email]", "E-Mail *"), ("input[type=text]", "Name"),
                                    ("input[type=password]", "Startpasswort *"), ("select", "Rolle")):
                control = form.locator(selector)
                named_control = (form.get_by_role("combobox", name=label, exact=True) if selector == "select"
                                 else form.get_by_label(label, exact=True))
                if await named_control.count() != 1:
                    failures.append(f"{width}px missing accessible name: {label}")
                associated_label = form.locator("label").filter(has=page.locator(selector))
                if await associated_label.count() != 1 or not await associated_label.is_visible():
                    failures.append(f"{width}px associated label is not visible: {label}")
                contained = await control.evaluate("e => {const a=e.getBoundingClientRect(), b=e.closest('form').getBoundingClientRect(); return a.left >= b.left && a.right <= b.right}")
                if not contained:
                    failures.append(f"{width}px control leaves its card: {label}")
            if not await form.get_by_role("button", name="Benutzer anlegen", exact=True).evaluate("e => {const a=e.getBoundingClientRect(), b=e.closest('form').getBoundingClientRect(); return a.left >= b.left && a.right <= b.right}"):
                failures.append(f"{width}px submit button leaves its card")
        browser_world.world.data["user_creation_failures"] = failures
    browser_world.run(run())


@then("every new-user control has a visible label and fits its card without sideways page scrolling")
def labelled_contained_form(world):
    assert not world.data["user_creation_failures"], "\n".join(world.data["user_creation_failures"])


@when("the administrator creates the read-only user using the keyboard")
def keyboard_create(browser_world):
    async def run():
        page = browser_world.page
        form = creation_form(page)
        await page.keyboard.press("Tab")
        await expect(form.get_by_role("button", name="Benutzer anlegen", exact=True)).to_be_focused()
        async with page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == "/api/users") as pending:
            await page.keyboard.press("Enter")
        answer = await pending.value
        assert answer.status == 201, await answer.text()
    browser_world.run(run())


@then("the new user appears with the selected read-only role")
def created_row(browser_world):
    async def run():
        page = browser_world.page
        row = page.get_by_role("row").filter(has=page.get_by_role("cell", name=EMAIL, exact=True))
        await expect(row.get_by_role("cell", name=NAME, exact=True)).to_be_visible()
        await expect(row.get_by_role("cell", name="Lesender Benutzer", exact=True)).to_be_visible()
        await expect(creation_form(page).locator('input[type="password"]')).to_have_value("")
    browser_world.run(run())


@then("trying to create the same user again preserves all entered fields")
def duplicate_retains_inputs(browser_world):
    async def run():
        page = browser_world.page
        form = creation_form(page)
        await fill_form(form)
        await page.keyboard.press("Tab")
        async with page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == "/api/users") as pending:
            await page.keyboard.press("Enter")
        answer = await pending.value
        assert answer.status == 409, await answer.text()
        await expect(form.get_by_role("alert")).to_be_visible()
        for selector, value in (("input[type=email]", EMAIL), ("input[type=text]", NAME),
                                ("input[type=password]", PASSWORD), ("select", "READ_ONLY")):
            await expect(form.locator(selector)).to_have_value(value)
        await expect(form.get_by_role("button", name="Benutzer anlegen", exact=True)).to_be_enabled()
    browser_world.run(run())
