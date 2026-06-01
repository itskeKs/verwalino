"""M3 RLS policies for settlement_run and settlement_line.

Same pattern as 0002/0004/0007: ENABLE + FORCE + four policies per tenant table
(SELECT/INSERT/UPDATE/DELETE), fail-closed via
NULLIF(current_setting('app.current_org_id', true), '')::uuid.

Both tables get the DELETE policy (both have a full CRUD grant -- editable
`draft` state, section 1.6 / decision E3-5).

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Both tables have full CRUD RLS (4 policies each).
FULL_RLS_TABLES = ["settlement_run", "settlement_line"]


def upgrade() -> None:
    for table in FULL_RLS_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')

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
        op.execute(f"""
            CREATE POLICY org_isolation_delete ON "{table}"
                FOR DELETE
                USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        """)


def downgrade() -> None:
    for table in FULL_RLS_TABLES:
        for suffix in ("select", "insert", "update", "delete"):
            op.execute(f'DROP POLICY IF EXISTS org_isolation_{suffix} ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
