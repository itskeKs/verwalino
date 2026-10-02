"""Persist private, actor-scoped review results for safe request retries."""
from alembic import op

revision = '0074'
down_revision = '0073'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE declaration_review_receipt (
      organization_id uuid NOT NULL, actor_user_id uuid NOT NULL,
      operation text NOT NULL CHECK(operation IN ('single','bulk')), request_key uuid NOT NULL,
      import_id uuid NOT NULL, request_digest text NOT NULL CHECK(request_digest ~ '^[0-9a-f]{64}$'),
      response jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(organization_id,actor_user_id,operation,request_key),
      FOREIGN KEY(organization_id,import_id) REFERENCES declaration_import(organization_id,id) ON DELETE RESTRICT
    );
    ALTER TABLE declaration_review_receipt ENABLE ROW LEVEL SECURITY;
    ALTER TABLE declaration_review_receipt FORCE ROW LEVEL SECURITY;
    CREATE POLICY review_receipt_private ON declaration_review_receipt USING(false) WITH CHECK(false);
    REVOKE ALL ON declaration_review_receipt FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION declaration_review_request(org uuid,job uuid,kind text,key uuid,digest text,result jsonb DEFAULT NULL)
      RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      receipt public.declaration_review_receipt; revision integer;
    BEGIN
      IF NOT public.lock_declaration_review_graph(org,job) THEN
        RAISE EXCEPTION 'Current human review authority required' USING ERRCODE='42501'; END IF;
      IF kind IS NULL OR kind NOT IN ('single','bulk') OR key IS NULL OR digest IS NULL OR digest !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'INVALID_REVIEW_REQUEST' USING ERRCODE='22023'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('review-request:'||org::text||':'||actor::text||':'||kind||':'||key::text,0));
      SELECT * INTO receipt FROM public.declaration_review_receipt r WHERE r.organization_id=org
        AND r.actor_user_id=actor AND r.operation=kind AND r.request_key=key;
      IF FOUND THEN
        IF receipt.import_id<>job OR receipt.request_digest<>digest THEN
          RAISE EXCEPTION 'IDEMPOTENCY_KEY_CONFLICT' USING ERRCODE='22023'; END IF;
        RETURN receipt.response;
      END IF;
      IF result IS NULL THEN RETURN NULL; END IF;
      SELECT draft_revision INTO revision FROM public.declaration_import WHERE organization_id=org AND id=job;
      IF jsonb_typeof(result)<>'object' OR result->>'draft_revision' IS DISTINCT FROM revision::text OR NOT EXISTS(
        SELECT 1 FROM public.declaration_review_context c WHERE c.organization_id=org AND c.import_id=job
          AND c.actor_user_id=actor AND c.backend_pid=pg_backend_pid() AND c.transaction_id=pg_current_xact_id()
          AND c.draft_revision=revision AND c.expires_at>clock_timestamp()) THEN
        RAISE EXCEPTION 'Current review transaction context required' USING ERRCODE='42501'; END IF;
      INSERT INTO public.declaration_review_receipt(organization_id,actor_user_id,operation,request_key,import_id,request_digest,response)
        VALUES(org,actor,kind,key,job,digest,result);
      RETURN result;
    END $$;
    REVOKE ALL ON FUNCTION declaration_review_request(uuid,uuid,text,uuid,text,jsonb) FROM PUBLIC,app_rw,app_worker;
    GRANT EXECUTE ON FUNCTION declaration_review_request(uuid,uuid,text,uuid,text,jsonb) TO app_review;
    """)


def downgrade():
    op.execute("""
    LOCK TABLE declaration_review_receipt IN ACCESS EXCLUSIVE MODE NOWAIT;
    DO $$ BEGIN IF EXISTS(SELECT 1 FROM declaration_review_receipt) THEN
      RAISE EXCEPTION 'Retain durable review receipts; disable writers instead'; END IF; END $$;
    DROP FUNCTION declaration_review_request(uuid,uuid,text,uuid,text,jsonb);
    DROP TABLE declaration_review_receipt;
    """)
