"""The scoped pause is not an organization-wide financial freeze."""
import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Mode = Literal['compatibility', 'paused']


class ScopeControlCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    mode: Mode
    expected_event_id: uuid.UUID | None
    reason: str = Field(min_length=1, max_length=2000)


class ScopeControlEvent(BaseModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID
    mode: Mode
    previous_event_id: uuid.UUID | None
    reason: str
    created_at: datetime


class ScopeControlState(BaseModel):
    mode: Mode
    event: ScopeControlEvent | None
