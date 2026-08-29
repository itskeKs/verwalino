from __future__ import annotations

from app.declarations.evidence import (
    EvidenceTextIndex,
    normalize_evidence_text,
    validate_evidence_quote,
)
from app.declarations.provider import PageInput
from app.declarations.schemas import EvidencePayload, FactPayload, ReconciliationOutput, TextValue
from app.declarations.validation import validate_reconciliation


def _index(*pages: PageInput) -> EvidenceTextIndex:
    return EvidenceTextIndex.from_pages(pages)


def _evidence(*, document_key: str = "declaration-main", pdf_page: int = 4, quote: str = "Heizkosten - 70 % Verbrauch") -> EvidencePayload:
    return EvidencePayload(
        document_key=document_key,
        pdf_page=pdf_page,
        quote=quote,
    )


def test_quote_on_exact_referenced_page_is_accepted() -> None:
    index = _index(
        PageInput("declaration-main", 4, "Heizkosten - 70 % Verbrauch", 0.99)
    )

    assert validate_evidence_quote(_evidence(), index) is None


def test_quote_normalization_handles_unicode_whitespace_and_dash_variants() -> None:
    index = _index(
        PageInput("declaration-main", 4, "Heizkosten\u00a0–\r\n70\u2003%\u00a0Verbrauch", 0.99)
    )

    assert normalize_evidence_text("Heizkosten – 70 % Verbrauch") == "heizkosten - 70 % verbrauch"
    assert validate_evidence_quote(
        _evidence(quote="Heizkosten - 70 % Verbrauch"), index
    ) is None


def test_quote_on_another_page_is_blocked() -> None:
    index = _index(
        PageInput("declaration-main", 4, "Seite ohne den behaupteten Satz", 0.99),
        PageInput("declaration-main", 5, "Heizkosten - 70 % Verbrauch", 0.99),
    )

    issue = validate_evidence_quote(_evidence(pdf_page=4), index)

    assert issue is not None
    assert issue.code == "EVIDENCE_QUOTE_NOT_FOUND"
    assert issue.document_key == "declaration-main"
    assert issue.pdf_page == 4


def test_unknown_document_or_page_is_not_routable() -> None:
    index = _index(PageInput("declaration-main", 4, "Heizkosten", 0.99))

    unknown_document = validate_evidence_quote(
        _evidence(document_key="unrelated-document"), index
    )
    missing_page = validate_evidence_quote(_evidence(pdf_page=5), index)

    assert unknown_document is not None
    assert unknown_document.code == "UNKNOWN_EVIDENCE_DOCUMENT"
    assert unknown_document.document_key == "unrelated-document"
    assert unknown_document.pdf_page == 4
    assert missing_page is not None
    assert missing_page.code == "EVIDENCE_PAGE_OUT_OF_RANGE"
    assert missing_page.document_key == "declaration-main"
    assert missing_page.pdf_page == 5


def test_empty_normalized_quote_is_blocked() -> None:
    index = _index(PageInput("declaration-main", 4, "Seiteninhalt", 0.99))

    issue = validate_evidence_quote(_evidence(quote="\u00a0\r\n\t"), index)

    assert issue is not None
    assert issue.code == "EVIDENCE_QUOTE_NOT_FOUND"


def test_quote_failure_message_contains_no_quote_or_page_text() -> None:
    quote = "nicht vorhandene geheime Formulierung"
    page_text = "vertraulicher Seitentext, der nie in Fehlern erscheinen darf"
    index = _index(PageInput("declaration-main", 4, page_text, 0.99))

    issue = validate_evidence_quote(_evidence(quote=quote), index)

    assert issue is not None
    assert quote not in issue.message
    assert page_text not in issue.message
    assert quote not in repr(issue)
    assert page_text not in repr(issue)


