"""Prepared shared-data identity and explicit grants, independent from money."""
import uuid
from datetime import datetime
from sqlalchemy import Computed, DateTime, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDPrimaryKeyMixin


class PersonDataDomain(Base, UUIDPrimaryKeyMixin):
    __tablename__ = 'person_data_domain'
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    label: Mapped[str] = mapped_column(String(255), Computed("'Contact ' || person_id::text"), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint('organization_id','id'), UniqueConstraint('organization_id','person_id'),
        UniqueConstraint('organization_id','created_by','request_key'),
        ForeignKeyConstraint(['organization_id','person_id'],['person.organization_id','person.id']),
        ForeignKeyConstraint(['organization_id','created_by'],['user.organization_id','user.id']),
    )


class PersonDataGrant(Base, UUIDPrimaryKeyMixin):
    __tablename__ = 'person_data_grant'
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    domain_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(String(16)), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    granted_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    __table_args__ = (
        UniqueConstraint('organization_id','granted_by','request_key'),
        ForeignKeyConstraint(['organization_id','domain_id'],['person_data_domain.organization_id','person_data_domain.id']),
        ForeignKeyConstraint(['organization_id','user_id'],['user.organization_id','user.id']),
        ForeignKeyConstraint(['organization_id','granted_by'],['user.organization_id','user.id']),
        ForeignKeyConstraint(['organization_id','revoked_by'],['user.organization_id','user.id']),
    )
