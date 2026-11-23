"""The inbox path of an acceptance instance, run by the session that launches a blind acceptance (issue #862).

An acceptance instance (scripts/acceptance-env.sh) runs only the API: no worker and no provider. This command stands in for
both on the path of ``/posteingang``, beside ``tests.offline_invoice_processing`` for invoices, whose refusals, model-list stub,
cipher stand-in and cleanup it uses. The models are the fakes of the inbox's journey twin (the building-insurance invoice and
the water bill of features/journeys/inbox_model_processing.feature). Every form refuses, with a message and exit status 1, when
no acceptance instance of ``acceptance-env.sh`` is running; the database forms also when that run does not hold the test
database lock. Test tree only: no product switch, no configuration key.

    fixtures                        the sample documents into the instance's dist/assets, and the page script that hands them
                                    to the form of /posteingang in the tester's browser
    stub start | stub stop          the model list of an OpenAI-compatible provider on 127.0.0.1 (the invoice command's)
    local --organization <name>     the inbox worker's two stages once over the uploads of one organization: the inspection of
                                    each original and the delivery of its documents; the scanner is stood in
    process --organization <name>   the extraction stage once over the documents queued for one organization, with the twin's
            [--fail-extraction]       fake models (the extraction model times out after the reading was paid); one line per attempt
    status --organization <name>    the organization's batches, files and documents with the stage each one has reached
    discard                         the model metering rows and model-read proposals of the run (the invoice command's), before
                                    `acceptance-env.sh stop`

By hand, from the checkout of the PR's commit, with the test database settings:

    cd <repository>/backend && . ../scripts/test-env.sh && uv run --locked --extra dev --extra ops \\
      python -m tests.offline_inbox_processing <form>

The stored API key is neither decrypted for real nor used (the invoice command's stand-in for ``ai_settings_service._cipher``);
the switch, the confirmation, the models, the budget, the claim, the fence, the split decision and the ledger are the product's own.
The local stage runs the product's inspection (type, structure, limits) in this process and takes only the malware scanner from
a stand-in that answers "clean", because the instance's host has no ClamAV signatures: it is no check of the scanner.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import sys
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from unittest.mock import patch

import psycopg2
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.core import intake_inspection
from app.core.db import get_worker_session_factory
from app.core.storage import LocalFsDocumentStore, set_store
from app.declarations.fake_provider import InvoiceExtractionRecord, ReadingRecord, synthetic_invoice_output
from app.declarations.invoice_pipeline import InvoiceProviders
from app.services import ai_settings_service
from app.workers import intake_extraction, outbox, workflow
from tests import offline_invoice_processing as invoice
from tests.bdd.steps.browser_invoice import _extraction_failure
from tests.bdd.steps.inbox_model_processing import INSURANCE, WATER_BILL, WATER_PROPOSAL, Extractor, Reader
from tests.bdd.steps.invoice_processing import EXTRACTION_USAGE, READING_USAGE
from tests.offline_declaration_import import offline_worker_engine
from tests.offline_invoice_processing import Refusal
from tests.test_m5_einvoice import UBL_WITH_IBAN

SCAN, SECOND_SCAN = "eingang-scan.pdf", "eingang-scan-b.pdf"
FOUR_PAGES, XRECHNUNG = "eingang-vier-seiten.pdf", "eingang-e-rechnung.xml"
FIXTURE_NOTES = {
    SCAN: "building-insurance invoice, a scan of one page, no text layer: both models are asked",
    SECOND_SCAN: "the same scan with one pixel changed (a copy would be refused as a duplicate), for a second upload",
    FOUR_PAGES: "pages 1-2 the insurance invoice with a text layer, pages 3-4 a water bill as pictures: waits for its division",
    XRECHNUNG: "XRechnung (UBL), read directly, no model",
}

# What the samples print and what the fake models answer for them: the insurance invoice stands on page 1 (a scan, or the text
# layer of the four-page upload), the water bill on pages 3 and 4; the extraction model answers by the first page it is given.
INVOICE_LINES = [*INSURANCE, "Rechnungsdatum 15.01.2026"]
SECOND_PAGE = ["Mustermann Versicherungen AG, Seite 2", "Zahlbar bis 28.02.2026 auf das angegebene Konto"]
INVOICE_PROPOSAL = synthetic_invoice_output(
    vendor_name=("Mustermann Versicherungen AG", 0.95, 1, INSURANCE[0]),
    document_number=("RE-2026-0042", 0.9, 1, INSURANCE[1]),
    gross_amount=("312.00", 0.98, 1, "Gesamtbetrag 312,00 EUR"),
    invoice_date=("2026-01-15", 0.97, 1, "Rechnungsdatum 15.01.2026"))
READ_PAGES = {1: INVOICE_LINES, 3: WATER_BILL[:1], 4: WATER_BILL[1:]}

# Pasted into the tester's browser on /posteingang with one call appended, in one evaluation (functions declared next to a
# top-level await do not survive into the next one). It reads no page content; the tester clicks "Stapel einreichen".
PAGE_SCRIPT = """\
async function handIn(...names) { // puts the named sample documents into the form of /posteingang
  const form = document.querySelector('section[aria-label="Neuer Eingang"]');
  const input = form && form.querySelector('input[type="file"]');
  if (!input) throw new Error("hand-in: no upload form on this page; open /posteingang as an administrator or clerk and call again");
  const types = { pdf: "application/pdf", xml: "application/xml" };
  const transfer = new DataTransfer();
  for (const name of names) {
    const answer = await fetch("/assets/" + name);
    if (!answer.ok) throw new Error(`hand-in: /assets/${name} answered ${answer.status}`);
    transfer.items.add(new File([await answer.blob()], name, { type: types[name.split(".").pop()] || "" }));
  }
  input.files = transfer.files;
  input.dispatchEvent(new Event("change", { bubbles: true }));
  return Array.from(input.files, (file) => `${file.name} ${file.type} ${file.size}`);
}"""


# ---- the sample documents -----------------------------------------------------------------------------------------------

def four_page_upload() -> bytes:
    """Pages 1 and 2 print the insurance invoice as text; pages 3 and 4 hold the water bill as pictures, without a text layer."""
    out = io.BytesIO()
    sheet = canvas.Canvas(out)
    for lines in (INVOICE_LINES, SECOND_PAGE):
        for row, line in enumerate(lines):
            sheet.drawString(60, 750 - 40 * row, line)
        sheet.showPage()
    for lines in (WATER_BILL[:1], WATER_BILL[1:]):
        sheet.drawImage(ImageReader(invoice.printed_image(lines)), 0, 0, width=595, height=842)
        sheet.showPage()
    sheet.save()
    return out.getvalue()


def fixture_documents() -> dict[str, bytes]:
    return {SCAN: invoice.scan_pdf(INVOICE_LINES), SECOND_SCAN: invoice.scan_pdf(INVOICE_LINES, 1), FOUR_PAGES: four_page_upload(),
            XRECHNUNG: UBL_WITH_IBAN}


# ---- the database (read only; the run holds the test database lock) -----------------------------------------------------

def _rows(sync_url: str, sql: str, *parameters) -> list[tuple]:
    with closing(psycopg2.connect(sync_url)) as connection, connection, connection.cursor() as cursor:
        cursor.execute(sql, parameters)
        return cursor.fetchall()


FINISHED = """
SELECT a.id, d.id, d.page_start, d.page_end, a.attempt_number, a.result->>'status', a.result->>'error_code'
FROM public.intake_extraction_attempt a
JOIN public.intake_extraction_job j ON j.organization_id = a.organization_id AND j.id = a.job_id
JOIN public.logical_document d ON d.organization_id = j.organization_id AND d.id = j.logical_document_id
WHERE a.organization_id = %s AND a.finished_at IS NOT NULL ORDER BY a.finished_at, a.id"""

WAITING = "SELECT count(*) FROM public.intake_extraction_job WHERE organization_id = %s AND status IN ('queued', 'running')"

LISTING = """
SELECT b.id, i.filename, w.status, w.error_code, d.id, d.page_start, d.page_end, j.status, j.error_code, j.attempt_number
FROM public.intake_batch b
JOIN public.intake_item i ON i.organization_id = b.organization_id AND i.batch_id = b.id
LEFT JOIN public.intake_original o ON o.organization_id = i.organization_id AND o.item_id = i.id
LEFT JOIN public.workflow_job w ON w.organization_id = o.organization_id AND w.original_id = o.id
LEFT JOIN public.document_ref r ON r.organization_id = o.organization_id AND r.intake_original_id = o.id
LEFT JOIN public.logical_document d ON d.organization_id = r.organization_id AND d.document_ref_id = r.id
LEFT JOIN public.intake_extraction_job j ON j.organization_id = d.organization_id AND j.logical_document_id = d.id
WHERE b.organization_id = %s ORDER BY b.created_at, b.id, i.position, d.page_start NULLS FIRST, d.page_end"""


def _pages(start: int | None, end: int | None) -> str:
    return "whole document" if start is None else f"pages {start}-{end}"


def _state(status: str | None, code: str | None) -> str:
    return (status or "none") + (f" ({code})" if code and code != status else "")


def listing(sync_url: str, organization_id: uuid.UUID) -> list[str]:
    """The organization's batches, their files and the documents of each file with the stage each has reached."""
    rows = _rows(sync_url, LISTING, str(organization_id))
    if not rows:
        raise Refusal("no inbox batch of this organization: has the tester submitted one on /posteingang?")
    lines: list[str] = []
    batch = filename = None
    for batch_id, name, inspection, inspection_code, document, start, end, extraction, extraction_code, attempts in rows:
        if batch_id != batch:
            batch, filename = batch_id, None
            lines.append(f"batch {batch_id}")
        if (batch_id, name) != filename:
            filename = (batch_id, name)
            lines.append(f"  {name}: inspection {_state(inspection or 'not uploaded', inspection_code)}")
        if document is not None:
            stage = f"extraction {_state(extraction, extraction_code)}, attempts {attempts}" if extraction else "no extraction job"
            lines.append(f"    document {document} ({_pages(start, end)}): {stage}")
    return lines


