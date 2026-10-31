"""Allow retained built-in lease documents without an editable template row."""
from alembic import op

revision = '0142'
down_revision = '0141'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('letter_document', 'template_id', nullable=True)


def downgrade():
    # PostgreSQL refuses this change while a retained original has no template.
    op.alter_column('letter_document', 'template_id', nullable=False)
