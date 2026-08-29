"""Deterministic, offline metrics for synthetic declaration-knowledge cases.

This module intentionally has no eager OpenAI SDK import or provider
construction.  The ordinary CLI evaluates only redacted JSON fixtures.  The
separate real-PDF smoke path constructs the bounded production adapter only
after all explicit live gates have passed.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence, runtime_checkable


_METRIC_QUANTUM = Decimal("0.000001")
_SAFE_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SAFE_PASS_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SAFE_FAILURE_CLASS = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SAFE_MODEL_ID = re.compile(r"^[a-z0-9.-]{1,128}$")
_FORBIDDEN_FIXTURE_KEY_PARTS = ("source_text", "path", "pdf", "signature")
_QUOTE_HASH = re.compile(r"^[a-f0-9]{64}$")
_COMPARISON_LABELS = (
    "luna_medium",
    "luna_low",
    "terra_primary",
    "luna_with_mini_router",
)
_ROUTE_ACTIONS = frozenset({"NONE", "VISION_RECHECK", "HUMAN_REVIEW"})
_ROUTE_REASON_CODES = frozenset(
    {
        "EVIDENCE_QUOTE_NOT_FOUND",
        "LOW_PAGE_QUALITY",
        "PROVIDER_REFUSAL",
        "PROVIDER_INCOMPLETE",
        "PROVIDER_TIMEOUT",
        "PROVIDER_SCHEMA_FAILURE",
        "UNKNOWN_EVIDENCE_DOCUMENT",
        "EVIDENCE_PAGE_OUT_OF_RANGE",
        "DOMAIN_BLOCKER",
    }
)
_LIVE_CAPABILITY_SEAL = object()


def _metric(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return Decimal("1")
    return (Decimal(numerator) / Decimal(denominator)).quantize(
        _METRIC_QUANTUM, rounding=ROUND_HALF_UP
    )


def _decimal(value: Any, *, field_name: str) -> Decimal:
    if isinstance(value, float):
        raise ValueError(f"{field_name} must not use float")
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{field_name} must be finite and non-negative")
    return parsed


def _positive_int(value: Any, *, field_name: str, allow_zero: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or (not allow_zero and value == 0):
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _canonical_json(value: Any) -> str:
    _reject_float_values(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _normalized_quote_hash(value: str) -> str:
    """Hash a quote transiently with the product evidence normalization."""
    if not isinstance(value, str):
        raise ValueError("evidence quote must be a string")
    from app.declarations.evidence import normalize_evidence_text

    normalized = normalize_evidence_text(value)
    if not normalized:
        raise ValueError("evidence quote must not be empty")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _reject_float_values(value: Any) -> None:
    if isinstance(value, float):
        raise ValueError("float values are not permitted in legal or cost metrics")
    if isinstance(value, Mapping):
        for child in value.values():
            _reject_float_values(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_float_values(child)


@dataclass(frozen=True)
class CoreFact:
    """A synthetic core fact; MEA values stay integer fractions end to end."""

    subject_id: str
    field_key: str
    value: str | None = None
    numerator: int | None = None
    denominator: int | None = None

    def fraction(self) -> Fraction | None:
        if self.field_key != "mea":
            return None
        if self.numerator is None or self.denominator is None:
            raise ValueError("MEA facts require numerator and denominator")
        if (
            isinstance(self.numerator, bool)
            or isinstance(self.denominator, bool)
            or not isinstance(self.numerator, int)
            or not isinstance(self.denominator, int)
        ):
            raise ValueError("MEA parts must be integers")
        if self.denominator <= 0:
            raise ValueError("MEA denominator must be positive")
        return Fraction(self.numerator, self.denominator)

    def identity(self) -> tuple[str, str]:
        return self.subject_id, self.field_key

    def normalized_value(self) -> tuple[int, int] | str | None:
        fraction = self.fraction()
        if fraction is not None:
            return fraction.numerator, fraction.denominator
        return self.value

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"subject_id": self.subject_id, "field_key": self.field_key}
        if self.field_key == "mea":
            data.update({"numerator": self.numerator, "denominator": self.denominator})
        else:
            data["value"] = self.value
        return data


def mea_fact(subject_id: str, numerator: int, denominator: int) -> CoreFact:
    return CoreFact(subject_id=subject_id, field_key="mea", numerator=numerator, denominator=denominator)


def _coerce_fact(value: CoreFact | Mapping[str, Any]) -> CoreFact:
    if isinstance(value, CoreFact):
        value.fraction()
        return value
    if not isinstance(value, Mapping):
        raise ValueError("core facts must be mappings")
    subject_id = value.get("subject_id")
    field_key = value.get("field_key")
    if not isinstance(subject_id, str) or not subject_id or not isinstance(field_key, str) or not field_key:
        raise ValueError("core facts require synthetic subject_id and field_key")
    if field_key == "mea":
        fact = CoreFact(
            subject_id=subject_id,
            field_key=field_key,
            numerator=value.get("numerator"),
            denominator=value.get("denominator"),
        )
        fact.fraction()
        return fact
    raw_value = value.get("value")
    if raw_value is not None and not isinstance(raw_value, str):
        raise ValueError("non-MEA fact values must be strings")
    return CoreFact(subject_id=subject_id, field_key=field_key, value=raw_value)


@dataclass(frozen=True)
class CoreFactMetrics:
    normalized_precision: Decimal
    normalized_recall: Decimal
    normalized_f1: Decimal
    normalized_exact_matches: int
    expected_count: int
    actual_count: int
    exact_property_identity: bool
    exact_unit_count: bool
    mea_exact_matches: int
    mea_expected_count: int
    mea_denominator_consistency: Decimal


def evaluate_core_facts(
    *, expected: Sequence[CoreFact | Mapping[str, Any]], actual: Sequence[CoreFact | Mapping[str, Any]]
) -> CoreFactMetrics:
    expected_facts = tuple(_coerce_fact(item) for item in expected)
    actual_facts = tuple(_coerce_fact(item) for item in actual)
    expected_signatures = Counter((fact.identity(), fact.normalized_value()) for fact in expected_facts)
    actual_signatures = Counter((fact.identity(), fact.normalized_value()) for fact in actual_facts)
    exact_matches = sum((expected_signatures & actual_signatures).values())
    expected_mea = [fact for fact in expected_facts if fact.field_key == "mea"]
    actual_mea = [fact for fact in actual_facts if fact.field_key == "mea"]
    mea_matches = sum(
        (
            Counter((fact.identity(), fact.fraction()) for fact in expected_mea)
            & Counter((fact.identity(), fact.fraction()) for fact in actual_mea)
        ).values()
    )
    same_denominator = sum(
        (
            Counter((fact.identity(), fact.fraction(), fact.denominator) for fact in expected_mea)
            & Counter((fact.identity(), fact.fraction(), fact.denominator) for fact in actual_mea)
        ).values()
    )

    def _same_singleton(field_key: str) -> bool:
        expected_values = [fact.normalized_value() for fact in expected_facts if fact.field_key == field_key]
        actual_values = [fact.normalized_value() for fact in actual_facts if fact.field_key == field_key]
        return len(expected_values) == 1 and expected_values == actual_values

    precision = _metric(exact_matches, len(actual_facts))
    recall = _metric(exact_matches, len(expected_facts))
    f1 = (
        Decimal("0")
        if precision + recall == 0
        else ((Decimal("2") * precision * recall) / (precision + recall)).quantize(
            _METRIC_QUANTUM, rounding=ROUND_HALF_UP
        )
    )
    return CoreFactMetrics(
        normalized_precision=precision,
        normalized_recall=recall,
        normalized_f1=f1,
        normalized_exact_matches=exact_matches,
        expected_count=len(expected_facts),
        actual_count=len(actual_facts),
        exact_property_identity=_same_singleton("property_identity"),
        exact_unit_count=_same_singleton("unit_count"),
        mea_exact_matches=mea_matches,
        mea_expected_count=len(expected_mea),
        mea_denominator_consistency=_metric(same_denominator, len(expected_mea)),
    )


@dataclass(frozen=True)
class EvaluationPass:
    pass_id: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: Decimal
    latency_ms: int
    retry_count: int
    failure_class: str | None
    route_action: str = "NONE"
    route_reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.pass_id, str) or not _SAFE_PASS_ID.fullmatch(self.pass_id):
            raise ValueError("passes require pass_id")
        _positive_int(self.input_tokens, field_name="input_tokens")
        _positive_int(self.output_tokens, field_name="output_tokens")
        _positive_int(self.latency_ms, field_name="latency_ms")
        _positive_int(self.retry_count, field_name="retry_count")
        if (
            not isinstance(self.estimated_cost_usd, Decimal)
            or not self.estimated_cost_usd.is_finite()
            or self.estimated_cost_usd < 0
        ):
            raise ValueError("estimated_cost_usd must be a finite non-negative Decimal")
        if self.failure_class is not None and (
            not isinstance(self.failure_class, str)
            or not _SAFE_FAILURE_CLASS.fullmatch(self.failure_class)
        ):
            raise ValueError("failure_class must be a sanitized string or null")
        if not isinstance(self.route_action, str) or self.route_action not in _ROUTE_ACTIONS:
            raise ValueError("route_action must be a safe router action")
        if not isinstance(self.route_reason_codes, tuple) or any(
            reason not in _ROUTE_REASON_CODES for reason in self.route_reason_codes
        ):
            raise ValueError("route_reason_codes must be safe router reasons")

    def as_dict(self) -> dict[str, Any]:
        return {
            "pass_id": self.pass_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": str(self.estimated_cost_usd),
            "latency_ms": self.latency_ms,
            "retry_count": self.retry_count,
            "failure_class": self.failure_class,
            "route_action": self.route_action,
            "route_reason_codes": list(self.route_reason_codes),
        }


def _coerce_pass(value: EvaluationPass | Mapping[str, Any]) -> EvaluationPass:
    if isinstance(value, EvaluationPass):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("passes must be mappings")
    pass_id = value.get("pass_id")
    if not isinstance(pass_id, str) or not _SAFE_PASS_ID.fullmatch(pass_id):
        raise ValueError("passes require pass_id")
    failure_class = value.get("failure_class")
    if failure_class is not None and (
        not isinstance(failure_class, str) or not _SAFE_FAILURE_CLASS.fullmatch(failure_class)
    ):
        raise ValueError("failure_class must be a string or null")
    raw_route_reasons = value.get("route_reason_codes", ())
    if not isinstance(raw_route_reasons, Sequence) or isinstance(raw_route_reasons, (str, bytes)):
        raise ValueError("route_reason_codes must be a list")
    return EvaluationPass(
        pass_id=pass_id,
        input_tokens=_positive_int(value.get("input_tokens"), field_name="input_tokens"),
        output_tokens=_positive_int(value.get("output_tokens"), field_name="output_tokens"),
        estimated_cost_usd=_decimal(value.get("estimated_cost_usd"), field_name="estimated_cost_usd"),
        latency_ms=_positive_int(value.get("latency_ms"), field_name="latency_ms"),
        retry_count=_positive_int(value.get("retry_count"), field_name="retry_count"),
        failure_class=failure_class,
        route_action=value.get("route_action", "NONE"),
        route_reason_codes=tuple(raw_route_reasons),
    )


def gold_case(
    *,
    case_id: str,
    allowed_source_ids: set[str],
    expected_status: str,
    core_facts: Sequence[CoreFact | Mapping[str, Any]] = (),
    rules: Sequence[Mapping[str, Any]] = (),
    document_rule_relations: Sequence[tuple[str, str] | list[str]] = (),
    blockers: set[str] | None = None,
    retrieval_relevant_ids: set[str] | None = None,
    expected_supported_source_ids: set[str] | None = None,
    tenant_id: str = "tenant-eval",
    evidence_quotes: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    safe_rules = deepcopy([dict(item) for item in rules])
    rules_by_id = {item.get("id"): item for item in safe_rules}
    for item in evidence_quotes:
        quote = _as_mapping(item, label="evidence quote")
        rule_id, source_id, page, raw_quote = (
            quote.get("rule_id"),
            quote.get("source_id"),
            quote.get("page"),
            quote.get("quote"),
        )
        if (
            not isinstance(rule_id, str)
            or rule_id not in rules_by_id
            or not isinstance(source_id, str)
            or not source_id
        ):
            raise ValueError("evidence quote requires a known rule and source")
        page_number = _positive_int(page, field_name="evidence quote page", allow_zero=False)
        quote_hash = _normalized_quote_hash(raw_quote)
        evidence = rules_by_id[rule_id].get("evidence")
        if not isinstance(evidence, list):
            raise ValueError("evidence quote rule requires evidence")
        for citation in evidence:
            if (
                isinstance(citation, Mapping)
                and citation.get("source_id") == source_id
                and citation.get("page") == page_number
            ):
                citation["quote_hash"] = quote_hash
                break
        else:
            raise ValueError("evidence quote must reference rule evidence")
    return {
        "id": case_id,
        "allowed_source_ids": sorted(allowed_source_ids),
        "expected_status": expected_status,
        "core_facts": [_coerce_fact(item).as_dict() for item in core_facts],
        "rules": safe_rules,
        "document_rule_relations": [list(item) for item in document_rule_relations],
        "blockers": sorted(blockers or set()),
        "retrieval_relevant_ids": sorted(retrieval_relevant_ids or set()),
        "expected_supported_source_ids": sorted(expected_supported_source_ids or allowed_source_ids),
        "tenant_id": tenant_id,
    }


def actual_answer(*, status: str, source_ids: Sequence[str]) -> dict[str, Any]:
    return {"status": status, "source_ids": list(source_ids)}


def _as_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _string_set(value: Any, *, label: str) -> set[str]:
    return set(_string_list(value, label=label))


def _string_list(value: Any, *, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be a list")
    if not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{label} must contain non-empty strings")
    return tuple(value)


def _rule_index(items: Any) -> dict[str, Mapping[str, Any]]:
    if items is None:
        return {}
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise ValueError("rules must be a list")
    indexed: dict[str, Mapping[str, Any]] = {}
    for item in items:
        rule = _as_mapping(item, label="rule")
        rule_id = rule.get("id")
        topic = rule.get("topic")
        if not isinstance(rule_id, str) or not rule_id or not isinstance(topic, str) or not topic:
            raise ValueError("rules require id and topic")
        _reject_float_values(rule)
        if rule_id in indexed:
            raise ValueError("duplicate rule IDs are invalid")
        _evidence_pairs(rule)
        indexed[rule_id] = rule
    return indexed


def _evidence_entries(rule: Mapping[str, Any]) -> tuple[tuple[str, int, str | None], ...]:
    evidence = rule.get("evidence", [])
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
        raise ValueError("rule evidence must be a list")
    entries: set[tuple[str, int, str | None]] = set()
    pairs: set[tuple[str, int]] = set()
    for item in evidence:
        source = _as_mapping(item, label="evidence")
        source_id = source.get("source_id")
        page = source.get("page")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("evidence requires source_id")
        quote_hash = source.get("quote_hash")
        if quote_hash is not None and (
            not isinstance(quote_hash, str) or not _QUOTE_HASH.fullmatch(quote_hash)
        ):
            raise ValueError("evidence quote_hash must be lowercase SHA-256")
        if any("quote" in str(key).lower() and key != "quote_hash" for key in source):
            raise ValueError("evidence must not retain raw quote content")
        entry = (
            source_id,
            _positive_int(page, field_name="page", allow_zero=False),
            quote_hash,
        )
        pair = entry[:2]
        if entry in entries or pair in pairs:
            raise ValueError("duplicate rule evidence is invalid")
        entries.add(entry)
        pairs.add(pair)
    return tuple(sorted(entries))


def _evidence_pairs(rule: Mapping[str, Any]) -> set[tuple[str, int]]:
    return {(source_id, page) for source_id, page, _quote_hash in _evidence_entries(rule)}


def _evidence_quote_set(rule: Mapping[str, Any]) -> set[tuple[str, int, str]]:
    return {
        (source_id, page, quote_hash)
        for source_id, page, quote_hash in _evidence_entries(rule)
        if quote_hash is not None
    }


def _relation_set(value: Any) -> set[tuple[str, str]]:
    if value is None:
        return set()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("document_rule_relations must be a list")
    relations: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes)) or len(item) != 2:
            raise ValueError("document_rule_relations entries must be pairs")
        document_id, rule_id = item
        if not isinstance(document_id, str) or not isinstance(rule_id, str):
            raise ValueError("document_rule_relations entries must be string pairs")
        relation = (document_id, rule_id)
        if relation in relations:
            raise ValueError("duplicate document_rule_relations are invalid")
        relations.add(relation)
    return relations


def _unit_breakdown(value: Any) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("unit_breakdown must be a mapping")
    parsed: dict[str, int] = {}
    for kind, count in value.items():
        if not isinstance(kind, str) or not _SAFE_PASS_ID.fullmatch(kind):
            raise ValueError("unit_breakdown requires neutral kinds")
        parsed[kind] = _positive_int(count, field_name="unit_breakdown", allow_zero=False)
    return parsed


def _assignment_ids(value: Any) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, Mapping):
        raise ValueError("assignments must be a mapping")
    seen: set[str] = set()
    for kind, identifiers in value.items():
        if not isinstance(kind, str) or not _SAFE_PASS_ID.fullmatch(kind):
            raise ValueError("assignments require neutral kinds")
        values = _string_list(identifiers, label="assignments")
        if len(values) != len(set(values)):
            raise ValueError("duplicate assignments are invalid")
        seen.update(f"{kind}:{identifier}" for identifier in values)
    return seen


def _lineage_ids(value: Any) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, Mapping):
        raise ValueError("amendment_lineage must be a mapping")
    allowed = {"split_from", "split_to"}
    if not set(value) <= allowed:
        raise ValueError("amendment_lineage contains unsupported keys")
    seen: set[str] = set()
    for kind, identifiers in value.items():
        values = _string_list(identifiers, label="amendment_lineage")
        if len(values) != len(set(values)):
            raise ValueError("duplicate amendment lineage identifiers are invalid")
        seen.update(f"{kind}:{identifier}" for identifier in values)
    return seen


def _set_coverage(expected: set[Any], actual: set[Any]) -> Decimal:
    matches = len(expected & actual)
    return min(_metric(matches, len(expected)), _metric(matches, len(actual)))


@dataclass(frozen=True)
class CaseEvaluationReport:
    case_id: str
    candidate_schema_valid: bool
    core: CoreFactMetrics
    rule_topic_coverage: Decimal
    rule_structured_effect_match: Decimal
    rule_structured_effect_exact_matches: int
    rule_structured_effect_expected_count: int
    rule_structured_effect_actual_count: int
    rule_evidence_page_accuracy: Decimal
    evidence_page_exact_matches: int
    evidence_page_expected_count: int
    evidence_page_actual_count: int
    evidence_quote_text_accuracy: Decimal
    evidence_quote_exact_matches: int
    evidence_quote_expected_count: int
    evidence_quote_actual_count: int
    heating_effect_accuracy: Decimal
    heating_effect_exact_matches: int
    heating_effect_expected_count: int
    heating_effect_actual_count: int
    amendment_effect_accuracy: Decimal
    amendment_effect_exact_matches: int
    amendment_effect_expected_count: int
    amendment_effect_actual_count: int
    adversarial_data_outcome: Decimal
    adversarial_expected_count: int
    adversarial_exact_matches: int
    mini_router_compliance: Decimal
    mini_router_expected_count: int
    mini_router_exact_matches: int
    document_rule_relation_precision: Decimal
    document_rule_relation_recall: Decimal
    blocker_recall: Decimal
    retrieval_recall_at_5: Decimal
    retrieval_mrr: Decimal
    qa_status_accuracy: Decimal
    qa_citation_validity: Decimal
    qa_source_support: Decimal
    qa_unsupported_answer_count: int
    qa_abstention_accuracy: Decimal
    total_input_tokens: int
    total_output_tokens: int
    total_estimated_cost_usd: Decimal
    total_latency_ms: int
    total_retry_count: int
    failure_classes: tuple[str, ...]
    tenant_leakage_count: int
    unit_assignment_coverage: Decimal
    unit_assignment_expected_count: int
    passes: tuple[EvaluationPass, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "candidate_schema_valid": self.candidate_schema_valid,
            "core": {
                "normalized_precision": str(self.core.normalized_precision),
                "normalized_recall": str(self.core.normalized_recall),
                "normalized_f1": str(self.core.normalized_f1),
                "normalized_exact_matches": self.core.normalized_exact_matches,
                "exact_property_identity": self.core.exact_property_identity,
                "exact_unit_count": self.core.exact_unit_count,
                "mea_exact_matches": self.core.mea_exact_matches,
                "mea_expected_count": self.core.mea_expected_count,
                "mea_denominator_consistency": str(self.core.mea_denominator_consistency),
            },
            "rule_topic_coverage": str(self.rule_topic_coverage),
            "rule_structured_effect_match": str(self.rule_structured_effect_match),
            "rule_structured_effect_exact_matches": self.rule_structured_effect_exact_matches,
            "rule_structured_effect_expected_count": self.rule_structured_effect_expected_count,
            "rule_structured_effect_actual_count": self.rule_structured_effect_actual_count,
            "rule_evidence_page_accuracy": str(self.rule_evidence_page_accuracy),
            "evidence_page_exact_matches": self.evidence_page_exact_matches,
            "evidence_page_expected_count": self.evidence_page_expected_count,
            "evidence_page_actual_count": self.evidence_page_actual_count,
            "evidence_quote_text_accuracy": str(self.evidence_quote_text_accuracy),
            "evidence_quote_exact_matches": self.evidence_quote_exact_matches,
            "evidence_quote_expected_count": self.evidence_quote_expected_count,
            "evidence_quote_actual_count": self.evidence_quote_actual_count,
            "heating_effect_accuracy": str(self.heating_effect_accuracy),
            "heating_effect_exact_matches": self.heating_effect_exact_matches,
            "heating_effect_expected_count": self.heating_effect_expected_count,
            "heating_effect_actual_count": self.heating_effect_actual_count,
            "amendment_effect_accuracy": str(self.amendment_effect_accuracy),
            "amendment_effect_exact_matches": self.amendment_effect_exact_matches,
            "amendment_effect_expected_count": self.amendment_effect_expected_count,
            "amendment_effect_actual_count": self.amendment_effect_actual_count,
            "adversarial_data_outcome": str(self.adversarial_data_outcome),
            "adversarial_expected_count": self.adversarial_expected_count,
            "adversarial_exact_matches": self.adversarial_exact_matches,
            "mini_router_compliance": str(self.mini_router_compliance),
            "mini_router_expected_count": self.mini_router_expected_count,
            "mini_router_exact_matches": self.mini_router_exact_matches,
            "document_rule_relation_precision": str(self.document_rule_relation_precision),
            "document_rule_relation_recall": str(self.document_rule_relation_recall),
            "blocker_recall": str(self.blocker_recall),
            "retrieval_recall_at_5": str(self.retrieval_recall_at_5),
            "retrieval_mrr": str(self.retrieval_mrr),
            "qa_status_accuracy": str(self.qa_status_accuracy),
            "qa_citation_validity": str(self.qa_citation_validity),
            "qa_source_support": str(self.qa_source_support),
            "qa_unsupported_answer_count": self.qa_unsupported_answer_count,
            "qa_abstention_accuracy": str(self.qa_abstention_accuracy),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_estimated_cost_usd": str(self.total_estimated_cost_usd),
            "total_latency_ms": self.total_latency_ms,
            "total_retry_count": self.total_retry_count,
            "failure_classes": list(self.failure_classes),
            "tenant_leakage_count": self.tenant_leakage_count,
            "unit_assignment_coverage": str(self.unit_assignment_coverage),
            "unit_assignment_expected_count": self.unit_assignment_expected_count,
            "passes": [item.as_dict() for item in self.passes],
        }


def evaluate_case(*, gold: Mapping[str, Any], actual: Mapping[str, Any]) -> CaseEvaluationReport:
    gold = _as_mapping(gold, label="gold")
    actual = _as_mapping(actual, label="actual")
    case_id = gold.get("id", gold.get("case_id", "case-eval"))
    if not isinstance(case_id, str) or not _SAFE_CASE_ID.fullmatch(case_id):
        raise ValueError("case IDs must be neutral identifiers")

    core = evaluate_core_facts(
        expected=gold.get("core_facts", ()), actual=actual.get("core_facts", ())
    )
    gold_rules = _rule_index(gold.get("rules", ()))
    actual_rules = _rule_index(actual.get("rules", ()))
    gold_topics = {rule["topic"] for rule in gold_rules.values()}
    actual_topics = {rule["topic"] for rule in actual_rules.values()}
    topic_coverage = _metric(len(gold_topics & actual_topics), len(gold_topics))
    effect_matches = sum(
        1
        for rule_id, rule in gold_rules.items()
        if rule_id in actual_rules
        and _canonical_json(rule.get("effect", {})) == _canonical_json(actual_rules[rule_id].get("effect", {}))
    )
    expected_evidence_pages = {
        (rule_id, source_id, page)
        for rule_id, rule in gold_rules.items()
        for source_id, page in _evidence_pairs(rule)
    }
    actual_evidence_pages = {
        (rule_id, source_id, page)
        for rule_id, rule in actual_rules.items()
        for source_id, page in _evidence_pairs(rule)
    }
    evidence_matches = sum(
        1
        for rule_id, rule in gold_rules.items()
        if rule_id in actual_rules and _evidence_pairs(rule) == _evidence_pairs(actual_rules[rule_id])
    )
    expected_quote_evidence = {
        item for rule in gold_rules.values() for item in _evidence_quote_set(rule)
    }
    actual_quote_evidence = {
        item for rule in actual_rules.values() for item in _evidence_quote_set(rule)
    }
    quote_exact_matches = len(expected_quote_evidence & actual_quote_evidence)
    expected_heating_effects = {
        (rule_id, _canonical_json(rule.get("effect", {})))
        for rule_id, rule in gold_rules.items()
        if rule.get("topic") == "heating_costs"
    }
    actual_heating_effects = {
        (rule_id, _canonical_json(rule.get("effect", {})))
        for rule_id, rule in actual_rules.items()
        if rule.get("topic") == "heating_costs"
    }
    heating_exact_matches = len(expected_heating_effects & actual_heating_effects)

    expected_relations = _relation_set(gold.get("document_rule_relations", ()))
    actual_relations = _relation_set(actual.get("document_rule_relations", ()))
    relation_matches = len(expected_relations & actual_relations)
    expected_blockers = _string_set(gold.get("blockers", ()), label="blockers")
    actual_blockers = _string_set(actual.get("blockers", ()), label="blockers")
    relevant_ids = _string_set(gold.get("retrieval_relevant_ids", ()), label="retrieval_relevant_ids")
    _string_set(actual.get("retrieved_ids", ()), label="retrieved_ids")
    # Preserve ranking in actual retrieval lists; sets above only validate elements.
    raw_retrieved = actual.get("retrieved_ids", ())
    if not isinstance(raw_retrieved, Sequence) or isinstance(raw_retrieved, (str, bytes)):
        raise ValueError("retrieved_ids must be a list")
    ranked_ids = [item for item in raw_retrieved if isinstance(item, str)][:5]
    retrieval_hits = set(ranked_ids) & relevant_ids
    reciprocal_rank = Decimal("0")
    for index, hit_id in enumerate(ranked_ids, start=1):
        if hit_id in relevant_ids:
            reciprocal_rank = _metric(1, index)
            break

    # Public helper ``actual_answer`` is also accepted directly for small,
    # focused metric examples; fixture cases carry it under ``qa``.
    qa = _as_mapping(actual.get("qa", actual), label="qa")
    qa_status = qa.get("status")
    source_id_list = _string_list(qa.get("source_ids", ()), label="qa source_ids")
    source_ids = set(source_id_list)
    expected_status = gold.get("expected_status")
    if qa_status not in {"ANSWER", "NOT_DOCUMENTED", "NEEDS_CLARIFICATION"}:
        raise ValueError("qa status is invalid")
    if expected_status not in {"ANSWER", "NOT_DOCUMENTED", "NEEDS_CLARIFICATION"}:
        raise ValueError("gold expected_status is invalid")
    allowed_source_ids = _string_set(gold.get("allowed_source_ids", ()), label="allowed_source_ids")
    supported_source_ids = _string_set(
        gold.get("expected_supported_source_ids", allowed_source_ids), label="expected_supported_source_ids"
    )
    duplicate_citations = len(source_id_list) != len(source_ids)
    requires_citation = qa_status in {"ANSWER", "NEEDS_CLARIFICATION"}
    citations_valid = (
        not duplicate_citations
        and (not requires_citation or bool(source_id_list))
        and source_ids <= allowed_source_ids
    )
    source_supported = citations_valid and source_ids <= supported_source_ids
    unsupported = int(
        qa_status == "ANSWER"
        and (not source_id_list or not citations_valid or not source_supported or expected_status != "ANSWER")
    )
    expected_tenant = gold.get("tenant_id", "tenant-eval")
    if not isinstance(expected_tenant, str) or not expected_tenant:
        raise ValueError("tenant_id must be a neutral identifier")
    seen_tenants = _string_set(actual.get("tenant_ids", ()), label="tenant_ids")
    tenant_leakage = len(seen_tenants - {expected_tenant})

    passes = tuple(_coerce_pass(item) for item in actual.get("passes", ()))
    expected_breakdown = _unit_breakdown(gold.get("unit_breakdown", {}))
    actual_breakdown = _unit_breakdown(actual.get("unit_breakdown", {}))
    expected_assignments = _assignment_ids(gold.get("assignments", {}))
    actual_assignments = _assignment_ids(actual.get("assignments", {}))
    expected_lineage = _lineage_ids(gold.get("amendment_lineage", {}))
    actual_lineage = _lineage_ids(actual.get("amendment_lineage", {}))
    amendment_exact_matches = len(expected_lineage & actual_lineage)
    adversarial_expected = gold.get("adversarial_expected_outcome")
    if adversarial_expected is not None and adversarial_expected not in {
        "ANSWER",
        "NOT_DOCUMENTED",
        "NEEDS_CLARIFICATION",
    }:
        raise ValueError("adversarial_expected_outcome is invalid")
    adversarial_marker = gold.get("adversarial_document_marker")
    if adversarial_marker is not None and (
        not isinstance(adversarial_marker, str)
        or not _SAFE_PASS_ID.fullmatch(adversarial_marker)
    ):
        raise ValueError("adversarial document marker is invalid")
    observed_adversarial_marker = actual.get("adversarial_marker_observed")
    adversarial_actions = actual.get("adversarial_action_count", 0)
    if isinstance(adversarial_actions, bool) or not isinstance(adversarial_actions, int) or adversarial_actions < 0:
        raise ValueError("adversarial action count is invalid")
    expected_router_action = gold.get("expected_router_action")
    expected_router_reasons = gold.get("expected_router_reason_codes", ())
    actual_router_action = actual.get("router_action", "NONE")
    actual_router_reasons = actual.get("router_reason_codes", ())
    for action, reasons, label in (
        (expected_router_action, expected_router_reasons, "expected router"),
        (actual_router_action, actual_router_reasons, "actual router"),
    ):
        if action is None and label == "expected router":
            continue
        if not isinstance(action, str) or action not in _ROUTE_ACTIONS:
            raise ValueError(f"{label} action is invalid")
        if not isinstance(reasons, Sequence) or isinstance(reasons, (str, bytes)) or any(
            reason not in _ROUTE_REASON_CODES for reason in reasons
        ):
            raise ValueError(f"{label} reasons are invalid")
    router_expected_count = int(expected_router_action is not None)
    router_exact_matches = int(
        expected_router_action is not None
        and actual_router_action == expected_router_action
        and tuple(actual_router_reasons) == tuple(expected_router_reasons)
    )
    coverage_parts = [
        _set_coverage(set(expected_breakdown.items()), set(actual_breakdown.items())),
        _set_coverage(expected_assignments, actual_assignments),
        _set_coverage(expected_lineage, actual_lineage),
    ]
    return CaseEvaluationReport(
        case_id=case_id,
        candidate_schema_valid=actual.get("candidate_schema_valid", False) is True,
        core=core,
        rule_topic_coverage=topic_coverage,
        rule_structured_effect_match=_metric(effect_matches, len(gold_rules)),
        rule_structured_effect_exact_matches=effect_matches,
        rule_structured_effect_expected_count=len(gold_rules),
        rule_structured_effect_actual_count=len(actual_rules),
        rule_evidence_page_accuracy=_metric(evidence_matches, len(gold_rules)),
        evidence_page_exact_matches=len(expected_evidence_pages & actual_evidence_pages),
        evidence_page_expected_count=len(expected_evidence_pages),
        evidence_page_actual_count=len(actual_evidence_pages),
        evidence_quote_text_accuracy=_set_coverage(expected_quote_evidence, actual_quote_evidence),
        evidence_quote_exact_matches=quote_exact_matches,
        evidence_quote_expected_count=len(expected_quote_evidence),
        evidence_quote_actual_count=len(actual_quote_evidence),
        heating_effect_accuracy=_set_coverage(expected_heating_effects, actual_heating_effects),
        heating_effect_exact_matches=heating_exact_matches,
        heating_effect_expected_count=len(expected_heating_effects),
        heating_effect_actual_count=len(actual_heating_effects),
        amendment_effect_accuracy=_set_coverage(expected_lineage, actual_lineage),
        amendment_effect_exact_matches=amendment_exact_matches,
        amendment_effect_expected_count=len(expected_lineage),
        amendment_effect_actual_count=len(actual_lineage),
        adversarial_data_outcome=(
            Decimal("1")
            if adversarial_expected is None
            or (
                qa_status == adversarial_expected
                and observed_adversarial_marker == adversarial_marker
                and adversarial_actions == 0
                and actual_router_action == "NONE"
                and tuple(actual_router_reasons) == ()
            )
            else Decimal("0")
        ),
        adversarial_expected_count=0 if adversarial_expected is None else 1,
        adversarial_exact_matches=(
            0
            if adversarial_expected is None
            else int(
                qa_status == adversarial_expected
                and observed_adversarial_marker == adversarial_marker
                and adversarial_actions == 0
                and actual_router_action == "NONE"
                and tuple(actual_router_reasons) == ()
            )
        ),
        mini_router_compliance=_metric(router_exact_matches, router_expected_count),
        mini_router_expected_count=router_expected_count,
        mini_router_exact_matches=router_exact_matches,
        document_rule_relation_precision=_metric(relation_matches, len(actual_relations)),
        document_rule_relation_recall=_metric(relation_matches, len(expected_relations)),
        blocker_recall=_metric(len(expected_blockers & actual_blockers), len(expected_blockers)),
        retrieval_recall_at_5=_metric(len(retrieval_hits), len(relevant_ids)),
        retrieval_mrr=reciprocal_rank if relevant_ids else Decimal("1"),
        qa_status_accuracy=Decimal("1") if qa_status == expected_status else Decimal("0"),
        qa_citation_validity=Decimal("1") if citations_valid else Decimal("0"),
        qa_source_support=Decimal("1") if source_supported else Decimal("0"),
        qa_unsupported_answer_count=unsupported,
        qa_abstention_accuracy=(
            Decimal("1")
            if expected_status in {"NOT_DOCUMENTED", "NEEDS_CLARIFICATION"} and qa_status == expected_status
            else Decimal("0")
            if expected_status in {"NOT_DOCUMENTED", "NEEDS_CLARIFICATION"}
            else Decimal("1")
        ),
        total_input_tokens=sum(item.input_tokens for item in passes),
        total_output_tokens=sum(item.output_tokens for item in passes),
        total_estimated_cost_usd=sum((item.estimated_cost_usd for item in passes), Decimal("0")),
        total_latency_ms=sum(item.latency_ms for item in passes),
        total_retry_count=sum(item.retry_count for item in passes),
        failure_classes=tuple(sorted({item.failure_class for item in passes if item.failure_class})),
        tenant_leakage_count=tenant_leakage,
        unit_assignment_coverage=min(coverage_parts),
        unit_assignment_expected_count=(
            len(expected_breakdown) + len(expected_assignments)
        ),
        passes=passes,
    )


@dataclass(frozen=True)
class EvaluationReport:
    cases: tuple[CaseEvaluationReport, ...]
    candidate_schema_validity: Decimal
    invalid_citation_acceptance: int
    unsupported_answer_count: int
    blocker_recall: Decimal
    tenant_leakage_count: int
    exact_fraction_normalization: Decimal
    retrieval_recall_at_5: Decimal
    unit_assignment_coverage: Decimal
    evidence_quote_text_accuracy: Decimal
    evidence_quote_exact_matches: int
    evidence_quote_expected_count: int
    evidence_quote_actual_count: int
    heating_effect_accuracy: Decimal
    heating_effect_exact_matches: int
    heating_effect_expected_count: int
    heating_effect_actual_count: int
    amendment_effect_accuracy: Decimal
    amendment_effect_exact_matches: int
    amendment_effect_expected_count: int
    amendment_effect_actual_count: int
    adversarial_data_outcome: Decimal
    adversarial_expected_count: int
    adversarial_exact_matches: int
    total_estimated_cost_usd: Decimal
    mea_exact_matches: int
    mea_expected_count: int
    structured_rule_effect_exact_matches: int
    structured_rule_effect_expected_count: int
    structured_rule_effect_actual_count: int
    structured_rule_effect_accuracy: Decimal
    evidence_page_accuracy: Decimal
    evidence_page_exact_matches: int
    evidence_page_expected_count: int
    evidence_page_actual_count: int
    unit_assignment_expected_count: int
    mini_router_compliance: Decimal
    mini_router_expected_count: int
    mini_router_exact_matches: int

    def with_overrides(self, **values: Any) -> "EvaluationReport":
        return replace(self, **values)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1",
            "cases": [case.as_dict() for case in self.cases],
            "threshold_metrics": {
                "candidate_schema_validity": str(self.candidate_schema_validity),
                "invalid_citation_acceptance": self.invalid_citation_acceptance,
                "unsupported_answer_count": self.unsupported_answer_count,
                "blocker_recall": str(self.blocker_recall),
                "tenant_leakage_count": self.tenant_leakage_count,
                "exact_fraction_normalization": str(self.exact_fraction_normalization),
                "retrieval_recall_at_5": str(self.retrieval_recall_at_5),
                "unit_assignment_coverage": str(self.unit_assignment_coverage),
                "evidence_quote_text_accuracy": str(self.evidence_quote_text_accuracy),
                "evidence_quote_exact_matches": self.evidence_quote_exact_matches,
                "evidence_quote_expected_count": self.evidence_quote_expected_count,
                "evidence_quote_actual_count": self.evidence_quote_actual_count,
                "heating_effect_accuracy": str(self.heating_effect_accuracy),
                "heating_effect_exact_matches": self.heating_effect_exact_matches,
                "heating_effect_expected_count": self.heating_effect_expected_count,
                "heating_effect_actual_count": self.heating_effect_actual_count,
                "amendment_effect_accuracy": str(self.amendment_effect_accuracy),
                "amendment_effect_exact_matches": self.amendment_effect_exact_matches,
                "amendment_effect_expected_count": self.amendment_effect_expected_count,
                "amendment_effect_actual_count": self.amendment_effect_actual_count,
                "adversarial_data_outcome": str(self.adversarial_data_outcome),
                "adversarial_expected_count": self.adversarial_expected_count,
                "adversarial_exact_matches": self.adversarial_exact_matches,
                "total_estimated_cost_usd": str(self.total_estimated_cost_usd),
                "mea_exact_matches": self.mea_exact_matches,
                "mea_expected_count": self.mea_expected_count,
                "structured_rule_effect_exact_matches": self.structured_rule_effect_exact_matches,
                "structured_rule_effect_expected_count": self.structured_rule_effect_expected_count,
                "structured_rule_effect_actual_count": self.structured_rule_effect_actual_count,
                "structured_rule_effect_accuracy": str(self.structured_rule_effect_accuracy),
                "evidence_page_accuracy": str(self.evidence_page_accuracy),
                "evidence_page_exact_matches": self.evidence_page_exact_matches,
                "evidence_page_expected_count": self.evidence_page_expected_count,
                "evidence_page_actual_count": self.evidence_page_actual_count,
                "unit_assignment_expected_count": self.unit_assignment_expected_count,
                "mini_router_compliance": str(self.mini_router_compliance),
                "mini_router_expected_count": self.mini_router_expected_count,
                "mini_router_exact_matches": self.mini_router_exact_matches,
            },
        }


@runtime_checkable
class EvaluationScenarioAdapter(Protocol):
    """Typed no-network bridge from synthetic scenarios to observed metrics."""

    def actual_for(
        self,
        *,
        case_id: str,
        gold: Mapping[str, Any],
        observed: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class SyntheticFakeEvaluationAdapter:
    """Run synthetic observations through the product's no-network providers.

    The DB-backed worker needs persisted imports and leases, which these metric
    fixtures deliberately do not fabricate.  The adapter still exercises the
    production extraction/Q&A fake-provider contracts and strict schemas, then
    translates their synthetic outputs back into the neutral metric contract.
    It never reads a PDF, database, credential, or network resource.
    """

    def __init__(self, *, corrupt_output: bool = False) -> None:
        self._corrupt_output = corrupt_output

    def actual_for(
        self,
        *,
        case_id: str,
        gold: Mapping[str, Any],
        observed: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        output = deepcopy(dict(observed))
        output.update(asyncio.run(self._product_fake_observation(case_id, gold, observed)))
        if self._corrupt_output:
            output["candidate_schema_valid"] = False
        return output

    async def _product_fake_observation(
        self,
        case_id: str,
        gold: Mapping[str, Any],
        observed: Mapping[str, Any],
    ) -> dict[str, Any]:
        from app.declarations.fake_knowledge_provider import FakeKnowledgeProvider
        from app.declarations.fake_provider import FakeDeclarationProvider
        from app.declarations.knowledge_provider import KnowledgeProviderRequest
        from app.declarations.knowledge_schemas import KnowledgeHit, KnowledgeSource
        from app.declarations.provider import DocumentInput, PageInput
        from app.declarations.schemas import (
            AdditionalFactPayload,
            CoreExtractionOutput,
            DocumentInventoryItem,
            DocumentInventoryOutput,
            EvidencePayload,
            ReconciliationOutput,
            RuleEffect,
            RuleExtractionOutput,
            RulePayload,
            RuleScope,
            TargetedRecheckOutput,
        )

        allowed_sources = _string_list(gold.get("allowed_source_ids", ()), label="allowed_source_ids")
        document_keys = allowed_sources or (f"source-{case_id}",)
        passive_adversarial_marker = gold.get("adversarial_document_marker")
        if passive_adversarial_marker is not None and (
            not isinstance(passive_adversarial_marker, str)
            or not _SAFE_PASS_ID.fullmatch(passive_adversarial_marker)
        ):
            raise ValueError("adversarial document marker must be a neutral token")

        def evidence(source_id: str, page: int = 1) -> EvidencePayload:
            return EvidencePayload(
                document_key=source_id,
                pdf_page=page,
                quote="synthetic-evidence",
            )

        core_items = tuple(_coerce_fact(item) for item in observed.get("core_facts", ()))
        additional_facts = [
            AdditionalFactPayload(
                subject_key=f"eval-fact-{index}",
                label=item.field_key,
                value=_canonical_json(item.as_dict()),
                management_relevance="synthetic-evaluation",
                evidence=[evidence(document_keys[0])],
                confidence=1,
                clarification_needed=False,
            )
            for index, item in enumerate(core_items, start=1)
        ]
        observed_rules = _rule_index(observed.get("rules", ()))
        rule_items = []
        for rule in observed_rules.values():
            rule_evidence = [
                evidence(source_id, page)
                for source_id, page in sorted(_evidence_pairs(rule))
            ]
            if not rule_evidence:
                rule_evidence = [evidence(document_keys[0])]
            rule_items.append(
                RulePayload(
                    subject_key=str(rule["id"]),
                    title="Synthetic evaluation rule",
                    topic=str(rule["topic"]),
                    summary="Synthetic evaluation rule",
                    scope=RuleScope(all_units=True),
                    effect=RuleEffect(
                        administrative_note=_canonical_json(rule.get("effect", {}))
                    ),
                    proposed_status=(
                        "NEEDS_CLARIFICATION"
                        if gold.get("expected_status") == "NEEDS_CLARIFICATION"
                        else "EFFECTIVE"
                    ),
                    evidence=rule_evidence,
                    confidence=1,
                    clarification_needed=gold.get("expected_status") == "NEEDS_CLARIFICATION",
                )
            )

        inventory_output = DocumentInventoryOutput(
            documents=[
                DocumentInventoryItem(
                    document_key=document_key,
                    document_type="DECLARATION",
                    title="Synthetic evaluation document",
                )
                for document_key in document_keys
            ]
        )
        core_output = CoreExtractionOutput(facts=[], additional_facts=additional_facts)
        rules_output = RuleExtractionOutput(rules=rule_items)
        reconciliation_output = ReconciliationOutput(
            facts=[],
            rules=rule_items,
            additional_facts=additional_facts,
            mea_set_status="COMPLETE" if any(item.field_key == "mea" for item in core_items) else "UNKNOWN",
        )
        extraction_provider = FakeDeclarationProvider(
            inventory_output=inventory_output,
            core_output=core_output,
            rules_output=rules_output,
            reconciliation_output=reconciliation_output,
            recheck_output=TargetedRecheckOutput(),
        )
        documents = (
            DocumentInput(
                document_key=document_keys[0],
                pages=(
                    PageInput(
                        document_key=document_keys[0],
                        pdf_page=1,
                        text=(
                            "synthetic-evaluation"
                            if passive_adversarial_marker is None
                            else f"synthetic-evaluation marker={passive_adversarial_marker}"
                        ),
                        quality=1,
                    ),
                ),
            ),
        )
        inventory_result = await extraction_provider.inventory(documents, max_output_tokens=1_000)
        core_result = await extraction_provider.extract_core(documents, max_output_tokens=1_000)
        rules_result = await extraction_provider.extract_rules(documents, max_output_tokens=1_000)
        reconciliation_result = await extraction_provider.reconcile(
            inventory_result.output,
            core_result.output,
            rules_result.output,
            max_output_tokens=1_000,
        )

        roundtrip_core = [
            _as_mapping(json.loads(item.value), label="fake core fact")
            for item in reconciliation_result.output.additional_facts
        ]
        roundtrip_rules = [
            {
                "id": item.subject_key,
                "topic": item.topic,
                "effect": json.loads(item.effect.administrative_note or "{}"),
                "evidence": [
                    {"source_id": item.document_key, "page": item.pdf_page}
                    for item in item.evidence
                ],
            }
            for item in reconciliation_result.output.rules
        ]
        observed_quote_hashes = {
            (str(rule["id"]), source_id, page): quote_hash
            for rule in observed_rules.values()
            for source_id, page, quote_hash in _evidence_entries(rule)
            if quote_hash is not None
        }
        for rule in roundtrip_rules:
            for citation in rule["evidence"]:
                quote_hash = observed_quote_hashes.get(
                    (str(rule["id"]), citation["source_id"], citation["page"])
                )
                if quote_hash is not None:
                    citation["quote_hash"] = quote_hash

        expected_status = gold.get("expected_status")
        hits: list[KnowledgeHit] = []
        if expected_status in {"ANSWER", "NEEDS_CLARIFICATION"}:
            if not allowed_sources:
                raise ValueError("synthetic answering cases require an allowed source")
            relevant = _string_list(
                gold.get("retrieval_relevant_ids", ()), label="retrieval_relevant_ids"
            )
            rule_by_id = {str(item["id"]): item for item in roundtrip_rules}
            selected_rule = next((rule_by_id[item] for item in relevant if item in rule_by_id), None)
            source_id = allowed_sources[0]
            source_page = 1
            if selected_rule and selected_rule["evidence"]:
                source_id = str(selected_rule["evidence"][0]["source_id"])
                source_page = int(selected_rule["evidence"][0]["page"])
            hit_id = str(selected_rule["id"]) if selected_rule else f"fact-{case_id}"
            hits.append(
                KnowledgeHit(
                    id=hit_id,
                    kind="RULE" if selected_rule else "FACT",
                    topic=str(selected_rule["topic"]) if selected_rule else "core-fact",
                    title="Synthetic evaluation hit",
                    summary="Synthetic supported answer",
                    status=(
                        "NEEDS_CLARIFICATION"
                        if expected_status == "NEEDS_CLARIFICATION"
                        else "EFFECTIVE"
                    ),
                    scope={},
                    effect={},
                    sources=[
                        KnowledgeSource(
                            id=source_id,
                            document_id=f"document-{case_id}",
                            document_title="Synthetic evaluation document",
                            pdf_page=source_page,
                            quote="synthetic-evidence",
                        )
                    ],
                    score=1,
                )
            )
        knowledge_request = KnowledgeProviderRequest(
            normalized_question="synthetic evaluation question",
            hits=hits,
            allowed_fact_ids=[item.id for item in hits if item.kind == "FACT"],
            allowed_rule_ids=[item.id for item in hits if item.kind == "RULE"],
            allowed_source_ids=[source.id for item in hits for source in item.sources],
            schema_version="knowledge-answer-v1",
            prompt_version="knowledge-qa-v1",
        )
        knowledge_result = await FakeKnowledgeProvider(max_output_tokens=1_000).answer(
            knowledge_request,
            max_output_tokens=1_000,
        )

        def pass_metric(pass_id: str, result: Any) -> dict[str, Any]:
            usage = result.usage
            return EvaluationPass(
                pass_id=pass_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                estimated_cost_usd=usage.cost_usd,
                latency_ms=0,
                retry_count=max(0, usage.attempt_count - 1),
                failure_class=None,
            ).as_dict()

        return {
            "core_facts": [dict(item) for item in roundtrip_core],
            "rules": roundtrip_rules,
            "qa": {
                "status": knowledge_result.output.status,
                "source_ids": list(knowledge_result.output.source_ids),
            },
            "passes": [
                pass_metric("fake-inventory", inventory_result),
                pass_metric("fake-core", core_result),
                pass_metric("fake-rules", rules_result),
                pass_metric("fake-reconcile", reconciliation_result),
                pass_metric("fake-qa", knowledge_result),
            ],
            # Read the marker back from the PageInput that the fake provider
            # received. It is data only; no tool/action surface exists here.
            "adversarial_marker_observed": (
                documents[0].pages[0].text.partition(" marker=")[2] or None
            ),
            "adversarial_action_count": 0,
        }


def _mean(values: Iterable[Decimal]) -> Decimal:
    collected = tuple(values)
    if not collected:
        return Decimal("1")
    return (sum(collected, Decimal("0")) / Decimal(len(collected))).quantize(
        _METRIC_QUANTUM, rounding=ROUND_HALF_UP
    )


def build_report(cases: Sequence[CaseEvaluationReport]) -> EvaluationReport:
    case_reports = tuple(cases)
    if not case_reports:
        raise ValueError("at least one evaluation case is required")
    fraction_normalizations = [
        _metric(case.core.mea_exact_matches, case.core.mea_expected_count)
        for case in case_reports
        if case.core.mea_expected_count
    ]
    def aggregate_exact(
        matches_field: str, expected_field: str, actual_field: str
    ) -> tuple[Decimal, int, int, int]:
        matches = sum(getattr(case, matches_field) for case in case_reports)
        expected = sum(getattr(case, expected_field) for case in case_reports)
        actual = sum(getattr(case, actual_field) for case in case_reports)
        return min(_metric(matches, expected), _metric(matches, actual)), matches, expected, actual

    quote_accuracy, quote_matches, quote_expected, quote_actual = aggregate_exact(
        "evidence_quote_exact_matches", "evidence_quote_expected_count", "evidence_quote_actual_count"
    )
    heating_accuracy, heating_matches, heating_expected, heating_actual = aggregate_exact(
        "heating_effect_exact_matches", "heating_effect_expected_count", "heating_effect_actual_count"
    )
    amendment_accuracy, amendment_matches, amendment_expected, amendment_actual = aggregate_exact(
        "amendment_effect_exact_matches", "amendment_effect_expected_count", "amendment_effect_actual_count"
    )
    adversarial_expected = sum(case.adversarial_expected_count for case in case_reports)
    adversarial_matches = sum(case.adversarial_exact_matches for case in case_reports)
    rule_effect_accuracy, rule_effect_matches, rule_effect_expected, rule_effect_actual = aggregate_exact(
        "rule_structured_effect_exact_matches",
        "rule_structured_effect_expected_count",
        "rule_structured_effect_actual_count",
    )
    page_accuracy, page_matches, page_expected, page_actual = aggregate_exact(
        "evidence_page_exact_matches", "evidence_page_expected_count", "evidence_page_actual_count"
    )
    router_expected = sum(case.mini_router_expected_count for case in case_reports)
    router_matches = sum(case.mini_router_exact_matches for case in case_reports)
    return EvaluationReport(
        cases=case_reports,
        candidate_schema_validity=_metric(
            sum(case.candidate_schema_valid for case in case_reports), len(case_reports)
        ),
        invalid_citation_acceptance=sum(
            1 for case in case_reports if case.qa_citation_validity == 0
        ),
        unsupported_answer_count=sum(case.qa_unsupported_answer_count for case in case_reports),
        blocker_recall=_mean(case.blocker_recall for case in case_reports),
        tenant_leakage_count=sum(case.tenant_leakage_count for case in case_reports),
        exact_fraction_normalization=_mean(fraction_normalizations),
        retrieval_recall_at_5=_mean(case.retrieval_recall_at_5 for case in case_reports),
        unit_assignment_coverage=_mean(case.unit_assignment_coverage for case in case_reports),
        evidence_quote_text_accuracy=quote_accuracy,
        evidence_quote_exact_matches=quote_matches,
        evidence_quote_expected_count=quote_expected,
        evidence_quote_actual_count=quote_actual,
        heating_effect_accuracy=heating_accuracy,
        heating_effect_exact_matches=heating_matches,
        heating_effect_expected_count=heating_expected,
        heating_effect_actual_count=heating_actual,
        amendment_effect_accuracy=amendment_accuracy,
        amendment_effect_exact_matches=amendment_matches,
        amendment_effect_expected_count=amendment_expected,
        amendment_effect_actual_count=amendment_actual,
        adversarial_data_outcome=_metric(adversarial_matches, adversarial_expected),
        adversarial_expected_count=adversarial_expected,
        adversarial_exact_matches=adversarial_matches,
        total_estimated_cost_usd=sum(
            (case.total_estimated_cost_usd for case in case_reports), Decimal("0")
        ),
        mea_exact_matches=sum(case.core.mea_exact_matches for case in case_reports),
        mea_expected_count=sum(case.core.mea_expected_count for case in case_reports),
        structured_rule_effect_exact_matches=rule_effect_matches,
        structured_rule_effect_expected_count=rule_effect_expected,
        structured_rule_effect_actual_count=rule_effect_actual,
        structured_rule_effect_accuracy=rule_effect_accuracy,
        evidence_page_accuracy=page_accuracy,
        evidence_page_exact_matches=page_matches,
        evidence_page_expected_count=page_expected,
        evidence_page_actual_count=page_actual,
        unit_assignment_expected_count=sum(
            case.unit_assignment_expected_count for case in case_reports
        ),
        mini_router_compliance=_metric(router_matches, router_expected),
        mini_router_expected_count=router_expected,
        mini_router_exact_matches=router_matches,
    )


def _reject_unsafe_fixture_content(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("fixture keys must be strings")
            lowered = key.lower()
            if "quote" in lowered and lowered != "quote_hash":
                raise ValueError("fixtures must remain sanitized")
            if lowered == "quote_hash" and (
                not isinstance(child, str) or not _QUOTE_HASH.fullmatch(child)
            ):
                raise ValueError("fixtures must use lowercase quote hashes")
            if any(part in lowered for part in _FORBIDDEN_FIXTURE_KEY_PARTS):
                raise ValueError("fixtures must remain sanitized")
            _reject_unsafe_fixture_content(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_unsafe_fixture_content(child)
    elif isinstance(value, str) and (
        value.startswith(("/", "\\"))
        or ".pdf" in value.lower()
        or "library/cloudstorage" in value.lower()
    ):
        raise ValueError("fixtures must remain sanitized")


def load_fixture_file(path: str | Path) -> tuple[tuple[Mapping[str, Any], Mapping[str, Any]], ...]:
    fixture_path = Path(path)
    try:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("fixture could not be loaded") from exc
    _reject_unsafe_fixture_content(data)
    if not isinstance(data, Mapping) or data.get("schema_version") != "1":
        raise ValueError("fixture schema is unsupported")
    cases = data.get("cases")
    if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)) or not cases:
        raise ValueError("fixture requires cases")
    parsed: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for item in cases:
        case = _as_mapping(item, label="fixture case")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not _SAFE_CASE_ID.fullmatch(case_id):
            raise ValueError("fixture case ID is invalid")
        gold = dict(_as_mapping(case.get("gold"), label="gold"))
        gold.setdefault("id", case_id)
        parsed.append((gold, _as_mapping(case.get("fake_observed"), label="fake_observed")))
    return tuple(parsed)


def evaluate_fixture_file(
    path: str | Path,
    *,
    adapter: EvaluationScenarioAdapter | None = None,
) -> EvaluationReport:
    return build_report(
        [
            evaluate_case(
                gold=gold,
                actual=(
                    adapter.actual_for(case_id=str(gold["id"]), gold=gold, observed=actual)
                    if adapter is not None
                    else actual
                ),
            )
            for gold, actual in load_fixture_file(path)
        ]
    )


def validate_thresholds(report: EvaluationReport) -> tuple[str, ...]:
    failures: list[str] = []
    if report.candidate_schema_validity != Decimal("1"):
        failures.append("candidate_schema_validity")
    if report.invalid_citation_acceptance != 0:
        failures.append("invalid_citation_acceptance")
    if report.unsupported_answer_count != 0:
        failures.append("unsupported_answer_count")
    if report.blocker_recall != Decimal("1"):
        failures.append("blocker_recall")
    if report.tenant_leakage_count != 0:
        failures.append("tenant_leakage_count")
    if report.exact_fraction_normalization != Decimal("1"):
        failures.append("exact_fraction_normalization")
    if report.retrieval_recall_at_5 < Decimal("0.95"):
        failures.append("retrieval_recall_at_5")
    if report.unit_assignment_coverage != Decimal("1"):
        failures.append("unit_assignment_coverage")
    return tuple(failures)


@dataclass(frozen=True)
class ModelComparisonReport:
    """Content-free comparison over an identical, neutral synthetic case set."""

    reports: Mapping[str, EvaluationReport]

    def __post_init__(self) -> None:
        if tuple(sorted(self.reports)) != tuple(sorted(_COMPARISON_LABELS)):
            raise ValueError("model comparison requires exactly the fixed configurations")
        if not all(isinstance(report, EvaluationReport) for report in self.reports.values()):
            raise ValueError("model comparison requires typed reports")
        for report in self.reports.values():
            case_ids = [case.case_id for case in report.cases]
            if len(case_ids) != len(set(case_ids)):
                raise ValueError("model comparison requires unique case IDs")
        case_sets = {
            label: tuple(sorted(case.case_id for case in report.cases))
            for label, report in self.reports.items()
        }
        if len(set(case_sets.values())) != 1:
            raise ValueError("model comparison requires identical case IDs")

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(sorted(case.case_id for case in self.reports["luna_medium"].cases))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1",
            "mode": "model-comparison",
            "case_ids": list(self.case_ids),
            "models": {
                label: report.as_dict()["threshold_metrics"]
                for label, report in sorted(self.reports.items())
            },
        }


def validate_promotion_gates(
    candidate: EvaluationReport,
    baseline: EvaluationReport,
    comparison: ModelComparisonReport | None = None,
) -> tuple[str, ...]:
    """Return stable gates only; a lower cost never compensates for quality."""
    if not isinstance(candidate, EvaluationReport) or not isinstance(baseline, EvaluationReport):
        raise ValueError("promotion gates require typed evaluation reports")
    failures = list(validate_thresholds(candidate))
    candidate_ids = tuple(case.case_id for case in candidate.cases)
    baseline_ids = tuple(case.case_id for case in baseline.cases)
    if not candidate_ids:
        failures.append("representative_gold_required")
    if (
        len(candidate_ids) != len(set(candidate_ids))
        or len(baseline_ids) != len(set(baseline_ids))
        or candidate_ids != baseline_ids
    ):
        failures.append("candidate_baseline_case_ids")
    required_case_ids = {
        "gold-units-mea",
        "gold-heating-amendment",
        "gold-adversarial-evidence",
    }
    if not required_case_ids <= set(candidate_ids):
        failures.append("representative_gold_coverage")
    required_dimensions = {
        "units_mea": candidate.mea_expected_count,
        "unit_assignment": candidate.unit_assignment_expected_count,
        "structured_rule_effect": candidate.structured_rule_effect_expected_count,
        "evidence_page": candidate.evidence_page_expected_count,
        "evidence_quote": candidate.evidence_quote_expected_count,
        "heating_effect": candidate.heating_effect_expected_count,
        "amendment_effect": candidate.amendment_effect_expected_count,
        "adversarial_outcome": candidate.adversarial_expected_count,
        "mini_router": candidate.mini_router_expected_count,
    }
    for dimension, denominator in required_dimensions.items():
        if denominator <= 0:
            failures.append(f"representative_{dimension}_required")
    if comparison is None:
        failures.append("model_comparison_required")
    else:
        comparison_ids = comparison.case_ids
        if comparison_ids != tuple(sorted(candidate_ids)):
            failures.append("model_comparison_case_ids")
        elif (
            comparison.reports["luna_low"] != candidate
            or comparison.reports["luna_medium"] != baseline
        ):
            failures.append("model_comparison_detached")
    if candidate.evidence_quote_text_accuracy != Decimal("1"):
        failures.append("evidence_quote_text_accuracy")
    for metric_name in (
        "exact_fraction_normalization",
        "unit_assignment_coverage",
        "structured_rule_effect_accuracy",
        "evidence_page_accuracy",
        "heating_effect_accuracy",
        "amendment_effect_accuracy",
        "adversarial_data_outcome",
        "mini_router_compliance",
    ):
        if getattr(candidate, metric_name) != Decimal("1"):
            failures.append(metric_name)
    for metric_name in (
        "exact_fraction_normalization",
        "retrieval_recall_at_5",
        "unit_assignment_coverage",
        "structured_rule_effect_accuracy",
        "evidence_page_accuracy",
        "mini_router_compliance",
        "heating_effect_accuracy",
        "amendment_effect_accuracy",
        "adversarial_data_outcome",
    ):
        if getattr(candidate, metric_name) < getattr(baseline, metric_name):
            failures.append(metric_name)
    return tuple(dict.fromkeys(failures))


def report_as_json(report: EvaluationReport) -> str:
    """Return metrics and neutral IDs only; never serialize fixture evidence."""
    return json.dumps(report.as_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class LiveHarnessUnavailable(RuntimeError):
    """Raised instead of bypassing the bounded production import pipeline."""


@dataclass(frozen=True)
class RealPdfManifestEntry:
    """Validated local input; its path must never enter a serialized report."""

    case_id: str
    path: Path
    sha256: str


@dataclass(frozen=True)
class _LiveEvaluationCapability:
    """Unforgeable-in-normal-use token issued only after all runner gates pass."""

    _seal: object

    def is_valid(self) -> bool:
        return self._seal is _LIVE_CAPABILITY_SEAL


@dataclass(frozen=True)
class ComparisonConfiguration:
    """One caller-approved, comparison-only provider configuration."""

    label: str
    primary_model: str
    reasoning_effort: str
    mini_router_enabled: bool
    input_usd_per_million: Decimal | None = None
    output_usd_per_million: Decimal | None = None
    cached_input_usd_per_million: Decimal | None = None

    def __post_init__(self) -> None:
        if self.label not in _COMPARISON_LABELS:
            raise ValueError("comparison configuration label is invalid")
        if not isinstance(self.primary_model, str) or not _SAFE_MODEL_ID.fullmatch(self.primary_model):
            raise ValueError("comparison model is invalid")
        if self.reasoning_effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("comparison reasoning effort is invalid")
        if type(self.mini_router_enabled) is not bool:
            raise ValueError("comparison router flag is invalid")
        rates = (
            self.input_usd_per_million,
            self.output_usd_per_million,
            self.cached_input_usd_per_million,
        )
        if self.label == "terra_primary":
            if any(not isinstance(value, Decimal) or not value.is_finite() or value <= 0 for value in rates):
                raise ValueError("terra comparison requires caller-verified rates")
        elif any(value is not None for value in rates):
            raise ValueError("only terra comparison accepts explicit rates")


@dataclass(frozen=True)
class LivePipelineCaseMetrics:
    """Sanitized, aggregate-only output from one bounded extraction run."""

    case_id: str
    page_count: int
    fact_count: int
    rule_count: int
    blocker_count: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: Decimal
    latency_ms: int
    retry_count: int
    failure_classes: tuple[str, ...]
    passes: tuple[EvaluationPass, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not _SAFE_CASE_ID.fullmatch(self.case_id):
            raise ValueError("live case IDs must be neutral")
        for value in (
            self.page_count,
            self.fact_count,
            self.rule_count,
            self.blocker_count,
            self.input_tokens,
            self.output_tokens,
            self.latency_ms,
            self.retry_count,
        ):
            _positive_int(value, field_name="live metric")
        if (
            not isinstance(self.estimated_cost_usd, Decimal)
            or not self.estimated_cost_usd.is_finite()
            or self.estimated_cost_usd < 0
        ):
            raise ValueError("live cost must be a finite Decimal")
        if any(
            not isinstance(item, str) or not _SAFE_FAILURE_CLASS.fullmatch(item)
            for item in self.failure_classes
        ):
            raise ValueError("live failure classes must be sanitized")
        if not isinstance(self.passes, tuple) or not all(
            isinstance(item, EvaluationPass) for item in self.passes
        ):
            raise ValueError("live pass metadata must be typed and content-free")

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "page_count": self.page_count,
            "fact_count": self.fact_count,
            "rule_count": self.rule_count,
            "blocker_count": self.blocker_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": str(self.estimated_cost_usd),
            "latency_ms": self.latency_ms,
            "retry_count": self.retry_count,
            "failure_classes": list(self.failure_classes),
            "passes": [item.as_dict() for item in self.passes],
        }


@dataclass(frozen=True)
class LivePipelineReport:
    cases: tuple[LivePipelineCaseMetrics, ...]

    def __post_init__(self) -> None:
        if not self.cases or not all(isinstance(case, LivePipelineCaseMetrics) for case in self.cases):
            raise ValueError("live report requires typed case metrics")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("live report case IDs must be unique")

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": "1", "mode": "live", "cases": [case.as_dict() for case in self.cases]}


@dataclass(frozen=True)
class LiveModelComparisonReport:
    """A descriptive-only report; real PDFs are not labeled gold evaluations."""

    reports: Mapping[str, LivePipelineReport]

    def __post_init__(self) -> None:
        if tuple(sorted(self.reports)) != tuple(sorted(_COMPARISON_LABELS)):
            raise ValueError("live comparison requires exactly the fixed configurations")
        if not all(isinstance(report, LivePipelineReport) for report in self.reports.values()):
            raise ValueError("live comparison requires typed reports")
        case_sets = {
            label: tuple(sorted(case.case_id for case in report.cases))
            for label, report in self.reports.items()
        }
        if len(set(case_sets.values())) != 1:
            raise ValueError("live comparison requires identical case IDs")

    def as_dict(self) -> dict[str, Any]:
        case_ids = tuple(sorted(case.case_id for case in self.reports["luna_medium"].cases))
        return {
            "schema_version": "1",
            "mode": "live-model-comparison",
            "descriptive_only": True,
            "promotion_eligible": False,
            "stable_gates": ["UNLABELED_REAL_PDF_DESCRIPTIVE_ONLY"],
            "model_comparison": {
                "case_ids": list(case_ids),
                "models": {
                    label: {
                        "cases": [case.as_dict() for case in sorted(report.cases, key=lambda item: item.case_id)],
                        "aggregate": _live_aggregate(report),
                    }
                    for label, report in sorted(self.reports.items())
                },
            },
        }


@runtime_checkable
class ProductionPipelineEvaluationAdapter(Protocol):
    """A first-party adapter must retain the product pipeline's bounds."""

    async def evaluate(self, entries: Sequence[RealPdfManifestEntry]) -> LivePipelineReport: ...


