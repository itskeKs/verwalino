"""Current review is distinct from historical attachment authorization."""
import uuid
from datetime import datetime
from pydantic import BaseModel,ConfigDict,Field


class SourceAdmissionReviewCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    document_id: uuid.UUID
    import_id: uuid.UUID
    draft_revision: int=Field(ge=0)
    content_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    reason: str=Field(min_length=1,max_length=2000)


class SourceAdmissionReviewOut(SourceAdmissionReviewCreate):
    model_config=ConfigDict(from_attributes=True)
    id: uuid.UUID
    property_id: uuid.UUID
    domain_id: uuid.UUID
    reviewed_by: uuid.UUID
    created_at: datetime


class PendingSourceReviewOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    import_id: uuid.UUID
    property_id: uuid.UUID
    property_name: str
    draft_revision: int
    title: str
    original_filename: str
    content_sha256: str
    can_preview: bool
