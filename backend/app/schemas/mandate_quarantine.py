"""Content-free observation and current administrative disposition."""
import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Reason = Literal['CUSTODY_UNVERIFIED', 'EXTERNAL_ORIGINAL_UNAVAILABLE']


class QuarantineReviewCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: uuid.UUID
    snapshot_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    reason: Reason


class QuarantineDisposition(QuarantineReviewCreate):
    id: uuid.UUID
    reviewed_by: uuid.UUID
    reviewed_at: datetime


class QuarantineObservation(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    snapshot_digest: str
    observed_at: datetime
    disposition: QuarantineDisposition | None