class BoundedProductionDeclarationPipelineAdapter:
    """No-lease evaluation seam over the product pipeline's bounded internals.

    This is deliberately coupled to the first-party pipeline helpers.  It runs
    preflight, batching, reservations, fixed passes, bounded reconciliation and
    optional bounded rechecks exactly as the worker does, but stops before the
    DB-only candidate publication stage.  A fresh ``DeclarationPipeline`` has
    no active lease, so its durable checkpoint is intentionally a no-op.
    """

    def __init__(
        self,
        configuration: ComparisonConfiguration | None = None,
        *,
        capability: _LiveEvaluationCapability | None = None,
    ) -> None:
        self._configuration = configuration or ComparisonConfiguration(
            label="luna_with_mini_router",
            primary_model="gpt-5.6-luna",
            reasoning_effort="medium",
            mini_router_enabled=True,
        )
        self._capability = capability

    @staticmethod
    def _read_input(entry: RealPdfManifestEntry, *, max_bytes: int) -> bytes:
        try:
            if entry.path.stat().st_size > max_bytes:
                raise LiveHarnessUnavailable("Live input exceeds the configured PDF size limit.")
            with entry.path.open("rb") as input_file:
                data = input_file.read(max_bytes + 1)
        except LiveHarnessUnavailable:
            raise
        except OSError as exc:
            raise LiveHarnessUnavailable("Live input could not be read.") from exc
        if len(data) > max_bytes:
            raise LiveHarnessUnavailable("Live input exceeds the configured PDF size limit.")
        return data

    async def evaluate(self, entries: Sequence[RealPdfManifestEntry]) -> LivePipelineReport:
        if not isinstance(self._capability, _LiveEvaluationCapability) or not self._capability.is_valid():
            raise LiveHarnessUnavailable("Bounded adapter requires a validated live capability.")
        # All imports that can construct the SDK/provider stay after explicit
        # live gates in ``run_real_pdf_smoke``.  Ordinary unit tests do not load
        # this runtime path.
        from app.core.config import settings
        from app.declarations.evidence import EvidenceTextIndex
        from app.declarations.openai_provider import OpenAiDeclarationProvider, _Rates
        from app.declarations.pipeline import (
            DeclarationPipeline,
            PipelineFailure,
            _Ledger,
            _declared_denominator,
            _merge_core,
            _merge_inventory,
            _merge_recheck,
            _merge_rules,
            _page_quality,
            _validate_inventory_documents,
            _validate_inventory_evidence,
        )
        from app.declarations.provider import PageInput
        from app.declarations.schemas import (
            CoreExtractionOutput,
            DocumentInventoryOutput,
            RuleExtractionOutput,
        )
        from app.declarations.validation import ValidationIssue, validate_reconciliation

        runtime_updates: dict[str, Any] = {
                "declaration_primary_model": self._configuration.primary_model,
                "declaration_primary_reasoning_effort": self._configuration.reasoning_effort,
                "declaration_max_recheck_calls": (
                    settings.declaration_max_recheck_calls
                    if self._configuration.mini_router_enabled
                    else 0
                ),
            }
        if self._configuration.label == "terra_primary":
            runtime_updates.update(
                {
                    "declaration_primary_input_usd_per_million": self._configuration.input_usd_per_million,
                    "declaration_primary_output_usd_per_million": self._configuration.output_usd_per_million,
                    "declaration_primary_cached_input_usd_per_million": self._configuration.cached_input_usd_per_million,
                }
            )
        runtime_settings = settings.model_copy(update=runtime_updates)
        provider = OpenAiDeclarationProvider(configured_settings=runtime_settings)
        if self._configuration.label == "terra_primary":
            # Comparison-only caller supplied rates are deliberately not copied
            # into application settings or production defaults.
            provider._rates[self._configuration.primary_model] = _Rates(
                self._configuration.input_usd_per_million,  # type: ignore[arg-type]
                self._configuration.output_usd_per_million,  # type: ignore[arg-type]
                self._configuration.cached_input_usd_per_million,  # type: ignore[arg-type]
                None,
            )

        class _CountingProvider:
            _METHODS = {"inventory", "extract_core", "extract_rules", "reconcile", "recheck"}

            def __init__(self, delegate: Any) -> None:
                self.delegate = delegate
                self.logical_calls = 0

            def __getattr__(self, name: str) -> Any:
                attribute = getattr(self.delegate, name)
                if name not in self._METHODS:
                    return attribute

                async def counted(*args: Any, **kwargs: Any) -> Any:
                    self.logical_calls += 1
                    return await attribute(*args, **kwargs)

                return counted

        counting_provider = _CountingProvider(provider)
        pipeline = DeclarationPipeline(counting_provider, configured=runtime_settings)
        metrics: list[LivePipelineCaseMetrics] = []
        try:
            for entry in entries:
                started = time.monotonic()
                calls_before = counting_provider.logical_calls
                data = self._read_input(entry, max_bytes=settings.declaration_max_pdf_bytes)
                preflight_budget = pipeline._preflight.new_import_budget()
                result = pipeline._preflight.run_bytes(
                    data,
                    expected_sha256=entry.sha256,
                    expected_size=len(data),
                    budget=preflight_budget,
                )
                pages = tuple(
                    PageInput(
                        document_key=entry.case_id,
                        pdf_page=artifact.page_number,
                        text=artifact.text,
                        quality=_page_quality(artifact.source),
                    )
                    for artifact in result.pages
                )
                evidence_index = EvidenceTextIndex.from_pages(pages)
                batches = pipeline._page_batches(pages)
                if not batches:
                    raise LiveHarnessUnavailable("Bounded pipeline received no evaluable pages.")
                ledger = _Ledger(
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=Decimal("0"),
                    attempts=0,
                    max_input_tokens=settings.declaration_max_input_tokens,
                    max_output_tokens=settings.declaration_max_output_tokens,
                    max_cost_usd=settings.declaration_max_cost_usd,
                    configured=settings,
                )
                inventories = [
                    await pipeline._call("inventory", batch.as_documents(), ledger, DocumentInventoryOutput)
                    for batch in batches
                ]
                cores = [
                    await pipeline._call("extract_core", batch.as_documents(), ledger, CoreExtractionOutput)
                    for batch in batches
                ]
                rulesets = [
                    await pipeline._call("extract_rules", batch.as_documents(), ledger, RuleExtractionOutput)
                    for batch in batches
                ]
                inventory = _merge_inventory(inventories)
                _validate_inventory_documents(inventory, {entry.case_id: None})  # type: ignore[dict-item]
                core = _merge_core(cores)
                rules = _merge_rules(rulesets)
                output = await pipeline._reconcile_bounded(inventory, core, rules, ledger)
                page_counts = {entry.case_id: result.page_count}
                issues = [
                    *_validate_inventory_evidence(
                        inventory, page_counts, evidence_index=evidence_index
                    ),
                    *validate_reconciliation(
                        output,
                        page_counts,
                        _declared_denominator(output),
                        evidence_index=evidence_index,
                    ),
                ]
                if any(issue.code == "UNKNOWN_EVIDENCE_DOCUMENT" for issue in issues):
                    raise PipelineFailure("UNKNOWN_EVIDENCE_DOCUMENT")
                selected, route_reason_codes = pipeline._selected_recheck(
                    output, inventory, issues, pages
                )
                if selected:
                    rendered, render_issue = pipeline._render_recheck_pages(
                        selected, {entry.case_id: data}, preflight_budget
                    )
                    if render_issue is not None:
                        issues.append(render_issue)
                    else:
                        try:
                            recheck, _remaining = await pipeline._call_rechecks(
                                rendered,
                                issues,
                                ledger,
                                route_reason_codes=route_reason_codes,
                            )
                        except PipelineFailure as exc:
                            if exc.code not in {
                                "IMPORT_TOKEN_BUDGET_EXCEEDED",
                                "IMPORT_COST_BUDGET_EXCEEDED",
                                "PROVIDER_REQUEST_CHAR_LIMIT_EXCEEDED",
                            }:
                                raise
                        else:
                            output = _merge_recheck(output, recheck)
                            issues = [
                                *_validate_inventory_evidence(
                                    inventory, page_counts, evidence_index=evidence_index
                                ),
                                *validate_reconciliation(
                                    output,
                                    page_counts,
                                    _declared_denominator(output),
                                    evidence_index=evidence_index,
                                ),
                            ]
                            if any(issue.code == "UNKNOWN_EVIDENCE_DOCUMENT" for issue in issues):
                                raise PipelineFailure("UNKNOWN_EVIDENCE_DOCUMENT")
                            if _remaining:
                                issues.append(
                                    ValidationIssue(
                                        code="RECHECK_CALL_LIMIT_REACHED",
                                        severity="WARNING",
                                        message="Weitere gezielte Nachprüfungen benötigen eine fachliche Klärung.",
                                    )
                                )
                logical_calls = counting_provider.logical_calls - calls_before
                metrics.append(
                    LivePipelineCaseMetrics(
                        case_id=entry.case_id,
                        page_count=result.page_count,
                        fact_count=len(output.facts) + len(output.additional_facts),
                        rule_count=len(output.rules),
                        blocker_count=sum(issue.severity == "BLOCKER" for issue in issues),
                        input_tokens=ledger.input_tokens,
                        output_tokens=ledger.output_tokens,
                        estimated_cost_usd=ledger.cost_usd,
                        latency_ms=int((time.monotonic() - started) * 1_000),
                        retry_count=max(0, ledger.attempts - logical_calls),
                        failure_classes=(),
                        passes=tuple(
                            EvaluationPass(
                                pass_id=entry.pass_id,
                                input_tokens=entry.input_tokens,
                                output_tokens=entry.output_tokens,
                                estimated_cost_usd=entry.estimated_cost_usd,
                                latency_ms=entry.latency_ms,
                                retry_count=entry.retry_count,
                                failure_class=entry.failure_class,
                                route_action=entry.route_action,
                                route_reason_codes=entry.route_reason_codes,
                            )
                            for entry in ledger.pass_entries
                        ),
                    )
                )
        except PipelineFailure as exc:
            raise LiveHarnessUnavailable("Bounded production pipeline rejected live evaluation.") from exc
        finally:
            client = getattr(provider, "_client", None)
            close = getattr(client, "close", None)
            if callable(close):
                result = close()
                if hasattr(result, "__await__"):
                    await result
        return LivePipelineReport(cases=tuple(metrics))


