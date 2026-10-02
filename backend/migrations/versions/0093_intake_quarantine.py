"""Durable private intake manifests, fresh-upload custody and inspection work."""
from alembic import op
from sqlalchemy import text

revision='0093'
down_revision='0092'
branch_labels=None
depends_on=None


def registry_adapter(add):
    variants={
      'document_ref_visible(public.document_ref)':("ELSIF ref.source_kind='mandate_document' THEN", "ELSIF ref.source_kind='intake_original' THEN\n        RETURN EXISTS(SELECT 1 FROM public.intake_original WHERE organization_id=ref.organization_id AND id=ref.intake_original_id);\n      ELSIF ref.source_kind='mandate_document' THEN"),
      'validate_document_ref()':("ELSE RAISE EXCEPTION 'Unknown original adapter'", "ELSIF NEW.source_kind='intake_original' THEN\n        SELECT content_sha256,mime_type,content_byte_size INTO digest,media,bytes FROM public.intake_original WHERE organization_id=NEW.organization_id AND id=NEW.intake_original_id;\n      ELSE RAISE EXCEPTION 'Unknown original adapter'"),
      'register_original_document()':('mandate_document_id,content_sha256','mandate_document_id,intake_original_id,content_sha256'),
    }
    for name,(before,after) in variants.items():
        definition=op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:name AS regprocedure))'),{'name':name})
        source,target=(before,after) if add else (after,before)
        assert definition.count(source)==1
        definition=definition.replace(source,target)
        if name=='register_original_document()':
            before="CASE WHEN TG_TABLE_NAME='mandate_document' THEN NEW.id END,"
            after=before+"CASE WHEN TG_TABLE_NAME='intake_original' THEN NEW.id END,"
            source,target=(before,after) if add else (after,before)
            assert definition.count(source)==1
            definition=definition.replace(source,target)
        op.execute(definition)


