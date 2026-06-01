"""LeaseAgreement repository -- tenant-scoped with temporal queries."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Sequence

from sqlalchemy import select, and_, or_

from app.models.lease_agreement import LeaseAgreement
from app.repositories.base import TenantRepository


class LeaseAgreementRepository(TenantRepository[LeaseAgreement]):
    model = LeaseAgreement

    async def list_by_unit(self, unit_id: uuid.UUID) -> Sequence[LeaseAgreement]:
        stmt = self._base_query().where(
            LeaseAgreement.unit_id == unit_id
        ).order_by(LeaseAgreement.valid_from)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def tenant_at_date(
        self, unit_id: uuid.UUID, target_date: date
    ) -> LeaseAgreement | None:
        """Point-in-time query: who was the tenant of the unit on target_date?

        Returns the lease agreement where target_date falls within
        [valid_from, valid_to).  If valid_to is NULL, lease is open-ended.
        At most one result (enforced by EXCLUDE constraint).
        """
        stmt = self._base_query().where(
            and_(
                LeaseAgreement.unit_id == unit_id,
                LeaseAgreement.valid_from <= target_date,
                or_(
                    LeaseAgreement.valid_to.is_(None),
                    LeaseAgreement.valid_to > target_date,
                ),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
