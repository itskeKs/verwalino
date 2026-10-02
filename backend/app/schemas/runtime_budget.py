"""Explicit approval of the configuration attested by the serving runtime."""
import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field


class RuntimeBudgetPolicy(BaseModel):
    enabled: bool
    max_cost_usd: str
    window_minutes: int
    reservation_seconds: int


class RuntimeBudgetReview(BaseModel):
    id: uuid.UUID
    previous_review_id: uuid.UUID | None
    reviewed_by: uuid.UUID
    reviewed_at: datetime
    policy: RuntimeBudgetPolicy
    policy_digest: str
    reason: str


class RuntimeBudgetStatus(BaseModel):
    policy: RuntimeBudgetPolicy
    policy_digest: str
    review: RuntimeBudgetReview | None
    matches_reviewed_policy: bool


class RuntimeBudgetReviewCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_review_id: uuid.UUID | None = None
    policy_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    reason: str = Field(min_length=1,max_length=2000)
    acknowledged: Literal[True]
