"""M8: owners_meeting + agenda_item + resolution + resolution_register_entry
tables, RLS policies, immutability trigger for resolution_register_entry.

Creates four tables for the Eigentümerversammlung & Beschluss-Sammlung module.
Includes:
  - Tables with all columns, CHECKs, UNIQUE constraints, indices.
  - RLS: ENABLE + FORCE + four policies per table (fail-closed, same pattern
    as 0002/0004/0007/0010/0014/0017/0019).
  - Immutability trigger on resolution_register_entry (ADR-0011):
    * ALL columns frozen from INSERT (no status transition needed).
    * The ONLY allowed mutation is challenge_annotation (Anfechtungsvermerk)
      and updated_at.
    * DELETE is ALWAYS forbidden.
  - Grants for app_rw.

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables created in this migration (for RLS/grant helpers below).
_TABLES = ["owners_meeting", "agenda_item", "resolution", "resolution_register_entry"]


def _add_rls(table_name: str) -> None:
    """Enable RLS, FORCE it, and create four standard org-isolation policies."""
    op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')

    for cmd_suffix, clause in [
        ("select",
         "USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)"),
        ("insert",
         "WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)"),
        ("update",
         "USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid) "
         "WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)"),
        ("delete",
         "USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)"),
    ]:
        op.execute(
            f'CREATE POLICY org_isolation_{cmd_suffix} ON "{table_name}" '
            f'FOR {cmd_suffix.upper()} {clause}'
        )


def _grant_crud(table_name: str) -> None:
    """Grant full CRUD to app_rw. Immutability is enforced by triggers, not grants."""
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table_name}" TO app_rw')


def _grant_no_delete(table_name: str) -> None:
    """Grant SELECT, INSERT, UPDATE (no DELETE) to app_rw -- trigger is extra guard."""
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON "{table_name}" TO app_rw')


def upgrade() -> None:
    # ===================================================================
    # 1. owners_meeting
    # ===================================================================
    op.create_table(
        "owners_meeting",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("property_id", UUID(as_uuid=True),
                  sa.ForeignKey("property.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("meeting_date", sa.Date, nullable=False),
        sa.Column("location", sa.String(500), nullable=False, server_default=""),
        sa.Column("meeting_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="PLANNED"),
        sa.Column("invitation_text", sa.Text, nullable=True),
        sa.Column("protocol_text", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # CHECKs
        sa.CheckConstraint(
            "meeting_type IN ('ORDENTLICH', 'AUSSERORDENTLICH')",
            name="ck_owners_meeting_type",
        ),
        sa.CheckConstraint(
            "status IN ('PLANNED', 'HELD')",
            name="ck_owners_meeting_status",
        ),
    )
    _add_rls("owners_meeting")
    _grant_crud("owners_meeting")

    # ===================================================================
    # 2. agenda_item
    # ===================================================================
    op.create_table(
        "agenda_item",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("meeting_id", UUID(as_uuid=True),
                  sa.ForeignKey("owners_meeting.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("item_number", sa.SmallInteger, nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # UNIQUE
        sa.UniqueConstraint("meeting_id", "item_number", name="uq_agenda_item_meeting_number"),
    )
    _add_rls("agenda_item")
    _grant_crud("agenda_item")

    # ===================================================================
    # 3. resolution
    # ===================================================================
    op.create_table(
        "resolution",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("meeting_id", UUID(as_uuid=True),
                  sa.ForeignKey("owners_meeting.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("agenda_item_id", UUID(as_uuid=True),
                  sa.ForeignKey("agenda_item.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("wording", sa.Text, nullable=False),
        sa.Column("votes_yes", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("votes_no", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("votes_abstain", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("votes_yes_mea", sa.Numeric(12, 6), nullable=True),
        sa.Column("votes_no_mea", sa.Numeric(12, 6), nullable=True),
        sa.Column("votes_abstain_mea", sa.Numeric(12, 6), nullable=True),
        sa.Column("result", sa.String(12), nullable=False),
        sa.Column("majority_type", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # CHECKs
        sa.CheckConstraint("result IN ('ANGENOMMEN', 'ABGELEHNT')", name="ck_resolution_result"),
        sa.CheckConstraint("votes_yes >= 0", name="ck_resolution_votes_yes"),
        sa.CheckConstraint("votes_no >= 0", name="ck_resolution_votes_no"),
        sa.CheckConstraint("votes_abstain >= 0", name="ck_resolution_votes_abstain"),
    )
    _add_rls("resolution")
    _grant_crud("resolution")

    # ===================================================================
    # 4. resolution_register_entry (Beschluss-Sammlung section 24 VII)
    # ===================================================================
    op.create_table(
        "resolution_register_entry",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("property_id", UUID(as_uuid=True),
                  sa.ForeignKey("property.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("sequence_number", sa.Integer, nullable=False),
        sa.Column("entry_date", sa.Date, nullable=False),
        sa.Column("location", sa.String(500), nullable=False, server_default=""),
        sa.Column("wording", sa.Text, nullable=False),
        sa.Column("resolution_id", UUID(as_uuid=True),
                  sa.ForeignKey("resolution.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("challenge_annotation", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # UNIQUE: lückenlose Nummerierung je (org, property)
        sa.UniqueConstraint(
            "organization_id", "property_id", "sequence_number",
            name="uq_register_entry_org_prop_seq",
        ),
    )
    _add_rls("resolution_register_entry")
    # No DELETE grant -- trigger also blocks, but grant is backstop (ADR-0003 defense-in-depth)
    _grant_no_delete("resolution_register_entry")

    # ===================================================================
    # 5. Immutability trigger for resolution_register_entry (ADR-0011)
    # ===================================================================
    op.execute("""
    CREATE OR REPLACE FUNCTION trg_register_entry_immutability()
    RETURNS TRIGGER AS $$
    -- Beschluss-Sammlung immutability (ADR-0011, WEG section 24 Abs. 7).
    --
    -- FREEZE CONDITION: always (immutable from INSERT).
    --
    -- Once a resolution register entry exists, it is legally immutable.
    -- The ONLY allowed mutation is the challenge_annotation
    -- (Anfechtungs-/Aufhebungsvermerk) and updated_at.
    --
    -- Content-bearing columns (frozen from INSERT):
    --   organization_id, property_id, sequence_number, entry_date,
    --   location, wording, resolution_id
    --
    -- Allowed changes:
    --   challenge_annotation (may be set/updated/cleared)
    --   updated_at (technical timestamp)
    --
    -- DELETE is ALWAYS forbidden.
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION
                'Immutability: DELETE of resolution_register_entry is forbidden (id=%, seq=%)',
                OLD.id, OLD.sequence_number;
        END IF;

        -- UPDATE path: block any change to frozen columns
        IF NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
            RAISE EXCEPTION
                'Immutability: Cannot change organization_id on register entry (id=%)', OLD.id;
        END IF;
        IF NEW.property_id IS DISTINCT FROM OLD.property_id THEN
            RAISE EXCEPTION
                'Immutability: Cannot change property_id on register entry (id=%)', OLD.id;
        END IF;
        IF NEW.sequence_number IS DISTINCT FROM OLD.sequence_number THEN
            RAISE EXCEPTION
                'Immutability: Cannot change sequence_number on register entry (id=%)', OLD.id;
        END IF;
        IF NEW.entry_date IS DISTINCT FROM OLD.entry_date THEN
            RAISE EXCEPTION
                'Immutability: Cannot change entry_date on register entry (id=%)', OLD.id;
        END IF;
        IF NEW.location IS DISTINCT FROM OLD.location THEN
            RAISE EXCEPTION
                'Immutability: Cannot change location on register entry (id=%)', OLD.id;
        END IF;
        IF NEW.wording IS DISTINCT FROM OLD.wording THEN
            RAISE EXCEPTION
                'Immutability: Cannot change wording on register entry (id=%)', OLD.id;
        END IF;
        IF NEW.resolution_id IS DISTINCT FROM OLD.resolution_id THEN
            RAISE EXCEPTION
                'Immutability: Cannot change resolution_id on register entry (id=%)', OLD.id;
        END IF;
        IF NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION
                'Immutability: Cannot change created_at on register entry (id=%)', OLD.id;
        END IF;

        -- challenge_annotation + updated_at: ALLOWED (the only legal mutation).
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER trg_register_entry_immutability
        BEFORE UPDATE OR DELETE ON resolution_register_entry
        FOR EACH ROW
        EXECUTE FUNCTION trg_register_entry_immutability();
    """)


def downgrade() -> None:
    # Trigger
    op.execute("DROP TRIGGER IF EXISTS trg_register_entry_immutability ON resolution_register_entry")
    op.execute("DROP FUNCTION IF EXISTS trg_register_entry_immutability()")

    # Drop tables in reverse FK order
    for table in reversed(_TABLES):
        # Policies
        for suffix in ("select", "insert", "update", "delete"):
            op.execute(f'DROP POLICY IF EXISTS org_isolation_{suffix} ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
        op.drop_table(table)
