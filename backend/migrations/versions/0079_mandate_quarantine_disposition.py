"""Record current administrative quarantine disposition without original access."""
from alembic import op

revision = '0079'
down_revision = '0078'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE mandate_quarantine_disposition (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL,
      document_id uuid NOT NULL, snapshot_digest text NOT NULL CHECK(snapshot_digest ~ '^[0-9a-f]{64}$'),
      reason text NOT NULL CHECK(reason IN ('CUSTODY_UNVERIFIED','EXTERNAL_ORIGINAL_UNAVAILABLE')),
      reviewed_by uuid NOT NULL, reviewed_at timestamptz NOT NULL DEFAULT clock_timestamp(), request_key uuid NOT NULL,
      UNIQUE(organization_id,document_id), UNIQUE(organization_id,reviewed_by,request_key),
      FOREIGN KEY(organization_id,document_id) REFERENCES mandate_legacy_observation(organization_id,document_id),
      FOREIGN KEY(organization_id,reviewed_by) REFERENCES "user"(organization_id,id)
    );
    ALTER TABLE mandate_quarantine_disposition ENABLE ROW LEVEL SECURITY;
    ALTER TABLE mandate_quarantine_disposition FORCE ROW LEVEL SECURITY;
    CREATE POLICY mandate_disposition_private ON mandate_quarantine_disposition AS RESTRICTIVE FOR ALL USING(false) WITH CHECK(false);
    REVOKE ALL ON mandate_quarantine_disposition FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER protect_mandate_disposition BEFORE UPDATE OR DELETE ON mandate_quarantine_disposition
      FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();

    CREATE FUNCTION require_mandate_quarantine_admin(org uuid) RETURNS void
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF session_user<>'app_rw' OR org IS NULL OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Current quarantine administrator required' USING ERRCODE='42501'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      IF public.scope_actor_role(org) IS DISTINCT FROM 'OWNER_ADMIN' THEN
        RAISE EXCEPTION 'Current quarantine administrator required' USING ERRCODE='42501'; END IF;
    END $$;
    REVOKE ALL ON FUNCTION require_mandate_quarantine_admin(uuid) FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION mandate_quarantine_page(org uuid,after_id uuid,take integer) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE result jsonb;
    BEGIN
      PERFORM public.require_mandate_quarantine_admin(org);
      IF take IS NULL OR take NOT BETWEEN 1 AND 201 THEN RAISE EXCEPTION 'Invalid page size' USING ERRCODE='22023'; END IF;
      SELECT COALESCE(jsonb_agg(jsonb_build_object('id',o.document_id,'document_id',o.document_id,
        'snapshot_digest',o.snapshot_digest,'observed_at',o.observed_at,
        'disposition',CASE WHEN r.id IS NULL THEN NULL ELSE to_jsonb(r)-ARRAY['organization_id','request_key'] END)
        ORDER BY o.document_id),'[]'::jsonb) INTO result
      FROM (SELECT * FROM public.mandate_legacy_observation WHERE organization_id=org
        AND (after_id IS NULL OR document_id>after_id) ORDER BY document_id LIMIT take) o
      LEFT JOIN public.mandate_quarantine_disposition r ON r.organization_id=o.organization_id AND r.document_id=o.document_id;
      RETURN result;
    END $$;

    CREATE FUNCTION review_mandate_quarantine(org uuid,document uuid,request uuid,digest text,reason_code text) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid; observation public.mandate_legacy_observation; receipt public.mandate_quarantine_disposition;
    BEGIN
      PERFORM public.require_mandate_quarantine_admin(org);
      actor:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      IF document IS NULL OR request IS NULL OR digest IS NULL OR digest !~ '^[0-9a-f]{64}$'
        OR reason_code IS NULL OR reason_code NOT IN ('CUSTODY_UNVERIFIED','EXTERNAL_ORIGINAL_UNAVAILABLE') THEN
        RAISE EXCEPTION 'Invalid quarantine decision' USING ERRCODE='22023'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('mandate-disposition:'||org::text||':'||actor::text||':'||request::text,0));
      SELECT * INTO receipt FROM public.mandate_quarantine_disposition
        WHERE organization_id=org AND reviewed_by=actor AND request_key=request;
      IF FOUND THEN
        IF (receipt.document_id,receipt.snapshot_digest,receipt.reason) IS DISTINCT FROM (document,digest,reason_code) THEN
          RAISE EXCEPTION 'Quarantine request conflict' USING ERRCODE='23505'; END IF;
        RETURN to_jsonb(receipt)-ARRAY['organization_id','request_key'];
      END IF;
      SELECT * INTO observation FROM public.mandate_legacy_observation
        WHERE organization_id=org AND document_id=document FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'Quarantine observation inaccessible' USING ERRCODE='42501'; END IF;
      IF observation.snapshot_digest<>digest THEN RAISE EXCEPTION 'Quarantine snapshot conflict' USING ERRCODE='40001'; END IF;
      IF EXISTS(SELECT 1 FROM public.mandate_quarantine_disposition WHERE organization_id=org AND document_id=document) THEN
        RAISE EXCEPTION 'Quarantine already reviewed' USING ERRCODE='23505'; END IF;
      INSERT INTO public.mandate_quarantine_disposition(organization_id,document_id,snapshot_digest,reason,reviewed_by,request_key)
        VALUES(org,document,digest,reason_code,actor,request) RETURNING * INTO receipt;
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(org,actor,'mandate_document',document,'mandate_document.quarantine_reviewed',
          jsonb_build_object('disposition_id',receipt.id,'reason',reason_code,'snapshot_digest',digest));
      RETURN to_jsonb(receipt)-ARRAY['organization_id','request_key'];
    END $$;
    REVOKE ALL ON FUNCTION mandate_quarantine_page(uuid,uuid,integer),review_mandate_quarantine(uuid,uuid,uuid,text,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION mandate_quarantine_page(uuid,uuid,integer),review_mandate_quarantine(uuid,uuid,uuid,text,text) TO app_rw;
    """)


def downgrade():
    op.execute('LOCK TABLE mandate_quarantine_disposition IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM mandate_quarantine_disposition) THEN
      RAISE EXCEPTION 'Retain reviewed quarantine decisions; repair forward'; END IF; END $$""")
    op.execute("""
    DROP FUNCTION review_mandate_quarantine(uuid,uuid,uuid,text,text),mandate_quarantine_page(uuid,uuid,integer);
    DROP FUNCTION require_mandate_quarantine_admin(uuid);
    DROP TABLE mandate_quarantine_disposition;
    """)
