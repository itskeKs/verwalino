"""Individual, scoped and selected dunning journeys, with API-only prerequisites."""
import io
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import pdfplumber
from playwright.async_api import expect
from pytest_bdd import given, then, when, parsers

from tests.bdd.steps.browser_common import browser_step, button, field, ledger, submit
from tests.bdd.steps.browser_setup import bank_account, create, create_scope, rental


@given("an unpaid rental agreement for 700 EUR rent and no advance payment")
def unpaid_rent(world, seed_data):
    rental(world, seed_data, advance="0.00")


@given("an unpaid rental agreement for 1234.56 EUR rent and no advance payment")
def unpaid_four_digit_rent(world, seed_data):
    unpaid_rent(world, seed_data)
    response = world.act("PATCH", f"/api/leases/{world.data['leases'][0]['id']}", json={"base_rent": "1234.56"})
    assert response.status_code == 200, world.describe_response()
    bank_account(world)


@when("the manager creates a January notice as of 2 March 2026 with automatic interest")
def interest_notice(browser_world, reset_dunning_settings):
    _set_interest(browser_world.world)
    browser_world.run(_browser_notice(browser_world, "2026-01-01", "2026-01-31", "2026-03-02"))


@when("the administrator saves the dunning settings in the browser")
def configure_interest_in_browser(browser_world, reset_dunning_settings):
    async def configure():
        page = browser_world.page
        await browser_world.visit("/mahnungen")
        await page.get_by_text("Mahneinstellungen", exact=True).click()
        await field(page, "Mahngebühr Stufe 2 (EUR)").fill("3,00")
        await field(page, "Mahngebühr Stufe 3 (EUR)").fill("4,00")
        await field(page, "Verzugszinsen automatisch berechnen").check()
        async with page.expect_response(lambda response: response.request.method == "PUT"
                                        and response.url.endswith("/api/dunning/settings")) as pending:
            await button(page, "Mahneinstellungen speichern").click()
        answer = await pending.value
        assert answer.ok, await answer.text()
        await expect(page.get_by_text("Mahneinstellungen gespeichert.", exact=True)).to_be_visible()

    browser_world.run(configure())


@when("the manager creates an April notice as of 31 October 2026")
@browser_step
async def april_notice_in_browser(browser_world):
    await _browser_notice(browser_world, "2026-04-01", "2026-04-30", "2026-10-31")


async def _browser_notice(browser_world, period_start, period_end, cutoff):
    page = browser_world.page
    data = browser_world.world.data
    await browser_world.visit("/mahnungen")
    await button(page, "+ Neue Mahnung").click()
    await field(page, "Empfängertyp *").select_option("tenant")
    await browser_world.select("Mieter *", data["tenants"][0]["id"])
    await browser_world.select("Mietvertrag *", data["leases"][0]["id"])
    for label, value in (("Zeitraum von *", period_start), ("Zeitraum bis *", period_end), ("Stichtag", cutoff)):
        await field(page, label).fill(value)
    await browser_world.scope()
    assert await field(page, "Basiszinssatz (%)").count() == 0
    await submit(browser_world, "Mahnung erstellen", "/api/dunning/tenant")
    await expect(button(page, "Mahnung ausgeben")).to_be_visible()
    data["notice_url"] = page.url


async def interest_amounts(browser):
    await browser.row("Verzugszinsen", "3,64 €")
    await browser.row("Mahngebühr", "3,00 €")
    await browser.row("Gesamtsumme", "106,64 €")
    await expect(browser.page.get_by_role("heading", name="Eingefrorene Zinsberechnung", exact=True)).to_be_visible()
    await expect(browser.page.get_by_text("Stichtag:", exact=False).filter(has_text="31.10.2026")).to_be_visible()


@then("the reloaded notice shows 3.64 EUR interest and 106.64 EUR in total")
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


@then("the issued principal, interest and open amounts are readable without horizontal scrolling")
@browser_step
async def readable_issued_amounts(browser_world):
    page = browser_world.page
    await page.reload()
    await expect(page.get_by_text("Ausgestellt", exact=True).first).to_be_visible()
    for width in (1280, 360):
        await page.set_viewport_size({"width": width, "height": 900})
        if width == 360:
            await expect(page.locator("aside")).to_have_count(0)
        for title, amounts in (
            ("Eingefrorene Zinsberechnung", ["1.234,56 €", "11,66 €"]),
            ("Rückstandsaufstellung", ["1.234,56 €"]),
        ):
            card = page.locator(".card").filter(has=page.get_by_role("heading", name=title, exact=True))
            table = card.get_by_role("table").first
            await table.scroll_into_view_if_needed()
            # Read the initial table viewport: no horizontal scrolling to reach money.
            await table.evaluate("table => { table.parentElement.scrollLeft = 0; }")
            row = table.locator("tbody tr").first
            for index, value in enumerate(amounts):
                cell = row.get_by_role("cell").nth(index)
                if title == "Eingefrorene Zinsberechnung" and index == 0:
                    await expect(cell.locator("span").first).to_have_text(value)
                else:
                    await expect(cell).to_have_text(value)
                fits = await cell.evaluate("cell => { const box = cell.getBoundingClientRect(); const clip = cell.closest('table').parentElement.getBoundingClientRect(); return box.left >= clip.left && box.right <= clip.right && box.right <= innerWidth && cell.scrollWidth <= cell.clientWidth; }")
                assert fits, f"{title}: {value} is clipped at {width} px"
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Page overflows at {width} px"


