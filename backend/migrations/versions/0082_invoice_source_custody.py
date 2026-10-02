"""Record fresh invoice custody and authorize standalone source/lineage effects."""
from alembic import op
from sqlalchemy import text

revision = '0082'
down_revision = '0081'
branch_labels = None
depends_on = None
FIELDS = ('id','organization_id','uploaded_by','cost_entry_id','storage_uri','storage_backend','storage_version',
          'original_filename','mime_type','content_byte_size','content_sha256','version','supersedes_id','chain_root_id')
BEFORE = "sphere:=public.financial_scope_id(org,kind,resource);"
AFTER = """IF kind='invoice_document' AND public.property_source_security_mode(org) IN ('ENFORCED','PAUSED') THEN
        RETURN public.invoice_source_access(org,resource,capability); END IF;
      """ + BEFORE


def _replace(before, after):
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('financial_scope_access(uuid,text,uuid,text)'::regprocedure)"))
    if definition.count(before) != 1:
        raise RuntimeError('Unexpected financial source helper')
    op.execute(definition.replace(before, after))


def upgrade():
    op.execute('LOCK TABLE invoice_document,invoice_processing_attempt,property_source_security IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""
    ALTER TABLE invoice_document ADD COLUMN uploaded_by uuid,
      ADD CONSTRAINT fk_invoice_uploader FOREIGN KEY(organization_id,uploaded_by) REFERENCES "user"(organization_id,id);
    CREATE TABLE invoice_upload_admission(
      document_id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organization(id),
      uploaded_by uuid NOT NULL, payload_digest text NOT NULL CHECK(payload_digest ~ '^[0-9a-f]{64}$'),
      admitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      FOREIGN KEY(organization_id,uploaded_by) REFERENCES "user"(organization_id,id));
    ALTER TABLE invoice_upload_admission ENABLE ROW LEVEL SECURITY;
    ALTER TABLE invoice_upload_admission FORCE ROW LEVEL SECURITY;
    CREATE POLICY invoice_admission_private ON invoice_upload_admission AS RESTRICTIVE FOR ALL USING(false) WITH CHECK(false);
    REVOKE ALL ON invoice_upload_admission FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER protect_invoice_admission BEFORE UPDATE OR DELETE ON invoice_upload_admission
      FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();

    CREATE FUNCTION admit_invoice_upload(org uuid,document uuid,payload jsonb) RETURNS void
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      IF session_user<>'app_review' OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Trusted upload runtime required' USING ERRCODE='42501'; END IF;
      -- Attestation never waits on ACL: the calling upload holds that lock on
      -- another connection. A queued revocation must not create an application
      -- level cycle. Admission itself confers no authority; INSERT rechecks it.
      IF COALESCE((SELECT mode FROM public.property_source_security WHERE organization_id=org),'PREPARATION')='PAUSED'
        OR NOT COALESCE(public.scope_actor_role(org) IN ('OWNER_ADMIN','CLERK'),false) THEN
        RAISE EXCEPTION 'Current upload authority required' USING ERRCODE='42501'; END IF;
      IF document IS NULL OR jsonb_typeof(payload) IS DISTINCT FROM 'object'
        OR (payload->>'id')::uuid IS DISTINCT FROM document OR (payload->>'organization_id')::uuid IS DISTINCT FROM org
        OR (payload->>'uploaded_by')::uuid IS DISTINCT FROM actor THEN
        RAISE EXCEPTION 'Upload admission identity mismatch' USING ERRCODE='22023'; END IF;
      INSERT INTO public.invoice_upload_admission(document_id,organization_id,uploaded_by,payload_digest)
        VALUES(document,org,actor,encode(sha256(convert_to(payload::text,'UTF8')),'hex'));
    END $$;
    REVOKE ALL ON FUNCTION admit_invoice_upload(uuid,uuid,jsonb) FROM PUBLIC,app_rw,app_worker;
    GRANT EXECUTE ON FUNCTION admit_invoice_upload(uuid,uuid,jsonb) TO app_review;

    CREATE FUNCTION invoice_source_row_access(source public.invoice_document,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE org uuid:=source.organization_id; mode text; actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; role text;
    BEGIN
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL OR capability IS NULL OR capability NOT IN ('read','prepare','confirm','approve','send','export') THEN RETURN false; END IF;
      role:=public.scope_actor_role(org);
      IF role IS NULL OR (capability NOT IN ('read','export') AND (mode='PAUSED' OR role NOT IN ('OWNER_ADMIN','CLERK'))) THEN RETURN false; END IF;
      IF source.cost_entry_id IS NOT NULL THEN RETURN public.financial_scope_access(org,'cost_entry',source.cost_entry_id,capability); END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      RETURN COALESCE(source.uploaded_by=actor,false) AND (source.chain_root_id=source.id OR EXISTS(SELECT 1 FROM public.invoice_document root
        WHERE root.organization_id=org AND root.id=source.chain_root_id AND root.uploaded_by=actor))
        AND (capability IN ('read','export') OR (capability='prepare' AND role IN ('OWNER_ADMIN','CLERK')));
    END $$;
    CREATE FUNCTION invoice_source_access(org uuid,document uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE source public.invoice_document;
    BEGIN
      SELECT * INTO source FROM public.invoice_document WHERE organization_id=org AND id=document;
      IF NOT FOUND THEN RETURN false; END IF;
      RETURN public.invoice_source_row_access(source,capability);
    END $$;
    REVOKE ALL ON FUNCTION invoice_source_row_access(public.invoice_document,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION invoice_source_row_access(public.invoice_document,text) TO app_rw;
    -- INSERT RETURNING checks this policy before a lookup can find the new row.
    -- The immutable custody/admission trigger separately validates every NEW row.
    ALTER POLICY classified_financial_read ON invoice_document USING(invoice_source_row_access(invoice_document,'read'));
    CREATE FUNCTION authorize_invoice_source_update() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      -- Narrow definer cancellation remains trusted; ordinary SQL never gains
      -- its owner identity by changing a session setting.
      IF current_user IN ('app_rw','app_worker') AND NOT public.invoice_source_access(OLD.organization_id,OLD.id,
        CASE WHEN OLD.released_at IS NOT NULL OR NEW.released_at IS NOT NULL THEN 'confirm' ELSE 'prepare' END) THEN
        RAISE EXCEPTION 'Current invoice source preparation authority required' USING ERRCODE='42501'; END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION authorize_invoice_source_update() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER b_authorize_invoice_source_update BEFORE UPDATE ON invoice_document
      FOR EACH ROW EXECUTE FUNCTION authorize_invoice_source_update();
    REVOKE ALL ON FUNCTION invoice_source_access(uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION invoice_source_access(uuid,uuid,text) TO app_rw;

    CREATE FUNCTION lock_invoice_sources() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE org uuid:=NULLIF(current_setting('app.current_org_id',true),'')::uuid;
    BEGIN
      IF org IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
        PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0)); END IF;
      RETURN NULL;
    END $$;
    REVOKE ALL ON FUNCTION lock_invoice_sources() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER a_lock_invoice_sources BEFORE INSERT OR UPDATE OR DELETE ON invoice_document
      FOR EACH STATEMENT EXECUTE FUNCTION lock_invoice_sources();
    """)
    fields = ','.join("'%s',to_jsonb(NEW)->'%s'" % (field,field) for field in FIELDS)
    frozen = ','.join('NEW.'+field for field in FIELDS if field != 'cost_entry_id')+',NEW.created_at'
    old = frozen.replace('NEW.','OLD.')
    op.execute(f"""
    CREATE FUNCTION guard_invoice_custody() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; previous public.invoice_document; payload jsonb;
      runtime boolean:=session_user IN ('app_rw','app_worker');
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Invoice originals are retained' USING ERRCODE='23514'; END IF;
      IF TG_OP='UPDATE' THEN
        IF ({frozen}) IS DISTINCT FROM ({old}) THEN
          RAISE EXCEPTION 'Invoice identity, custody, content and lineage are immutable' USING ERRCODE='23514'; END IF;
        IF NEW.cost_entry_id IS DISTINCT FROM OLD.cost_entry_id THEN
          IF OLD.cost_entry_id IS NOT NULL OR OLD.uploaded_by IS NULL OR OLD.uploaded_by IS DISTINCT FROM actor
            OR NEW.cost_entry_id IS NULL OR NOT public.financial_scope_access(NEW.organization_id,'cost_entry',NEW.cost_entry_id,'prepare') THEN
            RAISE EXCEPTION 'Invoice linking requires original uploader and destination preparation authority' USING ERRCODE='42501'; END IF;
        END IF;
        RETURN NEW;
      END IF;
      IF actor IS NOT NULL THEN
        IF NOT COALESCE(public.scope_actor_role(NEW.organization_id) IN ('OWNER_ADMIN','CLERK'),false)
          OR public.property_source_security_mode(NEW.organization_id)='PAUSED'
          OR (NEW.uploaded_by IS NOT NULL AND NEW.uploaded_by IS DISTINCT FROM actor) THEN
          RAISE EXCEPTION 'Current invoice uploader required' USING ERRCODE='42501'; END IF;
        NEW.uploaded_by:=actor;
      ELSIF runtime THEN RAISE EXCEPTION 'Invoice uploader required' USING ERRCODE='42501'; END IF;
      IF runtime THEN
        payload:=jsonb_build_object({fields});
        IF NOT EXISTS(SELECT 1 FROM public.invoice_upload_admission a WHERE a.document_id=NEW.id
          AND a.organization_id=NEW.organization_id AND a.uploaded_by=actor
          AND a.payload_digest=encode(sha256(convert_to(payload::text,'UTF8')),'hex')) THEN
          RAISE EXCEPTION 'Trusted fresh upload admission required' USING ERRCODE='42501'; END IF;
        NEW.created_at:=clock_timestamp();
      END IF;
      IF NEW.supersedes_id IS NULL THEN
        IF NEW.version<>1 OR NEW.chain_root_id IS DISTINCT FROM NEW.id THEN
          RAISE EXCEPTION 'Invalid invoice root' USING ERRCODE='23514'; END IF;
      ELSE
        SELECT * INTO previous FROM public.invoice_document WHERE organization_id=NEW.organization_id AND id=NEW.supersedes_id;
        IF NOT FOUND OR NEW.chain_root_id IS DISTINCT FROM previous.chain_root_id OR NEW.version<>previous.version+1
          OR EXISTS(SELECT 1 FROM public.invoice_document d WHERE d.organization_id=NEW.organization_id AND d.supersedes_id=previous.id) THEN
          RAISE EXCEPTION 'Invoice successor must extend one current chain version' USING ERRCODE='23514'; END IF;
        IF previous.cost_entry_id IS NULL AND (previous.uploaded_by IS NULL OR previous.uploaded_by IS DISTINCT FROM actor) THEN
          RAISE EXCEPTION 'Unknown or foreign standalone invoice custody cannot be inherited' USING ERRCODE='42501'; END IF;
        IF runtime AND NOT public.invoice_source_access(NEW.organization_id,previous.id,'prepare') THEN
          RAISE EXCEPTION 'Invoice predecessor preparation authority required' USING ERRCODE='42501'; END IF;
        IF previous.cost_entry_id IS NOT NULL AND NEW.cost_entry_id IS DISTINCT FROM previous.cost_entry_id
          AND NOT EXISTS(SELECT 1 FROM public.cost_entry c WHERE c.organization_id=NEW.organization_id
            AND c.id=previous.cost_entry_id AND c.superseded_by_id=NEW.cost_entry_id) THEN
          RAISE EXCEPTION 'Invoice successor must retain cost or its correction' USING ERRCODE='23514'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION guard_invoice_custody() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER guard_invoice_custody BEFORE INSERT OR UPDATE OR DELETE ON invoice_document
      FOR EACH ROW EXECUTE FUNCTION guard_invoice_custody();
    """)
    _replace(BEFORE, AFTER)
    # Cancellation must take the same locks before its document/attempt row locks.
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('cancel_unauthorized_invoice_attempt(uuid,uuid,uuid)'::regprocedure)"))
    marker='SELECT * INTO job FROM public.invoice_processing_attempt WHERE organization_id=org AND id=attempt;'
    assert definition.count(marker)==1
    op.execute(definition.replace(marker,"PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));\n      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));\n      "+marker))


def downgrade():
    op.execute('LOCK TABLE invoice_document,invoice_processing_attempt,invoice_upload_admission,property_source_security IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM invoice_upload_admission) OR EXISTS(SELECT 1 FROM invoice_document WHERE uploaded_by IS NOT NULL)
      OR EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain invoice custody and admissions; repair forward'; END IF; END $$""")
    _replace(AFTER, BEFORE)
    op.execute("ALTER POLICY classified_financial_read ON invoice_document USING(financial_scope_access(organization_id,'invoice_document',id,'read'))")
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('cancel_unauthorized_invoice_attempt(uuid,uuid,uuid)'::regprocedure)"))
    locks="PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));\n      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));\n      "
    assert definition.count(locks)==1
    op.execute(definition.replace(locks,''))
    op.execute('''DROP TRIGGER guard_invoice_custody ON invoice_document;
      DROP TRIGGER a_lock_invoice_sources ON invoice_document;
      DROP TRIGGER b_authorize_invoice_source_update ON invoice_document;
      DROP FUNCTION authorize_invoice_source_update(),guard_invoice_custody(),lock_invoice_sources(),invoice_source_access(uuid,uuid,text),admit_invoice_upload(uuid,uuid,jsonb);
      DROP FUNCTION IF EXISTS invoice_source_row_access(public.invoice_document,text);
      DROP TABLE invoice_upload_admission;
      ALTER TABLE invoice_document DROP COLUMN uploaded_by;''')
