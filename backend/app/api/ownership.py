"""Ownership history endpoints -- temporal records within the current organization."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import (
    AuthContext,
    get_current_user,
    get_db,
    require_write_dep,
)
from app.repositories.ownership_history_repo import OwnershipHistoryRepository
from app.models.ownership_history import OwnershipHistory

router = APIRouter(prefix="/api/ownership", tags=["ownership"])


class OwnershipOut(BaseModel):
    id: str
    unit_id: str
    owner_profile_id: str
    valid_from: date
    valid_to: date | None
    mea_share: str | None

    model_config = {"from_attributes": True}


class OwnershipCreate(BaseModel):
    unit_id: str
    owner_profile_id: str
    valid_from: date
    valid_to: date | None = None
    mea_share: str | None = None


@router.get("", response_model=list[OwnershipOut])
async def list_ownership(
    unit_id: uuid.UUID | None = None,
    at_date: date | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = OwnershipHistoryRepository(db, auth.organization_id)
    if unit_id and at_date:
        items = await repo.owners_at_date(unit_id, at_date)
    elif unit_id:
        items = await repo.list_by_unit(unit_id)
    else:
        items = await repo.list_all()
    return [_to_out(o) for o in items]


@router.post("", response_model=OwnershipOut, status_code=201)
async def create_ownership(
    body: OwnershipCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    from decimal import Decimal

    repo = OwnershipHistoryRepository(db, auth.organization_id)
    oh = OwnershipHistory(
        unit_id=uuid.UUID(body.unit_id),
        owner_profile_id=uuid.UUID(body.owner_profile_id),
        valid_from=body.valid_from,
        valid_to=body.valid_to,
        mea_share=Decimal(body.mea_share) if body.mea_share else None,
    )
    try:
        oh = await repo.create(oh)
    except Exception as e:
        # EXCLUDE constraint violation surfaces as IntegrityError
        if "excl_ownership_no_overlap" in str(e):
            raise HTTPException(
                status_code=409,
                detail="Overlapping ownership period for this unit/owner combination",
            )
        raise
    return _to_out(oh)


def _to_out(o: OwnershipHistory) -> OwnershipOut:
    return OwnershipOut(
        id=str(o.id),
        unit_id=str(o.unit_id),
        owner_profile_id=str(o.owner_profile_id),
        valid_from=o.valid_from,
        valid_to=o.valid_to,
        mea_share=str(o.mea_share) if o.mea_share is not None else None,
    )
