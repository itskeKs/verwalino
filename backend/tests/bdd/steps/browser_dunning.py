"""Individual, scoped and selected dunning journeys, with API-only prerequisites."""
import io
import re
from urllib.parse import urlsplit

import pdfplumber
from playwright.async_api import expect
from pytest_bdd import given, then, when

from tests.bdd.steps.browser_common import browser_step, button, field, submit
from tests.bdd.steps.browser_setup import bank_account, create, create_scope, rental


@given("an unpaid rental agreement for 700 EUR rent and no advance payment")
def unpaid_rent(world, seed_data):
    rental(world, seed_data, advance="0.00")


@when('the manager creates a January notice as of 2 March 2026 with a base rate of "2,50"')
@browser_step
async def interest_notice(browser_world):
    page = browser_world.page
    data = browser_world.world.data
    await browser_world.visit("/mahnungen")
    await button(page, "+ Neue Mahnung").click()
    await field(page, "Empfaengertyp *").select_option("tenant")
    await browser_world.select("Mieter *", data["tenants"][0]["id"])
    await browser_world.select("Mietvertrag *", data["leases"][0]["id"])
    for label, value in (("Zeitraum von *", "2026-01-01"), ("Zeitraum bis *", "2026-01-31"), ("Stichtag", "2026-03-02"), ("Karenzzeit (Tage)", "0"), ("Mahngebuehr (Override)", "0"), ("Basiszinssatz (%)", "2,50")):
        await field(page, label).fill(value)
    await browser_world.scope()
    await submit(browser_world, "Mahnung erstellen", "/api/dunning/tenant")
    await expect(button(page, "Mahnung ausgeben")).to_be_visible()
    data["notice_url"] = page.url


async def interest_amounts(browser):
    await browser.row("Verzugszinsen", "8.63 EUR")
    await browser.row("Gesamtsumme", "708.63 EUR")
    await expect(browser.page.get_by_role("heading", name="Eingefrorene Zinsberechnung", exact=True)).to_be_visible()
    await expect(browser.page.get_by_text("Stichtag:", exact=False).filter(has_text="2026-03-02")).to_be_visible()


@then("the reloaded notice shows 8.63 EUR interest and 708.63 EUR in total")
@browser_step
async def draft_interest(browser_world):
    await browser_world.page.reload()
    await interest_amounts(browser_world)


@when('the manager confirms "Mahnung ausgeben"')
@browser_step
async def issue_notice(browser_world):
    page = browser_world.page
    page.once("dialog", lambda dialog: dialog.accept())
    await button(page, "Mahnung ausgeben").click()
    await expect(page.get_by_text("Ausgestellt", exact=True).first).to_be_visible()


@then("the issued notice still shows the reviewed amounts after reload")
@browser_step
async def issued_interest(browser_world):
    await browser_world.page.reload()
    await expect(browser_world.page.get_by_text("Ausgestellt", exact=True).first).to_be_visible()
    await interest_amounts(browser_world)


@then('"PDF herunterladen" opens a PDF with the tenant and the reviewed interest')
@browser_step
async def interest_pdf(browser_world):
    page = browser_world.page
    async with page.expect_popup() as opened:
        await button(page, "PDF herunterladen").click()
    popup = await opened.value
    try:
        await popup.wait_for_load_state()
        assert urlsplit(popup.url).path.endswith("/pdf"), popup.url
        assert await popup.evaluate("document.contentType") == "application/pdf"
        # Read the exact document opened by the UI, using the same authentication.
        response = await browser_world.context.request.get(popup.url)
        assert response.status == 200
        assert response.headers["content-type"].startswith("application/pdf")
        content = await response.body()
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            text = " ".join(part.extract_text() or "" for part in pdf.pages)
        assert browser_world.world.data["people"][0].name in text
        for value in (r"700[.,]00", r"2[.,]50\s*%", r"7[.,]50\s*%", r"\b60\b", r"8[.,]63", r"708[.,]63"):
            assert re.search(value, text), f"PDF missing {value}: {text}"
    finally:
        await popup.close()


