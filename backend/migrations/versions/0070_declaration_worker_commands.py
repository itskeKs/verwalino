"""Fence strict declaration lifecycle effects behind typed database commands."""
from alembic import op

revision='0070'
down_revision='0069'
branch_labels=None
depends_on=None
TABLES=('declaration_import','declaration_extraction_run','declaration_queue_request','property_source_security',
        'property_document','declaration_import_document','property_data_domain','property_data_grant','"user"','membership')
FIELDS=('status','current_stage','progress_percent','input_tokens','output_tokens','estimated_cost_usd','attempt_count',
        'next_attempt_at','error_code','error_message','lease_generation','lease_owner','lease_token','lease_expires_at','max_cost_usd','draft_revision')


def upgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN
      IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='app_worker' AND rolcanlogin AND rolinherit
        AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication)
        OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='app_rw' AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication)
        OR EXISTS(SELECT 1 FROM pg_auth_members WHERE roleid=(SELECT oid FROM pg_roles WHERE rolname='app_worker'))
        OR EXISTS(WITH RECURSIVE inherited(roleid) AS (
          SELECT roleid FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='app_worker')
          UNION SELECT m.roleid FROM pg_auth_members m JOIN inherited p ON m.member=p.roleid)
          SELECT 1 FROM inherited WHERE roleid<>(SELECT oid FROM pg_roles WHERE rolname='app_rw'))
      THEN RAISE EXCEPTION 'Provision isolated app_worker role before migration'; END IF;
      END $$;
      GRANT app_rw TO app_worker WITH INHERIT TRUE;
      DO $$ DECLARE definition text; BEGIN
        SELECT pg_get_functiondef('protect_person_source()'::regprocedure) INTO definition;
        EXECUTE replace(definition, 'current_user=''app_rw''', 'current_user IN (''app_rw'',''app_worker'')');
      END $$;""")
    op.execute("""
    CREATE FUNCTION guard_declaration_worker_state() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE before_state jsonb; after_state jsonb; field text;
    BEGIN
      IF current_user NOT IN ('app_rw','app_worker') OR public.property_source_security_mode(NEW.organization_id)='PREPARATION' THEN RETURN NEW; END IF;
      IF TG_OP='INSERT' THEN
        IF NEW.status<>'DRAFT' OR NEW.progress_percent<>0 OR NEW.current_stage IS NOT NULL
          OR NEW.input_tokens<>0 OR NEW.output_tokens<>0 OR NEW.estimated_cost_usd<>0 OR NEW.attempt_count<>0
          OR NEW.next_attempt_at IS NOT NULL OR NEW.error_code IS NOT NULL OR NEW.error_message IS NOT NULL
          OR NEW.lease_generation<>0 OR NEW.lease_owner IS NOT NULL OR NEW.lease_token IS NOT NULL
          OR NEW.lease_expires_at IS NOT NULL OR NEW.queue_request_id IS NOT NULL OR NEW.draft_revision<>1 THEN
          RAISE EXCEPTION 'Worker lifecycle command required' USING ERRCODE='42501'; END IF;
        RETURN NEW;
      END IF;
      before_state:=to_jsonb(OLD); after_state:=to_jsonb(NEW);
      -- Publication is a separate approval path, guarded by the existing exact
      -- audience/current-approval triggers and immutable publication receipt.
      IF OLD.status='NEEDS_REVIEW' AND NEW.status='APPLIED' AND NEW.current_stage='applied'
        AND OLD.lease_token IS NULL THEN
        after_state:=jsonb_set(jsonb_set(after_state,'{status}',before_state->'status'),'{current_stage}',before_state->'current_stage');
      END IF;
      IF OLD.status='NEEDS_REVIEW' AND NEW.status='NEEDS_REVIEW' AND NEW.draft_revision=OLD.draft_revision+1 THEN
        after_state:=jsonb_set(after_state,'{draft_revision}',before_state->'draft_revision');
      END IF;
      FOREACH field IN ARRAY TG_ARGV LOOP
        IF before_state->field IS DISTINCT FROM after_state->field THEN
          RAISE EXCEPTION 'Worker lifecycle command required' USING ERRCODE='42501'; END IF;
      END LOOP;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION guard_declaration_worker_state() FROM PUBLIC,app_rw;
    """)
    op.execute('CREATE TRIGGER guard_declaration_worker_state BEFORE INSERT OR UPDATE ON declaration_import FOR EACH ROW EXECUTE FUNCTION guard_declaration_worker_state('+','.join("'"+f+"'" for f in FIELDS)+')')
    op.execute("""
    CREATE FUNCTION claim_declaration_work(org uuid,worker text,seconds integer)
      RETURNS TABLE(import_id uuid,queue_request_id uuid,lease_generation bigint,lease_token uuid,lease_expires_at timestamptz)
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE candidate record; item public.declaration_import; actor uuid; instant timestamptz;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN; END IF;
      IF worker IS NULL OR length(btrim(worker))=0 OR length(worker)>120 OR seconds IS NULL OR seconds<30 OR seconds>3600 THEN
        RAISE EXCEPTION 'Invalid worker lease parameters' USING ERRCODE='22023'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      SELECT * INTO candidate FROM public.next_declaration_queue_metadata(org,clock_timestamp());
      IF NOT FOUND THEN RETURN; END IF;
      actor:=public.declaration_queue_actor(org,candidate.import_id,candidate.queue_request_id);
      IF actor IS NULL THEN
        PERFORM public.cancel_unauthorized_declaration(org,candidate.import_id,candidate.queue_request_id,candidate.lease_generation,candidate.lease_token);
        RETURN;
      END IF;
      PERFORM set_config('app.current_user_id',actor::text,true);
      SELECT * INTO item FROM public.declaration_import i WHERE i.organization_id=org AND i.id=candidate.import_id;
      instant:=clock_timestamp();
      IF item.status IN ('PREFLIGHT','EXTRACTING','RECONCILING') THEN
        UPDATE public.declaration_extraction_run SET status='FAILED',error_code='WORKER_LEASE_EXPIRED',completed_at=instant
          WHERE organization_id=org AND declaration_extraction_run.import_id=item.id AND status='RUNNING';
      END IF;
      RETURN QUERY UPDATE public.declaration_import i SET
        status=CASE WHEN i.status IN ('PREFLIGHT','EXTRACTING','RECONCILING') THEN 'RETRY_WAIT' ELSE i.status END,
        current_stage=CASE WHEN i.status IN ('PREFLIGHT','EXTRACTING','RECONCILING') THEN 'retry_wait' ELSE i.current_stage END,
        error_code=CASE WHEN i.status IN ('PREFLIGHT','EXTRACTING','RECONCILING') THEN 'WORKER_LEASE_EXPIRED' ELSE i.error_code END,
        error_message=CASE WHEN i.status IN ('PREFLIGHT','EXTRACTING','RECONCILING') THEN 'WORKER_LEASE_EXPIRED' ELSE i.error_message END,
        next_attempt_at=CASE WHEN i.status IN ('PREFLIGHT','EXTRACTING','RECONCILING') THEN NULL ELSE i.next_attempt_at END,
        lease_generation=i.lease_generation+1,lease_owner=worker,lease_token=gen_random_uuid(),
        lease_expires_at=instant+make_interval(secs=>seconds),updated_at=instant
        WHERE i.organization_id=org AND i.id=item.id AND public.declaration_queue_actor(org,item.id,item.queue_request_id)=actor
        RETURNING i.id,i.queue_request_id,i.lease_generation,i.lease_token,i.lease_expires_at;
    END $$;

    CREATE FUNCTION renew_declaration_work(org uuid,job uuid,request uuid,generation bigint,token uuid,seconds integer)
      RETURNS timestamptz
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid; expiry timestamptz;
    BEGIN
      IF seconds IS NULL OR seconds<30 OR seconds>3600 THEN RAISE EXCEPTION 'Invalid worker lease parameters' USING ERRCODE='22023'; END IF;
      IF NOT public.lock_declaration_lease_metadata(org,job,request,generation,token,ARRAY['QUEUED','RETRY_WAIT','PREFLIGHT','EXTRACTING','RECONCILING']) THEN RETURN NULL; END IF;
      actor:=public.declaration_queue_actor(org,job,request);
      IF actor IS NULL THEN RETURN NULL; END IF;
      PERFORM set_config('app.current_user_id',actor::text,true);
      UPDATE public.declaration_import i SET lease_expires_at=clock_timestamp()+make_interval(secs=>seconds),updated_at=clock_timestamp()
        WHERE i.organization_id=org AND i.id=job AND i.lease_expires_at>clock_timestamp()
          AND public.declaration_queue_actor(org,job,request)=actor
        RETURNING i.lease_expires_at INTO expiry;
      RETURN expiry;
    END $$;

    CREATE FUNCTION transition_declaration_work(org uuid,job uuid,request uuid,generation bigint,token uuid,expected text[],target text,patch jsonb)
      RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE item public.declaration_import; replacement public.declaration_import; actor uuid; key text; terminal boolean; instant timestamptz;
    BEGIN
      IF patch IS NULL OR jsonb_typeof(patch)<>'object' OR target IS NULL THEN RAISE EXCEPTION 'Invalid worker transition' USING ERRCODE='22023'; END IF;
      FOR key IN SELECT jsonb_object_keys(patch) LOOP
        IF key NOT IN ('current_stage','progress_percent','error_code','error_message','input_tokens','output_tokens','estimated_cost_usd',
          'attempt_count','next_attempt_at','advance_revision','lease_owner','lease_token','lease_expires_at') THEN
          RAISE EXCEPTION 'Invalid worker transition field' USING ERRCODE='22023'; END IF;
        IF key IN ('lease_owner','lease_token','lease_expires_at') AND patch->key<>'null'::jsonb THEN
          RAISE EXCEPTION 'Worker transition cannot install a lease' USING ERRCODE='22023'; END IF;
      END LOOP;
      IF NOT public.lock_declaration_lease_metadata(org,job,request,generation,token,expected) THEN RETURN false; END IF;
      actor:=public.declaration_queue_actor(org,job,request);
      IF actor IS NULL THEN RETURN false; END IF;
      PERFORM set_config('app.current_user_id',actor::text,true);
      SELECT * INTO item FROM public.declaration_import i WHERE i.organization_id=org AND i.id=job;
      IF NOT ((item.status=target AND target IN ('PREFLIGHT','EXTRACTING','RECONCILING'))
        OR (item.status IN ('QUEUED','RETRY_WAIT') AND target IN ('PREFLIGHT','FAILED'))
        OR (item.status='PREFLIGHT' AND target IN ('EXTRACTING','RETRY_WAIT','FAILED'))
        OR (item.status='EXTRACTING' AND target IN ('RECONCILING','RETRY_WAIT','FAILED'))
        OR (item.status='RECONCILING' AND target IN ('NEEDS_REVIEW','RETRY_WAIT','FAILED'))) THEN
        RAISE EXCEPTION 'Invalid worker lifecycle transition' USING ERRCODE='23514'; END IF;
      replacement:=jsonb_populate_record(item,patch-'advance_revision'-'lease_owner'-'lease_token'-'lease_expires_at');
      IF replacement.input_tokens IS NULL OR replacement.input_tokens<0
        OR replacement.output_tokens IS NULL OR replacement.output_tokens<0
        OR replacement.estimated_cost_usd IS NULL OR replacement.estimated_cost_usd<0 OR replacement.estimated_cost_usd::text IN ('NaN','Infinity','-Infinity')
        OR replacement.attempt_count IS NULL OR replacement.attempt_count<0
        OR replacement.progress_percent IS NULL OR replacement.progress_percent<0 OR replacement.progress_percent>100
        OR (patch ? 'advance_revision' AND (patch->'advance_revision'<>'true'::jsonb OR target<>'NEEDS_REVIEW')) THEN
        RAISE EXCEPTION 'Invalid worker accounting transition' USING ERRCODE='23514'; END IF;
      terminal:=target IN ('NEEDS_REVIEW','FAILED','RETRY_WAIT');
      IF NOT terminal AND (patch ?| ARRAY['lease_owner','lease_token','lease_expires_at']) THEN
        RAISE EXCEPTION 'Active work must retain its lease' USING ERRCODE='23514'; END IF;
      instant:=clock_timestamp();
      -- Scheduling, stage labels, completion and revision increments are derived
      -- from the closed transition, never minted from caller-provided values.
      UPDATE public.declaration_import i SET status=target,
        current_stage=CASE target WHEN 'NEEDS_REVIEW' THEN 'review' ELSE lower(target) END,
        progress_percent=CASE WHEN target='NEEDS_REVIEW' THEN 100 ELSE replacement.progress_percent END,
        input_tokens=replacement.input_tokens,output_tokens=replacement.output_tokens,
        estimated_cost_usd=replacement.estimated_cost_usd,attempt_count=replacement.attempt_count,
        error_code=replacement.error_code,error_message=replacement.error_message,
        next_attempt_at=CASE WHEN target='RETRY_WAIT' THEN instant+interval '5 minutes' ELSE NULL END,
        draft_revision=i.draft_revision+CASE WHEN target='NEEDS_REVIEW' THEN 1 ELSE 0 END,
        lease_owner=CASE WHEN terminal THEN NULL ELSE i.lease_owner END,
        lease_token=CASE WHEN terminal THEN NULL ELSE i.lease_token END,
        lease_expires_at=CASE WHEN terminal THEN NULL ELSE i.lease_expires_at END,updated_at=instant
        WHERE i.organization_id=org AND i.id=job AND i.lease_expires_at>clock_timestamp()
          AND public.declaration_queue_actor(org,job,request)=actor;
      RETURN FOUND;
    END $$;

    CREATE FUNCTION cancel_declaration_work(org uuid,job uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE item public.declaration_import; instant timestamptz;
    BEGIN
      IF public.property_source_security_mode(org) IS NULL
        OR NOT COALESCE(public.scope_actor_role(org) IN ('OWNER_ADMIN','CLERK'),false)
        OR NOT public.property_import_runtime_access(org,job,'prepare') THEN RETURN false; END IF;
      SELECT * INTO item FROM public.declaration_import i WHERE i.organization_id=org AND i.id=job FOR UPDATE;
      IF NOT FOUND OR item.status IN ('APPLIED','APPLYING') THEN RETURN false; END IF;
      IF item.status='CANCELLED' THEN RETURN true; END IF;
      IF NOT public.property_import_runtime_access(org,job,'prepare') THEN RETURN false; END IF;
      instant:=clock_timestamp();
      UPDATE public.declaration_extraction_run SET status='FAILED',error_code='IMPORT_CANCELLED',completed_at=instant
        WHERE organization_id=org AND import_id=job AND status='RUNNING';
      UPDATE public.declaration_import SET status='CANCELLED',current_stage=NULL,lease_generation=lease_generation+1,
        lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,next_attempt_at=NULL,updated_at=instant
        WHERE organization_id=org AND id=job;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION claim_declaration_work(uuid,text,integer),renew_declaration_work(uuid,uuid,uuid,bigint,uuid,integer),
      transition_declaration_work(uuid,uuid,uuid,bigint,uuid,text[],text,jsonb),cancel_declaration_work(uuid,uuid) FROM PUBLIC;
    REVOKE ALL ON FUNCTION claim_declaration_work(uuid,text,integer),renew_declaration_work(uuid,uuid,uuid,bigint,uuid,integer),
      transition_declaration_work(uuid,uuid,uuid,bigint,uuid,text[],text,jsonb) FROM app_rw;
    GRANT EXECUTE ON FUNCTION cancel_declaration_work(uuid,uuid) TO app_rw;
    GRANT EXECUTE ON FUNCTION claim_declaration_work(uuid,text,integer),renew_declaration_work(uuid,uuid,uuid,bigint,uuid,integer),
      transition_declaration_work(uuid,uuid,uuid,bigint,uuid,text[],text,jsonb) TO app_worker;
    """)


def downgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain strict worker command protection'; END IF; END $$;
    DROP TRIGGER guard_declaration_worker_state ON declaration_import;
    DROP FUNCTION guard_declaration_worker_state(),claim_declaration_work(uuid,text,integer),renew_declaration_work(uuid,uuid,uuid,bigint,uuid,integer),
      transition_declaration_work(uuid,uuid,uuid,bigint,uuid,text[],text,jsonb),cancel_declaration_work(uuid,uuid);
    """)
