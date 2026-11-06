"""Journey regression for duplicate numbers when setting up a property."""

from pathlib import Path

from playwright.async_api import expect
from pytest_bdd import given, parsers, scenarios, then, when

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
