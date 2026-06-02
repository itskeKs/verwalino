"""M6 RLS policies for payment.

Same pattern as 0002/0004/0007/0010/0014: ENABLE + FORCE + four policies
(SELECT/INSERT/UPDATE/DELETE), fail-closed via
NULLIF(current_setting('app.current_org_id', true), '')::uuid.

payment gets the DELETE policy too (it has a full CRUD grant -- editable
`draft` state, section 1.6 / decision E3-5). The Vermoegens-Kohaerenz
(Composite-FK, 0016) is a SEPARATE, additional coherence layer INSIDE the org;
RLS stays the primary org-wide isolation axis (ADR-0010: no second RLS axis).

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-02
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FULL_RLS_TABLES = ["payment"]


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
