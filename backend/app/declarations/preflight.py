"""Safe, bounded local preflight for uploaded declaration PDFs.

The preflight is deliberately separate from provider extraction.  It verifies
the stored bytes, rejects active PDF content, and produces per-page artifacts
that can safely be handed to a later extraction pipeline. A page without a usable
text layer is not read here: it is marked ``IMAGE`` with the resolution it will be
rendered at, and :meth:`PdfPreflight.render_page_for_reading` renders it one page at a
time for the reading model (issue #499, ADR 0017).
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
from typing import Callable

from app.core.document_limits import MAX_UPLOAD_BYTES


_PDF_TRAILING_WHITESPACE = b" \t\r\n\x0b\x0c\x00"
_ACTIVE_ACTIONS = {
    "/URI",
    "/SubmitForm",
    "/ImportData",
    "/Launch",
    "/JavaScript",
}


# A page without a usable text layer; the reading model reads it (``PdfPreflight.render_page_for_reading``).
IMAGE_PAGE_SOURCE = "IMAGE"
# A page image sent to the reading model has at most this many pixels; a larger render is scaled down, never
# refused. An A4 page (595 x 842 points) at the worker's 180 dpi (1,488 x 2,105 = 3.1 million pixels) passes
# unchanged. The bound keeps the conservative image reservation of the transports that publish no formula (one
# token per 14 x 14 pixel patch, 20,409 tokens at the bound) far below the import input budget, and every
# documented provider resizes beyond roughly this size before it reads the image anyway.
READING_MAX_IMAGE_PIXELS = 4_000_000


class DocumentIntegrityError(ValueError):
    """A stable, user-safe failure encountered before provider processing."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        super().__init__(detail or code)


@dataclass(frozen=True, slots=True)
class PageArtifact:
    page_number: int
    text: str
    source: str
    rotation: int
    issues: tuple[str, ...] = ()
    # The resolution an ``IMAGE`` page is rendered at for the reading model; 0 for a page with a text layer.
    dpi: int = 0


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

    def exclude_wait(self, seconds: float) -> None:
        """Take time spent waiting on the model out of the deadline: it bounds local work, not provider latency."""
        self.started_at += max(0.0, seconds)


@dataclass(frozen=True, slots=True)
class _StructuralPage:
    page_number: int
    width_points: float
    height_points: float


