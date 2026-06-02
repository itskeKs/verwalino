"""M4 RLS policies for economic_plan and economic_plan_line.

Same pattern as 0002/0004/0007/0010: ENABLE + FORCE + four policies per tenant
table (SELECT/INSERT/UPDATE/DELETE), fail-closed via
NULLIF(current_setting('app.current_org_id', true), '')::uuid.

Both tables get the DELETE policy (both have a full CRUD grant -- editable
`draft` state, m4 section 1.3/1.4, E3-5 line).

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-02
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FULL_RLS_TABLES = ["economic_plan", "economic_plan_line"]


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
