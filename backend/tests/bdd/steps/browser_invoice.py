"""Journey 2 through the browser: the building-insurance invoice, read by the organization's models (issue #500).

The invoice is uploaded through the API and the worker is the real queue-fenced invoice worker run in this process
against the same database and document directory, with the deterministic fake providers standing in for the two
models (the browser stack runs no worker and leaves invoice processing off); the browser reads what the user sees.
Expected values come from the journey's bank statement (docs/acceptance/data), not from the fake: the invoice of
312,00 EUR is the insurer's debit of 20 March 2026, and the IBAN the invoice prints is the one of that debit line.
"""
import csv
import io
import os
import re
from decimal import Decimal
from pathlib import Path

from playwright.async_api import expect
from pytest_bdd import given, parsers, then, when

from app.declarations.fake_provider import FakeDocumentReadingProvider, FakeInvoiceExtractionProvider, synthetic_invoice_output
from app.declarations.provider import ProviderFailure, ProviderUsage
from app.declarations.schemas import ReadingBlock
from tests.bdd.steps.browser_common import browser_step
from tests.bdd.steps.invoice_processing import EXTRACTION_USAGE, READING_USAGE, _invoice, _process, _scan, _settings

ROOT = Path(__file__).resolve().parents[4]
INSURER = "Musterversicherung AG"
DESKTOP, PHONE = {"width": 1280, "height": 900}, {"width": 360, "height": 800}
READING_MODEL, EXTRACTION_MODEL = "gpt-5.4-mini", "gpt-5.6-luna"


def _statement_line() -> tuple[str, str, str]:
    """The insurer's debit of the journey's statement: (counterparty, counterparty IBAN, amount)."""
    text = (ROOT / "docs/acceptance/data/sparkasse-2026-01-10.csv").read_text(encoding="utf-8", errors="replace")
    row = next(row for row in csv.reader(io.StringIO(text), delimiter=";") if any(cell.startswith("Wohngebaeudeversicherung") for cell in row))
    ibans = [cell for cell in row if re.fullmatch(r"DE\d{20}", cell)]
    name = row[row.index(ibans[-1]) - 1]
    return name, ibans[-1], row[row.index(ibans[-1]) + 2]


def _printed_iban(iban: str) -> str:
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


def _insurance_output():
    """What the extraction model answers: the IBAN of the statement's debit line, as the invoice prints it."""
    _name, iban, _amount = _statement_line()
    return synthetic_invoice_output(vendor_name=(INSURER, 0.95), vendor_iban=(_printed_iban(iban), 0.9))


def _structured(kind: str) -> tuple[str, str, bytes]:
    """The same invoice as structured data: an XRechnung (UBL) or a ZUGFeRD PDF with an embedded CII."""
    _name, iban, _amount = _statement_line()
    ubl = f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
  <cbc:ID>RE-2026-0042</cbc:ID><cbc:IssueDate>2026-01-15</cbc:IssueDate>
  <cac:InvoicePeriod><cbc:StartDate>2026-01-01</cbc:StartDate><cbc:EndDate>2026-12-31</cbc:EndDate></cac:InvoicePeriod>
  <cac:AccountingSupplierParty><cac:Party><cac:PartyName><cbc:Name>{INSURER}</cbc:Name></cac:PartyName></cac:Party></cac:AccountingSupplierParty>
  <cac:PaymentMeans><cbc:PaymentMeansCode>58</cbc:PaymentMeansCode>
    <cac:PayeeFinancialAccount><cbc:ID>{iban}</cbc:ID></cac:PayeeFinancialAccount></cac:PaymentMeans>
  <cac:LegalMonetaryTotal><cbc:TaxInclusiveAmount currencyID="EUR">312.00</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="EUR">312.00</cbc:PayableAmount></cac:LegalMonetaryTotal>