@given("a rental agreement with receipts in two separate accounting scopes")
def scoped_rent(world, seed_data):
    rental(world, seed_data)
    first = world.data["scope"]
    second = create_scope(world, world.data["user_id"], "Zweiter Mietkreis")
    for scope, amount in ((first, "100.00"), (second, "900.00")):
        world.data["scope"] = scope
        account = bank_account(world)
        payment = create(world, "/api/payments", {
            "property_id": world.data["property"]["id"], "accounting_scope_id": scope["id"],
            "bank_account_id": account["id"], "tenant_profile_id": world.data["tenants"][0]["id"],
            "lease_agreement_id": world.data["leases"][0]["id"], "amount": amount,
            "direction": "IN", "value_date": "2026-06-01", "payment_type": "KALTMIETE", "period_year": 2026, "period_month": 6,
        })
        response = world.act("POST", f"/api/payments/{payment['id']}/book")
        assert response.status_code == 200, world.describe_response()
    world.data["scope"] = first


@when("the manager opens the notice form from the first scope's open items")
@browser_step
async def shortcut(browser_world):
    page = browser_world.page
    await browser_world.visit("/offene-posten")
    await field(page, "Zeitraum von *").fill("2026-01-01")
    await field(page, "Zeitraum bis *").fill("2026-12-31")
    await browser_world.scope()
    await button(page, "Offene Posten laden").click()
    await expect(page.get_by_text("10100.00 EUR offen", exact=True)).to_be_visible()
    await page.get_by_role("link", name="Mahnung erstellen", exact=True).click()
    await expect(page.get_by_role("heading", name="Neue Mahnung anlegen", exact=True)).to_be_visible()


@when("the manager corrects a reporting period outside that scope")
@browser_step
async def correct_period(browser_world):
    page = browser_world.page
    await field(page, "Zeitraum von *").fill("2025-01-01")
    await field(page, "Stichtag").fill("2027-01-15")
    await field(page, "Basiszinssatz (%)").fill("0")
    await button(page, "Mahnung erstellen").click()
    await expect(page.get_by_text("Report period is outside accounting scope", exact=True)).to_be_visible()
    await field(page, "Zeitraum von *").fill("2026-01-01")


@then("the notice form keeps the first scope selected")
@browser_step
async def scope_retained(browser_world):
    await expect(field(browser_world.page, "Abrechnungskreis")).to_have_value(browser_world.world.data["scope"]["id"])


@when("the manager creates the scoped notice")
@browser_step
async def create_scoped_notice(browser_world):
    await field(browser_world.page, "Mahngebuehr (Override)").fill("0")
    await submit(browser_world, "Mahnung erstellen", "/api/dunning/tenant")
    await expect(button(browser_world.page, "Entwurf verwerfen")).to_be_visible()
    browser_world.world.data["notice_url"] = browser_world.page.url


@then("the notice shows only the first scope's outstanding rent")
@browser_step
async def scoped_amount(browser_world):
    await browser_world.row("Hauptforderung (Rueckstand)", "10100.00 EUR")


@when('the manager confirms "Entwurf verwerfen"')
@browser_step
async def discard(browser_world):
    browser_world.page.once("dialog", lambda dialog: dialog.accept())
    await button(browser_world.page, "Entwurf verwerfen").click()
    await browser_world.page.wait_for_url("**/mahnungen")


@then('the discarded notice remains visible as "Verworfen" after reload')
@browser_step
async def discarded_history(browser_world):
    page = browser_world.page
    await page.reload()
    row = page.get_by_role("row").filter(has=page.locator(f'a[href="{urlsplit(browser_world.world.data["notice_url"]).path}"]'))
    await expect(row.get_by_text("Verworfen", exact=True)).to_be_visible()


@given("three unpaid rental agreements in one accounting scope")
def batch_rent(world, seed_data):
    rental(world, seed_data, count=3)


async def preview_batch(browser):
    page = browser.page
    await browser.visit("/offene-posten")
    await field(page, "Zeitraum von *").fill("2026-10-01")
    await field(page, "Zeitraum bis *").fill("2026-10-31")
    await button(page, "Mahnlauf öffnen").click()
    batch = page.get_by_role("region", name="Mahnlauf", exact=True)
    await browser.select("Immobilie für Abrechnungskreis", browser.world.data["property"]["id"], batch)
    await browser.scope(batch)
    for label, value in (("Stichtag *", "2026-11-15"), ("Mindestrückstand (EUR) *", "100.00"), ("Karenztage", "0")):
        await field(batch, label).fill(value)
    await button(batch, "Mahnlauf prüfen").click()
    await expect(batch.get_by_text("3 geeignete Rückstände oberhalb des Mindestbetrags.", exact=True)).to_be_visible()
    return batch


