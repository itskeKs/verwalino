"""Immutable current classification; unknown custody is never rewritten."""
import uuid
from datetime import datetime
from sqlalchemy import DateTime,ForeignKeyConstraint,Index,String,UniqueConstraint,func,FetchedValue
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped,mapped_column
from app.models.base import Base,UUIDPrimaryKeyMixin


class PropertySourceClassification(Base,UUIDPrimaryKeyMixin):
    __tablename__='property_source_classification'
    organization_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    document_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    property_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    domain_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    grant_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    reviewed_by: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    decision: Mapped[str]=mapped_column(String,nullable=False)
    content_sha256: Mapped[str]=mapped_column(String(64),nullable=False)
    source_context_digest: Mapped[str]=mapped_column(String(64),nullable=False)
    reason: Mapped[str]=mapped_column(String(2000),nullable=False)
    request_key: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    request_digest: Mapped[str]=mapped_column(String(64),nullable=False,server_default=FetchedValue())
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,server_default=func.clock_timestamp())
    __table_args__=(
        UniqueConstraint('organization_id','document_id',name='uq_source_classification_document'),
        UniqueConstraint('organization_id','reviewed_by','request_key',name='uq_source_classification_request'),
        ForeignKeyConstraint(['organization_id','document_id'],['property_document.organization_id','property_document.id'],name='fk_source_classification_document'),
        ForeignKeyConstraint(['organization_id','property_id','domain_id'],['property_data_domain.organization_id','property_data_domain.property_id','property_data_domain.id'],name='fk_source_classification_domain'),
        ForeignKeyConstraint(['organization_id','domain_id','reviewed_by','grant_id'],['property_data_grant.organization_id','property_data_grant.domain_id','property_data_grant.user_id','property_data_grant.id'],name='fk_source_classification_grant'),
        Index('ix_source_classification_domain','organization_id','domain_id'),
        Index('ix_source_classification_grant','organization_id','grant_id'),
    )
