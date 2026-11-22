"""The inbox of the portable stack through the browser: documents read by the organization's models (issue #501).

The original is uploaded and the worker ticks run in this process against the same database and document directory as the
stack, with the fake providers of ``inbox_model_processing`` standing in for the two models; the browser reads what the user
sees. The expected values come from the sample documents (the lines of the invoice and of the water bill, and the page of
the scan each stands on), not from what the fake providers answer.
"""
import os
import re
from pathlib import Path

from playwright.async_api import expect
from pytest_bdd import given, parsers, then, when

from tests.ai_settings_support import configure_ai
from tests.bdd.steps.browser_common import browser_step
from tests.bdd.steps.browser_invoice import DESKTOP, PHONE, READING_MODEL, EXTRACTION_MODEL, _fits_the_width
from tests.bdd.steps.inbox_model_processing import (
    INSURANCE, INSURANCE_PROPOSAL, WATER_BILL, Extractor, _retry)
from tests.bdd.steps.invoice_processing import EXTRACTION_USAGE, _organization

ROOT = Path(__file__).resolve().parents[4]
# What the scan says and where: page 1 and 2 of the insurance invoice, pages 3 and 4 of the water bill.
PAGE_OF = {INSURANCE[0]: 1, INSURANCE[1]: 1, INSURANCE[2]: 1, WATER_BILL[0]: 3, WATER_BILL[1]: 4}
UNAVAILABLE = "Auswertung nicht möglich"


async def _shoot(browser_world, name, prepare, *, element=None, until=None):
    """With ``VERWALINO_CAPTURE_DESIGN=1``: the screen at both widths, in ``docs/assets/design/inbox-processing``.

    ``VERWALINO_CAPTURE_STATE`` names the state (``after`` unless a run on the previous frontend says ``before``).
    ``prepare`` brings the page to the state to show and runs before every picture; ``element`` (a function of the page)
    limits the picture to one element of it, shown in a window tall enough to hold it, down to the end of ``until``
    (another function of the page) when the element goes on with panels the picture does not need. A previous frontend
    that has no such element is captured without it.
    """
    if os.environ.get("VERWALINO_CAPTURE_DESIGN") != "1":
        return
    page = browser_world.page
    directory = ROOT / "docs/assets/design/inbox-processing"
    directory.mkdir(parents=True, exist_ok=True)
    state = os.environ.get("VERWALINO_CAPTURE_STATE", "after")
    for viewport in (DESKTOP, PHONE):
        # The app scrolls inside its window: the window is made as tall as what is shown.
        await page.set_viewport_size({**viewport, "height": 4000 if element else 2600 if viewport is PHONE else 1100})
        await prepare()
        await page.wait_for_load_state("networkidle")
        file = str(directory / f"{name}-{state}-{viewport['width']}.png")
        if element is None:
            await page.screenshot(path=file, full_page=True)
        elif await element(page).count():
            if until is None:
                await element(page).first.screenshot(path=file)
                continue
            await page.evaluate("window.scrollTo(0, 0)")
            top, end = await element(page).first.bounding_box(), await until(page).last.bounding_box()
            await page.screenshot(path=file, full_page=True, clip={"x": top["x"], "y": top["y"], "width": top["width"], "height": end["y"] + end["height"] - top["y"]})
    await page.set_viewport_size(DESKTOP)


def _proposals(page):
    return page.get_by_role("region", name="Extraktionsvorschläge")


def _attempts(page):
    return _proposals(page).locator("details")


def _ai_card(page):
    return page.locator("section").filter(has=page.get_by_role("heading", name="KI für Ihre Organisation", exact=True))


async def _open_batch(browser_world):
    await browser_world.visit(f"/posteingang/{browser_world.world.data['batch']['id']}")
    await expect(browser_world.page.get_by_role("heading", name="Dokumentenstapel")).to_be_visible()
    await expect(browser_world.page.get_by_role("status")).to_have_count(0)


async def _open_source(browser_world, first=None, last=None):
    """The source page of the original, with the document of these pages selected (the only one when none is named)."""
    page = browser_world.page
    await browser_world.visit(f"/posteingang/belege/{browser_world.world.data['ref']['id']}")
    await expect(page.get_by_role("heading", name="Belege und Vorschläge")).to_be_visible()
    if first is not None:
        option = page.locator("#logical-source option").filter(has_text=f"Seiten {first}–{last}")
        await page.locator("#logical-source").select_option(await option.get_attribute("value"))
    await expect(page.get_by_text("Lade Verarbeitungsstand und Fundstellen…")).to_have_count(0)
    return page.get_by_role("region", name="Extraktionsvorschläge")