def live_eval_missing_gates(
    environ: Mapping[str, str] | None = None,
    *,
    provider: str | None = None,
    live: bool | None = None,
    compare_models: bool | None = None,
) -> tuple[str, ...]:
    values = os.environ if environ is None else environ
    required = (
        "HV_DECLARATION_REAL_PDF_MANIFEST",
        "HV_DECLARATION_LIVE_EVAL",
        "HV_OPENAI_API_KEY",
    )
    missing = [
        key
        for key in required
        if not isinstance(values.get(key), str) or not values[key].strip()
    ]
    live_env = values.get("HV_DECLARATION_LIVE_EVAL")
    if live_env != "1":
        missing.append("HV_DECLARATION_LIVE_EVAL")
    if provider != "live":
        missing.append("LIVE_PROVIDER_REQUIRED")
    if live is not True:
        missing.append("LIVE_FLAG_REQUIRED")
    if compare_models is not True:
        missing.append("COMPARE_MODELS_FLAG_REQUIRED")
    if compare_models is not True:
        # The comparison-only Terra gates are part of the required capability,
        # even when the caller omitted the CLI flag.
        missing.extend(
            (
                "HV_DECLARATION_COMPARE_TERRA_INPUT_USD_PER_MILLION",
                "HV_DECLARATION_COMPARE_TERRA_OUTPUT_USD_PER_MILLION",
                "HV_DECLARATION_COMPARE_TERRA_CACHED_INPUT_USD_PER_MILLION",
            )
        )
    else:
        for key in (
            "HV_DECLARATION_COMPARE_TERRA_INPUT_USD_PER_MILLION",
            "HV_DECLARATION_COMPARE_TERRA_OUTPUT_USD_PER_MILLION",
            "HV_DECLARATION_COMPARE_TERRA_CACHED_INPUT_USD_PER_MILLION",
        ):
            if not isinstance(values.get(key), str) or not values[key].strip():
                missing.append(key)
    return tuple(sorted(set(missing)))


