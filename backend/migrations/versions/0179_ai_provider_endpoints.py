"""Support tenant-selected compatible and Anthropic provider destinations."""
from alembic import op
import sqlalchemy as sa

revision = '0179'
down_revision = '0178'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organization_ai_settings', sa.Column('base_url', sa.String(2048)))
    op.execute("ALTER TABLE organization_ai_settings DROP CONSTRAINT organization_ai_settings_provider_check")
    op.execute("ALTER TABLE organization_ai_settings ADD CONSTRAINT organization_ai_settings_provider_check CHECK (provider IN ('openai','openai_compatible','anthropic'))")
    op.execute("ALTER TABLE organization_ai_settings ADD CONSTRAINT organization_ai_settings_destination_check CHECK ((provider='openai_compatible' AND base_url IS NOT NULL) OR (provider<>'openai_compatible' AND base_url IS NULL))")
    # Preserve accounting of known-price active reservations without pricing an
    # arbitrary compatible origin merely because it reuses a known model name.
    definition = op.get_bind().scalar(sa.text("SELECT pg_get_functiondef('public.organization_ai_monthly_usage(uuid,text[])'::regprocedure)"))
    old = "r.status='RUNNING' AND r.model_name=ANY(priced_models)"
    new = "r.status='RUNNING' AND ((r.provider='OpenAiDeclarationProvider' AND r.model_name=ANY(priced_models)) OR (r.provider='AnthropicDeclarationProvider' AND r.model_name IN ('claude-haiku-4-5','claude-haiku-4-5-20251001')))"
    if definition.count(old) != 1:
        raise RuntimeError('Unexpected AI monthly usage function')
    op.execute(definition.replace(old, new))


def downgrade():
    op.execute('LOCK TABLE organization_ai_settings IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM organization_ai_settings WHERE provider<>'openai' OR base_url IS NOT NULL)
      THEN RAISE EXCEPTION 'Configured AI providers require this release'; END IF; END $$""")
    definition = op.get_bind().scalar(sa.text("SELECT pg_get_functiondef('public.organization_ai_monthly_usage(uuid,text[])'::regprocedure)"))
    new = "r.status='RUNNING' AND ((r.provider='OpenAiDeclarationProvider' AND r.model_name=ANY(priced_models)) OR (r.provider='AnthropicDeclarationProvider' AND r.model_name IN ('claude-haiku-4-5','claude-haiku-4-5-20251001')))"
    old = "r.status='RUNNING' AND r.model_name=ANY(priced_models)"
    if definition.count(new) != 1:
        raise RuntimeError('Unexpected AI monthly usage function')
    op.execute(definition.replace(new, old))
    op.drop_constraint('organization_ai_settings_destination_check', 'organization_ai_settings')
    op.drop_constraint('organization_ai_settings_provider_check', 'organization_ai_settings')
    op.execute("ALTER TABLE organization_ai_settings ADD CONSTRAINT organization_ai_settings_provider_check CHECK (provider='openai')")
    op.drop_column('organization_ai_settings', 'base_url')
