"""Smoke tests for the PDF rendering infrastructure.

These tests verify:
  1. The WeasyPrint renderer produces valid PDF bytes from sample HTML.
  2. The Protocol / swappable abstraction works.
  3. The output starts with the PDF magic bytes (%PDF).

No database or backend server needed -- pure unit tests.
"""

from pathlib import Path

import pytest

from app.core.pdf import (
    PdfRenderer,
    WeasyPrintRenderer,
    render_pdf,
    get_renderer,
    set_renderer,
)


TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "templates"


class TestWeasyPrintRenderer:
    """Test the default WeasyPrint-based renderer."""

    def test_render_minimal_html(self):
        """Minimal HTML produces non-empty PDF with correct header."""
        html = "<html><body><h1>Test</h1></body></html>"
        renderer = WeasyPrintRenderer()
        pdf_bytes = renderer.render(html)

        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 100, "PDF should be more than a few bytes"
        assert pdf_bytes[:5] == b"%PDF-", "Output must start with PDF magic bytes"

    def test_render_smoke_template(self):
        """The smoke-test template renders to a valid PDF."""
        template_path = TEMPLATES_DIR / "smoke_test.html"
        assert template_path.exists(), f"Template not found: {template_path}"

        html = template_path.read_text(encoding="utf-8")
        pdf_bytes = render_pdf(html)

        assert pdf_bytes[:5] == b"%PDF-"
        assert len(pdf_bytes) > 500, "Smoke-test PDF should be substantial"
        # Verify the PDF ends with %%EOF (standard PDF trailer).
        assert b"%%EOF" in pdf_bytes, "PDF should contain the %%EOF trailer"

    def test_render_german_characters(self):
        """German umlauts and special characters render without error."""
        html = """
        <html><body>
          <p>Betriebskostenabrechnung fuer Eigentuemer</p>
          <p>Gebuehren: 1.234,56 EUR</p>
        </body></html>
        """
        pdf_bytes = render_pdf(html)
        assert pdf_bytes[:5] == b"%PDF-"


class TestPdfRendererProtocol:
    """Test the swappable abstraction."""

    def test_weasyprint_satisfies_protocol(self):
        """WeasyPrintRenderer is a valid PdfRenderer."""
        renderer = WeasyPrintRenderer()
        assert isinstance(renderer, PdfRenderer)

    def test_custom_renderer_can_replace_default(self):
        """A custom renderer can be swapped in via set_renderer."""

        class StubRenderer:
            def render(self, html: str, *, base_url: str | None = None) -> bytes:
                return b"%PDF-stub"

        original = get_renderer()
        try:
            set_renderer(StubRenderer())
            result = render_pdf("<html></html>")
            assert result == b"%PDF-stub"
        finally:
            # Restore the original renderer.
            set_renderer(original)

    def test_get_renderer_returns_default(self):
        """By default, the module uses WeasyPrintRenderer."""
        renderer = get_renderer()
        assert isinstance(renderer, WeasyPrintRenderer)
