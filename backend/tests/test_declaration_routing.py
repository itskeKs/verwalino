from __future__ import annotations

import pytest

from app.declarations.routing import (
    PageRoutingSignal,
    RouterDecision,
    route_escalation,
)
from app.declarations.validation import ValidationIssue


def _issue(
    code: str,
    *,
    document_key: str | None = None,
    pdf_page: int | None = None,
    severity: str = "BLOCKER",
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        message="fixed test issue",
        document_key=document_key,
        pdf_page=pdf_page,
    )


def _page(
    document_key: str = "declaration",
    pdf_page: int = 1,
    *,
    quality: float = 0.99,
    renderable: bool = True,
    cited: bool = False,
    has_blocker: bool = False,
) -> PageRoutingSignal:
    return PageRoutingSignal(
        document_key=document_key,
        pdf_page=pdf_page,
        quality=quality,
        renderable=renderable,
        cited=cited,
        has_blocker=has_blocker,
    )


@pytest.mark.parametrize(
    "failure",
    (
        "PROVIDER_REFUSAL",
        "PROVIDER_INCOMPLETE",
        "PROVIDER_TIMEOUT",
        "PROVIDER_SCHEMA_FAILURE",
    ),
)
def test_failure_classes_never_route_to_vision(failure: str) -> None:
    decision = route_escalation(
        issues=(_issue("EVIDENCE_QUOTE_NOT_FOUND", document_key="declaration", pdf_page=1),),
        page_inventory=(_page(cited=True, has_blocker=True),),
        provider_failure=failure,
        max_pages=2,
    )

    assert decision == RouterDecision("HUMAN_REVIEW", (failure,), (), None)


@pytest.mark.parametrize("code", ("UNKNOWN_EVIDENCE_DOCUMENT", "EVIDENCE_PAGE_OUT_OF_RANGE"))
def test_unknown_evidence_never_routes_to_vision(code: str) -> None:
    decision = route_escalation(
        issues=(_issue(code, document_key="declaration", pdf_page=1),),
        page_inventory=(_page(cited=True, has_blocker=True),),
        provider_failure=None,
        max_pages=2,
    )

    assert decision == RouterDecision("HUMAN_REVIEW", (code,), (), None)


def test_missing_quote_on_renderable_known_page_routes_to_mini() -> None:
    decision = route_escalation(
        issues=(_issue("EVIDENCE_QUOTE_NOT_FOUND", document_key="declaration", pdf_page=2),),
        page_inventory=(_page(pdf_page=2, cited=True, has_blocker=True),),
        provider_failure=None,
        max_pages=2,
    )

    assert decision == RouterDecision(
        "VISION_RECHECK",
        ("EVIDENCE_QUOTE_NOT_FOUND",),
        (("declaration", 2),),
        "gpt-5.4-mini",
    )


@pytest.mark.parametrize(
    "page_inventory",
    (
        (_page(pdf_page=2, renderable=False, cited=True, has_blocker=True),),
        (),
    ),
)
def test_missing_quote_without_a_renderable_known_page_routes_to_human_review(
    page_inventory: tuple[PageRoutingSignal, ...],
) -> None:
    decision = route_escalation(
        issues=(_issue("EVIDENCE_QUOTE_NOT_FOUND", document_key="declaration", pdf_page=2),),
        page_inventory=page_inventory,
        provider_failure=None,
        max_pages=2,
    )

    assert decision == RouterDecision(
        "HUMAN_REVIEW", ("EVIDENCE_QUOTE_NOT_FOUND",), (), None
    )


def test_missing_quote_without_a_page_reference_routes_to_human_review() -> None:
    decision = route_escalation(
        issues=(_issue("EVIDENCE_QUOTE_NOT_FOUND"),),
        page_inventory=(_page(),),
        provider_failure=None,
        max_pages=2,
    )

    assert decision == RouterDecision(
        "HUMAN_REVIEW", ("EVIDENCE_QUOTE_NOT_FOUND",), (), None
    )


def test_low_quality_cited_blocker_page_routes_to_mini() -> None:
    decision = route_escalation(
        issues=(_issue("MEA_TOTAL_INVALID", document_key="declaration", pdf_page=2),),
        page_inventory=(
            _page(pdf_page=1),
            _page(pdf_page=2, quality=0.84, cited=True, has_blocker=True),
        ),
        provider_failure=None,
        max_pages=2,
    )

    assert decision == RouterDecision(
        "VISION_RECHECK", ("LOW_PAGE_QUALITY",), (("declaration", 2),), "gpt-5.4-mini"
    )


def test_low_quality_uncited_page_does_not_route() -> None:
    decision = route_escalation(
        issues=(),
        page_inventory=(_page(quality=0.84),),
        provider_failure=None,
        max_pages=2,
    )

    assert decision == RouterDecision("NONE", (), (), None)


def test_low_quality_unrenderable_relevant_page_routes_to_human_review() -> None:
    decision = route_escalation(
        issues=(),
        page_inventory=(
            _page(quality=0.84, renderable=False, cited=True),
        ),
        provider_failure=None,
        max_pages=2,
    )

    assert decision == RouterDecision("HUMAN_REVIEW", ("LOW_PAGE_QUALITY",), (), None)


def test_domain_blocker_without_visual_signal_routes_to_human_review() -> None:
    decision = route_escalation(
        issues=(_issue("MEA_TOTAL_INVALID"),),
        page_inventory=(_page(),),
        provider_failure=None,
        max_pages=2,
    )

    assert decision == RouterDecision("HUMAN_REVIEW", ("DOMAIN_BLOCKER",), (), None)


def test_router_sorts_deduplicates_and_caps_page_keys() -> None:
    decision = route_escalation(
        issues=(
            _issue("EVIDENCE_QUOTE_NOT_FOUND", document_key="z", pdf_page=3),
            _issue("EVIDENCE_QUOTE_NOT_FOUND", document_key="a", pdf_page=2),
            _issue("EVIDENCE_QUOTE_NOT_FOUND", document_key="a", pdf_page=1),
        ),
        page_inventory=(
            _page("z", 3, cited=True, has_blocker=True),
            _page("a", 2, cited=True, has_blocker=True),
            _page("a", 1, cited=True, has_blocker=True),
            _page("a", 1, cited=True, has_blocker=True),
        ),
        provider_failure=None,
        max_pages=2,
    )

    assert decision == RouterDecision(
        "VISION_RECHECK",
        ("EVIDENCE_QUOTE_NOT_FOUND",),
        (("a", 1), ("a", 2)),
        "gpt-5.4-mini",
    )


@pytest.mark.parametrize("max_pages", (True, 0, -1, 1.5, "2"))
def test_router_rejects_non_positive_or_non_integer_page_limits(max_pages: object) -> None:
    with pytest.raises(ValueError, match="max_pages"):
        route_escalation(
            issues=(),
            page_inventory=(),
            provider_failure=None,
            max_pages=max_pages,  # type: ignore[arg-type]
        )
