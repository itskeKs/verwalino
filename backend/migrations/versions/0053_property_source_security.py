"""Stage organization source enforcement and integrate immutable queue actors.

Revision ID: 0053
Revises: 0052
"""
from alembic import op

revision='0053'
down_revision='0052'
branch_labels=None
depends_on=None

PREVIOUS_QUEUE_ACTOR = """
    CREATE OR REPLACE FUNCTION declaration_queue_actor(org uuid, job uuid, request uuid) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF org IS NULL OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN NULL; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      RETURN (SELECT q.actor_user_id FROM public.declaration_queue_request q
        JOIN public."user" u ON u.organization_id=q.organization_id AND u.id=q.actor_user_id
        JOIN public.membership m ON m.organization_id=u.organization_id AND m.user_id=u.id
        WHERE q.organization_id=org AND q.import_id=job AND q.id=request
          AND u.is_active AND m.role IN ('OWNER_ADMIN','CLERK'));
    END $$;
"""

PREVIOUS_QUEUE_COMMAND = """
    CREATE OR REPLACE FUNCTION record_declaration_queue_request(org uuid, job uuid, request uuid, command text) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE item public.declaration_import; receipt public.declaration_queue_request; actor uuid;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Import inaccessible' USING ERRCODE='42501'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      IF NOT COALESCE(public.scope_actor_role(org) IN ('OWNER_ADMIN','CLERK'),false) THEN
        RAISE EXCEPTION 'Writer required' USING ERRCODE='42501'; END IF;
      actor:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      SELECT * INTO item FROM public.declaration_import WHERE organization_id=org AND id=job FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'Import inaccessible' USING ERRCODE='42501'; END IF;
      SELECT * INTO receipt FROM public.declaration_queue_request
        WHERE organization_id=org AND import_id=job AND request_key=request;
      IF FOUND THEN
        IF receipt.actor_user_id<>actor OR receipt.operation<>command THEN
          RAISE EXCEPTION 'Queue command conflict' USING ERRCODE='23514'; END IF;
        RETURN receipt.id;
      END IF;
      IF request IS NULL OR command IS NULL OR NOT (
        (command='START' AND item.status='DRAFT' AND EXISTS(SELECT 1 FROM public.declaration_import_document WHERE organization_id=org AND import_id=job)) OR
        (command='RETRY' AND ((item.status IN ('FAILED','RETRY_WAIT') AND item.error_code IN
          ('PROVIDER_TIMEOUT','PROVIDER_INCOMPLETE','PROVIDER_RATE_LIMIT','PROVIDER_SERVER_ERROR','PROVIDER_CONNECTION_ERROR','OCR_TIMEOUT','WORKER_INTERRUPTED')) OR
          (item.status='CANCELLED' AND item.error_code IN ('JOB_PRINCIPAL_UNVERIFIED','JOB_PERMISSION_CHANGED'))))) THEN
        RAISE EXCEPTION 'Invalid queue command' USING ERRCODE='23514'; END IF;
      INSERT INTO public.declaration_queue_request(organization_id,import_id,actor_user_id,request_key,operation,previous_request_id)
        VALUES(org,job,actor,request,command,item.queue_request_id) RETURNING * INTO receipt;
      UPDATE public.declaration_import SET queue_request_id=receipt.id,status='QUEUED',current_stage='queued',
        next_attempt_at=NULL,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
        lease_generation=lease_generation+1,error_code=NULL,error_message=NULL WHERE organization_id=org AND id=job;
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(org,actor,'declaration_import',job,'declaration.queued',jsonb_build_object('queue_request_id',receipt.id,'operation',command));
      RETURN receipt.id;
    END $$;
"""

