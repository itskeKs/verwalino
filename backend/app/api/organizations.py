"""Organization endpoints -- minimal CRUD for M0."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db, require_admin_dep
from app.repositories.organization_repo import OrganizationRepository
from app.models.organization import Organization

router = APIRouter(prefix="/api/organizations", tags=["organizations"])


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
