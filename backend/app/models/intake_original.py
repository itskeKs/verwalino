"""Immutable original metadata; runtime construction uses private SQL commands."""
import uuid
from datetime import datetime
from sqlalchemy import BigInteger,DateTime,ForeignKey,String,UniqueConstraint,func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped,mapped_column
from app.models.base import Base,UUIDPrimaryKeyMixin


class IntakeOriginal(Base,UUIDPrimaryKeyMixin):
    __tablename__='intake_original'
    organization_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey('organization.id'),nullable=False)
    item_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    uploaded_by: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    content_sha256: Mapped[str]=mapped_column(String(64),nullable=False)
    content_byte_size: Mapped[int]=mapped_column(BigInteger,nullable=False)
    mime_type: Mapped[str]=mapped_column(String(100),nullable=False)
    storage_uri: Mapped[str]=mapped_column(String,nullable=False)
    storage_backend: Mapped[str]=mapped_column(String,nullable=False)
    storage_version: Mapped[str|None]=mapped_column(String)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.clock_timestamp(),nullable=False)
    __table_args__=(UniqueConstraint('organization_id','id'),UniqueConstraint('organization_id','item_id'))
