"""BankAccount endpoints -- minimal CRUD for M6 frontend (Zahlungen need a BankAccount).

RBAC (like M0-M4): GET for all roles; POST requires a write role.
Organization-scoped via the standard tenant repo pattern.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db, require_write_dep
from app.models.bank_account import BankAccount
from app.services.financial_scope_service import FinancialScopeService
from app.repositories.bank_account_repo import BankAccountRepository

router = APIRouter(prefix="/api/bank-accounts", tags=["bank_accounts"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class BankAccountCreate(BaseModel):
    accounting_scope_id: uuid.UUID | None = None
    property_id: str
    account_type: str  # LAUFEND | RUECKLAGE | KAUTION
    label: str
    iban: str | None = None
    bic: str | None = None
    bank_name: str | None = None


class BankAccountOut(BaseModel):
    property_name: str | None = None
    id: str
    organization_id: str
    property_id: str
    account_type: str
    label: str
    iban: str | None
    bic: str | None
    bank_name: str | None

    model_config = {"from_attributes": True}


def _to_out(a: BankAccount, property_name: str | None = None) -> BankAccountOut:
    return BankAccountOut(
        property_name=property_name,
        id=str(a.id),
        organization_id=str(a.organization_id),
        property_id=str(a.property_id),
        account_type=a.account_type,
        label=a.label,
        iban=a.iban,
        bic=a.bic,
        bank_name=a.bank_name,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("", response_model=list[BankAccountOut])
async def list_bank_accounts(
    property_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = BankAccountRepository(db, auth.organization_id)
    items = await repo.list_with_property_names(property_id)
    return [_to_out(account, name) for account, name in items]


@router.get("/{account_id}", response_model=BankAccountOut)
async def get_bank_account(
    account_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = BankAccountRepository(db, auth.organization_id)
    account = await repo.get_by_id(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Bank account not found")
    return _to_out(account)


@router.post("", response_model=BankAccountOut, status_code=201)
async def create_bank_account(
    body: BankAccountCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    valid_types = {"LAUFEND", "RUECKLAGE", "KAUTION"}
    if body.account_type not in valid_types:
        raise HTTPException(
            status_code=422,
            detail=f"account_type must be one of {sorted(valid_types)}",
        )

    repo = BankAccountRepository(db, auth.organization_id)
    account = BankAccount(
        organization_id=auth.organization_id,
        property_id=uuid.UUID(body.property_id),
        account_type=body.account_type,
        label=body.label,
        iban=body.iban,
        bic=body.bic,
        bank_name=body.bank_name,
    )
    await FinancialScopeService(db, auth.organization_id, auth.user_id).prepare_new(account, body.accounting_scope_id)
    account = await repo.create(account)
    return _to_out(account)
