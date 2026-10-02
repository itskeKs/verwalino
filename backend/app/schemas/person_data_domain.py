"""Explicit setup commands; preparation is not source-policy activation."""
import uuid
from datetime import datetime
from typing import Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

DataCapability = Literal['read','prepare','export']
ReasonCode = Literal['INITIAL_SETUP','TEAM_ACCESS','TEMPORARY_ACCESS','REPLACEMENT']


class DomainCreate(BaseModel):
    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True)
    person_id: uuid.UUID
    reason_code: ReasonCode


class DomainOut(DomainCreate):
    label: str
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime
    enforcement_status: Literal['PREPARATION'] = 'PREPARATION'


class DataGrantCreate(BaseModel):
    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True)
    user_id: uuid.UUID
    capabilities: set[DataCapability]
    reason_code: ReasonCode
    expires_at: AwareDatetime | None = None

    @model_validator(mode='after')
    def require_read(self):
        if 'read' not in self.capabilities:
            raise ValueError('Read capability is required')
        return self


class DataGrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    domain_id: uuid.UUID
    user_id: uuid.UUID
    capabilities: list[DataCapability]
    reason_code: ReasonCode
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class DataGrantListOut(DataGrantOut):
    user_display_name: str
