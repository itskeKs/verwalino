"""Review candidate, source evidence, and issue persistence models."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    FetchedValue,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DeclarationCandidate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "declaration_candidate"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    import_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    candidate_type: Mapped[str] = mapped_column(String(24), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(180), nullable=False)
    field_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    operation: Mapped[str] = mapped_column(String(10), nullable=False)
    previous_value_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    proposed_value_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    risk_class: Mapped[str] = mapped_column(String(10), nullable=False)
    requires_individual_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    review_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="PENDING"
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    candidate_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    # SQLAlchemy's PostgreSQL JSON default serializes Python None as JSON null.
    # The database invariant deliberately needs a SQL NULL for every non-corrected
    # decision, so make that distinction explicit at the ORM boundary.
    corrected_value_json: Mapped[dict | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_declaration_candidate_organization_id_id"),
        UniqueConstraint("import_id", "stable_key", name="uq_declaration_candidate_stable_key"),
        UniqueConstraint("organization_id", "import_id", "id", name="uq_declaration_candidate_import_context"),
        ForeignKeyConstraint(
            ["organization_id", "import_id"],
            ["declaration_import.organization_id", "declaration_import.id"],
            name="fk_declaration_candidate_import_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reviewed_by"],
            ["user.organization_id", "user.id"],
            name="fk_declaration_candidate_reviewed_by_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(review_status = 'CORRECTED' AND corrected_value_json IS NOT NULL) "
            "OR (review_status <> 'CORRECTED' AND corrected_value_json IS NULL)",
            name=conv("ck_declaration_candidate_corrected_value"),
        ),
    )


class DeclarationCandidateEvidence(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "declaration_candidate_evidence"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Filled from the immutable candidate parent for older insert callers.
    import_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, server_default=FetchedValue())
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    field_path: Mapped[str | None] = mapped_column(String(160), nullable=True)
    pdf_page: Mapped[int] = mapped_column(Integer, nullable=False)
    printed_page: Mapped[str | None] = mapped_column(String(40), nullable=True)
    section_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    quote: Mapped[str] = mapped_column(String(2000), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "import_id", "candidate_id"],
            ["declaration_candidate.organization_id", "declaration_candidate.import_id", "declaration_candidate.id"],
            name="fk_declaration_evidence_candidate_import_context", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "import_id", "document_id"],
            ["declaration_import_document.organization_id", "declaration_import_document.import_id", "declaration_import_document.document_id"],
            name="fk_declaration_evidence_document_import_context", ondelete="RESTRICT"),
        Index("ix_declaration_evidence_import_document", "organization_id", "import_id", "document_id"),

        ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["property_document.organization_id", "property_document.id"],
            name="fk_declaration_candidate_evidence_document_tenant",
            ondelete="RESTRICT",
        ),
    )


class DeclarationCandidateIssue(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "declaration_candidate_issue"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    import_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    resolution_status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="OPEN"
    )
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "import_id", "candidate_id"],
            ["declaration_candidate.organization_id", "declaration_candidate.import_id", "declaration_candidate.id"],
            name="fk_declaration_issue_import_context", ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["organization_id", "import_id"],
            ["declaration_import.organization_id", "declaration_import.id"],
            name="fk_declaration_candidate_issue_import_tenant",
            ondelete="RESTRICT",
        ),

        ForeignKeyConstraint(
            ["organization_id", "resolved_by"],
            ["user.organization_id", "user.id"],
            name="fk_declaration_candidate_issue_resolved_by_tenant",
            ondelete="RESTRICT",
        ),
    )
