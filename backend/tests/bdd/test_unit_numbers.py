"""Journey regression for duplicate numbers when setting up a property."""

from pathlib import Path

from playwright.async_api import expect
from pytest_bdd import given, parsers, scenarios, then, when

from tests.bdd.steps.browser_common import browser_step, button, field
from tests.bdd.steps.browser_setup import create

scenarios(str(Path(__file__).resolve().parents[3] / "features/journeys/unit_numbers.feature"))


@given('a rental property with an existing "Wohnung 1"')
def numbered_property(world, seed_data):
    world.actor = world.administrator_of("Verwaltung Alpha")
    home = create(world, "/api/properties", {
        "name": "Musterhaus", "management_type": "MIET",
        "data_audience": {"label": "Musterhaus", "reason": "Synthetic unit number check", "grants": [{
            "user_id": str(seed_data["user_a_admin_id"]), "capabilities": ["read", "prepare"],
            "reason": "Synthetic unit number check",
        }]},
    }, keyed=True)
    create(world, "/api/units", {
        "property_id": home["id"], "number": "Wohnung 1", "usage_type": "WOHNUNG",
    })
    world.data["numbered_property_id"] = home["id"]


@when(parsers.parse('the manager tries to add another "Wohnung 1" at {width:d} px'))
def submit_duplicate_number(browser_world, width):
    b = browser_world

    async def submit():
        await b.page.set_viewport_size({"width": width, "height": 900})
        await b.visit(f"/objekte/{b.world.data['numbered_property_id']}")
        await expect(b.page.get_by_role("link", name="Wohnung 1", exact=True)).to_be_visible()
        await b.page.get_by_role("button", name="+ Neue Einheit", exact=True).click()
        await b.page.get_by_role("textbox", name="Nummer *", exact=True).fill("Wohnung 1")
        await b.page.get_by_role("button", name="Speichern", exact=True).click()

    b.run(submit())


@then("the unit form explains the conflict and keeps the number for correction")
def duplicate_number_refusal(browser_world):
    b = browser_world

    async def check():
        message = "Eine Einheit mit dieser Nummer gibt es in diesem Objekt schon."
        field = b.page.get_by_role("textbox", name="Nummer *", exact=True)
        await expect(field).to_have_accessible_description(message)
        await expect(field).to_have_value("Wohnung 1")
        await expect(b.page.get_by_role("link", name="Wohnung 1", exact=True)).to_have_count(1)

    b.run(check())


@when(parsers.parse("the manager tries to save a new unit with invalid area at {width:d} px"))
@browser_step
async def submit_invalid_area(browser_world, width):
    b = browser_world
    await b.page.set_viewport_size({"width": width, "height": 900})
    await b.visit(f"/objekte/{b.world.data['numbered_property_id']}")
    await button(b.page, "+ Neue Einheit").click()
    await field(b.page, "Nummer *").fill("Wohnung 2")
    await field(b.page, "Lage").fill("Vorderhaus")
    await field(b.page, "Flaeche (m2)").fill("abc")
    async with b.page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/api/units")) as response:
        await button(b.page, "Speichern").click()
    b.world.data["area_status"] = (await response.value).status
    await expect(button(b.page, "Speichern")).to_be_enabled()


@then("the area field explains the refusal and the form retains the input")
@browser_step
async def area_refusal(browser_world, tmp_path):
    b = browser_world
    try:
        assert b.world.data["area_status"] == 422
        await expect(field(b.page, "Flaeche (m2)")).to_have_accessible_description(
            "Fläche: Bitte eine Zahl wie 75,50 mit höchstens zwei Nachkommastellen eingeben."
        )
        await expect(field(b.page, "Flaeche (m2)")).to_have_value("abc")
        await expect(field(b.page, "Nummer *")).to_have_value("Wohnung 2")
        await expect(field(b.page, "Lage")).to_have_value("Vorderhaus")
        await expect(b.page.get_by_role("link", name="Wohnung 2", exact=True)).to_have_count(0)
    finally:
        # Keep the error and entered values in the same capture, including the baseline 500.
        await b.page.get_by_role("heading", name="Neue Einheit anlegen", exact=True).wait_for()
        await b.page.screenshot(path=str(tmp_path / f"unit-area-{b.page.viewport_size['width']}.png"), full_page=True)


@when(parsers.parse('the manager corrects the area to "{area}" and saves'))
@browser_step
async def correct_area(browser_world, area):
    await field(browser_world.page, "Flaeche (m2)").fill(area)
    await button(browser_world.page, "Speichern").click()


@when(parsers.parse('the manager edits the area to "{area}" and saves'))
@browser_step
async def edit_area(browser_world, area):
    page = browser_world.page
    row = page.get_by_role("row").filter(has=page.get_by_role("link", name="Wohnung 2", exact=True))
    await button(row, "Bearbeiten").click()
    await field(page, "Flaeche (m2)").fill(area)
    await button(page, "Speichern").click()


@then(parsers.parse("the saved unit shows {area} square metres"))
@browser_step
async def area_saved(browser_world, area):
    page = browser_world.page
    row = page.get_by_role("row").filter(has=page.get_by_role("link", name="Wohnung 2", exact=True))
    await expect(row.get_by_role("cell", name=area, exact=True)).to_be_visible()
    await page.reload()
    await expect(row.get_by_role("cell", name=area, exact=True)).to_be_visible()
