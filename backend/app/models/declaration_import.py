"""Declaration import workflow and extraction-run persistence models."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DeclarationImport(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "declaration_import"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    target_property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    applied_property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="DRAFT")
    draft_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    progress_percent: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    current_stage: Mapped[str | None] = mapped_column(String(120), nullable=True)
    initiated_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, server_default="0"
    )
    max_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    lease_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approval_idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    approval_result_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_declaration_import_organization_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "target_property_id"],
            ["property.organization_id", "property.id"],
            name="fk_declaration_import_target_property_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "applied_property_id"],
            ["property.organization_id", "property.id"],
            name="fk_declaration_import_applied_property_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "initiated_by"],
            ["user.organization_id", "user.id"],
            name="fk_declaration_import_initiated_by_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "approved_by"],
            ["user.organization_id", "user.id"],
            name="fk_declaration_import_approved_by_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(status = 'APPLIED' AND approval_idempotency_key IS NOT NULL "
            "AND approval_result_json IS NOT NULL) OR "
            "(status <> 'APPLIED' AND approval_idempotency_key IS NULL "
            "AND approval_result_json IS NULL)",
            name=conv("ck_declaration_import_approval_result_fields"),
        ),
    )


class DeclarationImportDocument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "declaration_import_document"

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
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    document_role: Mapped[str] = mapped_column(String(20), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "import_id"],
            ["declaration_import.organization_id", "declaration_import.id"],
            name="fk_declaration_import_document_import_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["property_document.organization_id", "property_document.id"],
            name="fk_declaration_import_document_document_tenant",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("import_id", "document_id", name="uq_declaration_import_document"),
        UniqueConstraint("import_id", "sequence_no", name="uq_declaration_import_sequence"),
    )


class DeclarationExtractionRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "declaration_extraction_run"

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
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="RUNNING")
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(120), nullable=False)
    harness_version: Mapped[str] = mapped_column(String(120), nullable=False)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, server_default="0"
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "id",
            name="uq_declaration_extraction_run_organization_id_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "import_id"],
            ["declaration_import.organization_id", "declaration_import.id"],
            name="fk_declaration_extraction_run_import_tenant",
            ondelete="RESTRICT",
        ),
    )


class DeclarationImportPass(Base, UUIDPrimaryKeyMixin):
    """Append-only, content-free provider attempt evidence for one extraction run."""

    __tablename__ = "declaration_import_pass"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    declaration_extraction_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    safe_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "declaration_extraction_run_id"],
            [
                "declaration_extraction_run.organization_id",
                "declaration_extraction_run.id",
            ],
            name="fk_declaration_import_pass_run_tenant",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "organization_id",
            "declaration_extraction_run_id",
            "attempt_no",
            name="uq_declaration_import_pass_run_attempt",
        ),
        CheckConstraint("attempt_no > 0", name="ck_declaration_import_pass_positive_attempt"),
        CheckConstraint(
            "declaration_import_pass_safe_metadata_is_valid(safe_metadata, attempt_no)",
            name="ck_declaration_import_pass_safe_metadata",
        ),
    )
