"""Durable bounded ordinary-invoice extraction; no rewrite of legacy payloads."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_invoice_document_org_id", "invoice_document", ["organization_id", "id"])
    op.add_column("invoice_document", sa.Column("processing_status", sa.String(24), nullable=False, server_default="legacy"))
    op.add_column("invoice_document", sa.Column("processing_error_code", sa.String(64)))
    op.create_table("invoice_processing_attempt",
        sa.Column("id", UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(), nullable=False),
        sa.Column("document_id", UUID(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("token", UUID()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("processor_version", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["organization_id", "document_id"], ["invoice_document.organization_id", "invoice_document.id"], name="fk_invoice_processing_document_tenant", ondelete="RESTRICT"),
        sa.UniqueConstraint("organization_id", "document_id", "attempt_no", name="uq_invoice_processing_attempt"),
        sa.CheckConstraint("attempt_no BETWEEN 1 AND 3", name="invoice_attempt_budget"),
        sa.CheckConstraint("status IN ('queued','running','succeeded','needs_review','unsupported','unavailable','timeout','failed','canceled')", name="invoice_attempt_status"),
        sa.CheckConstraint("status <> 'running' OR (token IS NOT NULL AND lease_until IS NOT NULL)", name="invoice_attempt_lease"),
    )
    op.create_index("ix_invoice_attempt_queue", "invoice_processing_attempt", ["organization_id", "status", "created_at", "id"])
    op.create_index("uq_invoice_attempt_active", "invoice_processing_attempt", ["organization_id", "document_id"], unique=True,
                    postgresql_where=sa.text("status IN ('queued','running')"))
    op.execute("ALTER TABLE invoice_processing_attempt ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invoice_processing_attempt FORCE ROW LEVEL SECURITY")
    scope = "organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid"
    op.execute(f"CREATE POLICY invoice_attempt_select ON invoice_processing_attempt FOR SELECT USING ({scope})")
    op.execute(f"CREATE POLICY invoice_attempt_insert ON invoice_processing_attempt FOR INSERT WITH CHECK ({scope})")
    op.execute(f"CREATE POLICY invoice_attempt_update ON invoice_processing_attempt FOR UPDATE USING ({scope}) WITH CHECK ({scope})")
    op.execute("REVOKE ALL ON invoice_processing_attempt FROM app_rw")
    op.execute("GRANT SELECT, INSERT, UPDATE ON invoice_processing_attempt TO app_rw")


def downgrade():
    # Disposable test teardown only: operational rollback keeps attempts/originals.
    op.drop_table("invoice_processing_attempt")
    op.drop_column("invoice_document", "processing_error_code")
    op.drop_column("invoice_document", "processing_status")
    op.drop_constraint("uq_invoice_document_org_id", "invoice_document", type_="unique")
