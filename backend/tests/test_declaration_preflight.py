from __future__ import annotations

import hashlib
import io
from dataclasses import FrozenInstanceError
from pathlib import Path

import pikepdf
import pytest
from PIL import Image
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPopplerTimeoutError
import pdf2image
import pytesseract

from app.core.config import Settings
from app.declarations.preflight import (
    DocumentIntegrityError,
    OcrUnavailableError,
    PdfPreflight,
    TesseractOcrEngine,
)
from tests.declaration_fixtures import make_text_pdf, mutate_pdf


def _integrity(data: bytes) -> dict[str, int | str]:
    return {
        "expected_sha256": hashlib.sha256(data).hexdigest(),
        "expected_size": len(data),
    }


class RecordingOcrEngine:
    def __init__(self, text: str = "erkannter OCR-Text") -> None:
        self.calls: list[int] = []
        self.text = text

    def extract_page(self, pdf_bytes: bytes, page_number: int) -> str:
        self.calls.append(page_number)
        return self.text


class UnavailableOcrEngine:
    def __init__(self) -> None:
        self.calls = 0

    def extract_page(self, pdf_bytes: bytes, page_number: int) -> str:
        self.calls += 1
        raise OcrUnavailableError("tesseract is unavailable")


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class AdvancingOcrEngine(RecordingOcrEngine):
    def __init__(self, clock: FakeClock, seconds_per_page: float) -> None:
        super().__init__()
        self._clock = clock
        self._seconds_per_page = seconds_per_page

    def extract_page(self, pdf_bytes: bytes, page_number: int) -> str:
        text = super().extract_page(pdf_bytes, page_number)
        self._clock.value += self._seconds_per_page
        return text


class TempReportingOcrEngine(RecordingOcrEngine):
    def __init__(self, temp_bytes: int) -> None:
        super().__init__()
        self.last_temp_bytes = temp_bytes


class RenderedPixelReportingOcrEngine(RecordingOcrEngine):
    def __init__(self, rendered_pixels: int) -> None:
        super().__init__()
        self.last_rendered_pixels = rendered_pixels


def test_extracts_text_per_page_without_ocr() -> None:
    data = make_text_pdf(
        "Seite eins mit ausreichend langem Text fuer die Textschicht-Erkennung.",
        "Seite zwei mit ebenfalls ausreichend langem Text fuer die Erkennung.",
    )

    result = PdfPreflight().run_bytes(data, **_integrity(data))

    assert result.page_count == 2
    assert "Seite eins" in result.pages[0].text
    assert result.pages[0].source == "TEXT_LAYER"
    assert result.pages[1].page_number == 2
    with pytest.raises(FrozenInstanceError):
        result.pages[0].text = "verändert"  # type: ignore[misc]


def test_hash_mismatch_stops_before_pdf_safety_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    data = make_text_pdf("Beleg")
    parser_was_called = False

    def parser_must_not_run(*args: object, **kwargs: object) -> None:
        nonlocal parser_was_called
        parser_was_called = True
        raise AssertionError("PDF parser must not run")

    monkeypatch.setattr(PdfPreflight, "_validate_pdf_safety", parser_must_not_run)

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight().run_bytes(data, expected_sha256="0" * 64, expected_size=len(data))

    assert error.value.code == "STORAGE_INTEGRITY_MISMATCH"
    assert parser_was_called is False


def test_size_mismatch_stops_before_pdf_safety_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    data = make_text_pdf("Beleg")
    monkeypatch.setattr(
        PdfPreflight,
        "_validate_pdf_safety",
        lambda *args, **kwargs: pytest.fail("PDF parser must not run"),
    )

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight().run_bytes(data, expected_sha256=hashlib.sha256(data).hexdigest(), expected_size=1)

    assert error.value.code == "STORAGE_INTEGRITY_MISMATCH"


def test_page_limit_is_enforced_before_text_or_ocr() -> None:
    data = make_text_pdf("1", "2")
    engine = RecordingOcrEngine()

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight(max_pages=1, ocr_engine=engine).run_bytes(data, **_integrity(data))

    assert error.value.code == "PDF_PAGE_LIMIT_EXCEEDED"
    assert engine.calls == []


