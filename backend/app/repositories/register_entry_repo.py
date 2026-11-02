"""ResolutionRegisterEntry repository -- tenant-scoped.

Extends TenantRepository with queries for the Beschluss-Sammlung (M8).
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resolution_register_entry import ResolutionRegisterEntry
from app.models.resolution import Resolution
from app.repositories.base import TenantRepository


class RegisterEntryRepository(TenantRepository[ResolutionRegisterEntry]):
    model = ResolutionRegisterEntry

    async def resolution_results(self, entries):
        identifiers = {entry.resolution_id for entry in entries}
        if not identifiers:
            return {}
        rows = await self._session.execute(select(Resolution.id, Resolution.result).where(
            Resolution.organization_id == self._org_id, Resolution.id.in_(identifiers)))
        return dict(rows.all())

    async def list_for_property(
        self, property_id: uuid.UUID
    ) -> Sequence[ResolutionRegisterEntry]:
        """List all register entries for a WEG property, ordered by sequence."""
        stmt = (
            self._base_query()
            .where(ResolutionRegisterEntry.property_id == property_id)
            .order_by(ResolutionRegisterEntry.sequence_number)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def next_sequence_number(self, property_id: uuid.UUID) -> int:
        """Return the next sequence number for a property (max+1, or 1 if empty).

        IMPORTANT: this must be called within the same transaction that INSERTs
        the new entry. The UNIQUE constraint (org, property, seq) prevents
        duplicates; on collision the DB raises IntegrityError -> retry.
        """
        stmt = (
            select(func.coalesce(func.max(ResolutionRegisterEntry.sequence_number), 0))
            .where(ResolutionRegisterEntry.organization_id == self._org_id)
            .where(ResolutionRegisterEntry.property_id == property_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one() + 1

    async def update(self, entity: ResolutionRegisterEntry) -> ResolutionRegisterEntry:
        """Flush changes. Trigger blocks all mutations except challenge_annotation."""
        await self._session.flush()
        return entity

    async def get_by_resolution_id(
        self, resolution_id: uuid.UUID
    ) -> ResolutionRegisterEntry | None:
        """Look up a register entry by its resolution_id."""
        stmt = self._base_query().where(
            ResolutionRegisterEntry.resolution_id == resolution_id
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
