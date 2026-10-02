"""Bounded explicit changes to three separate audiences."""
import uuid
from datetime import datetime
from typing import Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class GrantChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    domain_kind: Literal['financial','property','person']
    audience_id: uuid.UUID
    user_id: uuid.UUID
    expected_grant_id: uuid.UUID | None
    capabilities: list[Literal['read','prepare','confirm','approve','send','export']] = Field(max_length=6)
    expires_at: AwareDatetime | None = None

    @model_validator(mode='after')
    def valid_change(self):
        allowed = {'financial':{'read','prepare','confirm','approve','send','export'},
                   'property':{'read','prepare','approve','export'},'person':{'read','prepare','export'}}[self.domain_kind]
        if len(self.capabilities)!=len(set(self.capabilities)) or not set(self.capabilities)<=allowed:
            raise ValueError('Invalid capabilities for audience')
        if self.capabilities and 'read' not in self.capabilities:
            raise ValueError('Read capability required')
        if not self.capabilities and (self.expected_grant_id is None or self.expires_at is not None):
            raise ValueError('Revocation requires a previous grant and no expiry')
        self.capabilities.sort()
        return self


class GrantProposalCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    manifest_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    changes: list[GrantChange] = Field(min_length=1,max_length=100)

    @model_validator(mode='after')
    def unique_targets(self):
        if len({(r.domain_kind,r.audience_id,r.user_id) for r in self.changes})!=len(self.changes):
            raise ValueError('Each audience/member must occur once')
        self.changes.sort(key=lambda r:(r.domain_kind,str(r.audience_id),str(r.user_id)))
        return self


class GrantAcceptanceCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    proposal_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    acknowledged: Literal[True]


class GrantResult(BaseModel):
    domain_kind: Literal['financial','property','person']
    audience_id: uuid.UUID
    user_id: uuid.UUID
    revoked_grant_id: uuid.UUID | None
    grant_id: uuid.UUID | None


class GrantAcceptance(BaseModel):
    id: uuid.UUID
    proposal_id: uuid.UUID
    proposal_digest: str
    results: list[GrantResult]
    accepted_by: uuid.UUID
    accepted_at: datetime


class GrantProposal(BaseModel):
    id: uuid.UUID
    snapshot_id: uuid.UUID
    manifest_digest: str
    proposal_digest: str
    changes: list[GrantChange]
    proposed_by: uuid.UUID
    proposed_at: datetime
    acceptance: GrantAcceptance | None = None