def test_pdf_zip_polyglot_is_rejected_before_ocr() -> None:
    data = make_text_pdf("Beleg") + b"PK\x03\x04foreign-payload"
    engine = RecordingOcrEngine()

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight(ocr_engine=engine).run_bytes(data, **_integrity(data))

    assert error.value.code == "PDF_TRAILING_PAYLOAD"
    assert engine.calls == []


def test_pdf_eof_allows_pdf_whitespace_and_nul() -> None:
    original = make_text_pdf("ausreichend langer Text fuer eine erlaubte Endmarkierung")
    data = original + b"\r\n\x00 \t"

    result = PdfPreflight().run_bytes(data, **_integrity(data))

    assert result.page_count == 1


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda pdf: pdf.Root.__setitem__(
                "/Names",
                pikepdf.Dictionary(EmbeddedFiles=pikepdf.Dictionary(Names=pikepdf.Array())),
            ),
            "PDF_EMBEDDED_FILES_FORBIDDEN",
        ),
        (
            lambda pdf: pdf.Root.__setitem__(
                "/Names",
                pikepdf.Dictionary(
                    JavaScript=pikepdf.Dictionary(
                        Names=pikepdf.Array(
                            ["script", pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS="app.alert('x')")]
                        )
                    )
                ),
            ),
            "PDF_ACTIVE_CONTENT_FORBIDDEN",
        ),
        (
            lambda pdf: pdf.Root.__setitem__(
                "/OpenAction",
                pikepdf.Dictionary(S=pikepdf.Name.URI, URI="https://example.invalid"),
            ),
            "PDF_ACTIVE_CONTENT_FORBIDDEN",
        ),
        (
            lambda pdf: pdf.pages[0].obj.__setitem__(
                "/Annots",
                pikepdf.Array(
                    [
                        pikepdf.Dictionary(
                            Subtype=pikepdf.Name.Annot,
                            Rect=pikepdf.Array([0, 0, 10, 10]),
                            A=pikepdf.Dictionary(S=pikepdf.Name.Launch, F="/tmp/never-run"),
                        )
                    ]
                ),
            ),
            "PDF_ACTIVE_CONTENT_FORBIDDEN",
        ),
        (
            lambda pdf: pdf.Root.__setitem__(
                "/AA",
                pikepdf.Dictionary(
                    WC=pikepdf.Dictionary(
                        S=pikepdf.Name.SubmitForm,
                        F="https://example.invalid",
                    )
                ),
            ),
            "PDF_ACTIVE_CONTENT_FORBIDDEN",
        ),
        (
            lambda pdf: pdf.Root.__setitem__(
                "/OpenAction",
                pikepdf.Dictionary(
                    S=pikepdf.Name.GoTo,
                    Next=pikepdf.Dictionary(S=pikepdf.Name.ImportData, F="/tmp/data"),
                ),
            ),
            "PDF_ACTIVE_CONTENT_FORBIDDEN",
        ),
    ],
)
def test_unsafe_pdf_objects_are_rejected_before_ocr(
    mutate: object, expected_code: str
) -> None:
    data = mutate_pdf(make_text_pdf("kurz"), mutate)  # type: ignore[arg-type]
    engine = RecordingOcrEngine()

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight(ocr_engine=engine).run_bytes(data, **_integrity(data))

    assert error.value.code == expected_code
    assert engine.calls == []


def test_file_attachment_annotation_is_rejected_before_ocr() -> None:
    def add_attachment(pdf: pikepdf.Pdf) -> None:
        embedded_file = pikepdf.Stream(pdf, b"untrusted attachment")
        embedded_file["/Type"] = pikepdf.Name.EmbeddedFile
        file_spec = pikepdf.Dictionary(
            Type=pikepdf.Name.Filespec,
            F="attachment.bin",
            EF=pikepdf.Dictionary(F=embedded_file),
        )
        pdf.pages[0].obj["/Annots"] = pikepdf.Array(
            [
                pikepdf.Dictionary(
                    Subtype=pikepdf.Name.FileAttachment,
                    Rect=pikepdf.Array([0, 0, 10, 10]),
                    FS=file_spec,
                )
            ]
        )

    data = mutate_pdf(make_text_pdf("kurz"), add_attachment)
    engine = RecordingOcrEngine()

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight(ocr_engine=engine).run_bytes(data, **_integrity(data))

    assert error.value.code == "PDF_EMBEDDED_FILES_FORBIDDEN"
    assert engine.calls == []


