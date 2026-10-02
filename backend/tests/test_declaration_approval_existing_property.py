"""Safety contracts for reviewed amendments to an existing property."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.declaration_candidate import (
    DeclarationCandidate,
    DeclarationCandidateEvidence,
)
from app.models.declaration_import import DeclarationImport
from app.models.property_document import PropertyDocument
from app.models.approved_fact_source import ApprovedFactSource
from app.models.audit_event import AuditEvent
from app.models.declaration_import import DeclarationImportDocument
from app.models.property import Property
from app.models.property_assignment import PropertyAssignment
from app.models.property_document_relation import PropertyDocumentRelation
from app.models.property_legal_reference import PropertyLegalReference
from app.models.property_rule import PropertyRule, PropertyRuleRelation, PropertyRuleSource
from app.models.unit import Unit


def test_exact_mea_helpers_preserve_legal_fraction_and_reject_lossy_conversion() -> None:
    """A Talstraße share may only be rewritten when its fraction stays exact."""
    from app.declarations.approval import (
        ApprovalConflict,
        common_denominator,
        convert_mea_exact,
    )

    assert convert_mea_exact(95_464, 1_000_000, 2_000_000) == 190_928
    assert common_denominator([6, 7, 1_000_000]) == 21_000_000
    with pytest.raises(ApprovalConflict, match="MEA_NOT_EXACTLY_CONVERTIBLE"):
        convert_mea_exact(1, 6, 7)


def test_talstrasse_amendment_plans_exact_rule_supersession_and_rejects_stale_state() -> None:
    """The 1/6→1/7 elevator rule identifies one locked rule, never its title."""
    from app.declarations.approval import ApprovalConflict, build_existing_approval_plan

    org_id, import_id, user_id, property_id = (uuid.uuid4() for _ in range(4))
    item = DeclarationImport(
        id=import_id, organization_id=org_id, mode="EXISTING_PROPERTY",
        target_property_id=property_id, initiated_by=user_id, max_cost_usd=Decimal("1"),
    )
    property_row = SimpleNamespace(
        id=property_id, organization_id=org_id, name="WEG Talstraße 111",
        street="Talstraße 111", zip_code="40217", city="Düsseldorf",
        management_type="WEG", mea_denominator=1_000_000,
        mea_scale_status="CONFIRMED",
    )
    units = [
        SimpleNamespace(id=uuid.uuid4(), number="1", location="", building_part="", area=None, heated_area=None, usage_type="WOHNUNG", mea=Decimal("95464")),
        SimpleNamespace(id=uuid.uuid4(), number="2", location="", building_part="", area=None, heated_area=None, usage_type="WOHNUNG", mea=Decimal("95464")),
    ]
    old_rule = SimpleNamespace(id=uuid.uuid4(), stable_key="elevator-allocation-v1")
    document = PropertyDocument(
        id=uuid.uuid4(), organization_id=org_id, doc_type="AMENDMENT", title="Nachtrag",
        original_filename="nachtrag.pdf", mime_type="application/pdf", content_byte_size=1,
        content_sha256="a" * 64, storage_uri="test/amendment.pdf", storage_backend="LOCAL",
        page_count=2, uploaded_by=user_id,
    )

    def candidate(kind: str, subject: str, field: str | None, previous: dict | None, proposed: dict, *, operation: str = "UPDATE") -> DeclarationCandidate:
        return DeclarationCandidate(
            id=uuid.uuid4(), organization_id=org_id, import_id=import_id,
            stable_key=f"{kind}:{subject}:{field or 'all'}:{uuid.uuid4()}", candidate_type=kind,
            subject_key=subject, field_key=field, operation=operation,
            previous_value_json=previous, proposed_value_json=proposed,
            confidence=Decimal("0.99"), risk_class="CRITICAL", requires_individual_review=True,
            review_status="ACCEPTED", reviewed_by=user_id, reviewed_at=datetime.now(timezone.utc),
        )

    candidates = [
        candidate("PROPERTY_FIELD", "property:talstrasse", "street", {"kind": "TEXT", "value": "Talstraße 111"}, {"kind": "TEXT", "value": "Talstraße 112"}),
        candidate("UNIT_FIELD", "unit:1", "number", {"kind": "TEXT", "value": "1"}, {"kind": "TEXT", "value": "1"}, operation="KEEP"),
        candidate("UNIT_FIELD", "unit:1", "mea", {"kind": "MEA", "numerator": 95_464, "denominator": 1_000_000}, {"kind": "MEA", "numerator": 47_732, "denominator": 1_000_000}),
        candidate("UNIT_FIELD", "unit:2", "number", {"kind": "TEXT", "value": "2"}, {"kind": "TEXT", "value": "2"}, operation="KEEP"),
        candidate("UNIT_FIELD", "unit:2", "mea", {"kind": "MEA", "numerator": 95_464, "denominator": 1_000_000}, {"kind": "MEA", "numerator": 47_732, "denominator": 1_000_000}),
        candidate("UNIT_CREATE", "unit:3", None, None, {"number": {"kind": "TEXT", "value": "3"}, "usage_type": {"kind": "TEXT", "value": "WOHNUNG"}, "mea": {"kind": "MEA", "numerator": 47_732, "denominator": 1_000_000}}, operation="CREATE"),
        candidate("UNIT_CREATE", "unit:4", None, None, {"number": {"kind": "TEXT", "value": "4"}, "usage_type": {"kind": "TEXT", "value": "WOHNUNG"}, "mea": {"kind": "MEA", "numerator": 47_732, "denominator": 1_000_000}}, operation="CREATE"),
        candidate("RULE", "rule:elevator-v2", None, None, {"title": "Aufzug", "topic": "elevator", "summary": "Kosten zu einem Siebtel.", "scope": {"all_units": True}, "effect": {"allocation_formula": {"declared_complete": False, "parts": [{"label": "Aufzug", "basis": "MEA", "share": {"kind": "FRACTION", "numerator": 1, "denominator": 7}}]}}, "proposed_status": "EFFECTIVE", "supersedes_subject_keys": ["elevator-allocation-v1"], "conflicts_with_subject_keys": [], "clarification_needed": False}, operation="CREATE"),
    ]
    evidence = {
        row.id: [DeclarationCandidateEvidence(id=uuid.uuid4(), organization_id=org_id, candidate_id=row.id, document_id=document.id, pdf_page=1, quote="Nachtrag § 8")]
        for row in candidates
    }
    plan = build_existing_approval_plan(item, candidates, evidence, [], [document], property_row, units, [old_rule])
    assert plan.summary == {"properties_created": 0, "properties_updated": 1, "units_created": 2, "units_updated": 2, "rules_created": 1, "documents_bound": 1}
    assert plan.rule_supersedes == ((plan.rules_created[0].id, old_rule.id),)

    property_row.street = "Talstraße 113"
    with pytest.raises(ApprovalConflict, match="TARGET_CHANGED"):
        build_existing_approval_plan(item, candidates, evidence, [], [document], property_row, units, [old_rule])

    property_row.street = "Talstraße 111"
    rule_candidate = next(row for row in candidates if row.candidate_type == "RULE")
    rule_candidate.proposed_value_json["supersedes_subject_keys"] = ["foreign-or-unknown-rule"]
    with pytest.raises(ApprovalConflict, match="DANGLING_RULE_REFERENCE"):
        build_existing_approval_plan(item, candidates, evidence, [], [document], property_row, units, [old_rule])
    rule_candidate.proposed_value_json["supersedes_subject_keys"] = [old_rule.stable_key]

    legacy_confirmation = DeclarationCandidate(
        id=uuid.uuid4(), organization_id=org_id, import_id=import_id,
        stable_key="property:legacy-denominator", candidate_type="PROPERTY_FIELD",
        subject_key="property:talstrasse", field_key="mea_denominator", operation="UPDATE",
        previous_value_json=None, proposed_value_json={"kind": "INTEGER", "value": 1_000_000},
        confidence=Decimal("0.99"), risk_class="CRITICAL", requires_individual_review=True,
        review_status="ACCEPTED", reviewed_by=user_id, reviewed_at=datetime.now(timezone.utc),
    )
    property_row.mea_denominator = None
    property_row.mea_scale_status = "LEGACY_UNCONFIRMED"
    with pytest.raises(ApprovalConflict, match="MEA_CANONICAL_SCALE_UNCONFIRMED"):
        build_existing_approval_plan(item, candidates, evidence, [], [document], property_row, units, [old_rule])
    confirmation_evidence = {
        legacy_confirmation.id: [DeclarationCandidateEvidence(
            id=uuid.uuid4(), organization_id=org_id, candidate_id=legacy_confirmation.id,
            document_id=document.id, pdf_page=1, quote="Bestätigung des Nennerwerts",
        )]
    }
    confirmation = build_existing_approval_plan(
        item, [legacy_confirmation], confirmation_evidence, [], [document], property_row, units, []
    )
    assert confirmation.mea_scale_confirmed is True
    assert confirmation.mea_denominator == 1_000_000


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("legacy_without_confirmation", "MEA_CANONICAL_SCALE_UNCONFIRMED"),
        ("partial_mapping", "MIXED_MEA_SCALE"),
        ("mixed_denominators", "MIXED_MEA_SCALE"),
        ("non_proportional", "MIXED_MEA_SCALE"),
        ("over_limit", "INVALID_MEA_DENOMINATOR"),
        ("duplicate_final_number", "DUPLICATE_UNIT_NUMBER"),
        ("delete", "DELETE_NOT_ALLOWED"),
    ],
)
def test_existing_property_prewrite_matrix_rejects_unsafe_graphs(case: str, expected: str) -> None:
    """Every unsafe amendment fails in the pure locked-plan boundary before writes."""
    from app.declarations.approval import ApprovalConflict, build_existing_approval_plan

    org_id, import_id, user_id, property_id = (uuid.uuid4() for _ in range(4))
    item = DeclarationImport(id=import_id, organization_id=org_id, mode="EXISTING_PROPERTY", target_property_id=property_id, initiated_by=user_id, max_cost_usd=Decimal("1"))
    property_row = SimpleNamespace(id=property_id, organization_id=org_id, name="Talstraße", street="Talstraße 1", zip_code="40217", city="Düsseldorf", management_type="WEG", mea_denominator=1_000_000, mea_scale_status="CONFIRMED")
    units = [
        SimpleNamespace(id=uuid.uuid4(), number="1", location="", building_part="", area=None, heated_area=None, usage_type="WOHNUNG", mea=Decimal("100000")),
        SimpleNamespace(id=uuid.uuid4(), number="2", location="", building_part="", area=None, heated_area=None, usage_type="WOHNUNG", mea=Decimal("100000")),
    ]
    document = PropertyDocument(id=uuid.uuid4(), organization_id=org_id, doc_type="AMENDMENT", title="Nachtrag", original_filename="nachtrag.pdf", mime_type="application/pdf", content_byte_size=1, content_sha256="b" * 64, storage_uri="test/matrix.pdf", storage_backend="LOCAL", page_count=1, uploaded_by=user_id)

    def candidate(subject: str, field: str | None, value: dict, previous: dict | None, *, kind: str = "UNIT_FIELD", operation: str = "UPDATE") -> DeclarationCandidate:
        return DeclarationCandidate(id=uuid.uuid4(), organization_id=org_id, import_id=import_id, stable_key=f"{kind}:{subject}:{field}:{uuid.uuid4()}", candidate_type=kind, subject_key=subject, field_key=field, operation=operation, previous_value_json=previous, proposed_value_json=value, confidence=Decimal("0.99"), risk_class="CRITICAL", requires_individual_review=True, review_status="ACCEPTED", reviewed_by=user_id, reviewed_at=datetime.now(timezone.utc))

    keep_numbers = [
        candidate(f"unit:{number}", "number", _fact("TEXT", str(number)), _fact("TEXT", str(number)), operation="KEEP")
        for number in (1, 2)
    ]
    mea_updates = [
        candidate(f"unit:{number}", "mea", {"kind": "MEA", "numerator": 200000, "denominator": 2000000}, {"kind": "MEA", "numerator": 100000, "denominator": 1000000})
        for number in (1, 2)
    ]
    rescale = candidate("property:tal", "mea_denominator", {"kind": "INTEGER", "value": 2000000}, {"kind": "INTEGER", "value": 1000000}, kind="MEA_RESCALE")
    candidates = [*keep_numbers, *mea_updates, rescale]
    if case == "legacy_without_confirmation":
        property_row.mea_denominator = None
        property_row.mea_scale_status = "LEGACY_UNCONFIRMED"
        candidates = [*keep_numbers, mea_updates[0]]
    elif case == "partial_mapping":
        candidates = [*keep_numbers, mea_updates[0], rescale]
    elif case == "mixed_denominators":
        mea_updates[1].proposed_value_json = {"kind": "MEA", "numerator": 300000, "denominator": 3000000}
    elif case == "non_proportional":
        mea_updates[1].proposed_value_json = {"kind": "MEA", "numerator": 199999, "denominator": 2000000}
    elif case == "over_limit":
        rescale.proposed_value_json = {"kind": "INTEGER", "value": 1_000_000_000_001}
    elif case == "duplicate_final_number":
        candidates = [candidate("unit:1", "number", _fact("TEXT", "2"), _fact("TEXT", "1"))]
    elif case == "delete":
        candidates = [candidate("unit:1", "number", _fact("TEXT", "1"), _fact("TEXT", "1"), operation="DELETE")]
    evidence = {
        row.id: [DeclarationCandidateEvidence(id=uuid.uuid4(), organization_id=org_id, candidate_id=row.id, document_id=document.id, pdf_page=1, quote="Nachtrag")]
        for row in candidates
    }
    with pytest.raises(ApprovalConflict, match=expected):
        build_existing_approval_plan(item, candidates, evidence, [], [document], property_row, units, [])


def test_existing_rescale_rejects_non_exact_fraction_conversion() -> None:
    """A full mapping still fails when changing the denominator would round a share."""
    from app.declarations.approval import ApprovalConflict, build_existing_approval_plan

    org_id, import_id, user_id, property_id = (uuid.uuid4() for _ in range(4))
    item = DeclarationImport(id=import_id, organization_id=org_id, mode="EXISTING_PROPERTY", target_property_id=property_id, initiated_by=user_id, max_cost_usd=Decimal("1"))
    property_row = SimpleNamespace(id=property_id, organization_id=org_id, name="Talstraße", street="Talstraße 1", zip_code="40217", city="Düsseldorf", management_type="WEG", mea_denominator=6, mea_scale_status="CONFIRMED")
    unit = SimpleNamespace(id=uuid.uuid4(), number="1", location="", building_part="", area=None, heated_area=None, usage_type="WOHNUNG", mea=Decimal("1"))
    document = PropertyDocument(id=uuid.uuid4(), organization_id=org_id, doc_type="AMENDMENT", title="Nachtrag", original_filename="nachtrag.pdf", mime_type="application/pdf", content_byte_size=1, content_sha256="c" * 64, storage_uri="test/non-exact.pdf", storage_backend="LOCAL", page_count=1, uploaded_by=user_id)
    def row(kind: str, field: str | None, value: dict, previous: dict | None, operation: str = "UPDATE") -> DeclarationCandidate:
        return DeclarationCandidate(id=uuid.uuid4(), organization_id=org_id, import_id=import_id, stable_key=f"{kind}:{field}:{uuid.uuid4()}", candidate_type=kind, subject_key="unit:1" if kind == "UNIT_FIELD" else "property:tal", field_key=field, operation=operation, previous_value_json=previous, proposed_value_json=value, confidence=Decimal("0.99"), risk_class="CRITICAL", requires_individual_review=True, review_status="ACCEPTED", reviewed_by=user_id, reviewed_at=datetime.now(timezone.utc))
    candidates = [
        row("UNIT_FIELD", "number", _fact("TEXT", "1"), _fact("TEXT", "1"), "KEEP"),
        row("UNIT_FIELD", "mea", {"kind": "MEA", "numerator": 1, "denominator": 7}, {"kind": "MEA", "numerator": 1, "denominator": 6}),
        row("MEA_RESCALE", "mea_denominator", {"kind": "INTEGER", "value": 7}, {"kind": "INTEGER", "value": 6}),
    ]
    evidence = {candidate.id: [DeclarationCandidateEvidence(id=uuid.uuid4(), organization_id=org_id, candidate_id=candidate.id, document_id=document.id, pdf_page=1, quote="Nachtrag")] for candidate in candidates}
    with pytest.raises(ApprovalConflict, match="MEA_NOT_EXACTLY_CONVERTIBLE"):
        build_existing_approval_plan(item, candidates, evidence, [], [document], property_row, [unit], [])


@pytest.mark.parametrize(
    ("candidate_type", "operation"),
    [
        ("PROPERTY_FIELD", "CREATE"), ("UNIT_FIELD", "CREATE"),
        ("MEA_RESCALE", "CREATE"), ("UNIT_CREATE", "UPDATE"),
        ("LEGAL_REFERENCE", "UPDATE"), ("ASSIGNMENT", "UPDATE"),
        ("DOCUMENT_RELATION", "UPDATE"), ("RULE", "UPDATE"),
    ],
)
def test_existing_operation_type_matrix_rejects_impossible_combinations_prewrite(
    candidate_type: str, operation: str,
) -> None:
    """Existing amendments accept only the explicit safe operation/type matrix."""
    from app.declarations.approval import ApprovalConflict, build_existing_approval_plan

    org_id, import_id, user_id, property_id = (uuid.uuid4() for _ in range(4))
    item = DeclarationImport(id=import_id, organization_id=org_id, mode="EXISTING_PROPERTY", target_property_id=property_id, initiated_by=user_id, max_cost_usd=Decimal("1"))
    property_row = SimpleNamespace(id=property_id, organization_id=org_id, name="Talstraße", street="", zip_code="", city="", management_type="WEG", mea_denominator=1_000_000, mea_scale_status="CONFIRMED")
    unit = SimpleNamespace(id=uuid.uuid4(), number="1", location="", building_part="", area=None, heated_area=None, usage_type="WOHNUNG", mea=Decimal("1"))
    document = PropertyDocument(id=uuid.uuid4(), organization_id=org_id, doc_type="AMENDMENT", title="Nachtrag", original_filename="matrix.pdf", mime_type="application/pdf", content_byte_size=1, content_sha256="e" * 64, storage_uri="test/matrix-operation.pdf", storage_backend="LOCAL", page_count=1, uploaded_by=user_id)
    candidate = DeclarationCandidate(
        id=uuid.uuid4(), organization_id=org_id, import_id=import_id,
        stable_key=f"{candidate_type}:{operation}", candidate_type=candidate_type,
        subject_key="unit:1", field_key="number", operation=operation,
        previous_value_json=None, proposed_value_json=_fact("TEXT", "1"),
        confidence=Decimal("0.99"), risk_class="CRITICAL", requires_individual_review=True,
        review_status="ACCEPTED", reviewed_by=user_id, reviewed_at=datetime.now(timezone.utc),
    )
    with pytest.raises(ApprovalConflict, match="EXISTING_MUTATION_UNSUPPORTED"):
        build_existing_approval_plan(item, [candidate], {}, [], [document], property_row, [unit], [])


def test_existing_keep_subjects_resolve_for_assignment_and_rule_scope() -> None:
    """A reference can safely use a locked unit even if all of its fields are KEEP."""
    from app.declarations.approval import ApprovalConflict, build_existing_approval_plan

    org_id, import_id, user_id, property_id = (uuid.uuid4() for _ in range(4))
    item = DeclarationImport(id=import_id, organization_id=org_id, mode="EXISTING_PROPERTY", target_property_id=property_id, initiated_by=user_id, max_cost_usd=Decimal("1"))
    property_row = SimpleNamespace(id=property_id, organization_id=org_id, name="Talstraße", street="", zip_code="", city="", management_type="WEG", mea_denominator=1_000_000, mea_scale_status="CONFIRMED")
    unit = SimpleNamespace(id=uuid.uuid4(), number="1", location="", building_part="", area=None, heated_area=None, usage_type="WOHNUNG", mea=Decimal("1"))
    document = PropertyDocument(id=uuid.uuid4(), organization_id=org_id, doc_type="AMENDMENT", title="Nachtrag", original_filename="keep.pdf", mime_type="application/pdf", content_byte_size=1, content_sha256="f" * 64, storage_uri="test/keep.pdf", storage_backend="LOCAL", page_count=1, uploaded_by=user_id)
    keep = _reviewed_candidate(item, stable_key="unit:1:number", candidate_type="UNIT_FIELD", subject_key="unit:1", field_key="number", previous=_fact("TEXT", "1"), value=_fact("TEXT", "1"), operation="KEEP")
    assignment = _reviewed_candidate(item, stable_key="assignment:keep", candidate_type="ASSIGNMENT", subject_key="assignment:keep", operation="CREATE", value={"assignment_type": _fact("TEXT", "SPECIAL_USE"), "designation": _fact("TEXT", "Stellplatz 1"), "unit_subject_key": _fact("TEXT", "unit:1")})
    rule = _reviewed_candidate(item, stable_key="rule:keep", candidate_type="RULE", subject_key="rule:keep", operation="CREATE", value={"title": "Aufzug", "topic": "elevator", "summary": "Nur Einheit 1.", "scope": {"unit_subject_keys": ["unit:1"]}, "effect": {"obligation": "Kosten tragen"}, "proposed_status": "EFFECTIVE", "supersedes_subject_keys": [], "conflicts_with_subject_keys": [], "clarification_needed": False})
    evidence = {
        candidate.id: [DeclarationCandidateEvidence(organization_id=org_id, candidate_id=candidate.id, document_id=document.id, pdf_page=1, quote="Nachtrag")]
        for candidate in (assignment, rule)
    }
    plan = build_existing_approval_plan(item, [keep, assignment, rule], evidence, [], [document], property_row, [unit], [])
    assert plan.unit_subject_ids == {"unit:1": unit.id}
    assert plan.assignments[0].unit_subject_key == "unit:1"
    assert plan.rules_created[0].scope_json["unit_subject_keys"] == ["unit:1"]
    with pytest.raises(ApprovalConflict, match="DUPLICATE_ASSIGNMENT_STABLE_KEY"):
        build_existing_approval_plan(
            item, [keep, assignment], {assignment.id: evidence[assignment.id]}, [], [document],
            property_row, [unit], [], [SimpleNamespace(stable_key="assignment:keep")],
        )

    related = PropertyDocument(id=uuid.uuid4(), organization_id=org_id, doc_type="DECLARATION", title="Stamm", original_filename="stamm.pdf", mime_type="application/pdf", content_byte_size=1, content_sha256="a" * 64, storage_uri="test/stamm.pdf", storage_backend="LOCAL", page_count=1, uploaded_by=user_id)
    relation = _reviewed_candidate(item, stable_key="relation:duplicate", candidate_type="DOCUMENT_RELATION", subject_key=str(document.id), operation="CREATE", value={"relation_type": "AMENDS", "target_document_key": str(related.id)})
    relation_evidence = {relation.id: [DeclarationCandidateEvidence(organization_id=org_id, candidate_id=relation.id, document_id=document.id, pdf_page=1, quote="Nachtrag")]}
    with pytest.raises(ApprovalConflict, match="DUPLICATE_DOCUMENT_RELATION"):
        build_existing_approval_plan(
            item, [keep, relation], relation_evidence, [], [document, related], property_row,
            [unit], [], (), [SimpleNamespace(source_document_id=document.id, target_document_id=related.id, relation_type="AMENDS")],
        )


def test_existing_plan_rejects_duplicate_new_rule_subject_before_writes() -> None:
    """Two candidates cannot collapse the planned rule/source provenance key."""
    from app.declarations.approval import ApprovalConflict, build_existing_approval_plan

    org_id, import_id, user_id, property_id = (uuid.uuid4() for _ in range(4))
    item = DeclarationImport(id=import_id, organization_id=org_id, mode="EXISTING_PROPERTY", target_property_id=property_id, initiated_by=user_id, max_cost_usd=Decimal("1"))
    property_row = SimpleNamespace(id=property_id, organization_id=org_id, name="Talstraße", street="", zip_code="", city="", management_type="WEG", mea_denominator=1_000_000, mea_scale_status="CONFIRMED")
    document = PropertyDocument(id=uuid.uuid4(), organization_id=org_id, doc_type="AMENDMENT", title="Nachtrag", original_filename="rules.pdf", mime_type="application/pdf", content_byte_size=1, content_sha256="1" * 64, storage_uri="test/rules.pdf", storage_backend="LOCAL", page_count=1, uploaded_by=user_id)
    value = {
        "title": "Heizkosten", "topic": "heating", "summary": "Verteilung nach MEA.",
        "scope": {"all_units": True}, "effect": {"obligation": "Kosten tragen"},
        "proposed_status": "EFFECTIVE", "supersedes_subject_keys": [],
        "conflicts_with_subject_keys": [], "clarification_needed": False,
    }
    first = _reviewed_candidate(item, stable_key="rule:heating:first", candidate_type="RULE", subject_key="rule:heating", operation="CREATE", value=value)
    second = _reviewed_candidate(item, stable_key="rule:heating:second", candidate_type="RULE", subject_key="rule:heating", operation="CREATE", value=value)
    evidence = {
        candidate.id: [DeclarationCandidateEvidence(organization_id=org_id, candidate_id=candidate.id, document_id=document.id, pdf_page=1, quote="§ 12")]
        for candidate in (first, second)
    }

    with pytest.raises(ApprovalConflict, match="DUPLICATE_RULE_SUBJECT"):
        build_existing_approval_plan(item, [first, second], evidence, [], [document], property_row, [], [])


def test_existing_rescale_rejects_non_integral_canonical_mea_before_conversion() -> None:
    """A malformed stored numerator must never be silently truncated by int()."""
    from app.declarations.approval import ApprovalConflict, build_existing_approval_plan

    org_id, import_id, user_id, property_id = (uuid.uuid4() for _ in range(4))
    item = DeclarationImport(id=import_id, organization_id=org_id, mode="EXISTING_PROPERTY", target_property_id=property_id, initiated_by=user_id, max_cost_usd=Decimal("1"))
    property_row = SimpleNamespace(id=property_id, organization_id=org_id, name="Talstraße", street="", zip_code="", city="", management_type="WEG", mea_denominator=6, mea_scale_status="CONFIRMED")
    unit = SimpleNamespace(id=uuid.uuid4(), number="1", location="", building_part="", area=None, heated_area=None, usage_type="WOHNUNG", mea=Decimal("1.5"))
    document = PropertyDocument(id=uuid.uuid4(), organization_id=org_id, doc_type="AMENDMENT", title="Nachtrag", original_filename="rescale.pdf", mime_type="application/pdf", content_byte_size=1, content_sha256="2" * 64, storage_uri="test/rescale.pdf", storage_backend="LOCAL", page_count=1, uploaded_by=user_id)
    rescale = _reviewed_candidate(item, stable_key="property:rescale", candidate_type="MEA_RESCALE", subject_key="property:tal", field_key="mea_denominator", previous=_fact("INTEGER", 6), operation="UPDATE", value=_fact("INTEGER", 7))
    evidence = {rescale.id: [DeclarationCandidateEvidence(organization_id=org_id, candidate_id=rescale.id, document_id=document.id, pdf_page=1, quote="§ 3")]}

    with pytest.raises(ApprovalConflict, match="INVALID_MEA"):
        build_existing_approval_plan(item, [rescale], evidence, [], [document], property_row, [unit], [])


def _fact(kind: str, value: object, **extra: object) -> dict[str, object]:
    return {"kind": kind, "value": value, **extra}


def _reviewed_candidate(
    item: DeclarationImport,
    *,
    stable_key: str,
    candidate_type: str,
    subject_key: str,
    value: dict[str, object],
    field_key: str | None = None,
    previous: dict[str, object] | None = None,
    operation: str = "UPDATE",
) -> DeclarationCandidate:
    return DeclarationCandidate(
        organization_id=item.organization_id,
        import_id=item.id,
        stable_key=stable_key,
        candidate_type=candidate_type,
        subject_key=subject_key,
        field_key=field_key,
        operation=operation,
        previous_value_json=previous,
        proposed_value_json=value,
        confidence=Decimal("0.99"),
        risk_class="CRITICAL",
        requires_individual_review=True,
        review_status="ACCEPTED",
        reviewed_by=item.initiated_by,
        reviewed_at=datetime.now(timezone.utc),
    )


@pytest_asyncio.fixture
async def reviewed_existing_property_import(seed_data: dict):
    """A synthetic Talstraße amendment with an already-provenanced target graph."""
    from app.core.config import settings
    from app.declarations.approval import value_fingerprint

    engine = create_async_engine(settings.database_url)
    item = property_row = main = amendment = None
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await session.execute(text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(seed_data["org_a_id"])})
            property_row = Property(
                id=uuid.uuid4(), organization_id=seed_data["org_a_id"], name="WEG Talstraße 111",
                street="Talstraße 111", zip_code="40217", city="Düsseldorf",
                management_type="WEG", mea_denominator=1_000_000,
                mea_scale_status="CONFIRMED",
            )
            session.add(property_row)
            await session.flush()
            item = DeclarationImport(
                id=uuid.uuid4(), organization_id=seed_data["org_a_id"], mode="EXISTING_PROPERTY",
                target_property_id=property_row.id, status="NEEDS_REVIEW", draft_revision=5,
                initiated_by=seed_data["user_a_clerk_id"], max_cost_usd=Decimal("10"),
            )
            main = PropertyDocument(
                organization_id=item.organization_id, property_id=property_row.id,
                doc_type="DECLARATION", title="Teilungserklärung", original_filename="talstrasse.pdf",
                mime_type="application/pdf", content_byte_size=10, content_sha256="c" * 64,
                storage_uri="test/talstrasse.pdf", storage_backend="LOCAL", page_count=3,
                uploaded_by=item.initiated_by,
            )
            amendment = PropertyDocument(
                organization_id=item.organization_id, doc_type="AMENDMENT", title="Nachtrag",
                original_filename="nachtrag.pdf", mime_type="application/pdf", content_byte_size=10,
                content_sha256="d" * 64, storage_uri="test/talstrasse-nachtrag.pdf",
                storage_backend="LOCAL", page_count=2, uploaded_by=item.initiated_by,
            )
            units = [
                Unit(organization_id=item.organization_id, property_id=property_row.id, number=str(number),
                     location="", building_part="", usage_type="WOHNUNG", mea=Decimal("95464"))
                for number in (1, 2)
            ]
            old_rule = PropertyRule(
                organization_id=item.organization_id, property_id=property_row.id,
                stable_key="elevator-allocation-v1", topic="elevator", title="Aufzug",
                summary="Kosten zu einem Sechstel.", scope_json={"all_units": True},
                effect_json={"share": "1/6"}, source_import_id=item.id,
                approved_by=item.initiated_by, approved_at=datetime.now(timezone.utc), search_text="Aufzug",
            )
            session.add_all([property_row, item, main, amendment, *units, old_rule])
            await session.flush()
            session.add_all([
                DeclarationImportDocument(organization_id=item.organization_id, import_id=item.id, document_id=main.id, sequence_no=1, document_role="DECLARATION"),
                DeclarationImportDocument(organization_id=item.organization_id, import_id=item.id, document_id=amendment.id, sequence_no=2, document_role="AMENDMENT"),
            ])
            session.add(PropertyRuleSource(
                organization_id=item.organization_id, property_id=property_row.id,
                property_rule_id=old_rule.id, property_document_id=main.id,
                pdf_page=1, quote="Altbestand § 8", field_path="rule",
            ))
            candidates = [
                _reviewed_candidate(item, stable_key="property:street", candidate_type="PROPERTY_FIELD", subject_key="property:tal", field_key="street", previous=_fact("TEXT", "Talstraße 111"), value=_fact("TEXT", "Talstraße 112")),
                _reviewed_candidate(item, stable_key="unit:1:number", candidate_type="UNIT_FIELD", subject_key="unit:1", field_key="number", previous=_fact("TEXT", "1"), value=_fact("TEXT", "1"), operation="KEEP"),
                _reviewed_candidate(item, stable_key="unit:1:mea", candidate_type="UNIT_FIELD", subject_key="unit:1", field_key="mea", previous={"kind": "MEA", "numerator": 95464, "denominator": 1000000}, value={"kind": "MEA", "numerator": 47732, "denominator": 1000000}),
                _reviewed_candidate(item, stable_key="unit:2:number", candidate_type="UNIT_FIELD", subject_key="unit:2", field_key="number", previous=_fact("TEXT", "2"), value=_fact("TEXT", "2"), operation="KEEP"),
                _reviewed_candidate(item, stable_key="unit:2:mea", candidate_type="UNIT_FIELD", subject_key="unit:2", field_key="mea", previous={"kind": "MEA", "numerator": 95464, "denominator": 1000000}, value={"kind": "MEA", "numerator": 47732, "denominator": 1000000}),
                _reviewed_candidate(item, stable_key="unit:3", candidate_type="UNIT_CREATE", subject_key="unit:3", operation="CREATE", value={"number": _fact("TEXT", "3"), "usage_type": _fact("TEXT", "WOHNUNG"), "mea": {"kind": "MEA", "numerator": 47732, "denominator": 1000000}}),
                _reviewed_candidate(item, stable_key="unit:4", candidate_type="UNIT_CREATE", subject_key="unit:4", operation="CREATE", value={"number": _fact("TEXT", "4"), "usage_type": _fact("TEXT", "WOHNUNG"), "mea": {"kind": "MEA", "numerator": 47732, "denominator": 1000000}}),
                _reviewed_candidate(item, stable_key="legal:tal", candidate_type="LEGAL_REFERENCE", subject_key="legal_reference:tal", operation="CREATE", value={"reference_type": _fact("TEXT", "LAND_REGISTER"), "district": _fact("TEXT", "Düsseldorf"), "sheet": _fact("TEXT", "1234")}),
                _reviewed_candidate(item, stable_key="assignment:parking", candidate_type="ASSIGNMENT", subject_key="assignment:parking", operation="CREATE", value={"assignment_type": _fact("TEXT", "SPECIAL_USE"), "designation": _fact("TEXT", "Stellplatz 1"), "unit_subject_key": _fact("TEXT", "unit:1")}),
                _reviewed_candidate(item, stable_key="relation:amends", candidate_type="DOCUMENT_RELATION", subject_key=str(amendment.id), operation="CREATE", value={"relation_type": "AMENDS", "target_document_key": str(main.id)}),
                _reviewed_candidate(item, stable_key="rule:elevator-v2", candidate_type="RULE", subject_key="rule:elevator-v2", operation="CREATE", value={"title": "Aufzug", "topic": "elevator", "summary": "Kosten zu einem Siebtel.", "scope": {"all_units": True}, "effect": {"allocation_formula": {"declared_complete": False, "parts": [{"label": "Aufzug", "basis": "MEA", "share": {"kind": "FRACTION", "numerator": 1, "denominator": 7}}]}}, "proposed_status": "EFFECTIVE", "supersedes_subject_keys": [old_rule.stable_key], "conflicts_with_subject_keys": [], "clarification_needed": False}),
            ]
            session.add_all(candidates)
            await session.flush()
            for candidate in candidates:
                session.add(DeclarationCandidateEvidence(
                    organization_id=item.organization_id, candidate_id=candidate.id,
                    document_id=amendment.id, pdf_page=1, quote="Nachtrag § 8",
                    field_path=candidate.field_key,
                ))
            session.add(ApprovedFactSource(
                organization_id=item.organization_id, property_id=property_row.id,
                subject_kind="PROPERTY", subject_id=property_row.id, field_key="street",
                approved_value_json=_fact("TEXT", "Talstraße 111"),
                value_fingerprint="0" * 64, valid_from=datetime.now(timezone.utc),
                property_document_id=main.id, pdf_page=1, quote="Altbestand", source_import_id=item.id,
            ))
            session.add(ApprovedFactSource(
                organization_id=item.organization_id, property_id=property_row.id,
                subject_kind="PROPERTY", subject_id=property_row.id, field_key="street",
                approved_value_json=_fact("TEXT", "Talstraße 111"),
                value_fingerprint=value_fingerprint(_fact("TEXT", "Talstraße 111")),
                valid_from=datetime.now(timezone.utc), property_document_id=main.id,
                pdf_page=2, quote="Passender Altbestand", source_import_id=item.id,
            ))
            await session.commit()
            yield SimpleNamespace(item=item, property=property_row, main=main, amendment=amendment, units=units, old_rule=old_rule)
    finally:
        await engine.dispose()
        if item is not None:
            cleanup_engine = create_async_engine(settings.database_url_migrator)
            try:
                async with async_sessionmaker(cleanup_engine, class_=AsyncSession)() as cleanup:
                    async with cleanup.begin():
                        for statement in (
                            "DELETE FROM approved_fact_source WHERE source_import_id = :id",
                            "DELETE FROM property_rule_source WHERE property_id = :property",
                            "DELETE FROM property_rule_source WHERE property_rule_id IN (SELECT id FROM property_rule WHERE source_import_id = :id)",
                            "DELETE FROM property_rule_relation WHERE source_import_id = :id",
                            "DELETE FROM property_rule WHERE source_import_id = :id",
                            "DELETE FROM property_document_relation WHERE source_import_id = :id",
                            "DELETE FROM property_assignment WHERE source_import_id = :id",
                            "DELETE FROM property_legal_reference WHERE source_import_id = :id",
                            "DELETE FROM audit_event WHERE entity_id = :id",
                            "DELETE FROM declaration_candidate_evidence WHERE candidate_id IN (SELECT id FROM declaration_candidate WHERE import_id = :id)",
                            "DELETE FROM declaration_candidate WHERE import_id = :id",
                            "DELETE FROM declaration_import_document WHERE import_id = :id",
                            "DELETE FROM declaration_import WHERE id = :id",
                        ):
                            await cleanup.execute(text(statement), {"id": item.id, "property": property_row.id})
                        await cleanup.execute(text("DELETE FROM property_document WHERE id IN (:main, :amendment)"), {"main": main.id, "amendment": amendment.id})
                        await cleanup.execute(text("DELETE FROM unit WHERE property_id = :property"), {"property": property_row.id})
                        await cleanup.execute(text("DELETE FROM property WHERE id = :property"), {"property": property_row.id})
            finally:
                await cleanup_engine.dispose()


@pytest.mark.asyncio
async def test_existing_property_approval_is_atomic_traceable_and_idempotent(
    client_a_clerk, reviewed_existing_property_import, seed_data
) -> None:
    """The reviewed Talstraße split persists history and replays only its own key."""
    item = reviewed_existing_property_import.item
    response = await client_a_clerk.post(
        f"/api/declaration-imports/{item.id}/approve",
        json={"draft_revision": item.draft_revision, "idempotency_key": "existing-property-approval-0001"},
    )
    assert response.status_code == 200, response.json()
    result = response.json()
    assert result["summary"] == {"properties_created": 0, "properties_updated": 1, "units_created": 2, "units_updated": 2, "rules_created": 1, "documents_bound": 1}

    from app.core.config import settings
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            await session.execute(text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(seed_data["org_a_id"])})
            property_row = await session.scalar(select(Property).where(Property.id == reviewed_existing_property_import.property.id))
            units = (await session.scalars(select(Unit).where(Unit.property_id == property_row.id).order_by(Unit.number))).all()
            rules = (await session.scalars(select(PropertyRule).where(PropertyRule.property_id == property_row.id).order_by(PropertyRule.stable_key))).all()
            facts = (await session.scalars(select(ApprovedFactSource).where(ApprovedFactSource.property_id == property_row.id))).all()
            legal = await session.scalar(select(PropertyLegalReference).where(PropertyLegalReference.property_id == property_row.id))
            assignment = await session.scalar(select(PropertyAssignment).where(PropertyAssignment.property_id == property_row.id))
            relation = await session.scalar(select(PropertyDocumentRelation).where(PropertyDocumentRelation.property_id == property_row.id))
            assert property_row.street == "Talstraße 112"
            assert [unit.mea for unit in units] == [Decimal("47732")] * 4
            assert property_row.id == (await session.scalar(select(PropertyDocument.property_id).where(PropertyDocument.id == reviewed_existing_property_import.amendment.id)))
            assert next(rule for rule in rules if rule.id == reviewed_existing_property_import.old_rule.id).status == "SUPERSEDED"
            assert len([rule for rule in rules if rule.status == "EFFECTIVE"]) == 1
            assert await session.scalar(
                select(func.count())
                .select_from(PropertyRuleRelation)
                .where(PropertyRuleRelation.property_id == property_row.id)
            ) == 1
            street_sources = [fact for fact in facts if fact.field_key == "street"]
            from app.declarations.approval import value_fingerprint
            matching = next(source for source in street_sources if source.value_fingerprint == value_fingerprint(_fact("TEXT", "Talstraße 111")))
            mismatching = next(source for source in street_sources if source.value_fingerprint == "0" * 64)
            assert matching.status == "SUPERSEDED" and matching.valid_to is not None
            assert mismatching.status == "EFFECTIVE" and mismatching.valid_to is None
            assert any(source.status == "EFFECTIVE" and source.value_fingerprint == value_fingerprint(_fact("TEXT", "Talstraße 112")) for source in street_sources)
            assert any(fact.subject_kind == "RULE" for fact in facts)
            assert legal is not None and legal.reference_type == "LAND_REGISTER"
            assert assignment is not None and assignment.unit_id == next(unit.id for unit in units if unit.number == "1")
            assert relation is not None and relation.relation_type == "AMENDS"
            old_sources = (await session.scalars(select(PropertyRuleSource).where(PropertyRuleSource.property_rule_id == reviewed_existing_property_import.old_rule.id))).all()
            new_sources = (await session.scalars(
                select(PropertyRuleSource).where(
                    PropertyRuleSource.property_id == property_row.id,
                    PropertyRuleSource.property_rule_id != reviewed_existing_property_import.old_rule.id,
                )
            )).all()
            assert [(source.quote, source.field_path) for source in old_sources] == [("Altbestand § 8", "rule")]
            assert new_sources and all(source.quote == "Nachtrag § 8" for source in new_sources)
            assert {"LEGAL_REFERENCE", "ASSIGNMENT", "DOCUMENT_RELATION"} <= {fact.subject_kind for fact in facts}
            assert await session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id == item.id, AuditEvent.action == "declaration_import.applied")) == 1
    finally:
        await engine.dispose()

    replay = await client_a_clerk.post(
        f"/api/declaration-imports/{item.id}/approve",
        json={"draft_revision": 0, "idempotency_key": "existing-property-approval-0001"},
    )
    assert replay.status_code == 409
    replay = await client_a_clerk.post(
        f"/api/declaration-imports/{item.id}/approve",
        json={"draft_revision": item.draft_revision, "idempotency_key": "existing-property-approval-0001"},
    )
    assert replay.status_code == 200 and replay.json() == result
    conflict = await client_a_clerk.post(
        f"/api/declaration-imports/{item.id}/approve",
        json={"draft_revision": item.draft_revision, "idempotency_key": "existing-property-approval-other"},
    )
    assert conflict.status_code == 409 and conflict.json()["detail"] == {"code": "ALREADY_APPLIED"}


@pytest.mark.asyncio
async def test_existing_property_late_failure_rolls_back_every_mutation(
    client_a_clerk, reviewed_existing_property_import, seed_data, monkeypatch
) -> None:
    """A failure after final source work leaves the target graph and import untouched."""
    from app.core.config import settings
    from app.repositories.property_knowledge_repo import PropertyKnowledgeRepository

    original_apply = PropertyKnowledgeRepository.apply_existing

    async def fail_after_sources(self, *args, **kwargs):
        await original_apply(self, *args, **kwargs)
        raise RuntimeError("forced-existing-property-late-failure")

    monkeypatch.setattr(PropertyKnowledgeRepository, "apply_existing", fail_after_sources)
    item = reviewed_existing_property_import.item
    with pytest.raises(RuntimeError, match="forced-existing-property-late-failure"):
        await client_a_clerk.post(
            f"/api/declaration-imports/{item.id}/approve",
            json={"draft_revision": item.draft_revision, "idempotency_key": "existing-property-rollback"},
        )

    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            await session.execute(text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(seed_data["org_a_id"])})
            property_row = await session.scalar(select(Property).where(Property.id == reviewed_existing_property_import.property.id))
            units = (await session.scalars(select(Unit).where(Unit.property_id == property_row.id))).all()
            import_row = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == item.id))
            assert property_row.street == "Talstraße 111"
            assert [unit.mea for unit in units] == [Decimal("95464"), Decimal("95464")]
            assert await session.scalar(select(PropertyDocument.property_id).where(PropertyDocument.id == reviewed_existing_property_import.amendment.id)) is None
            assert await session.scalar(select(func.count()).select_from(PropertyRule).where(PropertyRule.property_id == property_row.id)) == 1
            assert await session.scalar(select(func.count()).select_from(ApprovedFactSource).where(ApprovedFactSource.property_id == property_row.id)) == 2
            assert import_row.status == "NEEDS_REVIEW" and import_row.approved_at is None
            assert await session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id == item.id)) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_existing_keep_only_approval_does_not_rewrite_canonical_graph(
    client_a_clerk, reviewed_existing_property_import, seed_data,
) -> None:
    """KEEP and REJECTED rows do not dirty an already-bound target graph."""
    from app.core.config import settings

    item = reviewed_existing_property_import.item
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            await session.execute(text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(seed_data["org_a_id"])})
            candidates = (await session.scalars(select(DeclarationCandidate).where(DeclarationCandidate.import_id == item.id))).all()
            for candidate in candidates:
                if candidate.operation == "UPDATE":
                    candidate.operation = "KEEP"
                    candidate.review_status = "ACCEPTED"
                else:
                    candidate.review_status = "REJECTED"
            amendment = await session.scalar(select(PropertyDocument).where(PropertyDocument.id == reviewed_existing_property_import.amendment.id))
            amendment.property_id = reviewed_existing_property_import.property.id
            property_row = await session.scalar(select(Property).where(Property.id == reviewed_existing_property_import.property.id))
            before_updated_at = property_row.updated_at
            before_units = (await session.scalars(select(Unit.id).where(Unit.property_id == property_row.id).order_by(Unit.id))).all()
            await session.commit()

        response = await client_a_clerk.post(
            f"/api/declaration-imports/{item.id}/approve",
            json={"draft_revision": item.draft_revision, "idempotency_key": "existing-keep-only-0001"},
        )
        assert response.status_code == 200, response.json()
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            await session.execute(text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(seed_data["org_a_id"])})
            property_row = await session.scalar(select(Property).where(Property.id == reviewed_existing_property_import.property.id))
            assert property_row.updated_at == before_updated_at
            assert (await session.scalars(select(Unit.id).where(Unit.property_id == property_row.id).order_by(Unit.id))).all() == before_units
            assert await session.scalar(select(func.count()).select_from(PropertyRule).where(PropertyRule.property_id == property_row.id)) == 1
            assert await session.scalar(select(func.count()).select_from(PropertyAssignment).where(PropertyAssignment.property_id == property_row.id)) == 0
            assert await session.scalar(select(func.count()).select_from(ApprovedFactSource).where(ApprovedFactSource.property_id == property_row.id)) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_existing_all_keep_unit_reference_persists_assignment_and_scoped_rule(
    client_a_clerk, reviewed_existing_property_import, seed_data,
) -> None:
    """Special CREATE rows use the full locked map even when unit fields are KEEP."""
    from app.core.config import settings

    item = reviewed_existing_property_import.item
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            await session.execute(text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(seed_data["org_a_id"])})
            candidates = (await session.scalars(select(DeclarationCandidate).where(DeclarationCandidate.import_id == item.id))).all()
            for candidate in candidates:
                if candidate.candidate_type in {"ASSIGNMENT", "RULE"}:
                    continue
                if candidate.candidate_type == "UNIT_FIELD":
                    candidate.operation = "KEEP"
                    candidate.review_status = "ACCEPTED"
                else:
                    candidate.review_status = "REJECTED"
            rule = next(candidate for candidate in candidates if candidate.candidate_type == "RULE")
            rule.proposed_value_json = {
                **rule.proposed_value_json,
                "scope": {"unit_subject_keys": ["unit:1"]},
                "supersedes_subject_keys": [],
            }
            amendment = await session.scalar(select(PropertyDocument).where(PropertyDocument.id == reviewed_existing_property_import.amendment.id))
            amendment.property_id = reviewed_existing_property_import.property.id
            await session.commit()

        response = await client_a_clerk.post(
            f"/api/declaration-imports/{item.id}/approve",
            json={"draft_revision": item.draft_revision, "idempotency_key": "existing-keep-reference-0001"},
        )
        assert response.status_code == 200, response.json()
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            await session.execute(text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(seed_data["org_a_id"])})
            unit = await session.scalar(select(Unit).where(Unit.property_id == reviewed_existing_property_import.property.id, Unit.number == "1"))
            assignment = await session.scalar(select(PropertyAssignment).where(PropertyAssignment.property_id == reviewed_existing_property_import.property.id))
            scoped_rule = await session.scalar(select(PropertyRule).where(PropertyRule.property_id == reviewed_existing_property_import.property.id, PropertyRule.id != reviewed_existing_property_import.old_rule.id))
            assert assignment is not None and assignment.unit_id == unit.id
            assert scoped_rule is not None and scoped_rule.scope_json["unit_subject_keys"] == ["unit:1"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_existing_legacy_confirmation_is_atomic_and_audited(
    client_a_clerk, reviewed_existing_property_import, seed_data,
) -> None:
    """The DB transaction confirms a legacy MEA scale with the exact audit payload."""
    from app.core.config import settings

    item = reviewed_existing_property_import.item
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            await session.execute(text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(seed_data["org_a_id"])})
            property_row = await session.scalar(select(Property).where(Property.id == reviewed_existing_property_import.property.id))
            property_row.mea_denominator = None
            property_row.mea_scale_status = "LEGACY_UNCONFIRMED"
            candidates = (await session.scalars(select(DeclarationCandidate).where(DeclarationCandidate.import_id == item.id))).all()
            for candidate in candidates:
                candidate.review_status = "REJECTED"
            confirmation = _reviewed_candidate(item, stable_key="property:legacy-confirm", candidate_type="PROPERTY_FIELD", subject_key="property:tal", field_key="mea_denominator", previous=None, value=_fact("INTEGER", 1_000_000))
            session.add(confirmation)
            await session.flush()
            session.add(DeclarationCandidateEvidence(organization_id=item.organization_id, candidate_id=confirmation.id, document_id=reviewed_existing_property_import.amendment.id, pdf_page=1, quote="Nenner bestätigt"))
            await session.commit()

        response = await client_a_clerk.post(f"/api/declaration-imports/{item.id}/approve", json={"draft_revision": item.draft_revision, "idempotency_key": "existing-legacy-confirm-0001"})
        assert response.status_code == 200, response.json()
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            await session.execute(text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(seed_data["org_a_id"])})
            property_row = await session.scalar(select(Property).where(Property.id == reviewed_existing_property_import.property.id))
            audit = await session.scalar(select(AuditEvent).where(AuditEvent.entity_id == property_row.id, AuditEvent.action == "property.mea_scale_confirmed"))
            assert property_row.mea_scale_status == "CONFIRMED" and property_row.mea_denominator == 1_000_000
            assert audit.payload_diff == {"old_scale": {"mea_scale_status": "LEGACY_UNCONFIRMED", "mea_denominator": None}, "new_scale": {"mea_scale_status": "CONFIRMED", "mea_denominator": 1_000_000}, "existing_values_confirmed_as_numerators": True}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_existing_full_rescale_updates_every_current_unit_atomically(
    client_a_clerk, reviewed_existing_property_import, seed_data,
) -> None:
    """Changing the denominator needs exact numerators for all locked current units."""
    from app.core.config import settings

    item = reviewed_existing_property_import.item
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            await session.execute(text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(seed_data["org_a_id"])})
            candidates = (await session.scalars(select(DeclarationCandidate).where(DeclarationCandidate.import_id == item.id))).all()
            for candidate in candidates:
                if candidate.candidate_type == "UNIT_FIELD":
                    if candidate.field_key == "mea":
                        candidate.proposed_value_json = {"kind": "MEA", "numerator": 190_928, "denominator": 2_000_000}
                    else:
                        candidate.operation = "KEEP"
                    candidate.review_status = "ACCEPTED"
                else:
                    candidate.review_status = "REJECTED"
            rescale = _reviewed_candidate(item, stable_key="property:rescale", candidate_type="MEA_RESCALE", subject_key="property:tal", field_key="mea_denominator", previous=_fact("INTEGER", 1_000_000), value=_fact("INTEGER", 2_000_000))
            session.add(rescale)
            await session.flush()
            session.add(DeclarationCandidateEvidence(organization_id=item.organization_id, candidate_id=rescale.id, document_id=reviewed_existing_property_import.amendment.id, pdf_page=1, quote="Nenner umgestellt"))
            await session.commit()

        response = await client_a_clerk.post(f"/api/declaration-imports/{item.id}/approve", json={"draft_revision": item.draft_revision, "idempotency_key": "existing-rescale-0001"})
        assert response.status_code == 200, response.json()
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            await session.execute(text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(seed_data["org_a_id"])})
            property_row = await session.scalar(select(Property).where(Property.id == reviewed_existing_property_import.property.id))
            units = (await session.scalars(select(Unit).where(Unit.property_id == property_row.id).order_by(Unit.number))).all()
            assert property_row.mea_denominator == 2_000_000
            assert [unit.mea for unit in units] == [Decimal("190928"), Decimal("190928")]
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("candidate_type", "historic_status", "expected_code"),
    [
        ("ASSIGNMENT", "SUPERSEDED", "DUPLICATE_ASSIGNMENT_STABLE_KEY"),
        ("DOCUMENT_RELATION", "NEEDS_CLARIFICATION", "DUPLICATE_DOCUMENT_RELATION"),
    ],
)
async def test_existing_historical_graph_collisions_fail_before_canonical_writes(
    client_a_clerk, reviewed_existing_property_import, seed_data,
    candidate_type: str, historic_status: str, expected_code: str,
) -> None:
    """Uniqueness covers every historical status, not just effective rows."""
    from app.core.config import settings

    item = reviewed_existing_property_import.item
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            await session.execute(text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(seed_data["org_a_id"])})
            candidates = (await session.scalars(select(DeclarationCandidate).where(DeclarationCandidate.import_id == item.id))).all()
            target = next(candidate for candidate in candidates if candidate.candidate_type == candidate_type)
            keep = next(candidate for candidate in candidates if candidate.stable_key == "unit:1:number")
            for candidate in candidates:
                candidate.review_status = "ACCEPTED" if candidate.id in {target.id, keep.id} else "REJECTED"
            amendment = await session.scalar(select(PropertyDocument).where(PropertyDocument.id == reviewed_existing_property_import.amendment.id))
            amendment.property_id = reviewed_existing_property_import.property.id
            if candidate_type == "ASSIGNMENT":
                session.add(PropertyAssignment(
                    organization_id=item.organization_id, property_id=reviewed_existing_property_import.property.id,
                    unit_id=reviewed_existing_property_import.units[0].id, stable_key=target.stable_key,
                    assignment_type="SPECIAL_USE", designation="Historischer Stellplatz",
                    status=historic_status, source_import_id=item.id,
                ))
            else:
                session.add(PropertyDocumentRelation(
                    organization_id=item.organization_id, property_id=reviewed_existing_property_import.property.id,
                    source_document_id=reviewed_existing_property_import.amendment.id,
                    target_document_id=reviewed_existing_property_import.main.id,
                    relation_type="AMENDS", status=historic_status, source_import_id=item.id,
                ))
            await session.commit()

        response = await client_a_clerk.post(
            f"/api/declaration-imports/{item.id}/approve",
            json={"draft_revision": item.draft_revision, "idempotency_key": f"historical-{candidate_type.lower()}"},
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == {"code": expected_code}

        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            await session.execute(text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(seed_data["org_a_id"])})
            import_row = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == item.id))
            if candidate_type == "ASSIGNMENT":
                count = await session.scalar(select(func.count()).select_from(PropertyAssignment).where(PropertyAssignment.property_id == reviewed_existing_property_import.property.id, PropertyAssignment.stable_key == target.stable_key))
            else:
                count = await session.scalar(select(func.count()).select_from(PropertyDocumentRelation).where(PropertyDocumentRelation.property_id == reviewed_existing_property_import.property.id, PropertyDocumentRelation.source_document_id == reviewed_existing_property_import.amendment.id, PropertyDocumentRelation.target_document_id == reviewed_existing_property_import.main.id, PropertyDocumentRelation.relation_type == "AMENDS"))
            assert count == 1
            assert import_row.status == "NEEDS_REVIEW" and import_row.approved_at is None
    finally:
        await engine.dispose()
