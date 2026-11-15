"""Ownership history endpoints -- temporal records within the current organization."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
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
from app.repositories.unit_repo import UnitRepository
from app.repositories.owner_profile_repo import OwnerProfileRepository
from app.models.ownership_history import OwnershipHistory
from app.models.user import User

router = APIRouter(prefix="/api/ownership", tags=["ownership"])


class OwnershipOut(BaseModel):
    id: str
    unit_id: str
    owner_profile_id: str
    valid_from: date
    valid_to: date | None
    mea_share: str | None
    corrected_at: datetime | None = None
    corrected_by_name: str | None = None

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
    names = await _correctors(db, auth.organization_id, items)
    return [_to_out(o, names.get(o.corrected_by)) for o in items]


@router.post("", response_model=OwnershipOut, status_code=201)
async def create_ownership(
    body: OwnershipCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    from decimal import Decimal

    repo = OwnershipHistoryRepository(db, auth.organization_id)
    unit_id = uuid.UUID(body.unit_id)
    owner_profile_id = uuid.UUID(body.owner_profile_id)
    if await UnitRepository(db, auth.organization_id).get_by_id(unit_id) is None:
        raise HTTPException(404, "Unit not found")
    if await OwnerProfileRepository(db, auth.organization_id).get_by_id(owner_profile_id) is None:
        raise HTTPException(404, "Owner profile not found")
    # API write edge (ADR-0006): the user enters the INCLUSIVE last day of ownership;
    # storage is HALF-OPEN [valid_from, valid_to) so persist the EXCLUSIVE bound
    # (last day + 1). valid_from unchanged; NULL stays open-ended. Lets a seamless
    # owner change "Alt bis 30.06." + "Neu ab 01.07." coexist without a false 409.
    oh = OwnershipHistory(
        id=uuid.uuid4(),
        unit_id=unit_id,
        owner_profile_id=owner_profile_id,
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


def _correction_refusal(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


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
    if existing.corrected_at is not None:
        raise _correction_refusal(409, "ownership_corrected",
            "Dieser Eintrag wurde korrigiert und kann nicht mehr geändert werden. "
            "Bitte verwenden Sie den aktuellen Eintrag.")
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


class OwnershipCorrection(BaseModel):
    """The corrected values of a mistaken row: the start date and the share, both stated in full."""

    model_config = ConfigDict(extra="forbid")

    valid_from: date
    mea_share: str | None = None


SHARE_STEP = Decimal("0.000001")  # NUMERIC(12,6): the stored share is rounded to six decimals


def _corrected_share(value: str | None) -> Decimal | None:
    """The share as the database will store it; blank means none. Positive and within NUMERIC(12,6).

    The checks run on the rounded value: 999999,9999999 would store as 1000000, 0,0000001 as 0, and
    50,0000001 on a row of 50 changes nothing.
    """
    if value is None or not value.strip():
        return None
    try:
        share = Decimal(value.strip().replace(",", "."))
        share = share.quantize(SHARE_STEP, rounding=ROUND_HALF_UP) if share.is_finite() and share.adjusted() < 7 else None
    except InvalidOperation:
        share = None
    if share is None or share <= 0 or share >= Decimal(10) ** 6:
        raise _correction_refusal(422, "ownership_correction_share",
            "Der MEA-Anteil muss eine positive Zahl sein, zum Beispiel 50 oder 0,5.")
    return share


@router.post("/{ownership_id}/correction", response_model=OwnershipOut, status_code=201)
async def correct_ownership(
    ownership_id: uuid.UUID,
    body: OwnershipCorrection,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    """Mark a mistaken row as corrected and insert its replacement (#668).

    The mistaken row keeps its values, who corrected it and when; calculations read only
    uncorrected rows, so finalized results (their frozen snapshots) never change.
    """
    # Match financial computations and T23: ACL/input locks precede parent locks.
    await FinancialScopeService(db, auth.organization_id, auth.user_id).lock_inputs()
    repo = OwnershipHistoryRepository(db, auth.organization_id)
    existing = await repo.get_by_id_for_update(ownership_id)
    if existing is None:
        raise HTTPException(404, "Ownership history not found")
    await require_history_prepare(db, auth.organization_id, "ownership_history", existing)
    if existing.corrected_at is not None:
        raise _correction_refusal(409, "ownership_already_corrected",
            "Dieser Eintrag wurde bereits korrigiert. Bitte korrigieren Sie den aktuellen Eintrag.")
    share = _corrected_share(body.mea_share)
    if body.valid_from == existing.valid_from and share == existing.mea_share:
        raise _correction_refusal(422, "ownership_correction_unchanged",
            "Es wurde nichts geändert. Bitte geben Sie einen anderen MEA-Anteil oder Beginn ein.")
    if existing.valid_to is not None and body.valid_from >= existing.valid_to:
        raise _correction_refusal(422, "ownership_correction_period",
            "Das Eigentum muss vor seinem Ende beginnen. Bitte wählen Sie einen früheren Beginn.")
    replacement = OwnershipHistory(
        id=uuid.uuid4(), unit_id=existing.unit_id, owner_profile_id=existing.owner_profile_id,
        valid_from=body.valid_from, valid_to=existing.valid_to, mea_share=share,
    )
    await require_history_prepare(db, auth.organization_id, "ownership_history", replacement)
    existing.corrected_at = datetime.now(timezone.utc)
    existing.corrected_by = auth.user_id
    try:
        # The mistaken row leaves the no-overlap constraint first; the replacement may then
        # only collide with another uncorrected row of the same person and unit.
        await db.flush()
        replacement = await repo.create(replacement)
    except IntegrityError as exc:
        if "excl_ownership_no_overlap" in str(exc):
            raise _correction_refusal(409, "ownership_correction_overlap",
                "Die Korrektur überschneidet sich mit einem anderen Eintrag derselben Person in dieser "
                "Einheit. Bitte wählen Sie einen anderen Beginn oder beenden Sie den anderen Eintrag zuerst.",
            ) from exc
        raise
    return _to_out(replacement)


async def _correctors(db: AsyncSession, org_id: uuid.UUID, rows) -> dict[uuid.UUID, str]:
    """Display names of the staff who corrected the given rows, within the organization."""
    ids = {o.corrected_by for o in rows if o.corrected_by is not None}
    if not ids:
        return {}
    users = (await db.scalars(select(User).where(User.organization_id == org_id, User.id.in_(ids)))).all()
    return {u.id: u.display_name or u.email for u in users}


def _to_out(o: OwnershipHistory, corrected_by_name: str | None = None) -> OwnershipOut:
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
        corrected_at=o.corrected_at,
        corrected_by_name=corrected_by_name,
    )