def test_large_media_box_is_rejected_before_ocr() -> None:
    data = mutate_pdf(
        make_text_pdf("kurz"),
        lambda pdf: pdf.pages[0].obj.__setitem__(
            "/MediaBox", pikepdf.Array([0, 0, 10_000_000, 10_000_000])
        ),
    )
    engine = RecordingOcrEngine()

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight(
            ocr_engine=engine,
            max_pixels_per_page=20_000_000,
        ).run_bytes(data, **_integrity(data))

    assert error.value.code == "OCR_PAGE_DIMENSIONS_UNSAFE"
    assert engine.calls == []


def test_large_media_box_lowers_default_tesseract_dpi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = mutate_pdf(
        make_text_pdf("kurz"),
        lambda pdf: pdf.pages[0].obj.__setitem__(
            "/MediaBox", pikepdf.Array([0, 0, 1_000, 1_000])
        ),
    )
    engine = TesseractOcrEngine()
    rendered_dpis: list[int] = []

    def render(
        pdf_bytes: bytes, page_number: int, dpi: int, output_dir: str, **kwargs: object
    ) -> str:
        rendered_dpis.append(dpi)
        return "OCR"

    monkeypatch.setattr(engine, "_render_and_recognize", render)
    result = PdfPreflight(
        ocr_engine=engine,
        max_pixels_per_page=1_100_000,
    ).run_bytes(data, **_integrity(data))

    assert result.pages[0].source == "OCR"
    assert rendered_dpis == [75]


def test_user_unit_is_included_in_ocr_dimension_guard() -> None:
    data = mutate_pdf(
        make_text_pdf("kurz"),
        lambda pdf: pdf.pages[0].obj.__setitem__("/UserUnit", 100),
    )
    engine = RecordingOcrEngine()

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight(ocr_engine=engine).run_bytes(data, **_integrity(data))

    assert error.value.code == "OCR_PAGE_DIMENSIONS_UNSAFE"
    assert engine.calls == []


def test_encrypted_pdf_is_rejected_before_ocr() -> None:
    encrypted = io.BytesIO()
    with pikepdf.open(io.BytesIO(make_text_pdf("kurz")), attempt_recovery=False) as pdf:
        pdf.save(encrypted, encryption=pikepdf.Encryption(owner="owner", user="user", R=4))
    data = encrypted.getvalue()
    engine = RecordingOcrEngine()

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight(ocr_engine=engine).run_bytes(data, **_integrity(data))

    assert error.value.code == "PDF_ENCRYPTED_FORBIDDEN"
    assert engine.calls == []


def test_import_ocr_page_budget_stops_before_next_renderer() -> None:
    first = make_text_pdf("kurz")
    second = make_text_pdf("auch kurz")
    engine = RecordingOcrEngine()
    preflight = PdfPreflight(ocr_engine=engine, max_ocr_pages_per_import=1)
    budget = preflight.new_import_budget()

    preflight.run_bytes(first, budget=budget, **_integrity(first))
    with pytest.raises(DocumentIntegrityError) as error:
        preflight.run_bytes(second, budget=budget, **_integrity(second))

    assert error.value.code == "OCR_IMPORT_PAGE_LIMIT_EXCEEDED"
    assert engine.calls == [1]


def test_import_pixel_budget_stops_before_next_renderer() -> None:
    first = make_text_pdf("kurz")
    second = make_text_pdf("auch kurz")
    engine = RecordingOcrEngine()
    preflight = PdfPreflight(
        ocr_engine=engine,
        max_pixels_per_import=4_000_000,
    )
    budget = preflight.new_import_budget()

    preflight.run_bytes(first, budget=budget, **_integrity(first))
    with pytest.raises(DocumentIntegrityError) as error:
        preflight.run_bytes(second, budget=budget, **_integrity(second))

    assert error.value.code == "OCR_IMPORT_PIXEL_LIMIT_EXCEEDED"
    assert engine.calls == [1]


def test_import_deadline_stops_before_next_renderer() -> None:
    data = make_text_pdf("kurz", "auch kurz")
    clock = FakeClock()
    engine = AdvancingOcrEngine(clock, seconds_per_page=11)

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight(
            ocr_engine=engine,
            deadline_seconds=10,
            monotonic=clock,
        ).run_bytes(data, **_integrity(data))

    assert error.value.code == "PREFLIGHT_DEADLINE_EXCEEDED"
    assert engine.calls == [1]


