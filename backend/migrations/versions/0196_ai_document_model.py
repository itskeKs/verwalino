"""Add the nullable document reading model of the organization AI settings (issue #499, ADR 0017).

The organization has two model roles with one provider, endpoint, timeout and credential: the existing ``model``
(extraction, questions) and the new ``document_model`` (reading documents). The column is nullable and the
migration writes no row: ``model`` and every other column stay as they are, and no organization gets a reading
model until an administrator chooses one. A paid model is never selected on the organization's behalf.

``downgrade()`` refuses while a reading model is stored, so that the choice is not discarded silently; the older
runtime has no reading role and would run the import without it.

Revision ID: 0196
Revises: 0195
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa

revision = '0196'
down_revision = '0195'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organization_ai_settings', sa.Column('document_model', sa.String(255)))


def downgrade():
    op.execute('LOCK TABLE organization_ai_settings IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM organization_ai_settings WHERE document_model IS NOT NULL)
      THEN RAISE EXCEPTION 'Configured document reading models require this release'; END IF; END $$""")
    op.drop_column('organization_ai_settings', 'document_model')
