"""Explicit administrative source security transitions."""
import uuid
from datetime import datetime
from typing import Literal
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,model_validator

Mode=Literal['PREPARATION','ENFORCED','PAUSED']


class SourceSecurityTransitionCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    target_mode: Literal['ENFORCED','PAUSED']
    expected_mode: Mode
    expected_changed_at: AwareDatetime | None
    expected_transition_id: uuid.UUID | None
    acceptance_id: uuid.UUID | None
    manifest_digest: str | None = Field(pattern=r'^[0-9a-f]{64}$')
    assessment_digest: str | None = Field(pattern=r'^[0-9a-f]{64}$')
    reason: str = Field(min_length=1,max_length=2000)
    acknowledged: Literal[True]

    @model_validator(mode='after')
    def evidence_matches_target(self):
        evidence=(self.acceptance_id,self.manifest_digest,self.assessment_digest)
        if self.target_mode=='ENFORCED' and any(value is None for value in evidence):
            raise ValueError('Accepted current evidence required')
        if self.target_mode=='PAUSED' and any(value is not None for value in evidence):
            raise ValueError('Pause requires no activation evidence')
        return self


class SourceSecurityTransition(BaseModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID
    previous_transition_id: uuid.UUID | None
    previous_mode: Mode
    previous_changed_at: datetime | None
    target_mode: Literal['ENFORCED','PAUSED']
    changed_at: datetime
    acceptance_id: uuid.UUID | None
    manifest_digest: str | None
    assessment_digest: str | None
    reason: str


class SourceSecurityStatus(BaseModel):
    mode: Mode
    changed_at: datetime | None
    last_transition: SourceSecurityTransition | None
