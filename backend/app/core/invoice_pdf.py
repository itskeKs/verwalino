"""Bounded local inspection of an invoice original. This module never invokes a provider and runs no OCR.

The inspection runs in a killable child process. It decides what an original is: a structured invoice (XRechnung,
or a PDF with embedded ZUGFeRD/Factur-X data) that the deterministic parser reads in full, an original the local
checks refuse, or an unstructured PDF whose pages the model-assisted path reads (issue #500, ADR 0017). For the
last it returns each page's text layer and size; reading a page without a text layer is the model's work and
rendering it is bounded in ``declarations/invoice_pipeline.py``. Failures discard partial financial proposals;
original bytes remain in storage.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

from app.core.einvoice import EInvoiceExtractor, invoice_proposal
from app.core.document_limits import MAX_UPLOAD_BYTES

# The version of a proposal the deterministic parsers produced; ``invoice-model-v1`` marks one the model-assisted
# path produced. Change it with the prompt or schema of that path (``declarations/prompts.py``).
LOCAL_PROCESSOR_VERSION = "invoice-local-v1"
MODEL_PROCESSOR_VERSION = "invoice-model-v1"
PROCESSOR_VERSIONS = frozenset({LOCAL_PROCESSOR_VERSION, MODEL_PROCESSOR_VERSION})
# The status of an inspection that is not a final outcome: the pages go to the model-assisted path.
UNSTRUCTURED = "unstructured"
# An original with more pages is refused before it is parsed; an unstructured invoice goes to the models only up to
# INVOICE_MAX_PAGES. A real invoice or credit note has one to three pages and a utility's final bill a few more.
MAX_PDF_PAGES = 200
INVOICE_MAX_PAGES = 10
# The text of all pages of one unstructured invoice, text layers and transcripts together (about 15,000 tokens): a
# dense page holds three to four thousand characters, so ten pages fit with room to spare.
INVOICE_MAX_TEXT_CHARS = 60_000
TOTAL_SECONDS = 120


def outcome(status, error_code=None, payload=None, *, processor_version=LOCAL_PROCESSOR_VERSION):
    return {"status": status, "error_code": error_code, "payload": payload,
            "processor_version": processor_version}


def inspect_invoice(data: bytes, mime_type: str) -> dict:
    """Child-process entry point; synchronous and independently testable."""
    if len(data) > MAX_UPLOAD_BYTES:
        return outcome("unsupported", "file_size_limit")
    is_pdf = data.startswith(b"%PDF") or mime_type in {"application/pdf", "application/x-pdf"}
    if not is_pdf:
        structured = EInvoiceExtractor().extract(data, mime_type)
        if structured.is_einvoice:
            return outcome("succeeded", payload=invoice_proposal(structured, []))
        return outcome("unsupported", "unsupported_format")
    try:
        import pdfplumber
        from pdfminer.pdfdocument import PDFPasswordIncorrect
    except ImportError:
        return outcome("unavailable", "pdf_parser_unavailable")
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if pdf.doc.encryption:
                return outcome("unsupported", "encrypted_pdf")
            pages = pdf.pages
            if not pages or len(pages) > MAX_PDF_PAGES:
                return outcome("unsupported", "pdf_page_limit")
            # Validate before routing: a valid structured invoice never reaches a model.
            structured = EInvoiceExtractor().extract(data, mime_type)
            if structured.is_einvoice:
                return outcome("succeeded", payload=invoice_proposal(structured, list(range(1, len(pages) + 1))))
            if len(pages) > INVOICE_MAX_PAGES:
                return {"status": UNSTRUCTURED, "page_count": len(pages), "pages": []}
            layers = [{"number": number, "text": page.extract_text() or "", "width": float(page.width), "height": float(page.height)}
                      for number, page in enumerate(pages, start=1)]
            if sum(len(layer["text"]) for layer in layers) > INVOICE_MAX_TEXT_CHARS:
                return outcome("needs_review", "invoice_text_limit")
            return {"status": UNSTRUCTURED, "page_count": len(pages), "pages": layers}
    except PDFPasswordIncorrect:
        return outcome("unsupported", "encrypted_pdf")
    except ImportError:
        return outcome("unavailable", "pdf_parser_unavailable")
    except Exception as error:
        # pdfplumber wraps pdfminer's password error. Inspect type, never text.
        if isinstance(error.__context__, PDFPasswordIncorrect):
            return outcome("unsupported", "encrypted_pdf")
        return outcome("unsupported", "invalid_pdf")


def run_isolated_inspection(data: bytes, mime_type: str, *, timeout: float = TOTAL_SECONDS) -> dict:
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "PYTHONPATH", "TMPDIR"}}
    process = subprocess.Popen([sys.executable, "-m", "app.core.invoice_pdf", mime_type],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, start_new_session=True, env=env,
                               cwd=Path(__file__).resolve().parents[2])
    try:
        output, _ = process.communicate(data, timeout=min(timeout, TOTAL_SECONDS))
        if process.returncode != 0:
            return outcome("failed", "processor_failed")
        return json.loads(output)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        return outcome("timeout", "extraction_timeout")
    except (ValueError, OSError):
        return outcome("failed", "processor_failed")


if __name__ == "__main__":
    # The deadline survives a worker crash; terminate the entire child group.
    signal.signal(signal.SIGALRM, lambda *_: os.killpg(os.getpgrp(), signal.SIGKILL))
    signal.alarm(TOTAL_SECONDS)
    if sys.platform == "linux":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (1024 ** 3, 1024 ** 3))
    # This is a private IPC result, never an operational log.
    print(json.dumps(inspect_invoice(sys.stdin.buffer.read(MAX_UPLOAD_BYTES + 1), sys.argv[1])))
