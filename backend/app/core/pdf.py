"""PDF rendering infrastructure -- HTML to PDF.

Design (architecture.md section 7.5, SDD section 16):
  - Thin, **swappable** abstraction behind a Protocol.
  - Default implementation: WeasyPrint (pure Python, no headless browser).
  - The engine is replaceable -- the actual billing layouts come in M3.

Why WeasyPrint over Playwright/Puppeteer:
  1. Pure Python -- no browser binary to manage; simpler deployment.
  2. Optimized for document-style PDFs (tables, text, consistent
     page breaks) which is exactly what billing statements need.
  3. No external process/service overhead.
  4. Deterministic rendering -- same input always yields same output.

The Protocol makes it easy to swap in Playwright or another engine
later if design-heavy PDFs with complex CSS need a real browser engine.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator, Protocol, runtime_checkable

# Without libharfbuzz-subset (not part of the production image) WeasyPrint subsets
# fonts with fontTools, which stamps the current time into the ``head`` table of every
# embedded font. Documents are re-rendered on each download, so two renders of the same
# HTML a second apart would differ by bytes. fontTools takes that stamp from
# SOURCE_DATE_EPOCH; a constant keeps the output a pure function of the HTML.
_FONT_TIMESTAMP_EPOCH = "946684800"  # 2000-01-01T00:00:00Z
_env_lock = threading.Lock()


@contextmanager
def _fixed_font_timestamp() -> Iterator[None]:
    with _env_lock:
        previous = os.environ.get("SOURCE_DATE_EPOCH")
        os.environ["SOURCE_DATE_EPOCH"] = _FONT_TIMESTAMP_EPOCH
        try:
            yield
        finally:
            if previous is None:
                del os.environ["SOURCE_DATE_EPOCH"]
            else:
                os.environ["SOURCE_DATE_EPOCH"] = previous


@runtime_checkable
class PdfRenderer(Protocol):
    """Protocol for HTML-to-PDF rendering engines.

    Implementations must be stateless and thread-safe.
    """

    def render(self, html: str, *, base_url: str | None = None) -> bytes:
        """Render an HTML string to PDF bytes.

        Args:
            html: Complete HTML document as a string.
            base_url: Optional base URL for resolving relative paths
                      in images/CSS.  Defaults to None.

        Returns:
            Raw PDF bytes (must start with ``%PDF``).
        """
        ...


class WeasyPrintRenderer:
    """Default PDF renderer using WeasyPrint."""

    def render(self, html: str, *, base_url: str | None = None) -> bytes:
        """Render HTML to PDF via WeasyPrint."""
        # Lazy import so the module can be imported without WeasyPrint
        # being available (e.g. in lightweight test environments).
        from weasyprint import HTML

        doc = HTML(string=html, base_url=base_url)
        with _fixed_font_timestamp():
            return doc.write_pdf()


# --- Module-level singleton (the active renderer) ---

_renderer: PdfRenderer = WeasyPrintRenderer()


def get_renderer() -> PdfRenderer:
    """Return the currently configured PDF renderer."""
    return _renderer


def set_renderer(renderer: PdfRenderer) -> None:
    """Replace the active renderer (useful for testing or engine swap)."""
    global _renderer
    _renderer = renderer


def render_pdf(html: str, *, base_url: str | None = None) -> bytes:
    """Convenience function: render HTML to PDF using the active engine."""
    return _renderer.render(html, base_url=base_url)