# ---- the stages --------------------------------------------------------------------------------------------------------

async def _drain(step) -> int:
    """Run a stage until it claims nothing (bounded: a stage that never idles is a defect, not a queue)."""
    done = 0
    while done < invoice.MAX_ATTEMPTS and await step():
        done += 1
    return done


async def process_local(organization_id: uuid.UUID) -> tuple[int, int]:
    """The inbox worker's inspection and delivery over the organization's uploads; how many originals each handled.

    The product's inspection runs here, in this process, with the malware scanner answering "clean": the instance's host has
    no ClamAV signatures, and the isolated child of the real worker would look for the scanner there.
    """
    factory = get_worker_session_factory()

    async def inspect():
        return await workflow.process_once(factory, organization_id, inspector=intake_inspection.inspect_document)

    async def deliver():
        return await outbox.process_once(factory, organization_id)

    inspected = delivered = 0
    with patch.object(intake_inspection, "scan_malware", lambda _data: "clean"):
        for _ in range(invoice.MAX_ATTEMPTS):
            first, second = await _drain(inspect), await _drain(deliver)
            inspected, delivered = inspected + first, delivered + second
            if not (first or second):
                break
    return inspected, delivered


@dataclass
class AttemptRecord:
    """One attempt the extraction stage finished and what the fake models were asked meanwhile."""

    document_id: uuid.UUID
    pages: str
    attempt_no: int
    status: str
    error_code: str | None
    reads: list[ReadingRecord] = field(default_factory=list)
    calls: list[InvoiceExtractionRecord] = field(default_factory=list)

    def line(self) -> str:
        return (f"document {self.document_id} ({self.pages}) attempt {self.attempt_no}: {_state(self.status, self.error_code)}; "
                f"{invoice.models_asked(self.reads, self.calls)}")


