"""Statement review and rent assignment through the real browser controls."""
from pathlib import Path
import os
from decimal import Decimal

from pytest_bdd import given, then, when, parsers
from playwright.async_api import expect

from tests.bdd.steps.browser_common import browser_step, button, field, ledger, ledger_row
from tests.bdd.steps.browser_setup import bank_account, rental
from tests.bdd.steps.browser_setup import create

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


@given(parsers.parse('a confirmed electricity cost of {total:d} EUR and {debits}'))
def electricity_cost(world, total, debits):
    create_electricity_cost(world, total)
    count = 2 if debits == 'two 60 EUR bank debits' else 1
    assert debits in ('two 60 EUR bank debits', 'a 60 EUR bank debit')
    content = 'Buchungstag;Wertstellung;Betrag;Name;Verwendungszweck\n' + ''.join(
        f'10.0{index+1}.2026;10.0{index+1}.2026;-60,00;Stadtwerke Synthetisch;Stromabschlag {index+1}\n'
        for index in range(count))
    response = world.act('POST', f"/api/bank-accounts/{world.data['account']['id']}/imports",
                         params={'dry_run': 'false'}, files={'file': ('electricity.csv', content.encode(), 'text/csv')})
    assert response.status_code == 200, world.describe_response()


@given('a confirmed 60 EUR electricity cost for the January Sparkasse debit')
def sparkasse_electricity_cost(world):
    create_electricity_cost(world, 60)


def create_electricity_cost(world, total):
    categories = world.act('GET', '/api/cost-categories').json()
    category = next(item for item in categories if item['code'] == 'ALLGEMEINSTROM')
    cost = create(world, '/api/cost-entries', {
        'property_id': world.data['property']['id'], 'accounting_scope_id': world.data['scope']['id'],
        'cost_category_id': category['id'], 'cost_category_plan_version': category['plan_version'],
        'gross_amount': f'{total}.00', 'invoice_date': '2026-01-10',
        'service_period_from': '2026-01-01', 'service_period_to': '2026-12-31',
        'tenant_apportionable': 'UMLAGEFAEHIG', 'document_number': 'Strom Sonnenhof 2026',
    })
    response = world.act('POST', f"/api/cost-entries/{cost['id']}/confirm")
    assert response.status_code == 200, world.describe_response()
    world.data['electricity_cost'] = cost


@when(parsers.parse('the manager assigns the January Sparkasse electricity debit at {width:d} px'))
@browser_step
async def assign_sparkasse_cost(browser_world, width):
    page = browser_world.page
    await page.set_viewport_size({'width': width, 'height': 1000})
    row = transaction(browser_world, 'Abschlag Allgemeinstrom Musterstr 1').filter(
        has=page.get_by_role('cell', name='15.01.2026', exact=True))
    await button(row, 'Aufteilen').click()
    await field(page, 'Kostenposition').select_option(browser_world.world.data['electricity_cost']['id'])
    await expect(page.get_by_text('Nach Zuordnung noch offen: 0,00 €', exact=True)).to_be_visible()
    await button(page.get_by_role('dialog'), 'Kostenposition zuordnen').click()
    await expect(page.get_by_role('dialog')).to_have_count(0)
    await field(page, 'Status').select_option('ZUGEORDNET')
    await expect(row).to_contain_text('Zugeordnet')


