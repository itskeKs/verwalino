"""InvoiceDocument -- a stored document (PDF/XML) linked to a cost entry.

SDD section 6.6/13, m2-gobd-belegfluss.md section 4.
Tenant-behaftet (organization_id, RLS).
Immutable once released_at IS NOT NULL (ADR-0003 trigger).
No DELETE grant for app_rw.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, DateTime, Date, ForeignKey, ForeignKeyConstraint, UniqueConstraint, Index, text, SmallInteger, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class InvoiceDocument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "invoice_document"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_invoice_document_org_id"),
        ForeignKeyConstraint(["organization_id", "cost_entry_id"], ["cost_entry.organization_id", "cost_entry.id"], name="fk_invoice_document_cost_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "uploaded_by"], ["user.organization_id", "user.id"], name="fk_invoice_uploader"),
        Index("uq_invoice_document_active_cost", "organization_id", "cost_entry_id", unique=True,
              postgresql_where=text("status = 'active' AND cost_entry_id IS NOT NULL")),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    cost_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    storage_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_version: Mapped[str | None] = mapped_column(String(128), nullable=True)

    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content_byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )

    # Version chain -- backward pointer (architecture.md section 5.4, decision E-4)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoice_document.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # Denormalized chain root for partial unique index (decision E-5)
    chain_root_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default="active"
    )

    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    retention_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    extraction_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    processing_status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="legacy")
    processing_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
