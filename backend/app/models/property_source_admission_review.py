"""Immutable contemporary disclosure decision for an older unbound original."""
import uuid
from datetime import datetime
from sqlalchemy import DateTime,ForeignKeyConstraint,Index,Integer,String,UniqueConstraint,func,FetchedValue
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped,mapped_column
from app.models.base import Base,UUIDPrimaryKeyMixin


class PropertySourceAdmissionReview(Base,UUIDPrimaryKeyMixin):
    __tablename__='property_source_admission_review'
    organization_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    import_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    document_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    property_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    domain_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    grant_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    reviewed_by: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    draft_revision: Mapped[int]=mapped_column(Integer,nullable=False)
    content_sha256: Mapped[str]=mapped_column(String(64),nullable=False)
    reason: Mapped[str]=mapped_column(String(2000),nullable=False)
    request_key: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    request_digest: Mapped[str]=mapped_column(String(64),nullable=False,server_default=FetchedValue())
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,server_default=func.clock_timestamp())
    __table_args__=(
        UniqueConstraint('organization_id','document_id',name='uq_property_source_review_document'),
        UniqueConstraint('organization_id','reviewed_by','request_key',name='uq_property_source_review_request'),
        ForeignKeyConstraint(['organization_id','import_id','document_id'],
            ['declaration_import_document.organization_id','declaration_import_document.import_id','declaration_import_document.document_id'],name='fk_property_source_review_link'),
        ForeignKeyConstraint(['organization_id','property_id','domain_id'],
            ['property_data_domain.organization_id','property_data_domain.property_id','property_data_domain.id'],name='fk_property_source_review_domain'),
        ForeignKeyConstraint(['organization_id','domain_id','reviewed_by','grant_id'],
            ['property_data_grant.organization_id','property_data_grant.domain_id','property_data_grant.user_id','property_data_grant.id'],name='fk_property_source_review_grant'),
        Index('ix_property_source_review_import','organization_id','import_id'),
        Index('ix_property_source_review_domain','organization_id','domain_id'),
        Index('ix_property_source_review_grant','organization_id','grant_id'),
    )