async def assign_electricity(browser, number):
    await open_transactions(browser)
    page = browser.page
    await button(transaction(browser, f'Stromabschlag {number}'), 'Aufteilen').click()
    dialog = page.get_by_role('dialog', name='Umsatz aufteilen')
    await expect(dialog).to_be_visible()
    if await field(page, 'Kostenposition').count():
        await field(page, 'Kostenposition').select_option(browser.world.data['electricity_cost']['id'])
    if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1' and number == 1:
        directory = Path(__file__).resolve().parents[4] / 'docs/assets/design/bank-cost-assignment'
        directory.mkdir(parents=True, exist_ok=True)
        stage = 'after' if await field(page, 'Kostenposition').count() else 'before'
        for width in (1280, 360):
            await page.set_viewport_size({'width': width, 'height': 1000})
            if stage == 'after':
                assert await dialog.evaluate('(element) => element.scrollWidth <= element.clientWidth')
            await page.screenshot(path=str(directory / f'{stage}-{width}.png'), full_page=True)
        await page.set_viewport_size({'width': 1440, 'height': 1000})
    await field(page, 'Kostenposition').select_option(browser.world.data['electricity_cost']['id'])
    await button(dialog, 'Kostenposition zuordnen').click()
    await expect(dialog).to_have_count(0)
    await field(page, 'Status').select_option('ZUGEORDNET')
    await expect(transaction(browser, f'Stromabschlag {number}')).to_contain_text('Zugeordnet')


@when('the manager assigns the electricity debit to the existing cost')
@browser_step
async def first_electricity(browser_world):
    await assign_electricity(browser_world, 1)


@when('the manager assigns the second electricity debit to the existing cost')
@browser_step
async def second_electricity(browser_world):
    await assign_electricity(browser_world, 2)


@then(parsers.parse('the electricity debit is assigned and the cost has {remaining:d} EUR remaining'))
def electricity_remaining(world, remaining):
    cost = world.data['electricity_cost']
    response = world.act('GET', f"/api/cost-entries/{cost['id']}")
    assert response.status_code == 200, world.describe_response()
    assert Decimal(response.json()['bank_open_amount']) == remaining


@then(parsers.parse('the yearly electricity expense remains {total:d} EUR'))
def electricity_year(world, total):
    response = world.act('GET', '/api/reports/property-year', params={
        'property_id': world.data['property']['id'], 'year': 2026,
        'accounting_scope_id': world.data['scope']['id'],
    })
    assert response.status_code == 200, world.describe_response()
    assert Decimal(response.json()['expense_total']) == total


@when("the manager accepts the January rent suggestion")
@browser_step
async def accept_suggestion(browser_world):
    await open_transactions(browser_world)
    await button(transaction(browser_world, "Warmmiete Januar 2026"), "Vorschlag übernehmen").click()
    await expect(transaction(browser_world, "Warmmiete Januar 2026")).to_have_count(0)


@then('the January receipt appears under the "Zugeordnet" filter')
@browser_step
async def january_assigned(browser_world):
    await field(browser_world.page, "Status").select_option("ZUGEORDNET")
    await expect(transaction(browser_world, "Warmmiete Januar 2026")).to_contain_text("Zugeordnet")


@then("the January rent account shows both rent parts paid in full")
@browser_step
async def january_paid(browser_world):
    await ledger(browser_world, end="2026-01-31")
    await ledger_row(browser_world, "2026/01", "Kaltmiete", ["700.00", "700.00", "0.00"])
    await ledger_row(browser_world, "2026/01", "Betriebskosten-Vorauszahlung", ["150.00", "150.00", "0.00"])


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


@then('the "Neu" filter shows no transactions')
@browser_step
async def empty_new_filter(browser_world):
    await field(browser_world.page, "Status").select_option("NEU")
    await expect(browser_world.page.get_by_text("Keine Umsätze vorhanden.", exact=True)).to_be_visible()
    await expect(transactions(browser_world).locator("tbody tr")).to_have_count(0)


@when('the manager previews the original Sparkasse statement in "Kontoauszug"')
@browser_step
async def preview_sparkasse(browser_world):
    await open_transactions(browser_world)
    sample = Path(__file__).resolve().parents[4] / "docs" / "acceptance" / "data" / "sparkasse-2026-01-10.csv"
    await field(browser_world.page, "Kontoauszug").set_input_files(sample)
    await button(browser_world.page, "Vorschau erstellen").click()