def test_reconciliation_quote_issue_keeps_only_safe_page_reference() -> None:
    output = ReconciliationOutput(
        facts=[
            FactPayload(
                subject_key="property:main",
                field="street",
                raw_value="Hauptstraße 1",
                normalized_value=TextValue(kind="TEXT", value="Hauptstraße 1"),
                confidence=0.9,
                evidence=[_evidence(quote="erfundenes Zitat")],
            )
        ],
        rules=[],
    )
    index = _index(PageInput("declaration-main", 4, "Hauptstraße 1", 0.99))

    issues = validate_reconciliation(
        output,
        page_counts={"declaration-main": 4},
        declared_mea_denominator=1,
        evidence_index=index,
    )

    issue = next(item for item in issues if item.code == "EVIDENCE_QUOTE_NOT_FOUND")
    assert issue.subject_key == "property:main"
    assert issue.document_key == "declaration-main"
    assert issue.pdf_page == 4


def test_page_counts_remain_authoritative_when_index_has_extra_page() -> None:
    output = ReconciliationOutput(
        facts=[
            FactPayload(
                subject_key="property:main",
                field="street",
                raw_value="Hauptstraße 1",
                normalized_value=TextValue(kind="TEXT", value="Hauptstraße 1"),
                confidence=0.9,
                evidence=[_evidence(pdf_page=5, quote="Hauptstraße 1")],
            )
        ],
        rules=[],
    )
    index = _index(PageInput("declaration-main", 5, "Hauptstraße 1", 0.99))

    issues = validate_reconciliation(
        output,
        page_counts={"declaration-main": 4},
        declared_mea_denominator=1,
        evidence_index=index,
    )

    evidence_issues = [item for item in issues if item.code.startswith("EVIDENCE_")]
    assert [item.code for item in evidence_issues] == ["EVIDENCE_PAGE_OUT_OF_RANGE"]


def test_incomplete_index_fails_closed_without_second_page_issue() -> None:
    output = ReconciliationOutput(
        facts=[
            FactPayload(
                subject_key="property:main",
                field="street",
                raw_value="Hauptstraße 1",
                normalized_value=TextValue(kind="TEXT", value="Hauptstraße 1"),
                confidence=0.9,
                evidence=[_evidence(pdf_page=4, quote="Hauptstraße 1")],
            )
        ],
        rules=[],
    )
    index = _index(PageInput("declaration-main", 3, "Hauptstraße 1", 0.99))

    issues = validate_reconciliation(
        output,
        page_counts={"declaration-main": 4},
        declared_mea_denominator=1,
        evidence_index=index,
    )

    evidence_issues = [item for item in issues if item.code.startswith("EVIDENCE_")]
    assert [item.code for item in evidence_issues] == ["EVIDENCE_QUOTE_NOT_FOUND"]
    assert evidence_issues[0].document_key == "declaration-main"
    assert evidence_issues[0].pdf_page == 4


def test_page_counts_report_unknown_document_once_even_if_index_contains_it() -> None:
    output = ReconciliationOutput(
        facts=[
            FactPayload(
                subject_key="property:main",
                field="street",
                raw_value="Hauptstraße 1",
                normalized_value=TextValue(kind="TEXT", value="Hauptstraße 1"),
                confidence=0.9,
                evidence=[
                    _evidence(
                        document_key="not-in-page-counts",
                        quote="Hauptstraße 1",
                    )
                ],
            )
        ],
        rules=[],
    )
    index = _index(PageInput("not-in-page-counts", 4, "Hauptstraße 1", 0.99))

    issues = validate_reconciliation(
        output,
        page_counts={"declaration-main": 4},
        declared_mea_denominator=1,
        evidence_index=index,
    )

    evidence_issues = [
        item
        for item in issues
        if item.code in {"UNKNOWN_EVIDENCE_DOCUMENT", "EVIDENCE_PAGE_OUT_OF_RANGE"}
    ]
    assert [item.code for item in evidence_issues] == ["UNKNOWN_EVIDENCE_DOCUMENT"]
