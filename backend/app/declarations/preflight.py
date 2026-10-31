"""Safe, bounded local preflight for uploaded declaration PDFs.

The preflight is deliberately separate from provider extraction.  It verifies
the stored bytes, rejects active PDF content, and produces per-page artifacts
that can safely be handed to a later extraction pipeline.
"""

from __future__ import annotations

import base64
import hashlib
import io
import math
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from app.core.document_limits import MAX_UPLOAD_BYTES


_PDF_TRAILING_WHITESPACE = b" \t\r\n\x0b\x0c\x00"
_ACTIVE_ACTIONS = {
    "/URI",
    "/SubmitForm",
    "/ImportData",
    "/Launch",
    "/JavaScript",
}


class DocumentIntegrityError(ValueError):
    """A stable, user-safe failure encountered before provider processing."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        super().__init__(detail or code)


class OcrUnavailableError(RuntimeError):
    """The local OCR runtime is unavailable; this is a page-level condition."""


class OcrEngine(Protocol):
    def extract_page(self, pdf_bytes: bytes, page_number: int) -> str:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class PageArtifact:
    page_number: int
    text: str
    source: str
    rotation: int
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreflightResult:
    sha256: str
    byte_size: int
    page_count: int
    pages: tuple[PageArtifact, ...]


@dataclass(slots=True)
class ImportPreflightBudget:
    started_at: float
    ocr_pages: int = 0
    estimated_pixels: int = 0
    rendered_pixels: int = 0
    temporary_bytes: int = 0


@dataclass(frozen=True, slots=True)
class _StructuralPage:
    page_number: int
    width_points: float
    height_points: float


class TesseractOcrEngine:
    """Render and OCR precisely one PDF page without retaining image files."""

    def __init__(
        self,
        *,
        dpi: int = 180,
        timeout_seconds: int = 30,
        max_temp_bytes: int = 256 * 1024 * 1024,
        temporary_root: Path | None = None,
    ) -> None:
        self._dpi = dpi
        self._timeout_seconds = timeout_seconds
        self._max_temp_bytes = max_temp_bytes
        self._temporary_root = temporary_root
        self.last_temp_bytes = 0
        self.last_rendered_pixels = 0

    def extract_page(self, pdf_bytes: bytes, page_number: int) -> str:
        return self.extract_page_at_dpi(pdf_bytes, page_number, self._dpi)

    def extract_page_at_dpi(
        self,
        pdf_bytes: bytes,
        page_number: int,
        dpi: int,
        *,
        max_rendered_pixels: int | None = None,
        deadline_at: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> str:
        temp_root = str(self._temporary_root) if self._temporary_root is not None else None
        self.last_temp_bytes = 0
        self.last_rendered_pixels = 0
        with tempfile.TemporaryDirectory(dir=temp_root, prefix="hv-declaration-ocr-") as directory:
            try:
                render_options: dict[str, object] = {}
                if max_rendered_pixels is not None:
                    render_options["max_rendered_pixels"] = max_rendered_pixels
                if deadline_at is not None:
                    render_options["deadline_at"] = deadline_at
                    render_options["monotonic"] = monotonic
                text = self._render_and_recognize(
                    pdf_bytes, page_number, dpi, directory, **render_options
                )
            except BaseException:
                self.last_temp_bytes = _directory_size(Path(directory))
                if self.last_temp_bytes > self._max_temp_bytes:
                    raise DocumentIntegrityError("OCR_TEMP_BYTES_LIMIT_EXCEEDED")
                raise

            self.last_temp_bytes = _directory_size(Path(directory))
            if self.last_temp_bytes > self._max_temp_bytes:
                raise DocumentIntegrityError("OCR_TEMP_BYTES_LIMIT_EXCEEDED")
            return text

    def _render_and_recognize(
        self,
        pdf_bytes: bytes,
        page_number: int,
        dpi: int,
        output_dir: str,
        *,
        max_rendered_pixels: int | None = None,
        deadline_at: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> str:
        try:
            from pdf2image import convert_from_bytes
            from pdf2image.exceptions import PDFPopplerTimeoutError, PopplerNotInstalledError
            import pytesseract
            from PIL import Image
        except ImportError as error:  # pragma: no cover - depends on deployment image
            raise OcrUnavailableError("OCR dependencies are unavailable") from error

        try:
            render_timeout = self._operation_timeout(deadline_at, monotonic)
            paths = convert_from_bytes(
                pdf_bytes,
                dpi=dpi,
                first_page=page_number,
                last_page=page_number,
                thread_count=1,
                timeout=render_timeout,
                output_folder=output_dir,
                paths_only=True,
            )
        except PDFPopplerTimeoutError as error:
            raise TimeoutError("Poppler rendering timed out") from error
        except TimeoutError:
            raise
        except (PopplerNotInstalledError, FileNotFoundError, OSError) as error:
            raise OcrUnavailableError("Poppler is unavailable") from error

        if len(paths) != 1:
            raise RuntimeError("OCR renderer did not return exactly one page")

        with Image.open(paths[0]) as image:
            self.last_rendered_pixels = image.width * image.height
            if (
                max_rendered_pixels is not None
                and self.last_rendered_pixels > max_rendered_pixels
            ):
                raise DocumentIntegrityError("OCR_RENDERED_PIXELS_LIMIT_EXCEEDED")
            ocr_timeout = self._operation_timeout(deadline_at, monotonic)
            try:
                return pytesseract.image_to_string(
                    image,
                    lang="deu",
                    timeout=ocr_timeout,
                )
            except pytesseract.TesseractNotFoundError as error:
                raise OcrUnavailableError("Tesseract is unavailable") from error
            except RuntimeError as error:
                if "tesseract process timeout" in str(error).lower():
                    raise TimeoutError("Tesseract OCR timed out") from error
                raise

    def _operation_timeout(
        self,
        deadline_at: float | None,
        monotonic: Callable[[], float],
    ) -> int | float:
        if deadline_at is None:
            return self._timeout_seconds
        remaining = deadline_at - monotonic()
        if remaining <= 0:
            raise TimeoutError("OCR operation exceeded the preflight deadline")
        return min(self._timeout_seconds, remaining)


class PdfPreflight:
    """Validate PDFs before text/OCR extraction and enforce local resource caps."""

    def __init__(
        self,
        *,
        max_pdf_bytes: int = MAX_UPLOAD_BYTES,
        max_pages: int = 500,
        text_min_chars: int = 40,
        ocr_dpi: int = 180,
        ocr_timeout_seconds: int = 30,
        max_pixels_per_page: int = 20_000_000,
        max_temp_bytes: int = 256 * 1024 * 1024,
        max_ocr_pages_per_import: int = 200,
        max_pixels_per_import: int = 400_000_000,
        deadline_seconds: int = 600,
        ocr_engine: OcrEngine | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_pdf_bytes = max_pdf_bytes
        self._max_pages = max_pages
        self._text_min_chars = text_min_chars
        self._ocr_dpi = ocr_dpi
        if (
            isinstance(ocr_timeout_seconds, bool)
            or not isinstance(ocr_timeout_seconds, int)
            or ocr_timeout_seconds <= 0
        ):
            raise ValueError("ocr_timeout_seconds must be a positive integer")
        self._vision_timeout_seconds = ocr_timeout_seconds
        self._max_pixels_per_page = max_pixels_per_page
        self._max_temp_bytes = max_temp_bytes
        self._max_ocr_pages_per_import = max_ocr_pages_per_import
        self._max_pixels_per_import = max_pixels_per_import
        self._deadline_seconds = deadline_seconds
        self._monotonic = monotonic
        self._ocr_engine: OcrEngine = ocr_engine or TesseractOcrEngine(
            dpi=ocr_dpi,
            timeout_seconds=ocr_timeout_seconds,
            max_temp_bytes=max_temp_bytes,
        )

    def new_import_budget(self) -> ImportPreflightBudget:
        return ImportPreflightBudget(started_at=self._monotonic())

    def render_page_data_url(
        self,
        pdf_bytes: bytes,
        page_number: int,
        *,
        budget: ImportPreflightBudget,
    ) -> str:
        """Rasterize one already-selected safe PDF page into a PNG data URL.

        This is deliberately independent from OCR: a vision recheck is allowed
        to consume the shared import deadline, pixel and temporary-byte
        budgets, but it never increments the OCR page allowance.  The caller
        owns ``pdf_bytes`` and must retain it only on its stack until this
        method returns; no image or source bytes are retained by preflight.
        """
        if not isinstance(pdf_bytes, bytes):
            raise DocumentIntegrityError("VISION_RENDER_UNAVAILABLE")
        if isinstance(page_number, bool) or not isinstance(page_number, int):
            raise DocumentIntegrityError("PDF_PAGE_OUT_OF_RANGE")
        self._ensure_deadline(budget)
        if len(pdf_bytes) > self._max_pdf_bytes:
            raise DocumentIntegrityError("PDF_SIZE_LIMIT_EXCEEDED")
        self._validate_final_eof(pdf_bytes)
        structural_pages = self._validate_pdf_safety(pdf_bytes)
        self._ensure_deadline(budget)
        if len(structural_pages) > self._max_pages:
            raise DocumentIntegrityError("PDF_PAGE_LIMIT_EXCEEDED")
        if page_number < 1 or page_number > len(structural_pages):
            raise DocumentIntegrityError("PDF_PAGE_OUT_OF_RANGE")

        page = structural_pages[page_number - 1]
        dpi, estimated_pixels = self._safe_vision_dpi(page)
        self._reserve_vision_budget(budget, estimated_pixels)
        temp_root = (
            str(self._ocr_engine._temporary_root)
            if isinstance(self._ocr_engine, TesseractOcrEngine)
            and self._ocr_engine._temporary_root is not None
            else None
        )
        counted_temp_bytes = 0
        try:
            from pdf2image import convert_from_bytes
            from pdf2image.exceptions import PDFPopplerTimeoutError, PopplerNotInstalledError
            from PIL import Image
        except ImportError as error:  # pragma: no cover - deployment dependency
            raise DocumentIntegrityError("VISION_RENDER_UNAVAILABLE") from error

        directory_path: Path | None = None
        counted_temp_bytes = 0
        try:
            with tempfile.TemporaryDirectory(dir=temp_root, prefix="hv-declaration-vision-") as directory:
                directory_path = Path(directory)
                paths = convert_from_bytes(
                    pdf_bytes,
                    dpi=dpi,
                    first_page=page_number,
                    last_page=page_number,
                    thread_count=1,
                    timeout=self._vision_operation_timeout(budget),
                    output_folder=directory,
                    paths_only=True,
                )
                counted_temp_bytes = _directory_size(directory_path)
                self._account_vision_temp_bytes(budget, counted_temp_bytes)
                if len(paths) != 1:
                    raise DocumentIntegrityError("VISION_RENDER_UNAVAILABLE")
                with Image.open(paths[0]) as image:
                    rendered_pixels = image.width * image.height
                    self._account_actual_vision_pixels(budget, rendered_pixels)
                    encoded = io.BytesIO()
                    image.save(encoded, format="PNG")
                png = encoded.getvalue()
                value = base64.b64encode(png).decode("ascii")
                self._account_vision_temp_bytes(budget, len(png) + len(value))
                self._ensure_deadline(budget)
                return f"data:image/png;base64,{value}"
        except DocumentIntegrityError:
            raise
        except (PDFPopplerTimeoutError, TimeoutError, subprocess.TimeoutExpired) as error:
            raise DocumentIntegrityError("VISION_RENDER_TIMEOUT") from error
        except (PopplerNotInstalledError, FileNotFoundError, OSError, RuntimeError) as error:
            raise DocumentIntegrityError("VISION_RENDER_UNAVAILABLE") from error
        except Exception as error:
            raise DocumentIntegrityError("VISION_RENDER_UNAVAILABLE") from error
        finally:
            if directory_path is not None:
                try:
                    observed_temp_bytes = _directory_size(directory_path)
                    if observed_temp_bytes > counted_temp_bytes:
                        self._account_vision_temp_bytes(
                            budget, observed_temp_bytes - counted_temp_bytes
                        )
                except DocumentIntegrityError:
                    raise
                except Exception as error:
                    raise DocumentIntegrityError("VISION_RENDER_UNAVAILABLE") from error

    def run_bytes(
        self,
        data: bytes,
        *,
        expected_sha256: str,
        expected_size: int,
        budget: ImportPreflightBudget | None = None,
    ) -> PreflightResult:
        """Validate and prepare a single stored PDF byte stream.

        Integrity verification intentionally precedes every PDF parser import
        and open operation.  ``budget`` may be shared between every document in
        one import to carry OCR resource accounting across the batch.
        """
        active_budget = budget or self.new_import_budget()
        self._validate_integrity(data, expected_sha256, expected_size)
        self._ensure_deadline(active_budget)
        if len(data) > self._max_pdf_bytes:
            raise DocumentIntegrityError("PDF_SIZE_LIMIT_EXCEEDED")
        self._validate_final_eof(data)
        structural_pages = self._validate_pdf_safety(data)
        self._ensure_deadline(active_budget)
        if len(structural_pages) > self._max_pages:
            raise DocumentIntegrityError("PDF_PAGE_LIMIT_EXCEEDED")

        page_text, rotations = self._extract_text_layer(data)
        self._ensure_deadline(active_budget)
        if len(page_text) != len(structural_pages):
            raise DocumentIntegrityError("PDF_UNREADABLE")

        pages: list[PageArtifact] = []
        for structural_page, text, rotation in zip(structural_pages, page_text, rotations, strict=True):
            if _non_whitespace_length(text) >= self._text_min_chars:
                pages.append(
                    PageArtifact(
                        page_number=structural_page.page_number,
                        text=text,
                        source="TEXT_LAYER",
                        rotation=rotation,
                    )
                )
                continue

            dpi, estimated_pixels = self._safe_ocr_dpi(structural_page)
            self._reserve_ocr_budget(active_budget, estimated_pixels)
            try:
                if isinstance(self._ocr_engine, TesseractOcrEngine):
                    ocr_text = self._ocr_engine.extract_page_at_dpi(
                        data,
                        structural_page.page_number,
                        dpi,
                        max_rendered_pixels=self._max_pixels_per_page,
                        deadline_at=active_budget.started_at + self._deadline_seconds,
                        monotonic=self._monotonic,
                    )
                else:
                    ocr_text = self._ocr_engine.extract_page(data, structural_page.page_number)
            except OcrUnavailableError:
                pages.append(
                    PageArtifact(
                        page_number=structural_page.page_number,
                        text="",
                        source="UNAVAILABLE",
                        rotation=rotation,
                        issues=("OCR_UNAVAILABLE",),
                    )
                )
            except (TimeoutError, subprocess.TimeoutExpired):
                pages.append(
                    PageArtifact(
                        page_number=structural_page.page_number,
                        text="",
                        source="FAILED",
                        rotation=rotation,
                        issues=("OCR_TIMEOUT",),
                    )
                )
            except DocumentIntegrityError:
                raise
            except Exception:
                pages.append(
                    PageArtifact(
                        page_number=structural_page.page_number,
                        text="",
                        source="FAILED",
                        rotation=rotation,
                        issues=("OCR_FAILED",),
                    )
                )
            else:
                pages.append(
                    PageArtifact(
                        page_number=structural_page.page_number,
                        text=ocr_text,
                        source="OCR",
                        rotation=rotation,
                    )
                )

            self._account_actual_ocr_resources(active_budget, estimated_pixels)
            self._ensure_deadline(active_budget)

        self._ensure_deadline(active_budget)

        return PreflightResult(
            sha256=expected_sha256,
            byte_size=len(data),
            page_count=len(pages),
            pages=tuple(pages),
        )

    @staticmethod
    def _validate_integrity(data: bytes, expected_sha256: str, expected_size: int) -> None:
        if len(data) != expected_size:
            raise DocumentIntegrityError("STORAGE_INTEGRITY_MISMATCH")
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if actual_sha256 != expected_sha256:
            raise DocumentIntegrityError("STORAGE_INTEGRITY_MISMATCH")

    @staticmethod
    def _validate_final_eof(data: bytes) -> None:
        marker = data.rfind(b"%%EOF")
        if marker < 0:
            raise DocumentIntegrityError("PDF_UNREADABLE")
        trailing = data[marker + len(b"%%EOF") :]
        if any(byte not in _PDF_TRAILING_WHITESPACE for byte in trailing):
            raise DocumentIntegrityError("PDF_TRAILING_PAYLOAD")

    @staticmethod
    def _validate_pdf_safety(data: bytes) -> tuple[_StructuralPage, ...]:
        try:
            import pikepdf

            with pikepdf.open(io.BytesIO(data), attempt_recovery=False) as pdf:
                if pdf.is_encrypted:
                    raise DocumentIntegrityError("PDF_ENCRYPTED_FORBIDDEN")
                _reject_unsafe_pdf_objects(pdf.Root)
                pages: list[_StructuralPage] = []
                for number, page in enumerate(pdf.pages, start=1):
                    # Page annotations and form/widget actions are not necessarily
                    # reachable through the catalog's direct object traversal.
                    _reject_unsafe_pdf_objects(page.obj)
                    media_box = page.mediabox
                    user_unit = _page_user_unit(page.obj)
                    width = abs(float(media_box[2]) - float(media_box[0])) * user_unit
                    height = abs(float(media_box[3]) - float(media_box[1])) * user_unit
                    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
                        raise DocumentIntegrityError("PDF_UNREADABLE")
                    pages.append(_StructuralPage(number, width, height))
                return tuple(pages)
        except DocumentIntegrityError:
            raise
        except pikepdf.PasswordError as error:
            raise DocumentIntegrityError("PDF_ENCRYPTED_FORBIDDEN") from error
        except Exception as error:
            raise DocumentIntegrityError("PDF_UNREADABLE") from error

    @staticmethod
    def _extract_text_layer(data: bytes) -> tuple[list[str], list[int]]:
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(data)) as pdf:
                return (
                    [(page.extract_text() or "") for page in pdf.pages],
                    [int(page.rotation or 0) for page in pdf.pages],
                )
        except Exception as error:
            raise DocumentIntegrityError("PDF_UNREADABLE") from error

    def _safe_ocr_dpi(self, page: _StructuralPage) -> tuple[int, int]:
        pixels_at_72 = _pixel_count(page.width_points, page.height_points, 72)
        if pixels_at_72 > self._max_pixels_per_page:
            raise DocumentIntegrityError("OCR_PAGE_DIMENSIONS_UNSAFE")

        dpi = self._ocr_dpi
        pixels = _pixel_count(page.width_points, page.height_points, dpi)
        if pixels <= self._max_pixels_per_page:
            return dpi, pixels

        ratio = math.sqrt(self._max_pixels_per_page / pixels_at_72)
        dpi = max(72, math.floor(72 * ratio))
        pixels = _pixel_count(page.width_points, page.height_points, dpi)
        while dpi > 72 and pixels > self._max_pixels_per_page:
            dpi -= 1
            pixels = _pixel_count(page.width_points, page.height_points, dpi)
        if pixels > self._max_pixels_per_page:
            raise DocumentIntegrityError("OCR_PAGE_DIMENSIONS_UNSAFE")
        return dpi, pixels

    def _safe_vision_dpi(self, page: _StructuralPage) -> tuple[int, int]:
        if isinstance(self._ocr_dpi, bool) or not isinstance(self._ocr_dpi, int) or self._ocr_dpi < 72:
            raise DocumentIntegrityError("VISION_DPI_CONFIG_INVALID")
        return self._safe_ocr_dpi(page)

    def _reserve_ocr_budget(self, budget: ImportPreflightBudget, estimated_pixels: int) -> None:
        self._ensure_deadline(budget)
        if budget.temporary_bytes >= self._max_temp_bytes:
            raise DocumentIntegrityError("OCR_IMPORT_TEMP_BYTES_LIMIT_EXCEEDED")
        if budget.ocr_pages + 1 > self._max_ocr_pages_per_import:
            raise DocumentIntegrityError("OCR_IMPORT_PAGE_LIMIT_EXCEEDED")
        if max(budget.estimated_pixels, budget.rendered_pixels) + estimated_pixels > self._max_pixels_per_import:
            raise DocumentIntegrityError("OCR_IMPORT_PIXEL_LIMIT_EXCEEDED")
        budget.ocr_pages += 1
        budget.estimated_pixels += estimated_pixels

    def _reserve_vision_budget(self, budget: ImportPreflightBudget, estimated_pixels: int) -> None:
        """Reserve shared rendering resources without spending an OCR page."""
        self._ensure_deadline(budget)
        if budget.temporary_bytes >= self._max_temp_bytes:
            raise DocumentIntegrityError("VISION_IMPORT_TEMP_BYTES_LIMIT_EXCEEDED")
        if (
            max(budget.estimated_pixels, budget.rendered_pixels) + estimated_pixels
            > self._max_pixels_per_import
        ):
            raise DocumentIntegrityError("VISION_IMPORT_PIXEL_LIMIT_EXCEEDED")
        budget.estimated_pixels += estimated_pixels

    def _account_actual_vision_pixels(
        self, budget: ImportPreflightBudget, rendered_pixels: int
    ) -> None:
        rendered = _nonnegative_int(rendered_pixels)
        if rendered > self._max_pixels_per_page:
            raise DocumentIntegrityError("VISION_RENDERED_PIXELS_LIMIT_EXCEEDED")
        budget.rendered_pixels += rendered
        if budget.rendered_pixels > self._max_pixels_per_import:
            raise DocumentIntegrityError("VISION_IMPORT_RENDERED_PIXEL_LIMIT_EXCEEDED")

    def _account_vision_temp_bytes(self, budget: ImportPreflightBudget, value: int) -> None:
        temporary_bytes = _nonnegative_int(value)
        budget.temporary_bytes += temporary_bytes
        if budget.temporary_bytes > self._max_temp_bytes:
            raise DocumentIntegrityError("VISION_IMPORT_TEMP_BYTES_LIMIT_EXCEEDED")

    def _vision_operation_timeout(self, budget: ImportPreflightBudget) -> int | float:
        remaining = budget.started_at + self._deadline_seconds - self._monotonic()
        if remaining <= 0:
            raise DocumentIntegrityError("PREFLIGHT_DEADLINE_EXCEEDED")
        return min(self._vision_timeout_seconds, remaining)

    def _account_actual_ocr_resources(
        self, budget: ImportPreflightBudget, estimated_pixels: int
    ) -> None:
        temporary_bytes = _nonnegative_int(getattr(self._ocr_engine, "last_temp_bytes", 0))
        budget.temporary_bytes += temporary_bytes
        if budget.temporary_bytes > self._max_temp_bytes:
            raise DocumentIntegrityError("OCR_IMPORT_TEMP_BYTES_LIMIT_EXCEEDED")

        rendered_value = getattr(self._ocr_engine, "last_rendered_pixels", None)
        rendered_pixels = (
            estimated_pixels if rendered_value is None else _nonnegative_int(rendered_value)
        )
        if rendered_pixels > self._max_pixels_per_page:
            raise DocumentIntegrityError("OCR_RENDERED_PIXELS_LIMIT_EXCEEDED")
        budget.rendered_pixels += rendered_pixels
        if budget.rendered_pixels > self._max_pixels_per_import:
            raise DocumentIntegrityError("OCR_IMPORT_RENDERED_PIXEL_LIMIT_EXCEEDED")

    def _ensure_deadline(self, budget: ImportPreflightBudget) -> None:
        if self._monotonic() - budget.started_at > self._deadline_seconds:
            raise DocumentIntegrityError("PREFLIGHT_DEADLINE_EXCEEDED")


def _reject_unsafe_pdf_objects(root: object) -> None:
    """Traverse dictionaries/arrays including actions' `/Next` chains."""
    import pikepdf

    visited: set[tuple[object, ...]] = set()

    def walk(value: object) -> None:
        if isinstance(value, (pikepdf.Dictionary, pikepdf.Array)):
            object_id = getattr(value, "objgen", None)
            # Inline PDF objects report ``(0, 0)``.  They are distinct values,
            # not one shared indirect object, so deduplicating all of them
            # would skip nested annotation and action dictionaries.
            key = (
                ("object", *object_id)
                if object_id and object_id != (0, 0)
                else ("memory", id(value))
            )
            if key in visited:
                return
            visited.add(key)

        if isinstance(value, pikepdf.Dictionary):
            for name, child in value.items():
                name_text = str(name)
                if name_text in {"/EmbeddedFiles", "/EF", "/AF"}:
                    raise DocumentIntegrityError("PDF_EMBEDDED_FILES_FORBIDDEN")
                if name_text == "/Subtype" and str(child) == "/FileAttachment":
                    raise DocumentIntegrityError("PDF_EMBEDDED_FILES_FORBIDDEN")
                if name_text == "/Type" and str(child) == "/EmbeddedFile":
                    raise DocumentIntegrityError("PDF_EMBEDDED_FILES_FORBIDDEN")
                if name_text in {"/JavaScript", "/OpenAction", "/AA"}:
                    raise DocumentIntegrityError("PDF_ACTIVE_CONTENT_FORBIDDEN")
                if name_text == "/S" and str(child) in _ACTIVE_ACTIONS:
                    raise DocumentIntegrityError("PDF_ACTIVE_CONTENT_FORBIDDEN")
                walk(child)
        elif isinstance(value, pikepdf.Array):
            for child in value:
                walk(child)

    walk(root)


def _non_whitespace_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _pixel_count(width_points: float, height_points: float, dpi: int) -> int:
    width = math.ceil(width_points * dpi / 72)
    height = math.ceil(height_points * dpi / 72)
    return width * height


def _directory_size(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def _page_user_unit(page: object) -> float:
    """Read an inheritable `/UserUnit` and reject invalid scaling factors."""
    import pikepdf

    seen: set[tuple[int, int]] = set()
    current = page
    while isinstance(current, pikepdf.Dictionary):
        objgen = getattr(current, "objgen", None)
        if objgen and objgen in seen:
            break
        if objgen:
            seen.add(objgen)
        if "/UserUnit" in current:
            user_unit = float(current["/UserUnit"])
            if not math.isfinite(user_unit) or user_unit < 1:
                raise DocumentIntegrityError("PDF_UNREADABLE")
            return user_unit
        current = current.get("/Parent")
    return 1.0


def _nonnegative_int(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise DocumentIntegrityError("OCR_RESOURCE_ACCOUNTING_INVALID") from error
    if parsed < 0:
        raise DocumentIntegrityError("OCR_RESOURCE_ACCOUNTING_INVALID")
    return parsed
