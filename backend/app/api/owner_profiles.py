"""OwnerProfile endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import (
    AuthContext,
    get_current_user,
    get_db,
    require_write_dep,
)
from app.repositories.owner_profile_repo import OwnerProfileRepository
from app.models.owner_profile import OwnerProfile

router = APIRouter(prefix="/api/owner-profiles", tags=["owner-profiles"])


class OwnerProfileOut(BaseModel):
    id: str
    person_id: str
    notes: str

    model_config = {"from_attributes": True}


class OwnerProfileCreate(BaseModel):
    person_id: str
    notes: str = ""


@router.get("", response_model=list[OwnerProfileOut])
async def list_owner_profiles(
    person_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = OwnerProfileRepository(db, auth.organization_id)
    items = await repo.list_all()
    if person_id:
        items = [i for i in items if i.person_id == person_id]
    return [_to_out(p) for p in items]


@router.post("", response_model=OwnerProfileOut, status_code=201)
async def create_owner_profile(
    body: OwnerProfileCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    repo = OwnerProfileRepository(db, auth.organization_id)
    profile = OwnerProfile(
        person_id=uuid.UUID(body.person_id),
        notes=body.notes,
    )
    profile = await repo.create(profile)
    return _to_out(profile)


def _to_out(p: OwnerProfile) -> OwnerProfileOut:
    return OwnerProfileOut(
        id=str(p.id),
        person_id=str(p.person_id),
        notes=p.notes,
    )
