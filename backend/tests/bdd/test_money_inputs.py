"""Focused twins of the money-entry steps of core journeys 1 and 2."""
import os
from pathlib import Path

from playwright.async_api import expect
from pytest_bdd import given, parsers, scenarios, then, when

from tests.bdd.steps.browser_common import button, field
from tests.bdd.steps.browser_setup import rental

ROOT = Path(__file__).resolve().parents[3]
scenarios(str(ROOT / "features/journeys/money_inputs.feature"))


async def capture(page, name, width):
    if os.environ.get("VERWALINO_CAPTURE_DESIGN") == "1":
        if name.endswith("invalid"):
            await page.get_by_role("alert").scroll_into_view_if_needed()
        await page.screenshot(path=str(ROOT / f"docs/assets/design/money-inputs-{name}-{width}.png"), full_page=True)


@given("a rental property and tenant ready for a new tenancy")
def tenancy_ready(world, seed_data):
    rental(world, seed_data)
    response = world.act("DELETE", f"/api/leases/{world.data['leases'][0]['id']}")
    assert response.status_code == 204, world.describe_response()


@given("a rental property ready for a manual cost")
def cost_ready(world, seed_data):
    rental(world, seed_data)


@given("a commercial unit and tenant ready for a new tenancy")
def commercial_tenancy_ready(world, seed_data):
    tenancy_ready(world, seed_data)
    response = world.act("PUT", f"/api/units/{world.data['units'][0]['id']}", json={"usage_type": "GEWERBE"})
    assert response.status_code == 200, world.describe_response()


@when("I save a commercial tenancy with 750 EUR rent and 2400 EUR deposit")
def save_commercial_tenancy(browser_world):
    save_tenancy(browser_world, 1280, commercial=True)


@then("the reloaded commercial tenancy retains 2400 EUR deposit")
def commercial_tenancy_persisted(browser_world):
    b = browser_world

    async def check():
        await b.page.reload()
        await button(b.page, "Bearbeiten / beenden").click()
        await expect(field(b.page, "Kaltmiete (EUR) *")).to_have_value("750.00")
        await expect(field(b.page, "Kaution (EUR)")).to_have_value("2400.00")
    b.run(check())


@when(parsers.parse("I correct an excessive deposit and save a tenancy with German amounts at {width:d} px"))
def save_tenancy(browser_world, width, *, commercial=False):
    b = browser_world

    async def save():
        data = b.world.data
        await b.page.set_viewport_size({"width": width, "height": 1000})
        await b.visit(f"/objekte/{data['property']['id']}/einheiten/{data['units'][0]['id']}")
        await button(b.page, "+ Mietvertrag").click()
        await b.page.get_by_role("combobox", name="Mieter *", exact=True).select_option(data["tenants"][0]["id"])
        for label, value in (("Vertragsbeginn *", "2026-01-01"), ("Kaltmiete (EUR) *", "750,00"),
                             ("Betriebskosten-Vorauszahlung (EUR) *", "150,00"),
                             ("Heizkosten-Vorauszahlung (EUR) *", "90,00"), ("Kaution (EUR)", "2.400,00")):
            await field(b.page, label).fill(value)
        if not commercial:
            await button(b.page, "Speichern").click()
            await expect(b.page.get_by_role("alert")).to_have_text(
                "Die Kaution darf höchstens drei Nettokaltmieten betragen (2.250,00 €).")
            await expect(field(b.page, "Kaution (EUR)")).to_have_value("2.400,00")
            response = await b.context.request.get("/api/leases", params={"unit_id": data["units"][0]["id"]})
            assert response.status == 200, await response.text()
            assert await response.json() == []
            await field(b.page, "Kaution (EUR)").fill("2.250,00")
        await button(b.page, "Speichern").click()
        try:
            await expect(b.page.get_by_role("heading", name="Neuen Mietvertrag anlegen", exact=True)).not_to_be_visible(timeout=3000)
            await expect(button(b.page, "Bearbeiten / beenden")).to_be_visible()
        finally:
            await capture(b.page, "lease", width)
    b.run(save())