def test_import_deadline_is_enforced_after_final_ocr_attempt() -> None:
    data = make_text_pdf("kurz")
    clock = FakeClock()
    engine = AdvancingOcrEngine(clock, seconds_per_page=11)

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight(
            ocr_engine=engine,
            deadline_seconds=10,
            monotonic=clock,
        ).run_bytes(data, **_integrity(data))

    assert error.value.code == "PREFLIGHT_DEADLINE_EXCEEDED"
    assert engine.calls == [1]


def test_import_temp_budget_stops_before_next_renderer_at_cap() -> None:
    first = make_text_pdf("kurz")
    second = make_text_pdf("auch kurz")
    engine = TempReportingOcrEngine(temp_bytes=10)
    preflight = PdfPreflight(ocr_engine=engine, max_temp_bytes=10)
    budget = preflight.new_import_budget()

    preflight.run_bytes(first, budget=budget, **_integrity(first))
    with pytest.raises(DocumentIntegrityError) as error:
        preflight.run_bytes(second, budget=budget, **_integrity(second))

    assert error.value.code == "OCR_IMPORT_TEMP_BYTES_LIMIT_EXCEEDED"
    assert engine.calls == [1]


def test_import_temp_budget_rejects_after_actual_usage_exceeds_cap() -> None:
    first = make_text_pdf("kurz")
    second = make_text_pdf("auch kurz")
    engine = TempReportingOcrEngine(temp_bytes=6)
    preflight = PdfPreflight(ocr_engine=engine, max_temp_bytes=10)
    budget = preflight.new_import_budget()

    preflight.run_bytes(first, budget=budget, **_integrity(first))
    with pytest.raises(DocumentIntegrityError) as error:
        preflight.run_bytes(second, budget=budget, **_integrity(second))

    assert error.value.code == "OCR_IMPORT_TEMP_BYTES_LIMIT_EXCEEDED"
    assert engine.calls == [1, 1]
    assert budget.temporary_bytes == 12


def test_actual_rendered_pixels_fail_closed_before_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    data = make_text_pdf("kurz")
    engine = TesseractOcrEngine(temporary_root=tmp_path)

    ocr_calls = 0

    def render_oversized(*args: object, **kwargs: object) -> list[str]:
        image_path = Path(str(kwargs["output_folder"])) / "oversized.png"
        Image.new("RGB", (2_000, 2_000)).save(image_path)
        return [str(image_path)]

    def fail_if_ocr_called(*args: object, **kwargs: object) -> str:
        nonlocal ocr_calls
        ocr_calls += 1
        raise AssertionError("Tesseract must not run for an oversized raster")

    monkeypatch.setattr(pdf2image, "convert_from_bytes", render_oversized)
    monkeypatch.setattr(pytesseract, "image_to_string", fail_if_ocr_called)

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight(
            ocr_engine=engine,
            ocr_dpi=72,
            max_pixels_per_page=1_000_000,
        ).run_bytes(data, **_integrity(data))

    assert error.value.code == "OCR_RENDERED_PIXELS_LIMIT_EXCEEDED"
    assert engine.last_rendered_pixels == 4_000_000
    assert ocr_calls == 0


