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

from sqlalchemy import and_, func, select

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

    async def update(self, entity: EconomicPlan) -> EconomicPlan:
        await self._session.flush()
        return entity

    async def delete(self, entity: EconomicPlan) -> None:
        """Delete a draft plan. Trigger blocks finalized plans."""
        await self._session.delete(entity)
        await self._session.flush()

    async def prior_year_actuals_by_category(
        self,
        property_id: uuid.UUID,
        period_start: date,
        period_end: date,
    ) -> Sequence[tuple[str, str, object]]:
        """Sum of active+confirmed cost_entry gross per category in a period.

        A plan PROPOSAL helper (m4 section 5.1): the prior year's Ist per
        category. Returns (category_code, category_label, sum_gross). The clerk
        confirms/overrides the planned value; this is only a starting point.
        """
        stmt = (
            select(
                CostCategory.code,
                CostCategory.label,
                func.coalesce(func.sum(CostEntry.gross_amount), 0),
            )
            .join(CostCategory, CostCategory.id == CostEntry.cost_category_id)
            .where(CostEntry.organization_id == self._org_id)
            .where(CostEntry.property_id == property_id)
            .where(CostEntry.status == "confirmed")
            .where(CostEntry.superseded_by_id.is_(None))
            .where(CostEntry.service_period_from <= period_end)
            .where(CostEntry.service_period_to >= period_start)
            .group_by(CostCategory.code, CostCategory.label, CostCategory.sort_order)
            .order_by(CostCategory.sort_order)
        )
        result = await self._session.execute(stmt)
        return [(row[0], row[1], row[2]) for row in result.all()]


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
