"""The zero-reserve draft journey through the real browser and API."""
import re
from pathlib import Path

from playwright.async_api import expect
from pytest_bdd import given, parsers, scenarios, then, when

from tests.settlement_period_support import period_data_cleanup  # noqa: F401
from tests.settlement_period_support import period_property

scenarios(str(Path(__file__).resolve().parents[3] / "features/economic_plan_reserve.feature"))


@given("a property with one owner responsible for the full WEG costs", target_fixture="plan_property")
def plan_property(world, period_data_cleanup):
    world.actor = world.administrator_of("Verwaltung Alpha")
    data = world.run(period_property(world.actor, month=1))
    return data["property"]["id"]


@when(parsers.parse(
    "the manager creates a zero-reserve economic plan and calculates 7200 euros in annual costs at {width:d} pixels"
))
def compute_draft(browser_world, plan_property, width):
    b = browser_world

    async def run():
        page = b.page
        await page.set_viewport_size({"width": width, "height": 1000})
        await b.visit("/wirtschaftsplaene")
        await page.get_by_role("button", name="+ Neuer Wirtschaftsplan", exact=True).click()
        await b.select("WEG-Objekt *", plan_property)
        await page.get_by_label("Wirtschaftsjahr *", exact=True).fill("2026")
        await expect(page.get_by_label("Zeitraum von *", exact=True)).to_have_value("2026-01-01")
        reserve = page.get_by_label("Rücklagenzuführung (Jahr)", exact=True)
        await expect(reserve).to_have_value("0.00")
        await page.get_by_role("button", name="Anlegen", exact=True).click()
        await expect(page.get_by_role("heading", name="Wirtschaftsplan 2026 Entwurf")).to_be_visible()
        await expect(page.get_by_role("row", name="Rücklagenzuführung 0.00 EUR", exact=True)).to_be_visible()
        await expect(reserve).to_have_value("0.00")
        await page.get_by_role("button", name="+ Position", exact=True).click()
        await b.select("Kategorie 1", "GARTENPFLEGE")
        await page.get_by_role("textbox", name=re.compile(" – Jahresbetrag$")).fill("7200.00")
        await page.get_by_label("Grundlage der Prüfung", exact=True).fill("Vollständige synthetische Jahresunterlagen geprüft")
        await page.get_by_role("checkbox", name=re.compile("^Alle angezeigten Beträge")).check()
        await page.get_by_role("button", name="Datengrundlage speichern", exact=True).click()
        await expect(page.get_by_text("Prüfung gespeichert.", exact=False)).to_be_visible()
        await page.get_by_role("button", name="Berechnen", exact=True).click()

    b.run(run())


@then("the plan shows a 600 euro monthly advance and zero reserve")
def verify_advance(browser_world):
    b = browser_world

    async def run():
        page = b.page
        await expect(page.get_by_role("cell", name="600.00 EUR", exact=True)).to_be_visible()
        await expect(page.get_by_role("row", name="Rücklagenzuführung 0.00 EUR", exact=True)).to_be_visible()
        await expect(page.get_by_label("Rücklagenzuführung (Jahr)", exact=True)).to_have_value("0.00")

    b.run(run())
