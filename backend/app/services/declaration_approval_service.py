"""One-transaction application of a reviewed declaration import."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.declarations.approval import (
    ApprovalConflict,
    build_approval_plan,
    build_existing_approval_plan,
)
from app.declarations.review_schemas import ApprovalRequest
from app.models.audit_event import AuditEvent
from app.repositories.audit_repo import AuditRepository
from app.repositories.declaration_import_repo import DeclarationImportRepository
from app.repositories.declaration_review_repo import DeclarationReviewRepository
from app.repositories.property_knowledge_repo import PropertyKnowledgeRepository
from app.repositories.property_repo import PropertyRepository
from app.repositories.unit_repo import UnitRepository


class DeclarationApprovalService:
    """Apply a NEW_PROPERTY review without committing the request session."""

    def __init__(self, session: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID):
        self._session = session
        self._organization_id = organization_id
        self._user_id = user_id
        self._imports = DeclarationImportRepository(session, organization_id)
        self._reviews = DeclarationReviewRepository(session, organization_id)
        self._knowledge = PropertyKnowledgeRepository(session, organization_id)
        self._audit = AuditRepository(session, organization_id)

    async def approve(self, import_id: uuid.UUID, request: ApprovalRequest) -> dict:
        item = await self._reviews.lock_import(import_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Declaration import not found")
        if item.status == "APPLIED":
            if item.approval_idempotency_key == request.idempotency_key and item.approval_result_json is not None:
                return item.approval_result_json
            raise _conflict("ALREADY_APPLIED")
        if item.status != "NEEDS_REVIEW":
            raise _conflict("IMPORT_NOT_REVIEWABLE")
        if item.draft_revision != request.draft_revision:
            raise _conflict("STALE_DRAFT_REVISION")

        candidates = await self._reviews.lock_all_candidates(import_id)
        evidence = await self._reviews.lock_evidence_for([candidate.id for candidate in candidates])
        issues = await self._reviews.issues_for_import(import_id, for_update=True)
        document_rows = await self._imports.lock_documents_for_approval(import_id)
        documents = [document for _link, document in document_rows]
        try:
            if item.mode == "EXISTING_PROPERTY":
                if item.target_property_id is None:
                    raise ApprovalConflict("EXISTING_PROPERTY_TARGET_REQUIRED")
                properties = PropertyRepository(
                    self._session, self._organization_id
                )
                property_row = await properties.get_by_id_for_update(item.target_property_id)
                if property_row is None:
                    raise ApprovalConflict("TARGET_PROPERTY_NOT_FOUND")
                units = await UnitRepository(self._session, self._organization_id).list_by_property_for_update(
                    property_row.id
                )
                rules = await properties.list_effective_rules_for_update(property_row.id)
                existing_assignments = await properties.list_assignments_for_update(
                    property_row.id
                )
                existing_document_relations = await properties.list_document_relations_for_update(
                    property_row.id
                )
                plan = build_existing_approval_plan(
                    item, candidates, evidence, issues, documents, property_row, units, rules,
                    existing_assignments, existing_document_relations,
                )
            else:
                plan = build_approval_plan(item, candidates, evidence, issues, documents)
        except ApprovalConflict as exc:
            raise _conflict(exc.code) from exc

        approved_at = datetime.now(timezone.utc)
        if item.mode == "EXISTING_PROPERTY":
            await self._knowledge.apply_existing(plan, self._user_id, approved_at, documents)
        else:
            await self._knowledge.apply(plan, self._user_id, approved_at, documents)
        result = plan.response(item.draft_revision)
        if item.mode == "EXISTING_PROPERTY" and plan.mea_scale_confirmed:
            await self._audit.create(AuditEvent(
                organization_id=self._organization_id,
                actor_user_id=self._user_id,
                entity_type="property",
                entity_id=plan.property_id,
                action="property.mea_scale_confirmed",
                payload_diff={
                    "old_scale": {
                        "mea_scale_status": plan.previous_mea_scale_status,
                        "mea_denominator": plan.previous_mea_denominator,
                    },
                    "new_scale": {
                        "mea_scale_status": "CONFIRMED",
                        "mea_denominator": plan.mea_denominator,
                    },
                    "existing_values_confirmed_as_numerators": True,
                },
            ))
        item.approved_by = self._user_id
        item.approved_at = approved_at
        item.approval_idempotency_key = request.idempotency_key
        item.applied_property_id = plan.property_id
        item.approval_result_json = result
        item.status = "APPLIED"
        item.current_stage = "applied"
        await self._audit.create(AuditEvent(
            organization_id=self._organization_id,
            actor_user_id=self._user_id,
            entity_type="declaration_import",
            entity_id=item.id,
            action="declaration_import.applied",
            payload_diff={
                "import_id": str(item.id),
                "property_id": str(plan.property_id),
                "summary": plan.summary,
            },
        ))
        await self._session.flush()
        return result


def _conflict(code: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code})
