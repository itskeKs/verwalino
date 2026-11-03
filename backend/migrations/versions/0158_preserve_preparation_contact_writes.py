"""Preserve actorless preparation contact writes while retaining name locks."""
from alembic import op
from sqlalchemy import text

revision = '0158'
down_revision = '0157'
branch_labels = None
depends_on = None


BEFORE = "IF current_user<>'app_migrator' AND public.scope_actor_role(org) IS NULL THEN"
AFTER = """IF current_user<>'app_migrator'
            AND public.property_source_security_mode(org) IS DISTINCT FROM 'PREPARATION'
            AND public.scope_actor_role(org) IS NULL THEN"""


def replace_principal_check(*, reverse=False):
    definition = op.get_bind().scalar(text(
        "SELECT pg_get_functiondef('public.lock_strict_property_source_binding()'::regprocedure)"))
    before, after = (AFTER, BEFORE) if reverse else (BEFORE, AFTER)
    assert definition.count(before) == 1
    op.execute(definition.replace(before, after))


def upgrade():
    replace_principal_check()


def downgrade():
    replace_principal_check(reverse=True)
