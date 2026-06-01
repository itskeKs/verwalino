"""M2 RLS policies for cost_entry and invoice_document.

Same pattern as 0002/0004: ENABLE + FORCE + four policies per tenant table.
invoice_document: DELETE policy OMITTED (no DELETE grant -- ADR-0003 backstop).

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables with full CRUD RLS (4 policies)
FULL_RLS_TABLES = ["cost_entry"]

# Tables with SELECT/INSERT/UPDATE RLS only (no DELETE grant, no DELETE policy)
NO_DELETE_TABLES = ["invoice_document"]

ALL_TABLES = FULL_RLS_TABLES + NO_DELETE_TABLES


def upgrade() -> None:
    for table in ALL_TABLES:
        # Enable RLS
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        # Force RLS even for table owner (belt-and-suspenders)
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')

        # SELECT policy
        op.execute(f"""
            CREATE POLICY org_isolation_select ON "{table}"
                FOR SELECT
                USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        """)

        # INSERT policy
        op.execute(f"""
            CREATE POLICY org_isolation_insert ON "{table}"
                FOR INSERT
                WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        """)

        # UPDATE policy
        op.execute(f"""
            CREATE POLICY org_isolation_update ON "{table}"
                FOR UPDATE
                USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
                WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        """)

    # DELETE policy only for tables where app_rw has DELETE grant
    for table in FULL_RLS_TABLES:
        op.execute(f"""
            CREATE POLICY org_isolation_delete ON "{table}"
                FOR DELETE
                USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        """)


def downgrade() -> None:
    for table in ALL_TABLES:
        for suffix in ("select", "insert", "update", "delete"):
            op.execute(f'DROP POLICY IF EXISTS org_isolation_{suffix} ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
