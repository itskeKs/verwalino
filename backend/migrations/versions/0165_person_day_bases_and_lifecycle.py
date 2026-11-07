"""Retain raw person-day bases and protect household writes during erasure."""
from alembic import op
import sqlalchemy as sa

revision = '0165'
down_revision = '0164'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''ALTER TABLE settlement_line
        ALTER COLUMN distribution_base_total TYPE numeric(28,6),
        ALTER COLUMN distribution_base_share TYPE numeric(28,6)''')
    op.execute('''CREATE TRIGGER a_organization_lifecycle
        BEFORE INSERT OR UPDATE OR DELETE ON public.unit_household
        FOR EACH ROW EXECUTE FUNCTION public.organization_lifecycle_guard()''')


def downgrade():
    # Hold the same table lock as the type change so no writer can insert a
    # larger base between the preservation check and narrowing the columns.
    op.execute('LOCK TABLE settlement_line IN ACCESS EXCLUSIVE MODE')
    if op.get_bind().scalar(sa.text('''SELECT EXISTS(SELECT 1 FROM settlement_line
        WHERE abs(distribution_base_total)>=1000000 OR abs(distribution_base_share)>=1000000)''')):
        raise RuntimeError('Cannot narrow stored settlement bases; repair forward')
    op.execute('''ALTER TABLE settlement_line
        ALTER COLUMN distribution_base_total TYPE numeric(12,6),
        ALTER COLUMN distribution_base_share TYPE numeric(12,6)''')
    op.execute('DROP TRIGGER a_organization_lifecycle ON public.unit_household')
