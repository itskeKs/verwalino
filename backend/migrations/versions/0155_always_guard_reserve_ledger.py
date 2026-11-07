"""Keep reserve history append-only in replica sessions as well."""
from alembic import op

revision = '0155'
down_revision = '0154'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE public.reserve_ledger_entry ENABLE ALWAYS TRIGGER guard_reserve_ledger')


def downgrade():
    op.execute('ALTER TABLE public.reserve_ledger_entry ENABLE TRIGGER guard_reserve_ledger')
