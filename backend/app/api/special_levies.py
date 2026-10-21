"""Append-only Sonderumlagen for an explicit whole-property WEG scope."""
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db, require_write_dep
from app.core.live_principal import require_live_principal
from app.services.special_levy_service import SpecialLevyService

router = APIRouter(prefix="/api/special-levies", tags=["special-levies"])


class SpecialLevyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    property_id: uuid.UUID
    accounting_scope_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    due_date: date
    resolution_id: uuid.UUID | None = None


class AllocationOut(BaseModel):
    owner_profile_id: uuid.UUID
    unit_id: uuid.UUID
    mea: str
    amount: str


class SpecialLevyOut(BaseModel):
    id: uuid.UUID
    property_id: uuid.UUID
    accounting_scope_id: uuid.UUID
    amount: str
    due_date: date
    resolution_id: uuid.UUID | None
    allocations: list[AllocationOut]


def output(levy, allocations):
    return SpecialLevyOut(id=levy.id, property_id=levy.property_id,
        accounting_scope_id=levy.accounting_scope_id, amount=f"{levy.amount:.2f}",
        due_date=levy.due_date, resolution_id=levy.resolution_id,
        allocations=[AllocationOut(owner_profile_id=a.owner_profile_id, unit_id=a.unit_id,
            mea=format(a.mea, "f"), amount=f"{a.amount:.2f}") for a in allocations])


@router.post("", response_model=SpecialLevyOut, status_code=201)
async def create_levy(body: SpecialLevyCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db, scope="function")):
    await require_live_principal(db, auth, writer=True)
    levy, allocations = await SpecialLevyService(db, auth.organization_id, auth.user_id).create(**body.model_dump())
    # Refresh the Numeric scale so POST and subsequent GET return identical basis.
    for allocation in allocations:
        await db.refresh(allocation)
    return output(levy, allocations)


@router.get("", response_model=list[SpecialLevyOut])
async def list_levies(accounting_scope_id: uuid.UUID | None = None, property_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function")):
    await require_live_principal(db, auth, writer=False)
    rows = await SpecialLevyService(db, auth.organization_id, auth.user_id).list(accounting_scope_id, property_id)
    return [output(levy, allocations) for levy, allocations in rows]
