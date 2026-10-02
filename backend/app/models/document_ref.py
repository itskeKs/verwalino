"""Canonical immutable typed references to retained originals."""
import uuid
from datetime import datetime
from sqlalchemy import BigInteger,DateTime,ForeignKey,ForeignKeyConstraint,String,UniqueConstraint,func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped,mapped_column
from app.models.base import Base,UUIDPrimaryKeyMixin


class DocumentRef(Base,UUIDPrimaryKeyMixin):
    __tablename__='document_ref'
    organization_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey('organization.id'),nullable=False)
    source_kind: Mapped[str]=mapped_column(String,nullable=False)
    invoice_document_id: Mapped[uuid.UUID | None]=mapped_column(UUID(as_uuid=True))
    property_document_id: Mapped[uuid.UUID | None]=mapped_column(UUID(as_uuid=True))
    mandate_document_id: Mapped[uuid.UUID | None]=mapped_column(UUID(as_uuid=True))
    intake_original_id: Mapped[uuid.UUID | None]=mapped_column(UUID(as_uuid=True))
    content_sha256: Mapped[str]=mapped_column(String(64),nullable=False)
    mime_type: Mapped[str]=mapped_column(String(100),nullable=False)
    content_byte_size: Mapped[int]=mapped_column(BigInteger,nullable=False)
    registered_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.clock_timestamp(),nullable=False)
    __table_args__=(
        UniqueConstraint('organization_id','id'),
        *(UniqueConstraint('organization_id',f'{kind}_id') for kind in ('invoice_document','property_document','mandate_document','intake_original')),
        *(ForeignKeyConstraint(['organization_id',f'{kind}_id'],[f'{kind}.organization_id',f'{kind}.id'],ondelete='RESTRICT')
          for kind in ('invoice_document','property_document','mandate_document','intake_original')),
    )

    @property
    def source_id(self):
        return getattr(self,f'{self.source_kind}_id')
