"""RLS policies for M1 Stammdaten tables.

Same pattern as 0002: ENABLE + FORCE + USING/WITH CHECK on all new tenant tables.
Applies to: property, unit, person, owner_profile, tenant_profile, vendor,
            ownership_history, lease_agreement, bank_account, meter, meter_reading.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# All M1 tenant tables that need RLS
M1_TENANT_TABLES = [
    "property",
    "unit",
    "person",
    "owner_profile",
    "tenant_profile",
    "vendor",
    "ownership_history",
    "lease_agreement",
    "bank_account",
    "meter",
    "meter_reading",
]


def upgrade() -> None:
    for table in M1_TENANT_TABLES:
        # Enable RLS
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        # Force RLS even for table owner
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

        # DELETE policy
        op.execute(f"""
            CREATE POLICY org_isolation_delete ON "{table}"
                FOR DELETE
                USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        """)


def downgrade() -> None:
    for table in M1_TENANT_TABLES:
        for suffix in ("select", "insert", "update", "delete"):
            op.execute(f'DROP POLICY IF EXISTS org_isolation_{suffix} ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