def _field(section, label):
    """The article of one proposed value, by its label."""
    return section.locator("article").filter(has=section.page.get_by_role("heading", name=label, exact=True))


async def _value_on_its_page(section, label, value, line, *, read_by, in_quote):
    """A proposed value with the page and the passage of the sample that it stands on: the passage is a part of the line
    the sample prints on that page and holds the value as the document prints it."""
    field = _field(section, label)
    await expect(field).to_contain_text(value)
    await expect(field).to_contain_text(f"Fundstelle auf Seite {PAGE_OF[line]} · {read_by}")
    quote = (await field.locator("blockquote").inner_text()).strip()
    assert quote in line and in_quote in quote, (label, quote, line)


@given("the organization enabled the AI import and confirmed the provider with the first text, which names declarations only")
def confirmed_first_text(world, invoice_workspace):
    # What an organization holds that confirmed before invoices and the inbox were covered. Whether this stack asks again
    # for the inbox, and for nothing else, is read off its own settings card in the step that confirms.
    configure_ai(_organization(world), text_version="1", import_enabled=True)


WAITS = "Das Original hat mehr als eine Seite und wird erst ausgewertet, wenn Sie bestätigt haben, wie es aufgeteilt ist"
NOTHING_SENT = "Bis dahin wurde nichts an einen KI-Anbieter übermittelt"


@then("the batch page and the source page say in German that the upload waits for the division and that nothing was sent at 1280 and 360 pixels")
@browser_step
async def waits_for_the_division(browser_world):
    page = browser_world.page
    await _shoot(browser_world, "batch-waits", lambda: _open_batch(browser_world), element=lambda page: page.locator("main"), until=lambda page: page.locator("main article"))
    await _shoot(browser_world, "source-waits", lambda: _open_source(browser_world), element=lambda page: page.get_by_role("region", name="Geprüfte Seitenaufteilung"),
                 until=lambda page: _proposals(page).get_by_text("Es liegt noch kein Extraktionsergebnis vor."))
    for viewport in (DESKTOP, PHONE):
        await page.set_viewport_size(viewport)
        await _open_batch(browser_world)
        item = page.locator("main article")
        await expect(item.locator(".badge")).to_have_text("Zur Sichtung")
        await expect(item).to_contain_text("Aufteilung ausstehend · 1 Beleg(e), 0 zur Sichtung")
        await expect(item).to_contain_text(WAITS)
        await expect(item).to_contain_text(NOTHING_SENT)
        await expect(item).to_contain_text("Das Original bleibt erhalten; es wurde nichts übernommen.")
        counts = page.get_by_role("region", name="Verarbeitungsstand")
        await expect(counts.locator(".card").filter(has_text="Zur Sichtung")).to_have_text(re.compile(r"^1\s*Zur Sichtung$"))
        await expect(counts.locator(".card").filter(has_text="Gesperrt / fehlgeschlagen")).to_have_text(re.compile(r"^0\s*Gesperrt / fehlgeschlagen$"))
        await _fits_the_width(page)
        section = await _open_source(browser_world)
        # Nothing was requested, so none of the three attempts is used and the status line names none.
        await expect(page.get_by_text("Wartet auf die Aufteilung", exact=True)).to_be_visible()
        await expect(page.get_by_text(re.compile(r"Versuch \d"))).to_have_count(0)
        await expect(section).to_contain_text(WAITS)
        await expect(section).to_contain_text(NOTHING_SENT)
        # Nothing to press: waiting is no failure, and the place to act is on the page.
        await expect(section.get_by_role("button", name="Auswertung erneut anfordern", exact=True)).to_have_count(0)
        await expect(section.get_by_role("region", name="Verbrauch der KI-Verarbeitung")).to_have_count(0)
        await expect(page.get_by_role("heading", name="Seiten zu Belegen zusammenstellen")).to_be_visible()
        await expect(page.get_by_text("Ein Original mit mehreren Seiten wird erst an einen KI-Anbieter übermittelt, wenn Sie die Aufteilung gespeichert haben")).to_be_visible()
        await _fits_the_width(page)
    await page.set_viewport_size(DESKTOP)