@when("the manager previews October arrears above 100 EUR as of 15 November 2026")
@browser_step
async def preview_arrears(browser_world):
    await preview_batch(browser_world)


@then("the preview offers three unselected arrears of 850 EUR each")
@browser_step
async def preview_candidates(browser_world):
    batch = browser_world.page.get_by_role("region", name="Mahnlauf", exact=True)
    await expect(batch.get_by_role("checkbox")).to_have_count(3)
    for index, person in enumerate(browser_world.world.data["people"]):
        row = batch.get_by_role("row").filter(has_text=person.name)
        await expect(row.get_by_role("checkbox")).not_to_be_checked()
        await expect(row.get_by_role("cell").nth(2)).to_have_text("850.00 EUR")
    await expect(button(batch, "Auswahl bestätigen")).to_be_disabled()


@when("the manager changes the minimum arrears")
@browser_step
async def edit_threshold(browser_world):
    batch = browser_world.page.get_by_role("region", name="Mahnlauf", exact=True)
    await batch.get_by_role("checkbox").first.check()
    await field(batch, "Mindestrückstand (EUR) *").fill("101.00")


@then("the reviewed selection is cleared")
@browser_step
async def selection_cleared(browser_world):
    batch = browser_world.page.get_by_role("region", name="Mahnlauf", exact=True)
    await expect(batch.get_by_role("checkbox")).to_have_count(0)
    await expect(button(batch, "Auswahl bestätigen")).to_have_count(0)


async def confirm_batch(browser):
    batch = browser.page.get_by_role("region", name="Mahnlauf", exact=True)
    await button(batch, "Alle geeigneten Rückstände auswählen").click()
    await button(batch, "Auswahl bestätigen").click()
    dialog = browser.page.get_by_role("dialog", name="Mahnentwürfe anlegen", exact=True)
    await expect(dialog).to_contain_text("3 Mahnentwürfe: Hauptforderung 2550.00 EUR; Gesamtsumme 2550.00 EUR.")
    return dialog


@when("the manager previews and cancels the selection on a phone")
@browser_step
async def cancel_phone(browser_world):
    await browser_world.page.set_viewport_size({"width": 390, "height": 844})
    await preview_batch(browser_world)
    dialog = await confirm_batch(browser_world)
    await button(dialog, "Abbrechen").click()
    await expect(dialog).not_to_be_visible()
    await button(browser_world.page, "Mahnlauf schließen").click()
    await browser_world.page.reload()
    await expect(button(browser_world.page, "Mahnlauf prüfen")).to_have_count(0)


@then("no notice appears in the reloaded history")
@browser_step
async def no_notices(browser_world):
    await browser_world.visit("/mahnungen")
    await browser_world.page.reload()
    settled = browser_world.page.get_by_text("Keine Mahnungen vorhanden.", exact=True).or_(
        browser_world.page.get_by_role("columnheader", name="Empfaenger", exact=True)
    )
    await expect(settled).to_be_visible()
    await expect(browser_world.page.get_by_role("alert")).to_have_count(0)
    # Other scenarios have their own scopes and names; inspect this journey's people.
    for person in browser_world.world.data["people"]:
        await expect(browser_world.page.get_by_role("cell", name=person.name, exact=True)).to_have_count(0)


@when('the manager selects the three arrears and confirms "Mahnentwürfe anlegen"')
@browser_step
async def create_batch(browser_world):
    dialog = await confirm_batch(browser_world)
    await button(dialog, "Mahnentwürfe anlegen").click()
    await expect(browser_world.page.get_by_role("status")).to_contain_text("3 Mahnentwürfe angelegt.")
    links = browser_world.page.get_by_role("link", name=re.compile(r"^Entwurf .* öffnen$"))
    await expect(links).to_have_count(3)
    browser_world.world.data["draft_paths"] = [await link.get_attribute("href") for link in await links.all()]


@then("three unissued drafts remain in the history after reload")
@browser_step
async def batch_history(browser_world):
    page = browser_world.page
    for path in browser_world.world.data["draft_paths"]:
        await browser_world.visit("/mahnungen")
        await page.reload()
        row = page.get_by_role("row").filter(has=page.locator(f'a[href="{path}"]'))
        await expect(row.get_by_text("Entwurf", exact=True)).to_be_visible()
        await row.get_by_role("link", name="Details", exact=True).click()
        await browser_world.row("Status", "Entwurf")
        await browser_world.row("Hauptforderung (Rueckstand)", "850.00 EUR")