@then("the reloaded tenancy retains 750 EUR rent, 150 EUR operating advance, 90 EUR heating advance and 2250 EUR deposit")
def tenancy_persisted(browser_world):
    b = browser_world

    async def check():
        await b.page.reload()
        await button(b.page, "Bearbeiten / beenden").click()
        for label, value in (("Kaltmiete (EUR) *", "750.00"), ("Betriebskosten-Vorauszahlung (EUR) *", "150.00"),
                             ("Heizkosten-Vorauszahlung (EUR) *", "90.00"), ("Kaution (EUR)", "2250.00")):
            await expect(field(b.page, label)).to_have_value(value)
        await b.page.get_by_role("heading", name="Mietvertrag bearbeiten / beenden", exact=True).scroll_into_view_if_needed()
        await capture(b.page, "lease", b.page.viewport_size["width"])
    b.run(check())


@then("invalid tenancy money shows a German field problem without losing my input")
def tenancy_invalid(browser_world):
    b = browser_world

    async def check():
        await field(b.page, "Kaltmiete (EUR) *").fill("abc")
        await button(b.page, "Speichern").click()
        await expect(b.page.get_by_role("alert")).to_contain_text("gültigen EUR-Betrag")
        await expect(field(b.page, "Kaltmiete (EUR) *")).to_have_value("abc")
        await expect(field(b.page, "Kaltmiete (EUR) *")).to_have_attribute("aria-invalid", "true")
        await capture(b.page, "lease-invalid", b.page.viewport_size["width"])
    b.run(check())


@when(parsers.parse("I save a 60 EUR cost with a decimal comma at {width:d} px"))
def save_cost(browser_world, width):
    b = browser_world

    async def save():
        await b.page.set_viewport_size({"width": width, "height": 1000})
        await b.visit("/kosten")
        await button(b.page, "+ Neue Kostenposition").click()
        await b.page.get_by_role("combobox", name="Objekt *", exact=True).select_option(b.world.data["property"]["id"])
        await b.page.get_by_role("combobox", name="Kategorie *", exact=True).select_option(index=1)
        for label, value in (("Rechnungsdatum *", "2026-01-01"), ("Leistungszeitraum von *", "2026-01-01"),
                             ("Leistungszeitraum bis *", "2026-01-31"), ("Bruttobetrag *", "60,00"),
                             ("Belegnummer", "STROM-60")):
            await field(b.page, label).fill(value)
        await b.scope()
        await button(b.page, "Speichern").click()
        try:
            await expect(field(b.page, "Bruttobetrag *")).not_to_be_visible(timeout=3000)
            await expect(b.page.get_by_role("cell", name=b.world.data["property"]["name"], exact=True)).to_be_visible()
        finally:
            await capture(b.page, "cost", width)
    b.run(save())


@then("the reloaded cost retains 60 EUR")
def cost_persisted(browser_world):
    b = browser_world

    async def check():
        await b.page.reload()
        await b.select("Objektfilter", b.world.data["property"]["id"])
        await expect(b.page.get_by_label("Listensumme Brutto", exact=True)).to_have_text("60,00 EUR")
        row = b.page.get_by_role("row").filter(has=b.page.get_by_role("cell", name=b.world.data["property"]["name"], exact=True))
        await expect(row.get_by_role("cell", name="60.00", exact=True)).to_be_visible()
        await b.page.get_by_role("heading", name="Kostenpositionen", exact=True).scroll_into_view_if_needed()
        await capture(b.page, "cost", b.page.viewport_size["width"])
    b.run(check())


@then("invalid cost money shows a German field problem without losing my input")
def cost_invalid(browser_world):
    b = browser_world

    async def check():
        await button(b.page, "+ Neue Kostenposition").click()
        # Browser-native required fields must be valid before custom validation runs.
        await b.page.get_by_role("combobox", name="Objekt *", exact=True).select_option(b.world.data["property"]["id"])
        await b.page.get_by_role("combobox", name="Kategorie *", exact=True).select_option(index=1)
        for label in ("Rechnungsdatum *", "Leistungszeitraum von *", "Leistungszeitraum bis *"):
            await field(b.page, label).fill("2026-01-01")
        await field(b.page, "Bruttobetrag *").fill("abc")
        await button(b.page, "Speichern").click()
        await expect(b.page.get_by_role("alert")).to_contain_text("gültigen EUR-Betrag")
        await expect(field(b.page, "Bruttobetrag *")).to_have_value("abc")
        await expect(field(b.page, "Bruttobetrag *")).to_have_attribute("aria-invalid", "true")
        await capture(b.page, "cost-invalid", b.page.viewport_size["width"])
    b.run(check())
