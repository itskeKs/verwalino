"""Pure planning primitives for applying a locked declaration review graph.

The builder in this module deliberately has no SQLAlchemy dependency.  It turns
already locked draft rows into a complete, typed, deterministic write plan and
raises a stable conflict before a canonical row can be added to a session.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import gcd
from typing import Any, Iterable, Mapping, Sequence

from pydantic import TypeAdapter, ValidationError

from app.declarations.policy import CandidateContext, classify_candidate
from app.declarations.schemas import (
    DocumentRelationPayload,
    EvidencePayload,
    FactPayload,
    NormalizedValue,
    RulePayload,
)
from app.declarations.validation import validate_allocation_formulas, validate_mea_totals
from app.models.declaration_candidate import (
    DeclarationCandidate,
    DeclarationCandidateEvidence,
    DeclarationCandidateIssue,
)
from app.models.declaration_import import DeclarationImport
from app.models.property_document import PropertyDocument


APPROVAL_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "hausverwaltung.declaration.approval.v1"
)
_VALUE_ADAPTER = TypeAdapter(NormalizedValue)
_PROPERTY_FIELDS = frozenset({"name", "street", "zip_code", "city", "management_type", "mea_denominator"})
_UNIT_FIELDS = frozenset({"number", "location", "building_part", "area", "heated_area", "usage_type", "mea"})
_LEGAL_FIELDS = frozenset({"reference_type", "district", "cadastral_district", "field_number", "parcel", "sheet", "details"})
_ASSIGNMENT_FIELDS = frozenset({"assignment_type", "designation", "location", "description", "unit_subject_key", "details"})
_TERMINAL = frozenset({"ACCEPTED", "CORRECTED", "REJECTED"})
_MAX_MEA_DENOMINATOR = 1_000_000_000_000


class ApprovalConflict(Exception):
    """A deterministic, safe-to-return validation conflict."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def convert_mea_exact(
    numerator: int, source_denominator: int, target_denominator: int
) -> int:
    """Convert a legal MEA fraction without rounding it."""
    if numerator < 0 or source_denominator <= 0 or target_denominator <= 0:
        raise ApprovalConflict("INVALID_MEA")
    quotient, remainder = divmod(numerator * target_denominator, source_denominator)
    if remainder:
        raise ApprovalConflict("MEA_NOT_EXACTLY_CONVERTIBLE")
    return quotient


def common_denominator(denominators: Sequence[int]) -> int:
    """Return the bounded LCM needed to represent every supplied MEA share."""
    value = 1
    for denominator in denominators:
        if denominator <= 0:
            raise ApprovalConflict("INVALID_MEA")
        value = value * denominator // gcd(value, denominator)
        if value > _MAX_MEA_DENOMINATOR:
            raise ApprovalConflict("MEA_SCALE_TOO_LARGE")
    return value


@dataclass(frozen=True, slots=True)
class EvidencePlan:
    document_id: uuid.UUID
    pdf_page: int
    printed_page: str | None
    section: str | None
    quote: str
    field_path: str | None


@dataclass(frozen=True, slots=True)
class PlannedUnit:
    id: uuid.UUID
    stable_key: str
    subject_key: str
    number: str
    location: str
    building_part: str
    area: Decimal | None
    heated_area: Decimal | None
    usage_type: str
    mea_numerator: int
    mea_denominator: int
    value_fields: Mapping[str, dict[str, Any]]
    evidence: tuple[EvidencePlan, ...]


@dataclass(frozen=True, slots=True)
class PlannedLegalReference:
    id: uuid.UUID
    stable_key: str
    values: Mapping[str, Any]
    value_fields: Mapping[str, dict[str, Any]]
    evidence: tuple[EvidencePlan, ...]


@dataclass(frozen=True, slots=True)
class PlannedAssignment:
    id: uuid.UUID
    stable_key: str
    unit_subject_key: str | None
    values: Mapping[str, Any]
    value_fields: Mapping[str, dict[str, Any]]
    evidence: tuple[EvidencePlan, ...]


@dataclass(frozen=True, slots=True)
class PlannedRule:
    id: uuid.UUID
    stable_key: str
    source_stable_key: str
    topic: str
    title: str
    summary: str
    scope_json: dict[str, Any]
    effect_json: dict[str, Any]
    status: str
    supersedes_subject_keys: tuple[str, ...]
    conflicts_with_subject_keys: tuple[str, ...]
    evidence: tuple[EvidencePlan, ...]
    approved_value_json: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PlannedDocumentRelation:
    id: uuid.UUID
    stable_key: str
    source_document_id: uuid.UUID
    target_document_id: uuid.UUID
    relation_type: str
    approved_value_json: dict[str, Any]
    evidence: tuple[EvidencePlan, ...]


