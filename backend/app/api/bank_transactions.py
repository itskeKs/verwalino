"""Bank matching proposals and atomic pending-only assignment/ignore (T21)."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.bank_accounts import BankTransactionOut
from app.api.payments import PaymentOut, _to_out
from app.core.tenant import AuthContext, get_current_user, get_db, require_write_dep
from app.services.bank_import_service import BankImportService
from app.services.payment_service import OWNER_TYPES, TENANT_TYPES

router = APIRouter(prefix="/api/bank-transactions", tags=["bank-transactions"])
Status = Literal["NEU", "ZUGEORDNET", "IGNORIERT"]
PaymentType = Literal["KALTMIETE", "NK_VORAUSZAHLUNG", "KAUTION", "HAUSGELD_VORSCHUSS", "RUECKLAGENANTEIL", "RUECKLAGENENTNAHME", "SONDERZAHLUNG", "NACHZAHLUNG"]


class AssignmentLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payment_type: PaymentType
    amount: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)]
    period_year: Annotated[int, Field(ge=1, le=9999, strict=True)]
    period_month: Annotated[int, Field(ge=1, le=12, strict=True)]
    lease_agreement_id: uuid.UUID | None = None
    tenant_profile_id: uuid.UUID | None = None
    owner_profile_id: uuid.UUID | None = None
    note: Annotated[str, Field(max_length=500)] | None = None

    @model_validator(mode="after")
    def coherent_target(self):
        tenant = self.lease_agreement_id is not None
        owner = self.owner_profile_id is not None
        if tenant == owner or owner and self.tenant_profile_id is not None:
            raise ValueError("Choose exactly one lease or owner target")
        if self.payment_type in TENANT_TYPES and not tenant:
            raise ValueError("Tenant payments require a lease")
        if self.payment_type in OWNER_TYPES and not owner:
            raise ValueError("Owner payments require an owner")
        return self


class AssignmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: Annotated[list[AssignmentLine], Field(min_length=1, max_length=100)]


class MatchLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    payment_type: str
    period_year: int
    period_month: int
    amount: Decimal | None
    open_amount: Decimal
    lease_agreement_id: uuid.UUID | None
    tenant_profile_id: uuid.UUID | None
    owner_profile_id: uuid.UUID | None


class MatchSuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    target_name: str
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    lines: list[MatchLineOut]
    reasons: list[str]
    requires_review: bool
    unallocated_amount: Decimal


class TransactionWithSuggestionsOut(BankTransactionOut):
    suggestions: list[MatchSuggestionOut]


class TransactionPageOut(BaseModel):
    items: list[TransactionWithSuggestionsOut]
    next_offset: int | None


class StatusOut(BaseModel):
    id: uuid.UUID
    status: Status
    model_config = ConfigDict(from_attributes=True)


class AssignmentOut(StatusOut):
    payments: list[PaymentOut]


@router.get("", response_model=TransactionPageOut)
async def list_transactions(
    status: Literal["ALL", "NEU", "ZUGEORDNET", "IGNORIERT"] | None = "NEU",
    bank_account_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await BankImportService(db, auth.organization_id, auth.user_id).list_transactions(
        bank_account_id=bank_account_id, status=None if status == "ALL" else status,
        limit=limit, offset=offset)


@router.post("/{transaction_id}/assign", response_model=AssignmentOut)
async def assign_transaction(
    transaction_id: uuid.UUID,
    body: AssignmentIn,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    transaction, payments = await BankImportService(db, auth.organization_id, auth.user_id).assign(
        transaction_id, [line.model_dump() for line in body.lines])
    return {"id": transaction.id, "status": transaction.status,
            "payments": [_to_out(payment) for payment in payments]}


@router.post("/{transaction_id}/ignore", response_model=StatusOut)
async def ignore_transaction(
    transaction_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    return await BankImportService(db, auth.organization_id, auth.user_id).ignore(transaction_id)