@then("the issue time is displayed in German date and minute format")
@browser_step
async def readable_issue_time(browser_world):
    row = browser_world.page.get_by_role("row").filter(has=browser_world.page.get_by_role("cell", name="Ausgestellt am", exact=True))
    await expect(row.get_by_role("cell").nth(1)).to_have_text(re.compile(r"^\d{2}\.\d{2}\.\d{4}, \d{2}:\d{2} Uhr$"))
    value = await row.get_by_role("cell").nth(1).inner_text()
    await expect(browser_world.page.get_by_text(f"Ausgestellt am: {value}", exact=True)).to_be_visible()


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
        for value in (r"100[.,]00", r"1[.,]27\s*%", r"1[.,]52\s*%", r"\b84\b", r"\b123\b", r"3[.,]64", r"106[.,]64"):
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
    await expect(page.get_by_text("10.100,00 € offen", exact=True)).to_be_visible()
    await page.get_by_role("link", name="Mahnung erstellen", exact=True).click()
    await expect(page.get_by_role("heading", name="Neue Mahnung anlegen", exact=True)).to_be_visible()


@when("the manager corrects a reporting period outside that scope")
@browser_step
async def correct_period(browser_world):
    page = browser_world.page
    await field(page, "Zeitraum von *").fill("2025-01-01")
    await field(page, "Stichtag").fill("2027-01-15")
    await button(page, "Mahnung erstellen").click()
    await expect(page.get_by_text("Report period is outside accounting scope", exact=True)).to_be_visible()
    await field(page, "Zeitraum von *").fill("2026-01-01")


@then("the notice form keeps the first scope selected and requires no manual rate")
@browser_step
async def scope_retained(browser_world):
    await expect(field(browser_world.page, "Abrechnungskreis")).to_have_value(browser_world.world.data["scope"]["id"])
    assert await field(browser_world.page, "Basiszinssatz (%)").count() == 0


@when("the manager creates the scoped notice")
@browser_step
async def create_scoped_notice(browser_world):
    await field(browser_world.page, "Mahngebühr (Override)").fill("0")
    await submit(browser_world, "Mahnung erstellen", "/api/dunning/tenant")
    await expect(button(browser_world.page, "Entwurf verwerfen")).to_be_visible()
    browser_world.world.data["notice_url"] = browser_world.page.url


@then("the notice shows only the first scope's outstanding rent")
@browser_step
async def scoped_amount(browser_world):
    await browser_world.row("Hauptforderung (Rückstand)", "10.100,00 €")


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


@then("the preview offers three unselected arrears of 850 EUR each with 6.23 EUR interest")
@browser_step
async def preview_candidates(browser_world):
    batch = browser_world.page.get_by_role("region", name="Mahnlauf", exact=True)
    assert await field(batch, "Basiszinssatz (%)").count() == 0
    await expect(batch.get_by_role("checkbox")).to_have_count(3)
    for index, person in enumerate(browser_world.world.data["people"]):
        row = batch.get_by_role("row").filter(has_text=person.name)
        await expect(row.get_by_role("checkbox")).not_to_be_checked()
        await expect(row.get_by_role("cell").nth(2)).to_have_text("850,00 €")
        await expect(row).to_contain_text("6,23 €")
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
    await expect(dialog).to_contain_text("3 Mahnentwürfe: Hauptforderung 2.550,00 €; Gesamtsumme 2.568,69 €.")
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
        browser_world.page.get_by_role("columnheader", name="Empfänger", exact=True)
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
        await browser_world.row("Hauptforderung (Rückstand)", "850,00 €")
        await browser_world.row("Verzugszinsen", "6,23 €")


@given(parsers.parse("April rent of 700 EUR with {paid:d} EUR paid on 7 April 2026"))
def april_rent(world, seed_data, paid):
    rental(world, seed_data, advance="0.00")
    account = bank_account(world)
    payment = create(world, "/api/payments", {
        "accounting_scope_id": world.data["scope"]["id"],
        "bank_account_id": account["id"], "tenant_profile_id": world.data["tenants"][0]["id"],
        "lease_agreement_id": world.data["leases"][0]["id"], "amount": f"{paid}.00",
        "direction": "IN", "value_date": "2026-04-07", "payment_type": "KALTMIETE",
        "period_year": 2026, "period_month": 4,
    })
    assert world.act("POST", f"/api/payments/{payment['id']}/book").status_code == 200