@when(parsers.parse("the reviewer divides the original on the source page into pages {a:d} to {b:d} and pages {c:d} to {d:d} and confirms the division"))
@browser_step
async def divide_and_confirm(browser_world, a, b, c, d):
    """What the user does: marks the two documents, ticks both in the division, gives the reason and saves it."""
    page = browser_world.page
    await _open_source(browser_world)
    ranges = page.locator("details").filter(has_text="Weiteren Seitenbereich anlegen")
    for first, last in ((a, b), (c, d)):
        if not await ranges.evaluate("details => details.open"):
            await ranges.locator("summary").click()
        await ranges.get_by_label("Erste Seite").fill(str(first))
        await ranges.get_by_label("Letzte Seite").fill(str(last))
        await ranges.get_by_label("Grund für den Seitenbereich").fill("Zwei Belege in einem Scan")
        await ranges.get_by_role("button", name="Seitenbereich anlegen", exact=True).click()
        await expect(page.get_by_label(f"Seiten {first}–{last} in Aufteilung aufnehmen")).to_be_visible()
    for first, last in ((a, b), (c, d)):
        await page.get_by_label(f"Seiten {first}–{last} in Aufteilung aufnehmen").check()
    await expect(page.get_by_text("Jede sichtbare Seite ist genau einmal enthalten.")).to_be_visible()
    await page.get_by_label("Begründung der Aufteilung").fill("Zwei Belege, Grenzen am Original geprüft")
    await page.get_by_label("Alle sichtbaren Seiten im Original geprüft und die Beleggrenzen bewusst gewählt.").check()
    await page.get_by_role("button", name="Aufteilung speichern", exact=True).click()
    await expect(page.get_by_text("Aufteilung als Version 1 gespeichert.")).to_be_visible()
    # The whole is no part of the division the user saved, and the page says so for it once the worker has looked.


@then(parsers.parse('the source page proposes {amount} EUR with the number {number} from "{vendor}" for pages {first:d} to {last:d} at 1280 and 360 pixels'))
@browser_step
async def proposal_of_the_invoice(browser_world, amount, number, vendor, first, last):
    page = browser_world.page
    # The picture of the screen comes first, so that a previous frontend is captured before its assertions fail. Only the
    # scenario that marked the documents shows the insurance invoice among the others.
    if browser_world.world.data.get("documents"):
        await _shoot(browser_world, "source-proposal", lambda: _open_source(browser_world, first, last), element=_proposals, until=_attempts)
    for viewport in (DESKTOP, PHONE):
        await page.set_viewport_size(viewport)
        section = await _open_source(browser_world, first, last)
        await expect(page.get_by_text("Vorschläge verfügbar · Versuch")).to_be_visible()
        await _value_on_its_page(section, "Aussteller", vendor, INSURANCE[0], read_by="PDF-Text", in_quote=vendor)
        await _value_on_its_page(section, "Belegnummer", number, INSURANCE[1], read_by="PDF-Text", in_quote=number)
        # The value is shown in the money format of the project (not as the ISO amount it is stored as); the passage is the
        # document's own print of it.
        await _value_on_its_page(section, "Bruttobetrag", f"{amount} €", INSURANCE[2], read_by="PDF-Text", in_quote=f"{amount} EUR")
        await expect(_field(section, "Rechnungsdatum")).to_contain_text("15.01.2026")
        await expect(section).not_to_contain_text("2026-01-15")
        await _fits_the_width(page)
    await page.set_viewport_size(DESKTOP)


