"""Immutable declaration queue commands and live worker principals."""
from alembic import op

revision = '0043'
down_revision = '0042'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    CREATE TABLE declaration_queue_request (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL REFERENCES organization(id),
      import_id uuid NOT NULL, actor_user_id uuid NOT NULL, request_key uuid NOT NULL,
      operation text NOT NULL CHECK(operation IN ('START','RETRY')), previous_request_id uuid,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(organization_id,import_id,id), UNIQUE(organization_id,import_id,request_key),
      UNIQUE NULLS NOT DISTINCT(organization_id,import_id,previous_request_id),
      FOREIGN KEY(organization_id,import_id) REFERENCES declaration_import(organization_id,id),
      FOREIGN KEY(organization_id,actor_user_id) REFERENCES "user"(organization_id,id),
      FOREIGN KEY(organization_id,import_id,previous_request_id)
        REFERENCES declaration_queue_request(organization_id,import_id,id)
    );
    ALTER TABLE declaration_import ADD COLUMN queue_request_id uuid,
      ADD CONSTRAINT fk_declaration_current_queue FOREIGN KEY(organization_id,id,queue_request_id)
        REFERENCES declaration_queue_request(organization_id,import_id,id);
    CREATE TRIGGER protect_declaration_queue_request BEFORE UPDATE OR DELETE ON declaration_queue_request
      FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();
    ALTER TABLE declaration_queue_request ENABLE ROW LEVEL SECURITY;
    ALTER TABLE declaration_queue_request FORCE ROW LEVEL SECURITY;
    CREATE POLICY declaration_queue_read ON declaration_queue_request FOR SELECT
      USING (organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid);
    CREATE POLICY declaration_queue_insert ON declaration_queue_request FOR INSERT WITH CHECK(false);
    REVOKE ALL ON declaration_queue_request FROM PUBLIC,app_rw;
    GRANT SELECT ON declaration_queue_request TO app_rw;

    CREATE FUNCTION declaration_queue_actor(org uuid, job uuid, request uuid) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN NULL; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      RETURN (SELECT q.actor_user_id FROM public.declaration_queue_request q
        JOIN public."user" u ON u.organization_id=q.organization_id AND u.id=q.actor_user_id
        JOIN public.membership m ON m.organization_id=u.organization_id AND m.user_id=u.id
        WHERE q.organization_id=org AND q.import_id=job AND q.id=request
          AND u.is_active AND m.role IN ('OWNER_ADMIN','CLERK'));
    END $$;

    CREATE FUNCTION protect_declaration_queue_pointer() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE receipt public.declaration_queue_request;
    BEGIN
      IF NEW.initiated_by IS DISTINCT FROM OLD.initiated_by THEN
        RAISE EXCEPTION 'Import creator is immutable' USING ERRCODE='23514'; END IF;
      IF NEW.queue_request_id IS DISTINCT FROM OLD.queue_request_id THEN
        SELECT * INTO receipt FROM public.declaration_queue_request
          WHERE organization_id=NEW.organization_id AND import_id=NEW.id AND id=NEW.queue_request_id;
        IF NOT FOUND OR receipt.previous_request_id IS DISTINCT FROM OLD.queue_request_id
          OR receipt.actor_user_id IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid
          OR public.declaration_queue_actor(NEW.organization_id,NEW.id,receipt.id) IS NULL
          OR NEW.status<>'QUEUED'
          OR NOT ((OLD.status='DRAFT' AND receipt.operation='START') OR
            (OLD.status IN ('FAILED','RETRY_WAIT','CANCELLED') AND receipt.operation='RETRY')) THEN
          RAISE EXCEPTION 'Invalid queue principal transition' USING ERRCODE='23514'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER protect_declaration_queue_pointer BEFORE UPDATE ON declaration_import
      FOR EACH ROW EXECUTE FUNCTION protect_declaration_queue_pointer();

    CREATE FUNCTION record_declaration_queue_request(org uuid, job uuid, request uuid, command text) RETURNS uuid
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

    CREATE FUNCTION cancel_unauthorized_declaration(org uuid, job uuid, request uuid, generation bigint, token uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE item public.declaration_import; code text;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      SELECT * INTO item FROM public.declaration_import WHERE organization_id=org AND id=job FOR UPDATE;
      IF NOT FOUND OR item.queue_request_id IS DISTINCT FROM request OR item.lease_generation<>generation
        OR item.lease_token IS DISTINCT FROM token OR item.status NOT IN ('QUEUED','RETRY_WAIT','PREFLIGHT','EXTRACTING','RECONCILING')
        OR public.declaration_queue_actor(org,job,request) IS NOT NULL THEN RETURN false; END IF;
      code:=CASE WHEN request IS NULL THEN 'JOB_PRINCIPAL_UNVERIFIED' ELSE 'JOB_PERMISSION_CHANGED' END;
      UPDATE public.declaration_extraction_run SET status='FAILED',error_code=code,completed_at=clock_timestamp()
        WHERE organization_id=org AND import_id=job AND status='RUNNING';
      UPDATE public.declaration_import SET status='CANCELLED',current_stage=NULL,error_code=code,error_message=code,
        lease_generation=lease_generation+1,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,next_attempt_at=NULL
        WHERE organization_id=org AND id=job;
      INSERT INTO public.audit_event(organization_id,entity_type,entity_id,action,payload_diff)
        VALUES(org,'declaration_import',job,'declaration.permission_cancelled',jsonb_build_object('queue_request_id',request,'reason',code));
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION declaration_queue_actor(uuid,uuid,uuid),protect_declaration_queue_pointer(),
      record_declaration_queue_request(uuid,uuid,uuid,text),cancel_unauthorized_declaration(uuid,uuid,uuid,bigint,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION declaration_queue_actor(uuid,uuid,uuid),record_declaration_queue_request(uuid,uuid,uuid,text),
      cancel_unauthorized_declaration(uuid,uuid,uuid,bigint,uuid) TO app_rw;
    ''')


def downgrade():
    op.execute('''
    DO $$ BEGIN IF EXISTS(SELECT 1 FROM declaration_queue_request) THEN
      RAISE EXCEPTION 'Retain populated declaration queue evidence'; END IF; END $$;
    DROP TRIGGER protect_declaration_queue_pointer ON declaration_import;
    DROP FUNCTION protect_declaration_queue_pointer(),record_declaration_queue_request(uuid,uuid,uuid,text),
      cancel_unauthorized_declaration(uuid,uuid,uuid,bigint,uuid),declaration_queue_actor(uuid,uuid,uuid);
    ALTER TABLE declaration_import DROP COLUMN queue_request_id;
    DROP TABLE declaration_queue_request;
    ''')
