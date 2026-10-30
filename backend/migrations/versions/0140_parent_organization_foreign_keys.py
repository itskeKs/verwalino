"""Bind client-selected parents to the child's organization."""
from alembic import op

revision = "0140"
down_revision = "0139"
branch_labels = None
depends_on = None

# All replaced single-column foreign keys use ON DELETE RESTRICT.
RELATIONS = (
    ("lease_agreement", "unit_id", "unit"),
    ("lease_agreement", "tenant_profile_id", "tenant_profile"),
    ("ownership_history", "unit_id", "unit"),
    ("ownership_history", "owner_profile_id", "owner_profile"),
    ("bank_account", "property_id", "property"),
    ("economic_plan", "property_id", "property"),
    ("economic_plan", "management_mandate_id", "management_mandate"),
    ("settlement_run", "property_id", "property"),
    ("settlement_run", "management_mandate_id", "management_mandate"),
    ("payment", "tenant_profile_id", "tenant_profile"),
    ("payment", "owner_profile_id", "owner_profile"),
    ("payment", "lease_agreement_id", "lease_agreement"),
    ("payment", "settlement_run_id", "settlement_run"),
    ("cost_entry", "vendor_id", "vendor"),
)
NEW_UNIQUE_PARENTS = ("unit", "tenant_profile", "vendor")


def upgrade():
    # Fail rather than inspect an RLS-filtered subset of existing rows. Hold
    # writers until both the preflight and constraint replacement have finished.
    op.execute("SET LOCAL row_security = off")
    # Earlier revisions can queue deferred mandate checks in the same upgrade
    # transaction. Validate them before replacing constraints on their tables.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    tables = sorted({table for table, _, _ in RELATIONS} | {parent for _, _, parent in RELATIONS})
    op.execute(f"LOCK TABLE {', '.join(tables)} IN SHARE ROW EXCLUSIVE MODE")
    for table, column, parent in RELATIONS:
        op.execute(f"""DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM {table} child LEFT JOIN {parent} parent
              ON parent.id=child.{column} AND parent.organization_id=child.organization_id
            WHERE child.{column} IS NOT NULL AND parent.id IS NULL
          ) THEN
            RAISE EXCEPTION 'Cannot upgrade 0140: invalid organization parent in {table}.{column}; repair existing data before retrying';
          END IF;
        END $$""")
    for parent in NEW_UNIQUE_PARENTS:
        op.create_unique_constraint(f"uq_{parent}_org_id", parent, ["organization_id", "id"])
    for table, column, parent in RELATIONS:
        op.drop_constraint(op.f(f"fk_{table}_{column}_{parent}"), table, type_="foreignkey")
        op.create_foreign_key(
            f"fk_{table}_{column}_tenant", table, parent,
            ["organization_id", column], ["organization_id", "id"], ondelete="RESTRICT",
        )
    op.execute("""
    CREATE FUNCTION financial_property_exists(org uuid,property uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR public.scope_actor_role(org) IS NULL THEN RETURN false; END IF;
      RETURN EXISTS(SELECT 1 FROM public.property p WHERE p.organization_id=org AND p.id=property);
    END $$;
    REVOKE ALL ON FUNCTION financial_property_exists(uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION financial_property_exists(uuid,uuid) TO app_rw;
    """)


def downgrade():
    op.execute("DROP FUNCTION IF EXISTS financial_property_exists(uuid,uuid)")
    for table, column, parent in reversed(RELATIONS):
        op.drop_constraint(f"fk_{table}_{column}_tenant", table, type_="foreignkey")
        op.create_foreign_key(
            op.f(f"fk_{table}_{column}_{parent}"), table, parent,
            [column], ["id"], ondelete="RESTRICT",
        )
    for parent in reversed(NEW_UNIQUE_PARENTS):
        op.drop_constraint(f"uq_{parent}_org_id", parent, type_="unique")
