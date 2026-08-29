from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal


RiskClass = Literal["LOW", "CRITICAL"]


@dataclass(frozen=True)
class CandidateContext:
    """Server-owned inputs used to decide how a draft must be reviewed."""

    candidate_type: str
    operation: str
    confidence: float
    issue_severities: tuple[str, ...]
    has_conflict: bool
    field_key: str | None = None
    has_evidence: bool = False


@dataclass(frozen=True)
class CandidateDecision:
    risk_class: RiskClass
    requires_individual_review: bool


_ALWAYS_CRITICAL_TYPES = frozenset(
    {
        "RULE",
        "UNIT_CREATE",
        "MEA_RESCALE",
        "LEGAL_REFERENCE",
        "ASSIGNMENT",
        "DOCUMENT_RELATION",
    }
)
_PROPERTY_FIELDS = frozenset(
    {"name", "street", "zip_code", "city", "management_type", "mea_denominator"}
)
_UNIT_FIELDS = frozenset(
    {
        "number",
        "location",
        "usage_type",
        "type",
        "area",
        "heated_area",
        "building_part",
        "mea",
    }
)
_IDENTITY_PROPERTY_FIELDS = _PROPERTY_FIELDS
_CRITICAL_UNIT_FIELDS = frozenset({"number", "usage_type", "type", "mea"})
_KNOWN_ISSUE_SEVERITIES = frozenset({"INFO", "WARNING", "BLOCKER"})


def classify_candidate(context: CandidateContext) -> CandidateDecision:
    """Return the conservative review route without trusting model risk data."""
    if _requires_individual_review(context):
        return CandidateDecision(risk_class="CRITICAL", requires_individual_review=True)
    return CandidateDecision(risk_class="LOW", requires_individual_review=False)


def _requires_individual_review(context: CandidateContext) -> bool:
    if context.candidate_type in _ALWAYS_CRITICAL_TYPES:
        return True
    if context.operation != "CREATE" or context.has_conflict or not context.has_evidence:
        return True
    if not _has_safe_confidence(context.confidence):
        return True
    if any(severity not in _KNOWN_ISSUE_SEVERITIES or severity != "INFO" for severity in context.issue_severities):
        return True
    if context.candidate_type == "PROPERTY_FIELD":
        return context.field_key not in _PROPERTY_FIELDS or context.field_key in _IDENTITY_PROPERTY_FIELDS
    if context.candidate_type == "UNIT_FIELD":
        return context.field_key not in _UNIT_FIELDS or context.field_key in _CRITICAL_UNIT_FIELDS
    return True


def _has_safe_confidence(confidence: object) -> bool:
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return False
    return isfinite(confidence) and 0.85 <= confidence <= 1.0
