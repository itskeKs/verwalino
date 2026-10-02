"""Bounded explicit commands for evidence-based legacy payment classification."""
import uuid
from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class LegacyPaymentCandidate(BaseModel):
    id: uuid.UUID
    bank_account_id: uuid.UUID
    bank_label: str
    amount: str
    direction: str
    value_date: date
    payment_type: str
    proof_status: Literal['proven', 'review_required']
    blocker: str | None
    source_digest: str | None


class ReconciliationSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    payment_id: uuid.UUID
    source_digest: str = Field(pattern=r'^[0-9a-f]{64}$')


class ReconciliationCommand(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    items: list[ReconciliationSelection] = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def canonical_items(self):
        if len({item.payment_id for item in self.items}) != len(self.items):
            raise ValueError('Each payment must be selected once')
        self.items.sort(key=lambda item: str(item.payment_id))
        return self


class ReconciliationResultItem(ReconciliationSelection):
    binding_id: uuid.UUID


class ReconciliationResult(BaseModel):
    id: uuid.UUID
    scope_id: uuid.UUID
    reason: str
    items: list[ReconciliationResultItem]
