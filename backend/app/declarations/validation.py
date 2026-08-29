from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from app.declarations.schemas import (
    FactPayload,
    FractionValue,
    MeaFraction,
    PercentageValue,
    ReconciliationOutput,
    RulePayload,
    TextValue,
)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: Literal["INFO", "WARNING", "BLOCKER"]
    message: str
    subject_key: str | None = None


def validate_reconciliation(
    output: ReconciliationOutput,
    page_counts: dict[str, int],
    declared_mea_denominator: int | None,
) -> list[ValidationIssue]:
    """Validate untrusted reconciliation data without resolving it automatically."""
    issues: list[ValidationIssue] = []
    subject_keys = _subject_keys(output)

    for payload in [*output.facts, *output.rules, *output.additional_facts]:
        issues.extend(_validate_evidence(payload.subject_key, payload.evidence, page_counts))

    issues.extend(_validate_conflicting_fact_alternatives(output))
    issues.extend(_validate_unit_numbers(output))
    issues.extend(_validate_rules(output, subject_keys))

    mea_values = _mea_values(output, issues)
    issues.extend(
        validate_mea_totals(
            mea_values,
            declared_mea_denominator,
            output.mea_set_status,
        )
    )
    issues.extend(validate_allocation_formulas(output.rules))
    issues.extend(
        ValidationIssue(
            code="PROVIDER_UNRESOLVED_ISSUE",
            severity="WARNING",
            message="Der Anbieter hat einen fachlichen Punkt nicht aufgelöst.",
        )
        for _ in output.unresolved_issues
    )
    return issues


def validate_mea_totals(
    mea_values: list[tuple[str, int, int]],
    declared_mea_denominator: int | None,
    mea_set_status: Literal["COMPLETE", "PARTIAL", "UNKNOWN"],
) -> list[ValidationIssue]:
    """Check shares with integer arithmetic and preserve partial amendment sets."""
    if not _is_positive_int(declared_mea_denominator):
        return [
            ValidationIssue(
                code="MEA_DECLARED_DENOMINATOR_INVALID",
                severity="BLOCKER",
                message="Der erklärte Nenner der Miteigentumsanteile fehlt oder ist ungültig.",
            )
        ]

    assert declared_mea_denominator is not None
    denominators = {denominator for _, _, denominator in mea_values}
    denominators.add(declared_mea_denominator)
    if len(denominators) > 1:
        return [
            ValidationIssue(
                code="MIXED_MEA_DENOMINATOR",
                severity="BLOCKER",
                message="Die Miteigentumsanteile verwenden mehrere Nenner.",
            )
        ]

    numerator_total = sum(numerator for _, numerator, _ in mea_values)
    if mea_set_status == "COMPLETE" and numerator_total != declared_mea_denominator:
        return [
            ValidationIssue(
                code="MEA_TOTAL_INVALID",
                severity="BLOCKER",
                message="Die vollständige Summe der Miteigentumsanteile stimmt nicht mit dem Nenner überein.",
            )
        ]
    if mea_set_status in {"PARTIAL", "UNKNOWN"} and numerator_total > declared_mea_denominator:
        return [
            ValidationIssue(
                code="MEA_TOTAL_INVALID",
                severity="BLOCKER",
                message="Die Summe der Miteigentumsanteile ist größer als der erklärte Nenner.",
            )
        ]
    if mea_set_status == "UNKNOWN" and numerator_total < declared_mea_denominator:
        return [
            ValidationIssue(
                code="MEA_TOTAL_STATUS_UNKNOWN",
                severity="WARNING",
                message="Der Erfassungsstand der Miteigentumsanteile ist noch nicht geklärt.",
            )
        ]
    return []