</Invoice>""".encode()
    if kind == "xrechnung":
        return "versicherung-xrechnung.xml", "application/xml", ubl
    from tests.test_invoice_extraction_worker import zugferd_pdf
    cii = f"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
  xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
  xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocument><ram:ID>RE-2026-0042</ram:ID>
    <ram:IssueDateTime><udt:DateTimeString format="102">20260115</udt:DateTimeString></ram:IssueDateTime></rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:ApplicableHeaderTradeAgreement><ram:SellerTradeParty><ram:Name>{INSURER}</ram:Name></ram:SellerTradeParty></ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:SpecifiedTradeSettlementPaymentMeans><ram:TypeCode>58</ram:TypeCode>
        <ram:PayeePartyCreditorFinancialAccount><ram:IBANID>{iban}</ram:IBANID></ram:PayeePartyCreditorFinancialAccount>
      </ram:SpecifiedTradeSettlementPaymentMeans>
      <ram:BillingSpecifiedPeriod>
        <ram:StartDateTime><udt:DateTimeString format="102">20260101</udt:DateTimeString></ram:StartDateTime>
        <ram:EndDateTime><udt:DateTimeString format="102">20261231</udt:DateTimeString></ram:EndDateTime>
      </ram:BillingSpecifiedPeriod>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation><ram:GrandTotalAmount>312.00</ram:GrandTotalAmount></ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>""".encode()
    return "versicherung-zugferd.pdf", "application/pdf", zugferd_pdf(cii)


def _upload(world, name: str, mime: str, data: bytes) -> str:
    """Upload as the signed-in administrator does, through the API: the browser stack runs no worker, so this
    process stores the original and queues the attempt itself and runs the worker below."""
    answer = world.act("POST", "/api/invoice-documents/upload", files={"file": (name, data, mime)})
    assert answer.status_code == 201, world.describe_response()
    return answer.json()["id"]


async def _capture(browser_world, path: str, name: str, *, card: str | None = None, container: str = "section"):
    """With ``VERWALINO_CAPTURE_DESIGN=1``: the screen of one of DESIGN.md's invoice rows at both widths.

    The files are ``docs/assets/design/invoice-processing/<name>-<state>-<width>.png``; ``VERWALINO_CAPTURE_STATE``
    names the state (``after`` unless a run on the previous frontend says ``before``). ``card`` limits the picture to
    the ``container`` element (a section unless said otherwise) with that heading. It runs before the step's assertions,
    so a previous frontend is captured as well.
    """
    if os.environ.get("VERWALINO_CAPTURE_DESIGN") != "1":
        return
    page = browser_world.page
    directory = ROOT / "docs/assets/design/invoice-processing"
    directory.mkdir(parents=True, exist_ok=True)
    state = os.environ.get("VERWALINO_CAPTURE_STATE", "after")
    for viewport in (DESKTOP, PHONE):
        # The card of a settings page is taller than the window: the window is made as tall as the card.
        await page.set_viewport_size({**viewport, "height": 3200} if card else viewport)
        await browser_world.visit(path)
        await page.wait_for_load_state("networkidle")
        file = str(directory / f"{name}-{state}-{viewport['width']}.png")
        if not card:
            await page.screenshot(path=file, full_page=True)
            continue
        section = page.locator(container).filter(has=page.get_by_role("heading", name=card, exact=True))
        # A previous frontend has no such card: its absence is the state to show, not a failure of the capture.
        if await section.count():
            await section.first.screenshot(path=file)
    await page.set_viewport_size(DESKTOP)


async def _fits_the_width(page):
    assert not await page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"), "horizontal scrolling"


async def _open_invoice(browser_world, invoice_id: str):
    await browser_world.visit(f"/belege/{invoice_id}")
    await expect(browser_world.page.get_by_role("link", name="Dokument ansehen")).to_be_visible()


def _row(page, label: str):
    """The row of the card „Extrahierte Felder“ with this label."""
    return page.locator(".extraction-fields > div").filter(has=page.get_by_text(label, exact=True))


# A value of the card stays whole when it fits a line of its own: no word of it is cut across two lines.
_CUT_WORDS = r"""() => {
  const cut = [];
  for (const value of document.querySelectorAll(".extraction-fields dd")) {
    const walker = document.createTreeWalker(value, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      for (const word of node.textContent.matchAll(/\S+/g)) {
        const range = document.createRange();
        range.setStart(node, word.index);
        range.setEnd(node, word.index + word[0].length);
        if (range.getClientRects().length > 1) cut.push(word[0]);
      }
    }
  }
  return cut;
}"""


