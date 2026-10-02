"""Revision-safe review endpoints for unapproved declaration candidates."""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.audit_event import AuditEvent
from app.models.declaration_candidate import (
    DeclarationCandidate,
    DeclarationCandidateEvidence,
    DeclarationCandidateIssue,
)
from app.models.declaration_import import DeclarationExtractionRun, DeclarationImport
from app.models.property_document import PropertyDocument
from app.models.property import Property
from app.models.unit import Unit


@pytest.fixture(autouse=True)
def enable_declaration_imports(monkeypatch) -> None:
    """Keep retry endpoint coverage independent of the deploy-time default."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "declaration_import_enabled", True)


@pytest_asyncio.fixture
async def review_session(seed_data: dict):
    """Independent committed setup session for an HTTP-visible draft graph."""
    from app.core.config import settings

    engine = create_async_engine(settings.database_url)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await session.execute(
                text("SELECT set_config('app.current_org_id', :organization_id, false)"),
                {"organization_id": str(seed_data["org_a_id"])},
            )
            yield session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def review_import(review_session: AsyncSession, seed_data: dict) -> SimpleNamespace:
    """A tenant-local NEEDS_REVIEW graph with both bulk-safe and critical rows."""
    item = DeclarationImport(
        organization_id=seed_data["org_a_id"],
        mode="NEW_PROPERTY",
        status="NEEDS_REVIEW",
        draft_revision=3,
        initiated_by=seed_data["user_a_admin_id"],
        max_cost_usd=Decimal("10"),
    )
    document = PropertyDocument(
        organization_id=seed_data["org_a_id"],
        doc_type="DECLARATION",
        title="Review source",
        mime_type="application/pdf",
        content_byte_size=10,
        content_sha256="a" * 64,
        storage_uri="test/review-source.pdf",
        storage_backend="local",
        page_count=1,
        original_filename="review.pdf",
    )
    review_session.add_all([item, document])
    await review_session.flush()

    street = DeclarationCandidate(
        organization_id=item.organization_id,
        import_id=item.id,
        stable_key="fact:property:new:street",
        candidate_type="PROPERTY_FIELD",
        subject_key="property:new",
        field_key="street",
        operation="CREATE",
        proposed_value_json={"kind": "TEXT", "value": "Talstraße 111"},
        confidence=Decimal("0.99"),
        risk_class="LOW",  # Deliberately tampered: policy must recompute.
        requires_individual_review=False,
        review_status="PENDING",
    )
    rule = DeclarationCandidate(
        organization_id=item.organization_id,
        import_id=item.id,
        stable_key="rule:maintenance",
        candidate_type="RULE",
        subject_key="rule:maintenance",
        field_key=None,
        operation="CREATE",
        proposed_value_json={"title": "Zutritt"},
        confidence=Decimal("0.99"),
        risk_class="LOW",
        requires_individual_review=False,
        review_status="PENDING",
    )
    safe = DeclarationCandidate(
        organization_id=item.organization_id,
        import_id=item.id,
        stable_key="fact:unit:1:location",
        candidate_type="UNIT_FIELD",
        subject_key="test:safe",
        field_key="location",
        operation="CREATE",
        proposed_value_json={"kind": "TEXT", "value": "EG links"},
        confidence=Decimal("0.99"),
        risk_class="LOW",
        requires_individual_review=False,
        review_status="PENDING",
    )
    blocker = DeclarationCandidate(
        organization_id=item.organization_id,
        import_id=item.id,
        stable_key="fact:unit:2:location",
        candidate_type="UNIT_FIELD",
        subject_key="test:blocker",
        field_key="location",
        operation="CREATE",
        proposed_value_json={"kind": "TEXT", "value": "EG rechts"},
        confidence=Decimal("0.99"),
        risk_class="LOW",
        requires_individual_review=False,
        review_status="PENDING",
    )
    review_session.add_all([street, rule, safe, blocker])
    await review_session.flush()
    for candidate in (street, rule, safe, blocker):
        review_session.add(
            DeclarationCandidateEvidence(
                organization_id=item.organization_id,
                candidate_id=candidate.id,
                document_id=document.id,
                pdf_page=1,
                quote="Belegstelle",
            )
        )
    review_session.add(
        DeclarationCandidateIssue(
            organization_id=item.organization_id,
            import_id=item.id,
            candidate_id=blocker.id,
            code="UNRESOLVED_BLOCKER",
            severity="BLOCKER",
            message="Must remain unresolved",
            resolution_status="OPEN",
        )
    )
    await review_session.flush()
    # HTTP requests use an independent tenant transaction, so the draft graph
    # must be visible before the client exercises the public API.
    await review_session.commit()
    return SimpleNamespace(item=item, street=street, rule=rule, safe=safe, blocker=blocker)


async def _add_failed_runs(
    session: AsyncSession, item: DeclarationImport, count: int
) -> None:
    for _ in range(count):
        session.add(
            DeclarationExtractionRun(
                organization_id=item.organization_id,
                import_id=item.id,
                status="FAILED",
                provider="test",
                model_name="test",
                prompt_version="test",
                schema_version="test",
                harness_version="test",
                error_code="PROVIDER_TIMEOUT",
            )
        )
    await session.flush()


async def _add_evidence(
    session: AsyncSession,
    item: DeclarationImport,
    candidate: DeclarationCandidate,
) -> None:
    document = await session.scalar(
        select(PropertyDocument).where(
            PropertyDocument.organization_id == item.organization_id,
            PropertyDocument.title == "Review source",
        )
    )
    assert document is not None
    session.add(
        DeclarationCandidateEvidence(
            organization_id=item.organization_id,
            candidate_id=candidate.id,
            document_id=document.id,
            pdf_page=1,
            quote="Belegstelle",
        )
    )


def _unit_create_candidate(
    item: DeclarationImport,
    stable_key: str,
    subject_key: str,
    number: str,
    numerator: int,
    denominator: int = 1_000,
) -> DeclarationCandidate:
    return DeclarationCandidate(
        organization_id=item.organization_id,
        import_id=item.id,
        stable_key=stable_key,
        candidate_type="UNIT_CREATE",
        subject_key=subject_key,
        field_key=None,
        operation="CREATE",
        proposed_value_json={
            "number": {"kind": "TEXT", "value": number},
            "mea": {"kind": "MEA", "numerator": numerator, "denominator": denominator},
        },
        confidence=Decimal("0.99"),
        risk_class="LOW",
        requires_individual_review=False,
        review_status="PENDING",
    )


def _allocation_rule_payload(kind: str) -> dict:
    if kind == "VALID":
        shares = [
            {"kind": "PERCENTAGE", "value": "50"},
            {"kind": "PERCENTAGE", "value": "50"},
        ]
    elif kind == "TOTAL_INVALID":
        shares = [
            {"kind": "PERCENTAGE", "value": "60"},
            {"kind": "PERCENTAGE", "value": "50"},
        ]
    elif kind == "SHARE_INVALID":
        shares = [
            {"kind": "PERCENTAGE", "value": "50"},
            {"kind": "FRACTION", "numerator": 1, "denominator": 2},
        ]
    else:  # pragma: no cover - test helper misuse
        raise ValueError(kind)
    return {
        "title": "Heizkostenverteilung",
        "topic": "heating_costs",
        "summary": "Die Heizkosten werden nach zwei Anteilen verteilt.",
        "scope": {"all_units": True},
        "effect": {
            "allocation_formula": {
                "declared_complete": True,
                "parts": [
                    {"label": "Anteil 1", "basis": "Verbrauch", "share": shares[0]},
                    {"label": "Anteil 2", "basis": "Fläche", "share": shares[1]},
                ],
            }
        },
        "proposed_status": "EFFECTIVE",
        "supersedes_subject_keys": [],
        "conflicts_with_subject_keys": [],
        "clarification_needed": False,
    }


@pytest.mark.asyncio
async def test_rule_candidate_requires_individual_review(client_a_clerk, review_import) -> None:
    response = await client_a_clerk.post(
        f"/api/declaration-imports/{review_import.item.id}/candidates/bulk-review",
        json={
            "draft_revision": review_import.item.draft_revision,
            "candidate_ids": [str(review_import.rule.id)],
            "decision": "ACCEPTED",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INDIVIDUAL_REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_correction_is_typed_revisioned_idempotent_and_audited_without_values(
    client_a_clerk, review_import, review_session: AsyncSession
) -> None:
    payload = {
        "draft_revision": review_import.item.draft_revision,
        "decision": "CORRECTED",
        "corrected_value": "Talstraße 112",
        "comment": " Schreibweise geprüft ",
    }
    response = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{review_import.street.id}",
        json=payload,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["candidate"]["review_status"] == "CORRECTED"
    assert body["candidate"]["corrected_value_json"] == {"kind": "TEXT", "value": "Talstraße 112"}
    assert body["draft_revision"] == 4
    await review_session.refresh(review_import.street)
    first_reviewed_at = review_import.street.reviewed_at

    replay = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{review_import.street.id}",
        json={**payload, "draft_revision": 4, "comment": "Schreibweise geprüft"},
    )
    assert replay.status_code == 200
    assert replay.json()["draft_revision"] == 4
    await review_session.refresh(review_import.street)
    assert review_import.street.reviewed_at == first_reviewed_at
    events = (await review_session.execute(
        select(AuditEvent).where(AuditEvent.entity_id == review_import.item.id)
    )).scalars().all()
    assert len(events) == 1
    assert events[0].payload_diff == {
        "import_id": str(review_import.item.id),
        "candidate_id": str(review_import.street.id),
        "decision": "CORRECTED",
        "revision": 4,
    }


@pytest.mark.asyncio
async def test_stale_and_unsafe_bulk_requests_are_atomic(
    client_a_clerk, review_import, review_session: AsyncSession
) -> None:
    stale = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{review_import.street.id}",
        json={
            "draft_revision": 0, "decision": "CORRECTED",
            "corrected_value": {"kind": "INTEGER", "value": 7},
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "STALE_DRAFT_REVISION"
    await review_session.refresh(review_import.street)
    assert review_import.street.review_status == "PENDING"
    assert review_import.street.corrected_value_json is None
    bulk = await client_a_clerk.post(
        f"/api/declaration-imports/{review_import.item.id}/candidates/bulk-review",
        json={
            "draft_revision": review_import.item.draft_revision,
            "candidate_ids": [str(review_import.safe.id), str(review_import.rule.id)],
            "decision": "ACCEPTED",
        },
    )
    assert bulk.status_code == 409
    await review_session.refresh(review_import.safe)
    assert review_import.safe.review_status == "PENDING"


@pytest.mark.asyncio
async def test_preview_blocks_unresolved_blockers_and_readonly_cannot_mutate(
    client_a_clerk, client_a_readonly, review_import
) -> None:
    for path, method, payload in (
        (f"/api/declaration-imports/{review_import.item.id}/candidates/{review_import.street.id}", "patch", {"draft_revision": 3, "decision": "ACCEPTED"}),
        (f"/api/declaration-imports/{review_import.item.id}/candidates/bulk-review", "post", {"draft_revision": 3, "candidate_ids": [str(review_import.safe.id)], "decision": "ACCEPTED"}),
        (f"/api/declaration-imports/{review_import.item.id}/retry", "post", None),
        (f"/api/declaration-imports/{review_import.item.id}/cancel", "post", None),
        (f"/api/declaration-imports/{review_import.item.id}/approve", "post", {"draft_revision": 3, "idempotency_key": "0123456789abcdef"}),
    ):
        response = await getattr(client_a_readonly, method)(path, json=payload)
        assert response.status_code == 403
    preview = await client_a_clerk.get(
        f"/api/declaration-imports/{review_import.item.id}/approval-preview"
    )
    assert preview.status_code == 200
    assert preview.json()["can_approve"] is False
    assert any(item["code"] == "UNRESOLVED_BLOCKER" for item in preview.json()["issues"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unresolved_value",
    [
        {"clarification_needed": True},
        {"proposed_status": "NEEDS_CLARIFICATION"},
    ],
)
async def test_preview_blocks_an_accepted_rule_that_still_needs_clarification(
    client_a_clerk, review_import, review_session: AsyncSession, unresolved_value: dict
) -> None:
    """Preview must expose the same unresolved-rule boundary as approval."""
    rule_value = _allocation_rule_payload("VALID")
    rule_value.update(unresolved_value)
    review_import.rule.proposed_value_json = rule_value
    await review_session.commit()
    revision = review_import.item.draft_revision
    for candidate in (
        review_import.street,
        review_import.rule,
        review_import.safe,
        review_import.blocker,
    ):
        reviewed = await client_a_clerk.patch(
            f"/api/declaration-imports/{review_import.item.id}/candidates/{candidate.id}",
            json={"draft_revision": revision, "decision": "ACCEPTED"},
        )
        assert reviewed.status_code == 200
        revision = reviewed.json()["draft_revision"]
    blocker = await review_session.scalar(
        select(DeclarationCandidateIssue).where(
            DeclarationCandidateIssue.import_id == review_import.item.id,
            DeclarationCandidateIssue.code == "UNRESOLVED_BLOCKER",
        )
    )
    assert blocker is not None
    await review_session.delete(blocker)
    await review_session.commit()

    preview = await client_a_clerk.get(
        f"/api/declaration-imports/{review_import.item.id}/approval-preview"
    )

    assert preview.status_code == 200
    body = preview.json()
    assert body["can_approve"] is False
    assert any(
        issue["code"] == "RULE_NEEDS_CLARIFICATION"
        and issue["severity"] == "WARNING"
        and issue["candidate_id"] == str(review_import.rule.id)
        for issue in body["issues"]
    )

    corrected = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{review_import.rule.id}",
        json={
            "draft_revision": revision,
            "decision": "CORRECTED",
            "corrected_value": _allocation_rule_payload("VALID"),
        },
    )
    assert corrected.status_code == 200
    corrected_preview = await client_a_clerk.get(
        f"/api/declaration-imports/{review_import.item.id}/approval-preview"
    )
    assert corrected_preview.status_code == 200
    assert corrected_preview.json()["can_approve"] is True

    rejected = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{review_import.rule.id}",
        json={
            "draft_revision": corrected.json()["draft_revision"],
            "decision": "REJECTED",
            "comment": "Nicht übernehmen.",
        },
    )
    assert rejected.status_code == 200
    rejected_preview = await client_a_clerk.get(
        f"/api/declaration-imports/{review_import.item.id}/approval-preview"
    )
    assert rejected_preview.status_code == 200
    assert rejected_preview.json()["can_approve"] is True


@pytest.mark.asyncio
async def test_foreign_tenant_is_404_and_server_enforces_comment_and_typed_field(
    client_b_admin, client_a_clerk, review_import, review_session: AsyncSession
) -> None:
    foreign = await client_b_admin.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{review_import.street.id}",
        json={"draft_revision": 3, "decision": "ACCEPTED"},
    )
    assert foreign.status_code == 404
    clarification = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{review_import.street.id}",
        json={"draft_revision": 3, "decision": "NEEDS_CLARIFICATION"},
    )
    assert clarification.status_code == 422
    wrong_kind = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{review_import.street.id}",
        json={"draft_revision": 3, "decision": "CORRECTED", "corrected_value": {"kind": "INTEGER", "value": 7}},
    )
    assert wrong_kind.status_code == 422
    review_import.safe.field_key = "unknown_server_field"
    await review_session.commit()
    unknown_field = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{review_import.safe.id}",
        json={"draft_revision": 3, "decision": "CORRECTED", "corrected_value": "x"},
    )
    assert unknown_field.status_code == 422


@pytest.mark.asyncio
async def test_typed_correction_resolves_and_reverting_it_reopens_local_conflict(
    client_a_clerk, review_import, review_session: AsyncSession, seed_data: dict
) -> None:
    review_import.street.proposed_value_json = {
        "alternatives": [
            {"kind": "TEXT", "value": "Talstraße 111"},
            {"kind": "TEXT", "value": "Talstraße 112"},
        ]
    }
    issue = DeclarationCandidateIssue(
        organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
        candidate_id=review_import.street.id, code="CONFLICTING_FACT_ALTERNATIVES",
        severity="BLOCKER", message="ambiguous", resolution_status="OPEN",
    )
    unrelated = DeclarationCandidate(
        organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
        stable_key="fact:property:new:provenance-city", candidate_type="PROPERTY_FIELD",
        subject_key="property:new", field_key="city", operation="CREATE",
        proposed_value_json={"kind": "TEXT", "value": "Düsseldorf"}, confidence=Decimal("0.99"),
        risk_class="LOW", requires_individual_review=False, review_status="PENDING",
    )
    review_session.add(unrelated)
    await review_session.flush()
    await _add_evidence(review_session, review_import.item, unrelated)
    review_session.add(issue)
    await review_session.commit()
    correction = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{review_import.street.id}",
        json={"draft_revision": 3, "decision": "CORRECTED", "corrected_value": "Talstraße 112"},
    )
    assert correction.status_code == 200
    await review_session.refresh(issue)
    assert issue.resolution_status == "RESOLVED"
    assert issue.resolved_by == seed_data["user_a_clerk_id"]
    assert issue.resolved_at is not None
    resolved_at = issue.resolved_at
    unrelated_review = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{unrelated.id}",
        json={"draft_revision": 4, "decision": "CORRECTED", "corrected_value": "Köln"},
    )
    assert unrelated_review.status_code == 200
    await review_session.refresh(issue)
    assert issue.resolved_by == seed_data["user_a_clerk_id"]
    assert issue.resolved_at == resolved_at
    preview = await client_a_clerk.get(f"/api/declaration-imports/{review_import.item.id}/approval-preview")
    assert "CONFLICTING_FACT_ALTERNATIVES" not in {item["code"] for item in preview.json()["issues"]}
    reverted = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{review_import.street.id}",
        json={"draft_revision": 5, "decision": "ACCEPTED"},
    )
    assert reverted.status_code == 200
    await review_session.refresh(issue)
    assert issue.resolution_status == "OPEN"
    assert issue.resolved_by is None
    assert issue.resolved_at is None


@pytest.mark.asyncio
async def test_global_blocker_retry_allowlist_and_cancel_fence(
    client_a_clerk, review_import, review_session: AsyncSession, seed_data: dict
) -> None:
    review_session.add(DeclarationCandidateIssue(
        organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
        candidate_id=None, code="GLOBAL_BLOCKER", severity="BLOCKER",
        message="global invariant", resolution_status="OPEN",
    ))
    await review_session.commit()
    safe_bulk = await client_a_clerk.post(
        f"/api/declaration-imports/{review_import.item.id}/candidates/bulk-review",
        json={"draft_revision": 3, "candidate_ids": [str(review_import.safe.id)], "decision": "ACCEPTED"},
    )
    assert safe_bulk.status_code == 200
    preview = await client_a_clerk.get(f"/api/declaration-imports/{review_import.item.id}/approval-preview")
    assert preview.status_code == 200
    assert {issue["code"] for issue in preview.json()["issues"]} >= {"GLOBAL_BLOCKER"}

    review_import.item.status = "FAILED"
    review_import.item.error_code = "PROVIDER_TIMEOUT"
    review_import.item.attempt_count = 4
    review_import.item.estimated_cost_usd = Decimal("0")
    await _add_failed_runs(review_session, review_import.item, 1)
    await review_session.commit()
    retry = await client_a_clerk.post(f"/api/declaration-imports/{review_import.item.id}/retry", headers={"Idempotency-Key": "11111111-1111-4111-8111-111111111111"})
    assert retry.status_code == 200
    assert retry.json()["status"] == "QUEUED"

    review_import.item.status = "QUEUED"
    review_import.item.lease_generation = 9
    review_import.item.lease_owner = "worker"
    review_import.item.lease_token = uuid.uuid4()
    from datetime import datetime, timedelta, timezone
    review_import.item.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await review_session.commit()
    cancel = await client_a_clerk.post(f"/api/declaration-imports/{review_import.item.id}/cancel")
    assert cancel.status_code == 200
    await review_session.refresh(review_import.item)
    assert review_import.item.status == "CANCELLED"
    assert review_import.item.lease_generation == 10
    assert review_import.item.lease_token is None
    repeated = await client_a_clerk.post(f"/api/declaration-imports/{review_import.item.id}/cancel")
    assert repeated.status_code == 200
    await review_session.refresh(review_import.item)
    assert review_import.item.lease_generation == 10


@pytest.mark.asyncio
async def test_critical_evidence_comment_and_review_never_write_canonical_rows(
    client_a_clerk, review_import, review_session: AsyncSession
) -> None:
    before = (
        await review_session.scalar(select(func.count()).select_from(Property)),
        await review_session.scalar(select(func.count()).select_from(Unit)),
    )
    rejected = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{review_import.rule.id}",
        json={"draft_revision": 3, "decision": "REJECTED"},
    )
    assert rejected.status_code == 422
    for evidence in (await review_session.execute(
        select(DeclarationCandidateEvidence).where(
            DeclarationCandidateEvidence.candidate_id == review_import.rule.id
        )
    )).scalars():
        await review_session.delete(evidence)
    await review_session.commit()
    no_evidence = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{review_import.rule.id}",
        json={"draft_revision": 3, "decision": "ACCEPTED"},
    )
    assert no_evidence.status_code == 422
    after = (
        await review_session.scalar(select(func.count()).select_from(Property)),
        await review_session.scalar(select(func.count()).select_from(Unit)),
    )
    assert after == before


@pytest.mark.asyncio
async def test_retry_rejects_non_allowlisted_and_run_budget_and_cancel_rejects_applied(
    client_a_clerk, review_import, review_session: AsyncSession
) -> None:
    review_import.item.status = "FAILED"
    review_import.item.error_code = "INVALID_PDF"
    await review_session.commit()
    preview = await client_a_clerk.get(f"/api/declaration-imports/{review_import.item.id}/approval-preview")
    assert preview.status_code == 409
    assert (await client_a_clerk.post(f"/api/declaration-imports/{review_import.item.id}/retry", headers={"Idempotency-Key": "11111111-1111-4111-8111-111111111111"})).status_code == 409

    from app.core.config import settings
    review_import.item.error_code = "OCR_TIMEOUT"
    review_import.item.attempt_count = 0
    await _add_failed_runs(review_session, review_import.item, settings.declaration_max_attempts)
    await review_session.commit()
    assert (await client_a_clerk.post(f"/api/declaration-imports/{review_import.item.id}/retry", headers={"Idempotency-Key": "11111111-1111-4111-8111-111111111111"})).json()["detail"] == "RETRY_BUDGET_EXHAUSTED"

    review_import.item.attempt_count = 0
    review_import.item.estimated_cost_usd = review_import.item.max_cost_usd
    await review_session.commit()
    assert (await client_a_clerk.post(f"/api/declaration-imports/{review_import.item.id}/retry", headers={"Idempotency-Key": "11111111-1111-4111-8111-111111111111"})).json()["detail"] == "RETRY_BUDGET_EXHAUSTED"

    from fastapi import HTTPException
    from app.services.declaration_import_service import DeclarationImportService
    service = DeclarationImportService(
        review_session, review_import.item.organization_id, review_import.item.initiated_by
    )
    service._get_for_update = AsyncMock(return_value=SimpleNamespace(status="APPLIED"))
    with pytest.raises(HTTPException, match="IMPORT_CANNOT_BE_CANCELLED"):
        await service.cancel(review_import.item.id)


@pytest.mark.asyncio
async def test_effective_graph_revalidates_global_mea_total_and_duplicate_unit_numbers(
    client_a_clerk, review_import, review_session: AsyncSession, seed_data: dict
) -> None:
    first = _unit_create_candidate(
        review_import.item, "unit-create:mea-1", "unit:mea-1", "1", 600
    )
    second = _unit_create_candidate(
        review_import.item, "unit-create:mea-2", "unit:mea-2", "1", 500
    )
    review_session.add_all([first, second])
    await review_session.flush()
    await _add_evidence(review_session, review_import.item, first)
    await _add_evidence(review_session, review_import.item, second)
    denominator = DeclarationCandidate(
        organization_id=seed_data["org_a_id"],
        import_id=review_import.item.id,
        stable_key="fact:property:new:mea_denominator",
        candidate_type="PROPERTY_FIELD",
        subject_key="property:new",
        field_key="mea_denominator",
        operation="CREATE",
        proposed_value_json={"kind": "INTEGER", "value": 1_000},
        confidence=Decimal("0.99"),
        risk_class="LOW",
        requires_individual_review=False,
        review_status="PENDING",
    )
    review_session.add(denominator)
    location_only = DeclarationCandidate(
        organization_id=seed_data["org_a_id"],
        import_id=review_import.item.id,
        stable_key="fact:unit:location-only:location",
        candidate_type="UNIT_FIELD",
        subject_key="unit:location-only",
        field_key="location",
        operation="CREATE",
        proposed_value_json={"kind": "TEXT", "value": "Hof"},
        confidence=Decimal("0.99"),
        risk_class="LOW",
        requires_individual_review=False,
        review_status="PENDING",
    )
    review_session.add(location_only)
    review_session.add_all([
        DeclarationCandidateIssue(
            organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
            candidate_id=None, code="MEA_TOTAL_INVALID", severity="BLOCKER",
            message="total", details_json={"mea_set_status": "COMPLETE"}, resolution_status="OPEN",
        ),
        DeclarationCandidateIssue(
            organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
            candidate_id=None, code="DUPLICATE_UNIT_NUMBER", severity="BLOCKER",
            message="duplicate", resolution_status="OPEN",
        ),
    ])
    await review_session.commit()

    mea_only = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{second.id}",
        json={
            "draft_revision": 3,
            "decision": "CORRECTED",
            "corrected_value": {
                "number": {"kind": "TEXT", "value": "1"},
                "mea": {"kind": "MEA", "numerator": 400, "denominator": 1_000},
            },
        },
    )
    assert mea_only.status_code == 200
    global_issues = (await review_session.execute(
        select(DeclarationCandidateIssue).where(
            DeclarationCandidateIssue.import_id == review_import.item.id,
            DeclarationCandidateIssue.candidate_id.is_(None),
        )
    )).scalars().all()
    assert {issue.code for issue in global_issues if issue.resolution_status == "OPEN"} == {"DUPLICATE_UNIT_NUMBER"}

    corrected_number = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{second.id}",
        json={
            "draft_revision": 4,
            "decision": "CORRECTED",
            "corrected_value": {
                "number": {"kind": "TEXT", "value": "2"},
                "mea": {"kind": "MEA", "numerator": 400, "denominator": 1_000},
            },
        },
    )
    assert corrected_number.status_code == 200
    import_id = review_import.item.id
    review_session.expire_all()
    assert not (await review_session.execute(
        select(DeclarationCandidateIssue).where(
            DeclarationCandidateIssue.import_id == import_id,
            DeclarationCandidateIssue.resolution_status == "OPEN",
            DeclarationCandidateIssue.code.in_({"MEA_TOTAL_INVALID", "DUPLICATE_UNIT_NUMBER"}),
        )
    )).scalars().all()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mea_set_status", "corrected_numerator"),
    [("COMPLETE", 400), ("PARTIAL", 300), ("UNKNOWN", 300)],
)
async def test_global_mea_revalidation_preserves_original_set_status_semantics(
    client_a_clerk,
    review_import,
    review_session: AsyncSession,
    seed_data: dict,
    mea_set_status: str,
    corrected_numerator: int,
) -> None:
    first = _unit_create_candidate(
        review_import.item, "unit-create:status-1", "unit:status-1", "1", 600
    )
    second = _unit_create_candidate(
        review_import.item, "unit-create:status-2", "unit:status-2", "2", 500
    )
    denominator = DeclarationCandidate(
        organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
        stable_key="fact:property:new:status-denominator", candidate_type="PROPERTY_FIELD",
        subject_key="property:new", field_key="mea_denominator", operation="CREATE",
        proposed_value_json={"kind": "INTEGER", "value": 1_000},
        confidence=Decimal("0.99"), risk_class="LOW", requires_individual_review=False,
        review_status="PENDING",
    )
    review_session.add_all([first, second, denominator])
    await review_session.flush()
    await _add_evidence(review_session, review_import.item, first)
    await _add_evidence(review_session, review_import.item, second)
    issue = DeclarationCandidateIssue(
        organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
        candidate_id=None, code="MEA_TOTAL_INVALID", severity="BLOCKER", message="total",
        details_json={"mea_set_status": mea_set_status}, resolution_status="OPEN",
    )
    review_session.add(issue)
    await review_session.commit()

    response = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{second.id}",
        json={
            "draft_revision": 3,
            "decision": "CORRECTED",
            "corrected_value": {
                "number": {"kind": "TEXT", "value": "2"},
                "mea": {"kind": "MEA", "numerator": corrected_numerator, "denominator": 1_000},
            },
        },
    )
    assert response.status_code == 200
    await review_session.refresh(issue)
    assert issue.resolution_status == "RESOLVED"


@pytest.mark.asyncio
async def test_mea_type_issue_uses_attached_candidate_subject_not_candidate_id(
    client_a_clerk, review_import, review_session: AsyncSession, seed_data: dict
) -> None:
    number = DeclarationCandidate(
        organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
        stable_key="fact:unit:type-check:number", candidate_type="UNIT_FIELD",
        subject_key="unit:type-check", field_key="number", operation="CREATE",
        proposed_value_json={"kind": "TEXT", "value": "1"}, confidence=Decimal("0.99"),
        risk_class="LOW", requires_individual_review=False, review_status="PENDING",
    )
    mea = DeclarationCandidate(
        organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
        stable_key="fact:unit:type-check:mea", candidate_type="UNIT_FIELD",
        subject_key="unit:type-check", field_key="mea", operation="CREATE",
        proposed_value_json={"kind": "TEXT", "value": "not a fraction"}, confidence=Decimal("0.99"),
        risk_class="LOW", requires_individual_review=False, review_status="PENDING",
    )
    review_session.add_all([number, mea])
    await review_session.flush()
    await _add_evidence(review_session, review_import.item, mea)
    issue = DeclarationCandidateIssue(
        organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
        candidate_id=number.id, code="MEA_VALUE_TYPE_INVALID", severity="BLOCKER",
        message="wrong type", resolution_status="OPEN",
    )
    review_session.add(issue)
    await review_session.commit()
    response = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{mea.id}",
        json={
            "draft_revision": 3,
            "decision": "CORRECTED",
            "corrected_value": {"kind": "MEA", "numerator": 1, "denominator": 1_000},
        },
    )
    assert response.status_code == 200
    await review_session.refresh(issue)
    assert issue.resolution_status == "RESOLVED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial_kind", "initial_code", "corrected_kind", "corrected_code"),
    [
        ("TOTAL_INVALID", "ALLOCATION_TOTAL_INVALID", "SHARE_INVALID", "ALLOCATION_SHARE_TYPE_INVALID"),
        ("SHARE_INVALID", "ALLOCATION_SHARE_TYPE_INVALID", "TOTAL_INVALID", "ALLOCATION_TOTAL_INVALID"),
    ],
)
async def test_allocation_revalidation_cannot_hide_a_changed_blocker_code(
    client_a_clerk,
    review_import,
    review_session: AsyncSession,
    seed_data: dict,
    initial_kind: str,
    initial_code: str,
    corrected_kind: str,
    corrected_code: str,
) -> None:
    candidate = DeclarationCandidate(
        organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
        stable_key=f"rule:allocation:{initial_kind}", candidate_type="RULE",
        subject_key=f"rule:allocation:{initial_kind}", field_key=None, operation="CREATE",
        proposed_value_json=_allocation_rule_payload(initial_kind), confidence=Decimal("0.99"),
        risk_class="LOW", requires_individual_review=False, review_status="PENDING",
    )
    review_session.add(candidate)
    await review_session.flush()
    await _add_evidence(review_session, review_import.item, candidate)
    original = DeclarationCandidateIssue(
        organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
        candidate_id=candidate.id, code=initial_code, severity="BLOCKER",
        message="initial allocation blocker", resolution_status="OPEN",
    )
    review_session.add(original)
    await review_session.commit()
    import_id = review_import.item.id
    candidate_id = candidate.id

    response = await client_a_clerk.patch(
        f"/api/declaration-imports/{import_id}/candidates/{candidate_id}",
        json={
            "draft_revision": 3,
            "decision": "CORRECTED",
            "corrected_value": _allocation_rule_payload(corrected_kind),
        },
    )
    assert response.status_code == 200
    review_session.expire_all()
    rows = (await review_session.execute(
        select(DeclarationCandidateIssue).where(
            DeclarationCandidateIssue.import_id == import_id,
            DeclarationCandidateIssue.candidate_id == candidate_id,
            DeclarationCandidateIssue.code.in_({initial_code, corrected_code}),
        )
    )).scalars().all()
    statuses = {row.code: row.resolution_status for row in rows}
    assert statuses == {initial_code: "RESOLVED", corrected_code: "OPEN"}
    preview = await client_a_clerk.get(
        f"/api/declaration-imports/{import_id}/approval-preview"
    )
    assert corrected_code in {item["code"] for item in preview.json()["issues"]}


@pytest.mark.asyncio
async def test_allocation_revalidation_creates_a_new_blocker_for_an_invalid_correction(
    client_a_clerk, review_import, review_session: AsyncSession, seed_data: dict
) -> None:
    candidate = DeclarationCandidate(
        organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
        stable_key="rule:allocation:new-blocker", candidate_type="RULE",
        subject_key="rule:allocation:new-blocker", field_key=None, operation="CREATE",
        proposed_value_json=_allocation_rule_payload("VALID"), confidence=Decimal("0.99"),
        risk_class="LOW", requires_individual_review=False, review_status="PENDING",
    )
    review_session.add(candidate)
    await review_session.flush()
    await _add_evidence(review_session, review_import.item, candidate)
    await review_session.commit()

    response = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{candidate.id}",
        json={
            "draft_revision": 3,
            "decision": "CORRECTED",
            "corrected_value": _allocation_rule_payload("TOTAL_INVALID"),
        },
    )
    assert response.status_code == 200
    issue = await review_session.scalar(
        select(DeclarationCandidateIssue).where(
            DeclarationCandidateIssue.import_id == review_import.item.id,
            DeclarationCandidateIssue.candidate_id == candidate.id,
            DeclarationCandidateIssue.code == "ALLOCATION_TOTAL_INVALID",
        )
    )
    assert issue is not None
    assert issue.resolution_status == "OPEN"


@pytest.mark.asyncio
async def test_duplicate_rule_subject_blockers_resolve_only_after_one_rule_is_kept(
    client_a_clerk, review_import, review_session: AsyncSession, seed_data: dict
) -> None:
    subject_key = "rule:heating-collision"
    regular = DeclarationCandidate(
        organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
        stable_key="rule:rule:heating-collision", candidate_type="RULE",
        subject_key=subject_key, field_key=None, operation="CREATE",
        proposed_value_json=_allocation_rule_payload("VALID"), confidence=Decimal("0.99"),
        risk_class="LOW", requires_individual_review=False, review_status="PENDING",
    )
    additional = DeclarationCandidate(
        organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
        stable_key="additional-rule:rule:heating-collision", candidate_type="RULE",
        subject_key=subject_key, field_key=None, operation="CREATE",
        proposed_value_json={
            **_allocation_rule_payload("VALID"),
            "title": "Zusatzinformation Heizkosten",
            "scope": {"additional_scope": [subject_key]},
        },
        confidence=Decimal("0.99"), risk_class="LOW",
        requires_individual_review=False, review_status="PENDING",
    )
    review_session.add_all([regular, additional])
    await review_session.flush()
    await _add_evidence(review_session, review_import.item, regular)
    await _add_evidence(review_session, review_import.item, additional)
    blockers = [
        DeclarationCandidateIssue(
            organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
            candidate_id=candidate.id, code="DUPLICATE_RULE_SUBJECT", severity="BLOCKER",
            message="Zwei Regelvorschläge betreffen denselben Gegenstand.",
            resolution_status="OPEN",
        )
        for candidate in (regular, additional)
    ]
    review_session.add_all(blockers)
    await review_session.commit()
    import_id = review_import.item.id
    regular_id = regular.id
    additional_id = additional.id

    first = await client_a_clerk.patch(
        f"/api/declaration-imports/{import_id}/candidates/{regular_id}",
        json={"draft_revision": 3, "decision": "ACCEPTED"},
    )
    assert first.status_code == 200
    review_session.expire_all()
    initially_open = (await review_session.scalars(
        select(DeclarationCandidateIssue).where(
            DeclarationCandidateIssue.import_id == import_id,
            DeclarationCandidateIssue.code == "DUPLICATE_RULE_SUBJECT",
        )
    )).all()
    assert {issue.resolution_status for issue in initially_open} == {"OPEN"}

    second = await client_a_clerk.patch(
        f"/api/declaration-imports/{import_id}/candidates/{additional_id}",
        json={
            "draft_revision": first.json()["draft_revision"],
            "decision": "REJECTED",
            "comment": "Doppelte Alternative verworfen.",
        },
    )
    assert second.status_code == 200
    review_session.expire_all()
    refreshed = (await review_session.scalars(
        select(DeclarationCandidateIssue).where(
            DeclarationCandidateIssue.import_id == import_id,
            DeclarationCandidateIssue.code == "DUPLICATE_RULE_SUBJECT",
        )
    )).all()
    assert len(refreshed) == 2
    assert {issue.resolution_status for issue in refreshed} == {"RESOLVED"}

    reopened = await client_a_clerk.patch(
        f"/api/declaration-imports/{import_id}/candidates/{additional_id}",
        json={"draft_revision": second.json()["draft_revision"], "decision": "ACCEPTED"},
    )
    assert reopened.status_code == 200
    review_session.expire_all()
    reopened_rows = (await review_session.scalars(
        select(DeclarationCandidateIssue).where(
            DeclarationCandidateIssue.import_id == import_id,
            DeclarationCandidateIssue.code == "DUPLICATE_RULE_SUBJECT",
        )
    )).all()
    assert {issue.resolution_status for issue in reopened_rows} == {"OPEN"}

@pytest.mark.asyncio
async def test_unrelated_correction_cannot_resolve_subject_fact_conflict(
    client_a_clerk, review_import, review_session: AsyncSession, seed_data: dict
) -> None:
    city_a = DeclarationCandidate(
        organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
        stable_key="fact:property:conflict:city:a", candidate_type="PROPERTY_FIELD",
        subject_key="property:conflict", field_key="city", operation="CREATE",
        proposed_value_json={"kind": "TEXT", "value": "Köln"}, confidence=Decimal("0.99"),
        risk_class="LOW", requires_individual_review=False, review_status="PENDING",
    )
    city_b = DeclarationCandidate(
        organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
        stable_key="fact:property:conflict:city:b", candidate_type="PROPERTY_FIELD",
        subject_key="property:conflict", field_key="city", operation="CREATE",
        proposed_value_json={"kind": "TEXT", "value": "Bonn"}, confidence=Decimal("0.99"),
        risk_class="LOW", requires_individual_review=False, review_status="PENDING",
    )
    street = DeclarationCandidate(
        organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
        stable_key="fact:property:conflict:street", candidate_type="PROPERTY_FIELD",
        subject_key="property:conflict", field_key="street", operation="CREATE",
        proposed_value_json={"kind": "TEXT", "value": "Alt"}, confidence=Decimal("0.99"),
        risk_class="LOW", requires_individual_review=False, review_status="PENDING",
    )
    review_session.add_all([city_a, city_b, street])
    await review_session.flush()
    await _add_evidence(review_session, review_import.item, street)
    review_session.add(DeclarationCandidateIssue(
        organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
        candidate_id=street.id, code="CONFLICTING_FACT_ALTERNATIVES", severity="BLOCKER",
        message="ambiguous city", resolution_status="OPEN",
    ))
    await review_session.commit()
    response = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{street.id}",
        json={"draft_revision": 3, "decision": "CORRECTED", "corrected_value": "Neu"},
    )
    assert response.status_code == 200
    issue = await review_session.scalar(select(DeclarationCandidateIssue).where(
        DeclarationCandidateIssue.import_id == review_import.item.id,
        DeclarationCandidateIssue.code == "CONFLICTING_FACT_ALTERNATIVES",
    ))
    assert issue is not None
    assert issue.resolution_status == "OPEN"


@pytest.mark.asyncio
async def test_approve_boundary_hides_foreign_and_rejects_incomplete_review(
    client_a_clerk, client_b_admin, review_import
) -> None:
    payload = {"draft_revision": 3, "idempotency_key": "0123456789abcdef"}
    foreign = await client_b_admin.post(
        f"/api/declaration-imports/{review_import.item.id}/approve", json=payload
    )
    assert foreign.status_code == 404
    local = await client_a_clerk.post(
        f"/api/declaration-imports/{review_import.item.id}/approve", json=payload
    )
    assert local.status_code == 409
    assert local.json()["detail"]["code"] == "OPEN_BLOCKER"


@pytest.mark.asyncio
async def test_every_comment_required_class_and_mismatched_candidate_are_hidden(
    client_a_clerk, review_import, review_session: AsyncSession, seed_data: dict
) -> None:
    second = DeclarationImport(
        organization_id=seed_data["org_a_id"], mode="NEW_PROPERTY", status="NEEDS_REVIEW",
        draft_revision=3, initiated_by=seed_data["user_a_admin_id"], max_cost_usd=Decimal("10"),
    )
    review_session.add(second)
    await review_session.flush()
    other = DeclarationCandidate(
        organization_id=seed_data["org_a_id"], import_id=second.id, stable_key="other",
        candidate_type="UNIT_FIELD", subject_key="unit:other", field_key="location",
        operation="CREATE", proposed_value_json={"kind": "TEXT", "value": "x"},
        confidence=Decimal("0.99"), risk_class="LOW", requires_individual_review=False,
        review_status="PENDING",
    )
    review_session.add(other)
    required = [
        ("UPDATE", "UNIT_FIELD", "UPDATE"), ("RULE", "RULE", "CREATE"),
        ("DOCUMENT_RELATION", "DOCUMENT_RELATION", "CREATE"),
        ("MEA_RESCALE", "MEA_RESCALE", "UPDATE"),
        ("WARNING", "UNIT_FIELD", "CREATE"), ("BLOCKER", "UNIT_FIELD", "CREATE"),
    ]
    rows = []
    for index, (label, candidate_type, operation) in enumerate(required):
        candidate = DeclarationCandidate(
            organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
            stable_key=f"comment:{index}", candidate_type=candidate_type,
            subject_key=f"subject:{index}", field_key="location", operation=operation,
            proposed_value_json={"kind": "TEXT", "value": "x"}, confidence=Decimal("0.99"),
            risk_class="LOW", requires_individual_review=False, review_status="PENDING",
        )
        review_session.add(candidate)
        rows.append((label, candidate))
    await review_session.flush()
    for label, candidate in rows:
        if label in {"WARNING", "BLOCKER"}:
            review_session.add(DeclarationCandidateIssue(
                organization_id=seed_data["org_a_id"], import_id=review_import.item.id,
                candidate_id=candidate.id, code=f"{label}_ISSUE", severity=label,
                message=label, resolution_status="OPEN",
            ))
    await review_session.commit()
    mismatch = await client_a_clerk.patch(
        f"/api/declaration-imports/{review_import.item.id}/candidates/{other.id}",
        json={"draft_revision": 3, "decision": "ACCEPTED"},
    )
    assert mismatch.status_code == 404
    for _label, candidate in rows:
        response = await client_a_clerk.patch(
            f"/api/declaration-imports/{review_import.item.id}/candidates/{candidate.id}",
            json={"draft_revision": 3, "decision": "REJECTED"},
        )
        assert response.status_code == 422
