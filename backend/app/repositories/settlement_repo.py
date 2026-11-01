"""SettlementRun / SettlementLine repositories -- tenant-scoped.

Also provides the read-aggregation that feeds the engine: the active+confirmed
cost_entry chain overlapping the settlement period, plus the units / leases /
ownership of a property (m3-abrechnungs-engine.md section 10.2).

The repo returns ORM rows; the service maps them to the engine's frozen
dataclasses (keeps the engine free of ORM, SDD section 16).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Sequence

from sqlalchemy import and_, func, or_, select

from app.models.accounting_scope import AccountingScopeBinding
from app.models.cost_category import CostCategory
from app.models.cost_entry import CostEntry
from app.models.lease_agreement import LeaseAgreement
from app.models.owner_profile import OwnerProfile
from app.models.ownership_history import OwnershipHistory
from app.models.person import Person
from app.models.settlement_line import SettlementLine
from app.models.settlement_run import SettlementRun
from app.models.unit import Unit
from app.repositories.base import TenantRepository


class SettlementRunRepository(TenantRepository[SettlementRun]):
    model = SettlementRun

    async def list_active(self) -> Sequence[SettlementRun]:
        """Runs that have not been superseded by a correction (E-4)."""
        stmt = (
            self._base_query()
            .where(SettlementRun.superseded_by_id.is_(None))
            .order_by(SettlementRun.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_by_property(self, property_id: uuid.UUID) -> Sequence[SettlementRun]:
        stmt = (
            self._base_query()
            .where(SettlementRun.property_id == property_id)
            .order_by(SettlementRun.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def update(self, entity: SettlementRun) -> SettlementRun:
        await self._session.flush()
        return entity

    async def finalized_bk_for_advance_years(
        self, lease_id: uuid.UUID, period_start: date, period_end: date,
    ) -> Sequence[SettlementRun]:
        """Other active frozen windows sharing an assigned year with this run."""
        stmt = (self._base_query().where(
            SettlementRun.lease_agreement_id == lease_id,
            SettlementRun.run_type == "BK_MIETER", SettlementRun.status == "finalized",
            SettlementRun.superseded_by_id.is_(None),
            or_(SettlementRun.period_end < period_start, SettlementRun.period_start > period_end),
            SettlementRun.period_end >= date(period_start.year, 1, 1),
            SettlementRun.period_start <= date(period_end.year, 12, 31),
        ).order_by(SettlementRun.period_start, SettlementRun.period_end, SettlementRun.created_at.desc()))
        return (await self._session.execute(stmt)).scalars().all()

    async def delete(self, entity: SettlementRun) -> None:
        """Delete a draft run. Trigger blocks finalized runs."""
        await self._session.delete(entity)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Engine input aggregation (read-only)
    # ------------------------------------------------------------------
    async def active_cost_entries_for_period(
        self,
        property_id: uuid.UUID,
        period_start: date,
        period_end: date,
    ) -> Sequence[tuple[CostEntry, CostCategory | None]]:
        """Active + confirmed cost entries of a property overlapping the period.

        Active chain = status='confirmed' AND superseded_by_id IS NULL
        (m2 section 3.4). Overlap = service_period intersects the settlement
        period. Joined to cost_category (for the prueffest code/label).
        """
        stmt = (
            select(CostEntry, CostCategory)
            .outerjoin(
                CostCategory,
                and_(
                    CostCategory.id == CostEntry.cost_category_id,
                ),
            )
            .where(CostEntry.organization_id == self._org_id)
            .where(CostEntry.property_id == property_id)
            .where(CostEntry.status == "confirmed")
            .where(CostEntry.superseded_by_id.is_(None))
            # overlap: service_period_from <= period_end AND service_period_to >= period_start
            .where(CostEntry.service_period_from <= period_end)
            .where(CostEntry.service_period_to >= period_start)
            .order_by(CostEntry.service_period_from)
        )
        result = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def units_for_property(self, property_id: uuid.UUID) -> Sequence[Unit]:
        stmt = (
            self._base_query_for(Unit)
            .where(Unit.property_id == property_id)
            .order_by(Unit.number)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def leases_for_property(self, property_id: uuid.UUID) -> Sequence[LeaseAgreement]:
        stmt = (
            select(LeaseAgreement)
            .join(Unit, Unit.id == LeaseAgreement.unit_id)
            .where(LeaseAgreement.organization_id == self._org_id)
            .where(Unit.property_id == property_id)
            .order_by(LeaseAgreement.valid_from)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def leases_for_period(
        self, property_id: uuid.UUID, period_start: date, period_end: date,
    ) -> Sequence[LeaseAgreement]:
        """DB [valid_from, valid_to) overlaps an inclusive settlement period."""
        stmt = (
            select(LeaseAgreement)
            .join(Unit, Unit.id == LeaseAgreement.unit_id)
            .where(LeaseAgreement.organization_id == self._org_id)
            .where(Unit.organization_id == self._org_id, Unit.property_id == property_id)
            .where(LeaseAgreement.valid_from <= period_end)
            .where(or_(LeaseAgreement.valid_to.is_(None), LeaseAgreement.valid_to > period_start))
            .where(func.unit_in_financial_context(self._org_id, Unit.id))
            .order_by(LeaseAgreement.valid_from, LeaseAgreement.id)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def active_bk_for_period(
        self, property_id: uuid.UUID, period_start: date, period_end: date,
        scope_id: uuid.UUID | None,
    ) -> Sequence[SettlementRun]:
        """Active intersecting runs; the caller matches each lease's clipped dates."""
        binding = select(AccountingScopeBinding.id).where(
            AccountingScopeBinding.organization_id == self._org_id,
            AccountingScopeBinding.settlement_run_id == SettlementRun.id,
        )
        context = (~binding.exists() if scope_id is None else
            binding.where(AccountingScopeBinding.scope_id == scope_id).exists())
        stmt = (
            self._base_query()
            .where(SettlementRun.property_id == property_id, SettlementRun.run_type == "BK_MIETER")
            .where(SettlementRun.period_start <= period_end, SettlementRun.period_end >= period_start)
            .where(SettlementRun.superseded_by_id.is_(None), context)
            .order_by(SettlementRun.created_at, SettlementRun.id)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def has_conflicting_period(self, run: SettlementRun, scope_id: uuid.UUID | None = None) -> bool:
        """A visible finalized period overlaps this run with different bounds."""
        stmt = (self._base_query()
            .where(SettlementRun.property_id == run.property_id,
                SettlementRun.lease_agreement_id == run.lease_agreement_id,
                SettlementRun.run_type == run.run_type,
                SettlementRun.status == "finalized",
                SettlementRun.superseded_by_id.is_(None),
                SettlementRun.period_start <= run.period_end,
                SettlementRun.period_end >= run.period_start,
                or_(SettlementRun.period_start != run.period_start,
                    SettlementRun.period_end != run.period_end)))
        if run.run_type == "WEG_JAHRESABRECHNUNG":
            binding = select(AccountingScopeBinding.id).where(
                AccountingScopeBinding.organization_id == self._org_id,
                AccountingScopeBinding.settlement_run_id == SettlementRun.id,
            )
            stmt = stmt.where(~binding.exists() if scope_id is None else
                binding.where(AccountingScopeBinding.scope_id == scope_id).exists())
        return (await self._session.execute(stmt.limit(1))).scalar_one_or_none() is not None

    async def ownership_for_property(
        self, property_id: uuid.UUID
    ) -> Sequence[OwnershipHistory]:
        stmt = (
            select(OwnershipHistory)
            .join(Unit, Unit.id == OwnershipHistory.unit_id)
            .where(OwnershipHistory.organization_id == self._org_id)
            .where(Unit.property_id == property_id)
            .order_by(OwnershipHistory.valid_from)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def owner_profiles_for_property(
        self, property_id: uuid.UUID
    ) -> Sequence[tuple[OwnerProfile, Person | None]]:
        """Distinct owner_profiles owning a unit of a property + their person.

        M4 (m4 section 10.2): for resolving per-owner display names in the WEG
        PDF (names, not UUIDs). Joins ownership_history (any record) -> the owner
        profile -> the person. DISTINCT on owner profile id.
        """
        stmt = (
            select(OwnerProfile, Person)
            .join(OwnershipHistory, OwnershipHistory.owner_profile_id == OwnerProfile.id)
            .join(Unit, Unit.id == OwnershipHistory.unit_id)
            .outerjoin(Person, Person.id == OwnerProfile.person_id)
            .where(OwnerProfile.organization_id == self._org_id)
            .where(Unit.property_id == property_id)
            .distinct(OwnerProfile.id)
        )
        result = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    def _base_query_for(self, model):
        return select(model).where(model.organization_id == self._org_id)


class SettlementLineRepository(TenantRepository[SettlementLine]):
    model = SettlementLine

    async def list_for_run(
        self, settlement_run_id: uuid.UUID
    ) -> Sequence[SettlementLine]:
        stmt = (
            self._base_query()
            .where(SettlementLine.settlement_run_id == settlement_run_id)
            .order_by(SettlementLine.sort_order)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def delete_for_run(self, settlement_run_id: uuid.UUID) -> None:
        """Delete all lines of a (draft) run -- used before recompute."""
        lines = await self.list_for_run(settlement_run_id)
        for line in lines:
            await self._session.delete(line)
        await self._session.flush()