async def _values_stay_whole(page):
    assert await page.evaluate(_CUT_WORDS) == [], "a value of the card is broken inside a word"


@when("the administrator opens the AI settings of the organization")
@browser_step
async def open_settings(browser_world):
    await _capture(browser_world, "/einstellungen", "settings-card-no-invoices", card="KI für Ihre Organisation")


@then("the one switch reads that it imports declarations and evaluates the documents of the inbox and no text of the card names invoices at 1280 and 360 pixels")
@browser_step
async def settings_switch(browser_world):
    """The stack runs with HV_INVOICE_EXTRACTION_ENABLED=false, as an installation whose operator switched invoice processing
    off: its card is about declarations and the documents of the inbox, which every installation reads with the organization's
    models, and names no invoice. The wording of an installation that processes invoices is tested in the card's own
    component test."""
    page = browser_world.page
    for viewport in (DESKTOP, PHONE):
        await page.set_viewport_size(viewport)
        await browser_world.visit("/einstellungen")
        switch = page.get_by_label("Teilungserklärungen importieren sowie Dokumente im Posteingang auswerten", exact=True)
        await expect(switch).to_be_checked()
        await expect(switch).to_have_attribute("aria-describedby", "ai-import-info")
        card = page.locator("section").filter(has=page.get_by_role("heading", name="KI für Ihre Organisation", exact=True))
        # A structured e-invoice is named as what is read directly ("E-Rechnung", "XRechnung"); no invoice is evaluated or sent.
        await expect(card).not_to_contain_text(re.compile(r"(?<!E-)(?<!X)Rechnung"))
        await _fits_the_width(page)
    await page.set_viewport_size(DESKTOP)


@given("the organization has switched the AI import off")
def ai_import_off(world, invoice_workspace):
    _settings(world, import_enabled=False)


@when("the administrator uploads the scanned building-insurance invoice")
def upload_scan(world):
    world.data["invoice"] = _upload(world, "versicherung-2026.pdf", "application/pdf", _scan())


@when("the administrator uploads the building-insurance invoice as XRechnung and as ZUGFeRD")
def upload_structured(world):
    world.data["invoices"] = [_upload(world, *_structured(kind)) for kind in ("xrechnung", "zugferd")]


def _printed_lines() -> list[str]:
    """What the scanned invoice prints, line by line: what the reading model reads from its one page."""
    _name, iban, _amount = _statement_line()
    return [INSURER, "Wohngebäudeversicherung 2026, Musterstr 1", "Rechnung RE-2026-0042 vom 15.01.2026", "Gesamtbetrag 312,00 EUR",
            f"IBAN {_printed_iban(iban)}"]


def _printed_invoice():
    return FakeDocumentReadingProvider(transcripts={("invoice", 1): [ReadingBlock(kind="TEXT", text=line) for line in _printed_lines()]},
                                       usage=READING_USAGE)


def _extraction_failure() -> ProviderFailure:
    """The extraction model times out after the reading was paid: the request was sent and consumed 300 input tokens."""
    return ProviderFailure("PROVIDER_TIMEOUT", False, "timed out", usage=ProviderUsage(300, 0, Decimal("0.000225"), 1))


@when("the worker processes the scan with the offline models")
def process_scan(world):
    _process(world, reader=_printed_invoice(), extractor=FakeInvoiceExtractionProvider(output=_insurance_output(), usage=EXTRACTION_USAGE))


@when("the worker processes the scan while the extraction model fails")
def process_scan_failing(world):
    _process(world, reader=_printed_invoice(), extractor=FakeInvoiceExtractionProvider(failure=_extraction_failure()))


@when("the worker processes both without any model")
def process_structured(world):
    _process(world, count=2)


