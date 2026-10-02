"""Explicit setup commands; preparation is not source-policy activation."""
import uuid
from datetime import datetime
from typing import Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

DataCapability = Literal['read','prepare','approve','export']


class DomainCreate(BaseModel):
    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True)
    property_id: uuid.UUID
    label: str = Field(min_length=1,max_length=255)
    reason: str = Field(min_length=1,max_length=2000)


class DomainOut(DomainCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime
    enforcement_status: Literal['PREPARATION'] = 'PREPARATION'


class DataGrantCreate(BaseModel):
    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True)
    user_id: uuid.UUID
    capabilities: set[DataCapability]
    reason: str = Field(min_length=1,max_length=2000)
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
    reason: str
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class DataGrantListOut(DataGrantOut):
    user_display_name: str
