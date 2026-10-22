"""Immutable actor-scoped CSV master import batches (T51)."""
from alembic import op

revision = '0134'
down_revision = '0133'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE master_import_batch (
      organization_id uuid NOT NULL,
      actor_user_id uuid NOT NULL,
      request_key uuid NOT NULL,
      request_digest varchar(64) NOT NULL CHECK(request_digest ~ '^[0-9a-f]{64}$'),
      result jsonb NOT NULL CHECK(jsonb_typeof(result)='object'),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(organization_id,actor_user_id,request_key),
      FOREIGN KEY(organization_id,actor_user_id) REFERENCES "user"(organization_id,id)
    );
    ALTER TABLE master_import_batch ENABLE ROW LEVEL SECURITY;
    ALTER TABLE master_import_batch FORCE ROW LEVEL SECURITY;
    CREATE POLICY master_import_read ON master_import_batch FOR SELECT USING (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND actor_user_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid
      AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK'));
    CREATE POLICY master_import_insert ON master_import_batch FOR INSERT WITH CHECK (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND actor_user_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid
      AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK'));
    REVOKE ALL ON master_import_batch FROM PUBLIC,app_rw;
    GRANT SELECT,INSERT ON master_import_batch TO app_rw;
    CREATE FUNCTION protect_master_import_batch() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      RAISE EXCEPTION 'Master import batch is immutable' USING ERRCODE='23514';
    END $$;
    REVOKE ALL ON FUNCTION protect_master_import_batch() FROM PUBLIC,app_rw;
    CREATE TRIGGER protect_master_import_batch BEFORE UPDATE OR DELETE ON master_import_batch
      FOR EACH ROW EXECUTE FUNCTION protect_master_import_batch();
    """)


def downgrade():
    op.execute("""
    LOCK TABLE master_import_batch IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM master_import_batch) THEN
        RAISE EXCEPTION 'Cannot remove populated master import batches';
      END IF;
    END $$;
    DROP TABLE master_import_batch;
    DROP FUNCTION protect_master_import_batch();
    """)
