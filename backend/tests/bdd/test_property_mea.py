"""Journey regression for MEA guidance when creating a property."""
import os
from pathlib import Path

from playwright.async_api import expect
from pytest_bdd import given, parsers, scenarios, then, when

from tests.bdd.steps.browser_setup import create

scenarios(str(Path(__file__).resolve().parents[3] / "features/journeys/property_mea.feature"))


@given(parsers.parse("a new {management_type} property without co-ownership shares"))
def new_property(world, seed_data, management_type):
    home = create(world, "/api/properties", {
        "name": f"Musterhaus {management_type}", "management_type": management_type,
        "street": "Sonnenweg 5", "zip_code": "12345", "city": "Teststadt",
        "data_audience": {"label": "Musterhaus", "reason": "Synthetic MEA check", "grants": [{
            "user_id": str(seed_data["user_a_admin_id"]),
            "capabilities": ["read", "prepare", "approve", "export"], "reason": "Synthetic MEA check",
        }]},
    }, keyed=True)
    world.data["mea_property"] = home


@when("the manager opens its details and new unit form on desktop and phone")
def property_views(browser_world, tmp_path):
    b = browser_world
    home = b.world.data["mea_property"]

    async def visit():
        views = []
        for width in (1280, 360):
            await b.page.set_viewport_size({"width": width, "height": 900})
            await b.visit(f"/objekte/{home['id']}")
            await expect(b.page.get_by_role("heading", name=home["name"], exact=True)).to_be_visible()
            await b.page.get_by_role("button", name="+ Neue Einheit", exact=True).click()
            await expect(b.page.get_by_label("Nummer *", exact=True)).to_be_visible()
            await expect(b.page.get_by_text("Keine Dokumente vorhanden.", exact=True)).to_be_visible()
            await b.page.locator("main").evaluate("element => { element.scrollTop = 0; }")
            for name, surface in (
                (f"mea-{home['management_type'].lower()}", b.page),
                (f"mea-{home['management_type'].lower()}-unit", b.page.get_by_role(
                    "heading", name="Neue Einheit anlegen", exact=True).locator("..")),
            ):
                filename = f"{name}-{width}.png"
                await surface.screenshot(path=str(tmp_path / filename))
                if os.environ.get("VERWALINO_CAPTURE_DESIGN") == "1":
                    destination = Path(__file__).resolve().parents[3] / "docs/assets/design" / filename
                    await surface.screenshot(path=str(destination))
            views.append({
                "prompt": await b.page.get_by_role("heading", name="MEA-Skala bestätigen", exact=True).count(),
                "field": await b.page.get_by_label("MEA (Miteigentumsanteil)", exact=True).count(),
                "german_status": await b.page.get_by_text("Skala noch nicht bestätigt", exact=True).count(),
                "raw_status": await b.page.get_by_text("LEGACY_UNCONFIRMED", exact=True).count(),
                "fits": await b.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"),
            })
        return views

    b.world.data["mea_views"] = b.run(visit())


@then("the MEA controls match the property's management type")
def relevant_controls(world):
    expected = int(world.data["mea_property"]["management_type"] == "WEG")
    for view in world.data["mea_views"]:
        assert view == {"prompt": expected, "field": expected, "german_status": expected,
                        "raw_status": 0, "fits": True}
