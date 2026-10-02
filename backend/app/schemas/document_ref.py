"""Metadata only; storage locations and source bytes stay behind original routes."""
import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel,ConfigDict

SourceKind=Literal['invoice_document','property_document','mandate_document','intake_original']


class DocumentRefOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id: uuid.UUID
    source_kind: SourceKind
    source_id: uuid.UUID
    content_sha256: str
    mime_type: str
    content_byte_size: int
    registered_at: datetime
