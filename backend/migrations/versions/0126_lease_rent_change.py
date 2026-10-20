"""T23: append-only effective-dated rental rates with parent-lease visibility.

Revision ID: 0126
Revises: 0125
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0126"
down_revision = "0125"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_lease_agreement_org_id", "lease_agreement", ["organization_id", "id"])
    op.create_table(
        "lease_rent_change",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("lease_agreement_id", UUID(as_uuid=True), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("base_rent", sa.Numeric(14, 2), nullable=False),
        sa.Column("advance_payment", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id", "lease_agreement_id"],
            ["lease_agreement.organization_id", "lease_agreement.id"],
            name="fk_lease_rent_change_lease_org", ondelete="RESTRICT"),
        sa.UniqueConstraint("lease_agreement_id", "valid_from", name="uq_lease_rent_change_lease_date"),
        sa.CheckConstraint("base_rent >= 0 AND base_rent < 'Infinity'::numeric", name="base_rent_finite_nonnegative"),
        sa.CheckConstraint("advance_payment >= 0 AND advance_payment < 'Infinity'::numeric", name="advance_finite_nonnegative"),
    )
    op.create_index("ix_lease_rent_change_organization_id", "lease_rent_change", ["organization_id"])
    op.execute("""
        ALTER TABLE lease_rent_change ENABLE ROW LEVEL SECURITY;
        ALTER TABLE lease_rent_change FORCE ROW LEVEL SECURITY;
        GRANT SELECT, INSERT ON lease_rent_change TO app_rw;

        CREATE POLICY lease_rent_change_read ON lease_rent_change FOR SELECT USING (
            organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
            AND scope_actor_role(organization_id) IS NOT NULL
            AND EXISTS (
                SELECT 1 FROM lease_agreement l
                WHERE l.organization_id = lease_rent_change.organization_id
                  AND l.id = lease_rent_change.lease_agreement_id
            )
        );
        CREATE POLICY lease_rent_change_insert ON lease_rent_change FOR INSERT WITH CHECK (
            organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
            AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK')
            AND EXISTS (
                SELECT 1 FROM lease_agreement l
                WHERE l.organization_id = lease_rent_change.organization_id
                  AND l.id = lease_rent_change.lease_agreement_id
                  AND property_history_row_access(l.organization_id,'lease_agreement',to_jsonb(l),'prepare')
            )
        );

        CREATE FUNCTION protect_lease_rent_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Rent history is append-only: create a new effective date';
        END;
        $$;
        CREATE TRIGGER protect_lease_rent_change BEFORE UPDATE OR DELETE ON lease_rent_change
            FOR EACH ROW EXECUTE FUNCTION protect_lease_rent_change();
        REVOKE ALL ON FUNCTION protect_lease_rent_change() FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER protect_lease_rent_change ON lease_rent_change")
    op.execute("DROP FUNCTION protect_lease_rent_change()")
    op.drop_table("lease_rent_change")
    op.drop_constraint("uq_lease_agreement_org_id", "lease_agreement", type_="unique")
