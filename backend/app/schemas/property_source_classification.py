"""Explicit current decision for a bound original."""
import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field


class SourceClassificationCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    document_id: uuid.UUID
    property_id: uuid.UUID
    content_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    source_context_digest: str=Field(pattern=r'^[0-9a-f]{64}$')
    decision: Literal['ADMITTED','QUARANTINED']
    reason: str=Field(min_length=1,max_length=2000)


class SourceClassificationOut(SourceClassificationCreate):
    model_config=ConfigDict(from_attributes=True)
    id: uuid.UUID
    domain_id: uuid.UUID
    reviewed_by: uuid.UUID
    created_at: datetime


class PendingSourceClassificationOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    property_id: uuid.UUID
    property_name: str
    content_sha256: str
    source_context_digest: str
    source_uploaded_by: uuid.UUID | None
    linked_import_count: int
    can_admit: bool
    can_preview: bool
    quiescent: bool
