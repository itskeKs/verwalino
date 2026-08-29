"""Pure, local escalation routing for declaration evidence checks.

This module deliberately accepts only typed, content-free signals.  It never
imports a provider, reads page text, or makes a model decision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Protocol, Sequence


_MINI_MODEL = "gpt-5.4-mini"
_QUALITY_THRESHOLD = 0.85
_PROVIDER_FAILURE_CODES = frozenset(
    {
        "PROVIDER_REFUSAL",
        "PROVIDER_INCOMPLETE",
        "PROVIDER_TIMEOUT",
        "PROVIDER_SCHEMA_FAILURE",
    }
)
_UNKNOWN_EVIDENCE_CODES = frozenset(
    {"UNKNOWN_EVIDENCE_DOCUMENT", "EVIDENCE_PAGE_OUT_OF_RANGE"}
)
_ROUTE_REASON_CODES = _PROVIDER_FAILURE_CODES | _UNKNOWN_EVIDENCE_CODES | {
    "EVIDENCE_QUOTE_NOT_FOUND",
    "LOW_PAGE_QUALITY",
    "DOMAIN_BLOCKER",
}


class RoutingIssue(Protocol):
    """Minimal, content-free validation issue surface consumed by the router."""

    code: str
    severity: str
    document_key: str | None
    pdf_page: int | None


@dataclass(frozen=True, slots=True)
class PageRoutingSignal:
    """A local per-page signal; it intentionally carries no document content."""

    document_key: str
    pdf_page: int
    quality: float
    renderable: bool
    cited: bool
    has_blocker: bool

    def __post_init__(self) -> None:
        if not isinstance(self.document_key, str) or not self.document_key:
            raise ValueError("invalid document_key")
        if isinstance(self.pdf_page, bool) or not isinstance(self.pdf_page, int) or self.pdf_page < 1:
            raise ValueError("invalid pdf_page")
        if (
            isinstance(self.quality, bool)
            or not isinstance(self.quality, (int, float))
            or not math.isfinite(self.quality)
            or self.quality < 0
            or self.quality > 1
        ):
            raise ValueError("invalid quality")
        if any(not isinstance(value, bool) for value in (self.renderable, self.cited, self.has_blocker)):
            raise ValueError("invalid page routing signal")

    @property
    def key(self) -> tuple[str, int]:
        return self.document_key, self.pdf_page


@dataclass(frozen=True, slots=True)
class RouterDecision:
    action: Literal["NONE", "VISION_RECHECK", "HUMAN_REVIEW"]
    reason_codes: tuple[str, ...]
    page_keys: tuple[tuple[str, int], ...]
    model: str | None

    def __post_init__(self) -> None:
        if self.action not in {"NONE", "VISION_RECHECK", "HUMAN_REVIEW"}:
            raise ValueError("invalid router action")
        if (
            not isinstance(self.reason_codes, tuple)
            or any(code not in _ROUTE_REASON_CODES for code in self.reason_codes)
            or tuple(sorted(set(self.reason_codes))) != self.reason_codes
        ):
            raise ValueError("invalid route reason codes")
        if (
            not isinstance(self.page_keys, tuple)
            or any(
                not isinstance(key, tuple)
                or len(key) != 2
                or not isinstance(key[0], str)
                or not key[0]
                or isinstance(key[1], bool)
                or not isinstance(key[1], int)
                or key[1] < 1
                for key in self.page_keys
            )
            or tuple(sorted(set(self.page_keys))) != self.page_keys
        ):
            raise ValueError("invalid route page keys")
        if self.action == "VISION_RECHECK":
            if not self.reason_codes or not self.page_keys or self.model != _MINI_MODEL:
                raise ValueError("invalid vision route")
            return
        if self.page_keys or self.model is not None:
            raise ValueError("invalid non-vision route")
        if self.action == "NONE" and self.reason_codes:
            raise ValueError("invalid no-route decision")
        if self.action == "HUMAN_REVIEW" and not self.reason_codes:
            raise ValueError("invalid human-review decision")


def route_escalation(
    *,
    issues: Sequence[RoutingIssue],
    page_inventory: Sequence[PageRoutingSignal],
    provider_failure: str | None,
    max_pages: int,
) -> RouterDecision:
    """Return the fixed escalation decision table without side effects."""
    _validate_max_pages(max_pages)
    _validate_provider_failure(provider_failure)
    pages = _coalesce_pages(page_inventory)

    if provider_failure is not None:
        return _human((provider_failure,))

    issue_values = tuple(issues)
    unknown_evidence = _issue_codes(issue_values, _UNKNOWN_EVIDENCE_CODES)
    if unknown_evidence:
        return _human(unknown_evidence)

    has_quote_failure = any(
        issue.code == "EVIDENCE_QUOTE_NOT_FOUND" for issue in issue_values
    )
    quote_pages = _issue_page_keys(issue_values, "EVIDENCE_QUOTE_NOT_FOUND")
    if has_quote_failure:
        if quote_pages is None:
            return _human(("EVIDENCE_QUOTE_NOT_FOUND",))
        selected = _select_renderable_pages(quote_pages, pages)
        if selected is None:
            return _human(("EVIDENCE_QUOTE_NOT_FOUND",))
        return _vision(("EVIDENCE_QUOTE_NOT_FOUND",), selected, max_pages)

    low_quality = tuple(
        key
        for key, page in pages.items()
        if page.quality < _QUALITY_THRESHOLD and (page.cited or page.has_blocker)
    )
    if low_quality:
        if any(not pages[key].renderable for key in low_quality):
            return _human(("LOW_PAGE_QUALITY",))
        return _vision(("LOW_PAGE_QUALITY",), low_quality, max_pages)

    if any(issue.severity == "BLOCKER" for issue in issue_values):
        return _human(("DOMAIN_BLOCKER",))
    return RouterDecision("NONE", (), (), None)


def _validate_max_pages(max_pages: int) -> None:
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError("max_pages must be a positive integer")


def _validate_provider_failure(provider_failure: str | None) -> None:
    if provider_failure is None:
        return
    if not isinstance(provider_failure, str) or provider_failure not in _PROVIDER_FAILURE_CODES:
        raise ValueError("invalid provider_failure")


def _coalesce_pages(
    page_inventory: Sequence[PageRoutingSignal],
) -> dict[tuple[str, int], PageRoutingSignal]:
    pages: dict[tuple[str, int], PageRoutingSignal] = {}
    for page in page_inventory:
        if not isinstance(page, PageRoutingSignal):
            raise ValueError("invalid page inventory")
        existing = pages.get(page.key)
        if existing is None:
            pages[page.key] = page
            continue
        pages[page.key] = PageRoutingSignal(
            document_key=page.document_key,
            pdf_page=page.pdf_page,
            quality=min(existing.quality, page.quality),
            renderable=existing.renderable and page.renderable,
            cited=existing.cited or page.cited,
            has_blocker=existing.has_blocker or page.has_blocker,
        )
    return dict(sorted(pages.items()))


def _issue_codes(
    issues: Sequence[RoutingIssue], codes: frozenset[str]
) -> tuple[str, ...]:
    return tuple(sorted({issue.code for issue in issues if issue.code in codes}))


def _issue_page_keys(
    issues: Sequence[RoutingIssue], code: str
) -> tuple[tuple[str, int], ...] | None:
    keys: set[tuple[str, int]] = set()
    for issue in issues:
        if issue.code != code:
            continue
        document_key, pdf_page = issue.document_key, issue.pdf_page
        if (
            not isinstance(document_key, str)
            or not document_key
            or isinstance(pdf_page, bool)
            or not isinstance(pdf_page, int)
            or pdf_page < 1
        ):
            return None
        keys.add((document_key, pdf_page))
    return tuple(sorted(keys))


def _select_renderable_pages(
    keys: Sequence[tuple[str, int]],
    pages: dict[tuple[str, int], PageRoutingSignal],
) -> tuple[tuple[str, int], ...] | None:
    selected: list[tuple[str, int]] = []
    for key in keys:
        page = pages.get(key)
        if page is None or not page.renderable:
            return None
        selected.append(key)
    return tuple(sorted(set(selected)))


def _vision(
    reasons: tuple[str, ...], keys: Sequence[tuple[str, int]], max_pages: int
) -> RouterDecision:
    selected = tuple(sorted(set(keys)))[:max_pages]
    if not selected:
        return _human(reasons)
    return RouterDecision("VISION_RECHECK", reasons, selected, _MINI_MODEL)


def _human(reasons: tuple[str, ...]) -> RouterDecision:
    return RouterDecision("HUMAN_REVIEW", tuple(sorted(set(reasons))), (), None)
