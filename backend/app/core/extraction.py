"""Document extraction interface -- Protocol + stub default.

m2-gobd-belegfluss.md section 3.5, section 8.3:
  - DocumentExtractor protocol (same pattern as PdfRenderer, DocumentStore).
  - StubExtractor: returns empty/minimal ExtractionResult.
  - Real OCR/parsing implementation is the NEXT M2 step (not this run).
  - E-Invoice (M5) is a second implementation of the same protocol.

The extraction result is stored as JSONB in invoice_document.extraction_payload
and used to pre-fill cost_entry fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ExtractionResult:
    """Result of document extraction (OCR/parsing/E-Rechnung).

    Fields mirror cost_entry columns. None = not detected.
    confidence: 0.0-1.0 overall confidence score.
    field_confidences: per-field confidence scores.
    raw_text: extracted raw text (for audit/debugging).
    """

    vendor_name: str | None = None
    invoice_date: str | None = None  # ISO date string
    document_number: str | None = None
    gross_amount: str | None = None  # Decimal as string
    vat_amount: str | None = None
    vat_rate: str | None = None
    service_period_from: str | None = None  # ISO date string
    service_period_to: str | None = None
    suggested_category_code: str | None = None
    confidence: float = 0.0
    field_confidences: dict[str, float] = field(default_factory=dict)
    raw_text: str | None = None

    def to_dict(self) -> dict:
        """Serialize to a dict suitable for JSONB storage."""
        return {
            "vendor_name": self.vendor_name,
            "invoice_date": self.invoice_date,
            "document_number": self.document_number,
            "gross_amount": self.gross_amount,
            "vat_amount": self.vat_amount,
            "vat_rate": self.vat_rate,
            "service_period_from": self.service_period_from,
            "service_period_to": self.service_period_to,
            "suggested_category_code": self.suggested_category_code,
            "confidence": self.confidence,
            "field_confidences": self.field_confidences,
            "raw_text": self.raw_text,
        }


@runtime_checkable
class DocumentExtractor(Protocol):
    """Protocol for document extraction engines.

    Implementations:
      - StubExtractor (M2 default): returns empty result.
      - OcrExtractor (M2 next step): real PDF OCR/parsing.
      - EInvoiceExtractor (M5): structured XRechnung/ZUGFeRD parsing.
    """

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
        """Extract structured data from a document.

        Args:
            file_bytes: Raw document content.
            mime_type: MIME type (e.g. 'application/pdf', 'application/xml').

        Returns:
            ExtractionResult with detected fields and confidence scores.
        """
        ...


class StubExtractor:
    """Stub extractor -- returns empty results.

    Placeholder for the real OCR implementation. Documents uploaded via this
    extractor will start as needs_review (low confidence forces review).
    """

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
        return ExtractionResult(
            confidence=0.0,
            raw_text=None,
        )


# --- Module-level singleton (the active extractor) ---

_extractor: DocumentExtractor = StubExtractor()


def get_extractor() -> DocumentExtractor:
    """Return the currently configured document extractor."""
    return _extractor


def set_extractor(extractor: DocumentExtractor) -> None:
    """Replace the active extractor (useful for testing or engine swap)."""
    global _extractor
    _extractor = extractor
