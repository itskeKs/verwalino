"""Tesseract is not part of the application, its workers, its installers or its image (issue #502, ADR 0017).

Class guard for the whole product: every import path reads a document through the organization's two models, so no
source of the application (backend code, the lock file, the image, the Compose files, the installers and the CI that
builds and tests them) names Tesseract: no Python binding, no executable, no package, no language data. The PDF tools
a rendering still needs (Poppler, qpdf, pikepdf, pdf2image) are not Tesseract and stay. Stored results of earlier
releases may still name the earlier text recognition; that is data and is not scanned.
"""
import tomllib
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPOSITORY = BACKEND.parent

pytestmark = pytest.mark.no_db

# What names Tesseract in a source: the binding, the executable, the packages (tesseract-ocr, tesseract-ocr-deu,
# libtesseract) and the language data directory its executable reads.
NEEDLES = (b"tesseract", b"tessdata")

# The modules that ran Tesseract before the models replaced it. Nothing calls them; they must not come back.
REMOVED_MODULES = ("app/core/invoice_ocr.py", "app/core/pdf_extraction.py")

SINGLE_FILES = ("backend/pyproject.toml", "backend/uv.lock", "Dockerfile", "Makefile", ".env.example",
                "docker-compose.prod.yml")
DIRECTORIES = ("backend/app", "docker", "infra", "scripts", ".github/workflows")


def sources() -> list[Path]:
    """Application and worker code, the dependency lock, the image, Compose, installers and the CI that runs them."""
    files = [REPOSITORY / name for name in SINGLE_FILES]
    for directory in DIRECTORIES:
        files += sorted(path for path in (REPOSITORY / directory).rglob("*")
                        if path.is_file() and "__pycache__" not in path.parts)
    return files


def names_tesseract(data: bytes) -> bool:
    lowered = data.lower()
    return any(needle in lowered for needle in NEEDLES)


def test_the_guard_scans_the_application_the_workers_the_image_and_the_installers() -> None:
    relative = {str(path.relative_to(REPOSITORY)) for path in sources()}
    assert {"backend/pyproject.toml", "backend/uv.lock", "Dockerfile", "docker-compose.prod.yml",
            "infra/docker-compose.yml", "docker/entrypoint.sh", "docker/worker-entrypoint.sh",
            "scripts/self-host.sh", "scripts/deploy-server.sh", "scripts/dev-local.sh",
            "backend/app/workers/declaration_import.py", "backend/app/workers/invoice_extraction.py",
            "backend/app/workers/intake_extraction.py", "backend/app/declarations/preflight.py",
            ".github/workflows/tests-on-pr.yaml"} <= relative
    assert all(path.is_file() for path in sources())


def test_no_source_names_tesseract() -> None:
    offenders = [str(path.relative_to(REPOSITORY)) for path in sources() if names_tesseract(path.read_bytes())]
    assert offenders == []


@pytest.mark.parametrize("source", [
    b"import pytesseract",
    b"subprocess.run(['tesseract', str(image), 'stdout', '-l', 'deu'])",
    b"shutil.which('Tesseract')",
    b"apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-deu",
    b"    { name = \"pytesseract\" },",
    b"env = {'TESSDATA_PREFIX': '/usr/share/tessdata'}",
], ids=["import", "executable", "lookup", "packages", "lock-entry", "language-data"])
def test_the_guard_recognises_each_way_of_naming_tesseract(source: bytes) -> None:
    assert names_tesseract(source)


def test_the_guard_ignores_the_tools_a_rendering_needs() -> None:
    assert not names_tesseract(b"apt-get install -y poppler-utils qpdf clamav\nimport pikepdf, pdf2image\npdftoppm")


@pytest.mark.parametrize("module", REMOVED_MODULES)
def test_the_modules_that_ran_tesseract_are_gone(module: str) -> None:
    assert not (BACKEND / module).exists()


def test_rendering_and_structural_validation_stay_reachable_without_an_extra() -> None:
    """The PDF libraries the preflight imports are ordinary dependencies; no extra is named after text recognition."""
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = {entry.split(">")[0].split("<")[0].split("=")[0].split("[")[0] for entry in project["dependencies"]}
    assert {"pdf2image", "pikepdf", "pdfplumber"} <= dependencies
    assert "ocr" not in project["optional-dependencies"]


def test_the_image_keeps_the_pdf_tools() -> None:
    dockerfile = (REPOSITORY / "Dockerfile").read_text(encoding="utf-8")
    assert "poppler-utils" in dockerfile and "qpdf" in dockerfile
    assert "--extra ocr" not in dockerfile
