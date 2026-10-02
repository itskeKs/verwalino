"""Opaque review of resources whose existing strict restrictions are retained."""
import uuid
from typing import Literal
from datetime import datetime
from pydantic import BaseModel,ConfigDict,Field,computed_field


class DispositionAcceptance(BaseModel):
    id: uuid.UUID
    snapshot_id: uuid.UUID
    manifest_digest: str
    assessment_digest: str
    accepted_by: uuid.UUID
    accepted_at: datetime
    reason: str
    retained_count: int
    team_count: int
    activation_available: Literal[False]


class DispositionAcceptCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    manifest_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    assessment_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    reason: str = Field(min_length=1,max_length=2000)
    team_acknowledged: Literal[True]
    retention_acknowledged: Literal[True]


class DispositionStatus(BaseModel):
    snapshot_id: uuid.UUID
    manifest_digest: str
    assessment_digest: str
    ready_count: int
    retained_count: int
    team_count: int
    blocker_count: int
    active_work_count: int
    current: bool
    reviewed: bool
    acceptance_available: bool
    acceptance: DispositionAcceptance | None
    activation_available: Literal[False]


class DispositionItem(BaseModel):
    kind: str
    resource_id: uuid.UUID
    findings: list[str]
    disposition: Literal['READY','RETAIN_BLOCKED','TEAM_REVIEW','BLOCKED']

    @computed_field
    @property
    def id(self) -> str:
        return f'{self.kind}:{self.resource_id}'
