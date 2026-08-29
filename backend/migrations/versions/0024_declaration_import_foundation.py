"""Tenant-isolated legal source, import, and review foundation.

Revision ID: 0024
Revises: 0023
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_rls(table_name: str) -> None:
    op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY org_isolation_select ON "{table_name}" FOR SELECT '
        "USING (organization_id = "
        "NULLIF(current_setting('app.current_org_id', true), '')::uuid)"
    )
    op.execute(
        f'CREATE POLICY org_isolation_insert ON "{table_name}" FOR INSERT '
        "WITH CHECK (organization_id = "
        "NULLIF(current_setting('app.current_org_id', true), '')::uuid)"
    )
    op.execute(
        f'CREATE POLICY org_isolation_update ON "{table_name}" FOR UPDATE '
        "USING (organization_id = "
        "NULLIF(current_setting('app.current_org_id', true), '')::uuid) "
        "WITH CHECK (organization_id = "
        "NULLIF(current_setting('app.current_org_id', true), '')::uuid)"
    )
    op.execute(
        f'CREATE POLICY org_isolation_delete ON "{table_name}" FOR DELETE '
        "USING (organization_id = "
        "NULLIF(current_setting('app.current_org_id', true), '')::uuid)"
    )


def _tenant_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organization.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
    ]


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_property_organization_id_id", "property", ["organization_id", "id"]
    )
    op.create_unique_constraint(
        "uq_user_organization_id_id", "user", ["organization_id", "id"]
    )

    op.create_table(
        "property_document",
        *_tenant_columns(),
        sa.Column(
            "property_id",
            UUID(as_uuid=True),
            nullable=True,
            index=True,
        ),
        sa.Column("doc_type", sa.String(20), nullable=False),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("deed_reference", sa.String(255), nullable=True),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("content_byte_size", sa.BigInteger(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("storage_uri", sa.String(1024), nullable=False),
        sa.Column("storage_backend", sa.String(16), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column(
            "uploaded_by",
            UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_property_document_organization_id_id"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "property_id"],
            ["property.organization_id", "property.id"],
            name="fk_property_document_property_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "uploaded_by"],
            ["user.organization_id", "user.id"],
            name="fk_property_document_uploaded_by_tenant",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "doc_type IN ('DECLARATION', 'AMENDMENT', 'ANNEX', 'OTHER')",
            name="ck_property_document_doc_type",
        ),
        sa.CheckConstraint(
            "content_byte_size > 0", name="ck_property_document_positive_size"
        ),
        sa.CheckConstraint(
            "page_count > 0", name="ck_property_document_positive_pages"
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_property_document_sha256_lower_hex",
        ),
    )
    _add_rls("property_document")

    op.create_table(
        "declaration_import",
        *_tenant_columns(),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column(
            "target_property_id",
            UUID(as_uuid=True),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "applied_property_id",
            UUID(as_uuid=True),
            nullable=True,
            index=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("draft_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("progress_percent", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("current_stage", sa.String(120), nullable=True),
        sa.Column(
            "initiated_by",
            UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "approved_by",
            UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "estimated_cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"
        ),
        sa.Column("max_cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(120), nullable=True),
        sa.Column("lease_token", UUID(as_uuid=True), nullable=True),
        sa.Column("lease_generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_declaration_import_organization_id_id"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "target_property_id"],
            ["property.organization_id", "property.id"],
            name="fk_declaration_import_target_property_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "applied_property_id"],
            ["property.organization_id", "property.id"],
            name="fk_declaration_import_applied_property_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "initiated_by"],
            ["user.organization_id", "user.id"],
            name="fk_declaration_import_initiated_by_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "approved_by"],
            ["user.organization_id", "user.id"],
            name="fk_declaration_import_approved_by_tenant",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "mode IN ('NEW_PROPERTY', 'EXISTING_PROPERTY')",
            name="ck_declaration_import_mode",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'QUEUED', 'PREFLIGHT', 'EXTRACTING', "
            "'RECONCILING', 'RETRY_WAIT', 'NEEDS_REVIEW', 'APPLYING', "
            "'APPLIED', 'FAILED', 'CANCELLED')",
            name="ck_declaration_import_status",
        ),
        sa.CheckConstraint(
            "(mode = 'NEW_PROPERTY' AND target_property_id IS NULL) OR "
            "(mode = 'EXISTING_PROPERTY' AND target_property_id IS NOT NULL)",
            name="ck_declaration_import_target_property",
        ),
        sa.CheckConstraint(
            "progress_percent BETWEEN 0 AND 100",
            name="ck_declaration_import_progress_percent",
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND "
            "estimated_cost_usd >= 0 AND max_cost_usd >= 0",
            name="ck_declaration_import_nonnegative_usage",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_declaration_import_lease_fields",
        ),
        sa.CheckConstraint(
            "(status IN ('DRAFT', 'QUEUED', 'PREFLIGHT', 'EXTRACTING', "
            "'RECONCILING', 'RETRY_WAIT', 'NEEDS_REVIEW', 'FAILED', 'CANCELLED') "
            "AND approved_by IS NULL AND approved_at IS NULL "
            "AND applied_property_id IS NULL) "
            "OR (status = 'APPLYING' AND approved_by IS NOT NULL "
            "AND approved_at IS NOT NULL AND applied_property_id IS NULL) "
            "OR (status = 'APPLIED' AND approved_by IS NOT NULL "
            "AND approved_at IS NOT NULL AND applied_property_id IS NOT NULL)",
            name="ck_declaration_import_approval_fields",
        ),
    )
    _add_rls("declaration_import")

    op.create_table(
        "declaration_import_document",
        *_tenant_columns(),
        sa.Column(
            "import_id",
            UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("document_role", sa.String(20), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id", "import_id"],
            ["declaration_import.organization_id", "declaration_import.id"],
            name="fk_declaration_import_document_import_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["property_document.organization_id", "property_document.id"],
            name="fk_declaration_import_document_document_tenant",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("import_id", "document_id", name="uq_declaration_import_document"),
        sa.UniqueConstraint("import_id", "sequence_no", name="uq_declaration_import_sequence"),
        sa.CheckConstraint(
            "document_role IN ('DECLARATION', 'AMENDMENT', 'ANNEX', 'OTHER')",
            name="ck_declaration_import_document_role",
        ),
    )
    _add_rls("declaration_import_document")

    op.create_table(
        "declaration_extraction_run",
        *_tenant_columns(),
        sa.Column(
            "import_id",
            UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column("status", sa.String(12), nullable=False, server_default="RUNNING"),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("prompt_version", sa.String(120), nullable=False),
        sa.Column("schema_version", sa.String(120), nullable=False),
        sa.Column("harness_version", sa.String(120), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "estimated_cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"
        ),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id", "import_id"],
            ["declaration_import.organization_id", "declaration_import.id"],
            name="fk_declaration_extraction_run_import_tenant",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_declaration_extraction_run_status",
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND estimated_cost_usd >= 0",
            name="ck_declaration_extraction_run_nonnegative_usage",
        ),
    )
    _add_rls("declaration_extraction_run")

    op.create_table(
        "declaration_candidate",
        *_tenant_columns(),
        sa.Column(
            "import_id",
            UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("candidate_type", sa.String(24), nullable=False),
        sa.Column("subject_key", sa.String(180), nullable=False),
        sa.Column("field_key", sa.String(100), nullable=True),
        sa.Column("operation", sa.String(10), nullable=False),
        sa.Column("previous_value_json", JSONB(), nullable=True),
        sa.Column("proposed_value_json", JSONB(), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("risk_class", sa.String(10), nullable=False),
        sa.Column(
            "requires_individual_review", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("review_status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column(
            "reviewed_by",
            UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("candidate_version", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_declaration_candidate_organization_id_id"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "import_id"],
            ["declaration_import.organization_id", "declaration_import.id"],
            name="fk_declaration_candidate_import_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reviewed_by"],
            ["user.organization_id", "user.id"],
            name="fk_declaration_candidate_reviewed_by_tenant",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("import_id", "stable_key", name="uq_declaration_candidate_stable_key"),
        sa.CheckConstraint(
            "candidate_type IN ('PROPERTY_FIELD', 'LEGAL_REFERENCE', 'UNIT_CREATE', "
            "'UNIT_FIELD', 'MEA_RESCALE', 'ASSIGNMENT', 'RULE', 'DOCUMENT_RELATION')",
            name="ck_declaration_candidate_type",
        ),
        sa.CheckConstraint(
            "operation IN ('CREATE', 'UPDATE', 'KEEP')",
            name="ck_declaration_candidate_operation",
        ),
        sa.CheckConstraint(
            "risk_class IN ('LOW', 'MEDIUM', 'CRITICAL')",
            name="ck_declaration_candidate_risk_class",
        ),
        sa.CheckConstraint(
            "review_status IN ('PENDING', 'ACCEPTED', 'CORRECTED', 'REJECTED', "
            "'NEEDS_CLARIFICATION')",
            name="ck_declaration_candidate_review_status",
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1", name="ck_declaration_candidate_confidence"
        ),
        sa.CheckConstraint(
            "(review_status = 'PENDING' AND reviewed_by IS NULL AND reviewed_at IS NULL) "
            "OR (review_status <> 'PENDING' AND reviewed_by IS NOT NULL "
            "AND reviewed_at IS NOT NULL)",
            name="ck_declaration_candidate_review_fields",
        ),
    )
    _add_rls("declaration_candidate")

    op.create_table(
        "declaration_candidate_evidence",
        *_tenant_columns(),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column("field_path", sa.String(160), nullable=True),
        sa.Column("pdf_page", sa.Integer(), nullable=False),
        sa.Column("printed_page", sa.String(40), nullable=True),
        sa.Column("section_ref", sa.String(160), nullable=True),
        sa.Column("quote", sa.String(2000), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id", "candidate_id"],
            ["declaration_candidate.organization_id", "declaration_candidate.id"],
            name="fk_declaration_candidate_evidence_candidate_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["property_document.organization_id", "property_document.id"],
            name="fk_declaration_candidate_evidence_document_tenant",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "pdf_page > 0", name="ck_declaration_candidate_evidence_positive_page"
        ),
    )
    _add_rls("declaration_candidate_evidence")

    op.create_table(
        "declaration_candidate_issue",
        *_tenant_columns(),
        sa.Column(
            "import_id",
            UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            nullable=True,
            index=True,
        ),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details_json", JSONB(), nullable=True),
        sa.Column("resolution_status", sa.String(10), nullable=False, server_default="OPEN"),
        sa.Column(
            "resolved_by",
            UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id", "import_id"],
            ["declaration_import.organization_id", "declaration_import.id"],
            name="fk_declaration_candidate_issue_import_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "candidate_id"],
            ["declaration_candidate.organization_id", "declaration_candidate.id"],
            name="fk_declaration_candidate_issue_candidate_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resolved_by"],
            ["user.organization_id", "user.id"],
            name="fk_declaration_candidate_issue_resolved_by_tenant",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "severity IN ('INFO', 'WARNING', 'BLOCKER')",
            name="ck_declaration_candidate_issue_severity",
        ),
        sa.CheckConstraint(
            "resolution_status IN ('OPEN', 'RESOLVED')",
            name="ck_declaration_candidate_issue_resolution_status",
        ),
        sa.CheckConstraint(
            "(resolution_status = 'OPEN' AND resolved_by IS NULL AND resolved_at IS NULL) "
            "OR (resolution_status = 'RESOLVED' AND resolved_by IS NOT NULL "
            "AND resolved_at IS NOT NULL)",
            name="ck_declaration_candidate_issue_resolution_fields",
        ),
    )
    _add_rls("declaration_candidate_issue")

    op.execute(
        """
        CREATE FUNCTION prevent_property_document_content_metadata_update()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.content_byte_size IS DISTINCT FROM OLD.content_byte_size
               OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
               OR NEW.storage_uri IS DISTINCT FROM OLD.storage_uri
               OR NEW.storage_backend IS DISTINCT FROM OLD.storage_backend
               OR NEW.mime_type IS DISTINCT FROM OLD.mime_type
               OR NEW.original_filename IS DISTINCT FROM OLD.original_filename THEN
                RAISE EXCEPTION 'property_document content metadata is immutable';
            END IF;
            IF OLD.property_id IS NOT NULL
               AND NEW.property_id IS DISTINCT FROM OLD.property_id THEN
                RAISE EXCEPTION 'property_document property binding is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER prevent_property_document_content_metadata_update
        BEFORE UPDATE ON property_document
        FOR EACH ROW EXECUTE FUNCTION prevent_property_document_content_metadata_update()
        """
    )

    for table_name in ("property_document", "declaration_extraction_run"):
        op.execute(f'GRANT SELECT, INSERT, UPDATE ON "{table_name}" TO app_rw')
        op.execute(f'REVOKE DELETE ON "{table_name}" FROM app_rw')
    for table_name in (
        "declaration_import",
        "declaration_import_document",
        "declaration_candidate",
        "declaration_candidate_evidence",
        "declaration_candidate_issue",
    ):
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table_name}" TO app_rw')


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS prevent_property_document_content_metadata_update "
        "ON property_document"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_property_document_content_metadata_update()")
    for table_name in (
        "declaration_candidate_issue",
        "declaration_candidate_evidence",
        "declaration_candidate",
        "declaration_extraction_run",
        "declaration_import_document",
        "declaration_import",
        "property_document",
    ):
        op.drop_table(table_name)
    op.execute('ALTER TABLE "user" DROP CONSTRAINT IF EXISTS uq_user_organization_id_id')
    op.execute(
        'ALTER TABLE "property" DROP CONSTRAINT IF EXISTS uq_property_organization_id_id'
    )
