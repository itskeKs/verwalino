"""Ephemeral, deterministic quote-to-page evidence checks."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping, Sequence

from app.declarations.provider import PageInput

if TYPE_CHECKING:
    from app.declarations.schemas import EvidencePayload
    from app.declarations.validation import ValidationIssue


_DASHES = frozenset(
    "-\u00ad\u058a\u05be\u1400\u1806\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
    "\u2e17\u2e1a\u2e3a\u2e3b\u2e40\u301c\u3030\u30a0\ufe31\ufe32\ufe58\ufe63\uff0d"
)
_WHITESPACE = re.compile(r"\s+")


def normalize_evidence_text(value: str) -> str:
    """Return the one exact normalization used for quotes and OCR page text."""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = "".join("-" if character in _DASHES else character for character in normalized)
    return _WHITESPACE.sub(" ", normalized.replace("\r", "\n")).strip().casefold()


@dataclass(frozen=True, slots=True, repr=False)
class EvidenceTextIndex:
    """Run-local page text index; normalized contents never leave this object."""

    _texts: Mapping[tuple[str, int], str] = field(repr=False)
    _documents: frozenset[str] = field(repr=False)

    @classmethod
    def from_pages(cls, pages: Sequence[PageInput]) -> "EvidenceTextIndex":
        texts: dict[tuple[str, int], str] = {}
        documents: set[str] = set()
        for page in pages:
            key = (page.document_key, page.pdf_page)
            if key in texts:
                raise ValueError("duplicate evidence page")
            documents.add(page.document_key)
            texts[key] = normalize_evidence_text(page.text)
        return cls(MappingProxyType(texts), frozenset(documents))

    def has_document(self, document_key: str) -> bool:
        return document_key in self._documents

    def has_page(self, document_key: str, pdf_page: int) -> bool:
        return (document_key, pdf_page) in self._texts

    def _page_text(self, document_key: str, pdf_page: int) -> str | None:
        """Internal lookup only; callers must never persist or report the result."""
        return self._texts.get((document_key, pdf_page))

    def __repr__(self) -> str:
        return "EvidenceTextIndex()"


def validate_evidence_quote(
    evidence: EvidencePayload, index: EvidenceTextIndex
) -> ValidationIssue | None:
    """Return a fixed, text-free validation issue for an invalid citation."""
    if not index.has_document(evidence.document_key):
        return _issue("UNKNOWN_EVIDENCE_DOCUMENT", evidence.document_key, evidence.pdf_page)
    if not index.has_page(evidence.document_key, evidence.pdf_page):
        return _issue("EVIDENCE_PAGE_OUT_OF_RANGE", evidence.document_key, evidence.pdf_page)
    return validate_quote_on_known_page(evidence, index)


def validate_quote_on_known_page(
    evidence: EvidencePayload, index: EvidenceTextIndex
) -> ValidationIssue | None:
    """Validate quote text after the caller has checked document/page bounds."""
    normalized_quote = normalize_evidence_text(evidence.quote)
    page_text = index._page_text(evidence.document_key, evidence.pdf_page)
    if not normalized_quote or page_text is None or normalized_quote not in page_text:
        return _issue("EVIDENCE_QUOTE_NOT_FOUND", evidence.document_key, evidence.pdf_page)
    return None


def _issue(code: str, document_key: str, pdf_page: int) -> ValidationIssue:
    # Deferred import keeps the validation module as the public issue contract
    # while avoiding an import cycle during module initialization.
    from app.declarations.validation import ValidationIssue

    messages = {
        "UNKNOWN_EVIDENCE_DOCUMENT": "Die Belegdatei gehört nicht zum Import.",
        "EVIDENCE_PAGE_OUT_OF_RANGE": "Die Belegseite liegt außerhalb des PDF.",
        "EVIDENCE_QUOTE_NOT_FOUND": "Das belegte Zitat wurde auf der angegebenen Seite nicht gefunden.",
    }
    return ValidationIssue(
        code=code,
        severity="BLOCKER",
        message=messages[code],
        document_key=document_key,
        pdf_page=pdf_page,
    )
