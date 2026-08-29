import pytest

from app.declarations.policy import CandidateContext, classify_candidate
from app.declarations.schemas import (
    AdditionalFactPayload,
    FactPayload,
    ReconciliationOutput,
    RulePayload,
)
from app.declarations.validation import validate_reconciliation


def test_model_confidence_cannot_downgrade_rule_risk() -> None:
    decision = classify_candidate(
        CandidateContext(
            candidate_type="RULE",
            operation="CREATE",
            confidence=1.0,
            issue_severities=(),
            has_conflict=False,
        )
    )

    assert decision.risk_class == "CRITICAL"
    assert decision.requires_individual_review is True


def test_existing_value_update_is_always_individual() -> None:
    decision = classify_candidate(
        CandidateContext(
            candidate_type="UNIT_FIELD",
            operation="UPDATE",
            confidence=1.0,
            issue_severities=(),
            has_conflict=False,
        )
    )

    assert decision.requires_individual_review is True


def test_warning_promotes_low_risk_fact() -> None:
    decision = classify_candidate(
        CandidateContext(
            candidate_type="PROPERTY_FIELD",
            operation="CREATE",
            confidence=0.99,
            issue_severities=("WARNING",),
            has_conflict=False,
            field_key="city",
        )
    )

    assert decision.requires_individual_review is True


def test_only_sourced_known_descriptive_create_is_group_eligible() -> None:
    decision = classify_candidate(
        CandidateContext(
            candidate_type="UNIT_FIELD",
            operation="CREATE",
            confidence=0.85,
            issue_severities=("INFO",),
            has_conflict=False,
            field_key="location",
            has_evidence=True,
        )
    )

    assert decision.risk_class == "LOW"
    assert decision.requires_individual_review is False


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -0.01, 0.849, 1.01])
def test_invalid_or_low_confidence_is_always_individual(confidence: float) -> None:
    decision = classify_candidate(
        CandidateContext(
            candidate_type="UNIT_FIELD",
            operation="CREATE",
            confidence=confidence,
            issue_severities=(),
            has_conflict=False,
            field_key="location",
            has_evidence=True,
        )
    )

    assert decision.risk_class == "CRITICAL"
    assert decision.requires_individual_review is True


def test_unknown_candidate_or_missing_evidence_is_always_individual() -> None:
    unknown = classify_candidate(
        CandidateContext(
            candidate_type="UNKNOWN",
            operation="CREATE",
            confidence=1.0,
            issue_severities=(),
            has_conflict=False,
            has_evidence=True,
        )
    )
    missing_evidence = classify_candidate(
        CandidateContext(
            candidate_type="UNIT_FIELD",
            operation="CREATE",
            confidence=1.0,
            issue_severities=(),
            has_conflict=False,
            field_key="location",
        )
    )

    assert unknown.requires_individual_review is True
    assert missing_evidence.requires_individual_review is True


def _evidence(document_key: str = "base", page: int = 1) -> list[dict[str, object]]:
    return [{"document_key": document_key, "pdf_page": page, "quote": "Beleg"}]


def _fact(
    subject_key: str,
    field: str,
    normalized_value: dict[str, object],
    *,
    document_key: str = "base",
    page: int = 1,
) -> FactPayload:
    return FactPayload.model_validate(
        {
            "subject_key": subject_key,
            "field": field,
            "raw_value": "Quellwert",
            "normalized_value": normalized_value,
            "confidence": 0.99,
            "evidence": _evidence(document_key, page),
        }
    )


def _rule(
    subject_key: str = "rule:heating",
    *,
    formula: dict[str, object] | None = None,
    proposed_status: str = "EFFECTIVE",
    clarification_needed: bool = False,
    conflicts: list[str] | None = None,
) -> RulePayload:
    effect: dict[str, object] = {}
    if formula is not None:
        effect["allocation_formula"] = formula
    return RulePayload.model_validate(
        {
            "subject_key": subject_key,
            "title": "Kostenverteilung",
            "topic": "kosten",
            "summary": "Die Kosten werden verteilt.",
            "scope": {},
            "effect": effect,
            "proposed_status": proposed_status,
            "conflicts_with_subject_keys": conflicts or [],
            "evidence": _evidence(),
            "confidence": 0.99,
            "clarification_needed": clarification_needed,
        }
    )


def _output(
    *,
    facts: list[FactPayload] | None = None,
    rules: list[RulePayload] | None = None,
    additional_facts: list[AdditionalFactPayload] | None = None,
    mea_set_status: str = "UNKNOWN",
) -> ReconciliationOutput:
    return ReconciliationOutput(
        facts=facts or [],
        rules=rules or [],
        additional_facts=additional_facts or [],
        mea_set_status=mea_set_status,
    )