@then(parsers.parse('the invoice page proposes 312,00 € dated 15.01.2026 from "{vendor}" and the IBAN of the statement\'s insurer line at 1280 and 360 pixels'))
@browser_step
async def scan_proposal(browser_world, vendor):
    page = browser_world.page
    await _capture(browser_world, f"/belege/{browser_world.world.data['invoice']}", "invoice-proposal")
    await _capture(browser_world, f"/belege/{browser_world.world.data['invoice']}", "invoice-fields", card="Extrahierte Felder", container="div.card")
    assert vendor == INSURER
    # What the journey's statement says the insurer took: the proposal equals the debit, not what the fake returns.
    name, iban, amount = _statement_line()
    assert (name, amount) == (vendor, "-312,00")
    for viewport in (DESKTOP, PHONE):
        await page.set_viewport_size(viewport)
        await _open_invoice(browser_world, browser_world.world.data["invoice"])
        await expect(page.get_by_text("Felder vorbereitet · bitte prüfen")).to_be_visible()
        await expect(_row(page, "Lieferant")).to_contain_text(vendor)
        await expect(_row(page, "Rechnungsdatum")).to_contain_text("15.01.2026")
        await expect(_row(page, "Belegnummer")).to_contain_text("RE-2026-0042")
        await expect(_row(page, "Bruttobetrag")).to_contain_text("312,00 €")
        # The category by its label, not by its code; a period as a German date.
        await expect(_row(page, "Kategorievorschlag")).to_contain_text("Gebaeudeversicherung")
        await expect(_row(page, "Kategorievorschlag")).not_to_contain_text("GEBAEUDEVERSICHERUNG")
        await expect(_row(page, "Leistungszeitraum von")).to_contain_text("01.01.2026")
        # The account a person reviews before paying is the one the statement shows the insurer's debit from.
        await expect(_row(page, "IBAN des Rechnungsstellers")).to_contain_text(_printed_iban(iban))
        await _fits_the_width(page)
        await _values_stay_whole(page)
    await page.set_viewport_size(DESKTOP)


@then("the invoice page shows the usage of the reading and of the extraction model")
@browser_step
async def usage_of_both(browser_world):
    page = browser_world.page
    await _capture(browser_world, f"/belege/{browser_world.world.data['invoice']}", "invoice-usage", card="Verbrauch der KI-Verarbeitung")
    for viewport in (DESKTOP, PHONE):
        await page.set_viewport_size(viewport)
        await _open_invoice(browser_world, browser_world.world.data["invoice"])
        card = page.get_by_role("region", name="Verbrauch der KI-Verarbeitung")
        await expect(card).to_be_visible()
        await expect(card).to_contain_text("Dokumente einlesen")
        await expect(card).to_contain_text(READING_MODEL)
        await expect(card).to_contain_text("Informationen extrahieren")
        await expect(card).to_contain_text(EXTRACTION_MODEL)
        await expect(card).to_contain_text("Gesamt")
        await _fits_the_width(page)
    await page.set_viewport_size(DESKTOP)


@then("the invoice page shows the usage of the reading that was already paid")
@browser_step
async def usage_of_reading(browser_world):
    page = browser_world.page
    await _open_invoice(browser_world, browser_world.world.data["invoice"])
    card = page.get_by_role("region", name="Verbrauch der KI-Verarbeitung")
    await expect(card).to_contain_text("Dokumente einlesen")
    await expect(card).to_contain_text(READING_MODEL)
    await expect(card).to_contain_text("Gesamt")


@then("the models received what the invoice prints, including the IBAN of the insurer's bank statement line")
def models_received_the_print(world):
    _name, iban, _amount = _statement_line()
    printed = _printed_iban(iban)
    assert [(read.model, read.document_key, read.pdf_page) for read in world.data["reader"].reads] == [(READING_MODEL, "invoice", 1)]
    (call,) = world.data["extractor"].calls
    assert call.model == EXTRACTION_MODEL and call.pages == (1,)
    # The extraction model is handed the transcript of the page, which is where the vendor's IBAN is read from.
    assert printed in call.text and "312,00 EUR" in call.text and "15.01.2026" in call.text
    # The proposal keeps the text it was made from as its source evidence.
    evidence = _invoice(world)["extraction_payload"]["raw_text"]
    assert printed in evidence and iban == printed.replace(" ", "")


