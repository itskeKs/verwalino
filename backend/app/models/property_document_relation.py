"""Approved relationships between source documents of a property."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PropertyDocumentRelation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "property_document_relation"

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="EFFECTIVE")
    source_import_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("source_document_id", "target_document_id", "relation_type", name="uq_property_document_relation_edge"),
        ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"], name="fk_property_document_relation_property_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "property_id", "source_document_id"], ["property_document.organization_id", "property_document.property_id", "property_document.id"], name="fk_property_document_relation_source_property_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "property_id", "target_document_id"], ["property_document.organization_id", "property_document.property_id", "property_document.id"], name="fk_property_document_relation_target_property_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "source_import_id"], ["declaration_import.organization_id", "declaration_import.id"], name="fk_property_document_relation_import_tenant", ondelete="RESTRICT"),
        CheckConstraint("relation_type IN ('AMENDS', 'SUPPLEMENTS', 'REFERENCES')", name=conv("ck_property_document_relation_type")),
        CheckConstraint("status IN ('EFFECTIVE', 'SUPERSEDED', 'NEEDS_CLARIFICATION')", name=conv("ck_property_document_relation_status")),
        CheckConstraint("source_document_id <> target_document_id", name=conv("ck_property_document_relation_distinct_documents")),
        Index("ix_property_document_relation_org_property", "organization_id", "property_id"),
        Index("ix_property_document_relation_source_document", "organization_id", "property_id", "source_document_id"),
        Index("ix_property_document_relation_target_document", "organization_id", "property_id", "target_document_id"),
        Index("ix_property_document_relation_source_import", "source_import_id"),
    )
