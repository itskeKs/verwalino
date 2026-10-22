"""Immutable actor-scoped tenant dunning batches (T53)."""
from alembic import op

revision = '0135'
down_revision = '0134'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE dunning_batch (
      organization_id uuid NOT NULL,
      actor_user_id uuid NOT NULL,
      request_id uuid NOT NULL,
      request_digest varchar(64) NOT NULL CHECK(request_digest ~ '^[0-9a-f]{64}$'),
      result jsonb NOT NULL CHECK(jsonb_typeof(result)='array'),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(organization_id,actor_user_id,request_id),
      FOREIGN KEY(organization_id,actor_user_id) REFERENCES "user"(organization_id,id)
    );
    ALTER TABLE dunning_batch ENABLE ROW LEVEL SECURITY;
    ALTER TABLE dunning_batch FORCE ROW LEVEL SECURITY;
    CREATE POLICY dunning_batch_read ON dunning_batch FOR SELECT USING (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND actor_user_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid
      AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK'));
    CREATE POLICY dunning_batch_insert ON dunning_batch FOR INSERT WITH CHECK (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND actor_user_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid
      AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK'));
    REVOKE ALL ON dunning_batch FROM PUBLIC,app_rw;
    GRANT SELECT,INSERT ON dunning_batch TO app_rw;
    CREATE FUNCTION protect_dunning_batch() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      RAISE EXCEPTION 'Dunning batch is immutable' USING ERRCODE='23514';
    END $$;
    REVOKE ALL ON FUNCTION protect_dunning_batch() FROM PUBLIC,app_rw;
    CREATE TRIGGER protect_dunning_batch BEFORE UPDATE OR DELETE ON dunning_batch
      FOR EACH ROW EXECUTE FUNCTION protect_dunning_batch();
    """)


def downgrade():
    op.execute("""
    LOCK TABLE dunning_batch IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM dunning_batch) THEN
        RAISE EXCEPTION 'Cannot remove populated dunning batches';
      END IF;
    END $$;
    DROP TABLE dunning_batch;
    DROP FUNCTION protect_dunning_batch();
    """)