@dataclass(frozen=True, slots=True)
class FactSourcePlan:
    id: uuid.UUID
    subject_kind: str
    subject_ref: str
    field_key: str
    approved_value_json: dict[str, Any]
    evidence: EvidencePlan

    @property
    def display_value(self) -> str:
        """The approved snapshot display used by the searchable provenance projection."""
        kind = self.approved_value_json.get("kind")
        if kind in {"MEA", "FRACTION"}:
            return f"{_legal_integer_display(self.approved_value_json.get('numerator'))}/{_legal_integer_display(self.approved_value_json.get('denominator'))}"
        if kind == "DECIMAL":
            return " ".join(
                part for part in (
                    str(self.approved_value_json.get("value", "")),
                    str(self.approved_value_json.get("unit") or ""),
                ) if part
            )
        if kind == "PERCENTAGE":
            return f"{self.approved_value_json.get('value')} Prozent"
        if kind == "BOOLEAN":
            return "Ja" if self.approved_value_json.get("value") else "Nein"
        if kind in {"TEXT", "INTEGER", "DATE"}:
            return str(self.approved_value_json.get("value", ""))
        return json.dumps(self.approved_value_json, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _legal_integer_display(value: object) -> str:
    return f"{int(value):,}".replace(",", ".")


@dataclass(frozen=True, slots=True)
class RuleRelationPlan:
    id: uuid.UUID
    source_rule_stable_key: str
    target_rule_subject_key: str
    relation_type: str


@dataclass(frozen=True, slots=True)
class ApprovalPlan:
    import_id: uuid.UUID
    organization_id: uuid.UUID
    property_id: uuid.UUID
    property_fields: Mapping[str, Any]
    property_value_fields: Mapping[str, dict[str, Any]]
    property_evidence: Mapping[str, tuple[EvidencePlan, ...]]
    mea_denominator: int
    units: tuple[PlannedUnit, ...]
    legal_references: tuple[PlannedLegalReference, ...]
    assignments: tuple[PlannedAssignment, ...]
    document_relations: tuple[PlannedDocumentRelation, ...]
    rules: tuple[PlannedRule, ...]
    rule_relations: tuple[RuleRelationPlan, ...]
    fact_sources: tuple[FactSourcePlan, ...]
    document_ids: tuple[uuid.UUID, ...]

    @property
    def summary(self) -> dict[str, int]:
        return {
            "properties_created": 1,
            "units_created": len(self.units),
            "units_updated": 0,
            "rules_created": len(self.rules),
            "documents_bound": len(self.document_ids),
        }

    def response(self, draft_revision: int) -> dict[str, Any]:
        return {
            "import_id": str(self.import_id),
            "status": "APPLIED",
            "property_id": str(self.property_id),
            "draft_revision": draft_revision,
            "summary": self.summary,
        }

@dataclass(frozen=True, slots=True)
class ExistingFieldUpdate:
    """One already-compared canonical field update and its provenance."""

    subject_kind: str
    subject_id: uuid.UUID
    subject_ref: str
    field_key: str
    value: Any
    value_json: dict[str, Any]
    previous_value_json: dict[str, Any]
    evidence: tuple[EvidencePlan, ...]


@dataclass(frozen=True, slots=True)
class ExistingApprovalPlan:
    """A complete, non-destructive amendment for one locked property graph."""

    import_id: uuid.UUID
    organization_id: uuid.UUID
    property_id: uuid.UUID
    unit_subject_ids: Mapping[str, uuid.UUID]
    property_updates: tuple[ExistingFieldUpdate, ...]
    unit_updates: tuple[ExistingFieldUpdate, ...]
    units_created: tuple[PlannedUnit, ...]
    legal_references: tuple[PlannedLegalReference, ...]
    assignments: tuple[PlannedAssignment, ...]
    document_relations: tuple[PlannedDocumentRelation, ...]
    rules_created: tuple[PlannedRule, ...]
    rule_supersedes: tuple[tuple[uuid.UUID, uuid.UUID], ...]
    fact_sources: tuple[FactSourcePlan, ...]
    mea_denominator: int | None
    mea_scale_confirmed: bool
    previous_mea_scale_status: str
    previous_mea_denominator: int | None
    document_ids: tuple[uuid.UUID, ...]

    @property
    def summary(self) -> dict[str, int]:
        return {
            "properties_created": 0,
            "properties_updated": int(bool(self.property_updates) or self.mea_denominator is not None),
            "units_created": len(self.units_created),
            "units_updated": len({update.subject_id for update in self.unit_updates}),
            "rules_created": len(self.rules_created),
            "documents_bound": len(self.document_ids),
        }

    def response(self, draft_revision: int) -> dict[str, Any]:
        return {
            "import_id": str(self.import_id),
            "status": "APPLIED",
            "property_id": str(self.property_id),
            "draft_revision": draft_revision,
            "summary": self.summary,
        }

def deterministic_id(
    organization_id: uuid.UUID, import_id: uuid.UUID, kind: str, stable_key: str
) -> uuid.UUID:
    return uuid.uuid5(APPROVAL_NAMESPACE, f"{organization_id}:{import_id}:{kind}:{stable_key}")


def value_fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_approval_plan(
    item: DeclarationImport,
    candidates: Sequence[DeclarationCandidate],
    evidence_by_candidate: Mapping[uuid.UUID, Sequence[DeclarationCandidateEvidence]],
    issues: Sequence[DeclarationCandidateIssue],
    documents: Sequence[PropertyDocument],
) -> ApprovalPlan:
    """Validate a complete locked graph without mutating canonical persistence."""
    if item.mode == "NEW_PROPERTY" and getattr(item, "target_property_id", None) is not None:
        raise ApprovalConflict("TARGET_PROPERTY_NOT_ALLOWED")
    if item.mode != "NEW_PROPERTY":
        raise ApprovalConflict("EXISTING_PROPERTY_APPROVAL_UNSUPPORTED")
    if any(issue.resolution_status == "OPEN" and issue.severity == "BLOCKER" for issue in issues):
        raise ApprovalConflict("OPEN_BLOCKER")
    if any(candidate.operation == "DELETE" for candidate in candidates):
        raise ApprovalConflict("DELETE_NOT_ALLOWED")
    if any(candidate.review_status not in _TERMINAL for candidate in candidates):
        raise ApprovalConflict("PENDING_REVIEW")
    docs_by_id = {document.id: document for document in documents}
    if len(docs_by_id) != len(documents) or not docs_by_id:
        raise ApprovalConflict("IMPORT_DOCUMENT_REFERENCE_INVALID")
    if any(document.organization_id != item.organization_id for document in documents):
        raise ApprovalConflict("IMPORT_DOCUMENT_REFERENCE_INVALID")
    if any(document.archived_at is not None for document in documents):
        raise ApprovalConflict("DOCUMENT_ARCHIVED")
    if any(document.property_id is not None for document in documents):
        raise ApprovalConflict("DOCUMENT_ALREADY_BOUND")

    open_issue_severities: dict[uuid.UUID, tuple[str, ...]] = {}
    for issue in issues:
        if issue.resolution_status == "OPEN" and issue.candidate_id is not None:
            open_issue_severities.setdefault(issue.candidate_id, ())
            open_issue_severities[issue.candidate_id] += (issue.severity,)
    accepted: list[tuple[DeclarationCandidate, object, tuple[EvidencePlan, ...]]] = []
    for candidate in sorted(candidates, key=lambda value: (value.stable_key, str(value.id))):
        if candidate.review_status == "REJECTED":
            continue
        value = _effective_value(candidate)
        evidence = _validated_evidence(candidate, evidence_by_candidate.get(candidate.id, ()), docs_by_id)
        decision = classify_candidate(CandidateContext(
            candidate.candidate_type,
            candidate.operation,
            float(candidate.confidence),
            open_issue_severities.get(candidate.id, ()),
            _has_conflict(value),
            candidate.field_key,
            bool(evidence),
        ))
        if decision.risk_class == "CRITICAL" and not evidence:
            raise ApprovalConflict("EVIDENCE_REQUIRED")
        if not evidence:
            raise ApprovalConflict("EVIDENCE_REQUIRED")
        accepted.append((candidate, value, evidence))

    property_id = deterministic_id(item.organization_id, item.id, "property", "new")
    property_values: dict[str, Any] = {}
    property_value_fields: dict[str, dict[str, Any]] = {}
    property_evidence: dict[str, tuple[EvidencePlan, ...]] = {}
    property_subject_keys: set[str] = set()
    units: list[PlannedUnit] = []
    legal_references: list[PlannedLegalReference] = []
    assignments: list[PlannedAssignment] = []
    relations: list[PlannedDocumentRelation] = []
    rules: list[PlannedRule] = []
    facts: list[FactSourcePlan] = []

    for candidate, raw_value, evidence in accepted:
        if candidate.candidate_type == "PROPERTY_FIELD":
            field = candidate.field_key
            if field not in _PROPERTY_FIELDS:
                raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")
            if field in property_values:
                raise ApprovalConflict("CONFLICTING_PROPERTY_FIELD")
            normalized = _parse_fact(candidate.subject_key, field, raw_value)
            property_subject_keys.add(candidate.subject_key)
            property_values[field] = _normalized_python(normalized)
            property_value_fields[field] = normalized.model_dump(mode="json")
            property_evidence[field] = evidence
            facts.extend(_fact_sources(item, candidate, "PROPERTY", "property", field, property_value_fields[field], evidence))
        elif candidate.candidate_type == "UNIT_CREATE":
            values = _parse_composite_fact(candidate, raw_value, _UNIT_FIELDS)
            unit = _build_unit(item, candidate, values, evidence)
            units.append(unit)
            for field, value in unit.value_fields.items():
                facts.extend(_fact_sources(item, candidate, "UNIT", unit.subject_key, field, value, evidence))
        elif candidate.candidate_type == "LEGAL_REFERENCE":
            values = _parse_composite_fact(candidate, raw_value, _LEGAL_FIELDS)
            reference_type = _required_text_value(values.get("reference_type"))
            legal_values = {
                field: _normalized_python(value) for field, value in values.items()
            }
            legal_values["reference_type"] = _normalized_python(reference_type)
            legal_value_fields = {
                field: value.model_dump(mode="json") for field, value in values.items()
            }
            legal_value_fields["reference_type"] = reference_type.model_dump(mode="json")
            legal = PlannedLegalReference(
                id=deterministic_id(item.organization_id, item.id, "legal_reference", candidate.stable_key),
                stable_key=candidate.stable_key,
                values=legal_values,
                value_fields=legal_value_fields,
                evidence=evidence,
            )
            _validate_string_lengths(legal.values, {
                "reference_type": 48, "district": 255, "cadastral_district": 255,
                "field_number": 100, "parcel": 100, "sheet": 100,
            })
            legal_references.append(legal)
            for field, value in legal.value_fields.items():
                facts.extend(_fact_sources(item, candidate, "LEGAL_REFERENCE", legal.stable_key, field, value, evidence))
        elif candidate.candidate_type == "ASSIGNMENT":
            values = _parse_composite_fact(candidate, raw_value, _ASSIGNMENT_FIELDS)
            assignment_type = _required_text_value(values.get("assignment_type"))
            designation = _required_text_value(values.get("designation"))
            assignment_values = {
                field: _normalized_python(value) for field, value in values.items()
            }
            assignment_values["assignment_type"] = _normalized_python(assignment_type)
            assignment_values["designation"] = _normalized_python(designation)
            assignment_value_fields = {
                field: value.model_dump(mode="json") for field, value in values.items()
            }
            assignment_value_fields["assignment_type"] = assignment_type.model_dump(mode="json")
            assignment_value_fields["designation"] = designation.model_dump(mode="json")
            assignment = PlannedAssignment(
                id=deterministic_id(item.organization_id, item.id, "assignment", candidate.stable_key),
                stable_key=candidate.stable_key,
                unit_subject_key=_required_text(values.get("unit_subject_key")) if "unit_subject_key" in values else None,
                values=assignment_values,
                value_fields=assignment_value_fields,
                evidence=evidence,
            )
            _validate_string_lengths(assignment.values, {
                "assignment_type": 64, "designation": 255, "location": 255,
                "description": 4_000,
            })
            assignments.append(assignment)
            for field, value in assignment.value_fields.items():
                facts.extend(_fact_sources(item, candidate, "ASSIGNMENT", assignment.stable_key, field, value, evidence))
        elif candidate.candidate_type == "DOCUMENT_RELATION":
            relation = _build_document_relation(item, candidate, raw_value, evidence, docs_by_id)
            relations.append(relation)
            facts.extend(_fact_sources(item, candidate, "DOCUMENT_RELATION", relation.stable_key, "relation", relation.approved_value_json, evidence))
        elif candidate.candidate_type == "RULE":
            rule = _build_rule(item, property_id, candidate, raw_value, evidence)
            rules.append(rule)
            facts.extend(_fact_sources(item, candidate, "RULE", rule.source_stable_key, "rule", rule.approved_value_json, evidence))
        else:
            raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")

    _validate_property(property_values)
    if len(property_subject_keys) != 1:
        raise ApprovalConflict("AMBIGUOUS_PROPERTY_SUBJECT")
    denominator = property_values["mea_denominator"]
    assert isinstance(denominator, int)
    _validate_units(units, denominator)
    unit_keys = {unit.subject_key for unit in units}
    if any(assignment.unit_subject_key not in unit_keys for assignment in assignments if assignment.unit_subject_key is not None):
        raise ApprovalConflict("DANGLING_UNIT_REFERENCE")
    relation_edges = {
        (relation.source_document_id, relation.target_document_id, relation.relation_type)
        for relation in relations
    }
    if len(relation_edges) != len(relations):
        raise ApprovalConflict("DUPLICATE_DOCUMENT_RELATION")
    rule_by_subject = {rule.source_stable_key: rule for rule in rules}
    if len(rule_by_subject) != len(rules):
        raise ApprovalConflict("DUPLICATE_RULE_SUBJECT")
    rule_relations: list[RuleRelationPlan] = []
    rule_relation_edges: set[tuple[str, str, str]] = set()
    for rule in rules:
        if any(key not in unit_keys for key in rule.scope_json.get("unit_subject_keys", [])):
            raise ApprovalConflict("DANGLING_UNIT_REFERENCE")
        for target in rule.supersedes_subject_keys:
            if target not in rule_by_subject:
                raise ApprovalConflict("DANGLING_RULE_REFERENCE")
            if target == rule.source_stable_key:
                raise ApprovalConflict("RULE_SELF_RELATION")
            edge = (rule.source_stable_key, target, "SUPERSEDES")
            if edge in rule_relation_edges:
                raise ApprovalConflict("DUPLICATE_RULE_RELATION")
            rule_relation_edges.add(edge)
            rule_relations.append(RuleRelationPlan(
                deterministic_id(item.organization_id, item.id, "rule_relation", f"{rule.source_stable_key}:SUPERSEDES:{target}"),
                rule.source_stable_key, target, "SUPERSEDES",
            ))
        for target in rule.conflicts_with_subject_keys:
            if target not in rule_by_subject:
                raise ApprovalConflict("DANGLING_RULE_REFERENCE")
            if target == rule.source_stable_key:
                raise ApprovalConflict("RULE_SELF_RELATION")
            edge = (rule.source_stable_key, target, "CONFLICTS")
            if edge in rule_relation_edges:
                raise ApprovalConflict("DUPLICATE_RULE_RELATION")
            rule_relation_edges.add(edge)
            rule_relations.append(RuleRelationPlan(
                deterministic_id(item.organization_id, item.id, "rule_relation", f"{rule.source_stable_key}:CONFLICTS:{target}"),
                rule.source_stable_key, target, "CONFLICTS",
            ))

    _validate_fact_source_collisions(facts)

    return ApprovalPlan(
        import_id=item.id,
        organization_id=item.organization_id,
        property_id=property_id,
        property_fields=property_values,
        property_value_fields=property_value_fields,
        property_evidence=property_evidence,
        mea_denominator=denominator,
        units=tuple(sorted(units, key=lambda value: value.subject_key)),
        legal_references=tuple(sorted(legal_references, key=lambda value: value.stable_key)),
        assignments=tuple(sorted(assignments, key=lambda value: value.stable_key)),
        document_relations=tuple(sorted(relations, key=lambda value: value.stable_key)),
        rules=tuple(sorted(rules, key=lambda value: value.source_stable_key)),
        rule_relations=tuple(sorted(rule_relations, key=lambda value: (value.source_rule_stable_key, value.relation_type, value.target_rule_subject_key))),
        fact_sources=tuple(sorted(facts, key=lambda value: str(value.id))),
        document_ids=tuple(sorted(docs_by_id, key=str)),
    )


def build_existing_approval_plan(
    item: DeclarationImport,
    candidates: Sequence[DeclarationCandidate],
    evidence_by_candidate: Mapping[uuid.UUID, Sequence[DeclarationCandidateEvidence]],
    issues: Sequence[DeclarationCandidateIssue],
    documents: Sequence[PropertyDocument],
    property_row: Any,
    units: Sequence[Any],
    rules: Sequence[Any],
    existing_assignments: Sequence[Any] = (),
    existing_document_relations: Sequence[Any] = (),
) -> ExistingApprovalPlan:
    """Build an amendment from a graph locked by the caller, without writes.

    Canonical rows are deliberately inputs rather than ORM queries so this
    function remains a pure, deterministic validation boundary.
    """
    if item.mode != "EXISTING_PROPERTY" or item.target_property_id is None:
        raise ApprovalConflict("EXISTING_PROPERTY_TARGET_REQUIRED")
    if property_row is None or property_row.id != item.target_property_id or property_row.organization_id != item.organization_id:
        raise ApprovalConflict("TARGET_PROPERTY_NOT_FOUND")
    if any(issue.resolution_status == "OPEN" and issue.severity == "BLOCKER" for issue in issues):
        raise ApprovalConflict("OPEN_BLOCKER")
    if any(candidate.operation == "DELETE" for candidate in candidates):
        raise ApprovalConflict("DELETE_NOT_ALLOWED")
    if any(candidate.review_status not in _TERMINAL for candidate in candidates):
        raise ApprovalConflict("PENDING_REVIEW")
    allowed_operations = {
        "PROPERTY_FIELD": "UPDATE",
        "UNIT_FIELD": "UPDATE",
        "MEA_RESCALE": "UPDATE",
        "UNIT_CREATE": "CREATE",
        "LEGAL_REFERENCE": "CREATE",
        "ASSIGNMENT": "CREATE",
        "DOCUMENT_RELATION": "CREATE",
        "RULE": "CREATE",
    }
    for candidate in candidates:
        if candidate.review_status == "REJECTED" or candidate.operation == "KEEP":
            continue
        if allowed_operations.get(candidate.candidate_type) != candidate.operation:
            raise ApprovalConflict("EXISTING_MUTATION_UNSUPPORTED")
    if property_row.mea_scale_status != "CONFIRMED":
        active = [candidate for candidate in candidates if candidate.review_status != "REJECTED"]
        touches_mea = any(
            candidate.candidate_type == "MEA_RESCALE"
            or (candidate.candidate_type == "UNIT_FIELD" and candidate.field_key == "mea")
            or candidate.candidate_type == "UNIT_CREATE"
            for candidate in active
        )
        has_confirmation = any(
            candidate.candidate_type == "PROPERTY_FIELD"
            and candidate.field_key == "mea_denominator"
            and candidate.operation == "UPDATE"
            for candidate in active
        )
        if touches_mea and not has_confirmation:
            raise ApprovalConflict("MEA_CANONICAL_SCALE_UNCONFIRMED")

    docs_by_id = {document.id: document for document in documents}
    if len(docs_by_id) != len(documents) or not docs_by_id:
        raise ApprovalConflict("IMPORT_DOCUMENT_REFERENCE_INVALID")
    for document in documents:
        if document.organization_id != item.organization_id:
            raise ApprovalConflict("IMPORT_DOCUMENT_REFERENCE_INVALID")
        if document.archived_at is not None:
            raise ApprovalConflict("DOCUMENT_ARCHIVED")
        if document.property_id not in {None, property_row.id}:
            raise ApprovalConflict("DOCUMENT_ALREADY_BOUND")

    by_subject: dict[str, list[DeclarationCandidate]] = {}
    for candidate in candidates:
        by_subject.setdefault(candidate.subject_key, []).append(candidate)
    units_by_number: dict[str, list[Any]] = {}
    for unit in units:
        units_by_number.setdefault(_normalize_unit_number(unit.number), []).append(unit)
    units_by_id = {unit.id: unit for unit in units}
    unit_subject_ids: dict[str, uuid.UUID] = {}
    for subject_key, subject_candidates in sorted(by_subject.items()):
        if any(
            candidate.candidate_type == "UNIT_FIELD"
            and candidate.review_status != "REJECTED"
            for candidate in subject_candidates
        ):
            unit_subject_ids[subject_key] = _resolve_existing_unit(
                subject_key, subject_candidates, units_by_number
            ).id
    existing_assignment_keys = {assignment.stable_key for assignment in existing_assignments}
    existing_relation_edges = {
        (relation.source_document_id, relation.target_document_id, relation.relation_type)
        for relation in existing_document_relations
    }

    property_updates: list[ExistingFieldUpdate] = []
    unit_updates: list[ExistingFieldUpdate] = []
    new_units: list[PlannedUnit] = []
    legal_references: list[PlannedLegalReference] = []
    assignments: list[PlannedAssignment] = []
    document_relations: list[PlannedDocumentRelation] = []
    new_rules: list[PlannedRule] = []
    new_rule_subject_keys: set[str] = set()
    rule_supersedes: list[tuple[uuid.UUID, uuid.UUID]] = []
    facts: list[FactSourcePlan] = []
    update_subjects: set[tuple[str, uuid.UUID, str]] = set()
    pending_mea: dict[uuid.UUID, tuple[int, int]] = {}
    superseded_rule_ids: set[uuid.UUID] = set()
    requested_denominator: int | None = None
    confirmation_requested = False
    open_issue_severities: dict[uuid.UUID, tuple[str, ...]] = {}
    for issue in issues:
        if issue.resolution_status == "OPEN" and issue.candidate_id is not None:
            open_issue_severities.setdefault(issue.candidate_id, ())
            open_issue_severities[issue.candidate_id] += (issue.severity,)

    for candidate in sorted(candidates, key=lambda value: (value.stable_key, str(value.id))):
        if candidate.review_status == "REJECTED" or candidate.operation == "KEEP":
            continue
        evidence = _validated_evidence(candidate, evidence_by_candidate.get(candidate.id, ()), docs_by_id)
        if not evidence:
            raise ApprovalConflict("EVIDENCE_REQUIRED")
        policy = classify_candidate(CandidateContext(
            candidate.candidate_type, candidate.operation, float(candidate.confidence),
            open_issue_severities.get(candidate.id, ()), _has_conflict(_effective_value(candidate)),
            candidate.field_key, bool(evidence),
        ))
        if policy.requires_individual_review and not candidate.requires_individual_review:
            # A persisted policy flag is defense in depth, but UPDATE is never
            # eligible for bulk/implicit application regardless of old drafts.
            raise ApprovalConflict("INDIVIDUAL_REVIEW_REQUIRED")
        value = _effective_value(candidate)
        if candidate.candidate_type == "PROPERTY_FIELD":
            field = candidate.field_key
            if field not in _PROPERTY_FIELDS:
                raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")
            normalized = _parse_fact(candidate.subject_key, field, value)
            if field != "mea_denominator" and (
                not isinstance(_normalized_python(normalized), str)
                or not _normalized_python(normalized).strip()
            ):
                raise ApprovalConflict("INCOMPLETE_PROPERTY")
            value_json = normalized.model_dump(mode="json")
            canonical = _canonical_property_value(property_row, field)
            if not (
                field == "mea_denominator"
                and property_row.mea_scale_status != "CONFIRMED"
                and canonical is None
                and candidate.previous_value_json is None
            ):
                _require_previous_matches(candidate, canonical)
            if field == "mea_denominator":
                requested_denominator = _normalized_python(normalized)
                if not isinstance(requested_denominator, int) or isinstance(requested_denominator, bool) or not 0 < requested_denominator <= _MAX_MEA_DENOMINATOR:
                    raise ApprovalConflict("INVALID_MEA_DENOMINATOR")
                confirmation_requested = True
                key = ("PROPERTY", property_row.id, field)
                if key in update_subjects:
                    raise ApprovalConflict("CONFLICTING_PROPERTY_FIELD")
                update_subjects.add(key)
                property_updates.append(ExistingFieldUpdate("PROPERTY", property_row.id, "property", field, requested_denominator, value_json, canonical or value_json, evidence))
            else:
                key = ("PROPERTY", property_row.id, field)
                if key in update_subjects:
                    raise ApprovalConflict("CONFLICTING_PROPERTY_FIELD")
                update_subjects.add(key)
                property_updates.append(ExistingFieldUpdate("PROPERTY", property_row.id, "property", field, _normalized_python(normalized), value_json, canonical or value_json, evidence))
        elif candidate.candidate_type == "UNIT_FIELD":
            field = candidate.field_key
            if field not in _UNIT_FIELDS:
                raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")
            unit_id = unit_subject_ids.get(candidate.subject_key)
            if unit_id is None:
                raise ApprovalConflict("UNIT_MATCH_NOT_FOUND")
            unit = units_by_id[unit_id]
            normalized = _parse_fact(candidate.subject_key, field, value)
            value_json = normalized.model_dump(mode="json")
            canonical = _canonical_unit_value(unit, property_row, field)
            _require_previous_matches(candidate, canonical)
            key = ("UNIT", unit.id, field)
            if key in update_subjects:
                raise ApprovalConflict("CONFLICTING_UNIT_FIELD")
            update_subjects.add(key)
            python_value = _normalized_python(normalized)
            if field in {"number", "location", "building_part", "usage_type"}:
                if not isinstance(python_value, str):
                    raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")
                if field in {"number", "usage_type"} and not python_value.strip():
                    raise ApprovalConflict("INCOMPLETE_UNIT")
                _validate_string_lengths({field: python_value}, {"number": 50, "location": 255, "building_part": 255, "usage_type": 12})
                if field == "usage_type" and python_value not in {"WOHNUNG", "GEWERBE", "STELLPLATZ", "SONSTIGE"}:
                    raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")
            if field in {"area", "heated_area"}:
                python_value = _decimal_or_none(normalized)
            if field == "mea":
                if not isinstance(python_value, dict):
                    raise ApprovalConflict("INVALID_MEA")
                if not all(isinstance(python_value.get(key), int) for key in ("numerator", "denominator")) or python_value["numerator"] < 0 or python_value["denominator"] <= 0 or python_value["numerator"] > python_value["denominator"]:
                    raise ApprovalConflict("INVALID_MEA")
                pending_mea[unit.id] = (python_value["numerator"], python_value["denominator"])
            unit_updates.append(ExistingFieldUpdate("UNIT", unit.id, candidate.subject_key, field, python_value, value_json, canonical or value_json, evidence))
        elif candidate.candidate_type == "UNIT_CREATE":
            values = _parse_composite_fact(candidate, value, _UNIT_FIELDS)
            planned = _build_unit(item, candidate, values, evidence)
            if _normalize_unit_number(planned.number) in units_by_number or any(_normalize_unit_number(existing.number) == _normalize_unit_number(planned.number) for existing in new_units):
                raise ApprovalConflict("DUPLICATE_UNIT_NUMBER")
            new_units.append(planned)
        elif candidate.candidate_type == "LEGAL_REFERENCE" and candidate.operation == "CREATE":
            values = _parse_composite_fact(candidate, value, _LEGAL_FIELDS)
            reference_type = _required_text_value(values.get("reference_type"))
            legal_values = {field: _normalized_python(entry) for field, entry in values.items()}
            legal_values["reference_type"] = _normalized_python(reference_type)
            value_fields = {field: entry.model_dump(mode="json") for field, entry in values.items()}
            value_fields["reference_type"] = reference_type.model_dump(mode="json")
            legal = PlannedLegalReference(
                deterministic_id(item.organization_id, item.id, "legal_reference", candidate.stable_key),
                candidate.stable_key, legal_values, value_fields, evidence,
            )
            _validate_string_lengths(legal.values, {"reference_type": 48, "district": 255, "cadastral_district": 255, "field_number": 100, "parcel": 100, "sheet": 100})
            legal_references.append(legal)
            for field, value_json in legal.value_fields.items():
                facts.extend(_fact_sources(item, candidate, "LEGAL_REFERENCE", legal.stable_key, field, value_json, evidence))
        elif candidate.candidate_type == "ASSIGNMENT" and candidate.operation == "CREATE":
            values = _parse_composite_fact(candidate, value, _ASSIGNMENT_FIELDS)
            assignment_type = _required_text_value(values.get("assignment_type"))
            designation = _required_text_value(values.get("designation"))
            assignment_values = {field: _normalized_python(entry) for field, entry in values.items()}
            assignment_values["assignment_type"] = _normalized_python(assignment_type)
            assignment_values["designation"] = _normalized_python(designation)
            value_fields = {field: entry.model_dump(mode="json") for field, entry in values.items()}
            value_fields["assignment_type"] = assignment_type.model_dump(mode="json")
            value_fields["designation"] = designation.model_dump(mode="json")
            assignment = PlannedAssignment(
                deterministic_id(item.organization_id, item.id, "assignment", candidate.stable_key),
                candidate.stable_key,
                _required_text(values.get("unit_subject_key")) if "unit_subject_key" in values else None,
                assignment_values, value_fields, evidence,
            )
            if assignment.stable_key in existing_assignment_keys:
                raise ApprovalConflict("DUPLICATE_ASSIGNMENT_STABLE_KEY")
            _validate_string_lengths(assignment.values, {"assignment_type": 64, "designation": 255, "location": 255, "description": 4_000})
            assignments.append(assignment)
            for field, value_json in assignment.value_fields.items():
                facts.extend(_fact_sources(item, candidate, "ASSIGNMENT", assignment.stable_key, field, value_json, evidence))
        elif candidate.candidate_type == "DOCUMENT_RELATION" and candidate.operation == "CREATE":
            relation = _build_document_relation(item, candidate, value, evidence, docs_by_id)
            if (relation.source_document_id, relation.target_document_id, relation.relation_type) in existing_relation_edges:
                raise ApprovalConflict("DUPLICATE_DOCUMENT_RELATION")
            document_relations.append(relation)
            facts.extend(_fact_sources(item, candidate, "DOCUMENT_RELATION", relation.stable_key, "relation", relation.approved_value_json, evidence))
        elif candidate.candidate_type == "MEA_RESCALE":
            if candidate.operation != "UPDATE" or not candidate.requires_individual_review:
                raise ApprovalConflict("INDIVIDUAL_REVIEW_REQUIRED")
            normalized = _parse_fact(candidate.subject_key, "mea_denominator", value)
            _require_previous_matches(candidate, _canonical_property_value(property_row, "mea_denominator"))
            requested_denominator = _normalized_python(normalized)
            if not isinstance(requested_denominator, int) or isinstance(requested_denominator, bool) or not 0 < requested_denominator <= _MAX_MEA_DENOMINATOR:
                raise ApprovalConflict("INVALID_MEA_DENOMINATOR")
            confirmation_requested = True
            key = ("PROPERTY", property_row.id, "mea_denominator")
            if key in update_subjects:
                raise ApprovalConflict("CONFLICTING_PROPERTY_FIELD")
            update_subjects.add(key)
            canonical = _canonical_property_value(property_row, "mea_denominator")
            property_updates.append(ExistingFieldUpdate("PROPERTY", property_row.id, "property", "mea_denominator", requested_denominator, normalized.model_dump(mode="json"), canonical or normalized.model_dump(mode="json"), evidence))
        elif candidate.candidate_type == "RULE":
            if not candidate.requires_individual_review:
                raise ApprovalConflict("INDIVIDUAL_REVIEW_REQUIRED")
            rule = _build_rule(item, property_row.id, candidate, value, evidence)
            if rule.source_stable_key in new_rule_subject_keys:
                raise ApprovalConflict("DUPLICATE_RULE_SUBJECT")
            new_rule_subject_keys.add(rule.source_stable_key)
            if rule.status != "EFFECTIVE":
                raise ApprovalConflict("RULE_NEEDS_CLARIFICATION")
            if rule.conflicts_with_subject_keys:
                raise ApprovalConflict("EXISTING_MUTATION_UNSUPPORTED")
            for identifier in rule.supersedes_subject_keys:
                matches = _resolve_rule_identifier(identifier, rules)
                if len(matches) != 1:
                    raise ApprovalConflict("DANGLING_RULE_REFERENCE" if not matches else "AMBIGUOUS_RULE_REFERENCE")
                if matches[0].id in superseded_rule_ids:
                    raise ApprovalConflict("DUPLICATE_RULE_RELATION")
                superseded_rule_ids.add(matches[0].id)
                rule_supersedes.append((rule.id, matches[0].id))
            new_rules.append(rule)
            facts.extend(_fact_sources(item, candidate, "RULE", rule.source_stable_key, "rule", rule.approved_value_json, evidence))
        else:
            # There is no safe implicit mapping for existing legal/assignment
            # rows or rule graphs.  Reject rather than silently replacing them.
            raise ApprovalConflict("EXISTING_MUTATION_UNSUPPORTED")

    current_denominator = property_row.mea_denominator
    touches_mea = bool(pending_mea or any("mea" in unit.value_fields for unit in new_units) or requested_denominator is not None)
    if property_row.mea_scale_status != "CONFIRMED":
        if touches_mea and not confirmation_requested:
            raise ApprovalConflict("MEA_CANONICAL_SCALE_UNCONFIRMED")
        if confirmation_requested:
            assert requested_denominator is not None
            if any(unit.mea is None or Decimal(unit.mea) != Decimal(unit.mea).to_integral_value() or int(unit.mea) < 0 or int(unit.mea) > requested_denominator for unit in units):
                raise ApprovalConflict("INVALID_MEA")
    elif requested_denominator is not None and requested_denominator != current_denominator:
        if current_denominator is None:
            raise ApprovalConflict("MEA_CANONICAL_SCALE_UNCONFIRMED")
        if any(
            unit.mea is None
            or Decimal(unit.mea) != Decimal(unit.mea).to_integral_value()
            or int(unit.mea) < 0
            or int(unit.mea) > current_denominator
            for unit in units
        ):
            raise ApprovalConflict("INVALID_MEA")
        if set(pending_mea) != {unit.id for unit in units}:
            raise ApprovalConflict("MIXED_MEA_SCALE")
        for unit in units:
            new_numerator, new_denominator = pending_mea[unit.id]
            expected_numerator = convert_mea_exact(
                int(unit.mea), current_denominator, requested_denominator
            )
            if new_denominator != requested_denominator or new_numerator != expected_numerator:
                raise ApprovalConflict("MIXED_MEA_SCALE")
    elif any(denominator != (current_denominator or requested_denominator) for _, denominator in pending_mea.values()):
        raise ApprovalConflict("MIXED_MEA_SCALE")

    final_denominator = requested_denominator if requested_denominator is not None else current_denominator
    if final_denominator is not None:
        for planned in new_units:
            if planned.mea_denominator != final_denominator:
                raise ApprovalConflict("MIXED_MEA_SCALE")

    final_property = {
        field: getattr(property_row, field, None)
        for field in _PROPERTY_FIELDS
    }
    for update in property_updates:
        final_property[update.field_key] = update.value
    _validate_existing_property(final_property)

    created_unit_keys = {unit.subject_key for unit in new_units}
    matched_unit_keys = set(unit_subject_ids)
    if any(
        assignment.unit_subject_key not in created_unit_keys | matched_unit_keys
        for assignment in assignments if assignment.unit_subject_key is not None
    ):
        raise ApprovalConflict("DANGLING_UNIT_REFERENCE")
    for rule in new_rules:
        if any(subject_key not in created_unit_keys | matched_unit_keys for subject_key in rule.scope_json.get("unit_subject_keys", [])):
            raise ApprovalConflict("DANGLING_UNIT_REFERENCE")
    relation_edges = {(relation.source_document_id, relation.target_document_id, relation.relation_type) for relation in document_relations}
    if len(relation_edges) != len(document_relations):
        raise ApprovalConflict("DUPLICATE_DOCUMENT_RELATION")

    updates_by_unit_field = {
        (update.subject_id, update.field_key): update.value for update in unit_updates
    }
    final_numbers: list[str] = []
    final_numerators: list[int] = []
    if final_denominator is None:
        if touches_mea:
            raise ApprovalConflict("MEA_CANONICAL_SCALE_UNCONFIRMED")
        final_numbers_without_mea = [
            _normalize_unit_number(updates_by_unit_field.get((unit.id, "number"), unit.number))
            for unit in units
        ]
        final_numbers_without_mea.extend(_normalize_unit_number(unit.number) for unit in new_units)
        if len(final_numbers_without_mea) != len(set(final_numbers_without_mea)):
            raise ApprovalConflict("DUPLICATE_UNIT_NUMBER")
        _validate_fact_source_collisions(facts)
        return ExistingApprovalPlan(
            item.id, item.organization_id, property_row.id, dict(sorted(unit_subject_ids.items())), tuple(property_updates),
            tuple(unit_updates), tuple(new_units), tuple(sorted(legal_references, key=lambda value: value.stable_key)),
            tuple(sorted(assignments, key=lambda value: value.stable_key)),
            tuple(sorted(document_relations, key=lambda value: value.stable_key)), tuple(new_rules),
            tuple(rule_supersedes), tuple(sorted(facts, key=lambda value: str(value.id))), requested_denominator,
            property_row.mea_scale_status != "CONFIRMED" and confirmation_requested,
            property_row.mea_scale_status, property_row.mea_denominator,
            tuple(sorted((document.id for document in documents if document.property_id is None), key=str)),
        )
    for unit in units:
        number = updates_by_unit_field.get((unit.id, "number"), unit.number)
        if not isinstance(number, str) or not number.strip():
            raise ApprovalConflict("INCOMPLETE_UNIT")
        final_numbers.append(_normalize_unit_number(number))
        numerator_value = updates_by_unit_field.get((unit.id, "mea"), unit.mea)
        if isinstance(numerator_value, Mapping):
            numerator_value = numerator_value.get("numerator")
        if (
            isinstance(numerator_value, bool)
            or not isinstance(numerator_value, (int, Decimal))
            or Decimal(numerator_value) != Decimal(numerator_value).to_integral_value()
        ):
            raise ApprovalConflict("INVALID_MEA")
        numerator = int(numerator_value)
        if numerator < 0 or numerator > final_denominator:
            raise ApprovalConflict("INVALID_MEA")
        final_numerators.append(numerator)
    for planned in new_units:
        final_numbers.append(_normalize_unit_number(planned.number))
        if planned.mea_numerator < 0 or planned.mea_numerator > final_denominator:
            raise ApprovalConflict("INVALID_MEA")
        final_numerators.append(planned.mea_numerator)
    if len(final_numbers) != len(set(final_numbers)):
        raise ApprovalConflict("DUPLICATE_UNIT_NUMBER")
    if sum(final_numerators) > final_denominator:
        raise ApprovalConflict("MEA_TOTAL_INVALID")

    _validate_fact_source_collisions(facts)
    return ExistingApprovalPlan(
        item.id,
        item.organization_id,
        property_row.id,
        dict(sorted(unit_subject_ids.items())),
        tuple(property_updates),
        tuple(unit_updates),
        tuple(new_units),
        tuple(sorted(legal_references, key=lambda value: value.stable_key)),
        tuple(sorted(assignments, key=lambda value: value.stable_key)),
        tuple(sorted(document_relations, key=lambda value: value.stable_key)),
        tuple(new_rules),
        tuple(rule_supersedes),
        tuple(sorted(facts, key=lambda value: str(value.id))),
        requested_denominator,
        property_row.mea_scale_status != "CONFIRMED" and confirmation_requested,
        property_row.mea_scale_status,
        property_row.mea_denominator,
        tuple(sorted((document.id for document in documents if document.property_id is None), key=str)),
    )


def _resolve_rule_identifier(identifier: str, rules: Sequence[Any]) -> list[Any]:
    """Resolve only an exact locked stable key or exact UUID; never by prose."""
    matches = [rule for rule in rules if rule.stable_key == identifier]
    try:
        parsed = uuid.UUID(identifier)
    except (ValueError, TypeError):
        parsed = None
    if parsed is not None:
        matches.extend(rule for rule in rules if rule.id == parsed and rule not in matches)
    return matches


def _require_previous_matches(candidate: DeclarationCandidate, canonical: dict[str, Any] | None) -> None:
    if candidate.operation != "UPDATE":
        return
    if candidate.previous_value_json is None or canonical is None:
        raise ApprovalConflict("TARGET_CHANGED")
    try:
        expected = _parse_fact(candidate.subject_key, candidate.field_key or "mea_denominator", candidate.previous_value_json)
    except ApprovalConflict as exc:
        raise ApprovalConflict("TARGET_CHANGED") from exc
    if not _typed_value_equal(expected.model_dump(mode="json"), canonical):
        raise ApprovalConflict("TARGET_CHANGED")


def _typed_value_equal(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
    if expected.get("kind") != actual.get("kind"):
        return False
    kind = expected.get("kind")
    if kind in {"DECIMAL", "PERCENTAGE"}:
        try:
            return Decimal(str(expected.get("value"))) == Decimal(str(actual.get("value"))) and expected.get("unit") == actual.get("unit")
        except (InvalidOperation, ValueError):
            return False
    if kind in {"MEA", "FRACTION"}:
        return expected.get("numerator") == actual.get("numerator") and expected.get("denominator") == actual.get("denominator")
    return expected == actual


def _canonical_property_value(property_row: Any, field: str) -> dict[str, Any] | None:
    value = getattr(property_row, field, None)
    if field == "mea_denominator":
        return {"kind": "INTEGER", "value": value} if value is not None else None
    return {"kind": "TEXT", "value": value} if isinstance(value, str) else None


def _canonical_unit_value(unit: Any, property_row: Any, field: str) -> dict[str, Any] | None:
    value = getattr(unit, field, None)
    if field == "mea":
        denominator = getattr(property_row, "mea_denominator", None)
        if value is None or denominator is None or Decimal(value) != Decimal(value).to_integral_value():
            return None
        return {"kind": "MEA", "numerator": int(value), "denominator": denominator}
    if field in {"area", "heated_area"}:
        return {"kind": "DECIMAL", "value": str(value), "unit": "m²"} if value is not None else None
    return {"kind": "TEXT", "value": value} if isinstance(value, str) else None


def _resolve_existing_unit(subject_key: str, candidates: Sequence[DeclarationCandidate], units_by_number: Mapping[str, Sequence[Any]]) -> Any:
    number_candidates = [candidate for candidate in candidates if candidate.field_key == "number"]
    if len(number_candidates) != 1:
        raise ApprovalConflict("UNIT_MATCH_NOT_FOUND")
    candidate = number_candidates[0]
    raw = candidate.previous_value_json or candidate.proposed_value_json
    try:
        number = _required_text(_parse_fact(subject_key, "number", raw))
    except ApprovalConflict as exc:
        raise ApprovalConflict("UNIT_MATCH_NOT_FOUND") from exc
    matches = units_by_number.get(_normalize_unit_number(number), ())
    if not matches:
        raise ApprovalConflict("UNIT_MATCH_NOT_FOUND")
    if len(matches) != 1:
        raise ApprovalConflict("AMBIGUOUS_UNIT_MATCH")
    return matches[0]


def _effective_value(candidate: DeclarationCandidate) -> object:
    if candidate.review_status == "CORRECTED":
        if candidate.corrected_value_json is None:
            raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")
        return candidate.corrected_value_json
    if candidate.proposed_value_json is None:
        raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")
    return candidate.proposed_value_json


def _validated_evidence(
    candidate: DeclarationCandidate,
    evidence: Sequence[DeclarationCandidateEvidence],
    documents: Mapping[uuid.UUID, PropertyDocument],
) -> tuple[EvidencePlan, ...]:
    values: dict[tuple[uuid.UUID, int, str | None, str | None, str, str | None], EvidencePlan] = {}
    for item in evidence:
        document = documents.get(item.document_id)
        if document is None or item.pdf_page < 1 or item.pdf_page > document.page_count or not item.quote.strip():
            raise ApprovalConflict("DANGLING_EVIDENCE_REFERENCE")
        planned = EvidencePlan(
            item.document_id,
            item.pdf_page,
            item.printed_page,
            item.section_ref,
            item.quote,
            item.field_path,
        )
        # Exact duplicates are safe to collapse.  Every citation detail is
        # part of the key so a second quote or field path is never lost.
        values.setdefault(
            (
                planned.document_id,
                planned.pdf_page,
                planned.field_path,
                planned.section,
                planned.quote,
                planned.printed_page,
            ),
            planned,
        )
    return tuple(
        values[key]
        for key in sorted(
            values,
            key=lambda value: (
                str(value[0]), value[1], value[2] or "", value[3] or "",
                value[4], value[5] or "",
            ),
        )
    )


def _parse_fact(subject_key: str, field: str, value: object):
    try:
        normalized = _VALUE_ADAPTER.validate_python(value)
        return FactPayload(
            subject_key=subject_key,
            field=field,
            raw_value="approval",
            normalized_value=normalized,
            confidence=1,
            evidence=[EvidencePayload(document_key="approval", pdf_page=1, quote="approval")],
        ).normalized_value
    except (ValidationError, ValueError, TypeError) as exc:
        raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED") from exc


def _parse_composite_fact(
    candidate: DeclarationCandidate, raw_value: object, allowed_fields: frozenset[str]
) -> dict[str, Any]:
    if not isinstance(raw_value, dict) or not raw_value:
        raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")
    if set(raw_value) - allowed_fields:
        raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")
    return {field: _parse_fact(candidate.subject_key, field, value) for field, value in sorted(raw_value.items())}


def _build_unit(item: DeclarationImport, candidate: DeclarationCandidate, values: Mapping[str, Any], evidence: tuple[EvidencePlan, ...]) -> PlannedUnit:
    required = {"number", "usage_type", "mea"}
    if not required <= set(values):
        raise ApprovalConflict("INCOMPLETE_UNIT")
    number = _required_text(values["number"])
    usage_type = _required_text(values["usage_type"])
    if usage_type not in {"WOHNUNG", "GEWERBE", "STELLPLATZ", "SONSTIGE"}:
        raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")
    mea = values["mea"]
    numerator = getattr(mea, "numerator", None)
    denominator = getattr(mea, "denominator", None)
    if not isinstance(numerator, int) or not isinstance(denominator, int):
        raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")
    result = PlannedUnit(
        id=deterministic_id(item.organization_id, item.id, "unit", candidate.subject_key),
        stable_key=candidate.stable_key,
        subject_key=candidate.subject_key,
        number=number,
        location=_text_or_default(values.get("location")),
        building_part=_text_or_default(values.get("building_part")),
        area=_decimal_or_none(values.get("area")),
        heated_area=_decimal_or_none(values.get("heated_area")),
        usage_type=usage_type,
        mea_numerator=numerator,
        mea_denominator=denominator,
        value_fields={field: value.model_dump(mode="json") for field, value in values.items()},
        evidence=evidence,
    )
    _validate_unit_bounds(result)
    return result


def _build_document_relation(item: DeclarationImport, candidate: DeclarationCandidate, raw_value: object, evidence: tuple[EvidencePlan, ...], docs: Mapping[uuid.UUID, PropertyDocument]) -> PlannedDocumentRelation:
    if not isinstance(raw_value, dict):
        raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")
    try:
        payload = DocumentRelationPayload(
            **raw_value,
            evidence=[EvidencePayload(document_key="approval", pdf_page=1, quote="approval")],
        )
        source = uuid.UUID(candidate.subject_key)
        target = uuid.UUID(payload.target_document_key)
    except (ValidationError, ValueError, TypeError) as exc:
        raise ApprovalConflict("DANGLING_DOCUMENT_REFERENCE") from exc
    if source not in docs or target not in docs or source == target:
        raise ApprovalConflict("DANGLING_DOCUMENT_REFERENCE")
    approved = payload.model_dump(mode="json", exclude={"evidence"})
    return PlannedDocumentRelation(
        deterministic_id(item.organization_id, item.id, "document_relation", candidate.stable_key),
        candidate.stable_key, source, target, payload.relation_type, approved, evidence,
    )


def _build_rule(item: DeclarationImport, property_id: uuid.UUID, candidate: DeclarationCandidate, raw_value: object, evidence: tuple[EvidencePlan, ...]) -> PlannedRule:
    if not isinstance(raw_value, dict):
        raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")
    _validate_rule_evidence_slots(evidence)
    try:
        payload = RulePayload.model_validate({
            **raw_value,
            "subject_key": candidate.subject_key,
            "evidence": [EvidencePayload(document_key="approval", pdf_page=1, quote="approval")],
            "confidence": 1,
        })
    except (ValidationError, ValueError, TypeError) as exc:
        raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED") from exc
    if payload.proposed_status == "NEEDS_CLARIFICATION" or payload.clarification_needed:
        raise ApprovalConflict("RULE_NEEDS_CLARIFICATION")
    allocation_issues = validate_allocation_formulas([payload])
    if any(issue.severity == "BLOCKER" for issue in allocation_issues):
        raise ApprovalConflict(next(issue.code for issue in allocation_issues if issue.severity == "BLOCKER"))
    stable_key = str(uuid.uuid5(APPROVAL_NAMESPACE, f"{property_id}:{item.id}:{candidate.stable_key}"))
    approved = payload.model_dump(mode="json", exclude={"subject_key", "evidence", "confidence"})
    return PlannedRule(
        deterministic_id(item.organization_id, item.id, "rule", candidate.stable_key),
        stable_key,
        candidate.subject_key,
        payload.topic,
        payload.title,
        payload.summary,
        payload.scope.model_dump(mode="json"),
        payload.effect.model_dump(mode="json"),
        payload.proposed_status,
        tuple(payload.supersedes_subject_keys),
        tuple(payload.conflicts_with_subject_keys),
        evidence,
        approved,
    )


def _fact_sources(item: DeclarationImport, candidate: DeclarationCandidate, subject_kind: str, subject_ref: str, field: str, value: dict[str, Any], evidence: Iterable[EvidencePlan]) -> list[FactSourcePlan]:
    fingerprint = value_fingerprint(value)
    entries = list(evidence)
    # A rule has one generic fact citation, while its complete, field-specific
    # evidence remains lossless in PropertyRuleSource rows.
    if subject_kind == "RULE":
        entries = entries[:1]
    return [
        FactSourcePlan(
            deterministic_id(item.organization_id, item.id, "fact_source", f"{candidate.stable_key}:{field}:{fingerprint}:{entry.document_id}:{entry.pdf_page}:{index}"),
            subject_kind, subject_ref, field, value, entry,
        )
        for index, entry in enumerate(entries)
    ]


def _validate_rule_evidence_slots(evidence: Sequence[EvidencePlan]) -> None:
    slots: set[tuple[uuid.UUID, int, str | None]] = set()
    for source in evidence:
        slot = (source.document_id, source.pdf_page, source.field_path)
        if slot in slots:
            raise ApprovalConflict("PROPERTY_RULE_SOURCE_COLLISION")
        slots.add(slot)


def _validate_fact_source_collisions(facts: Sequence[FactSourcePlan]) -> None:
    seen: dict[tuple[str, str, str, str, uuid.UUID, int], FactSourcePlan] = {}
    for source in facts:
        # RULE facts intentionally use the deterministic generic citation from
        # _fact_sources; detailed rule citations are persisted separately.
        if source.subject_kind == "RULE":
            continue
        key = (
            source.subject_kind,
            source.subject_ref,
            source.field_key,
            value_fingerprint(source.approved_value_json),
            source.evidence.document_id,
            source.evidence.pdf_page,
        )
        if key in seen:
            raise ApprovalConflict("APPROVED_FACT_SOURCE_COLLISION")
        seen[key] = source


def _validate_property(values: Mapping[str, Any]) -> None:
    if set(values) != _PROPERTY_FIELDS:
        raise ApprovalConflict("INCOMPLETE_PROPERTY")
    if any(not isinstance(values[field], str) or not values[field].strip() for field in _PROPERTY_FIELDS - {"mea_denominator"}):
        raise ApprovalConflict("INCOMPLETE_PROPERTY")
    if values["management_type"] not in {"MIET", "WEG", "SEV", "MISCH"}:
        raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")
    if not isinstance(values["mea_denominator"], int) or isinstance(values["mea_denominator"], bool) or not 0 < values["mea_denominator"] <= _MAX_MEA_DENOMINATOR:
        raise ApprovalConflict("INVALID_MEA_DENOMINATOR")
    _validate_string_lengths(values, {
        "name": 255, "street": 255, "zip_code": 10, "city": 255, "management_type": 5,
    })


def _validate_existing_property(values: Mapping[str, Any]) -> None:
    if not isinstance(values.get("name"), str) or not values["name"].strip():
        raise ApprovalConflict("INCOMPLETE_PROPERTY")
    if not isinstance(values.get("management_type"), str) or not values["management_type"].strip():
        raise ApprovalConflict("INCOMPLETE_PROPERTY")
    for field in ("street", "zip_code", "city"):
        if not isinstance(values.get(field), str):
            raise ApprovalConflict("INCOMPLETE_PROPERTY")
    if values["management_type"] not in {"MIET", "WEG", "SEV", "MISCH"}:
        raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")
    denominator = values.get("mea_denominator")
    if denominator is not None and (
        not isinstance(denominator, int)
        or isinstance(denominator, bool)
        or not 0 < denominator <= _MAX_MEA_DENOMINATOR
    ):
        raise ApprovalConflict("INVALID_MEA_DENOMINATOR")
    _validate_string_lengths(values, {
        "name": 255, "street": 255, "zip_code": 10, "city": 255, "management_type": 5,
    })


def _validate_units(units: Sequence[PlannedUnit], denominator: int) -> None:
    if not units:
        raise ApprovalConflict("INCOMPLETE_UNIT")
    if len({unit.subject_key for unit in units}) != len(units):
        raise ApprovalConflict("DUPLICATE_UNIT_SUBJECT")
    normalized_numbers = [_normalize_unit_number(unit.number) for unit in units]
    if len(normalized_numbers) != len(set(normalized_numbers)):
        raise ApprovalConflict("DUPLICATE_UNIT_NUMBER")
    if any(unit.mea_denominator != denominator for unit in units):
        raise ApprovalConflict("MIXED_MEA_DENOMINATOR")
    results = validate_mea_totals(
        [(unit.subject_key, unit.mea_numerator, unit.mea_denominator) for unit in units], denominator, "COMPLETE"
    )
    if any(issue.severity == "BLOCKER" for issue in results):
        raise ApprovalConflict(next(issue.code for issue in results if issue.severity == "BLOCKER"))


def _validate_unit_bounds(unit: PlannedUnit) -> None:
    _validate_string_lengths({
        "number": unit.number,
        "location": unit.location,
        "building_part": unit.building_part,
        "usage_type": unit.usage_type,
    }, {"number": 50, "location": 255, "building_part": 255, "usage_type": 12})
    _validate_decimal_column(unit.area)
    _validate_decimal_column(unit.heated_area)


def _validate_string_lengths(values: Mapping[str, Any], limits: Mapping[str, int]) -> None:
    for field, limit in limits.items():
        if field not in values:
            continue
        value = values.get(field)
        if not isinstance(value, str) or len(value) > limit:
            raise ApprovalConflict("APPROVAL_VALUE_TOO_LONG")


def _validate_decimal_column(value: Decimal | None) -> None:
    if value is None:
        return
    if not value.is_finite() or abs(value) > Decimal("99999999.99"):
        raise ApprovalConflict("APPROVAL_DECIMAL_OUT_OF_RANGE")
    try:
        quantized = value.quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ApprovalConflict("APPROVAL_DECIMAL_OUT_OF_RANGE") from exc
    if value != quantized:
        raise ApprovalConflict("APPROVAL_DECIMAL_OUT_OF_RANGE")


def _normalize_unit_number(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip().casefold()


def _normalized_python(value: Any) -> Any:
    kind = getattr(value, "kind", None)
    if kind in {"TEXT", "DECIMAL", "INTEGER", "BOOLEAN", "DATE", "PERCENTAGE"}:
        return getattr(value, "value")
    if kind in {"MEA", "FRACTION"}:
        return {"numerator": value.numerator, "denominator": value.denominator}
    raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")


def _required_text(value: Any) -> str:
    result = _normalized_python(value)
    if not isinstance(result, str) or not result.strip():
        raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")
    return result.strip()


def _required_text_value(value: Any) -> Any:
    """Require a TEXT value and return its canonical trimmed representation."""
    text = _required_text(value)
    try:
        return value.model_copy(update={"value": text})
    except AttributeError as exc:
        raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED") from exc


def _text_or_default(value: Any | None) -> str:
    return _required_text(value) if value is not None else ""


def _decimal_or_none(value: Any | None) -> Decimal | None:
    if value is None:
        return None
    if getattr(value, "kind", None) != "DECIMAL" or getattr(value, "unit", None) != "m²":
        raise ApprovalConflict("APPROVAL_AREA_UNIT_INVALID")
    raw = _normalized_python(value)
    if not isinstance(raw, str):
        raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED")
    try:
        return Decimal(raw)
    except Exception as exc:
        raise ApprovalConflict("APPROVAL_SCHEMA_UNSUPPORTED") from exc


def _has_conflict(value: object) -> bool:
    if isinstance(value, dict):
        return (isinstance(value.get("alternatives"), list) and len(value["alternatives"]) > 1) or any(_has_conflict(item) for item in value.values())
    return isinstance(value, list) and any(_has_conflict(item) for item in value)
