"""Cost category endpoints -- read-only global chart of accounts."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db
from app.repositories.cost_category_repo import CostCategoryRepository
from app.models.cost_category import CostCategory

router = APIRouter(prefix="/api/cost-categories", tags=["cost-categories"])


class CostCategoryOut(BaseModel):
    id: str
    plan_version: int
    code: str
    label: str
    cost_group: str
    tenant_apportionability_default: str
    owner_billing_relevant_default: bool
    owner_distribution_key_default: str | None
    tenant_distribution_key_default: str | None
    reserve_relevant: bool
    legal_hint: str | None
    sort_order: int
    is_active: bool

    model_config = {"from_attributes": True}


@router.get("", response_model=list[CostCategoryOut])
async def list_cost_categories(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all active cost categories (all roles can read)."""
    repo = CostCategoryRepository(db)
    cats = await repo.list_active()
    return [_to_out(c) for c in cats]


def _to_out(c: CostCategory) -> CostCategoryOut:
    return CostCategoryOut(
        id=str(c.id),
        plan_version=c.plan_version,
        code=c.code,
        label=c.label,
        cost_group=c.cost_group,
        tenant_apportionability_default=c.tenant_apportionability_default,
        owner_billing_relevant_default=c.owner_billing_relevant_default,
        owner_distribution_key_default=c.owner_distribution_key_default,
        tenant_distribution_key_default=c.tenant_distribution_key_default,
        reserve_relevant=c.reserve_relevant,
        legal_hint=c.legal_hint,
        sort_order=c.sort_order,
        is_active=c.is_active,
    )