def _comparison_configurations(values: Mapping[str, str]) -> tuple[ComparisonConfiguration, ...]:
    """Build only fixed labels; Terra prices are caller-supplied for this run."""
    try:
        terra_input = _decimal(
            values["HV_DECLARATION_COMPARE_TERRA_INPUT_USD_PER_MILLION"],
            field_name="terra comparison input rate",
        )
        terra_output = _decimal(
            values["HV_DECLARATION_COMPARE_TERRA_OUTPUT_USD_PER_MILLION"],
            field_name="terra comparison output rate",
        )
        terra_cached = _decimal(
            values["HV_DECLARATION_COMPARE_TERRA_CACHED_INPUT_USD_PER_MILLION"],
            field_name="terra comparison cached input rate",
        )
    except (KeyError, ValueError) as exc:
        raise LiveHarnessUnavailable("Live comparison Terra rates are invalid.") from exc
    try:
        return (
            ComparisonConfiguration("luna_medium", "gpt-5.6-luna", "medium", False),
            ComparisonConfiguration("luna_low", "gpt-5.6-luna", "low", False),
            ComparisonConfiguration(
                "terra_primary",
                "gpt-5.6-terra",
                "medium",
                False,
                terra_input,
                terra_output,
                terra_cached,
            ),
            ComparisonConfiguration("luna_with_mini_router", "gpt-5.6-luna", "medium", True),
        )
    except ValueError as exc:
        raise LiveHarnessUnavailable("Live comparison Terra rates are invalid.") from exc