def _codes(output: ReconciliationOutput, denominator: int | None = 1_000) -> set[str]:
    return {
        issue.code
        for issue in validate_reconciliation(
            output,
            page_counts={"base": 4, "amendment": 4},
            declared_mea_denominator=denominator,
        )
    }


def test_reconciliation_defaults_mea_status_and_preserves_conflicting_alternatives() -> None:
    first = _fact("property:new", "city", {"kind": "TEXT", "value": "Köln"})
    second = _fact("property:new", "city", {"kind": "TEXT", "value": "Bonn"})
    output = _output(facts=[first, second])

    assert output.mea_set_status == "UNKNOWN"
    assert "CONFLICTING_FACT_ALTERNATIVES" in _codes(output)


def test_equal_fact_duplicates_remain_available_for_evidence_merging() -> None:
    first = _fact("property:new", "city", {"kind": "TEXT", "value": "Köln"})
    same_value = _fact(
        "property:new",
        "city",
        {"kind": "TEXT", "value": "Köln"},
        document_key="amendment",
    )

    assert "CONFLICTING_FACT_ALTERNATIVES" not in _codes(_output(facts=[first, same_value]))


def test_equal_unit_number_duplicates_from_one_subject_do_not_block() -> None:
    first = _fact("unit:1", "number", {"kind": "TEXT", "value": "1"})
    same_number = _fact(
        "unit:1",
        "number",
        {"kind": "TEXT", "value": "1"},
        document_key="amendment",
    )

    assert "DUPLICATE_UNIT_NUMBER" not in _codes(_output(facts=[first, same_number]))


def test_complete_mea_set_requires_exact_common_denominator_sum() -> None:
    facts = [
        _fact(
            f"unit:{number}",
            "mea",
            {"kind": "MEA", "numerator": numerator, "denominator": 1_000},
        )
        for number, numerator in enumerate((100, 200, 150, 50, 250, 250), start=1)
    ]

    assert not (set(_codes(_output(facts=facts, mea_set_status="COMPLETE"))) & {"MEA_TOTAL_INVALID", "MIXED_MEA_DENOMINATOR"})


def test_equal_mea_duplicates_are_counted_once_for_complete_total() -> None:
    first = _fact(
        "unit:1", "mea", {"kind": "MEA", "numerator": 500, "denominator": 1_000}
    )
    same_value = _fact(
        "unit:1",
        "mea",
        {"kind": "MEA", "numerator": 500, "denominator": 1_000},
        document_key="amendment",
    )

    assert "MEA_TOTAL_INVALID" in _codes(
        _output(facts=[first, same_value], mea_set_status="COMPLETE")
    )


def test_partial_mea_set_retains_exact_replacement_sum() -> None:
    facts = [
        _fact("unit:34", "mea", {"kind": "MEA", "numerator": 47_732, "denominator": 1_000_000}),
        _fact("unit:35", "mea", {"kind": "MEA", "numerator": 47_732, "denominator": 1_000_000}),
    ]

    assert not (set(_codes(_output(facts=facts, mea_set_status="PARTIAL"), 1_000_000)) & {"MEA_TOTAL_INVALID", "MIXED_MEA_DENOMINATOR"})


def test_unknown_mea_lower_total_warns_and_partial_total_may_not_exceed_denominator() -> None:
    lower = _output(
        facts=[_fact("unit:1", "mea", {"kind": "MEA", "numerator": 500, "denominator": 1_000})]
    )
    excessive = _output(
        facts=[_fact("unit:1", "mea", {"kind": "MEA", "numerator": 1_001, "denominator": 1_000})],
        mea_set_status="PARTIAL",
    )

    assert "MEA_TOTAL_STATUS_UNKNOWN" in _codes(lower)
    assert "MEA_TOTAL_INVALID" in _codes(excessive)


def test_unknown_empty_mea_set_warns_or_blocks_for_missing_denominator() -> None:
    unknown = _output()

    assert "MEA_TOTAL_STATUS_UNKNOWN" in _codes(unknown)
    assert "MEA_DECLARED_DENOMINATOR_INVALID" in _codes(unknown, None)


