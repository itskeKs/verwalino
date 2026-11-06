"""Preserve complete accepted explanations in persisted settlement lines."""
from alembic import op
from sqlalchemy import text

revision = '0170'
down_revision = '0169'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('LOCK TABLE settlement_line IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute('ALTER TABLE settlement_line ALTER COLUMN note TYPE text')


def downgrade():
    # Serialize the preservation check with the narrowing ALTER. Existing
    # explanations must never be truncated to fit the previous schema.
    op.execute('LOCK TABLE settlement_line IN ACCESS EXCLUSIVE MODE NOWAIT')
    if op.get_bind().scalar(text('SELECT EXISTS(SELECT 1 FROM settlement_line WHERE char_length(note)>500)')):
        raise RuntimeError('Cannot narrow stored settlement notes; repair forward')
    op.execute('ALTER TABLE settlement_line ALTER COLUMN note TYPE varchar(500)')
