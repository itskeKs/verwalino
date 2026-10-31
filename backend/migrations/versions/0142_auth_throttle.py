"""Persistent global authentication throttle with hashed scoped keys."""
from alembic import op

revision = "0142"
down_revision = "0141"
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    CREATE TABLE public.auth_throttle (
      key_kind text NOT NULL CHECK (key_kind IN ('ip','email')),
      key_hash bytea NOT NULL CHECK (octet_length(key_hash)=32),
      window_start timestamptz NOT NULL,
      failures integer NOT NULL DEFAULT 0 CHECK (failures>=0),
      locked_until timestamptz,
      lockouts integer NOT NULL DEFAULT 0 CHECK (lockouts>=0),
      last_lockout_at timestamptz,
      updated_at timestamptz NOT NULL,
      PRIMARY KEY (key_kind,key_hash)
    );
    CREATE INDEX ix_auth_throttle_updated_at ON public.auth_throttle(updated_at);
    REVOKE ALL ON public.auth_throttle FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT SELECT,INSERT,UPDATE,DELETE ON public.auth_throttle TO app_rw;
    ''')


def downgrade():
    op.execute('''
    LOCK TABLE public.auth_throttle IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.auth_throttle) THEN
        RAISE EXCEPTION 'Cannot discard retained auth throttle state';
      END IF;
    END $$;
    DROP TABLE public.auth_throttle;
    ''')
