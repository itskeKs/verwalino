"""Every reason the invoice path and the inbox's extraction store has a German explanation on the screens (issues #500, #501).

The screens explain an outcome from one map (``frontend/src/lib/invoiceProcessing.ts``), keyed by the reason code the
backend stores with the attempt. A code that is added here without a text there would show the user a general sentence
instead of what happened; this test fails for it. Nothing here needs the database.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend" / "app"
CODE = r"([a-z]+(?:_[a-z]+)+)"

# Where the invoice path and the inbox's extraction name an outcome, and how: ``outcome("status", "code")``,
# ``InvoiceModelFailure("status", "code")``, a constant ``NAME = "code"``, a document's ``processing_error_code = "code"``, or
# the inbox's compact ``result('status','code')``, ``failure('code')`` and ``error='code'``.
SOURCES = {
    "core/invoice_pdf.py": [rf'outcome\(\s*"[a-z_]+",\s*"{CODE}"'],
    "declarations/invoice_pipeline.py": [rf'InvoiceModelFailure\(\s*"[a-z_]+",\s*"{CODE}"', rf'^[A-Z_]+ = "{CODE}"$'],
    "workers/invoice_extraction.py": [rf'outcome\(\s*"[a-z_]+",\s*"{CODE}"'],
    "services/invoice_processing_service.py": [rf'(?:processing_error_code|error_code) = "{CODE}"', rf'outcome\(\s*"[a-z_]+",\s*"{CODE}"'],
    "services/ai_settings_service.py": [rf"^(?:INVOICE|INBOX)_AI_[A-Z_]+ = '{CODE}'$"],
    "core/intake_extraction.py": [rf"\b(?:result|failure)\('[a-z_]+','{CODE}'"],
    "workers/intake_extraction.py": [rf"\b(?:refusal|failure)\('[a-z_]+','{CODE}'", rf"\bfailure\('{CODE}'\)", rf"\berror='{CODE}'"],
}
# Stored outside these files: by the database function that cancels an attempt whose initiator lost permission
# (migration 0035), by the proposal that lacks a required field (``_proposal`` in invoice_pipeline.py), and by the
# inbox's job functions (migration 0098): an initiator who lost permission, and the third attempt that did not finish.
ELSEWHERE = {"scope_permission_changed", "missing_fields", "initiator_permission_changed", "attempt_limit"}


def stored_codes() -> set[str]:
    codes = set(ELSEWHERE)
    for name, patterns in SOURCES.items():
        text = (BACKEND / name).read_text()
        for pattern in patterns:
            codes.update(re.findall(pattern, text, re.MULTILINE))
    return codes


def explained_codes() -> set[str]:
    text = (ROOT / "frontend" / "src" / "lib" / "invoiceProcessing.ts").read_text()
    block = text[text.index("const PROCESSING_MESSAGES"):text.index("const text = ")]
    return set(re.findall(r"^  ([a-z_]+):", block, re.MULTILINE))


def test_the_scan_finds_the_codes_this_issue_added():
    # A guard that finds nothing guards nothing.
    assert {"ai_import_disabled", "ai_invoice_confirmation_missing", "organization_ai_budget_exceeded", "invoice_page_limit",
            "model_reading_failed", "model_extraction_failed", "model_result_invalid", "lease_lost", "render_timeout",
            "render_unavailable", "image_pixel_limit", "retry_budget_exhausted", "processor_failed"} <= stored_codes()


def test_the_scan_finds_the_codes_of_the_inbox():
    assert {"ai_inbox_confirmation_missing", "structured_selection_partial", "text_size_limit", "pdf_parser_unavailable", "file_size_limit",
            "mime_mismatch", "invalid_page_selection", "source_page_count_changed", "unsupported_format", "invalid_document",
            "extraction_timeout", "storage_integrity_mismatch", "stored_original_unavailable", "initiator_permission_changed",
            "attempt_limit", "invoice_page_limit", "invoice_text_limit", "model_extraction_failed"} <= stored_codes()


def test_the_inbox_produces_no_code_of_the_text_recognition_it_dropped():
    """The texts for such codes stay for results that earlier releases stored; nothing produces them any more."""
    produced = set()
    for name in ("core/intake_extraction.py", "workers/intake_extraction.py"):
        for pattern in SOURCES[name]:
            produced.update(re.findall(pattern, (BACKEND / name).read_text(), re.MULTILINE))
    assert "processor_failed" in produced
    assert not {code for code in produced if code.startswith("ocr_") or code == "incomplete_text"}


def test_every_stored_reason_has_a_german_explanation():
    assert stored_codes() - explained_codes() == set()
