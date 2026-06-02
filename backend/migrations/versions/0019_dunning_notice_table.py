"""M7: dunning_notice table + RLS + immutability trigger.

Creates the dunning_notice table for the Mahnwesen (dunning) module.
Includes:
  - Table with all columns, CHECKs, indices.
  - RLS: ENABLE + FORCE + four policies (fail-closed, same pattern as 0002/0017).
  - Immutability trigger: ADR-0003 pattern, freeze condition status='issued'.
    Once issued, all content-bearing columns are frozen. The ONLY allowed
    mutation is updated_at (technical timestamp). DELETE of an issued notice
    is always forbidden.

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. Table
    # -----------------------------------------------------------------------
    op.create_table(
        "dunning_notice",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False, index=True),
        # Counterparty (exactly one set for each notice)
        sa.Column("tenant_profile_id", UUID(as_uuid=True), sa.ForeignKey("tenant_profile.id", ondelete="RESTRICT"), nullable=True, index=True),
        sa.Column("owner_profile_id", UUID(as_uuid=True), sa.ForeignKey("owner_profile.id", ondelete="RESTRICT"), nullable=True, index=True),
        # Level
        sa.Column("level", sa.SmallInteger, nullable=False),
        # Dates
        sa.Column("issue_date", sa.Date, nullable=False),
        sa.Column("due_date", sa.Date, nullable=False),
        # Amounts
        sa.Column("principal_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("fee_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("total_amount", sa.Numeric(14, 2), nullable=False),
        # Frozen snapshot of included overdue items
        sa.Column("included_items", JSONB, nullable=True),
        # Lifecycle
        sa.Column("status", sa.String(12), nullable=False, server_default="draft"),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_by", UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note", sa.String(1000), nullable=True),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # CHECKs
        sa.CheckConstraint("status IN ('draft', 'issued')", name="ck_dunning_notice_status"),
        sa.CheckConstraint("level BETWEEN 1 AND 3", name="ck_dunning_notice_level"),
        sa.CheckConstraint("principal_amount >= 0", name="ck_dunning_notice_principal_positive"),
        sa.CheckConstraint("fee_amount >= 0", name="ck_dunning_notice_fee_positive"),
        sa.CheckConstraint("total_amount >= 0", name="ck_dunning_notice_total_positive"),
        # Exactly one counterparty must be set.
        sa.CheckConstraint(
            "(tenant_profile_id IS NOT NULL AND owner_profile_id IS NULL) OR "
            "(tenant_profile_id IS NULL AND owner_profile_id IS NOT NULL)",
            name="ck_dunning_notice_one_counterparty",
        ),
    )

    # Additional indices for common query patterns.
    op.create_index("ix_dunning_notice_status", "dunning_notice", ["status"])

    # -----------------------------------------------------------------------
    # 2. RLS (same pattern as 0002/0004/0007/0010/0014/0017)
    # -----------------------------------------------------------------------
    op.execute('ALTER TABLE "dunning_notice" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "dunning_notice" FORCE ROW LEVEL SECURITY')

    for cmd_suffix, clause in [
        ("select", "USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)"),
        ("insert", "WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)"),
        ("update",
         "USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid) "
         "WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)"),
        ("delete", "USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)"),
    ]:
        op.execute(
            f'CREATE POLICY org_isolation_{cmd_suffix} ON "dunning_notice" '
            f'FOR {cmd_suffix.upper()} {clause}'
        )

    # -----------------------------------------------------------------------
    # 3. Immutability trigger (ADR-0003 pattern, freeze at status='issued')
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION trg_dunning_notice_immutability()
    RETURNS TRIGGER AS $$
    -- GoBD-style immutability for dunning_notice (ADR-0003 pattern).
    --
    -- FREEZE CONDITION: OLD.status = 'issued'
    --
    -- Once a dunning notice is issued, its content-bearing columns are
    -- frozen. Correction = new dunning notice (higher level or re-issue).
    -- An issued notice is NEVER edited or deleted.
    --
    -- Content-bearing columns (frozen once issued):
    --   organization_id, tenant_profile_id, owner_profile_id, level,
    --   issue_date, due_date, principal_amount, fee_amount, total_amount,
    --   included_items, status (cannot revert from issued), issued_at,
    --   issued_by, note
    --
    -- Allowed changes on an issued notice:
    --   updated_at (technical timestamp only)
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.status = 'issued' THEN
                RAISE EXCEPTION 'Immutability: DELETE of issued dunning_notice is forbidden (id=%)', OLD.id;
            END IF;
            RETURN OLD;
        END IF;

        -- UPDATE path
        IF OLD.status = 'issued' THEN
            IF NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
                RAISE EXCEPTION 'Immutability: Cannot change organization_id on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.tenant_profile_id IS DISTINCT FROM OLD.tenant_profile_id THEN
                RAISE EXCEPTION 'Immutability: Cannot change tenant_profile_id on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.owner_profile_id IS DISTINCT FROM OLD.owner_profile_id THEN
                RAISE EXCEPTION 'Immutability: Cannot change owner_profile_id on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.level IS DISTINCT FROM OLD.level THEN
                RAISE EXCEPTION 'Immutability: Cannot change level on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.issue_date IS DISTINCT FROM OLD.issue_date THEN
                RAISE EXCEPTION 'Immutability: Cannot change issue_date on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.due_date IS DISTINCT FROM OLD.due_date THEN
                RAISE EXCEPTION 'Immutability: Cannot change due_date on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.principal_amount IS DISTINCT FROM OLD.principal_amount THEN
                RAISE EXCEPTION 'Immutability: Cannot change principal_amount on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.fee_amount IS DISTINCT FROM OLD.fee_amount THEN
                RAISE EXCEPTION 'Immutability: Cannot change fee_amount on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.total_amount IS DISTINCT FROM OLD.total_amount THEN
                RAISE EXCEPTION 'Immutability: Cannot change total_amount on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.included_items IS DISTINCT FROM OLD.included_items THEN
                RAISE EXCEPTION 'Immutability: Cannot change included_items on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'Immutability: Cannot change status on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.issued_at IS DISTINCT FROM OLD.issued_at THEN
                RAISE EXCEPTION 'Immutability: Cannot change issued_at on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.issued_by IS DISTINCT FROM OLD.issued_by THEN
                RAISE EXCEPTION 'Immutability: Cannot change issued_by on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.note IS DISTINCT FROM OLD.note THEN
                RAISE EXCEPTION 'Immutability: Cannot change note on issued dunning_notice (id=%)', OLD.id;
            END IF;
            -- updated_at: technical timestamp, always allowed
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER trg_dunning_notice_immutability
        BEFORE UPDATE OR DELETE ON dunning_notice
        FOR EACH ROW
        EXECUTE FUNCTION trg_dunning_notice_immutability();
    """)

    # -----------------------------------------------------------------------
    # 4. Grants for app_rw (full CRUD -- trigger is the status barrier)
    # -----------------------------------------------------------------------
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "dunning_notice" TO app_rw')


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_dunning_notice_immutability ON dunning_notice")
    op.execute("DROP FUNCTION IF EXISTS trg_dunning_notice_immutability()")
    for suffix in ("select", "insert", "update", "delete"):
        op.execute(f'DROP POLICY IF EXISTS org_isolation_{suffix} ON "dunning_notice"')
    op.execute('ALTER TABLE "dunning_notice" DISABLE ROW LEVEL SECURITY')
    op.drop_table("dunning_notice")
