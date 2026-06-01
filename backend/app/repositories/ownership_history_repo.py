"""OwnershipHistory repository -- tenant-scoped with temporal queries."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Sequence

from sqlalchemy import select, and_, or_

from app.models.ownership_history import OwnershipHistory
from app.repositories.base import TenantRepository


class OwnershipHistoryRepository(TenantRepository[OwnershipHistory]):
    model = OwnershipHistory

    async def list_by_unit(self, unit_id: uuid.UUID) -> Sequence[OwnershipHistory]:
        stmt = self._base_query().where(
            OwnershipHistory.unit_id == unit_id
        ).order_by(OwnershipHistory.valid_from)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def owners_at_date(
        self, unit_id: uuid.UUID, target_date: date
    ) -> Sequence[OwnershipHistory]:
        """Point-in-time query: who owned the unit on target_date?

        Returns all ownership records where target_date falls within
        [valid_from, valid_to).  If valid_to is NULL, ownership is open-ended.
        """
        stmt = self._base_query().where(
            and_(
                OwnershipHistory.unit_id == unit_id,
                OwnershipHistory.valid_from <= target_date,
                or_(
                    OwnershipHistory.valid_to.is_(None),
                    OwnershipHistory.valid_to > target_date,
                ),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()