class PdfPreflight:
    """Validate PDFs before text extraction and page reading and enforce local resource caps."""

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
        max_pixels_per_import: int = 800_000_000,
        deadline_seconds: int = 600,
        temporary_root: Path | None = None,
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
        self._temporary_root = temporary_root

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

        This is deliberately independent from the rendering for the reading model:
        a vision recheck is allowed to consume the shared import deadline and pixel
        budget and, for the duration of this one render, the temporary-byte bound (its
        count is released when the render ends, as for the reading render), but it never
        increments the page-reading allowance.  The caller owns ``pdf_bytes`` and must
        retain it only on its stack until this method returns; no image or source bytes
        are retained by preflight.
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
        temp_root = str(self._temporary_root) if self._temporary_root is not None else None
        try:
            from pdf2image import convert_from_bytes
            from pdf2image.exceptions import PDFPopplerTimeoutError, PopplerNotInstalledError
            from PIL import Image
        except ImportError as error:  # pragma: no cover - deployment dependency
            raise DocumentIntegrityError("VISION_RENDER_UNAVAILABLE") from error

        directory_path: Path | None = None
        counted_temp_bytes = 0
        # What this render added to the import's temporary count; it leaves the count when the render ends.
        held = 0

        def hold_temp_bytes(value: int) -> None:
            nonlocal held
            held += _nonnegative_int(value)
            self._account_vision_temp_bytes(budget, value)

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
                hold_temp_bytes(counted_temp_bytes)
                if len(paths) != 1:
                    raise DocumentIntegrityError("VISION_RENDER_UNAVAILABLE")
                with Image.open(paths[0]) as image:
                    rendered_pixels = image.width * image.height
                    self._account_actual_vision_pixels(budget, rendered_pixels)
                    encoded = io.BytesIO()
                    image.save(encoded, format="PNG")
                png = encoded.getvalue()
                value = base64.b64encode(png).decode("ascii")
                hold_temp_bytes(len(png) + len(value))
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
            try:
                if directory_path is not None:
                    try:
                        observed_temp_bytes = _directory_size(directory_path)
                        if observed_temp_bytes > counted_temp_bytes:
                            hold_temp_bytes(observed_temp_bytes - counted_temp_bytes)
                    except DocumentIntegrityError:
                        raise
                    except Exception as error:
                        raise DocumentIntegrityError("VISION_RENDER_UNAVAILABLE") from error
            finally:
                budget.temporary_bytes -= held

    def render_page_for_reading(
        self,
        pdf_bytes: bytes,
        page: PageArtifact,
        *,
        budget: ImportPreflightBudget,
    ) -> bytes:
        """Render exactly one ``IMAGE`` page to PNG bytes for the reading model.

        The page was validated and its count and pixel bounds reserved by :meth:`run_bytes`; this accounts the
        actual rendered pixels against the import's budget and the shared deadline, and the temporary bytes of
        this render against the temporary bound: the count is released when the render's directory is deleted,
        so the bound limits the disk use of one render and not the sum over the pages of the import. No
        image file outlives the call and only this one page is in memory. A render above
        ``READING_MAX_IMAGE_PIXELS`` is scaled down deterministically instead of failing.
        """
        if (
            not isinstance(pdf_bytes, bytes)
            or page.source != IMAGE_PAGE_SOURCE
            or isinstance(page.dpi, bool)
            or not isinstance(page.dpi, int)
            or page.dpi < 1
        ):
            raise DocumentIntegrityError("PAGE_RENDER_UNAVAILABLE")
        self._ensure_deadline(budget)
        if budget.temporary_bytes >= self._max_temp_bytes:
            raise DocumentIntegrityError("OCR_IMPORT_TEMP_BYTES_LIMIT_EXCEEDED")
        try:
            from pdf2image import convert_from_bytes
            from pdf2image.exceptions import PDFPopplerTimeoutError
            from PIL import Image
        except ImportError as error:  # pragma: no cover - depends on deployment image
            raise DocumentIntegrityError("PAGE_RENDER_UNAVAILABLE") from error

        temp_root = str(self._temporary_root) if self._temporary_root is not None else None
        held = 0
        try:
            with tempfile.TemporaryDirectory(dir=temp_root, prefix="hv-declaration-page-") as directory:
                paths = convert_from_bytes(
                    pdf_bytes,
                    dpi=page.dpi,
                    first_page=page.page_number,
                    last_page=page.page_number,
                    thread_count=1,
                    timeout=self._vision_operation_timeout(budget),
                    output_folder=directory,
                    paths_only=True,
                )
                temporary_bytes = _directory_size(Path(directory))
                if temporary_bytes > self._max_temp_bytes:
                    raise DocumentIntegrityError("OCR_TEMP_BYTES_LIMIT_EXCEEDED")
                budget.temporary_bytes += temporary_bytes
                held = temporary_bytes
                if budget.temporary_bytes > self._max_temp_bytes:
                    raise DocumentIntegrityError("OCR_IMPORT_TEMP_BYTES_LIMIT_EXCEEDED")
                if len(paths) != 1:
                    raise DocumentIntegrityError("PAGE_RENDER_UNAVAILABLE")
                with Image.open(paths[0]) as image:
                    rendered_pixels = image.width * image.height
                    if rendered_pixels > self._max_pixels_per_page:
                        raise DocumentIntegrityError("OCR_RENDERED_PIXELS_LIMIT_EXCEEDED")
                    budget.rendered_pixels += rendered_pixels
                    if budget.rendered_pixels > self._max_pixels_per_import:
                        raise DocumentIntegrityError("OCR_IMPORT_RENDERED_PIXEL_LIMIT_EXCEEDED")
                    encoded = io.BytesIO()
                    _fit_pixels(image, READING_MAX_IMAGE_PIXELS).save(encoded, format="PNG")
        except DocumentIntegrityError:
            raise
        except (PDFPopplerTimeoutError, TimeoutError, subprocess.TimeoutExpired) as error:
            raise DocumentIntegrityError("PAGE_RENDER_TIMEOUT") from error
        except Exception as error:
            # Poppler missing, an unreadable render or a full disk: one stable code, no detail.
            raise DocumentIntegrityError("PAGE_RENDER_UNAVAILABLE") from error
        finally:
            # The render's directory is gone: its bytes no longer occupy the disk and leave the count.
            budget.temporary_bytes -= held
        self._ensure_deadline(budget)
        return encoded.getvalue()

    def render_image_for_reading(self, image_bytes: bytes, *, budget: ImportPreflightBudget) -> bytes:
        """The PNG of an uploaded PNG or JPEG original for the reading model.

        The image was inspected in an isolated process already (one frame, at most 20 million pixels, fully decodable);
        the pixel bound is checked here before the image is decoded, again from its header, and the deadline of the
        shared budget applies. The rotation of the EXIF header is applied, transparency is flattened on white and a
        picture above ``READING_MAX_IMAGE_PIXELS`` is scaled down deterministically, as a rendered page is.
        """
        if not isinstance(image_bytes, bytes):
            raise DocumentIntegrityError("PAGE_RENDER_UNAVAILABLE")
        self._ensure_deadline(budget)
        try:
            from PIL import Image, ImageOps
        except ImportError as error:  # pragma: no cover - depends on deployment image
            raise DocumentIntegrityError("PAGE_RENDER_UNAVAILABLE") from error
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                if image.width * image.height > self._max_pixels_per_page:
                    raise DocumentIntegrityError("OCR_RENDERED_PIXELS_LIMIT_EXCEEDED")
                image.load()
                upright = ImageOps.exif_transpose(image)
                if upright.mode in ("RGBA", "LA") or (upright.mode == "P" and "transparency" in upright.info):
                    flattened = upright.convert("RGBA")
                    page = Image.new("RGB", flattened.size, (255, 255, 255))
                    page.paste(flattened, mask=flattened.getchannel("A"))
                elif upright.mode in ("RGB", "L"):
                    page = upright
                else:
                    page = upright.convert("RGB")
                encoded = io.BytesIO()
                _fit_pixels(page, READING_MAX_IMAGE_PIXELS).save(encoded, format="PNG")
        except DocumentIntegrityError:
            raise
        except Exception as error:
            # An image that inspection accepted and that cannot be decoded now: one stable code, no detail.
            raise DocumentIntegrityError("PAGE_RENDER_UNAVAILABLE") from error
        self._ensure_deadline(budget)
        return encoded.getvalue()

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
        one import to carry the page-reading resource accounting across the batch.
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

            # No text layer: the page is read later, one rendered page at a time. The page count and pixel
            # bounds are reserved now, so that an import over them stops before the first model call.
            dpi, estimated_pixels = self._safe_ocr_dpi(structural_page)
            self._reserve_ocr_budget(active_budget, estimated_pixels)
            pages.append(
                PageArtifact(
                    page_number=structural_page.page_number,
                    text="",
                    source=IMAGE_PAGE_SOURCE,
                    rotation=rotation,
                    dpi=dpi,
                )
            )
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
        """Reserve shared rendering resources without spending a page of the reading allowance."""
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


def _fit_pixels(image, max_pixels: int):
    """The image itself when it has at most ``max_pixels``, else the same picture scaled down to fit them."""
    pixels = image.width * image.height
    if pixels <= max_pixels:
        return image
    from PIL import Image

    scale = math.sqrt(max_pixels / pixels)
    size = (max(1, math.floor(image.width * scale)), max(1, math.floor(image.height * scale)))
    return image.resize(size, Image.LANCZOS)


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
