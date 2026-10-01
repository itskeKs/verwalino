"""Validated commands and responses for explicit accounting scope setup."""
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.core.rbac import ScopeCapability

ResourceType = Literal['bank_account', 'payment', 'cost_entry', 'settlement_run', 'economic_plan']


class ScopeUnitInput(BaseModel):
    unit_id: uuid.UUID
    owner_profile_id: uuid.UUID | None = None


class ScopeCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    property_id: uuid.UUID
    management_mandate_id: uuid.UUID | None = None
    scope_type: Literal['WEG', 'MIET', 'SEV']
    label: str = Field(min_length=1, max_length=255)
    valid_from: date
    valid_to: date | None = None
    whole_property: bool = True
    units: list[ScopeUnitInput] = Field(default_factory=list, max_length=2000)

    @model_validator(mode='after')
    def validate_definition(self):
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError('The end date must not precede the start date')
        if self.whole_property == bool(self.units):
            raise ValueError('Choose either the whole property or an explicit unit set')
        if len({unit.unit_id for unit in self.units}) != len(self.units):
            raise ValueError('Each unit must be listed once')
        if self.scope_type == 'SEV' and (self.whole_property or any(unit.owner_profile_id is None for unit in self.units)):
            raise ValueError('SEV requires an explicit unit set with reviewed owners')
        self.units.sort(key=lambda unit: str(unit.unit_id))
        return self


class ScopeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    property_id: uuid.UUID
    management_mandate_id: uuid.UUID | None
    scope_type: str
    label: str
    valid_from: date
    valid_to: date | None
    whole_property: bool
    origin: str
    created_at: datetime


class ScopeDetail(ScopeOut):
    units: list[ScopeUnitInput]


class GrantCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    user_id: uuid.UUID
    capabilities: set[ScopeCapability]
    reason: str = Field(min_length=1, max_length=2000)
    expires_at: AwareDatetime | None = None

    @model_validator(mode='after')
    def must_include_read(self):
        if ScopeCapability.READ not in self.capabilities:
            raise ValueError('Read capability is required')
        return self


class GrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    scope_id: uuid.UUID
    user_id: uuid.UUID
    capabilities: list[str]
    reason: str
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class GrantListOut(GrantOut):
    user_display_name: str


class BindingCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    resource_type: ResourceType
    resource_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2000)


class BindingOut(BaseModel):
    id: uuid.UUID
    scope_id: uuid.UUID
    resource_type: ResourceType
    resource_id: uuid.UUID
    reason: str
