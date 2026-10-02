"""Opaque administrative evidence, separate from original content permissions."""
import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, computed_field


class CoverageCreate(BaseModel):
    predecessor_id: uuid.UUID | None = None


class CoverageSnapshot(BaseModel):
    id: uuid.UUID
    predecessor_id: uuid.UUID | None
    checker_version: int
    generated_by: uuid.UUID
    generated_at: datetime
    manifest_digest: str
    item_count: int
    finding_count: int
    activation_available: Literal[False]


class CoverageItem(BaseModel):
    kind: str
    resource_id: uuid.UUID
    references: dict
    findings: list[str]
    audience: list[dict]

    @computed_field
    @property
    def id(self) -> str:
        return f'{self.kind}:{self.resource_id}'


class CoverageReviewCreate(BaseModel):
    manifest_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    acknowledged: Literal[True]


class CoverageReview(BaseModel):
    id: uuid.UUID
    snapshot_id: uuid.UUID
    manifest_digest: str
    finding_count: int
    reviewed_by: uuid.UUID
    reviewed_at: datetime
    activation_available: Literal[False]


class CoverageSummary(CoverageSnapshot):
    review: CoverageReview | None
