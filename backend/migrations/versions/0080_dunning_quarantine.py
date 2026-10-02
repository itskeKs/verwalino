"""Retain reviewed unknown dunning history without inventing scope provenance."""
from alembic import op
from sqlalchemy import text

revision = '0080'
down_revision = '0079'
branch_labels = None
depends_on = None
LOOKUP = 'SELECT scope_id INTO sphere FROM public.dunning_scope_context WHERE organization_id=org AND dunning_notice_id=resource;'
GATED = "IF EXISTS(SELECT 1 FROM public.dunning_quarantine_disposition WHERE organization_id=org AND notice_id=resource) THEN RETURN false; END IF;\n      " + LOOKUP


def _gate(before, after):
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('public.dunning_scope_access(uuid,uuid,text,uuid,uuid)'::regprocedure)"))
    if definition.count(before) != 1:
        raise RuntimeError('Unexpected dunning access helper; refusing partial quarantine')
    op.execute(definition.replace(before, after))


def upgrade():
    op.execute('LOCK TABLE dunning_notice,dunning_scope_context IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""
    CREATE TABLE dunning_quarantine_disposition (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL,
      notice_id uuid NOT NULL, snapshot_digest text NOT NULL CHECK(snapshot_digest ~ '^[0-9a-f]{64}$'),
      reason text NOT NULL CHECK(reason IN ('SOURCE_CONTEXT_UNVERIFIED','MIXED_SCOPE_HISTORY')),
      reviewed_by uuid NOT NULL, reviewed_at timestamptz NOT NULL DEFAULT clock_timestamp(), request_key uuid NOT NULL,
      UNIQUE(organization_id,notice_id), UNIQUE(organization_id,reviewed_by,request_key),
      FOREIGN KEY(organization_id,notice_id) REFERENCES dunning_notice(organization_id,id),
      FOREIGN KEY(organization_id,reviewed_by) REFERENCES "user"(organization_id,id)
    );
    ALTER TABLE dunning_quarantine_disposition ENABLE ROW LEVEL SECURITY;
    ALTER TABLE dunning_quarantine_disposition FORCE ROW LEVEL SECURITY;
    CREATE POLICY dunning_disposition_private ON dunning_quarantine_disposition AS RESTRICTIVE FOR ALL USING(false) WITH CHECK(false);
    REVOKE ALL ON dunning_quarantine_disposition FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER protect_dunning_disposition BEFORE UPDATE OR DELETE ON dunning_quarantine_disposition
      FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();

    CREATE FUNCTION protect_dunning_quarantine() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE org uuid; resource uuid;
    BEGIN
      IF TG_TABLE_NAME='dunning_scope_context' THEN org:=NEW.organization_id; resource:=NEW.dunning_notice_id;
      ELSE org:=OLD.organization_id; resource:=OLD.id; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      IF EXISTS(SELECT 1 FROM public.dunning_quarantine_disposition WHERE organization_id=org AND notice_id=resource) THEN
        RAISE EXCEPTION 'Quarantined dunning history and unknown provenance are immutable' USING ERRCODE='23514'; END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION protect_dunning_quarantine() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER quarantine_dunning_original BEFORE UPDATE OR DELETE ON dunning_notice
      FOR EACH ROW EXECUTE FUNCTION protect_dunning_quarantine();
    CREATE TRIGGER quarantine_dunning_context BEFORE INSERT ON dunning_scope_context
      FOR EACH ROW EXECUTE FUNCTION protect_dunning_quarantine();

    CREATE FUNCTION dunning_quarantine_page(org uuid,after_id uuid,take integer) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE result jsonb; instant timestamptz;
    BEGIN
      -- Reuse the private actual-session/live-administrator boundary from 0079.
      PERFORM public.require_mandate_quarantine_admin(org);
      IF take IS NULL OR take NOT BETWEEN 1 AND 201 THEN RAISE EXCEPTION 'Invalid page size' USING ERRCODE='22023'; END IF;
      instant:=clock_timestamp();
      SELECT COALESCE(jsonb_agg(jsonb_build_object('id',n.id,'notice_id',n.id,
        'snapshot_digest',COALESCE(r.snapshot_digest,encode(sha256(convert_to(to_jsonb(n)::text,'UTF8')),'hex')),
        'observed_at',COALESCE(r.reviewed_at,instant),
        'disposition',CASE WHEN r.id IS NULL THEN NULL ELSE to_jsonb(r)-ARRAY['organization_id','request_key'] END)
        ORDER BY n.id),'[]'::jsonb) INTO result
      FROM (SELECT * FROM public.dunning_notice d WHERE d.organization_id=org AND (after_id IS NULL OR d.id>after_id)
        AND NOT EXISTS(SELECT 1 FROM public.dunning_scope_context c WHERE c.organization_id=org AND c.dunning_notice_id=d.id)
        ORDER BY d.id LIMIT take) n
      LEFT JOIN public.dunning_quarantine_disposition r ON r.organization_id=n.organization_id AND r.notice_id=n.id;
      RETURN result;
    END $$;

    CREATE FUNCTION review_dunning_quarantine(org uuid,notice uuid,request uuid,digest text,reason_code text) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid; original public.dunning_notice; receipt public.dunning_quarantine_disposition;
    BEGIN
      PERFORM public.require_mandate_quarantine_admin(org);
      actor:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      IF notice IS NULL OR request IS NULL OR digest IS NULL OR digest !~ '^[0-9a-f]{64}$'
        OR reason_code IS NULL OR reason_code NOT IN ('SOURCE_CONTEXT_UNVERIFIED','MIXED_SCOPE_HISTORY') THEN
        RAISE EXCEPTION 'Invalid dunning quarantine decision' USING ERRCODE='22023'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('dunning-disposition:'||org::text||':'||actor::text||':'||request::text,0));
      SELECT * INTO receipt FROM public.dunning_quarantine_disposition
        WHERE organization_id=org AND reviewed_by=actor AND request_key=request;
      IF FOUND THEN
        IF (receipt.notice_id,receipt.snapshot_digest,receipt.reason) IS DISTINCT FROM (notice,digest,reason_code) THEN
          RAISE EXCEPTION 'Dunning quarantine request conflict' USING ERRCODE='23505'; END IF;
        RETURN to_jsonb(receipt)-ARRAY['organization_id','request_key'];
      END IF;
      SELECT * INTO original FROM public.dunning_notice WHERE organization_id=org AND id=notice FOR UPDATE;
      IF NOT FOUND OR EXISTS(SELECT 1 FROM public.dunning_scope_context WHERE organization_id=org AND dunning_notice_id=notice) THEN
        RAISE EXCEPTION 'Unknown dunning context not accessible' USING ERRCODE='42501'; END IF;
      IF encode(sha256(convert_to(to_jsonb(original)::text,'UTF8')),'hex')<>digest THEN
        RAISE EXCEPTION 'Dunning snapshot changed' USING ERRCODE='40001'; END IF;
      IF EXISTS(SELECT 1 FROM public.dunning_quarantine_disposition WHERE organization_id=org AND notice_id=notice) THEN
        RAISE EXCEPTION 'Dunning quarantine already reviewed' USING ERRCODE='23505'; END IF;
      INSERT INTO public.dunning_quarantine_disposition(organization_id,notice_id,snapshot_digest,reason,reviewed_by,request_key)
        VALUES(org,notice,digest,reason_code,actor,request) RETURNING * INTO receipt;
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(org,actor,'dunning_notice',notice,'dunning_notice.quarantine_reviewed',
          jsonb_build_object('disposition_id',receipt.id,'reason',reason_code,'snapshot_digest',digest));
      RETURN to_jsonb(receipt)-ARRAY['organization_id','request_key'];
    END $$;
    REVOKE ALL ON FUNCTION dunning_quarantine_page(uuid,uuid,integer),review_dunning_quarantine(uuid,uuid,uuid,text,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dunning_quarantine_page(uuid,uuid,integer),review_dunning_quarantine(uuid,uuid,uuid,text,text) TO app_rw;
    """)
    _gate(LOOKUP, GATED)


def downgrade():
    op.execute('LOCK TABLE dunning_notice,dunning_scope_context,dunning_quarantine_disposition IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM dunning_quarantine_disposition) THEN
      RAISE EXCEPTION 'Retain quarantined dunning history; repair forward'; END IF; END $$""")
    _gate(GATED, LOOKUP)
    op.execute("""
    DROP TRIGGER quarantine_dunning_original ON dunning_notice;
    DROP TRIGGER quarantine_dunning_context ON dunning_scope_context;
    DROP FUNCTION protect_dunning_quarantine();
    DROP FUNCTION review_dunning_quarantine(uuid,uuid,uuid,text,text),dunning_quarantine_page(uuid,uuid,integer);
    DROP TABLE dunning_quarantine_disposition;
    """)