def upgrade():
    op.execute('''
    CREATE TABLE intake_batch(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),
      initiated_by uuid NOT NULL,scope_hint_id uuid,request_key uuid NOT NULL,request_digest text NOT NULL,
      manifest jsonb NOT NULL CHECK(jsonb_typeof(manifest)='array' AND jsonb_array_length(manifest) BETWEEN 1 AND 100),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(organization_id,id),UNIQUE(organization_id,initiated_by,request_key),
      FOREIGN KEY(organization_id,initiated_by) REFERENCES "user"(organization_id,id),
      FOREIGN KEY(organization_id,scope_hint_id) REFERENCES accounting_scope(organization_id,id));
    CREATE TABLE intake_item(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),batch_id uuid NOT NULL,
      position integer NOT NULL CHECK(position BETWEEN 1 AND 100),client_item_key varchar(128) NOT NULL CHECK(length(client_item_key)>0),
      filename varchar(255) NOT NULL CHECK(length(filename)>0),mime_type text NOT NULL CHECK(mime_type IN ('application/pdf','application/xml','image/png','image/jpeg')),
      content_sha256 text NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),content_byte_size bigint NOT NULL CHECK(content_byte_size BETWEEN 1 AND 26214400),
      UNIQUE(organization_id,id),UNIQUE(organization_id,batch_id,position),UNIQUE(organization_id,batch_id,client_item_key),
      FOREIGN KEY(organization_id,batch_id) REFERENCES intake_batch(organization_id,id));
    CREATE TABLE intake_upload_admission(
      id uuid PRIMARY KEY,organization_id uuid NOT NULL REFERENCES organization(id),initiated_by uuid NOT NULL,
      payload_digest text NOT NULL CHECK(payload_digest ~ '^[0-9a-f]{64}$'),admitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      FOREIGN KEY(organization_id,initiated_by) REFERENCES "user"(organization_id,id));
    CREATE TABLE intake_original(
      id uuid PRIMARY KEY,organization_id uuid NOT NULL REFERENCES organization(id),item_id uuid NOT NULL,uploaded_by uuid NOT NULL,
      content_sha256 varchar(64) NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),content_byte_size bigint NOT NULL CHECK(content_byte_size BETWEEN 1 AND 26214400),
      mime_type varchar(100) NOT NULL,storage_uri text NOT NULL,storage_backend text NOT NULL,storage_version text,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(organization_id,id),UNIQUE(organization_id,item_id),
      FOREIGN KEY(organization_id,item_id) REFERENCES intake_item(organization_id,id),
      FOREIGN KEY(organization_id,uploaded_by) REFERENCES "user"(organization_id,id));
    CREATE TABLE workflow_job(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),original_id uuid NOT NULL,
      kind text NOT NULL DEFAULT 'intake_inspection' CHECK(kind='intake_inspection'),
      status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','clean','rejected','blocked','cancelled')),
      attempt_number integer NOT NULL DEFAULT 0 CHECK(attempt_number BETWEEN 0 AND 3),fence uuid,lease_expires_at timestamptz,error_code text,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(organization_id,id),UNIQUE(organization_id,original_id),FOREIGN KEY(organization_id,original_id) REFERENCES intake_original(organization_id,id));
    CREATE TABLE job_attempt(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),job_id uuid NOT NULL,
      attempt_number integer NOT NULL CHECK(attempt_number BETWEEN 1 AND 3),fence uuid NOT NULL,
      started_at timestamptz NOT NULL DEFAULT clock_timestamp(),finished_at timestamptz,result jsonb,
      UNIQUE(organization_id,id),UNIQUE(organization_id,job_id,attempt_number),UNIQUE(organization_id,job_id,fence),
      FOREIGN KEY(organization_id,job_id) REFERENCES workflow_job(organization_id,id));
    CREATE TABLE outbox_event(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),resource_id uuid NOT NULL,
      event_type text NOT NULL CHECK(event_type IN ('intake.received','intake.inspected','intake.cancelled')),
      effect_key text NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(organization_id,id),UNIQUE(organization_id,effect_key),
      FOREIGN KEY(organization_id,resource_id) REFERENCES intake_original(organization_id,id));
    CREATE INDEX ix_intake_batch_owner ON intake_batch(organization_id,initiated_by,created_at,id);
    CREATE INDEX ix_workflow_claim ON workflow_job(organization_id,status,created_at,id);

    CREATE FUNCTION intake_actor_access(org uuid,actor uuid,sphere uuid,writing boolean) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE role text; selected uuid:=NULLIF(current_setting('app.current_scope_id',true),'')::uuid;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR actor IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid OR actor IS NULL THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      role:=public.scope_actor_role(org);
      IF role IS NULL OR (selected IS NOT NULL AND selected IS DISTINCT FROM sphere) THEN RETURN false; END IF;
      IF writing AND (role NOT IN ('OWNER_ADMIN','CLERK') OR public.property_source_security_mode(org)='PAUSED') THEN RETURN false; END IF;
      RETURN sphere IS NULL OR public.has_scope_capability(org,sphere,CASE WHEN writing THEN 'prepare' ELSE 'read' END);
    END $$;
    CREATE FUNCTION intake_batch_access(org uuid,batch uuid,writing boolean) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE row public.intake_batch;
    BEGIN
      SELECT * INTO row FROM public.intake_batch WHERE organization_id=org AND id=batch;
      IF NOT FOUND THEN RETURN false; END IF;
      RETURN public.intake_actor_access(org,row.initiated_by,row.scope_hint_id,writing);
    END $$;
    CREATE FUNCTION intake_original_access(org uuid,original uuid,clean_only boolean) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE batch uuid;
    BEGIN
      SELECT i.batch_id INTO batch FROM public.intake_original o JOIN public.intake_item i ON i.organization_id=o.organization_id AND i.id=o.item_id
        WHERE o.organization_id=org AND o.id=original;
      IF NOT FOUND OR NOT public.intake_batch_access(org,batch,false) THEN RETURN false; END IF;
      RETURN NOT clean_only OR EXISTS(SELECT 1 FROM public.workflow_job WHERE organization_id=org AND original_id=original AND status='clean');
    END $$;
    CREATE FUNCTION lock_intake(org uuid) RETURNS void
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$ BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid OR org IS NULL THEN
        RAISE EXCEPTION 'Intake organization required' USING ERRCODE='42501'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
    END $$;
    REVOKE ALL ON FUNCTION intake_actor_access(uuid,uuid,uuid,boolean),intake_batch_access(uuid,uuid,boolean),
      intake_original_access(uuid,uuid,boolean),lock_intake(uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION intake_actor_access(uuid,uuid,uuid,boolean),intake_batch_access(uuid,uuid,boolean),intake_original_access(uuid,uuid,boolean) TO app_rw;
    ''')
    for table in ('intake_batch','intake_item','intake_upload_admission','intake_original','workflow_job','job_attempt','outbox_event'):
        op.execute(f'''ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review;
          CREATE POLICY intake_private ON {table} AS RESTRICTIVE FOR ALL USING(false) WITH CHECK(false);''')
        if table not in ('workflow_job','job_attempt'):
            op.execute(f'CREATE TRIGGER protect_intake BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();')
    # Command functions return minimal status; only clean originals get table reads.
    op.execute('''DROP POLICY intake_private ON intake_original;
      CREATE POLICY intake_original_read ON intake_original FOR SELECT USING(intake_original_access(organization_id,id,true));
      CREATE POLICY intake_original_no_insert ON intake_original FOR INSERT WITH CHECK(false);
      GRANT SELECT ON intake_original TO app_rw;

    CREATE FUNCTION create_intake_batch(org uuid,request uuid,sphere uuid,items jsonb) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; row public.intake_batch; digest text; item jsonb; position integer:=0;
    BEGIN
      PERFORM public.lock_intake(org);
      IF NOT public.intake_actor_access(org,actor,sphere,true) THEN RAISE EXCEPTION 'Intake context inaccessible' USING ERRCODE='42501'; END IF;
      IF request IS NULL OR jsonb_typeof(items) IS DISTINCT FROM 'array' OR jsonb_array_length(items) NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'Intake manifest requires 1 to 100 items' USING ERRCODE='22023'; END IF;
      digest:=encode(sha256(convert_to(jsonb_build_object('scope_hint_id',sphere,'items',items)::text,'UTF8')),'hex');
      SELECT * INTO row FROM public.intake_batch WHERE organization_id=org AND initiated_by=actor AND request_key=request;
      IF FOUND THEN
        IF row.request_digest<>digest THEN RAISE EXCEPTION 'Intake manifest replay conflict' USING ERRCODE='23505'; END IF;
      ELSE
        INSERT INTO public.intake_batch(organization_id,initiated_by,scope_hint_id,request_key,request_digest,manifest)
          VALUES(org,actor,sphere,request,digest,items) RETURNING * INTO row;
        FOR item IN SELECT value FROM jsonb_array_elements(items) LOOP
          position:=position+1;
          IF jsonb_typeof(item) IS DISTINCT FROM 'object' OR
            (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(item) key) IS DISTINCT FROM
              ARRAY['client_item_key','content_byte_size','content_sha256','filename','mime_type']::text[] THEN
            RAISE EXCEPTION 'Invalid intake manifest item' USING ERRCODE='22023'; END IF;
          INSERT INTO public.intake_item(organization_id,batch_id,position,client_item_key,filename,mime_type,content_sha256,content_byte_size)
            VALUES(org,row.id,position,item->>'client_item_key',item->>'filename',item->>'mime_type',item->>'content_sha256',(item->>'content_byte_size')::bigint);
        END LOOP;
      END IF;
      RETURN jsonb_build_object('id',row.id,'created_at',row.created_at,'scope_hint_id',row.scope_hint_id,'item_count',jsonb_array_length(row.manifest));
    END $$;

    CREATE FUNCTION admit_intake_upload(org uuid,original uuid,payload jsonb) RETURNS void
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      IF session_user<>'app_review' OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR NOT COALESCE(public.scope_actor_role(org) IN ('OWNER_ADMIN','CLERK'),false)
        OR COALESCE((SELECT mode FROM public.property_source_security WHERE organization_id=org),'PREPARATION')='PAUSED' THEN
        RAISE EXCEPTION 'Trusted current intake runtime required' USING ERRCODE='42501'; END IF;
      -- No ACL wait on this second connection: main command rechecks under lock.
      IF original IS NULL OR jsonb_typeof(payload) IS DISTINCT FROM 'object' OR (payload->>'id')::uuid IS DISTINCT FROM original
        OR (payload->>'organization_id')::uuid IS DISTINCT FROM org OR (payload->>'uploaded_by')::uuid IS DISTINCT FROM actor THEN
        RAISE EXCEPTION 'Intake admission mismatch' USING ERRCODE='22023'; END IF;
      INSERT INTO public.intake_upload_admission(id,organization_id,initiated_by,payload_digest)
        VALUES(original,org,actor,encode(sha256(convert_to(payload::text,'UTF8')),'hex'));
    END $$;

    CREATE FUNCTION receive_intake_original(org uuid,batch uuid,item_key text,payload jsonb) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE item public.intake_item; original public.intake_original; actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; job uuid;
    BEGIN
      PERFORM public.lock_intake(org);
      IF NOT public.intake_batch_access(org,batch,true) THEN RAISE EXCEPTION 'Intake context inaccessible' USING ERRCODE='42501'; END IF;
      SELECT * INTO item FROM public.intake_item WHERE organization_id=org AND batch_id=batch AND client_item_key=item_key;
      IF NOT FOUND THEN RAISE EXCEPTION 'Intake item inaccessible' USING ERRCODE='42501'; END IF;
      IF (payload->>'organization_id')::uuid IS DISTINCT FROM org OR (payload->>'uploaded_by')::uuid IS DISTINCT FROM actor
        OR (payload->>'item_id')::uuid IS DISTINCT FROM item.id OR (payload->>'content_sha256') IS DISTINCT FROM item.content_sha256
        OR (payload->>'content_byte_size')::bigint IS DISTINCT FROM item.content_byte_size OR (payload->>'mime_type') IS DISTINCT FROM item.mime_type
        OR (payload->>'storage_uri') IS DISTINCT FROM org::text||'/'||left(item.content_sha256,2)||'/'||item.content_sha256||'.bin' THEN
        RAISE EXCEPTION 'Intake bytes differ from manifest' USING ERRCODE='22023'; END IF;
      SELECT * INTO original FROM public.intake_original WHERE organization_id=org AND item_id=item.id;
      IF NOT FOUND THEN
        IF NOT EXISTS(SELECT 1 FROM public.intake_upload_admission a WHERE a.organization_id=org AND a.id=(payload->>'id')::uuid AND a.initiated_by=actor
          AND a.payload_digest=encode(sha256(convert_to(payload::text,'UTF8')),'hex')) THEN
          RAISE EXCEPTION 'Trusted fresh intake upload required' USING ERRCODE='42501'; END IF;
        INSERT INTO public.intake_original(id,organization_id,item_id,uploaded_by,content_sha256,content_byte_size,mime_type,storage_uri,storage_backend,storage_version)
          VALUES((payload->>'id')::uuid,org,item.id,actor,item.content_sha256,item.content_byte_size,item.mime_type,payload->>'storage_uri',payload->>'storage_backend',payload->>'storage_version') RETURNING * INTO original;
        INSERT INTO public.workflow_job(organization_id,original_id) VALUES(org,original.id) RETURNING id INTO job;
        INSERT INTO public.outbox_event(organization_id,resource_id,event_type,effect_key) VALUES(org,original.id,'intake.received','received:'||original.id::text);
      ELSE
        SELECT id INTO job FROM public.workflow_job WHERE organization_id=org AND original_id=original.id;
      END IF;
      RETURN jsonb_build_object('original_id',original.id,'job_id',job,'status','received','received_at',original.created_at);
    END $$;
    REVOKE ALL ON FUNCTION create_intake_batch(uuid,uuid,uuid,jsonb),admit_intake_upload(uuid,uuid,jsonb),receive_intake_original(uuid,uuid,text,jsonb) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION create_intake_batch(uuid,uuid,uuid,jsonb),receive_intake_original(uuid,uuid,text,jsonb) TO app_rw;
    GRANT EXECUTE ON FUNCTION admit_intake_upload(uuid,uuid,jsonb) TO app_review;
    ''')

    op.execute('''
    CREATE FUNCTION intake_upload_target(org uuid,batch uuid,item_key text) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE item public.intake_item; original public.intake_original; job uuid;
    BEGIN
      PERFORM public.lock_intake(org);
      IF NOT public.intake_batch_access(org,batch,true) THEN RAISE EXCEPTION 'Intake context inaccessible' USING ERRCODE='42501'; END IF;
      SELECT * INTO item FROM public.intake_item WHERE organization_id=org AND batch_id=batch AND client_item_key=item_key;
      IF NOT FOUND THEN RAISE EXCEPTION 'Intake item inaccessible' USING ERRCODE='42501'; END IF;
      SELECT * INTO original FROM public.intake_original WHERE organization_id=org AND item_id=item.id;
      SELECT id INTO job FROM public.workflow_job WHERE organization_id=org AND original_id=original.id;
      RETURN (to_jsonb(item)-ARRAY['organization_id','batch_id'])||jsonb_build_object('receipt',CASE WHEN original.id IS NOT NULL THEN
        jsonb_build_object('original_id',original.id,'job_id',job,'status','received','received_at',original.created_at) END);
    END $$;
    CREATE FUNCTION intake_batch_status(org uuid,batch uuid,after_position integer,take integer) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE row public.intake_batch; entries jsonb; counts jsonb; version text;
    BEGIN
      IF NOT public.intake_batch_access(org,batch,false) THEN RAISE EXCEPTION 'Intake context inaccessible' USING ERRCODE='42501'; END IF;
      IF after_position IS NULL OR after_position NOT BETWEEN 0 AND 100 OR take IS NULL OR take NOT BETWEEN 1 AND 200 THEN
        RAISE EXCEPTION 'Invalid intake page' USING ERRCODE='22023'; END IF;
      SELECT * INTO row FROM public.intake_batch WHERE organization_id=org AND id=batch;
      SELECT jsonb_agg(jsonb_build_object('id',i.id,'position',i.position,'client_item_key',i.client_item_key,'filename',i.filename,
        'mime_type',i.mime_type,'content_sha256',i.content_sha256,'content_byte_size',i.content_byte_size,
        'original_id',o.id,'job_id',j.id,'fence',j.fence,'error_code',j.error_code,'status',CASE WHEN o.id IS NULL THEN 'waiting'
          WHEN j.status='queued' THEN 'received' WHEN j.status='running' THEN 'processing' WHEN j.status='clean' THEN 'review' ELSE 'failed' END)
        ORDER BY i.position) INTO entries FROM public.intake_item i
        LEFT JOIN public.intake_original o ON o.organization_id=i.organization_id AND o.item_id=i.id
        LEFT JOIN public.workflow_job j ON j.organization_id=o.organization_id AND j.original_id=o.id WHERE i.organization_id=org AND i.batch_id=batch;
      SELECT jsonb_build_object('waiting',count(*) FILTER(WHERE e->>'status'='waiting'),'received',count(*) FILTER(WHERE e->>'status'='received'),
        'processing',count(*) FILTER(WHERE e->>'status'='processing'),'review',count(*) FILTER(WHERE e->>'status'='review'),
        'confirmed',0,'failed',count(*) FILTER(WHERE e->>'status'='failed')) INTO counts FROM jsonb_array_elements(entries) e;
      version:=encode(sha256(convert_to(entries::text,'UTF8')),'hex');
      RETURN jsonb_build_object('id',row.id,'created_at',row.created_at,'scope_hint_id',row.scope_hint_id,'item_count',jsonb_array_length(entries),
        'counts',counts,'revision',version,'items',(SELECT COALESCE(jsonb_agg(e ORDER BY (e->>'position')::integer),'[]') FROM
          (SELECT e FROM jsonb_array_elements(entries) e WHERE (e->>'position')::integer>after_position ORDER BY (e->>'position')::integer LIMIT take+1) page));
    END $$;
    REVOKE ALL ON FUNCTION intake_upload_target(uuid,uuid,text),intake_batch_status(uuid,uuid,integer,integer) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION intake_upload_target(uuid,uuid,text),intake_batch_status(uuid,uuid,integer,integer) TO app_rw;
    ''')

    op.execute('''
    CREATE FUNCTION intake_worker_context(org uuid,job uuid,token uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE work public.workflow_job; original public.intake_original; batch public.intake_batch;
    BEGIN
      IF session_user<>'app_worker' THEN RAISE EXCEPTION 'Workflow runtime required' USING ERRCODE='42501'; END IF;
      PERFORM public.lock_intake(org);
      SELECT * INTO work FROM public.workflow_job WHERE organization_id=org AND id=job FOR UPDATE;
      IF NOT FOUND OR work.status<>'running' OR work.fence IS DISTINCT FROM token OR work.lease_expires_at<=clock_timestamp() THEN RETURN NULL; END IF;
      SELECT * INTO original FROM public.intake_original WHERE organization_id=org AND id=work.original_id;
      SELECT b.* INTO batch FROM public.intake_batch b JOIN public.intake_item i ON i.organization_id=b.organization_id AND i.batch_id=b.id
        WHERE i.organization_id=org AND i.id=original.item_id;
      PERFORM set_config('app.current_user_id',batch.initiated_by::text,true);
      PERFORM set_config('app.current_scope_id',COALESCE(batch.scope_hint_id::text,''),true);
      IF NOT public.intake_actor_access(org,batch.initiated_by,batch.scope_hint_id,true) THEN
        UPDATE public.workflow_job SET status='cancelled',error_code='initiator_permission_changed',lease_expires_at=NULL,updated_at=clock_timestamp() WHERE id=job;
        UPDATE public.job_attempt SET finished_at=clock_timestamp(),result=jsonb_build_object('status','cancelled','error_code','initiator_permission_changed') WHERE job_id=job AND fence=token AND finished_at IS NULL;
        INSERT INTO public.outbox_event(organization_id,resource_id,event_type,effect_key) VALUES(org,original.id,'intake.cancelled','cancelled:'||job::text||':'||token::text);
        RETURN NULL;
      END IF;
      RETURN jsonb_build_object('job_id',job,'fence',token,'original_id',original.id,'storage_uri',original.storage_uri,
        'content_sha256',original.content_sha256,'content_byte_size',original.content_byte_size,'mime_type',original.mime_type);
    END $$;
    CREATE FUNCTION claim_intake_inspection(org uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE work public.workflow_job; token uuid:=gen_random_uuid();
    BEGIN
      IF session_user<>'app_worker' THEN RAISE EXCEPTION 'Workflow runtime required' USING ERRCODE='42501'; END IF;
      PERFORM public.lock_intake(org);
      SELECT * INTO work FROM public.workflow_job WHERE organization_id=org AND
        (status='queued' OR status='running' AND lease_expires_at<=clock_timestamp()) ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED;
      IF NOT FOUND THEN RETURN NULL; END IF;
      IF work.status='running' THEN
        UPDATE public.job_attempt SET finished_at=clock_timestamp(),result=jsonb_build_object('status','blocked','error_code','lease_expired') WHERE job_id=work.id AND fence=work.fence AND finished_at IS NULL;
      END IF;
      IF work.attempt_number>=3 THEN
        UPDATE public.workflow_job SET status='blocked',error_code='attempt_limit',lease_expires_at=NULL,updated_at=clock_timestamp() WHERE id=work.id;
        RETURN NULL;
      END IF;
      UPDATE public.workflow_job SET status='running',attempt_number=attempt_number+1,fence=token,lease_expires_at=clock_timestamp()+interval '120 seconds',error_code=NULL,updated_at=clock_timestamp() WHERE id=work.id;
      INSERT INTO public.job_attempt(organization_id,job_id,attempt_number,fence) VALUES(org,work.id,work.attempt_number+1,token);
      RETURN public.intake_worker_context(org,work.id,token);
    END $$;
    CREATE FUNCTION complete_intake_inspection(org uuid,job uuid,token uuid,inspection jsonb) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE context jsonb; state text:=inspection->>'status'; pages integer;
    BEGIN
      context:=public.intake_worker_context(org,job,token);
      IF context IS NULL THEN RETURN false; END IF;
      IF jsonb_typeof(inspection) IS DISTINCT FROM 'object' OR
        (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(inspection) key) IS DISTINCT FROM
          ARRAY['content_byte_size','content_sha256','error_code','inspector_version','mime_type','page_count','status']::text[]
        OR jsonb_typeof(inspection->'content_byte_size') IS DISTINCT FROM 'number' OR state IS NULL OR state NOT IN ('clean','blocked','rejected')
        OR inspection->>'content_sha256' IS DISTINCT FROM context->>'content_sha256'
        OR inspection->>'content_byte_size' IS DISTINCT FROM context->>'content_byte_size'
        OR inspection->>'inspector_version' IS DISTINCT FROM 'intake-inspection-v1' THEN
        RAISE EXCEPTION 'Inspection identity mismatch' USING ERRCODE='22023'; END IF;
      IF state='clean' THEN
        IF inspection->>'error_code' IS NOT NULL OR inspection->>'mime_type' IS DISTINCT FROM context->>'mime_type' THEN
          RAISE EXCEPTION 'Incomplete clean inspection' USING ERRCODE='22023'; END IF;
        pages:=(inspection->>'page_count')::integer;
        IF (context->>'mime_type'='application/xml' AND pages IS NOT NULL) OR
          (context->>'mime_type'<>'application/xml' AND (pages IS NULL OR pages NOT BETWEEN 1 AND CASE WHEN context->>'mime_type'='application/pdf' THEN 200 ELSE 1 END)) THEN
          RAISE EXCEPTION 'Invalid verified page count' USING ERRCODE='22023'; END IF;
      ELSIF inspection->>'page_count' IS NOT NULL OR inspection->>'error_code' IS NULL OR
        (state='blocked' AND inspection->>'error_code' NOT IN ('scanner_failed','scanner_timeout','scanner_unavailable','parser_unavailable','inspector_failed','inspection_timeout','storage_integrity_mismatch','stored_original_unavailable')) OR
        (state='rejected' AND inspection->>'error_code' NOT IN ('empty_file','file_size_limit','mime_mismatch','scanner_detected','encrypted_pdf','pdf_page_limit','image_pixel_limit','unsupported_image_frames','xml_expansion_limit','xml_declaration_forbidden','invalid_document')) THEN
        RAISE EXCEPTION 'Invalid blocked inspection' USING ERRCODE='22023'; END IF;
      UPDATE public.job_attempt SET finished_at=clock_timestamp(),result=inspection WHERE organization_id=org AND job_id=job AND fence=token;
      UPDATE public.workflow_job SET status=state,error_code=inspection->>'error_code',lease_expires_at=NULL,updated_at=clock_timestamp() WHERE organization_id=org AND id=job;
      INSERT INTO public.outbox_event(organization_id,resource_id,event_type,effect_key) VALUES(org,(context->>'original_id')::uuid,'intake.inspected','inspected:'||job::text||':'||token::text);
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION intake_worker_context(uuid,uuid,uuid),claim_intake_inspection(uuid),complete_intake_inspection(uuid,uuid,uuid,jsonb) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION intake_worker_context(uuid,uuid,uuid),claim_intake_inspection(uuid),complete_intake_inspection(uuid,uuid,uuid,jsonb) TO app_worker;
    ''')

    op.execute('''LOCK TABLE document_ref IN ACCESS EXCLUSIVE MODE NOWAIT;
      ALTER TABLE document_ref DROP CONSTRAINT document_ref_check;
      ALTER TABLE document_ref ADD COLUMN intake_original_id uuid,
        ADD UNIQUE(organization_id,intake_original_id),
        ADD FOREIGN KEY(organization_id,intake_original_id) REFERENCES intake_original(organization_id,id) ON DELETE RESTRICT,
        ADD CONSTRAINT document_ref_check CHECK(
          (source_kind='invoice_document' AND invoice_document_id IS NOT NULL AND property_document_id IS NULL AND mandate_document_id IS NULL AND intake_original_id IS NULL) OR
          (source_kind='property_document' AND property_document_id IS NOT NULL AND invoice_document_id IS NULL AND mandate_document_id IS NULL AND intake_original_id IS NULL) OR
          (source_kind='mandate_document' AND mandate_document_id IS NOT NULL AND invoice_document_id IS NULL AND property_document_id IS NULL AND intake_original_id IS NULL) OR
          (source_kind='intake_original' AND intake_original_id IS NOT NULL AND invoice_document_id IS NULL AND property_document_id IS NULL AND mandate_document_id IS NULL));
      CREATE TRIGGER register_original_document AFTER INSERT ON intake_original FOR EACH ROW EXECUTE FUNCTION register_original_document();''')
    registry_adapter(True)

    for name,signature in [('access_coverage_manifest','uuid,jsonb'),('access_disposition_work','uuid'),('access_item_disposition','jsonb')]:
        definition=op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:name AS regprocedure))'),{'name':f'{name}({signature})'})
        op.execute(definition.replace(f'FUNCTION public.{name}(',f'FUNCTION public.{name}_pre_intake(',1))
        op.execute(f'REVOKE ALL ON FUNCTION {name}_pre_intake({signature}) FROM PUBLIC,app_rw,app_worker,app_review')
    op.execute('''
    CREATE OR REPLACE FUNCTION access_coverage_manifest(org uuid,policy jsonb) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE data jsonb; dependency jsonb; original record; prior_actor text:=current_setting('app.current_user_id',true); readable boolean; exportable boolean; item jsonb;
    BEGIN
      data:=public.access_coverage_manifest_pre_intake(org,policy);
      SELECT jsonb_build_object('kind','intake_quarantine_v1','digest',encode(sha256(convert_to(COALESCE(string_agg(value::text,'' ORDER BY kind,id),''),'UTF8')),'hex')) INTO dependency FROM (
        SELECT 'batch' kind,id,to_jsonb(r) value FROM public.intake_batch r WHERE organization_id=org
        UNION ALL SELECT 'item',id,to_jsonb(r) FROM public.intake_item r WHERE organization_id=org
        UNION ALL SELECT 'original',id,to_jsonb(r) FROM public.intake_original r WHERE organization_id=org
        UNION ALL SELECT 'job',id,to_jsonb(r) FROM public.workflow_job r WHERE organization_id=org
        UNION ALL SELECT 'attempt',id,to_jsonb(r) FROM public.job_attempt r WHERE organization_id=org) evidence;
      data:=jsonb_set(data,'{dependencies}',(data->'dependencies')||jsonb_build_array(dependency));
      FOR original IN SELECT o.id,o.uploaded_by,b.scope_hint_id,j.status FROM public.intake_original o
        JOIN public.intake_item i ON i.organization_id=o.organization_id AND i.id=o.item_id
        JOIN public.intake_batch b ON b.organization_id=i.organization_id AND b.id=i.batch_id
        JOIN public.workflow_job j ON j.organization_id=o.organization_id AND j.original_id=o.id WHERE o.organization_id=org LOOP
        PERFORM set_config('app.current_user_id',original.uploaded_by::text,true);
        readable:=public.intake_original_access(org,original.id,true);
        exportable:=public.intake_original_export_access(org,original.id);
        PERFORM set_config('app.current_user_id',COALESCE(prior_actor,''),true);
        item:=jsonb_build_object('kind','intake_original','resource_id',original.id,'audience','[]'::jsonb,
          'references',jsonb_build_object('uploaded_by',original.uploaded_by,'scope_hint_id',original.scope_hint_id,'inspection_status',original.status,
            'registry_readable',readable,'strict_audience',CASE WHEN readable THEN jsonb_build_array(jsonb_build_object('user_id',original.uploaded_by,'capabilities',CASE WHEN exportable THEN jsonb_build_array('read','export') ELSE jsonb_build_array('read') END)) ELSE '[]'::jsonb END),
          'findings',CASE WHEN readable THEN '[]'::jsonb ELSE jsonb_build_array(CASE WHEN original.status='clean' THEN 'INTAKE_CUSTODY_UNAVAILABLE' ELSE 'INTAKE_QUARANTINED' END) END);
        data:=jsonb_set(data,'{items}',(data->'items')||jsonb_build_array(item));
      END LOOP;
      data:=jsonb_set(data,'{items}',(SELECT COALESCE(jsonb_agg(value ORDER BY value->>'kind' COLLATE "C",(value->>'resource_id')::uuid),'[]') FROM jsonb_array_elements(data->'items')));
      RETURN data;
    EXCEPTION WHEN OTHERS THEN
      PERFORM set_config('app.current_user_id',COALESCE(prior_actor,''),true);
      RAISE;
    END $$;
    REVOKE ALL ON FUNCTION access_coverage_manifest(uuid,jsonb) FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE OR REPLACE FUNCTION access_disposition_work(org uuid) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT public.access_disposition_work_pre_intake(org)||COALESCE(jsonb_agg(jsonb_build_object('kind','workflow_job','resource_id',id) ORDER BY id),'[]')
        FROM public.workflow_job WHERE organization_id=org AND (status IN ('queued','running') OR lease_expires_at>clock_timestamp());
    $$;
    REVOKE ALL ON FUNCTION access_disposition_work(uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE OR REPLACE FUNCTION access_item_disposition(item jsonb) RETURNS text
    LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF item->>'kind'='intake_original' AND item#>>'{references,registry_readable}'='false'
        AND item#>'{references,strict_audience}'='[]'::jsonb AND (
          (item#>>'{references,inspection_status}' IN ('blocked','rejected','cancelled') AND item->'findings'='["INTAKE_QUARANTINED"]'::jsonb) OR
          (item#>>'{references,inspection_status}'='clean' AND item->'findings'='["INTAKE_CUSTODY_UNAVAILABLE"]'::jsonb)) THEN
        RETURN 'RETAIN_BLOCKED'; END IF;
      RETURN public.access_item_disposition_pre_intake(item);
    END $$;
    REVOKE ALL ON FUNCTION access_item_disposition(jsonb) FROM PUBLIC,app_rw,app_worker,app_review;
    ''')

    op.execute('''
    CREATE TABLE intake_retry_receipt(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),job_id uuid NOT NULL,
      actor_id uuid NOT NULL,request_key uuid NOT NULL,prior_fence uuid NOT NULL,reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 1000),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(organization_id,actor_id,request_key),
      FOREIGN KEY(organization_id,job_id) REFERENCES workflow_job(organization_id,id),FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
    ALTER TABLE intake_retry_receipt ENABLE ROW LEVEL SECURITY; ALTER TABLE intake_retry_receipt FORCE ROW LEVEL SECURITY;
    CREATE POLICY intake_private ON intake_retry_receipt AS RESTRICTIVE FOR ALL USING(false) WITH CHECK(false);
    REVOKE ALL ON intake_retry_receipt FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER protect_intake BEFORE UPDATE OR DELETE ON intake_retry_receipt FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();
    CREATE FUNCTION retry_intake_inspection(org uuid,job uuid,request uuid,expected_fence uuid,explanation text) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE work public.workflow_job; receipt public.intake_retry_receipt; batch uuid; actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      PERFORM public.lock_intake(org);
      SELECT * INTO work FROM public.workflow_job WHERE organization_id=org AND id=job FOR UPDATE;
      SELECT i.batch_id INTO batch FROM public.intake_original o JOIN public.intake_item i ON i.organization_id=o.organization_id AND i.id=o.item_id
        WHERE o.organization_id=org AND o.id=work.original_id;
      IF batch IS NULL OR NOT public.intake_batch_access(org,batch,true) THEN RAISE EXCEPTION 'Intake context inaccessible' USING ERRCODE='42501'; END IF;
      explanation:=btrim(explanation);
      IF request IS NULL OR expected_fence IS NULL OR explanation IS NULL OR length(explanation) NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'Explicit retry reason and generation required' USING ERRCODE='22023'; END IF;
      SELECT * INTO receipt FROM public.intake_retry_receipt WHERE organization_id=org AND actor_id=actor AND request_key=request;
      IF FOUND THEN
        IF (receipt.job_id,receipt.prior_fence,receipt.reason) IS DISTINCT FROM (job,expected_fence,explanation) THEN
          RAISE EXCEPTION 'Retry replay conflict' USING ERRCODE='23505'; END IF;
      ELSE
        IF work.status NOT IN ('blocked','cancelled') OR work.fence IS DISTINCT FROM expected_fence OR work.attempt_number>=3 THEN
          RAISE EXCEPTION 'Intake retry unavailable or stale' USING ERRCODE='23505'; END IF;
        INSERT INTO public.intake_retry_receipt(organization_id,job_id,actor_id,request_key,prior_fence,reason)
          VALUES(org,job,actor,request,expected_fence,explanation) RETURNING * INTO receipt;
        UPDATE public.workflow_job SET status='queued',error_code=NULL,lease_expires_at=NULL,updated_at=clock_timestamp() WHERE id=job;
      END IF;
      RETURN jsonb_build_object('id',receipt.id,'job_id',receipt.job_id,'status','queued','created_at',receipt.created_at);
    END $$;
    REVOKE ALL ON FUNCTION retry_intake_inspection(uuid,uuid,uuid,uuid,text) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION retry_intake_inspection(uuid,uuid,uuid,uuid,text) TO app_rw;
    ''')

    op.execute('''
    CREATE FUNCTION intake_original_export_access(org uuid,original uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE sphere uuid;
    BEGIN
      IF NOT public.intake_original_access(org,original,true) THEN RETURN false; END IF;
      SELECT b.scope_hint_id INTO sphere FROM public.intake_original o JOIN public.intake_item i ON i.organization_id=o.organization_id AND i.id=o.item_id
        JOIN public.intake_batch b ON b.organization_id=i.organization_id AND b.id=i.batch_id WHERE o.organization_id=org AND o.id=original;
      RETURN sphere IS NULL OR public.has_scope_capability(org,sphere,'export');
    END $$;
    REVOKE ALL ON FUNCTION intake_original_export_access(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION intake_original_export_access(uuid,uuid) TO app_rw;
    CREATE FUNCTION intake_export_original(org uuid,original uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE source public.intake_original; sphere uuid; filename text;
    BEGIN
      IF NOT public.intake_original_export_access(org,original) THEN RAISE EXCEPTION 'Intake original inaccessible' USING ERRCODE='42501'; END IF;
      SELECT * INTO source FROM public.intake_original WHERE organization_id=org AND id=original;
      SELECT b.scope_hint_id,i.filename INTO sphere,filename FROM public.intake_item i JOIN public.intake_batch b
        ON b.organization_id=i.organization_id AND b.id=i.batch_id WHERE i.organization_id=org AND i.id=source.item_id;
      RETURN jsonb_build_object('id',source.id,'storage_uri',source.storage_uri,'content_sha256',source.content_sha256,
        'content_byte_size',source.content_byte_size,'mime_type',source.mime_type,'filename',filename);
    END $$;
    REVOKE ALL ON FUNCTION intake_export_original(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION intake_export_original(uuid,uuid) TO app_rw;
    ''')


