"""EconomicPlan / EconomicPlanLine repositories -- tenant-scoped.

m4-weg-abrechnung.md section 10.2. Mirrors settlement_repo: active-query
(superseded_by_id IS NULL), per-property listing, and the read-aggregation that
feeds the plan engine (prior-year Ist per category as a plan proposal).

Returns ORM rows; the service maps them to the engine's frozen dataclasses
(keeps the engine free of ORM, SDD section 16).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Sequence

from sqlalchemy import select, or_

from app.models.cost_category import CostCategory
from app.models.cost_entry import CostEntry
from app.models.economic_plan import EconomicPlan
from app.models.economic_plan_line import EconomicPlanLine
from app.repositories.base import TenantRepository


class EconomicPlanRepository(TenantRepository[EconomicPlan]):
    model = EconomicPlan

    async def list_active(self) -> Sequence[EconomicPlan]:
        """Plans that have not been superseded by a correction (E-4)."""
        stmt = (
            self._base_query()
            .where(EconomicPlan.superseded_by_id.is_(None))
            .order_by(EconomicPlan.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_by_property(self, property_id: uuid.UUID) -> Sequence[EconomicPlan]:
        stmt = (
            self._base_query()
            .where(EconomicPlan.property_id == property_id)
            .order_by(EconomicPlan.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def has_conflicting_period(self, plan: EconomicPlan) -> bool:
        stmt = (self._base_query()
            .where(EconomicPlan.property_id == plan.property_id,
                EconomicPlan.superseded_by_id.is_(None),
                EconomicPlan.period_start <= plan.period_end,
                EconomicPlan.period_end >= plan.period_start,
                or_(EconomicPlan.period_start != plan.period_start,
                    EconomicPlan.period_end != plan.period_end)))
        return (await self._session.execute(stmt.limit(1))).scalar_one_or_none() is not None

    async def update(self, entity: EconomicPlan) -> EconomicPlan:
        await self._session.flush()
        return entity

    async def delete(self, entity: EconomicPlan) -> None:
        """Delete a draft plan. Trigger blocks finalized plans."""
        await self._session.delete(entity)
        await self._session.flush()

    async def prior_year_sources(
        self, property_id: uuid.UUID, period_start: date, period_end: date,
    ) -> Sequence[tuple[CostEntry, CostCategory | None]]:
        """Complete current confirmed sources; admission must precede this query.

        Keep missing categories visible for fail-closed validation. Proration and
        reserve separation belong to the service, not an SQL gross sum.
        """
        result = await self._session.execute(
            select(CostEntry, CostCategory)
            .outerjoin(CostCategory, CostCategory.id == CostEntry.cost_category_id)
            .where(CostEntry.organization_id == self._org_id)
            .where(CostEntry.property_id == property_id)
            .where(CostEntry.status == "confirmed")
            .where(CostEntry.superseded_by_id.is_(None))
            .where(CostEntry.service_period_from <= period_end)
            .where(CostEntry.service_period_to >= period_start)
            .order_by(CostEntry.id)
            .execution_options(populate_existing=True)
        )
        return [(row[0], row[1]) for row in result.all()]


class EconomicPlanLineRepository(TenantRepository[EconomicPlanLine]):
    model = EconomicPlanLine

    async def list_for_plan(
        self, economic_plan_id: uuid.UUID
    ) -> Sequence[EconomicPlanLine]:
        stmt = (
            self._base_query()
            .where(EconomicPlanLine.economic_plan_id == economic_plan_id)
            .order_by(EconomicPlanLine.sort_order)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def delete_for_plan(self, economic_plan_id: uuid.UUID) -> None:
        """Delete all lines of a (draft) plan -- used before recompute."""
        lines = await self.list_for_plan(economic_plan_id)
        for line in lines:
            await self._session.delete(line)
        await self._session.flush()