def validate_allocation_formulas(rules: list[RulePayload]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for rule in rules:
        formula = rule.effect.allocation_formula
        if formula is None:
            continue
        shares = [part.share for part in formula.parts]
        if all(isinstance(share, PercentageValue) for share in shares):
            total = sum((Decimal(share.value) for share in shares), Decimal("0"))
            if formula.declared_complete and total != Decimal("100"):
                issues.append(_allocation_total_invalid(rule.subject_key))
            continue
        if all(isinstance(share, FractionValue) for share in shares):
            total = sum(
                (Fraction(share.numerator, share.denominator) for share in shares),
                Fraction(0, 1),
            )
            if formula.declared_complete and total != Fraction(1, 1):
                issues.append(_allocation_total_invalid(rule.subject_key))
            continue
        issues.append(
            ValidationIssue(
                code="ALLOCATION_SHARE_TYPE_INVALID",
                severity="BLOCKER",
                message="Die Kostenverteilung enthält gemischte oder nicht rechnerisch prüfbare Anteile.",
                subject_key=rule.subject_key,
            )
        )
    return issues


def _subject_keys(output: ReconciliationOutput) -> set[str]:
    return {
        *(fact.subject_key for fact in output.facts),
        *(rule.subject_key for rule in output.rules),
        *(fact.subject_key for fact in output.additional_facts),
    }


def _validate_evidence(
    subject_key: str,
    evidence_items: list[object],
    page_counts: dict[str, int],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for evidence in evidence_items:
        document_key = evidence.document_key
        page_count = page_counts.get(document_key)
        if page_count is None:
            issues.append(
                ValidationIssue(
                    code="UNKNOWN_EVIDENCE_DOCUMENT",
                    severity="BLOCKER",
                    message="Die Belegdatei gehört nicht zum Import.",
                    subject_key=subject_key,
                )
            )
        elif evidence.pdf_page < 1 or evidence.pdf_page > page_count:
            issues.append(
                ValidationIssue(
                    code="EVIDENCE_PAGE_OUT_OF_RANGE",
                    severity="BLOCKER",
                    message="Die Belegseite liegt außerhalb des PDF.",
                    subject_key=subject_key,
                )
            )
    return issues


def _validate_conflicting_fact_alternatives(
    output: ReconciliationOutput,
) -> list[ValidationIssue]:
    values_by_key: dict[tuple[str, str], set[str]] = {}
    for fact in output.facts:
        key = (fact.subject_key, fact.field)
        value = _canonical_normalized_value(fact)
        values_by_key.setdefault(key, set()).add(value)
    return [
        ValidationIssue(
            code="CONFLICTING_FACT_ALTERNATIVES",
            severity="BLOCKER",
            message="Für denselben Gebäudewert liegen widersprüchliche Alternativen vor.",
            subject_key=subject_key,
        )
        for (subject_key, _), values in values_by_key.items()
        if len(values) > 1
    ]


def _validate_unit_numbers(output: ReconciliationOutput) -> list[ValidationIssue]:
    unit_numbers = [
        fact.normalized_value.value.strip().casefold()
        for fact in _distinct_facts(output)
        if fact.field == "number"
        and fact.subject_key.startswith("unit:")
        and isinstance(fact.normalized_value, TextValue)
    ]
    if len(unit_numbers) == len(set(unit_numbers)):
        return []
    return [
        ValidationIssue(
            code="DUPLICATE_UNIT_NUMBER",
            severity="BLOCKER",
            message="Eine Einheitsnummer kommt mehrfach vor.",
        )
    ]


def _validate_rules(
    output: ReconciliationOutput, subject_keys: set[str]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for rule in output.rules:
        targets = [
            *rule.scope.unit_subject_keys,
            *rule.supersedes_subject_keys,
            *rule.conflicts_with_subject_keys,
        ]
        for target in targets:
            if target not in subject_keys:
                issues.append(
                    ValidationIssue(
                        code="UNKNOWN_RULE_RELATION_TARGET",
                        severity="BLOCKER",
                        message="Eine Regel verweist auf ein unbekanntes Ziel.",
                        subject_key=rule.subject_key,
                    )
                )
        if rule.proposed_status == "EFFECTIVE" and rule.conflicts_with_subject_keys:
            issues.append(
                ValidationIssue(
                    code="EFFECTIVE_RULE_CONFLICT_UNRESOLVED",
                    severity="BLOCKER",
                    message="Eine wirksame Regel hat noch einen ungeklärten Widerspruch.",
                    subject_key=rule.subject_key,
                )
            )
        if rule.clarification_needed or rule.proposed_status == "NEEDS_CLARIFICATION":
            issues.append(
                ValidationIssue(
                    code="RULE_NEEDS_CLARIFICATION",
                    severity="WARNING",
                    message="Die Regel benötigt eine fachliche Klärung.",
                    subject_key=rule.subject_key,
                )
            )
    for fact in output.additional_facts:
        if fact.clarification_needed:
            issues.append(
                ValidationIssue(
                    code="ADDITIONAL_FACT_NEEDS_CLARIFICATION",
                    severity="WARNING",
                    message="Eine zusätzliche Gebäudeinformation benötigt eine fachliche Klärung.",
                    subject_key=fact.subject_key,
                )
            )
    return issues


def _mea_values(
    output: ReconciliationOutput, issues: list[ValidationIssue]
) -> list[tuple[str, int, int]]:
    values: list[tuple[str, int, int]] = []
    for fact in _distinct_facts(output):
        if fact.field != "mea":
            continue
        if not isinstance(fact.normalized_value, MeaFraction):
            issues.append(
                ValidationIssue(
                    code="MEA_VALUE_TYPE_INVALID",
                    severity="BLOCKER",
                    message="Ein Miteigentumsanteil hat keinen exakten Bruchwert.",
                    subject_key=fact.subject_key,
                )
            )
            continue
        values.append(
            (
                fact.subject_key,
                fact.normalized_value.numerator,
                fact.normalized_value.denominator,
            )
        )
    return values


def _allocation_total_invalid(subject_key: str) -> ValidationIssue:
    return ValidationIssue(
        code="ALLOCATION_TOTAL_INVALID",
        severity="BLOCKER",
        message="Die als vollständig erklärte Kostenverteilung ergibt rechnerisch nicht 100 Prozent.",
        subject_key=subject_key,
    )


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _distinct_facts(output: ReconciliationOutput) -> list[FactPayload]:
    """Keep same-value alternatives once while retaining every source elsewhere."""
    distinct: list[FactPayload] = []
    seen: set[tuple[str, str, str]] = set()
    for fact in output.facts:
        key = (fact.subject_key, fact.field, _canonical_normalized_value(fact))
        if key not in seen:
            seen.add(key)
            distinct.append(fact)
    return distinct


def _canonical_normalized_value(fact: FactPayload) -> str:
    return json.dumps(
        fact.normalized_value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
