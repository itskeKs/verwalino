"""Organization endpoints -- minimal CRUD for M0."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db, require_admin_dep
from app.repositories.organization_repo import OrganizationRepository
from app.models.organization import Organization
from app.core.live_principal import get_live_member_db
from app.services.quota_service import quota_summary

router = APIRouter(prefix="/api/organizations", tags=["organizations"])
quota_router = APIRouter(prefix="/api/organization", tags=["organizations"])


class QuotaUsage(BaseModel):
    storage_bytes: int
    properties: int
    units: int
    ai_budget_usd: str


class QuotaLimits(BaseModel):
    storage_bytes: int | None
    upload_bytes: int | None
    properties: int | None
    units: int | None
    ai_budget_usd: str | None


class QuotaOut(BaseModel):
    plan: str
    usage: QuotaUsage
    limits: QuotaLimits


@quota_router.get("/quota", response_model=QuotaOut)
async def get_organization_quota(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    return await quota_summary(db, auth.organization_id)


class OrgOut(BaseModel):
    id: str
    name: str

    model_config = {"from_attributes": True}


@router.get("/me", response_model=OrgOut)
async def get_my_org(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current user's organization."""
    repo = OrganizationRepository(db)
    org = await repo.get_by_id(auth.organization_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return OrgOut(id=str(org.id), name=org.name)
