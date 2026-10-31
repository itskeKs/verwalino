"""Bounded local invoice extraction. This module never invokes a provider.

All parsing runs in a killable child process. OCR renders one page at a time.
Failures discard partial financial proposals; original bytes remain in storage.
"""
from __future__ import annotations

import io
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from app.core.einvoice import EInvoiceExtractor
from app.core.document_limits import MAX_UPLOAD_BYTES
from app.core.pdf_extraction import PdfTextExtractor

PROCESSOR_VERSION = "invoice-local-v1"
MAX_PDF_PAGES = 200
MAX_OCR_PAGES = 20
DPI = 200
MAX_PIXELS = 20_000_000
PAGE_SECONDS = 10
TOTAL_SECONDS = 120
MAX_TEXT_CHARS = 1_000_000


def outcome(status, error_code=None, payload=None):
    return {"status": status, "error_code": error_code, "payload": payload,
            "processor_version": PROCESSOR_VERSION}


def ocr_page(pdf_path: Path, page: int, directory: Path) -> str:
    """One combined render/OCR deadline; caller's process group owns children."""
    if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
        raise FileNotFoundError("ocr_unavailable")
    started = time.monotonic()
    stem = directory / "page"
    subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile",
                    "-r", str(DPI), "-png", str(pdf_path), str(stem)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=PAGE_SECONDS)
    image_path = stem.with_suffix(".png")
    from PIL import Image
    with Image.open(image_path) as image:
        if image.width * image.height > MAX_PIXELS:
            raise ValueError("image_pixel_limit")
    remaining = PAGE_SECONDS - (time.monotonic() - started)
    if remaining <= 0:
        raise subprocess.TimeoutExpired("invoice-page", PAGE_SECONDS)
    try:
        result = subprocess.run(["tesseract", str(image_path), "stdout", "-l", "deu"],
                                check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=remaining)
        return result.stdout.decode("utf-8", errors="replace")
    finally:
        image_path.unlink(missing_ok=True)


def extract_invoice(data: bytes, mime_type: str) -> dict:
    """Child-process entry point; synchronous and independently testable."""
    if len(data) > MAX_UPLOAD_BYTES:
        return outcome("unsupported", "file_size_limit")
    is_pdf = data.startswith(b"%PDF") or mime_type in {"application/pdf", "application/x-pdf"}
    if not is_pdf:
        structured = EInvoiceExtractor().extract(data, mime_type)
        if structured.is_einvoice:
            payload = structured.to_dict()
            payload["source_pages"] = []
            return outcome("succeeded", payload=payload)
        return outcome("unsupported", "unsupported_format")
    try:
        import pdfplumber
        from pdfminer.pdfdocument import PDFPasswordIncorrect
    except ImportError:
        return outcome("unavailable", "ocr_unavailable")
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if pdf.doc.encryption:
                return outcome("unsupported", "encrypted_pdf")
            pages = pdf.pages
            if not pages or len(pages) > MAX_PDF_PAGES:
                return outcome("unsupported", "pdf_page_limit")
            # Validate before structured routing, but never OCR a structured invoice.
            structured = EInvoiceExtractor().extract(data, mime_type)
            if structured.is_einvoice:
                payload = structured.to_dict()
                payload["source_pages"] = list(range(1, len(pages) + 1))
                return outcome("succeeded", payload=payload)
            texts = [page.extract_text() or "" for page in pages]
            missing = [i for i, value in enumerate(texts) if len(value.strip()) < 20]
            if missing and len(pages) > MAX_OCR_PAGES:
                return outcome("needs_review", "ocr_page_limit")
            for index in missing:
                page = pages[index]
                width = math.ceil(float(page.width) * DPI / 72)
                height = math.ceil(float(page.height) * DPI / 72)
                if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                    return outcome("unsupported", "image_pixel_limit")
            if missing:
                with tempfile.TemporaryDirectory(prefix="hv-invoice-") as temp:
                    directory = Path(temp)
                    pdf_path = directory / "source.pdf"
                    pdf_path.write_bytes(data)
                    for index in missing:
                        texts[index] = ocr_page(pdf_path, index + 1, directory)
            text = "\n".join(texts)
            if len(text) > MAX_TEXT_CHARS:
                return outcome("needs_review", "text_size_limit")
            if any(len(value.strip()) < 20 for value in texts):
                return outcome("needs_review", "incomplete_text")
            result = PdfTextExtractor()._parse_german_invoice(text)
            payload = result.to_dict()
            payload["source_pages"] = list(range(1, len(pages) + 1))
            return outcome("succeeded" if result.gross_amount and result.invoice_date else "needs_review",
                           None if result.gross_amount and result.invoice_date else "missing_fields", payload)
    except PDFPasswordIncorrect:
        return outcome("unsupported", "encrypted_pdf")
    except (ImportError, FileNotFoundError):
        return outcome("unavailable", "ocr_unavailable")
    except subprocess.TimeoutExpired:
        return outcome("timeout", "ocr_timeout")
    except subprocess.CalledProcessError:
        return outcome("unavailable", "ocr_unavailable")
    except Exception as error:
        # pdfplumber wraps pdfminer's password error. Inspect type, never text.
        if isinstance(error.__context__, PDFPasswordIncorrect):
            return outcome("unsupported", "encrypted_pdf")
        return outcome("unsupported", "invalid_pdf")


def run_isolated_extraction(data: bytes, mime_type: str, *, timeout: float = TOTAL_SECONDS) -> dict:
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "TESSDATA_PREFIX", "PYTHONPATH", "TMPDIR"}}
    process = subprocess.Popen([sys.executable, "-m", "app.core.invoice_ocr", mime_type],
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
    print(json.dumps(extract_invoice(sys.stdin.buffer.read(MAX_UPLOAD_BYTES + 1), sys.argv[1])))