def test_invalid_declared_or_mixed_mea_denominators_block() -> None:
    mixed = _output(
        facts=[
            _fact("unit:1", "mea", {"kind": "MEA", "numerator": 1, "denominator": 1_000}),
            _fact("unit:2", "mea", {"kind": "MEA", "numerator": 1, "denominator": 1_000_000}),
        ],
        mea_set_status="PARTIAL",
    )
    missing = _output(
        facts=[_fact("unit:1", "mea", {"kind": "MEA", "numerator": 1, "denominator": 1_000})],
        mea_set_status="PARTIAL",
    )

    assert "MIXED_MEA_DENOMINATOR" in _codes(mixed)
    assert "MEA_DECLARED_DENOMINATOR_INVALID" in _codes(missing, None)


def test_invalid_evidence_page_and_duplicate_unit_number_block() -> None:
    out_of_range = _fact(
        "unit:1", "number", {"kind": "TEXT", "value": "1"}, page=5
    )
    duplicate = _fact("unit:2", "number", {"kind": "TEXT", "value": " 1 "})
    unknown_document = _fact(
        "unit:3", "location", {"kind": "TEXT", "value": "Hof"}, document_key="other"
    )
    output = _output(facts=[out_of_range, duplicate, unknown_document])

    assert {
        "UNKNOWN_EVIDENCE_DOCUMENT",
        "EVIDENCE_PAGE_OUT_OF_RANGE",
        "DUPLICATE_UNIT_NUMBER",
    } <= _codes(output)


def test_complete_homogeneous_percentage_and_fraction_allocations_are_valid() -> None:
    percentage = _rule(
        formula={
            "declared_complete": True,
            "parts": [
                {"label": "Verbrauch", "basis": "Verbrauch", "share": {"kind": "PERCENTAGE", "value": "70"}},
                {"label": "Fläche", "basis": "Fläche", "share": {"kind": "PERCENTAGE", "value": "30"}},
            ],
        }
    )
    fraction = _rule(
        "rule:lift",
        formula={
            "declared_complete": True,
            "parts": [
                {"label": "A", "basis": "A", "share": {"kind": "FRACTION", "numerator": 1, "denominator": 2}},
                {"label": "B", "basis": "B", "share": {"kind": "FRACTION", "numerator": 1, "denominator": 2}},
            ],
        },
    )

    assert "ALLOCATION_TOTAL_INVALID" not in _codes(_output(rules=[percentage, fraction]))


def test_incomplete_or_mixed_allocation_shares_are_blockers() -> None:
    incomplete = _rule(
        formula={
            "declared_complete": True,
            "parts": [
                {"label": "Verbrauch", "basis": "Verbrauch", "share": {"kind": "PERCENTAGE", "value": "90"}},
            ],
        }
    )
    mixed = _rule(
        "rule:mixed",
        formula={
            "declared_complete": True,
            "parts": [
                {"label": "A", "basis": "A", "share": {"kind": "PERCENTAGE", "value": "50"}},
                {"label": "B", "basis": "B", "share": {"kind": "FRACTION", "numerator": 1, "denominator": 2}},
            ],
        },
    )
    unsupported = _rule(
        "rule:text",
        formula={
            "declared_complete": False,
            "parts": [
                {"label": "A", "basis": "A", "share": {"kind": "TEXT", "value": "nach Verbrauch"}},
            ],
        },
    )

    codes = _codes(_output(rules=[incomplete, mixed, unsupported]))
    assert "ALLOCATION_TOTAL_INVALID" in codes
    assert "ALLOCATION_SHARE_TYPE_INVALID" in codes


def test_ambiguous_rule_is_preserved_for_clarification() -> None:
    rule = _rule(proposed_status="NEEDS_CLARIFICATION", clarification_needed=True)
    issues = validate_reconciliation(
        _output(rules=[rule]), page_counts={"base": 4}, declared_mea_denominator=1_000
    )

    assert any(
        issue.code == "RULE_NEEDS_CLARIFICATION" and issue.severity == "WARNING"
        for issue in issues
    )
    assert rule.proposed_status == "NEEDS_CLARIFICATION"


def test_unresolved_rule_relation_and_provider_issue_are_explicit() -> None:
    rule = _rule(conflicts=["rule:missing"])
    output = _output(rules=[rule])
    output = output.model_copy(update={"unresolved_issues": ["untrusted provider wording"]})
    issues = validate_reconciliation(
        output, page_counts={"base": 4}, declared_mea_denominator=1_000
    )

    assert {"UNKNOWN_RULE_RELATION_TARGET", "EFFECTIVE_RULE_CONFLICT_UNRESOLVED"} <= {
        issue.code for issue in issues
    }
    provider_issue = next(issue for issue in issues if issue.code == "PROVIDER_UNRESOLVED_ISSUE")
    assert provider_issue.severity == "WARNING"
    assert "untrusted" not in provider_issue.message