@when(parsers.parse("the manager checks April reminders on {day:d} April 2026"))
def april_reminder(world, day, reset_dunning_settings):
    _set_interest(world)
    world.act("POST", "/api/dunning/tenant", json={
        "tenant_profile_id": world.data["tenants"][0]["id"],
        "lease_agreement_id": world.data["leases"][0]["id"],
        "accounting_scope_id": world.data["scope"]["id"],
        "period_start": "2026-04-01", "period_end": "2026-04-30",
        "reference_date": f"2026-04-{day:02}", "override_fee": "0.00",
    })


@then("no April reminder or interest is due")
def april_on_time(world):
    assert world.response.status_code == 422, world.describe_response()
    assert world.response.json()["detail"] == "No overdue items found for this tenant in the given period"


@then("the April reminder claims 100 EUR due on 7 April and interest from 8 April")
def april_short(world):
    assert world.response.status_code == 201, world.describe_response()
    notice = world.response.json()
    assert notice["principal_amount"] == "100.00"
    assert notice["interest_amount"] == "0.02"
    assert notice["included_items"][0]["due_date"] == "2026-04-07"
    assert notice["interest_snapshot"]["items"][0]["days"] == 1
    assert notice["interest_snapshot"]["items"][0]["interest_start"] == "2026-04-08"


@when("the manager opens the created April reminder")
@browser_step
async def open_april_reminder(browser_world):
    response = browser_world.world.response
    assert response.status_code == 201, browser_world.world.describe_response()
    await browser_world.visit(f"/mahnungen/{response.json()['id']}")
    await expect(button(browser_world.page, "Mahnung ausgeben")).to_be_visible()


@then("the issued April reminder names 8 April 2026 as the interest start in the detail and PDF")
@browser_step
async def explicit_april_interest_start(browser_world):
    page = browser_world.page
    await page.reload()
    await expect(page.get_by_text("Ausgestellt", exact=True).first).to_be_visible()
    for width in (1280, 360):
        await page.set_viewport_size({"width": width, "height": 900})
        if width == 360:
            await expect(page.locator("aside")).to_have_count(0)
        if os.environ.get("VERWALINO_CAPTURE_DESIGN") == "1":
            directory = Path(__file__).resolve().parents[4] / "docs/assets/design/dunning-notice"
            await page.locator(".card").filter(has=page.get_by_role("heading", name="Eingefrorene Zinsberechnung", exact=True)).scroll_into_view_if_needed()
            await page.screenshot(path=str(directory / f"after-{width}.png"), full_page=True)
    async with page.expect_popup() as opened:
        await button(page, "PDF herunterladen").click()
    popup = await opened.value
    try:
        await popup.wait_for_load_state()
        response = await browser_world.context.request.get(popup.url)
        assert response.status == 200
        assert response.headers["content-type"].startswith("application/pdf")
        with pdfplumber.open(io.BytesIO(await response.body())) as pdf:
            text = " ".join(part.extract_text() or "" for part in pdf.pages)
        assert re.search(r"Zinsbeginn:\s*08\.04\.2026", text), text
        assert "07.04.2026" in text and re.search(r"0[.,]02", text), text
        # The issued document identifies the tenancy and the payment destination.
        assert "Wohnung 1" in text, text
        assert "Sonnenhof" in text, text
        assert "DE02120300000000202051" in text, text
    finally:
        await popup.close()
    for width in (1280, 360):
        await page.set_viewport_size({"width": width, "height": 900})
        await expect(page.locator("aside")).to_have_count(0 if width == 360 else 1)
        start = page.get_by_text("Zinsbeginn:", exact=False).filter(has_text="08.04.2026")
        await expect(start).to_be_visible()
        assert await start.evaluate("element => { const box = element.getBoundingClientRect(); return box.left >= 0 && box.right <= innerWidth; }")
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@given(parsers.parse('the April rental unit is "{usage}"'))
def april_unit_use(world, usage):
    unit_id = world.data["units"][0]["id"]
    response = world.act("PUT", f"/api/units/{unit_id}", json={"usage_type": usage})
    assert response.status_code == 200, world.describe_response()


def _set_interest(world):
    path = "/api/dunning/settings"
    settings = world.act("GET", path).json()
    body = {key: value for key, value in settings.items() if key != "base_rate_covered_until"}
    response = world.act("PUT", path, json={**body, "default_interest_enabled": True})
    assert response.status_code == 200, world.describe_response()


