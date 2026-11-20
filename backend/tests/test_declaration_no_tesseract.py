"""The declaration import has no Tesseract step and no fallback to one (issue #499, ADR 0017).

Class guard: the declaration modules and the declaration worker never name Tesseract. The invoice modules keep
their own use until #500 and #502 replace it; they are not scanned.
"""
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCANNED = [*sorted((BACKEND / "app" / "declarations").rglob("*.py")), BACKEND / "app" / "workers" / "declaration_import.py"]

pytestmark = pytest.mark.no_db


def test_the_guard_scans_the_declaration_modules_and_the_worker() -> None:
    names = {path.name for path in SCANNED}
    assert {"pipeline.py", "preflight.py", "openai_provider.py", "declaration_import.py"} <= names


@pytest.mark.parametrize("path", SCANNED, ids=lambda path: str(path.relative_to(BACKEND)))
def test_no_tesseract_in_the_declaration_path(path: Path) -> None:
    assert "tesseract" not in path.read_text(encoding="utf-8").lower()