def _providers(fail_extraction: bool):
    """The provider factory the worker is given where it would build the real ones: one fake pair per attempt that reaches the
    models, and the list of those pairs, so that the record of an attempt shows which model was asked for which page."""
    built: list[tuple[Reader, Extractor]] = []

    def providers(effective):
        reader = Reader(READ_PAGES, usage=READING_USAGE)
        options = {"failure": _extraction_failure()} if fail_extraction else {"usage": EXTRACTION_USAGE}
        extractor = Extractor({1: INVOICE_PROPOSAL, 3: WATER_PROPOSAL}, **options)
        built.append((reader, extractor))
        return InvoiceProviders(reader=reader if effective.document_model else None, extractor=extractor)

    return providers, built


async def process_waiting(organization_id: uuid.UUID, sync_url: str, *, fail_extraction: bool = False) -> list[AttemptRecord]:
    """Run the extraction stage over the organization's queue, oldest waiting document first, until nothing is claimed.

    The stage is the real ``process_once``, with its local parse in the isolated child; only the providers it would build are
    the twin's fakes. A multipage original that nobody has divided is parked by the stage itself and nothing is sent.
    """
    if not _rows(sync_url, WAITING, str(organization_id))[0][0]:
        raise Refusal("nothing is waiting: no document of this organization is queued for its extraction (has the tester submitted "
                      "a batch and has `local` run? a multipage original is queued again once its division is confirmed)")
    providers, built = _providers(fail_extraction)
    seen = {row[0] for row in _rows(sync_url, FINISHED, str(organization_id))}
    records: list[AttemptRecord] = []
    with patch.object(ai_settings_service, "_cipher", lambda _organization_id: invoice._StandInCipher()):
        for _ in range(invoice.MAX_ATTEMPTS):
            used = len(built)
            if not await intake_extraction.process_once(get_worker_session_factory(), organization_id, providers=providers):
                break
            finished = _rows(sync_url, FINISHED, str(organization_id))
            new = [AttemptRecord(row[1], _pages(row[2], row[3]), *row[4:]) for row in finished if row[0] not in seen]
            seen.update(row[0] for row in finished)
            records.extend(new)
            # The models of this attempt: the providers built while it ran (none for a parked or refused one).
            if new and len(built) > used:
                records[-1].reads, records[-1].calls = built[-1][0].reads, built[-1][1].calls
    return records


