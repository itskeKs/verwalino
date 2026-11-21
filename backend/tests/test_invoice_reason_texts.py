"""Every reason the invoice path stores has a German explanation on the invoice screen (issue #500).

The screen explains an outcome from one map (``frontend/src/lib/invoiceProcessing.ts``), keyed by the reason code the
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

# Where the invoice path names an outcome, and how: ``outcome("status", "code")``, ``InvoiceModelFailure("status", "code")``,
# a constant ``NAME = "code"``, or a document's ``processing_error_code = "code"``.
SOURCES = {
    "core/invoice_pdf.py": [rf'outcome\(\s*"[a-z_]+",\s*"{CODE}"'],
    "declarations/invoice_pipeline.py": [rf'InvoiceModelFailure\(\s*"[a-z_]+",\s*"{CODE}"', rf'^[A-Z_]+ = "{CODE}"$'],
    "workers/invoice_extraction.py": [rf'outcome\(\s*"[a-z_]+",\s*"{CODE}"'],
    "services/invoice_processing_service.py": [rf'(?:processing_error_code|error_code) = "{CODE}"', rf'outcome\(\s*"[a-z_]+",\s*"{CODE}"'],
    "services/ai_settings_service.py": [rf"^INVOICE_AI_[A-Z_]+ = '{CODE}'$"],
}
# Stored outside these files: by the database function that cancels an attempt whose initiator lost permission
# (migration 0035), and by the proposal that lacks a required field (``_proposal`` in invoice_pipeline.py).
ELSEWHERE = {"scope_permission_changed", "missing_fields"}


def stored_codes() -> set[str]:
    codes = set(ELSEWHERE)
    for name, patterns in SOURCES.items():
        text = (BACKEND / name).read_text()
        for pattern in patterns:
            codes.update(re.findall(pattern, text, re.MULTILINE))
    return codes


def explained_codes() -> set[str]:
    text = (ROOT / "frontend" / "src" / "lib" / "invoiceProcessing.ts").read_text()
    block = text[text.index("INVOICE_PROCESSING_MESSAGES"):text.index("const NO_EXPLANATION")]
    return set(re.findall(r"^  ([a-z_]+):", block, re.MULTILINE))


def test_the_scan_finds_the_codes_this_issue_added():
    # A guard that finds nothing guards nothing.
    assert {"ai_import_disabled", "ai_invoice_confirmation_missing", "organization_ai_budget_exceeded", "invoice_page_limit",
            "model_reading_failed", "model_extraction_failed", "model_result_invalid", "lease_lost", "render_timeout",
            "render_unavailable", "image_pixel_limit", "retry_budget_exhausted", "processor_failed"} <= stored_codes()


def test_every_stored_reason_has_a_german_explanation():
    assert stored_codes() - explained_codes() == set()
