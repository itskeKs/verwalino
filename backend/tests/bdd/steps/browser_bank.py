"""Statement review and rent assignment through the real browser controls."""
from pytest_bdd import given, then, when
from playwright.async_api import expect

from tests.bdd.steps.browser_common import browser_step, button, field, ledger, ledger_row, submit
from tests.bdd.steps.browser_setup import bank_account, rental

STATEMENT = (
    "Buchungstag;Wertstellung;Betrag;Name;Verwendungszweck\n"
    "02.01.2026;03.01.2026;850,00;Ada Synthetisch;Warmmiete Januar 2026\n"
    "02.02.2026;03.02.2026;600,00;Ada Synthetisch;Warmmiete Februar 2026\n"
    "04.02.2026;04.02.2026;25,00;Testdienst;Nichtmiete ignorieren\n"
).encode()


def transactions(browser):
    page = browser.page
    return page.get_by_role("table").filter(has=page.get_by_role("columnheader", name="Status", exact=True))


def transaction(browser, purpose):
    return transactions(browser).get_by_role("row").filter(has=browser.page.get_by_role("cell", name=purpose, exact=True))


async def open_transactions(browser):
    await browser.visit("/bankkonten")
    page = browser.page
    await field(page, "Objekte durchsuchen").fill(browser.world.data["property"]["name"])
    await browser.select("Nach Objekt filtern", browser.world.data["property"]["id"])
    row = page.get_by_role("row").filter(has=page.get_by_role("cell", name=browser.world.data["account"]["label"], exact=True))
    row = row.filter(has=page.get_by_role("cell", name=browser.world.data["property"]["name"], exact=True))
    await row.get_by_role("link", name="Kontoauszug importieren", exact=True).click()
    await expect(page.get_by_role("heading", name=f"Umsätze – {browser.world.data['account']['label']}", exact=True)).to_be_visible()


async def choose_statement(browser, name="synthetic.csv"):
    await field(browser.page, "Kontoauszug").set_input_files({"name": name, "mimeType": "text/csv", "buffer": statement(browser.world)})


def statement(world):
    return STATEMENT.replace(b"Ada Synthetisch", world.data["people"][0].name.encode())


@when('the manager previews the statement in "Kontoauszug"')
@browser_step
async def preview_statement(browser_world):
    await open_transactions(browser_world)
    await choose_statement(browser_world)
    await button(browser_world.page, "Vorschau erstellen").click()


@then("the browser shows the statement summary and an empty transaction list")
@browser_step
async def preview_summary(browser_world):
    page = browser_world.page
    for text in ("3 Umsätze: 3 neu, 0 Dubletten.", "Zeitraum: 02.01.2026 bis 04.02.2026", "Gesamtbetrag: 1.475,00 € · Davon neu: 1.475,00 €", "Keine Umsätze vorhanden."):
        await expect(page.get_by_text(text, exact=True)).to_be_visible()


@when("the manager changes the selected statement")
@browser_step
async def change_statement(browser_world):
    await choose_statement(browser_world, "renamed.csv")


@then('"Übernehmen" is hidden until another preview')
@browser_step
async def confirmation_hidden(browser_world):
    await expect(button(browser_world.page, "Übernehmen")).to_have_count(0)


@when("the manager previews and confirms the statement")
@browser_step
async def confirm_statement(browser_world):
    await button(browser_world.page, "Vorschau erstellen").click()
    await expect(button(browser_world.page, "Übernehmen")).to_be_visible()
    await button(browser_world.page, "Übernehmen").click()
    await expect(browser_world.page.get_by_role("status").filter(has_text="3 neue Umsätze importiert.")).to_be_visible()


@then("the imported transactions remain visible after reload")
@browser_step
async def persisted_transactions(browser_world):
    await browser_world.page.reload()
    await expect(transactions(browser_world).locator("tbody tr")).to_have_count(3)
    for value in ("850,00 €", "600,00 €", "25,00 €"):
        await expect(transactions(browser_world).get_by_role("cell", name=value, exact=True)).to_be_visible()


@then('the "Zugeordnet" filter shows no transactions')
@browser_step
async def empty_assigned_filter(browser_world):
    await field(browser_world.page, "Status").select_option("ZUGEORDNET")
    await expect(browser_world.page.get_by_text("Keine Umsätze vorhanden.", exact=True)).to_be_visible()


@given("a rental bank account with a full rent receipt, a partial receipt and an unrelated receipt")
def imported_receipts(world, seed_data):
    rental(world, seed_data)
    account = bank_account(world)
    response = world.act("POST", f"/api/bank-accounts/{account['id']}/imports", params={"dry_run": "false"}, files={"file": ("receipts.csv", statement(world), "text/csv")})
    assert response.status_code == 200, world.describe_response()


@when("the manager accepts the January rent suggestion")
@browser_step
async def accept_suggestion(browser_world):
    await open_transactions(browser_world)
    await button(transaction(browser_world, "Warmmiete Januar 2026"), "Vorschlag übernehmen").click()
    await expect(transaction(browser_world, "Warmmiete Januar 2026")).to_have_count(0)


@then("the January rent account shows both rent parts paid in full")
@browser_step
async def january_paid(browser_world):
    await ledger(browser_world, end="2026-01-31")
    await ledger_row(browser_world, "2026/01", "Kaltmiete", ["700.00", "700.00", "0.00"])
    await ledger_row(browser_world, "2026/01", "NK-Vorauszahlung", ["150.00", "150.00", "0.00"])


@when("the manager splits the February receipt into 500 EUR rent and 100 EUR advance payment")
@browser_step
async def split_receipt(browser_world):
    await open_transactions(browser_world)
    page = browser_world.page
    await button(transaction(browser_world, "Warmmiete Februar 2026"), "Aufteilen").click()
    dialog = page.get_by_role("dialog", name="Umsatz aufteilen", exact=True)
    amounts = field(dialog, "Betrag (EUR)")
    await amounts.nth(0).fill("500.00")
    await amounts.nth(1).fill("100.00")
    await button(dialog, "Zuordnung buchen").click()
    await expect(dialog).not_to_be_visible()


@when("the manager ignores the unrelated receipt")
@browser_step
async def ignore_receipt(browser_world):
    await button(transaction(browser_world, "Nichtmiete ignorieren"), "Ignorieren").click()
    await expect(transaction(browser_world, "Nichtmiete ignorieren")).to_have_count(0)


@then("the reloaded transaction filters separate assigned and ignored receipts")
@browser_step
async def assigned_filters(browser_world):
    page = browser_world.page
    await page.reload()
    await field(page, "Status").select_option("ZUGEORDNET")
    await expect(transaction(browser_world, "Warmmiete Februar 2026")).to_contain_text("Zugeordnet")
    await expect(button(transaction(browser_world, "Warmmiete Februar 2026"), "Aufteilen")).to_have_count(0)
    await field(page, "Status").select_option("IGNORIERT")
    await expect(transaction(browser_world, "Nichtmiete ignorieren")).to_contain_text("Ignoriert")
    await field(page, "Status").select_option("")
    await expect(transactions(browser_world).locator("tbody tr")).to_have_count(3)


@then("the February rent account shows 250 EUR still open")
@browser_step
async def february_partial(browser_world):
    await ledger(browser_world, start="2026-02-01", end="2026-02-28")
    await ledger_row(browser_world, "2026/02", "Kaltmiete", ["700.00", "500.00", "200.00"])
    await ledger_row(browser_world, "2026/02", "NK-Vorauszahlung", ["150.00", "100.00", "50.00"])
    await browser_world.row("Saldo", "250.00 EUR")
