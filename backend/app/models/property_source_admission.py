"""Immutable evidence of an original's authorized target-audience admission."""
import uuid
from datetime import datetime
from sqlalchemy import DateTime,ForeignKeyConstraint,Index,UniqueConstraint,func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped,mapped_column
from app.models.base import Base,UUIDPrimaryKeyMixin


class PropertySourceAdmission(Base,UUIDPrimaryKeyMixin):
    __tablename__='property_source_admission'
    organization_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    import_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    document_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    property_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    domain_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    grant_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    admitted_by: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,server_default=func.clock_timestamp())
    __table_args__=(
        UniqueConstraint('organization_id','document_id',name='uq_property_source_admission_document'),
        ForeignKeyConstraint(['organization_id','import_id','document_id'],
            ['declaration_import_document.organization_id','declaration_import_document.import_id','declaration_import_document.document_id'],name='fk_property_source_admission_link'),
        ForeignKeyConstraint(['organization_id','property_id','domain_id'],
            ['property_data_domain.organization_id','property_data_domain.property_id','property_data_domain.id'],name='fk_property_source_admission_domain'),
        ForeignKeyConstraint(['organization_id','domain_id','admitted_by','grant_id'],
            ['property_data_grant.organization_id','property_data_grant.domain_id','property_data_grant.user_id','property_data_grant.id'],name='fk_property_source_admission_grant'),
        Index('ix_property_source_admission_import','organization_id','import_id'),
    )
