"""Bounded manifest contracts. Context hints never assign originals."""
import uuid
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
from app.core.document_limits import MAX_UPLOAD_BYTES


class IntakeManifestItem(BaseModel):
    model_config=ConfigDict(extra='forbid')
    client_item_key: str=Field(min_length=1,max_length=128,pattern=r'^[A-Za-z0-9_.-]+$')
    filename: str=Field(min_length=1,max_length=255)
    mime_type: Literal['application/pdf','application/xml','image/png','image/jpeg']
    content_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    content_byte_size: int=Field(strict=True,ge=1,le=MAX_UPLOAD_BYTES)


class IntakeBatchCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scope_hint_id: uuid.UUID | None=None
    items: list[IntakeManifestItem]=Field(min_length=1,max_length=100)

    @model_validator(mode='after')
    def unique_items(self):
        if len({item.client_item_key for item in self.items})!=len(self.items):
            raise ValueError('Manifest item keys must be unique')
        return self


class IntakeRetry(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_id: uuid.UUID
    expected_fence: uuid.UUID
    reason: str=Field(min_length=1,max_length=1000)