def validate_real_pdf_manifest(
    path: str | Path,
    *,
    max_pdf_bytes: int,
    max_cases: int,
) -> tuple[RealPdfManifestEntry, ...]:
    """Perform only local manifest/path/hash checks for a caller-owned manifest."""
    if isinstance(max_pdf_bytes, bool) or not isinstance(max_pdf_bytes, int) or max_pdf_bytes <= 0:
        raise LiveHarnessUnavailable("Live evaluation PDF size limit is invalid.")
    if isinstance(max_cases, bool) or not isinstance(max_cases, int) or max_cases <= 0:
        raise LiveHarnessUnavailable("Live evaluation case limit is invalid.")
    manifest_path = Path(path)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LiveHarnessUnavailable("Live evaluation manifest could not be read.") from exc
    if not isinstance(data, Mapping) or not data:
        raise LiveHarnessUnavailable("Live evaluation manifest has no cases.")
    if len(data) > max_cases:
        raise LiveHarnessUnavailable("Live evaluation manifest exceeds the configured case limit.")
    entries: list[RealPdfManifestEntry] = []
    for case_id, raw_entry in data.items():
        if not isinstance(case_id, str) or not _SAFE_CASE_ID.fullmatch(case_id):
            raise LiveHarnessUnavailable("Live evaluation manifest contains an invalid case ID.")
        entry = _as_mapping(raw_entry, label="live manifest entry")
        raw_path, expected_hash = entry.get("path"), entry.get("sha256")
        if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
            raise LiveHarnessUnavailable("Live evaluation manifest paths must be absolute.")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected_hash):
            raise LiveHarnessUnavailable("Live evaluation manifest requires SHA-256 values.")
        pdf_path = Path(raw_path)
        if not pdf_path.is_file():
            raise LiveHarnessUnavailable("A declared real-PDF input is unavailable.")
        try:
            if pdf_path.stat().st_size > max_pdf_bytes:
                raise LiveHarnessUnavailable("Live input exceeds the configured PDF size limit.")
        except LiveHarnessUnavailable:
            raise
        except OSError as exc:
            raise LiveHarnessUnavailable("A declared real-PDF input is unavailable.") from exc
        digest = hashlib.sha256()
        observed_size = 0
        try:
            with pdf_path.open("rb") as input_file:
                while True:
                    chunk = input_file.read(min(1024 * 1024, max_pdf_bytes - observed_size + 1))
                    if not chunk:
                        break
                    observed_size += len(chunk)
                    if observed_size > max_pdf_bytes:
                        raise LiveHarnessUnavailable("Live input exceeds the configured PDF size limit.")
                    digest.update(chunk)
        except LiveHarnessUnavailable:
            raise
        except OSError as exc:
            raise LiveHarnessUnavailable("A declared real-PDF input is unavailable.") from exc
        observed = digest.hexdigest()
        if observed.lower() != expected_hash.lower():
            raise LiveHarnessUnavailable("A declared real-PDF input failed integrity validation.")
        entries.append(RealPdfManifestEntry(case_id=case_id, path=pdf_path, sha256=observed))
    return tuple(sorted(entries, key=lambda item: item.case_id))


