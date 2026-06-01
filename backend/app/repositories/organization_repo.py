"""Organization repository -- global (no tenant scoping)."""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization


class OrganizationRepository:
    """Organization is the tenant root -- no org_id filter."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, org_id: uuid.UUID) -> Organization | None:
        result = await self._session.execute(
            select(Organization).where(Organization.id == org_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> Sequence[Organization]:
        result = await self._session.execute(select(Organization))
        return result.scalars().all()

    async def create(self, org: Organization) -> Organization:
        self._session.add(org)
        await self._session.flush()
        return org
