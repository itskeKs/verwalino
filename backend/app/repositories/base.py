"""Base repository with explicit organization_id filtering.

Architecture.md section 2.3 (Ebene 3):
  - No direct table access from services; all tenant data goes through repos.
  - Repos filter on organization_id explicitly (in addition to RLS)
    for performance (index usage) and clarity.
  - RLS is the safety net; the explicit filter makes the code auditable.
"""

from __future__ import annotations

import uuid
from typing import Generic, TypeVar, Sequence

from sqlalchemy import select, Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base

T = TypeVar("T", bound=Base)


class TenantRepository(Generic[T]):
    """Base repository for tenant-scoped entities."""

    model: type[T]

    def __init__(self, session: AsyncSession, org_id: uuid.UUID):
        self._session = session
        self._org_id = org_id

    def _base_query(self) -> Select:
        """Return a SELECT already filtered by organization_id."""
        return select(self.model).where(
            self.model.organization_id == self._org_id  # type: ignore[attr-defined]
        )

    async def get_by_id(self, entity_id: uuid.UUID) -> T | None:
        stmt = self._base_query().where(self.model.id == entity_id)  # type: ignore[attr-defined]
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self) -> Sequence[T]:
        result = await self._session.execute(self._base_query())
        return result.scalars().all()

    async def create(self, entity: T) -> T:
        # Ensure org_id is set correctly
        entity.organization_id = self._org_id  # type: ignore[attr-defined]
        self._session.add(entity)
        await self._session.flush()
        return entity