@then("the invoice page explains in German that the extraction failed and the original is kept")
@browser_step
async def failed_explanation(browser_world):
    page = browser_world.page
    await _capture(browser_world, f"/belege/{browser_world.world.data['invoice']}", "invoice-provider-failure")
    for viewport in (DESKTOP, PHONE):
        await page.set_viewport_size(viewport)
        await _open_invoice(browser_world, browser_world.world.data["invoice"])
        await expect(page.get_by_text("Verarbeitung fehlgeschlagen · Original prüfen")).to_be_visible()
        await expect(page.get_by_text("Das Modell unter „Informationen extrahieren“ hat keine Angaben geliefert")).to_be_visible()
        await expect(page.get_by_text("Das Original bleibt erhalten; es wurde nichts übernommen.")).to_be_visible()
        await expect(page.get_by_text("verursacht erneut Kosten")).to_be_visible()
        await expect(page.get_by_role("button", name="Verarbeitung erneut starten", exact=True)).to_be_visible()
        await expect(page.get_by_role("heading", name="Extrahierte Felder")).to_have_count(0)
        await _fits_the_width(page)
    await page.set_viewport_size(DESKTOP)


@then("the invoice page explains in German that the AI evaluation is not set up and nothing was sent")
@browser_step
async def disabled_explanation(browser_world):
    page = browser_world.page
    await _capture(browser_world, f"/belege/{browser_world.world.data['invoice']}", "invoice-ai-not-set-up")
    for viewport in (DESKTOP, PHONE):
        await page.set_viewport_size(viewport)
        await _open_invoice(browser_world, browser_world.world.data["invoice"])
        await expect(page.get_by_text("Automatische Auswertung nicht möglich · Original prüfen")).to_be_visible()
        await expect(page.get_by_text("Die automatische Auswertung per KI ist für Ihre Organisation nicht eingerichtet.")).to_be_visible()
        await expect(page.get_by_text("Es wird nichts (mehr) an einen KI-Anbieter übermittelt.")).to_be_visible()
        await expect(page.get_by_role("region", name="Verbrauch der KI-Verarbeitung")).to_have_count(0)
        await _fits_the_width(page)
    await page.set_viewport_size(DESKTOP)


@then(parsers.parse('both invoice pages propose 312,00 € dated 15.01.2026 from "{vendor}" and the IBAN of the statement\'s insurer line as E-Rechnung'))
@browser_step
async def structured_proposals(browser_world, vendor):
    page = browser_world.page
    _name, iban, _amount = _statement_line()
    for invoice_id in browser_world.world.data["invoices"]:
        await _open_invoice(browser_world, invoice_id)
        await expect(page.get_by_text("Als E-Rechnung erkannt")).to_be_visible()
        await expect(_row(page, "Lieferant")).to_contain_text(vendor)
        await expect(_row(page, "Rechnungsdatum")).to_contain_text("15.01.2026")
        await expect(_row(page, "Bruttobetrag")).to_contain_text("312,00 €")
        await expect(_row(page, "IBAN des Rechnungsstellers")).to_contain_text(_printed_iban(iban))


@then("no provider was built and no invoice page shows a usage")
@browser_step
async def nothing_built_nothing_used(browser_world):
    assert browser_world.world.data["built"] == []
    for invoice_id in browser_world.world.data["invoices"]:
        await _open_invoice(browser_world, invoice_id)
        await expect(browser_world.page.get_by_text("Felder vorbereitet · bitte prüfen")).to_be_visible()
        await expect(browser_world.page.get_by_role("region", name="Verbrauch der KI-Verarbeitung")).to_have_count(0)


@then("nothing is posted and the original can be opened")
def nothing_posted_original_opens(world):
    invoice = _invoice(world)
    assert invoice["cost_entry_id"] is None and invoice["released_at"] is None
    response = world.look(world.actor, f"/api/invoice-documents/{world.data['invoice']}/content")
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
