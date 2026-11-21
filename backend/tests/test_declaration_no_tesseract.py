"""The declaration import and the invoice worker have no Tesseract step and no fallback to one (issues #499 and #500,
ADR 0017).

Class guard: the declaration modules, the declaration worker, the invoice worker and the local invoice inspection never
name Tesseract and never import the modules that still run it (the legacy invoice OCR, the intake and the PDF text
extraction). Those keep their own use until #501 and #502 replace it; they are not scanned.
"""
import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCANNED = [
    *sorted((BACKEND / "app" / "declarations").rglob("*.py")),
    BACKEND / "app" / "workers" / "declaration_import.py",
    BACKEND / "app" / "workers" / "invoice_extraction.py",
    BACKEND / "app" / "core" / "invoice_pdf.py",
]

pytestmark = pytest.mark.no_db

# The modules outside the declaration path that call Tesseract.
OCR_MODULES = {"app.core.pdf_extraction", "app.core.invoice_ocr", "app.core.intake_extraction"}


def ocr_imports(source: str) -> set[str]:
    """The OCR modules a source imports, by any form of import statement, at any depth."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names} & OCR_MODULES
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found |= {node.module} & OCR_MODULES
            found |= {f"{node.module}.{alias.name}" for alias in node.names} & OCR_MODULES
    return found


def test_the_guard_scans_the_declaration_modules_and_the_worker() -> None:
    names = {path.name for path in SCANNED}
    assert {"pipeline.py", "preflight.py", "openai_provider.py", "declaration_import.py",
            "invoice_pipeline.py", "invoice_extraction.py", "invoice_pdf.py"} <= names


@pytest.mark.parametrize("path", SCANNED, ids=lambda path: str(path.relative_to(BACKEND)))
def test_no_tesseract_in_the_declaration_and_invoice_paths(path: Path) -> None:
    assert "tesseract" not in path.read_text(encoding="utf-8").lower()


@pytest.mark.parametrize("path", SCANNED, ids=lambda path: str(path.relative_to(BACKEND)))
def test_the_declaration_and_invoice_paths_import_no_module_that_runs_tesseract(path: Path) -> None:
    assert ocr_imports(path.read_text(encoding="utf-8")) == set()


@pytest.mark.parametrize("source", [
    "import app.core.pdf_extraction",
    "from app.core.invoice_ocr import extract_invoice",
    "from app.core import intake_extraction",
    "def late():\n    from app.core.pdf_extraction import extract_text\n    return extract_text",
], ids=["import", "from-module", "from-package", "inside-a-function"])
def test_the_guard_fails_for_each_form_of_import_of_an_ocr_module(source: str) -> None:
    assert ocr_imports(source)


def test_the_guard_ignores_other_core_imports() -> None:
    assert ocr_imports("from app.core import storage\nfrom app.core.document_limits import MAX_UPLOAD_BYTES") == set()