QUEUE_COMMAND = """
    CREATE OR REPLACE FUNCTION record_declaration_queue_request(org uuid, job uuid, request uuid, command text) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE item public.declaration_import; receipt public.declaration_queue_request; actor uuid;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Import inaccessible' USING ERRCODE='42501'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      IF NOT COALESCE(public.scope_actor_role(org) IN ('OWNER_ADMIN','CLERK'),false) THEN
        RAISE EXCEPTION 'Writer required' USING ERRCODE='42501'; END IF;
      actor:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      IF NOT public.property_source_actor_permitted(org,actor,'declaration_import',job,'prepare') THEN
        RAISE EXCEPTION 'Import inaccessible' USING ERRCODE='42501'; END IF;
      SELECT * INTO item FROM public.declaration_import WHERE organization_id=org AND id=job FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'Import inaccessible' USING ERRCODE='42501'; END IF;
      SELECT * INTO receipt FROM public.declaration_queue_request
        WHERE organization_id=org AND import_id=job AND request_key=request;
      IF FOUND THEN
        IF receipt.actor_user_id<>actor OR receipt.operation<>command THEN
          RAISE EXCEPTION 'Queue command conflict' USING ERRCODE='23514'; END IF;
        RETURN receipt.id;
      END IF;
      IF request IS NULL OR command IS NULL OR NOT (
        (command='START' AND item.status='DRAFT' AND EXISTS(SELECT 1 FROM public.declaration_import_document WHERE organization_id=org AND import_id=job)) OR
        (command='RETRY' AND ((item.status IN ('FAILED','RETRY_WAIT') AND item.error_code IN
          ('PROVIDER_TIMEOUT','PROVIDER_INCOMPLETE','PROVIDER_RATE_LIMIT','PROVIDER_SERVER_ERROR','PROVIDER_CONNECTION_ERROR','OCR_TIMEOUT','WORKER_INTERRUPTED')) OR
          (item.status='CANCELLED' AND item.error_code IN ('JOB_PRINCIPAL_UNVERIFIED','JOB_PERMISSION_CHANGED'))))) THEN
        RAISE EXCEPTION 'Invalid queue command' USING ERRCODE='23514'; END IF;
      INSERT INTO public.declaration_queue_request(organization_id,import_id,actor_user_id,request_key,operation,previous_request_id)
        VALUES(org,job,actor,request,command,item.queue_request_id) RETURNING * INTO receipt;
      UPDATE public.declaration_import SET queue_request_id=receipt.id,status='QUEUED',current_stage='queued',
        next_attempt_at=NULL,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
        lease_generation=lease_generation+1,error_code=NULL,error_message=NULL WHERE organization_id=org AND id=job;
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(org,actor,'declaration_import',job,'declaration.queued',jsonb_build_object('queue_request_id',receipt.id,'operation',command));
      RETURN receipt.id;
    END $$;
"""

