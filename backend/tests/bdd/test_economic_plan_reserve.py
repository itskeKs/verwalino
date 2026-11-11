"""The zero-reserve draft journey through the real browser and API."""
import re
from pathlib import Path

from playwright.async_api import Page, expect
from pytest_bdd import given, parsers, scenarios, then, when

from tests.settlement_period_support import period_data_cleanup as period_data_cleanup
from tests.settlement_period_support import period_property

scenarios(str(Path(__file__).resolve().parents[3] / "features/journeys/economic_plan_reserve.feature"))


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
        await expect(page.get_by_role("row", name="Rücklagenzuführung 0,00 €", exact=True)).to_be_visible()
        await expect(reserve).to_have_value("0.00")
        await page.get_by_role("button", name="+ Position", exact=True).click()
        await b.select("Kategorie 1", "GARTENPFLEGE")
        await page.get_by_role("textbox", name=re.compile(" – Jahresbetrag$")).fill("7200.00")
        await confirm_basis(page)
        await page.get_by_role("button", name="Berechnen", exact=True).click()

    b.run(run())


@then("the plan shows a 600 euro monthly advance and zero reserve")
def verify_advance(browser_world):
    b = browser_world

    async def run():
        page = b.page
        await expect(page.get_by_role("cell", name="600,00 €", exact=True)).to_be_visible()
        await expect(page.get_by_role("row", name="Rücklagenzuführung 0,00 €", exact=True)).to_be_visible()
        await expect(page.get_by_label("Rücklagenzuführung (Jahr)", exact=True)).to_have_value("0.00")

    b.run(run())


async def confirm_basis(page: Page):
    await page.get_by_label("Grundlage der Prüfung", exact=True).fill("Vollständige synthetische Jahresunterlagen geprüft")
    await page.get_by_role("checkbox", name=re.compile("^Alle angezeigten Beträge")).check()
    await page.get_by_role("button", name="Datengrundlage speichern", exact=True).click()
    await expect(page.get_by_text("Prüfung gespeichert.", exact=False)).to_be_visible()


@when("the manager cancels finalization and reopens the draft from the list")
def reopen_plan(browser_world):
    async def run():
        page = browser_world.page
        path = "/wirtschaftsplaene/" + page.url.rsplit("/", 1)[-1]
        page.once("dialog", lambda dialog: dialog.dismiss())
        await page.get_by_role("button", name="Finalisieren", exact=True).click()
        await expect(page.get_by_role("heading", name="Wirtschaftsplan 2026 Entwurf")).to_be_visible()
        await page.get_by_role("link", name=re.compile("Alle Wirtschaftspläne")).click()
        await page.get_by_role("link", name="Details", exact=True).and_(page.locator(f'a[href="{path}"]')).click()

    browser_world.run(run())


async def expect_reopened_inputs(page: Page):
    await expect(page.get_by_role("cell", name="600,00 €", exact=True)).to_be_visible()
    await expect(page.get_by_role("textbox", name=re.compile(" – Jahresbetrag$"))).to_have_value(re.compile(r"^7200(?:\.00)?$"))
    await expect(page.get_by_label("Rücklagenzuführung (Jahr)", exact=True)).to_have_value("0.00")


@then("the editable annual cost is still 7200 euros and the monthly advance is still 600 euros")
def verify_reopened_inputs(browser_world):
    browser_world.run(expect_reopened_inputs(browser_world.page))


@when("the manager confirms the data basis again and finalizes the plan")
def finalize_reopened_plan(browser_world):
    async def run():
        page = browser_world.page
        await confirm_basis(page)
        page.once("dialog", lambda dialog: dialog.accept())
        await page.get_by_role("button", name="Finalisieren", exact=True).click()
        await expect(page.get_by_role("heading", name="Wirtschaftsplan 2026 Finalisiert")).to_be_visible()

    browser_world.run(run())


@then("the saved plan shows a 7200 euro annual advance and a 600 euro monthly advance")
def verify_saved_plan(browser_world):
    async def run():
        page = browser_world.page
        await page.reload()
        await expect(page.get_by_role("heading", name="Wirtschaftsplan 2026 Finalisiert")).to_be_visible()
        advance = page.get_by_role("row").filter(has=page.get_by_role("cell", name="600,00 €", exact=True))
        await expect(advance.get_by_role("cell", name="7.200,00 €", exact=True)).to_be_visible()

    browser_world.run(run())
