"""Bounded private inbox headers and live action availability."""
from alembic import op
from sqlalchemy import text

revision='0094'
down_revision='0093'
branch_labels=None
depends_on=None
BEFORE="'original_id',o.id,'job_id',j.id,'fence',j.fence,'error_code'"
AFTER="""'original_id',o.id,'job_id',j.id,'fence',j.fence,
        'can_export',CASE WHEN o.id IS NULL THEN false ELSE public.intake_original_export_access(org,o.id) END,
        'can_retry',COALESCE(j.status IN ('blocked','cancelled') AND j.attempt_number<3 AND public.intake_batch_access(org,batch,true),false),
        'can_upload',o.id IS NULL AND public.intake_batch_access(org,batch,true),'error_code'"""


def upgrade():
    op.execute('''
    CREATE FUNCTION intake_batch_headers(org uuid,before_time timestamptz,before_id uuid,take integer) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; result jsonb;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid OR org IS NULL THEN
        RAISE EXCEPTION 'Intake organization required' USING ERRCODE='42501'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      IF public.scope_actor_role(org) IS NULL THEN RAISE EXCEPTION 'Intake principal required' USING ERRCODE='42501'; END IF;
      IF take IS NULL OR take NOT BETWEEN 1 AND 200 OR (before_time IS NULL)<>(before_id IS NULL) THEN
        RAISE EXCEPTION 'Invalid intake page' USING ERRCODE='22023'; END IF;
      SELECT COALESCE(jsonb_agg(jsonb_build_object('id',b.id,'created_at',b.created_at,'scope_hint_id',b.scope_hint_id,
        'item_count',jsonb_array_length(b.manifest)) ORDER BY b.created_at DESC,b.id DESC),'[]') INTO result FROM (
          SELECT * FROM public.intake_batch b WHERE organization_id=org AND initiated_by=actor
            AND (before_time IS NULL OR (b.created_at,b.id)<(before_time,before_id))
            AND public.intake_actor_access(org,actor,b.scope_hint_id,false)
          ORDER BY created_at DESC,id DESC LIMIT take+1) b;
      RETURN result;
    END $$;
    REVOKE ALL ON FUNCTION intake_batch_headers(uuid,timestamptz,uuid,integer) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION intake_batch_headers(uuid,timestamptz,uuid,integer) TO app_rw;
    ''')
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('intake_batch_status(uuid,uuid,integer,integer)'::regprocedure)"))
    assert definition.count(BEFORE)==1
    op.execute(definition.replace(BEFORE,AFTER))


def downgrade():
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('intake_batch_status(uuid,uuid,integer,integer)'::regprocedure)"))
    assert definition.count(AFTER)==1
    op.execute(definition.replace(AFTER,BEFORE))
    op.execute('DROP FUNCTION intake_batch_headers(uuid,timestamptz,uuid,integer)')
