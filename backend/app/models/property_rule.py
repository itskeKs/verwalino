"""Canonical approved property rules, their graph, and source citations."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PropertyRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "property_rule"

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    topic: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    scope_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    effect_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="EFFECTIVE")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_import_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    approved_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR(),
        Computed("to_tsvector('german'::regconfig, coalesce(search_text, ''))", persisted=True),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "property_id", "stable_key", name="uq_property_rule_property_stable_key"),
        UniqueConstraint("organization_id", "property_id", "id", name="uq_property_rule_organization_property_id"),
        ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"], name="fk_property_rule_property_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "source_import_id"], ["declaration_import.organization_id", "declaration_import.id"], name="fk_property_rule_import_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "approved_by"], ["user.organization_id", "user.id"], name="fk_property_rule_approver_tenant", ondelete="RESTRICT"),
        CheckConstraint("status IN ('EFFECTIVE', 'SUPERSEDED', 'NEEDS_CLARIFICATION', 'RETRACTED')", name=conv("ck_property_rule_status")),
        CheckConstraint("valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from", name=conv("ck_property_rule_validity")),
        Index("ix_property_rule_org_property", "organization_id", "property_id"),
        Index("ix_property_rule_source_import", "source_import_id"),
        Index("ix_property_rule_approved_by", "approved_by"),
    )


class PropertyRuleRelation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "property_rule_relation"

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_rule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_rule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_import_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("source_rule_id", "target_rule_id", "relation_type", name="uq_property_rule_relation_edge"),
        ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"], name="fk_property_rule_relation_property_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "property_id", "source_rule_id"], ["property_rule.organization_id", "property_rule.property_id", "property_rule.id"], name="fk_property_rule_relation_source_property_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "property_id", "target_rule_id"], ["property_rule.organization_id", "property_rule.property_id", "property_rule.id"], name="fk_property_rule_relation_target_property_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "source_import_id"], ["declaration_import.organization_id", "declaration_import.id"], name="fk_property_rule_relation_import_tenant", ondelete="RESTRICT"),
        CheckConstraint("relation_type IN ('SUPERSEDES', 'SUPPLEMENTS', 'CONFLICTS')", name=conv("ck_property_rule_relation_type")),
        CheckConstraint("source_rule_id <> target_rule_id", name=conv("ck_property_rule_relation_distinct_rules")),
        Index("ix_property_rule_relation_org_property", "organization_id", "property_id"),
        Index("ix_property_rule_relation_source_rule", "organization_id", "property_id", "source_rule_id"),
        Index("ix_property_rule_relation_target_rule", "organization_id", "property_id", "target_rule_id"),
        Index("ix_property_rule_relation_source_import", "source_import_id"),
    )


class PropertyRuleSource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "property_rule_source"

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    property_rule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    property_document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    pdf_page: Mapped[int] = mapped_column(Integer, nullable=False)
    printed_page: Mapped[str | None] = mapped_column(String(40), nullable=True)
    section: Mapped[str | None] = mapped_column(String(160), nullable=True)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    field_path: Mapped[str | None] = mapped_column(String(160), nullable=True)

    __table_args__ = (
        UniqueConstraint("property_rule_id", "property_document_id", "pdf_page", "field_path", name="uq_property_rule_source_location"),
        ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"], name="fk_property_rule_source_property_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "property_id", "property_rule_id"], ["property_rule.organization_id", "property_rule.property_id", "property_rule.id"], name="fk_property_rule_source_rule_property_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "property_id", "property_document_id"], ["property_document.organization_id", "property_document.property_id", "property_document.id"], name="fk_property_rule_source_document_property_tenant", ondelete="RESTRICT"),
        CheckConstraint("pdf_page > 0", name=conv("ck_property_rule_source_positive_page")),
        CheckConstraint("btrim(quote) <> ''", name=conv("ck_property_rule_source_nonblank_quote")),
        Index("ix_property_rule_source_org_property", "organization_id", "property_id"),
        Index("ix_property_rule_source_rule", "organization_id", "property_id", "property_rule_id"),
        Index("ix_property_rule_source_document", "organization_id", "property_id", "property_document_id"),
    )
