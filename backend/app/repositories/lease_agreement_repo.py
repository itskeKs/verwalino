"""LeaseAgreement repository -- tenant-scoped with temporal queries."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Sequence

from sqlalchemy import select, and_, or_

from app.models.lease_agreement import LeaseAgreement
from app.models.unit import Unit
from app.models.person import Person
from app.models.tenant_profile import TenantProfile
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

    async def get_by_id(self, lease_id: uuid.UUID) -> LeaseAgreement | None:
        """Get a single lease agreement by ID."""
        stmt = self._base_query().where(LeaseAgreement.id == lease_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def has_booked_payments(self, lease_id: uuid.UUID) -> bool:
        """Check if lease has any booked payments."""
        from app.models.payment import Payment
        stmt = select(Payment.id).where(
            Payment.organization_id == self._org_id,
            Payment.lease_agreement_id == lease_id,
            Payment.status == "booked",
        ).limit(1)
        return await self._session.scalar(stmt) is not None

    async def has_finalized_settlement(self, lease_id: uuid.UUID) -> bool:
        """Check if lease has a finalized settlement."""
        from app.models.settlement_run import SettlementRun
        stmt = select(SettlementRun.id).where(
            SettlementRun.organization_id == self._org_id,
            SettlementRun.lease_agreement_id == lease_id,
            SettlementRun.status == "finalized",
        ).limit(1)
        return await self._session.scalar(stmt) is not None

    async def finalized_derived_statements_ending_after(self, lease_id: uuid.UUID, last_day: date):
        """Active derived statements ending after the proposed last occupied day."""
        from app.models.settlement_run import SettlementRun
        stmt = select(SettlementRun).where(
            SettlementRun.organization_id == self._org_id,
            SettlementRun.lease_agreement_id == lease_id,
            SettlementRun.status == "finalized", SettlementRun.superseded_by_id.is_(None),
            SettlementRun.advance_payments_source == "DERIVED",
            SettlementRun.period_end > last_day,
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def matching_targets(self, property_id, period_start, period_end):
        statement = select(LeaseAgreement, Person).join(Unit, and_(
            Unit.id == LeaseAgreement.unit_id, Unit.organization_id == self._org_id,
        )).join(TenantProfile, and_(
            TenantProfile.id == LeaseAgreement.tenant_profile_id,
            TenantProfile.organization_id == self._org_id,
        )).join(Person, and_(
            Person.id == TenantProfile.person_id, Person.organization_id == self._org_id,
        )).where(
            LeaseAgreement.organization_id == self._org_id, Unit.property_id == property_id,
            LeaseAgreement.valid_from <= period_end,
            or_(LeaseAgreement.valid_to.is_(None), LeaseAgreement.valid_to > period_start),
        ).order_by(LeaseAgreement.id)
        return (await self._session.execute(statement)).all()
