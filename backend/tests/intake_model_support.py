"""Test support for the inbox's model path (issue #501, ADR 0017).

The in-process fake providers, an organization that has opted in with both models and a runner for the queue. A test
that builds the providers makes metering records and model-read proposals, which the 0198 downgrade refuses to discard
and the chain-wide downgrade tests meet on a database that earlier tests have used: ``dirty`` tells the autouse fixture of
``conftest.py`` to remove them after the test.
"""
import io
import subprocess
import sys
import types
import uuid

import pytest
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy import select

from app.core.db import get_worker_session_factory, get_worker_tenant_session
from app.declarations.fake_provider import FakeDocumentReadingProvider, FakeInvoiceExtractionProvider
from app.declarations.invoice_pipeline import InvoiceProviders
from app.models.invoice_model_call import InvoiceModelCall
from app.workers.intake_extraction import process_once
from tests.ai_settings_support import configure_ai

EXTRACTION_MODEL, READING_MODEL = "gpt-5.6-luna", "gpt-5.4-mini"

# Set when a test built the providers of a run: it made data that has to be discarded afterwards (see ``conftest.py``).
dirty = False


class Harness:
    """The provider factory the worker is given: it records every construction and serves the two fakes."""

    def __init__(self, reader=None, extractor=None):
        self.reader = reader or FakeDocumentReadingProvider()
        self.extractor = extractor or FakeInvoiceExtractionProvider()
        self.built = []

    def __call__(self, effective):
        global dirty
        dirty = True
        self.built.append(effective)
        return InvoiceProviders(reader=self.reader if effective.document_model else None, extractor=self.extractor)


@pytest.fixture
def tesseract_unusable(monkeypatch):
    """Any pytesseract use or tesseract process raises: the inbox path must not need either."""
    blocked = types.ModuleType("pytesseract")

    def refuse(name):
        raise AssertionError(f"pytesseract.{name} was used")

    blocked.__getattr__ = refuse
    monkeypatch.setitem(sys.modules, "pytesseract", blocked)
    real_popen = subprocess.Popen

    class GuardedPopen(real_popen):
        def __init__(self, args, *rest, **options):
            argv = args if isinstance(args, (list, tuple)) else [args]
            if any("tesseract" in str(part).lower() for part in argv):
                raise AssertionError("a tesseract process was started")
            super().__init__(args, *rest, **options)

    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)


def enable_models(org, **updates):
    """The organization has opted in: switch on, key stored, both models chosen, the current confirmation recorded."""
    configure_ai(org, **{"model": EXTRACTION_MODEL, "document_model": READING_MODEL, "import_enabled": True,
                         "monthly_limit_usd": None, **updates})


async def extract_all(org, harness=None, **options) -> int:
    """Run the extraction queue of an organization until it is empty; the number of extractions that were recorded.

    Without a harness the organization opts in with both models and the fakes answer: the tests of what follows an
    extraction (assignments, duplicates, adoptions) need a proposal, not a particular run of the models.
    """
    if harness is None:
        enable_models(org)
        harness = Harness()
    done = 0
    while await process_once(get_worker_session_factory(), org, providers=harness, **options):
        done += 1
    return done


def inbox_adapters(stages, harness, **options):
    """The adapters a test drives with a ``WorkflowRunner``: the runner's own stages and, if named, ``intake_extraction``.

    The inbox's extraction stage reaches the organization's provider, so the declaration worker runs it and the inbox
    worker's runner has no such stage (``core/invoice_claimant.py``). A test that follows a document from the upload to its
    proposal puts the stage next to the runner's in this scaffold, with the fake providers; it is looked up when it runs, so
    that a test may replace ``intake_extraction.process_once``.
    """
    from app.workers import intake_extraction
    from app.workers.workflow_runner import build_adapters

    names = tuple(stage for stage in stages if stage != "intake_extraction")
    runner = build_adapters(names, **options) if names else {}

    async def extraction(org):
        return "PROCESSED" if await intake_extraction.process_once(get_worker_session_factory(), org, providers=harness) else "IDLE"

    return {stage: extraction if stage == "intake_extraction" else runner[stage] for stage in stages}


async def confirm_partition(api, ref, *documents):
    """A person confirms how the original divides: these logical documents, each page in exactly one of them.

    One document of all its pages is the confirmation "this is one document". It follows the partition that stands.
    """
    current = (await api.get("/api/document-refs/" + ref["id"] + "/partition")).json()["current"]
    reply = await api.post("/api/document-refs/" + ref["id"] + "/partition", headers={
        "Idempotency-Key": str(uuid.uuid4()), "If-Match": '"' + (current["id"] if current else "none") + '"'},
        json={"logical_document_ids": list(documents), "reason": "Synthetic confirmation", "visual_pages_reviewed": True})
    assert reply.status_code == 201, reply.text
    return reply.json()


async def ledger(org):
    async with get_worker_tenant_session(org) as db:
        return list((await db.scalars(select(InvoiceModelCall).order_by(InvoiceModelCall.created_at, InvoiceModelCall.id))).all())


def document_pdf(pages):
    """A PDF whose pages are given as text lines (a page with a text layer) or None (a scanned page: an image only)."""
    out = io.BytesIO()
    sheet = canvas.Canvas(out)
    for lines in pages:
        if lines is None:
            scan = io.BytesIO()
            Image.new("L", (595, 842), 255).save(scan, "PNG")
            scan.seek(0)
            sheet.drawImage(ImageReader(scan), 0, 0, width=595, height=842)
        else:
            for row, line in enumerate(lines):
                sheet.drawString(60, 750 - 40 * row, line)
        sheet.showPage()
    sheet.save()
    return out.getvalue()


def picture(fmt):
    """A synthetic PNG or JPEG original of a scanned voucher."""
    out = io.BytesIO()
    Image.new("RGB", (800, 1100), (250, 250, 250)).save(out, fmt)
    return out.getvalue()
