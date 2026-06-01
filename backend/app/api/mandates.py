"""ManagementMandate endpoints -- CRUD within the current organization."""

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
    require_admin_dep,
    require_write_dep,
)
from app.repositories.mandate_repo import MandateRepository
from app.models.management_mandate import ManagementMandate

router = APIRouter(prefix="/api/mandates", tags=["mandates"])


class MandateOut(BaseModel):
    id: str
    mandate_type: str
    label: str
    start_date: date
    end_date: date | None

    model_config = {"from_attributes": True}


class MandateCreate(BaseModel):
    mandate_type: str
    label: str = ""
    start_date: date
    end_date: date | None = None


@router.get("", response_model=list[MandateOut])
async def list_mandates(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = MandateRepository(db, auth.organization_id)
    mandates = await repo.list_all()
    return [
        MandateOut(
            id=str(m.id),
            mandate_type=m.mandate_type,
            label=m.label,
            start_date=m.start_date,
            end_date=m.end_date,
        )
        for m in mandates
    ]


@router.get("/{mandate_id}", response_model=MandateOut)
async def get_mandate(
    mandate_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = MandateRepository(db, auth.organization_id)
    m = await repo.get_by_id(mandate_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Mandate not found")
    return MandateOut(
        id=str(m.id),
        mandate_type=m.mandate_type,
        label=m.label,
        start_date=m.start_date,
        end_date=m.end_date,
    )


@router.post("", response_model=MandateOut, status_code=201)
async def create_mandate(
    body: MandateCreate,
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_db),
):
    """Create a mandate -- OWNER_ADMIN only."""
    if body.mandate_type not in ("WEG", "MIET", "SEV"):
        raise HTTPException(status_code=422, detail="Invalid mandate_type")

    repo = MandateRepository(db, auth.organization_id)
    mandate = ManagementMandate(
        organization_id=auth.organization_id,
        mandate_type=body.mandate_type,
        label=body.label,
        start_date=body.start_date,
        end_date=body.end_date,
    )
    mandate = await repo.create(mandate)
    return MandateOut(
        id=str(mandate.id),
        mandate_type=mandate.mandate_type,
        label=mandate.label,
        start_date=mandate.start_date,
        end_date=mandate.end_date,
    )