def upgrade():
    op.execute("""
    LOCK TABLE organization,"user",declaration_import,declaration_queue_request IN ACCESS EXCLUSIVE MODE NOWAIT;
    CREATE TABLE property_source_security (
      organization_id uuid PRIMARY KEY REFERENCES organization(id),
      mode text NOT NULL CHECK(mode IN ('PREPARATION','ENFORCED','PAUSED')),
      changed_by uuid NOT NULL, reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 2000),
      changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      FOREIGN KEY(organization_id,changed_by) REFERENCES "user"(organization_id,id)
    );
    ALTER TABLE property_source_security ENABLE ROW LEVEL SECURITY;
    ALTER TABLE property_source_security FORCE ROW LEVEL SECURITY;
    CREATE POLICY property_source_security_read ON property_source_security FOR SELECT USING(
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND scope_actor_role(organization_id)='OWNER_ADMIN');
    CREATE POLICY property_source_security_insert ON property_source_security FOR INSERT WITH CHECK(false);
    REVOKE ALL ON property_source_security FROM PUBLIC,app_rw;

    -- Deliberately unavailable until the complete graph and coverage gate exist.
    CREATE FUNCTION property_source_activation_ready(org uuid) RETURNS boolean
    LANGUAGE sql SET search_path=pg_catalog,pg_temp AS $$ SELECT false $$;
    REVOKE ALL ON FUNCTION property_source_activation_ready(uuid) FROM PUBLIC,app_rw;

    CREATE FUNCTION lock_property_source_security_change() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE org uuid:=NULLIF(current_setting('app.current_org_id',true),'')::uuid;
    BEGIN
      IF org IS NULL THEN RAISE EXCEPTION 'Source security organization required'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||org::text,0));
      RETURN NULL;
    END $$;
    CREATE TRIGGER lock_property_source_security_change BEFORE INSERT OR UPDATE OR DELETE ON property_source_security
      FOR EACH STATEMENT EXECUTE FUNCTION lock_property_source_security_change();

    CREATE FUNCTION guard_property_source_security() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Source security evidence cannot be deleted'; END IF;
      IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR NEW.changed_by IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid
        OR public.scope_actor_role(NEW.organization_id) IS DISTINCT FROM 'OWNER_ADMIN' THEN
        RAISE EXCEPTION 'Current source security administrator required' USING ERRCODE='42501'; END IF;
      IF TG_OP='UPDATE' AND (NEW.organization_id IS DISTINCT FROM OLD.organization_id
        OR (OLD.mode IN ('ENFORCED','PAUSED') AND NEW.mode='PREPARATION')) THEN
        RAISE EXCEPTION 'Source security cannot return to compatibility access'; END IF;
      IF NEW.mode IN ('ENFORCED','PAUSED') AND NOT public.property_source_activation_ready(NEW.organization_id)
        AND (TG_OP='INSERT' OR OLD.mode='PREPARATION' OR NEW.mode='ENFORCED') THEN
        RAISE EXCEPTION 'Source integration and coverage are incomplete'; END IF;
      NEW.reason:=btrim(NEW.reason); NEW.changed_at:=clock_timestamp();
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(NEW.organization_id,NEW.changed_by,'property_source_security',NEW.organization_id,'property_source.security_changed',
          jsonb_build_object('mode',NEW.mode,'reason',NEW.reason));
      RETURN NEW;
    END $$;
    CREATE TRIGGER guard_property_source_security BEFORE INSERT OR UPDATE OR DELETE ON property_source_security
      FOR EACH ROW EXECUTE FUNCTION guard_property_source_security();
    REVOKE ALL ON FUNCTION lock_property_source_security_change(),guard_property_source_security() FROM PUBLIC,app_rw;

    CREATE FUNCTION property_source_security_mode(org uuid) RETURNS text
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF org IS NULL OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN NULL; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      RETURN COALESCE((SELECT s.mode FROM public.property_source_security s WHERE s.organization_id=org),'PREPARATION');
    END $$;
    REVOKE ALL ON FUNCTION property_source_security_mode(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_source_security_mode(uuid) TO app_rw;

    CREATE FUNCTION property_source_actor_permitted(org uuid,actor uuid,kind text,resource uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE mode text; role text;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','prepare','approve','export') THEN RETURN false; END IF;
      IF kind IS NULL OR kind NOT IN ('property','unit','property_document','declaration_import','declaration_import_document',
        'declaration_candidate','declaration_candidate_evidence','declaration_candidate_issue','declaration_extraction_run',
        'declaration_queue_request','declaration_import_pass','property_knowledge_query_run','property_legal_reference',
        'property_assignment','approved_fact_source','property_rule','property_rule_source','property_rule_relation','property_document_relation')
        OR resource IS NULL THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      role:=public.property_source_actor_role(org,actor);
      IF role IS NULL OR (capability IN ('prepare','approve') AND role NOT IN ('OWNER_ADMIN','CLERK')) THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      IF mode='PAUSED' AND capability IN ('prepare','approve') THEN RETURN false; END IF;
      RETURN public.property_source_actor_access(org,actor,kind,resource,capability);
    END $$;
    REVOKE ALL ON FUNCTION property_source_actor_permitted(uuid,uuid,text,uuid,text) FROM PUBLIC,app_rw;
    CREATE FUNCTION property_source_permitted(org uuid,kind text,resource uuid,capability text) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT public.property_source_actor_permitted(org,NULLIF(current_setting('app.current_user_id',true),'')::uuid,kind,resource,capability)
    $$;
    REVOKE ALL ON FUNCTION property_source_permitted(uuid,text,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_source_permitted(uuid,text,uuid,text) TO app_rw;

    CREATE OR REPLACE FUNCTION declaration_queue_actor(org uuid,job uuid,request uuid) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE actor uuid;
    BEGIN
      IF org IS NULL OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN NULL; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      SELECT q.actor_user_id INTO actor FROM public.declaration_queue_request q
        WHERE q.organization_id=org AND q.import_id=job AND q.id=request;
      IF actor IS NULL OR NOT public.property_source_actor_permitted(org,actor,'declaration_import',job,'prepare') THEN RETURN NULL; END IF;
      RETURN actor;
    END $$;
    """)
    op.execute(QUEUE_COMMAND)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security) THEN
      RAISE EXCEPTION 'Retain populated source security state'; END IF; END $$""")
    op.execute(PREVIOUS_QUEUE_COMMAND)
    op.execute(PREVIOUS_QUEUE_ACTOR)
    op.execute("""
      DROP TABLE property_source_security;
      DROP FUNCTION guard_property_source_security(),lock_property_source_security_change(),
        property_source_permitted(uuid,text,uuid,text),property_source_actor_permitted(uuid,uuid,text,uuid,text),
        property_source_security_mode(uuid),property_source_activation_ready(uuid);
    """)
