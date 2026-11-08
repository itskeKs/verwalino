"""Persist endpoint-specific administrator request timeouts."""
from alembic import op
import sqlalchemy as sa

revision = '0180'
down_revision = '0179'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organization_ai_settings', sa.Column('request_timeout_seconds', sa.Integer()))
    op.create_check_constraint(op.f('organization_ai_settings_timeout_check'), 'organization_ai_settings',
        'request_timeout_seconds BETWEEN 1 AND 3600')


def downgrade():
    op.execute('LOCK TABLE organization_ai_settings IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM organization_ai_settings WHERE request_timeout_seconds IS NOT NULL)
      THEN RAISE EXCEPTION 'Configured AI timeouts require this release'; END IF; END $$""")
    op.drop_constraint(op.f('organization_ai_settings_timeout_check'), 'organization_ai_settings')
    op.drop_column('organization_ai_settings', 'request_timeout_seconds')