async def _run_stage(organization_id: uuid.UUID, sync_url: str, form: str, fail_extraction: bool):
    async with offline_worker_engine():
        if form == "local":
            return await process_local(organization_id)
        records = await process_waiting(organization_id, sync_url, fail_extraction=fail_extraction)
        return records, _rows(sync_url, WAITING, str(organization_id))[0][0]


# ---- the command --------------------------------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tests.offline_inbox_processing", description=__doc__.split("\n\n")[0])
    forms = parser.add_subparsers(dest="form", required=True)
    forms.add_parser("fixtures", help="write the sample documents into the instance's dist/assets and print the page script")
    stub = forms.add_parser("stub", help="start or stop the model-list stub on 127.0.0.1")
    stub.add_argument("action", choices=("start", "stop"))
    local = forms.add_parser("local", help="inspect and deliver the uploads of one organization")
    local.add_argument("--organization", required=True, help="name or id of the organization")
    process = forms.add_parser("process", help="run the extraction stage once over the documents queued for one organization")
    process.add_argument("--organization", required=True, help="name or id of the organization")
    process.add_argument("--fail-extraction", action="store_true", help="the extraction model times out after the reading was paid")
    status = forms.add_parser("status", help="list the batches, files and documents of one organization with their stage")
    status.add_argument("--organization", required=True, help="name or id of the organization")
    forms.add_parser("discard", help="discard the model metering rows and model-read proposals of the test database")
    return parser


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" + ("" if number == 1 else "s")


def _run(arguments: argparse.Namespace) -> int:
    if arguments.form in ("stub", "discard"):
        return invoice._run(arguments)
    if arguments.form == "fixtures":
        instance = invoice.running_instance()
        written = invoice.write_documents(instance, fixture_documents())
        print(f"Wrote {len(written)} documents into {instance.scratch / 'dist' / 'assets'}; the tester fetches them from the instance:")
        print("\n".join(f"  /assets/{name}  {FIXTURE_NOTES.get(name, '')}".rstrip() for name in written))
        print("\nPage script for the tester's browser on /posteingang: paste it with one call appended, in one evaluation, then the "
              "tester clicks „Stapel einreichen“ (one call per submission; several names in one call make one batch):\n")
        print(PAGE_SCRIPT)
        print("\n" + "\n".join(f'await handIn("{name}")' for name in written))
        return 0
    instance = invoice.running_instance(database=True)
    sync_url = invoice._require_test_database()
    organization_id = invoice._organization(arguments.organization)
    if arguments.form == "status":
        print("\n".join(listing(sync_url, organization_id)))
        return 0
    set_store(LocalFsDocumentStore(str(instance.scratch / "documents")))
    try:
        outcome = asyncio.run(_run_stage(organization_id, sync_url, arguments.form, getattr(arguments, "fail_extraction", False)))
    finally:
        set_store(None)
    if arguments.form == "local":
        inspected, delivered = outcome
        if not (inspected or delivered):
            raise Refusal("nothing is waiting: no upload of this organization awaits its inspection or its delivery (has the tester "
                          "submitted a batch on /posteingang?)")
        print(f"inspection: {_count(inspected, 'original')} (the malware scanner is stood in: every file counts as clean)")
        print(f"delivery: {_count(delivered, 'original')}")
        return 0
    records, waiting = outcome
    print("\n".join(record.line() for record in records) or "no attempt was claimed")
    if waiting:
        print(f"still waiting: {waiting}")
    return 0 if records else 1


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        return _run(arguments)
    except Refusal as refusal:
        print(f"offline_inbox_processing: {refusal}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
