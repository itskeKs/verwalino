"""Tenant-scoped repository for declaration review candidates."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from sqlalchemy import delete, select

from app.models.declaration_candidate import (
    DeclarationCandidate,
    DeclarationCandidateEvidence,
    DeclarationCandidateIssue,
)
from app.repositories.base import TenantRepository


class DeclarationCandidateRepository(TenantRepository[DeclarationCandidate]):
    model = DeclarationCandidate

    async def list_by_import(self, import_id: uuid.UUID) -> Sequence[DeclarationCandidate]:
        result = await self._session.execute(
            self._base_query()
            .where(DeclarationCandidate.import_id == import_id)
            .order_by(DeclarationCandidate.stable_key)
        )
        return result.scalars().all()

    async def replace_for_import(
        self,
        import_id: uuid.UUID,
        candidates: Sequence["DraftCandidate"],
        issues: Sequence["DraftIssue"],
    ) -> None:
        """Replace only the current pending graph in the caller transaction.

        Reviewed rows are legal review history.  A key collision with one is a
        hard failure rather than an implicit rename or deletion.
        """
        proposed_keys = {candidate.stable_key for candidate in candidates}
        if len(proposed_keys) != len(candidates):
            raise ValueError("DUPLICATE_PROPOSED_STABLE_KEY")
        unknown_issue_keys = {
            issue.candidate_stable_key
            for issue in issues
            if issue.candidate_stable_key is not None and issue.candidate_stable_key not in proposed_keys
        }
        if unknown_issue_keys:
            raise ValueError("UNKNOWN_DRAFT_ISSUE_REFERENCE")
        protected = await self._session.execute(
            select(DeclarationCandidate.stable_key)
            .where(DeclarationCandidate.organization_id == self._org_id)
            .where(DeclarationCandidate.import_id == import_id)
            .where(DeclarationCandidate.review_status != "PENDING")
            .where(DeclarationCandidate.stable_key.in_(proposed_keys))
            .with_for_update()
        )
        if protected.scalars().first() is not None:
            raise ValueError("PROTECTED_REVIEWED_CANDIDATE_COLLISION")

        pending_ids = select(DeclarationCandidate.id).where(
            DeclarationCandidate.organization_id == self._org_id,
            DeclarationCandidate.import_id == import_id,
            DeclarationCandidate.review_status == "PENDING",
        )
        await self._session.execute(
            delete(DeclarationCandidateEvidence).where(
                DeclarationCandidateEvidence.organization_id == self._org_id,
                DeclarationCandidateEvidence.candidate_id.in_(pending_ids),
            )
        )
        await self._session.execute(
            delete(DeclarationCandidateIssue).where(
                DeclarationCandidateIssue.organization_id == self._org_id,
                DeclarationCandidateIssue.candidate_id.in_(pending_ids),
            )
        )
        await self._session.execute(
            delete(DeclarationCandidateIssue).where(
                DeclarationCandidateIssue.organization_id == self._org_id,
                DeclarationCandidateIssue.import_id == import_id,
                DeclarationCandidateIssue.candidate_id.is_(None),
                DeclarationCandidateIssue.resolution_status == "OPEN",
            )
        )
        await self._session.execute(
            delete(DeclarationCandidate).where(
                DeclarationCandidate.organization_id == self._org_id,
                DeclarationCandidate.import_id == import_id,
                DeclarationCandidate.review_status == "PENDING",
            )
        )

        by_key: dict[str, DeclarationCandidate] = {}
        for draft in candidates:
            item = DeclarationCandidate(
                organization_id=self._org_id,
                import_id=import_id,
                stable_key=draft.stable_key,
                candidate_type=draft.candidate_type,
                subject_key=draft.subject_key,
                field_key=draft.field_key,
                operation=draft.operation,
                previous_value_json=draft.previous_value_json,
                proposed_value_json=draft.proposed_value_json,
                confidence=draft.confidence,
                risk_class=draft.risk_class,
                requires_individual_review=draft.requires_individual_review,
                review_status="PENDING",
            )
            self._session.add(item)
            by_key[draft.stable_key] = item
        await self._session.flush()
        for draft in candidates:
            item = by_key[draft.stable_key]
            for evidence in draft.evidence:
                self._session.add(
                    DeclarationCandidateEvidence(
                        organization_id=self._org_id,
                        candidate_id=item.id,
                        document_id=evidence.document_id,
                        field_path=evidence.field_path,
                        pdf_page=evidence.pdf_page,
                        printed_page=evidence.printed_page,
                        section_ref=evidence.section_ref,
                        quote=evidence.quote,
                    )
                )
        for issue in issues:
            candidate = by_key.get(issue.candidate_stable_key) if issue.candidate_stable_key else None
            self._session.add(
                DeclarationCandidateIssue(
                    organization_id=self._org_id,
                    import_id=import_id,
                    candidate_id=candidate.id if candidate is not None else None,
                    code=issue.code,
                    severity=issue.severity,
                    message=issue.message,
                    details_json=issue.details_json,
                    resolution_status="OPEN",
                )
            )
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class DraftEvidence:
    document_id: uuid.UUID
    pdf_page: int
    printed_page: str | None
    section_ref: str | None
    quote: str
    field_path: str | None = None


@dataclass(frozen=True, slots=True)
class DraftCandidate:
    stable_key: str
    candidate_type: str
    subject_key: str
    field_key: str | None
    operation: str
    previous_value_json: dict | None
    proposed_value_json: dict | None
    confidence: Decimal
    risk_class: str
    requires_individual_review: bool
    evidence: tuple[DraftEvidence, ...]


@dataclass(frozen=True, slots=True)
class DraftIssue:
    code: str
    severity: str
    message: str
    candidate_stable_key: str | None = None
    details_json: dict | None = None