@then(parsers.parse('the source page proposes {amount} EUR from "{vendor}" for pages {first:d} to {last:d}, read by the reading model, at 1280 and 360 pixels'))
@browser_step
async def proposal_of_the_water_bill(browser_world, amount, vendor, first, last):
    page = browser_world.page
    # The scanned water bill, read by the model, with its passages and its usage.
    await _shoot(browser_world, "source-scan", lambda: _open_source(browser_world, first, last), element=_proposals, until=_attempts)
    for viewport in (DESKTOP, PHONE):
        await page.set_viewport_size(viewport)
        section = await _open_source(browser_world, first, last)
        # Pages 3 and 4 are scans: the reading model read them, and the passages are theirs.
        await _value_on_its_page(section, "Aussteller", vendor, WATER_BILL[0], read_by="vom Lesemodell gelesen", in_quote=vendor)
        await _value_on_its_page(section, "Bruttobetrag", f"{amount} €", WATER_BILL[1], read_by="vom Lesemodell gelesen", in_quote=f"{amount} EUR")
        # The water bill's date has no passage: it is shown in the date format and as unlocated.
        await expect(_field(section, "Rechnungsdatum")).to_contain_text("03.02.2026")
        await expect(_field(section, "Rechnungsdatum")).to_contain_text("Keine Fundstelle bestätigt")
        await expect(section).not_to_contain_text("99.00")
        await expect(section).not_to_contain_text(INSURANCE[0])
        await _fits_the_width(page)
    await page.set_viewport_size(DESKTOP)


async def _usage(browser_world, section, *, stages):
    card = section.get_by_role("region", name="Verbrauch der KI-Verarbeitung")
    await expect(card).to_be_visible()
    for stage, model in stages:
        await expect(card).to_contain_text(stage)
        await expect(card).to_contain_text(model)
    await expect(card).to_contain_text("Gesamt")
    await _fits_the_width(browser_world.page)


@then(parsers.parse("the source page of pages {first:d} to {last:d} shows the usage of the reading and of the extraction model at 1280 and 360 pixels"))
@browser_step
async def usage_of_both(browser_world, first, last):
    for viewport in (DESKTOP, PHONE):
        await browser_world.page.set_viewport_size(viewport)
        section = await _open_source(browser_world, first, last)
        await _usage(browser_world, section, stages=[("Dokumente einlesen", READING_MODEL), ("Informationen extrahieren", EXTRACTION_MODEL)])
    await browser_world.page.set_viewport_size(DESKTOP)


@then("the source page shows the values of the e-invoice read directly and no usage at 1280 and 360 pixels")
@browser_step
async def structured_values(browser_world):
    page = browser_world.page
    for viewport in (DESKTOP, PHONE):
        await page.set_viewport_size(viewport)
        section = await _open_source(browser_world)
        await expect(_field(section, "Bruttobetrag")).to_contain_text("Fundstelle im XML")
        await expect(section.get_by_role("region", name="Verbrauch der KI-Verarbeitung")).to_have_count(0)
        await _fits_the_width(page)
    await page.set_viewport_size(DESKTOP)


@then("the batch page explains in German that the organization has to confirm again and that the original is kept at 1280 and 360 pixels")
@browser_step
async def batch_explains_confirmation(browser_world):
    page = browser_world.page
    await _shoot(browser_world, "batch-explained", lambda: _open_batch(browser_world), element=lambda page: page.locator("main"), until=lambda page: page.locator("main article"))
    await _shoot(browser_world, "settings-card-confirmation", lambda: browser_world.visit("/einstellungen"), element=_ai_card)
    for viewport in (DESKTOP, PHONE):
        await page.set_viewport_size(viewport)
        await _open_batch(browser_world)
        await expect(page.get_by_text("Ihre Organisation hat noch nicht bestätigt, dass Dokumente im Posteingang an den KI-Anbieter übermittelt werden dürfen.")).to_be_visible()
        await expect(page.get_by_text("Es wird nichts (mehr) an einen KI-Anbieter übermittelt.")).to_be_visible()
        await expect(page.get_by_text("Ihre Administration kann das")).to_contain_text("ohne den Schlüssel erneut einzugeben")
        # The file waits for the organization: it is counted as received, not as blocked or failed.
        counts = page.get_by_role("region", name="Verarbeitungsstand")
        await expect(counts.locator(".card").filter(has_text="Empfangen")).to_have_text(re.compile(r"^1\s*Empfangen$"))
        await expect(counts.locator(".card").filter(has_text="Gesperrt / fehlgeschlagen")).to_have_text(re.compile(r"^0\s*Gesperrt / fehlgeschlagen$"))
        await expect(page.locator("main article .badge")).to_have_text("Empfangen")
        await _fits_the_width(page)
    await page.set_viewport_size(DESKTOP)


