"""Real geometry and interactions for the two narrow-screen page regressions."""
import os
from datetime import date
from pathlib import Path

from playwright.async_api import expect
from pytest_bdd import given, parsers, scenarios, then, when

from tests.bdd.steps.browser_setup import create

scenarios(str(Path(__file__).resolve().parents[3] / "features/responsive_property_dashboard.feature"))


@given("a property with long unit rows and a long due task")
def long_rows(world, seed_data):
    world.actor = world.administrator_of("Verwaltung Alpha")
    world.actor_organization = "Verwaltung Alpha"
    home = create(world, "/api/properties", {
        "name": "Musterhaus", "management_type": "MIET", "street": "Sonnenweg 5",
        "zip_code": "12345", "city": "Teststadt",
        "data_audience": {"label": "Musterhaus", "reason": "Synthetic responsive check", "grants": [{
            "user_id": str(seed_data["user_a_admin_id"]),
            "capabilities": ["read", "prepare", "approve", "export"], "reason": "Synthetic responsive check",
        }]},
    }, keyed=True)
    create(world, "/api/properties", {
        "name": "Mietobjekt am langen Sonnenhof mit Vorderhaus und Seitenflügel 01234567",
        "management_type": "MIET",
        "data_audience": {"label": "Long property option", "reason": "Synthetic responsive check", "grants": [{
            "user_id": str(seed_data["user_a_admin_id"]),
            "capabilities": ["read", "prepare", "approve", "export"], "reason": "Synthetic responsive check",
        }]},
    }, keyed=True)
    for index in range(12):
        create(world, "/api/units", {
            "property_id": home["id"], "number": f"Gartenflügelwohnung{index + 1:02d}",
            "location": "Vorderhaus mit langem Durchgang zum Garten und Seitenflügel",
            "building_part": "Südlicher Gartenflügel", "usage_type": "WOHNUNG", "area": "1234.56",
        })
    task = create(world, "/api/tasks", {
        "title": "Dachflächenfensterinstandsetzungsbesprechung " * 3,
        "property_id": home["id"], "due_date": date.today().isoformat(),
    })
    world.data.update(responsive_property=home, responsive_task=task)


@when(parsers.parse("the manager opens the property and dashboard at {width:d} pixels"))
def open_pages(browser_world, width, tmp_path):
    b, d = browser_world, browser_world.world.data
    failures = []

    async def capture(name):
        filename = f"{name}-{'360' if width == 360 else 'desktop'}.png"
        await b.page.screenshot(path=str(tmp_path / filename), full_page=True)
        if os.environ.get("VERWALINO_CAPTURE_DESIGN") == "1":
            destination = Path(__file__).resolve().parents[3] / "docs/assets/design" / filename
            await b.page.screenshot(path=str(destination), full_page=True)

    async def check_width(name):
        geometry = await b.page.locator("main").evaluate("e => ({width: e.clientWidth, content: e.scrollWidth})")
        if geometry["content"] > geometry["width"] + 1:
            failures.append(f"{name} overflows: {geometry}")

    async def run():
        page = b.page
        await page.set_viewport_size({"width": width, "height": 900})
        await b.visit(f"/objekte/{d['responsive_property']['id']}")
        await expect(page.get_by_role("heading", name="Musterhaus", exact=True)).to_be_visible()
        await expect(page.get_by_role("link", name="Gartenflügelwohnung12", exact=True)).to_be_visible()
        await expect(page.get_by_text("Keine Dokumente vorhanden.", exact=True)).to_be_visible()
        await check_width("Property")
        table = page.get_by_role("table").filter(has=page.get_by_role("columnheader", name="Gebäudeteil", exact=True))
        await table.evaluate("e => e.scrollIntoView({block: 'start'})")
        await capture("detail-objekt")
        scroll = await table.evaluate("e => {const p = e.parentElement; p.scrollLeft = 200; return {left: p.scrollLeft, overflow: getComputedStyle(p).overflowX}}")
        if width == 360 and (scroll["left"] == 0 or scroll["overflow"] != "auto"):
            failures.append(f"Units do not scroll locally: {scroll}")
        upload = page.get_by_role("button", name="Dokument hochladen", exact=True)
        await upload.scroll_into_view_if_needed()
        await capture("detail-upload")
        if not await upload.evaluate("e => {const a=e.getBoundingClientRect(), b=e.closest('section').getBoundingClientRect(); return a.left >= b.left && a.right <= b.right}"):
            failures.append("Upload button extends beyond its card")
        await upload.click()
        await expect(page.get_by_role("alert").filter(has_text="Bitte eine nicht leere Dokumentdatei auswählen.")).to_be_visible()

        await b.visit("/")
        region = page.get_by_role("region", name="Fällige Aufgaben", exact=True)
        task_row = region.get_by_role("listitem").filter(has_text=d["responsive_task"]["title"])
        await expect(task_row).to_be_visible()
        await page.get_by_role("region", name="Mietkennzahlen", exact=True).scroll_into_view_if_needed()
        await capture("dashboard-kpis")
        await region.scroll_into_view_if_needed()
        await capture("dashboard")
        await check_width("Dashboard")
        for name in ("Aufgaben erneut laden", "Erledigen", "Vorherige Aufgaben", "Weitere Aufgaben"):
            buttons = region.get_by_role("button", name=name, exact=True)
            if not await buttons.first.evaluate("e => e.classList.contains('btn')"):
                failures.append(f"Task button lacks shared style: {name}")
        await task_row.get_by_role("button", name="Erledigen", exact=True).click()
        await expect(task_row).to_have_count(0)
        await region.get_by_role("button", name="Aufgaben erneut laden", exact=True).click()
        await expect(region.get_by_role("button", name="Aufgaben erneut laden", exact=True)).to_be_enabled()
        await expect(task_row).to_have_count(0)
    b.run(run())
    d["responsive_failures"] = failures


@then("the page stays within its width and its local controls remain usable")
def contained_pages(world):
    assert not world.data["responsive_failures"], "\n".join(world.data["responsive_failures"])
