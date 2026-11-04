"""Automated twin of the Sparkasse import checks in acceptance journey 2."""
from pathlib import Path
import os

from playwright.async_api import expect
from pytest_bdd import given, parsers, scenarios, then, when

from tests.bdd.steps.browser_bank import anna_april_receipt, open_transactions
from tests.bdd.steps.browser_common import button, field

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "journeys" / "sparkasse_statement.feature"))


@given("Anna's dwelling has a long descriptive name")
def long_dwelling(world):
    response = world.act("PUT", f"/api/units/{world.data['units'][0]['id']}", json={
        "number": "Wohnung 1 mit Wintergarten am langen Sonnenhof",
    })
    assert response.status_code == 200, world.describe_response()


@when(parsers.parse("the manager opens Anna's April split at {width:d} px"))
def open_split(browser_world, width):
    async def run():
        await browser_world.page.set_viewport_size({"width": width, "height": 1000})
        await open_transactions(browser_world)
        await button(anna_april_receipt(browser_world), "Aufteilen").click()
        await expect(field(browser_world.page.get_by_role("dialog"), "Mietvertrag").first.locator("option:checked")).to_contain_text("Wintergarten")
    browser_world.run(run())


@then("the split explanation and labelled fields fit the dialog")
def split_layout(browser_world, tmp_path):
    async def run():
        page = browser_world.page
        dialog = page.get_by_role("dialog", name="Umsatz aufteilen", exact=True)
        width = page.viewport_size["width"]
        directory = Path(__file__).resolve().parents[3] / "docs/assets/design/split-dialog" if os.getenv("VERWALINO_CAPTURE_DESIGN") == "1" else tmp_path
        directory.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(directory / f"after-{width}.png"), animations="disabled")
        bounds = await dialog.bounding_box()
        assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
        assert await dialog.evaluate("e => e.scrollWidth <= e.clientWidth"), "split content overflows horizontally"
        description = page.locator(f'#{await dialog.get_attribute("aria-describedby")}')
        await expect(description).to_have_text(
            "Anna Beispiel · 800,00 € · Eingang (IN). Splitbeträge immer positiv, z. B. 700,00. Die Buchungsberechtigung wird serverseitig geprüft."
        )
        assert await description.evaluate("e => e.scrollWidth <= e.clientWidth && e.scrollHeight <= e.clientHeight")
        for row in await dialog.get_by_role("group", name="Splitzeile").all():
            for control in await row.locator("input, select").all():
                assert await control.evaluate("e => getComputedStyle(e.closest('label')).flexDirection === 'column'"), "label does not sit above its field"
                assert await control.evaluate("e => { const r = e.getBoundingClientRect(), d = e.closest('[role=dialog]').getBoundingClientRect(); return r.left >= d.left && r.right <= d.right; }")
            for label in ("Mietvertrag", "Notiz"):
                assert await field(row, label).evaluate("e => Math.abs(e.getBoundingClientRect().width - e.closest('fieldset').clientWidth + parseFloat(getComputedStyle(e.closest('fieldset')).paddingLeft) + parseFloat(getComputedStyle(e.closest('fieldset')).paddingRight)) < 3"), f"{label} does not span the row"
    browser_world.run(run())


@when("the manager corrects the split to 650 EUR rent and 150 EUR advance with a note")
def correct_split(browser_world, tmp_path):
    async def run():
        page = browser_world.page
        dialog = page.get_by_role("dialog", name="Umsatz aufteilen", exact=True)
        await field(dialog, "Notiz").first.fill("April geprüft: Teilzahlung")
        await field(dialog, "Betrag (EUR)").first.fill("650,01")
        await field(dialog, "Betrag (EUR)").nth(1).fill("150,00")
        await button(dialog, "Zuordnung buchen").click()
        await expect(dialog.get_by_role("alert")).to_have_text("Die Summe der positiven Splitbeträge muss den Umsatz vollständig abdecken.")
        await expect(field(dialog, "Notiz").first).to_have_value("April geprüft: Teilzahlung")
        await expect(field(dialog, "Betrag (EUR)").first).to_have_value("650,01")
        await field(dialog, "Betrag (EUR)").first.fill("650,00")
        submit = button(dialog, "Zuordnung buchen")
        cancel = button(dialog, "Abbrechen")
        await submit.scroll_into_view_if_needed()
        await expect(submit).to_be_in_viewport()
        await expect(cancel).to_be_in_viewport()
        a, b = await submit.bounding_box(), await cancel.bounding_box()
        assert abs(a["y"] - b["y"]) < 2 and b["x"] >= a["x"] + a["width"], "actions are not in one bottom row"
        directory = Path(__file__).resolve().parents[3] / "docs/assets/design/split-dialog" if os.getenv("VERWALINO_CAPTURE_DESIGN") == "1" else tmp_path
        await page.screenshot(path=str(directory / f"after-actions-{page.viewport_size['width']}.png"), animations="disabled")
        await submit.click()
        await expect(dialog).not_to_be_visible()
    browser_world.run(run())


@then("Anna's April receipt remains assigned after reload")
def split_persisted(browser_world):
    async def run():
        await browser_world.page.reload()
        await field(browser_world.page, "Status").select_option("ZUGEORDNET")
        await expect(anna_april_receipt(browser_world)).to_contain_text("Zugeordnet")
    browser_world.run(run())