@then("the browser shows 31 new transactions and an empty transaction list")
@browser_step
async def sparkasse_preview(browser_world):
    await expect(browser_world.page.get_by_text("31 Umsätze: 31 neu, 0 Dubletten.", exact=True)).to_be_visible()
    await expect(browser_world.page.get_by_text("Keine Umsätze vorhanden.", exact=True)).to_be_visible()


@when("the manager confirms the Sparkasse statement")
@browser_step
async def confirm_sparkasse(browser_world):
    await button(browser_world.page, "Übernehmen").click()


@then("the browser confirms 31 imported transactions")
@browser_step
async def sparkasse_imported(browser_world):
    await expect(browser_world.page.get_by_role("status")).to_have_text("31 neue Umsätze importiert.")
    await expect(transactions(browser_world).locator("tbody tr")).to_have_count(31)


def anna_april_receipt(browser):
    return transaction(browser, "Miete April").filter(has=browser.page.get_by_role("cell", name="Anna Beispiel", exact=True))


@then("Anna's April receipt shows 800 EUR on 07.04.2026 with her name and IBAN")
@browser_step
async def sparkasse_anna_receipt(browser_world):
    row = anna_april_receipt(browser_world)
    await expect(row).to_have_count(1)
    await expect(row.get_by_role("cell").nth(0)).to_have_text("07.04.2026")
    for value in ("Anna Beispiel", "DE89370400440532013000", "800,00 €"):
        await expect(row.get_by_role("cell", name=value, exact=True)).to_be_visible()


@then("Anna's April receipt visibly suggests her lease")
@browser_step
async def sparkasse_anna_suggestion(browser_world):
    page = browser_world.page
    row = anna_april_receipt(browser_world)
    await expect(row.locator("strong")).to_contain_text("Anna Beispiel")
    await expect(button(row, "Vorschlag übernehmen")).to_be_visible()
    await button(row, "Aufteilen").click()
    dialog = page.get_by_role("dialog", name="Umsatz aufteilen", exact=True)
    targets = field(dialog, "Mietvertrag")
    await expect(targets).to_have_count(2)
    for index in range(2):
        await expect(targets.nth(index).locator("option:checked")).to_have_text(
            "Anna Beispiel · Einheit Wohnung 1 · ab 2026-01-01 (laufend)"
        )
    await button(dialog, "Abbrechen").click()


@then("the browser shows 31 duplicate transactions and no new transactions")
@browser_step
async def sparkasse_duplicates(browser_world):
    await expect(browser_world.page.get_by_text("31 Umsätze: 0 neu, 31 Dubletten.", exact=True)).to_be_visible()


@then("the browser confirms no imported transactions and still lists 31 after reload")
@browser_step
async def sparkasse_unchanged(browser_world):
    await expect(browser_world.page.get_by_role("status")).to_have_text("0 neue Umsätze importiert.")
    await browser_world.page.reload()
    await field(browser_world.page, "Status").select_option("")
    await expect(transactions(browser_world).locator("tbody tr")).to_have_count(31)


@then("the browser refuses the foreign account statement in German and lists no transactions")
@browser_step
async def sparkasse_foreign_account(browser_world):
    page = browser_world.page
    await expect(page.get_by_role("alert")).to_have_text("IBAN im Kontoauszug stimmt nicht mit dem gewählten Bankkonto überein.")
    await expect(button(page, "Übernehmen")).to_have_count(0)
    await page.reload()
    await field(page, "Status").select_option("")
    await expect(page.get_by_text("Keine Umsätze vorhanden.", exact=True)).to_be_visible()


@then("the February rent account shows 250 EUR still open")
@browser_step
async def february_partial(browser_world):
    await ledger(browser_world, start="2026-02-01", end="2026-02-28")
    await ledger_row(browser_world, "2026/02", "Kaltmiete", ["700.00", "500.00", "200.00"])
    await ledger_row(browser_world, "2026/02", "Betriebskosten-Vorauszahlung", ["150.00", "100.00", "50.00"])
    await browser_world.row("Saldo", "250.00 EUR")
