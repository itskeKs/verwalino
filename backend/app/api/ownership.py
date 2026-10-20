"""Ownership history endpoints -- temporal records within the current organization."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.financial_scope_service import FinancialScopeService

from app.core.tenant import (
    AuthContext,
    get_current_user,
    require_write_dep,
)
from app.core.live_principal import get_live_member_db, get_live_writer_db
from app.services.history_source_access import require_history_prepare
from app.core.temporal import (
    db_exclusive_to_api_inclusive,
    inclusive_last_day_to_db_exclusive,
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
    db: AsyncSession = Depends(get_live_member_db),
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
    db: AsyncSession = Depends(get_live_writer_db),
):
    from decimal import Decimal

    repo = OwnershipHistoryRepository(db, auth.organization_id)
    # API write edge (ADR-0006): the user enters the INCLUSIVE last day of ownership;
    # storage is HALF-OPEN [valid_from, valid_to) so persist the EXCLUSIVE bound
    # (last day + 1). valid_from unchanged; NULL stays open-ended. Lets a seamless
    # owner change "Alt bis 30.06." + "Neu ab 01.07." coexist without a false 409.
    oh = OwnershipHistory(
        id=uuid.uuid4(),
        unit_id=uuid.UUID(body.unit_id),
        owner_profile_id=uuid.UUID(body.owner_profile_id),
        valid_from=body.valid_from,
        valid_to=inclusive_last_day_to_db_exclusive(body.valid_to),
        mea_share=Decimal(body.mea_share) if body.mea_share else None,
    )
    await require_history_prepare(db, auth.organization_id, "ownership_history", oh)
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


class OwnershipEnd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid_to: date

    @field_validator("valid_to")
    @classmethod
    def require_representable_exclusive_end(cls, value: date) -> date:
        if value == date.max:
            raise ValueError("Ownership end must allow an exclusive next-day bound")
        return value


@router.patch("/{ownership_id}", response_model=OwnershipOut)
async def end_ownership(
    ownership_id: uuid.UUID,
    body: OwnershipEnd,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    # Match financial computations and T23: ACL/input locks precede parent locks.
    await FinancialScopeService(db, auth.organization_id, auth.user_id).lock_inputs()
    repo = OwnershipHistoryRepository(db, auth.organization_id)
    existing = await repo.get_by_id_for_update(ownership_id)
    if existing is None:
        raise HTTPException(404, "Ownership history not found")
    await require_history_prepare(db, auth.organization_id, "ownership_history", existing)
    if body.valid_to < existing.valid_from:
        raise HTTPException(422, "Ownership end must not precede its start")
    exclusive_end = inclusive_last_day_to_db_exclusive(body.valid_to)
    if existing.valid_to != exclusive_end:
        existing.valid_to = exclusive_end
        try:
            await db.flush()
        except IntegrityError as exc:
            if "excl_ownership_no_overlap" in str(exc):
                raise HTTPException(
                    409, "Overlapping ownership period for this unit/owner combination"
                ) from exc
            raise
    return _to_out(existing)


def _to_out(o: OwnershipHistory) -> OwnershipOut:
    # API display edge (ADR-0006): storage holds the EXCLUSIVE upper bound; return
    # the INCLUSIVE last day (bound - 1) so a GET shows what the user entered.
    # Applies to list, detail, and the owners_at_date path. NULL stays open-ended.
    return OwnershipOut(
        id=str(o.id),
        unit_id=str(o.unit_id),
        owner_profile_id=str(o.owner_profile_id),
        valid_from=o.valid_from,
        valid_to=db_exclusive_to_api_inclusive(o.valid_to),
        mea_share=str(o.mea_share) if o.mea_share is not None else None,
    )