@given("the organization enables automatic dunning interest")
def enable_automatic_interest(world, reset_dunning_settings):
    _set_interest(world)


@when("the administrator opens the dunning settings")
def open_dunning_settings(world):
    world.act("GET", "/api/dunning/settings")


@then("the settings report the base rate is covered until the end of 2026")
def base_rate_coverage(world):
    assert world.response.status_code == 200, world.describe_response()
    assert world.response.json()["base_rate_covered_until"] == "2026-12-31"


@when("the administrator saves reminder fees of 0, 3 and 4 EUR and enables interest")
def configure_april_reminders(world, reset_dunning_settings):
    response = world.act("PUT", "/api/dunning/settings", json={
        "fee_level_1": "0.00", "fee_level_2": "3.00", "fee_level_3": "4.00",
        "default_interest_enabled": True,
    })
    assert response.status_code == 200, world.describe_response()


def _april_notice(world, cutoff):
    response = world.act("POST", "/api/dunning/tenant", json={
        "tenant_profile_id": world.data["tenants"][0]["id"],
        "lease_agreement_id": world.data["leases"][0]["id"],
        "accounting_scope_id": world.data["scope"]["id"],
        "period_start": "2026-04-01", "period_end": "2026-04-30",
        "reference_date": cutoff,
    })
    assert response.status_code == 201, world.describe_response()
    return response.json()


@when("the manager issues a first reminder for the April arrears")
def issue_first_april_reminder(world):
    notice = _april_notice(world, "2026-04-08")
    assert notice["fee_amount"] == "0.00"
    response = world.act("POST", f"/api/dunning/{notice['id']}/issue")
    assert response.status_code == 200, world.describe_response()


@when("the manager prepares a second April reminder as of 31 October 2026")
def second_april_reminder(world):
    world.data["second_reminder"] = _april_notice(world, "2026-10-31")


@then(parsers.parse("the draft contains a 3.00 EUR fee and {interest} EUR interest at a {premium:d} point premium"))
def april_reminder_amounts(world, interest, premium):
    notice = world.data["second_reminder"]
    assert notice["level"] == 2
    assert notice["fee_amount"] == "3.00"
    assert notice["interest_amount"] == interest
    assert notice["interest_snapshot"]["premium"] == premium


@then("the interest is split into 84 days at 1.27 percent and 123 days at 1.52 percent")
def april_reminder_segments(world):
    segments = world.data["second_reminder"]["interest_snapshot"]["items"][0]["segments"]
    assert [(row["days"], row["base_interest_rate"]) for row in segments] == [(84, "1.27"), (123, "1.52")]



@then(parsers.parse("the issued PDF shows the same {premium:d} point premium"))
def issued_premium(world, premium):
    notice_id = world.data["second_reminder"]["id"]
    path = f"/api/dunning/{notice_id}"
    assert world.act("POST", f"{path}/issue").status_code == 200, world.describe_response()
    response = world.act("GET", f"{path}/pdf")
    assert response.status_code == 200, world.describe_response()
    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
        text = " ".join(part.extract_text() or "" for part in pdf.pages)
    paragraph = 2 if premium == 9 else 1
    assert f"Basiszinssatz + {premium} Prozentpunkte (§ 288 Abs. {paragraph} BGB)" in text
    assert "07.04.2026" in text


@then("the rent account lists the issued interest separately from the open rent")
@browser_step
async def informational_ledger(browser_world):
    await ledger(browser_world, start="2026-04-01", end="2026-04-30")
    page = browser_world.page
    await expect(page.get_by_role("heading", name="Ausgestellte Mahnungen")).to_be_visible()
    notice_row = page.get_by_role("row").filter(has_text="Stufe 2")
    await expect(notice_row).to_contain_text("3,64")
    await expect(notice_row).to_contain_text("3,00")
    await browser_world.row("Saldo", "100,00 €")


@when("the manager opens that lease account without an accounting scope")
@browser_step
async def unscoped_rent_account(browser_world):
    page = browser_world.page
    await browser_world.visit("/mietkonto")
    await browser_world.select("Mietvertrag *", browser_world.world.data["leases"][0]["id"])
    await field(page, "Zeitraum von *").fill("2026-04-01")
    await field(page, "Zeitraum bis *").fill("2026-04-30")
    await button(page, "Abrufen").click()
    await expect(page.get_by_role("heading", name="Kontoauszug", exact=True)).to_be_visible()


@then("the account links to dunning history for notices without a lease assignment")
@browser_step
async def unassigned_dunning_history(browser_world):
    hint = browser_world.page.get_by_text("Ältere Mahnungen ohne Vertragszuordnung", exact=False)
    await expect(hint).to_be_visible()
    await expect(hint.get_by_role("link", name="Mahnungen", exact=True)).to_have_attribute("href", "/mahnungen")
