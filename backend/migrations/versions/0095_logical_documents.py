"""Immutable logical page references bound to an exact clean inspection."""
from alembic import op
from sqlalchemy import text

revision='0095'
down_revision='0094'
branch_labels=None
depends_on=None
BEFORE="UNION ALL SELECT 'attempt',id,to_jsonb(r) FROM public.job_attempt r WHERE organization_id=org) evidence"
AFTER="UNION ALL SELECT 'attempt',id,to_jsonb(r) FROM public.job_attempt r WHERE organization_id=org UNION ALL SELECT 'logical',id,to_jsonb(r) FROM public.logical_document r WHERE organization_id=org) evidence"


def upgrade():
    op.execute('''
    CREATE TABLE logical_document(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),
      document_ref_id uuid NOT NULL,verified_attempt_id uuid NOT NULL,content_sha256 text NOT NULL,
      page_start integer,page_end integer,verified_page_count integer,
      review_state text NOT NULL DEFAULT 'unreviewed' CHECK(review_state='unreviewed'),
      created_by uuid NOT NULL,request_key uuid NOT NULL,request_digest text NOT NULL,
      reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 1000),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(organization_id,id),UNIQUE(organization_id,created_by,request_key),
      UNIQUE NULLS NOT DISTINCT(organization_id,document_ref_id,page_start,page_end),
      CHECK((verified_page_count IS NULL AND page_start IS NULL AND page_end IS NULL) OR
        (verified_page_count BETWEEN 1 AND 200 AND page_start IS NOT NULL AND page_end IS NOT NULL
          AND page_start>=1 AND page_end>=page_start AND page_end<=verified_page_count)),
      FOREIGN KEY(organization_id,document_ref_id) REFERENCES document_ref(organization_id,id),
      FOREIGN KEY(organization_id,verified_attempt_id) REFERENCES job_attempt(organization_id,id),
      FOREIGN KEY(organization_id,created_by) REFERENCES "user"(organization_id,id));
    CREATE INDEX ix_logical_document_source ON logical_document(organization_id,document_ref_id,created_at,id);
    ALTER TABLE logical_document ENABLE ROW LEVEL SECURITY;
    ALTER TABLE logical_document FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON logical_document FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT SELECT ON logical_document TO app_rw;
    CREATE POLICY logical_document_read ON logical_document FOR SELECT USING(
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid AND EXISTS(
        SELECT 1 FROM document_ref r WHERE r.organization_id=logical_document.organization_id AND r.id=logical_document.document_ref_id));
    CREATE POLICY logical_document_no_insert ON logical_document FOR INSERT WITH CHECK(false);
    CREATE TRIGGER protect_logical_document BEFORE UPDATE OR DELETE ON logical_document FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();

    CREATE FUNCTION validate_logical_document() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE ref public.document_ref; attempt public.job_attempt; pages integer;
    BEGIN
      SELECT * INTO ref FROM public.document_ref WHERE organization_id=NEW.organization_id AND id=NEW.document_ref_id;
      SELECT a.* INTO attempt FROM public.job_attempt a JOIN public.workflow_job j ON j.organization_id=a.organization_id AND j.id=a.job_id
        WHERE a.organization_id=NEW.organization_id AND a.id=NEW.verified_attempt_id AND j.original_id=ref.intake_original_id
          AND j.status='clean' AND j.fence=a.fence AND a.finished_at IS NOT NULL AND a.result->>'status'='clean';
      IF NOT FOUND OR ref.source_kind<>'intake_original' OR NEW.content_sha256 IS DISTINCT FROM ref.content_sha256
        OR attempt.result->>'content_sha256' IS DISTINCT FROM ref.content_sha256
        OR attempt.result->>'mime_type' IS DISTINCT FROM ref.mime_type THEN
        RAISE EXCEPTION 'Logical source requires exact clean inspection' USING ERRCODE='23514'; END IF;
      pages:=(attempt.result->>'page_count')::integer;
      IF NEW.verified_page_count IS DISTINCT FROM pages OR
        (ref.mime_type='application/xml' AND (NEW.page_start IS NOT NULL OR NEW.page_end IS NOT NULL)) OR
        (ref.mime_type<>'application/xml' AND (pages IS NULL OR NEW.page_start IS NULL OR NEW.page_end IS NULL)) THEN
        RAISE EXCEPTION 'Logical source requires verified page bounds' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER validate_logical_document BEFORE INSERT ON logical_document FOR EACH ROW EXECUTE FUNCTION validate_logical_document();

    CREATE FUNCTION create_logical_document(org uuid,ref_id uuid,request uuid,first_page integer,last_page integer,explanation text) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      ref public.document_ref; attempt public.job_attempt; prior public.logical_document; batch uuid; digest text; created uuid;
    BEGIN
      PERFORM public.lock_intake(org);
      SELECT * INTO ref FROM public.document_ref WHERE organization_id=org AND id=ref_id;
      IF NOT FOUND OR ref.source_kind<>'intake_original' OR NOT public.intake_original_access(org,ref.intake_original_id,true) THEN
        RAISE EXCEPTION 'Logical source inaccessible' USING ERRCODE='42501'; END IF;
      SELECT i.batch_id INTO batch FROM public.intake_original o JOIN public.intake_item i ON i.organization_id=o.organization_id AND i.id=o.item_id
        WHERE o.organization_id=org AND o.id=ref.intake_original_id;
      IF NOT public.intake_batch_access(org,batch,true) THEN RAISE EXCEPTION 'Logical source inaccessible' USING ERRCODE='42501'; END IF;
      IF request IS NULL OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'Logical source reason and request required' USING ERRCODE='22023'; END IF;
      digest:=encode(sha256(convert_to(jsonb_build_array(ref_id,first_page,last_page,explanation)::text,'UTF8')),'hex');
      SELECT * INTO prior FROM public.logical_document WHERE organization_id=org AND created_by=actor AND request_key=request;
      IF FOUND THEN
        IF prior.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Logical request changed' USING ERRCODE='23505'; END IF;
        RETURN prior.id;
      END IF;
      IF (SELECT count(*) FROM public.logical_document WHERE organization_id=org AND document_ref_id=ref_id)>=200 THEN
        RAISE EXCEPTION 'Logical source child limit reached' USING ERRCODE='22023'; END IF;
      SELECT a.* INTO attempt FROM public.job_attempt a JOIN public.workflow_job j ON j.organization_id=a.organization_id AND j.id=a.job_id
        WHERE j.organization_id=org AND j.original_id=ref.intake_original_id AND j.status='clean' AND j.fence=a.fence;
      INSERT INTO public.logical_document(organization_id,document_ref_id,verified_attempt_id,content_sha256,page_start,page_end,verified_page_count,
        created_by,request_key,request_digest,reason) VALUES(org,ref.id,attempt.id,ref.content_sha256,first_page,last_page,(attempt.result->>'page_count')::integer,
        actor,request,digest,explanation) RETURNING id INTO created;
      INSERT INTO public.outbox_event(organization_id,resource_id,event_type,effect_key)
        VALUES(org,ref.intake_original_id,'intake.logical_created','logical:'||created::text);
      RETURN created;
    END $$;
    REVOKE ALL ON FUNCTION validate_logical_document(),create_logical_document(uuid,uuid,uuid,integer,integer,text) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION create_logical_document(uuid,uuid,uuid,integer,integer,text) TO app_rw;
    ALTER TABLE outbox_event DROP CONSTRAINT outbox_event_event_type_check;
    ALTER TABLE outbox_event ADD CONSTRAINT outbox_event_event_type_check CHECK(event_type IN ('intake.received','intake.inspected','intake.cancelled','intake.logical_created'));
    ''')
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(BEFORE)==1
    op.execute(definition.replace(BEFORE,AFTER))


def downgrade():
    op.execute("LOCK TABLE logical_document,outbox_event IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM logical_document) OR EXISTS(SELECT 1 FROM outbox_event WHERE event_type='intake.logical_created') THEN RAISE EXCEPTION 'Retain logical source evidence; repair forward'; END IF; END $$")
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(AFTER)==1
    op.execute(definition.replace(AFTER,BEFORE))
    op.execute('''DROP FUNCTION create_logical_document(uuid,uuid,uuid,integer,integer,text);
      DROP TABLE logical_document;
      DROP FUNCTION validate_logical_document();
      ALTER TABLE outbox_event DROP CONSTRAINT outbox_event_event_type_check;
      ALTER TABLE outbox_event ADD CONSTRAINT outbox_event_event_type_check CHECK(event_type IN ('intake.received','intake.inspected','intake.cancelled'));''')
