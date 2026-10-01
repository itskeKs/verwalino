"""Append-only financial coverage; serialize source changes with finalization."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None
SOURCES = ("property", "unit", "cost_entry", "payment", "bank_account", "lease_agreement", "ownership_history", "economic_plan", "economic_plan_line")


def upgrade():
    for table in ("settlement_run", "economic_plan"):
        op.create_unique_constraint(f"uq_{table}_org_id", table, ["organization_id", "id"])
    op.create_table("coverage_declaration",
        sa.Column("id", UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(), nullable=False),
        sa.Column("settlement_run_id", UUID()), sa.Column("economic_plan_id", UUID()),
        sa.Column("property_id", UUID(), nullable=False),
        sa.Column("component", sa.String(40), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False), sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("state", sa.String(16), nullable=False),
        sa.Column("values", JSONB(), nullable=False), sa.Column("source_document_ids", JSONB(), nullable=False),
        sa.Column("source_revision", sa.String(64), nullable=False), sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("attestor_id", UUID(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("num_nonnulls(settlement_run_id,economic_plan_id)=1", name="coverage_one_subject"),
        sa.CheckConstraint("state='known' AND revision>0 AND period_end>=period_start AND length(trim(reason))>0", name="coverage_known_evidence"),
        *[sa.ForeignKeyConstraint(["organization_id", column], [f"{target}.organization_id", f"{target}.id"], name=f"fk_coverage_{column}_tenant", ondelete="RESTRICT")
          for column, target in [("settlement_run_id", "settlement_run"), ("economic_plan_id", "economic_plan"), ("property_id", "property")]],
    )
    for column in ("settlement_run_id", "economic_plan_id"):
        op.create_index(f"uq_coverage_{column}_revision", "coverage_declaration", ["organization_id", column, "revision"], unique=True)
    scope = "organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid"
    op.execute("ALTER TABLE coverage_declaration ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE coverage_declaration FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY coverage_select ON coverage_declaration FOR SELECT USING ({scope})")
    op.execute(f"CREATE POLICY coverage_insert ON coverage_declaration FOR INSERT WITH CHECK ({scope})")
    op.execute("REVOKE ALL ON coverage_declaration FROM app_rw")
    op.execute("GRANT SELECT, INSERT ON coverage_declaration TO app_rw")
    op.execute("""CREATE FUNCTION lock_financial_input_sources() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
        DECLARE org uuid;
        BEGIN
          IF TG_OP='DELETE' THEN org:=OLD.organization_id; ELSE org:=NEW.organization_id; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:' || org::text,0));
          IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END $$""")
    for table in SOURCES:
        op.execute(f"CREATE TRIGGER lock_financial_inputs BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION lock_financial_input_sources()")


def downgrade():
    # Disposable teardown only. Disable finalizers instead of removing this guard.
    for table in SOURCES:
        op.execute(f"DROP TRIGGER IF EXISTS lock_financial_inputs ON {table}")
    op.execute("DROP FUNCTION lock_financial_input_sources()")
    op.drop_table("coverage_declaration")
    for table in ("settlement_run", "economic_plan"):
        op.drop_constraint(f"uq_{table}_org_id", table, type_="unique")
