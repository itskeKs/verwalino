"""Store the personal light design without changing authentication contracts."""
from alembic import op

revision = '0175'
down_revision = '0174'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    ALTER TABLE public."user" ADD COLUMN design_theme varchar(8) NOT NULL DEFAULT 'klar'
      CONSTRAINT ck_user_design_theme CHECK (design_theme IN ('klar','warm'));
    ''')


def downgrade():
    op.execute('''
    LOCK TABLE public."user" IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public."user" WHERE design_theme <> 'klar') THEN
        RAISE EXCEPTION '0175 downgrade would discard personal design preferences';
      END IF;
    END $$;
    ALTER TABLE public."user" DROP COLUMN design_theme;
    ''')