@when("the administrator confirms the text for the inbox in the AI settings at 1280 and 360 pixels")
@browser_step
async def confirm_in_the_card(browser_world):
    page = browser_world.page

    async def card():
        await browser_world.visit("/einstellungen")
        await expect(page.get_by_text("galt, bevor Dokumente im Posteingang ausgewertet wurden")).to_be_visible()

    for viewport in (DESKTOP, PHONE):
        await page.set_viewport_size(viewport)
        await card()
        await expect(page.get_by_text("Teilungserklärungen sind davon nicht betroffen.")).to_be_visible()
        # No invoice is named: this stack processes none.
        await expect(_ai_card(page)).not_to_contain_text(re.compile(r"(?<!E-)(?<!X)Rechnung"))
        await _fits_the_width(page)
    await page.set_viewport_size(DESKTOP)
    await card()
    await page.get_by_label(re.compile("Wir haben den Anbieter selbst gewählt")).check()
    async with page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/ai-settings")) as saved:
        await page.get_by_role("button", name="KI-Einstellungen speichern", exact=True).click()
    response = await saved.value
    assert response.status == 200 and "api_key" not in (response.request.post_data or ""), "the key is not entered again"
    await expect(page.get_by_text("KI-Einstellungen gespeichert.")).to_be_visible()
    await expect(page.get_by_text(re.compile(r"Bestätigt am .* von"))).to_be_visible()


@when("the administrator asks for the processing once more")
def ask_again(world, monkeypatch):
    _retry(world, monkeypatch, Extractor({1: INSURANCE_PROPOSAL}, usage=EXTRACTION_USAGE))


@then("the source page explains in German that the AI evaluation is not set up, that the original is kept and that nothing was applied at 1280 and 360 pixels")
@browser_step
async def not_set_up(browser_world):
    page = browser_world.page
    for viewport in (DESKTOP, PHONE):
        await page.set_viewport_size(viewport)
        section = await _open_source(browser_world)
        # Refused before any request: none of the three attempts is used and the status line names none.
        await expect(page.get_by_text(UNAVAILABLE, exact=True)).to_be_visible()
        await expect(page.get_by_text(re.compile(r"Versuch \d"))).to_have_count(0)
        await expect(section).to_contain_text("Das Original bleibt erhalten; es wurde nichts übernommen.")
        await expect(section).to_contain_text("Die automatische Auswertung per KI ist für Ihre Organisation nicht eingerichtet. Es wird nichts (mehr) an einen KI-Anbieter übermittelt.")
        # The established workflow stays open: a new attempt can be asked for once the cause is removed.
        await expect(section.get_by_role("button", name="Auswertung erneut anfordern", exact=True)).to_be_visible()
        await expect(section.get_by_role("region", name="Verbrauch der KI-Verarbeitung")).to_have_count(0)
        await expect(section.locator("article")).to_have_count(0)
        await _fits_the_width(page)
    await page.set_viewport_size(DESKTOP)


@then("the source page explains in German that the extraction failed, that the original is kept and that a new attempt can be requested at 1280 and 360 pixels")
@browser_step
async def extraction_failed(browser_world):
    page = browser_world.page
    for viewport in (DESKTOP, PHONE):
        await page.set_viewport_size(viewport)
        section = await _open_source(browser_world)
        await expect(page.get_by_text("Verarbeitung fehlgeschlagen · Versuch")).to_be_visible()
        await expect(section).to_contain_text("Das Modell unter „Informationen extrahieren“ hat keine Angaben geliefert")
        await expect(section).to_contain_text("Das Original bleibt erhalten; es wurde nichts übernommen.")
        await expect(section.get_by_role("button", name="Auswertung erneut anfordern", exact=True)).to_be_visible()
        await expect(section.locator("article")).to_have_count(0)
        await _fits_the_width(page)
    await page.set_viewport_size(DESKTOP)


@then("the source page shows the usage of the reading and of the failed extraction at 1280 and 360 pixels")
@browser_step
async def usage_of_the_failure(browser_world):
    for viewport in (DESKTOP, PHONE):
        await browser_world.page.set_viewport_size(viewport)
        section = await _open_source(browser_world)
        await _usage(browser_world, section, stages=[("Dokumente einlesen", READING_MODEL), ("Informationen extrahieren", EXTRACTION_MODEL)])
    await browser_world.page.set_viewport_size(DESKTOP)
