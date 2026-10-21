"""Attachment -- general document metadata with retained originals.

Tenant-behaftet: carries organization_id, RLS active.
Immutable original metadata; one-way archive audit.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class Attachment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "attachment"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Parent links (property mandatory, others optional)
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("property.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("unit.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    lease_agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lease_agreement.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # Original metadata (immutable after upload)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content_byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(50), nullable=False, default="LOCAL_FS")
    storage_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # Archive audit (one-way, append-only via trigger)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    archive_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    archived_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="RESTRICT"), nullable=True
    )
