"""Immutable provenance for approved canonical property and unit facts."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ApprovedFactSource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "approved_fact_source"

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    subject_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    field_key: Mapped[str] = mapped_column(String(160), nullable=False)
    approved_value_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    value_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="EFFECTIVE")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    property_document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    pdf_page: Mapped[int] = mapped_column(Integer, nullable=False)
    printed_page: Mapped[str | None] = mapped_column(String(40), nullable=True)
    section: Mapped[str | None] = mapped_column(String(160), nullable=True)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    source_import_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR(),
        Computed("to_tsvector('german'::regconfig, coalesce(search_text, ''))", persisted=True),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("subject_kind", "subject_id", "field_key", "value_fingerprint", "property_document_id", "pdf_page", "source_import_id", name="uq_approved_fact_source_evidence"),
        ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"], name="fk_approved_fact_source_property_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "property_id", "property_document_id"], ["property_document.organization_id", "property_document.property_id", "property_document.id"], name="fk_approved_fact_source_document_property_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "source_import_id"], ["declaration_import.organization_id", "declaration_import.id"], name="fk_approved_fact_source_import_tenant", ondelete="RESTRICT"),
        CheckConstraint("status IN ('EFFECTIVE', 'SUPERSEDED')", name=conv("ck_approved_fact_source_status")),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name=conv("ck_approved_fact_source_validity")),
        CheckConstraint("(status = 'EFFECTIVE' AND valid_to IS NULL) OR (status = 'SUPERSEDED' AND valid_to IS NOT NULL)", name=conv("ck_approved_fact_source_lifecycle")),
        CheckConstraint("pdf_page > 0", name=conv("ck_approved_fact_source_positive_page")),
        CheckConstraint("btrim(quote) <> ''", name=conv("ck_approved_fact_source_nonblank_quote")),
        CheckConstraint("value_fingerprint ~ '^[0-9a-f]{64}$'", name=conv("ck_approved_fact_source_fingerprint")),
        Index("ix_approved_fact_source_org_property", "organization_id", "property_id"),
        Index("ix_approved_fact_source_subject_field", "subject_kind", "subject_id", "field_key"),
        Index("ix_approved_fact_source_document", "organization_id", "property_id", "property_document_id"),
        Index("ix_approved_fact_source_source_import", "source_import_id"),
    )
