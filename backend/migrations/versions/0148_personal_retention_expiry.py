"""Count personal fields eligible for erasure after every binding expires."""
from alembic import op
from sqlalchemy import text

revision = '0148'
down_revision = '0147'
branch_labels = None
depends_on = None


def _preview(install):
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('public.organization_deletion_preview(uuid)'::regprocedure)"))
    changes = [
        ("WHERE entity='person';", "WHERE entity='person' AND retention_until>=CURRENT_DATE;"),
        ('Ungebundene Namen, Anschriften, E-Mail, Telefon und Bankdaten',
         'Namen, Anschriften, E-Mail, Telefon und Bankdaten ohne laufende Aufbewahrungspflicht'),
    ]
    for before, after in changes:
        source, target = (before, after) if install else (after, before)
        assert definition.count(source) == 1
        definition = definition.replace(source, target)
    op.execute(definition)


def upgrade():
    _preview(True)


def downgrade():
    op.execute('''LOCK TABLE public.organization_deletion IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.organization_deletion) THEN
        RAISE EXCEPTION 'Retain reconciled organization erasure decisions'; END IF; END $$;''')
    _preview(False)
