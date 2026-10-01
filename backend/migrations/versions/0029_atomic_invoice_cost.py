"""Atomic invoice/cost scope and append-only operation receipts.

Legacy ambiguity aborts before any constraint changes. Run the companion
reconciliation query before rollout; never infer or rewrite released links.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM invoice_document d JOIN cost_entry c ON c.id=d.cost_entry_id
                     WHERE d.organization_id <> c.organization_id)
             OR EXISTS (SELECT 1 FROM invoice_document WHERE status='active' AND cost_entry_id IS NOT NULL
                        GROUP BY organization_id,cost_entry_id HAVING count(*)>1) THEN
            RAISE EXCEPTION 'Invoice link reconciliation required; see docs/operations/invoice-cost-linking.md';
          END IF;
        END $$;
    """)
    op.create_unique_constraint("uq_cost_entry_organization_id_id", "cost_entry", ["organization_id", "id"])
    op.drop_constraint("fk_invoice_document_cost_entry_id_cost_entry", "invoice_document", type_="foreignkey")
    op.create_foreign_key("fk_invoice_document_cost_tenant", "invoice_document", "cost_entry",
                         ["organization_id", "cost_entry_id"], ["organization_id", "id"], ondelete="RESTRICT")
    op.create_index("uq_invoice_document_active_cost", "invoice_document", ["organization_id", "cost_entry_id"],
                    unique=True, postgresql_where=sa.text("status='active' AND cost_entry_id IS NOT NULL"))
    op.create_table("operation_receipt",
        sa.Column("id", UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("actor_user_id", UUID(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("result_id", UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("organization_id", "actor_user_id", "operation", "key", name="uq_operation_receipt_key"),
        sa.ForeignKeyConstraint(["organization_id", "result_id"], ["cost_entry.organization_id", "cost_entry.id"],
                                name="fk_operation_receipt_cost_tenant", ondelete="RESTRICT"),
    )
    op.execute("ALTER TABLE operation_receipt ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE operation_receipt FORCE ROW LEVEL SECURITY")
    scope = "organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid"
    op.execute(f"CREATE POLICY operation_receipt_select ON operation_receipt FOR SELECT USING ({scope})")
    op.execute(f"CREATE POLICY operation_receipt_insert ON operation_receipt FOR INSERT WITH CHECK ({scope})")
    op.execute("REVOKE ALL ON operation_receipt FROM app_rw")
    op.execute("GRANT SELECT, INSERT ON operation_receipt TO app_rw")


def downgrade():
    # Test teardown only. Operational rollback disables writers and keeps evidence.
    op.drop_table("operation_receipt")
    op.drop_index("uq_invoice_document_active_cost", table_name="invoice_document")
    op.drop_constraint("fk_invoice_document_cost_tenant", "invoice_document", type_="foreignkey")
    op.create_foreign_key("fk_invoice_document_cost_entry_id_cost_entry", "invoice_document", "cost_entry",
                         ["cost_entry_id"], ["id"], ondelete="RESTRICT")
    op.drop_constraint("uq_cost_entry_organization_id_id", "cost_entry", type_="unique")