def test_actual_rendered_pixels_reduce_remaining_import_capacity(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    first = make_text_pdf("kurz")
    second = make_text_pdf("auch kurz")
    engine = TesseractOcrEngine(temporary_root=tmp_path)
    rendered_pages: list[int] = []

    def render_larger_than_estimate(*args: object, **kwargs: object) -> list[str]:
        rendered_pages.append(1)
        image_path = Path(str(kwargs["output_folder"])) / "rendered.png"
        Image.new("RGB", (800, 800)).save(image_path)
        return [str(image_path)]

    monkeypatch.setattr(pdf2image, "convert_from_bytes", render_larger_than_estimate)
    monkeypatch.setattr(pytesseract, "image_to_string", lambda *args, **kwargs: "OCR")
    preflight = PdfPreflight(
        ocr_engine=engine,
        ocr_dpi=72,
        max_pixels_per_page=1_000_000,
        max_pixels_per_import=1_000_000,
    )
    budget = preflight.new_import_budget()

    preflight.run_bytes(first, budget=budget, **_integrity(first))
    with pytest.raises(DocumentIntegrityError) as error:
        preflight.run_bytes(second, budget=budget, **_integrity(second))

    assert error.value.code == "OCR_IMPORT_PIXEL_LIMIT_EXCEEDED"
    assert budget.rendered_pixels == 640_000
    assert rendered_pages == [1]


@pytest.mark.parametrize(
    ("exception", "issue"),
    [
        (PDFPopplerTimeoutError("timeout"), "OCR_TIMEOUT"),
        (RuntimeError("Tesseract process timeout"), "OCR_TIMEOUT"),
        (PDFInfoNotInstalledError("missing poppler"), "OCR_UNAVAILABLE"),
    ],
)
def test_real_ocr_runtime_exceptions_map_to_stable_page_issues(
    monkeypatch: pytest.MonkeyPatch,
    exception: Exception,
    issue: str,
) -> None:
    data = make_text_pdf("kurz")
    engine = TesseractOcrEngine()

    if isinstance(exception, RuntimeError):
        def render_one_pixel(*args: object, **kwargs: object) -> list[str]:
            image_path = Path(str(kwargs["output_folder"])) / "one-pixel.png"
            Image.new("RGB", (1, 1)).save(image_path)
            return [str(image_path)]

        monkeypatch.setattr(pdf2image, "convert_from_bytes", render_one_pixel)
        monkeypatch.setattr(pytesseract, "image_to_string", lambda *args, **kwargs: (_ for _ in ()).throw(exception))
    else:
        monkeypatch.setattr(pdf2image, "convert_from_bytes", lambda *args, **kwargs: (_ for _ in ()).throw(exception))

    result = PdfPreflight(ocr_engine=engine).run_bytes(data, **_integrity(data))

    assert result.pages[0].issues == (issue,)


def test_ocr_unavailable_becomes_explicit_page_issue() -> None:
    data = make_text_pdf("kurz")
    engine = UnavailableOcrEngine()

    result = PdfPreflight(ocr_engine=engine).run_bytes(data, **_integrity(data))

    assert result.pages[0].source == "UNAVAILABLE"
    assert result.pages[0].issues == ("OCR_UNAVAILABLE",)
    assert engine.calls == 1


def test_broken_pdf_has_stable_error_without_provider_invocation() -> None:
    engine = RecordingOcrEngine()

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight(ocr_engine=engine).run_bytes(
            b"%PDF-broken",
            expected_sha256=hashlib.sha256(b"%PDF-broken").hexdigest(),
            expected_size=len(b"%PDF-broken"),
        )

    assert error.value.code == "PDF_UNREADABLE"
    assert engine.calls == []


def test_import_deadline_covers_text_layer_preflight() -> None:
    data = make_text_pdf("Dieser Text ist bewusst lang genug fuer die Textschicht-Erkennung.")
    clock = FakeClock()
    preflight = PdfPreflight(deadline_seconds=10, monotonic=clock)
    budget = preflight.new_import_budget()
    clock.value = 11

    with pytest.raises(DocumentIntegrityError) as error:
        preflight.run_bytes(data, budget=budget, **_integrity(data))

    assert error.value.code == "PREFLIGHT_DEADLINE_EXCEEDED"


def test_import_deadline_is_checked_after_text_layer_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = make_text_pdf("Dieser Text ist bewusst lang genug fuer die Textschicht-Erkennung.")
    clock = FakeClock()
    preflight = PdfPreflight(deadline_seconds=10, monotonic=clock)
    budget = preflight.new_import_budget()

    def extract_slowly(pdf_bytes: bytes) -> tuple[list[str], list[int]]:
        clock.value = 11
        return (["Dieser Text ist bewusst lang genug fuer die Textschicht-Erkennung."], [0])

    monkeypatch.setattr(PdfPreflight, "_extract_text_layer", staticmethod(extract_slowly))
    with pytest.raises(DocumentIntegrityError) as error:
        preflight.run_bytes(data, budget=budget, **_integrity(data))

    assert error.value.code == "PREFLIGHT_DEADLINE_EXCEEDED"


def test_tesseract_temp_byte_limit_is_enforced_when_renderer_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    data = make_text_pdf("kurz")
    root = tmp_path / "ocr-temp"
    root.mkdir()
    engine = TesseractOcrEngine(timeout_seconds=1, max_temp_bytes=1, temporary_root=root)

    def render(
        pdf_bytes: bytes, page_number: int, dpi: int, output_dir: str, **kwargs: object
    ) -> str:
        (Path(output_dir) / "oversized.png").write_bytes(b"too large")
        raise RuntimeError("renderer failed")

    monkeypatch.setattr(engine, "_render_and_recognize", render)
    with pytest.raises(DocumentIntegrityError) as error:
        engine.extract_page(data, 1)

    assert error.value.code == "OCR_TEMP_BYTES_LIMIT_EXCEEDED"
    assert list(root.iterdir()) == []


def test_settings_have_declaration_preflight_defaults() -> None:
    settings = Settings()

    assert settings.declaration_import_enabled is False
    assert settings.declaration_max_pdf_bytes == 25 * 1024 * 1024
    assert settings.declaration_max_documents == 10
    assert settings.declaration_max_pages == 500
    assert settings.declaration_ocr_dpi == 180
    assert settings.declaration_ocr_timeout_seconds == 30
    assert settings.declaration_text_min_chars == 40
    assert settings.declaration_ocr_max_pixels_per_page == 20_000_000
    assert settings.declaration_ocr_max_temp_bytes == 256 * 1024 * 1024
    assert settings.declaration_ocr_max_pages_per_import == 200
    assert settings.declaration_ocr_max_pixels_per_import == 400_000_000
    assert settings.declaration_preflight_deadline_seconds == 600


def test_tesseract_engine_removes_page_temp_directory_after_success_timeout_and_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    data = make_text_pdf("kurz")
    root = tmp_path / "ocr-temp"
    root.mkdir()
    engine = TesseractOcrEngine(timeout_seconds=1, temporary_root=root)

    # The engine's renderer is deliberately injectable only at construction time;
    # each branch writes a disposable page file and must leave the root empty.
    for outcome in ("erkannt", TimeoutError("timeout"), RuntimeError("renderer failed")):
        def render(
            pdf_bytes: bytes, page_number: int, dpi: int, output_dir: str, **kwargs: object
        ) -> str:
            page = Path(output_dir) / "placeholder.png"
            page.write_bytes(b"rendered")
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        monkeypatch.setattr(engine, "_render_and_recognize", render)
        if isinstance(outcome, BaseException):
            with pytest.raises(type(outcome)):
                engine.extract_page(data, 1)
        else:
            assert engine.extract_page(data, 1) == outcome
        assert list(root.iterdir()) == []


def test_tesseract_operations_use_remaining_preflight_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    data = make_text_pdf("kurz")
    clock = FakeClock()
    engine = TesseractOcrEngine(timeout_seconds=30, temporary_root=tmp_path)
    render_timeouts: list[float] = []
    ocr_timeouts: list[float] = []

    def render(*args: object, **kwargs: object) -> list[str]:
        render_timeouts.append(float(kwargs["timeout"]))
        image_path = Path(str(kwargs["output_folder"])) / "one-pixel.png"
        Image.new("RGB", (1, 1)).save(image_path)
        clock.value = 9
        return [str(image_path)]

    def recognize(*args: object, **kwargs: object) -> str:
        ocr_timeouts.append(float(kwargs["timeout"]))
        return "OCR"

    monkeypatch.setattr(pdf2image, "convert_from_bytes", render)
    monkeypatch.setattr(pytesseract, "image_to_string", recognize)

    assert (
        engine.extract_page_at_dpi(
            data,
            1,
            72,
            deadline_at=10,
            monotonic=clock,
        )
        == "OCR"
    )
    assert render_timeouts == [10]
    assert ocr_timeouts == [1]


def test_tesseract_does_not_start_renderer_at_exhausted_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    data = make_text_pdf("kurz")
    clock = FakeClock()
    clock.value = 10
    engine = TesseractOcrEngine(timeout_seconds=30, temporary_root=tmp_path)
    calls = 0

    def renderer(*args: object, **kwargs: object) -> list[str]:
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr(pdf2image, "convert_from_bytes", renderer)
    with pytest.raises(TimeoutError):
        engine.extract_page_at_dpi(data, 1, 72, deadline_at=10, monotonic=clock)
    assert calls == 0