def _live_aggregate(report: LivePipelineReport) -> dict[str, Any]:
    return {
        "case_count": len(report.cases),
        "page_count": sum(case.page_count for case in report.cases),
        "fact_count": sum(case.fact_count for case in report.cases),
        "rule_count": sum(case.rule_count for case in report.cases),
        "blocker_count": sum(case.blocker_count for case in report.cases),
        "input_tokens": sum(case.input_tokens for case in report.cases),
        "output_tokens": sum(case.output_tokens for case in report.cases),
        "estimated_cost_usd": str(sum((case.estimated_cost_usd for case in report.cases), Decimal("0"))),
        "latency_ms": sum(case.latency_ms for case in report.cases),
        "retry_count": sum(case.retry_count for case in report.cases),
        "failure_classes": sorted({item for case in report.cases for item in case.failure_classes}),
    }


def _write_live_report(report_dir: Path, report: LiveModelComparisonReport) -> Path:
    destination = report_dir / "declaration-knowledge-live-eval.json"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".declaration-knowledge-live-eval-", suffix=".json", dir=report_dir
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            output.write(json.dumps(report.as_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, destination)
        os.chmod(destination, 0o600)
        return destination
    finally:
        if descriptor != -1:
            os.close(descriptor)
        if temporary_path.exists():
            temporary_path.unlink()


def _close_adapter(adapter: object) -> bool:
    close = getattr(adapter, "close", None)
    if not callable(close):
        return True
    try:
        result = close()
        if hasattr(result, "__await__"):
            asyncio.run(result)
    except Exception:
        return False
    return True


def run_real_pdf_smoke(
    *,
    manifest_path: str | Path,
    report_dir: str | Path,
    provider: str | None = None,
    live: bool | None = None,
    compare_models: bool | None = None,
    adapter_factory: Callable[[ComparisonConfiguration], ProductionPipelineEvaluationAdapter] | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Run a gated, sequential, descriptive comparison and write metrics only."""
    values = os.environ if environ is None else environ
    if live_eval_missing_gates(
        values, provider=provider, live=live, compare_models=compare_models
    ):
        raise LiveHarnessUnavailable("Live evaluation requires all explicit live gates.")
    configurations = _comparison_configurations(values)
    target = Path(report_dir)
    if not target.is_dir():
        raise LiveHarnessUnavailable("Live evaluation requires a caller-selected report directory.")
    repository_root = Path(__file__).resolve().parents[3]
    if target.resolve().is_relative_to(repository_root):
        raise LiveHarnessUnavailable("Live evaluation report directory must be external and caller-owned.")
    passed_manifest = Path(manifest_path)
    configured_manifest_path = Path(values["HV_DECLARATION_REAL_PDF_MANIFEST"])
    if not passed_manifest.is_absolute() or not configured_manifest_path.is_absolute():
        raise LiveHarnessUnavailable("Live evaluation manifest must be an external caller-owned file.")
    resolved_manifest = passed_manifest.resolve()
    configured_manifest = configured_manifest_path.resolve()
    if resolved_manifest != configured_manifest or resolved_manifest.is_relative_to(repository_root):
        raise LiveHarnessUnavailable("Live evaluation manifest must be an external caller-owned file.")
    from app.core.config import settings as configured_settings

    entries = validate_real_pdf_manifest(
        resolved_manifest,
        max_pdf_bytes=configured_settings.declaration_max_pdf_bytes,
        max_cases=configured_settings.declaration_max_documents,
    )
    capability = _LiveEvaluationCapability(_LIVE_CAPABILITY_SEAL)
    reports: dict[str, LivePipelineReport] = {}
    expected_case_ids = tuple(entry.case_id for entry in entries)
    constructed_adapters: list[ProductionPipelineEvaluationAdapter] = []
    for configuration in configurations:
        try:
            active_adapter = (
                adapter_factory(configuration)
                if adapter_factory is not None
                else BoundedProductionDeclarationPipelineAdapter(
                    configuration, capability=capability
                )
            )
        except Exception as exc:
            raise LiveHarnessUnavailable("Live comparison adapter construction failed.") from exc
        if not isinstance(active_adapter, ProductionPipelineEvaluationAdapter):
            _close_adapter(active_adapter)
            raise LiveHarnessUnavailable("Live evaluation adapter does not implement the production pipeline contract.")
        if any(active_adapter is prior for prior in constructed_adapters):
            _close_adapter(active_adapter)
            raise LiveHarnessUnavailable("Live comparison requires a fresh adapter per configuration.")
        constructed_adapters.append(active_adapter)
        failure: LiveHarnessUnavailable | None = None
        report: LivePipelineReport | None = None
        try:
            report = asyncio.run(active_adapter.evaluate(entries))
        except LiveHarnessUnavailable:
            failure = LiveHarnessUnavailable("Bounded production pipeline live evaluation failed.")
        except Exception as exc:
            failure = LiveHarnessUnavailable("Bounded production pipeline live evaluation failed.")
        finally:
            cleanup_ok = _close_adapter(active_adapter)
        if not cleanup_ok:
            raise LiveHarnessUnavailable("Live comparison adapter cleanup failed.")
        if failure is not None:
            raise failure
        if not isinstance(report, LivePipelineReport):
            raise LiveHarnessUnavailable("Bounded adapter did not return a sanitized live report.")
        reported_case_ids = tuple(sorted(case.case_id for case in report.cases))
        if expected_case_ids != reported_case_ids or len(report.cases) != len(entries):
            raise LiveHarnessUnavailable("Sanitized live report does not match the manifest cases.")
        reports[configuration.label] = report
    try:
        comparison = LiveModelComparisonReport(reports=reports)
    except ValueError as exc:
        raise LiveHarnessUnavailable("Sanitized live model comparison is invalid.") from exc
    try:
        return _write_live_report(target, comparison)
    except OSError as exc:
        raise LiveHarnessUnavailable("Sanitized live report could not be written.") from exc


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run sanitized declaration-knowledge evaluations.")
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--provider", choices=("fake", "live"), required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--compare-models", action="store_true")
    parser.add_argument("--manifest")
    parser.add_argument("--report-dir")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.provider == "fake":
        try:
            report = evaluate_fixture_file(args.fixtures, adapter=SyntheticFakeEvaluationAdapter())
        except ValueError:
            print('{"status":"INVALID_FIXTURE"}')
            return 2
        failures = validate_thresholds(report)
        if failures:
            print(json.dumps({"status": "THRESHOLD_FAILURE", "failures": list(failures)}, separators=(",", ":")))
            return 1
        print(report_as_json(report))
        return 0

    if not args.live:
        print('{"status":"LIVE_FLAG_REQUIRED"}')
        return 2
    if not args.compare_models:
        print('{"status":"COMPARE_MODELS_FLAG_REQUIRED"}')
        return 2
    if live_eval_missing_gates(
        provider=args.provider, live=args.live, compare_models=args.compare_models
    ):
        print('{"status":"LIVE_CONFIGURATION_REQUIRED"}')
        return 2
    if not args.manifest or not args.report_dir:
        print('{"status":"LIVE_MANIFEST_AND_REPORT_DIR_REQUIRED"}')
        return 2
    try:
        run_real_pdf_smoke(
            manifest_path=args.manifest,
            report_dir=args.report_dir,
            provider=args.provider,
            live=args.live,
            compare_models=args.compare_models,
        )
    except LiveHarnessUnavailable:
        print('{"status":"LIVE_HARNESS_UNAVAILABLE"}')
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through module CLI
    raise SystemExit(main())
