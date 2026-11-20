from __future__ import annotations

import hashlib
import io
import base64
from dataclasses import FrozenInstanceError
from pathlib import Path

import pikepdf
import pytest
from PIL import Image
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPopplerTimeoutError
import pdf2image

from app.core.config import Settings
from app.declarations.preflight import (
    READING_MAX_IMAGE_PIXELS,
    DocumentIntegrityError,
    PageArtifact,
    PdfPreflight,
)
from tests.declaration_fixtures import make_text_pdf, mutate_pdf


def _integrity(data: bytes) -> dict[str, int | str]:
    return {
        "expected_sha256": hashlib.sha256(data).hexdigest(),
        "expected_size": len(data),
    }


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class Renderer:
    """Poppler stand-in: records each rendered page and writes the image the test chose."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.size = (1, 1)
        self.extra_bytes = 0
        self.fail: Exception | None = None
        self.after = None

    def __call__(self, pdf_bytes: bytes, **kwargs: object) -> list[str]:
        self.calls.append(kwargs)
        folder = Path(str(kwargs["output_folder"]))
        if self.extra_bytes:
            (folder / "extra.bin").write_bytes(b"x" * self.extra_bytes)
        if self.fail is not None:
            raise self.fail
        path = folder / "page.png"
        Image.new("RGB", self.size).save(path)
        if self.after is not None:
            self.after()
        return [str(path)]


@pytest.fixture
def renderer(monkeypatch: pytest.MonkeyPatch) -> Renderer:
    value = Renderer()
    monkeypatch.setattr(pdf2image, "convert_from_bytes", value)
    return value


def _image_page(preflight: PdfPreflight, data: bytes, budget=None) -> PageArtifact:
    """The first page of a scan: marked for reading by the preflight, not yet rendered."""
    result = preflight.run_bytes(data, budget=budget, **_integrity(data))
    assert result.pages[0].source == "IMAGE"
    return result.pages[0]


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


def test_render_page_data_url_renders_exact_safe_page_as_png() -> None:
    data = make_text_pdf(
        "Seite eins mit ausreichend langem Text fuer eine sichere Rasterisierung.",
        "Seite zwei mit ausreichend langem Text fuer eine sichere Rasterisierung.",
    )
    preflight = PdfPreflight()
    budget = preflight.new_import_budget()

    value = preflight.render_page_data_url(data, 2, budget=budget)

    assert value.startswith("data:image/png;base64,")
    encoded = value.removeprefix("data:image/png;base64,")
    decoded = base64.b64decode(encoded, validate=True)
    assert decoded.startswith(b"\x89PNG\r\n\x1a\n")
    assert budget.ocr_pages == 0
    assert budget.rendered_pixels > 0
    assert budget.temporary_bytes >= len(decoded) + len(encoded)


def test_render_page_data_url_rejects_out_of_range_page_before_poppler() -> None:
    data = make_text_pdf("Sichere Textseite für den Seitenbereichstest.")
    preflight = PdfPreflight()

    with pytest.raises(DocumentIntegrityError) as error:
        preflight.render_page_data_url(data, 2, budget=preflight.new_import_budget())

    assert error.value.code == "PDF_PAGE_OUT_OF_RANGE"


def test_render_page_data_url_counts_encoded_bytes_and_cleans_its_temp_directory(tmp_path: Path) -> None:
    data = make_text_pdf("Sichere Textseite für den temporären Renderpfad.")
    preflight = PdfPreflight(temporary_root=tmp_path)
    budget = preflight.new_import_budget()

    preflight.render_page_data_url(data, 1, budget=budget)

    assert budget.temporary_bytes > 0
    assert list(tmp_path.iterdir()) == []


def test_render_page_data_url_fails_closed_when_shared_deadline_has_elapsed() -> None:
    clock = FakeClock()
    data = make_text_pdf("Sichere Textseite für den Deadline-Test.")
    preflight = PdfPreflight(monotonic=clock, deadline_seconds=1)
    budget = preflight.new_import_budget()
    clock.value = 2

    with pytest.raises(DocumentIntegrityError) as error:
        preflight.render_page_data_url(data, 1, budget=budget)

    assert error.value.code == "PREFLIGHT_DEADLINE_EXCEEDED"


def test_vision_render_timeout_is_capped_by_configured_ocr_timeout() -> None:
    clock = FakeClock()
    preflight = PdfPreflight(monotonic=clock, deadline_seconds=600, ocr_timeout_seconds=30)
    budget = preflight.new_import_budget()

    assert preflight._vision_operation_timeout(budget) == 30
    clock.value = 601
    with pytest.raises(DocumentIntegrityError) as error:
        preflight._vision_operation_timeout(budget)
    assert error.value.code == "PREFLIGHT_DEADLINE_EXCEEDED"


def test_render_page_data_url_maps_temporary_directory_failure_without_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = make_text_pdf("Sichere Textseite für den Tempdir-Fehlertest.")
    preflight = PdfPreflight()

    def unavailable(*_args, **_kwargs):
        raise OSError("private path must not leak")

    monkeypatch.setattr("app.declarations.preflight.tempfile.TemporaryDirectory", unavailable)
    with pytest.raises(DocumentIntegrityError) as error:
        preflight.render_page_data_url(data, 1, budget=preflight.new_import_budget())

    assert error.value.code == "VISION_RENDER_UNAVAILABLE"
    assert "private path" not in str(error.value)


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


def test_page_limit_is_enforced_before_text_or_ocr(renderer: Renderer) -> None:
    data = make_text_pdf("1", "2")

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight(max_pages=1).run_bytes(data, **_integrity(data))

    assert error.value.code == "PDF_PAGE_LIMIT_EXCEEDED"
    assert renderer.calls == []


def test_pdf_zip_polyglot_is_rejected_before_ocr(renderer: Renderer) -> None:
    data = make_text_pdf("Beleg") + b"PK\x03\x04foreign-payload"

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight().run_bytes(data, **_integrity(data))

    assert error.value.code == "PDF_TRAILING_PAYLOAD"
    assert renderer.calls == []


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
    mutate: object, expected_code: str, renderer: Renderer
) -> None:
    data = mutate_pdf(make_text_pdf("kurz"), mutate)  # type: ignore[arg-type]

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight().run_bytes(data, **_integrity(data))

    assert error.value.code == expected_code
    assert renderer.calls == []


def test_file_attachment_annotation_is_rejected_before_ocr(renderer: Renderer) -> None:
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

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight().run_bytes(data, **_integrity(data))

    assert error.value.code == "PDF_EMBEDDED_FILES_FORBIDDEN"
    assert renderer.calls == []


def test_large_media_box_is_rejected_before_ocr(renderer: Renderer) -> None:
    data = mutate_pdf(
        make_text_pdf("kurz"),
        lambda pdf: pdf.pages[0].obj.__setitem__(
            "/MediaBox", pikepdf.Array([0, 0, 10_000_000, 10_000_000])
        ),
    )

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight(max_pixels_per_page=20_000_000).run_bytes(data, **_integrity(data))

    assert error.value.code == "OCR_PAGE_DIMENSIONS_UNSAFE"
    assert renderer.calls == []


def test_user_unit_is_included_in_ocr_dimension_guard(renderer: Renderer) -> None:
    data = mutate_pdf(
        make_text_pdf("kurz"),
        lambda pdf: pdf.pages[0].obj.__setitem__("/UserUnit", 100),
    )

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight().run_bytes(data, **_integrity(data))

    assert error.value.code == "OCR_PAGE_DIMENSIONS_UNSAFE"
    assert renderer.calls == []


def test_encrypted_pdf_is_rejected_before_ocr(renderer: Renderer) -> None:
    encrypted = io.BytesIO()
    with pikepdf.open(io.BytesIO(make_text_pdf("kurz")), attempt_recovery=False) as pdf:
        pdf.save(encrypted, encryption=pikepdf.Encryption(owner="owner", user="user", R=4))
    data = encrypted.getvalue()

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight().run_bytes(data, **_integrity(data))

    assert error.value.code == "PDF_ENCRYPTED_FORBIDDEN"
    assert renderer.calls == []


def test_broken_pdf_has_stable_error_without_provider_invocation(renderer: Renderer) -> None:

    with pytest.raises(DocumentIntegrityError) as error:
        PdfPreflight().run_bytes(
            b"%PDF-broken",
            expected_sha256=hashlib.sha256(b"%PDF-broken").hexdigest(),
            expected_size=len(b"%PDF-broken"),
        )

    assert error.value.code == "PDF_UNREADABLE"
    assert renderer.calls == []


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


def test_settings_have_declaration_preflight_defaults() -> None:
    settings = Settings()

    assert "declaration_import_enabled" not in Settings.model_fields
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




# ---- pages without a text layer are read by the reading model (issue #499, ADR 0017) ---------------------------


def test_a_page_without_a_text_layer_is_marked_for_reading_and_not_rendered(renderer: Renderer) -> None:
    data = make_text_pdf("kurz")

    result = PdfPreflight().run_bytes(data, **_integrity(data))

    assert [(page.source, page.text, page.dpi) for page in result.pages] == [("IMAGE", "", 180)]
    assert renderer.calls == []


def test_large_media_box_lowers_the_reading_dpi(renderer: Renderer) -> None:
    data = mutate_pdf(
        make_text_pdf("kurz"),
        lambda pdf: pdf.pages[0].obj.__setitem__("/MediaBox", pikepdf.Array([0, 0, 1_000, 1_000])),
    )

    result = PdfPreflight(max_pixels_per_page=1_100_000).run_bytes(data, **_integrity(data))

    assert (result.pages[0].source, result.pages[0].dpi) == ("IMAGE", 75)
    assert renderer.calls == []


def test_import_page_budget_stops_before_the_next_page_is_reserved(renderer: Renderer) -> None:
    first, second = make_text_pdf("kurz"), make_text_pdf("auch kurz")
    preflight = PdfPreflight(max_ocr_pages_per_import=1)
    budget = preflight.new_import_budget()

    preflight.run_bytes(first, budget=budget, **_integrity(first))
    with pytest.raises(DocumentIntegrityError) as error:
        preflight.run_bytes(second, budget=budget, **_integrity(second))

    assert error.value.code == "OCR_IMPORT_PAGE_LIMIT_EXCEEDED"
    assert renderer.calls == []


def test_import_pixel_budget_stops_before_the_next_page_is_reserved(renderer: Renderer) -> None:
    first, second = make_text_pdf("kurz"), make_text_pdf("auch kurz")
    preflight = PdfPreflight(max_pixels_per_import=4_000_000)
    budget = preflight.new_import_budget()

    preflight.run_bytes(first, budget=budget, **_integrity(first))
    with pytest.raises(DocumentIntegrityError) as error:
        preflight.run_bytes(second, budget=budget, **_integrity(second))

    assert error.value.code == "OCR_IMPORT_PIXEL_LIMIT_EXCEEDED"
    assert renderer.calls == []


def test_a_page_is_rendered_to_png_at_its_dpi_with_its_resources_accounted(
    renderer: Renderer, tmp_path: Path
) -> None:
    data = make_text_pdf("kurz")
    renderer.size = (800, 600)
    renderer.extra_bytes = 7
    preflight = PdfPreflight(temporary_root=tmp_path)
    budget = preflight.new_import_budget()
    page = _image_page(preflight, data, budget)

    png = preflight.render_page_for_reading(data, page, budget=budget)

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert Image.open(io.BytesIO(png)).size == (800, 600)
    assert (renderer.calls[0]["dpi"], renderer.calls[0]["first_page"], renderer.calls[0]["last_page"]) == (180, 1, 1)
    assert budget.rendered_pixels == 480_000 and budget.temporary_bytes >= 7
    assert list(tmp_path.iterdir()) == [], "no image file outlives the call"


def test_an_oversized_render_is_scaled_down_not_refused(renderer: Renderer) -> None:
    data = make_text_pdf("kurz")
    renderer.size = (3_000, 2_000)  # 6 million pixels
    preflight = PdfPreflight()
    page = _image_page(preflight, data)

    png = preflight.render_page_for_reading(data, page, budget=preflight.new_import_budget())

    width, height = Image.open(io.BytesIO(png)).size
    assert width * height <= READING_MAX_IMAGE_PIXELS
    assert abs(width / height - 1.5) < 0.01, "the aspect ratio is kept"


def test_an_a4_scan_at_the_worker_dpi_is_not_scaled() -> None:
    data = make_text_pdf("kurz")
    preflight = PdfPreflight()
    page = _image_page(preflight, data)

    png = preflight.render_page_for_reading(data, page, budget=preflight.new_import_budget())

    assert Image.open(io.BytesIO(png)).size == (1_489, 2_105)


def test_rendered_pixels_above_the_page_limit_fail_closed(renderer: Renderer) -> None:
    data = make_text_pdf("kurz")
    renderer.size = (2_000, 2_000)
    preflight = PdfPreflight(ocr_dpi=72, max_pixels_per_page=1_000_000)
    page = _image_page(preflight, data)

    with pytest.raises(DocumentIntegrityError) as error:
        preflight.render_page_for_reading(data, page, budget=preflight.new_import_budget())

    assert error.value.code == "OCR_RENDERED_PIXELS_LIMIT_EXCEEDED"


def test_actual_rendered_pixels_reduce_the_remaining_import_capacity(renderer: Renderer) -> None:
    first, second = make_text_pdf("kurz"), make_text_pdf("auch kurz")
    renderer.size = (800, 800)
    preflight = PdfPreflight(ocr_dpi=72, max_pixels_per_page=1_000_000, max_pixels_per_import=1_000_000)
    budget = preflight.new_import_budget()
    page = _image_page(preflight, first, budget)
    preflight.render_page_for_reading(first, page, budget=budget)

    with pytest.raises(DocumentIntegrityError) as error:
        preflight.run_bytes(second, budget=budget, **_integrity(second))

    assert error.value.code == "OCR_IMPORT_PIXEL_LIMIT_EXCEEDED"
    assert budget.rendered_pixels == 640_000


def test_import_temp_budget_stops_the_next_render_at_the_cap(renderer: Renderer) -> None:
    data = make_text_pdf("kurz")
    renderer.extra_bytes = 10
    preflight = PdfPreflight(max_temp_bytes=1_000_000)
    budget = preflight.new_import_budget()
    page = _image_page(preflight, data, budget)
    budget.temporary_bytes = 1_000_000

    with pytest.raises(DocumentIntegrityError) as error:
        preflight.render_page_for_reading(data, page, budget=budget)

    assert error.value.code == "OCR_IMPORT_TEMP_BYTES_LIMIT_EXCEEDED"
    assert renderer.calls == []


def test_actual_temp_bytes_above_the_cap_fail_the_render(renderer: Renderer) -> None:
    data = make_text_pdf("kurz")
    renderer.extra_bytes = 50
    preflight = PdfPreflight(max_temp_bytes=10)
    page = _image_page(preflight, data)

    with pytest.raises(DocumentIntegrityError) as error:
        preflight.render_page_for_reading(data, page, budget=preflight.new_import_budget())

    assert error.value.code == "OCR_TEMP_BYTES_LIMIT_EXCEEDED"


def test_the_deadline_is_enforced_after_the_render(renderer: Renderer) -> None:
    data = make_text_pdf("kurz")
    clock = FakeClock()
    renderer.after = lambda: setattr(clock, "value", 11)
    preflight = PdfPreflight(deadline_seconds=10, monotonic=clock)
    page = _image_page(preflight, data)

    with pytest.raises(DocumentIntegrityError) as error:
        preflight.render_page_for_reading(data, page, budget=preflight.new_import_budget())

    assert error.value.code == "PREFLIGHT_DEADLINE_EXCEEDED"


def test_time_waiting_for_the_model_does_not_count_against_the_deadline(renderer: Renderer) -> None:
    data = make_text_pdf("kurz")
    clock = FakeClock()
    preflight = PdfPreflight(deadline_seconds=10, monotonic=clock)
    budget = preflight.new_import_budget()
    page = _image_page(preflight, data, budget)
    clock.value = 40  # four deadlines of provider latency
    budget.exclude_wait(40)

    assert preflight.render_page_for_reading(data, page, budget=budget).startswith(b"\x89PNG")


def test_the_render_uses_the_remaining_deadline_as_its_timeout(renderer: Renderer) -> None:
    data = make_text_pdf("kurz")
    clock = FakeClock()
    preflight = PdfPreflight(ocr_timeout_seconds=30, deadline_seconds=10, monotonic=clock)
    page = _image_page(preflight, data)
    budget = preflight.new_import_budget()
    clock.value = 4

    preflight.render_page_for_reading(data, page, budget=budget)

    assert renderer.calls[0]["timeout"] == 6


def test_the_render_does_not_start_at_an_exhausted_deadline(renderer: Renderer) -> None:
    data = make_text_pdf("kurz")
    clock = FakeClock()
    preflight = PdfPreflight(deadline_seconds=10, monotonic=clock)
    page = _image_page(preflight, data)
    budget = preflight.new_import_budget()
    clock.value = 11

    with pytest.raises(DocumentIntegrityError) as error:
        preflight.render_page_for_reading(data, page, budget=budget)

    assert error.value.code == "PREFLIGHT_DEADLINE_EXCEEDED"
    assert renderer.calls == []


@pytest.mark.parametrize(
    ("exception", "code"),
    [
        (PDFPopplerTimeoutError("timeout"), "PAGE_RENDER_TIMEOUT"),
        (PDFInfoNotInstalledError("missing poppler"), "PAGE_RENDER_UNAVAILABLE"),
        (OSError("private path must not leak"), "PAGE_RENDER_UNAVAILABLE"),
    ],
)
def test_render_failures_map_to_stable_codes_and_leave_no_temp_directory(
    renderer: Renderer, tmp_path: Path, exception: Exception, code: str
) -> None:
    data = make_text_pdf("kurz")
    renderer.fail = exception
    renderer.extra_bytes = 5
    preflight = PdfPreflight(temporary_root=tmp_path)
    page = _image_page(preflight, data)

    with pytest.raises(DocumentIntegrityError) as error:
        preflight.render_page_for_reading(data, page, budget=preflight.new_import_budget())

    assert error.value.code == code
    assert "private path" not in str(error.value)
    assert list(tmp_path.iterdir()) == []


def test_only_an_image_page_with_a_resolution_can_be_rendered_for_reading() -> None:
    data = make_text_pdf("Dieser Text ist bewusst lang genug fuer die Textschicht-Erkennung.")
    preflight = PdfPreflight()
    result = preflight.run_bytes(data, **_integrity(data))

    with pytest.raises(DocumentIntegrityError) as error:
        preflight.render_page_for_reading(data, result.pages[0], budget=preflight.new_import_budget())

    assert error.value.code == "PAGE_RENDER_UNAVAILABLE"
