"""Persist the meaning of a lease's original and effective-dated advances."""

import sqlalchemy as sa
from alembic import op

revision = "0175"
down_revision = "0174"
branch_labels = None
depends_on = None

_TABLES = ("lease_agreement", "lease_rent_change")


def upgrade():
    op.execute("LOCK TABLE public.lease_agreement, public.lease_rent_change IN ACCESS EXCLUSIVE MODE NOWAIT")
    for table in _TABLES:
        op.add_column(table, sa.Column("advance_mode", sa.String(8)))
        op.create_check_constraint("advance_mode_matches_amounts", table,
            "advance_mode IN ('COMBINED', 'SEPARATE') AND (advance_mode <> 'COMBINED' OR heating_advance_payment = 0)")


def downgrade():
    op.execute("LOCK TABLE public.lease_agreement, public.lease_rent_change IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.lease_agreement WHERE advance_mode IS NOT NULL)
                OR EXISTS (SELECT 1 FROM public.lease_rent_change WHERE advance_mode IS NOT NULL) THEN
                RAISE EXCEPTION 'Retain explicit tenant advance modes. Restore a pre-upgrade backup for the previous release.';
            END IF;
        END $$;
    """)
    for table in reversed(_TABLES):
        op.drop_constraint("advance_mode_matches_amounts", table, type_="check")
        op.drop_column(table, "advance_mode")
