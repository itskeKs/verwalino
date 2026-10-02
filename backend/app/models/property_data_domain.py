"""Prepared shared-data identity and explicit grants, independent from money."""
import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKeyConstraint, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDPrimaryKeyMixin


class PropertyDataDomain(Base, UUIDPrimaryKeyMixin):
    __tablename__ = 'property_data_domain'
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint('organization_id','id'), UniqueConstraint('organization_id','property_id'),
        UniqueConstraint('organization_id','property_id','id',name='uq_property_data_domain_context'),
        UniqueConstraint('organization_id','created_by','request_key'),
        ForeignKeyConstraint(['organization_id','property_id'],['property.organization_id','property.id'], deferrable=True, initially='DEFERRED'),
        ForeignKeyConstraint(['organization_id','created_by'],['user.organization_id','user.id']),
    )


class PropertyDataGrant(Base, UUIDPrimaryKeyMixin):
    __tablename__ = 'property_data_grant'
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    domain_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(String(16)), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    granted_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    __table_args__ = (
        UniqueConstraint('organization_id','granted_by','request_key'),
        UniqueConstraint('organization_id','domain_id','user_id','id',name='uq_property_data_grant_actor_context'),
        ForeignKeyConstraint(['organization_id','domain_id'],['property_data_domain.organization_id','property_data_domain.id']),
        ForeignKeyConstraint(['organization_id','user_id'],['user.organization_id','user.id']),
        ForeignKeyConstraint(['organization_id','granted_by'],['user.organization_id','user.id']),
        ForeignKeyConstraint(['organization_id','revoked_by'],['user.organization_id','user.id']),
    )
