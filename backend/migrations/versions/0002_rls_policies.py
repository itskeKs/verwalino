"""RLS policies: enable + force + USING/WITH CHECK on all tenant tables.

This is a separate, explicit migration step so RLS is reviewable
independently from schema DDL (architecture.md section 7.2 step 3).

Tenant tables: user, membership, management_mandate, audit_event.
Global tables (no RLS): organization.

Policy: organization_id must match the session GUC `app.current_org_id`.
If the GUC is not set, current_setting returns '' which cannot cast to UUID,
so the comparison fails and zero rows are returned (fail-closed).

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# All tenant tables that need RLS
TENANT_TABLES = ["user", "membership", "management_mandate", "audit_event"]


def upgrade() -> None:
    # Set a default value for the GUC so that unset = impossible UUID = fail-closed.
    # This custom GUC is set per-transaction via SET LOCAL in the app.
    # PostgreSQL allows custom GUCs; if never set, current_setting returns the
    # default we define here (empty string, which fails the UUID cast).
    #
    # We use a COALESCE + NULLIF pattern in the policy so that an empty/unset
    # GUC results in NULL, which never equals any organization_id (fail-closed).

    for table in TENANT_TABLES:
        # Enable RLS
        op.execute(f"ALTER TABLE \"{table}\" ENABLE ROW LEVEL SECURITY")
        # Force RLS even for table owner (belt-and-suspenders)
        op.execute(f"ALTER TABLE \"{table}\" FORCE ROW LEVEL SECURITY")

        # Create the isolation policy.
        # NULLIF handles the case where current_setting returns '' (unset GUC):
        #   NULLIF(current_setting('app.current_org_id', true), '') returns NULL
        #   NULL::uuid never equals any organization_id => zero rows (fail-closed).
        #
        # current_setting(name, missing_ok) with missing_ok=true returns ''
        # instead of raising an error when the GUC is not set.
        op.execute(f"""
            CREATE POLICY org_isolation_select ON "{table}"
                FOR SELECT
                USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        """)

        op.execute(f"""
            CREATE POLICY org_isolation_insert ON "{table}"
                FOR INSERT
                WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        """)

        op.execute(f"""
            CREATE POLICY org_isolation_update ON "{table}"
                FOR UPDATE
                USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
                WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        """)

        # DELETE policy: only on tables where app_rw has DELETE grant
        if table != "audit_event":
            op.execute(f"""
                CREATE POLICY org_isolation_delete ON "{table}"
                    FOR DELETE
                    USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
            """)


def downgrade() -> None:
    for table in TENANT_TABLES:
        # Drop all policies
        for suffix in ("select", "insert", "update", "delete"):
            op.execute(f'DROP POLICY IF EXISTS org_isolation_{suffix} ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
