"""CostCategory repository -- global, read-only.

No organization_id filter (global system data).
Only read operations; writes happen through migrations.
"""

from __future__ import annotations

from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost_category import CostCategory


class CostCategoryRepository:
    """Read-only repository for the global cost category table."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_active(self, plan_version: int = 1) -> Sequence[CostCategory]:
        """List all active categories for a given plan version."""
        stmt = (
            select(CostCategory)
            .where(CostCategory.plan_version == plan_version)
            .where(CostCategory.is_active.is_(True))
            .order_by(CostCategory.sort_order)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_by_id(self, category_id) -> CostCategory | None:
        stmt = select(CostCategory).where(CostCategory.id == category_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_code(
        self, code: str, plan_version: int = 1
    ) -> CostCategory | None:
        stmt = (
            select(CostCategory)
            .where(CostCategory.code == code)
            .where(CostCategory.plan_version == plan_version)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
