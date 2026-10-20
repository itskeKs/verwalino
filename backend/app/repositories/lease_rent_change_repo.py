"""Tenant-filtered rent history, additionally constrained by parent-lease RLS."""
from __future__ import annotations

import uuid
from typing import Sequence

from app.models.lease_agreement import LeaseAgreement
from app.models.lease_rent_change import LeaseRentChange
from app.repositories.base import TenantRepository


class LeaseRentChangeRepository(TenantRepository[LeaseRentChange]):
    model = LeaseRentChange

    async def list_for_lease(self, lease_id: uuid.UUID) -> Sequence[LeaseRentChange]:
        # Keep the visible parent join as well as the explicit tenant filters:
        # a history row must never disclose a lease hidden by property/report scope.
        statement = self._base_query().join(LeaseAgreement,
            (LeaseAgreement.id == LeaseRentChange.lease_agreement_id)
            & (LeaseAgreement.organization_id == self._org_id),
        ).where(LeaseRentChange.lease_agreement_id == lease_id).order_by(LeaseRentChange.valid_from)
        return (await self._session.execute(statement)).scalars().all()
