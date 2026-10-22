"""Allow the isolated review runtime to validate, never look up, sessions."""
from alembic import op

revision = '0139'
down_revision = '0138'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('GRANT EXECUTE ON FUNCTION public.app_validate_session_versioned(uuid,uuid,integer) TO app_review')


def downgrade():
    op.execute('REVOKE EXECUTE ON FUNCTION public.app_validate_session_versioned(uuid,uuid,integer) FROM app_review')
