"""Locked persistence primitives for revision-safe declaration reviews."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.declaration_candidate import (
    DeclarationCandidate,
    DeclarationCandidateEvidence,
    DeclarationCandidateIssue,
)
from app.models.declaration_import import DeclarationImport
from app.repositories.declaration_import_repo import DeclarationImportRepository


class DeclarationReviewRepository:
    """Uses the caller transaction; it never commits or creates a session."""

    def __init__(self, session: AsyncSession, organization_id: uuid.UUID):
        self._session = session
        self._organization_id = organization_id
        self._imports = DeclarationImportRepository(session, organization_id)

    async def lock_import(self, import_id: uuid.UUID, *, capability: str = 'read') -> DeclarationImport | None:
        await self._imports.lock_acl()
        if not await self._session.scalar(text('SELECT property_import_runtime_access(:org,:job,:capability)'),
                {'org': self._organization_id, 'job': import_id, 'capability': capability}):
            return None
        return await self._imports.get_by_id_for_update(import_id)

    async def lock_candidates(
        self, import_id: uuid.UUID, candidate_ids: Sequence[uuid.UUID]
    ) -> list[DeclarationCandidate]:
        ids = sorted(set(candidate_ids), key=str)
        if not ids:
            return []
        result = await self._session.execute(
            select(DeclarationCandidate)
            .where(DeclarationCandidate.organization_id == self._organization_id)
            .where(DeclarationCandidate.import_id == import_id)
            .where(DeclarationCandidate.id.in_(ids))
            .order_by(DeclarationCandidate.id)
            .with_for_update()
        )
        rows = result.scalars().all()
        return rows if len(rows) == len(ids) else []

    async def list_candidates(self, import_id: uuid.UUID) -> list[DeclarationCandidate]:
        result = await self._session.execute(
            select(DeclarationCandidate)
            .where(DeclarationCandidate.organization_id == self._organization_id)
            .where(DeclarationCandidate.import_id == import_id)
            .order_by(DeclarationCandidate.stable_key, DeclarationCandidate.id)
        )
        return result.scalars().all()

    async def lock_all_candidates(self, import_id: uuid.UUID) -> list[DeclarationCandidate]:
        result = await self._session.execute(
            select(DeclarationCandidate)
            .where(DeclarationCandidate.organization_id == self._organization_id)
            .where(DeclarationCandidate.import_id == import_id)
            .order_by(DeclarationCandidate.stable_key, DeclarationCandidate.id)
            .with_for_update()
        )
        return result.scalars().all()

    async def evidence_for(self, candidate_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[DeclarationCandidateEvidence]]:
        if not candidate_ids:
            return {}
        result = await self._session.execute(
            select(DeclarationCandidateEvidence)
            .where(DeclarationCandidateEvidence.organization_id == self._organization_id)
            .where(DeclarationCandidateEvidence.candidate_id.in_(candidate_ids))
            .order_by(DeclarationCandidateEvidence.candidate_id, DeclarationCandidateEvidence.id)
        )
        values: dict[uuid.UUID, list[DeclarationCandidateEvidence]] = {}
        for row in result.scalars():
            values.setdefault(row.candidate_id, []).append(row)
        return values

    async def lock_evidence_for(
        self, candidate_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[DeclarationCandidateEvidence]]:
        if not candidate_ids:
            return {}
        result = await self._session.execute(
            select(DeclarationCandidateEvidence)
            .where(DeclarationCandidateEvidence.organization_id == self._organization_id)
            .where(DeclarationCandidateEvidence.candidate_id.in_(candidate_ids))
            .order_by(DeclarationCandidateEvidence.candidate_id, DeclarationCandidateEvidence.id)
            .with_for_update()
        )
        values: dict[uuid.UUID, list[DeclarationCandidateEvidence]] = {}
        for row in result.scalars():
            values.setdefault(row.candidate_id, []).append(row)
        return values

    async def issues_for_import(
        self, import_id: uuid.UUID, *, for_update: bool = False
    ) -> list[DeclarationCandidateIssue]:
        statement = (
            select(DeclarationCandidateIssue)
            .where(DeclarationCandidateIssue.organization_id == self._organization_id)
            .where(DeclarationCandidateIssue.import_id == import_id)
            .order_by(DeclarationCandidateIssue.created_at, DeclarationCandidateIssue.id)
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalars().all()
