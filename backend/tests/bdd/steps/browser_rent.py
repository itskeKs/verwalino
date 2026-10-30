"""Rent history and deposits; business data prerequisites use the API World."""
from pytest_bdd import given, then, when
from playwright.async_api import expect

from tests.bdd.steps.browser_common import browser_step, button, field, ledger, ledger_row, submit
from tests.bdd.steps.browser_setup import bank_account, rental


@given("a rental agreement for 700 EUR rent and 150 EUR advance payment")
def rent_agreement(world, seed_data):
    rental(world, seed_data)


@when('the manager records 750 EUR rent from 1 April 2026 in "Miete ändern"')
@browser_step
async def record_change(browser_world):
    data = browser_world.world.data
    await browser_world.visit(f"/objekte/{data['property']['id']}/einheiten/{data['units'][0]['id']}")
    page = browser_world.page
    await page.get_by_role("button", name="Miete ändern ab", exact=False).click()
    dialog = page.get_by_role("dialog", name="Miete ändern", exact=True)
    for label, value in (("Gültig ab", "2026-04-01"), ("Kaltmiete (EUR)", "750.00"), ("Vorauszahlung (EUR)", "150.00")):
        await field(dialog, label).fill(value)
    await button(dialog, "Mietänderung speichern").click()
    await expect(dialog).not_to_be_visible()


@then("the reloaded rent history shows the change beside the original contract")
@browser_step
async def history(browser_world):
    page = browser_world.page
    await page.reload()
    table = page.get_by_role("table", name="Miethistorie:", exact=False)
    row = table.get_by_role("row").filter(has=page.get_by_role("cell", name="2026-04-01", exact=True))
    await expect(row.get_by_role("cell")).to_have_text(["2026-04-01", "750.00", "150.00", "Mietänderung"])
    original = page.get_by_role("row").filter(has=page.get_by_role("cell", name=browser_world.world.data["people"][0].name, exact=True))
    await expect(original.get_by_role("cell", name="700.00", exact=True)).to_be_visible()
    await expect(original.get_by_role("cell", name="150.00", exact=True)).to_be_visible()


@then("the annual rent account shows the old rent until March and the new rent from April")
@browser_step
async def changed_ledger(browser_world):
    await ledger(browser_world)
    await browser_world.row("Soll gesamt", "10650.00 EUR")
    # Representative boundary months plus the full 24-row year.
    for period, amount in (("2026/01", "700.00"), ("2026/03", "700.00"), ("2026/04", "750.00"), ("2026/12", "750.00")):
        await ledger_row(browser_world, period, "Kaltmiete", [amount, "0.00", amount])
        await ledger_row(browser_world, period, "NK-Vorauszahlung", ["150.00", "0.00", "150.00"])
    table = browser_world.page.get_by_role("table").filter(has=browser_world.page.get_by_role("columnheader", name="Offen", exact=True))
    await expect(table.locator("tbody tr")).to_have_count(24)


@given("a rental agreement with an expected deposit of 1800 EUR and a deposit bank account")
def deposit_agreement(world, seed_data):
    rental(world, seed_data, deposit="1800.00")
    bank_account(world, deposit=True)


async def deposit_overview(browser):
    await browser.visit("/kautionen")
    await browser.scope()
    await button(browser.page, "Aktualisieren").click()


@when('the manager opens "Kautionen" for the rental accounting scope')
@browser_step
async def open_deposits(browser_world):
    await deposit_overview(browser_world)


async def assert_deposit(browser, paid, refunded, held):
    page = browser.page
    row = page.get_by_role("row").filter(has=page.get_by_role("cell", name="Wohnung 1", exact=True))
    await expect(row.get_by_role("cell")).to_have_text([
        f"{browser.world.data['people'][0].name}Mietvertrag ab 2026-01-01 (laufend)", browser.world.data["property"]["name"], "Wohnung 1",
        "1800.00 EUR", f"{paid}.00 EUR", f"{refunded}.00 EUR", f"{held}.00 EUR",
    ])


@then("the deposit overview shows 0 EUR paid, 0 EUR refunded and 0 EUR held")
@browser_step
async def empty_deposit(browser_world):
    await assert_deposit(browser_world, 0, 0, 0)


@then("the deposit overview shows 900 EUR paid, 0 EUR refunded and 900 EUR held")
@browser_step
async def first_deposit(browser_world):
    await deposit_overview(browser_world)
    await assert_deposit(browser_world, 900, 0, 900)


async def deposit_payment(browser, direction, amount, day):
    page = browser.page
    # Follow the overview's link for the first receipt; subsequent ones return
    # through the same overview rather than inventing a separate deposit form.
    await deposit_overview(browser)
    await page.get_by_role("link", name="Zahlung erfassen", exact=True).click()
    await button(page, "+ Neue Zahlung").click()
    await browser.select("Bankkonto *", browser.world.data["account"]["id"])
    await field(page, "Zahlungstyp *").select_option("KAUTION")
    await field(page, "Richtung *").select_option(direction)
    await field(page, "Betrag *").fill(amount)
    await field(page, "Wertstellungsdatum *").fill(day)
    await browser.select("Mieter", browser.world.data["tenants"][0]["id"])
    await browser.select("Mietvertrag", browser.world.data["leases"][0]["id"])
    await button(page, "Anlegen").click()
    await browser.row("Status", "Entwurf")
    page.once("dialog", lambda dialog: dialog.accept())
    await button(page, "Buchen").click()
    await browser.row("Status", "Gebucht")
    await browser.row("Richtung", "Eingang" if direction == "IN" else "Ausgang")
    await browser.row("Betrag", f"{amount} EUR")


@when("the manager books a deposit receipt of 900 EUR on 2 January 2026")
@browser_step
async def first_receipt(browser_world):
    await deposit_payment(browser_world, "IN", "900.00", "2026-01-02")


@when("the manager books a deposit receipt of 900 EUR on 16 January 2026")
@browser_step
async def second_receipt(browser_world):
    await deposit_payment(browser_world, "IN", "900.00", "2026-01-16")


@when("the manager books a deposit refund of 300 EUR on 1 February 2026")
@browser_step
async def refund(browser_world):
    await deposit_payment(browser_world, "OUT", "300.00", "2026-02-01")


@then("the reloaded deposit overview shows 1800 EUR paid, 300 EUR refunded and 1500 EUR held")
@browser_step
async def retained_deposit(browser_world):
    await deposit_overview(browser_world)
    await browser_world.page.reload()
    await browser_world.scope()
    await button(browser_world.page, "Aktualisieren").click()
    await assert_deposit(browser_world, 1800, 300, 1500)


@then("the rent account still shows no rent payments")
@browser_step
async def no_rent_payments(browser_world):
    await ledger(browser_world)
    await browser_world.row("Soll gesamt", "10200.00 EUR")
    await browser_world.row("Ist gesamt", "0.00 EUR")
