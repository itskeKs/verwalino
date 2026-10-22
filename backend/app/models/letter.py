"""Editable templates and immutable issued letter metadata."""
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, ForeignKeyConstraint, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class LetterTemplate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "letter_template"
    __table_args__ = (UniqueConstraint("organization_id", "id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)


class LetterDocument(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "letter_document"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "template_id"], ["letter_template.organization_id", "letter_template.id"], ondelete="RESTRICT"),
        UniqueConstraint("organization_id", "request_id", "person_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"))
    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("property.id", ondelete="RESTRICT"))
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("person.id", ondelete="RESTRICT"))
    tenant_profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenant_profile.id", ondelete="RESTRICT"))
    lease_agreement_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("lease_agreement.id", ondelete="RESTRICT"))
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    as_of: Mapped[date] = mapped_column(Date)
    recipient_name: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    storage_uri: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    # Issued documents have no mutable updated_at timestamp.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