def downgrade():
    op.execute('''LOCK TABLE intake_batch,intake_item,intake_original,intake_upload_admission,workflow_job,job_attempt,outbox_event IN ACCESS EXCLUSIVE MODE NOWAIT;
      DO $$ BEGIN IF EXISTS(SELECT 1 FROM intake_batch) OR EXISTS(SELECT 1 FROM intake_upload_admission) THEN
        RAISE EXCEPTION 'Retain intake originals and work; repair forward'; END IF; END $$;
''')
    for name,signature in [('access_coverage_manifest','uuid,jsonb'),('access_disposition_work','uuid'),('access_item_disposition','jsonb')]:
        definition=op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:name AS regprocedure))'),{'name':f'{name}_pre_intake({signature})'})
        op.execute(definition.replace(f'FUNCTION public.{name}_pre_intake(',f'FUNCTION public.{name}(',1))
        op.execute(f'DROP FUNCTION {name}_pre_intake({signature})')
    op.execute('''
      DROP FUNCTION intake_export_original(uuid,uuid),intake_original_export_access(uuid,uuid);
      DROP FUNCTION retry_intake_inspection(uuid,uuid,uuid,uuid,text);
      DROP TABLE intake_retry_receipt;
      DROP FUNCTION intake_worker_context(uuid,uuid,uuid),claim_intake_inspection(uuid),complete_intake_inspection(uuid,uuid,uuid,jsonb);
      DROP FUNCTION intake_upload_target(uuid,uuid,text),intake_batch_status(uuid,uuid,integer,integer);
      DROP FUNCTION create_intake_batch(uuid,uuid,uuid,jsonb),admit_intake_upload(uuid,uuid,jsonb),receive_intake_original(uuid,uuid,text,jsonb);
''')
    registry_adapter(False)
    op.execute('''ALTER TABLE document_ref DROP CONSTRAINT document_ref_check;
      ALTER TABLE document_ref DROP COLUMN intake_original_id;
      ALTER TABLE document_ref ADD CONSTRAINT document_ref_check CHECK(
        (source_kind='invoice_document' AND invoice_document_id IS NOT NULL AND property_document_id IS NULL AND mandate_document_id IS NULL) OR
        (source_kind='property_document' AND property_document_id IS NOT NULL AND invoice_document_id IS NULL AND mandate_document_id IS NULL) OR
        (source_kind='mandate_document' AND mandate_document_id IS NOT NULL AND invoice_document_id IS NULL AND property_document_id IS NULL));
      DROP TABLE outbox_event,job_attempt,workflow_job,intake_original,intake_upload_admission,intake_item,intake_batch;
      DROP FUNCTION intake_original_access(uuid,uuid,boolean),intake_batch_access(uuid,uuid,boolean),intake_actor_access(uuid,uuid,uuid,boolean),lock_intake(uuid);''')
