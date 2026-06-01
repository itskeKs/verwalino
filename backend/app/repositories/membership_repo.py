"""Membership repository -- tenant-scoped."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership import Membership
from app.repositories.base import TenantRepository


class MembershipRepository(TenantRepository[Membership]):
    model = Membership

    async def get_by_user_id(self, user_id: uuid.UUID) -> Membership | None:
        stmt = self._base_query().where(Membership.user_id == user_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
