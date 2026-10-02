"""Require a database-owned transaction lease receipt for worker output effects."""
from alembic import op

revision='0071'
down_revision='0070'
branch_labels=None
depends_on=None
OUTPUTS=('declaration_extraction_run','declaration_import_pass','declaration_candidate','declaration_candidate_evidence','declaration_candidate_issue')
TABLES=OUTPUTS+('declaration_import','declaration_queue_request','property_document','declaration_import_document','property_source_security','property_data_domain','property_data_grant','"user"','membership')


def upgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""
    CREATE TABLE declaration_worker_context (
      organization_id uuid NOT NULL, import_id uuid NOT NULL,
      backend_pid integer NOT NULL, transaction_id xid8 NOT NULL,
      queue_request_id uuid NOT NULL, lease_generation bigint NOT NULL,lease_token uuid NOT NULL,
      expires_at timestamptz NOT NULL,
      PRIMARY KEY(organization_id,import_id,backend_pid,transaction_id),
      FOREIGN KEY(organization_id,import_id) REFERENCES declaration_import(organization_id,id) ON DELETE CASCADE
    );
    CREATE INDEX ix_worker_context_expiry ON declaration_worker_context(organization_id,expires_at);
    ALTER TABLE declaration_worker_context ENABLE ROW LEVEL SECURITY;
    ALTER TABLE declaration_worker_context FORCE ROW LEVEL SECURITY;
    CREATE POLICY worker_context_private ON declaration_worker_context USING(false) WITH CHECK(false);
    REVOKE ALL ON declaration_worker_context FROM PUBLIC,app_rw,app_worker;

    ALTER FUNCTION lock_declaration_lease_metadata(uuid,uuid,uuid,bigint,uuid,text[]) RENAME TO lock_declaration_lease_before_effects;
    REVOKE ALL ON FUNCTION lock_declaration_lease_before_effects(uuid,uuid,uuid,bigint,uuid,text[]) FROM PUBLIC,app_rw,app_worker;
    CREATE FUNCTION lock_declaration_lease_metadata(org uuid,job uuid,request uuid,generation bigint,token uuid,statuses text[])
      RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE expiry timestamptz;
    BEGIN
      IF NOT public.lock_declaration_lease_before_effects(org,job,request,generation,token,statuses) THEN RETURN false; END IF;
      IF session_user='app_worker' AND public.declaration_queue_actor(org,job,request) IS NOT NULL THEN
        SELECT i.lease_expires_at INTO expiry FROM public.declaration_import i WHERE i.organization_id=org AND i.id=job;
        IF expiry<=clock_timestamp() THEN RETURN false; END IF;
        -- Internal, content-free receipts are transaction authority, not history.
        -- Bound cleanup avoids worker-to-worker lock waits while reclaiming them.
        DELETE FROM public.declaration_worker_context c WHERE c.ctid IN (
          SELECT old.ctid FROM public.declaration_worker_context old WHERE old.organization_id=org AND old.expires_at<clock_timestamp()
          ORDER BY old.expires_at LIMIT 1000 FOR UPDATE SKIP LOCKED);
        INSERT INTO public.declaration_worker_context(organization_id,import_id,backend_pid,transaction_id,queue_request_id,lease_generation,lease_token,expires_at)
          VALUES(org,job,pg_backend_pid(),pg_current_xact_id(),request,generation,token,expiry)
          ON CONFLICT(organization_id,import_id,backend_pid,transaction_id) DO UPDATE
          SET queue_request_id=EXCLUDED.queue_request_id,lease_generation=EXCLUDED.lease_generation,
            lease_token=EXCLUDED.lease_token,expires_at=EXCLUDED.expires_at;
      END IF;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION lock_declaration_lease_metadata(uuid,uuid,uuid,bigint,uuid,text[]) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION lock_declaration_lease_metadata(uuid,uuid,uuid,bigint,uuid,text[]) TO app_rw;

    CREATE FUNCTION declaration_worker_effect_access(org uuid,job uuid,stages text[]) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE receipt public.declaration_worker_context; item public.declaration_import; actor uuid;
    BEGIN
      IF session_user<>'app_worker' OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR job IS NULL OR stages IS NULL OR cardinality(stages)=0 THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      SELECT * INTO receipt FROM public.declaration_worker_context c WHERE c.organization_id=org AND c.import_id=job
        AND c.backend_pid=pg_backend_pid() AND c.transaction_id=pg_current_xact_id();
      IF NOT FOUND THEN RETURN false; END IF;
      SELECT * INTO item FROM public.declaration_import i WHERE i.organization_id=org AND i.id=job FOR UPDATE;
      IF NOT FOUND OR item.queue_request_id IS DISTINCT FROM receipt.queue_request_id
        OR item.lease_generation<>receipt.lease_generation OR item.lease_token IS DISTINCT FROM receipt.lease_token
        OR item.lease_expires_at IS NULL OR item.lease_expires_at<=clock_timestamp() OR NOT item.status=ANY(stages) THEN RETURN false; END IF;
      actor:=public.declaration_queue_actor(org,job,item.queue_request_id);
      RETURN actor IS NOT NULL AND actor=NULLIF(current_setting('app.current_user_id',true),'')::uuid
        AND item.lease_expires_at>clock_timestamp();
    END $$;
    REVOKE ALL ON FUNCTION declaration_worker_effect_access(uuid,uuid,text[]) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION declaration_worker_effect_access(uuid,uuid,text[]) TO app_rw;

    CREATE FUNCTION guard_declaration_worker_effect() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE context jsonb; org uuid; job uuid; phase text; stages text[]; owner name; valid boolean;
    BEGIN
      SELECT pg_get_userbyid(proowner) INTO owner FROM pg_proc WHERE oid='public.guard_declaration_worker_effect()'::regprocedure;
      -- Narrow typed cancellation/reclaim commands and migrations execute as owner.
      IF current_user=owner THEN IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF; END IF;
      context:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
      org:=(context->>'organization_id')::uuid;
      IF public.property_source_security_mode(org)='PREPARATION' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
      END IF;
      IF TG_TABLE_NAME='property_document' THEN
        IF NEW.page_count IS NOT DISTINCT FROM OLD.page_count THEN RETURN NEW; END IF;
        SELECT l.import_id INTO job FROM public.declaration_import_document l WHERE l.organization_id=org AND l.document_id=NEW.id;
        stages:=ARRAY['RECONCILING'];
      ELSIF TG_TABLE_NAME='declaration_import_pass' THEN
        SELECT r.import_id INTO job FROM public.declaration_extraction_run r
          WHERE r.organization_id=org AND r.id=(context->>'declaration_extraction_run_id')::uuid;
        stages:=ARRAY['PREFLIGHT','EXTRACTING','RECONCILING'];
      ELSE
        job:=(context->>'import_id')::uuid;
        stages:=CASE WHEN TG_TABLE_NAME='declaration_extraction_run' THEN ARRAY['PREFLIGHT','EXTRACTING','RECONCILING'] ELSE ARRAY['RECONCILING'] END;
      END IF;
      SELECT i.status INTO phase FROM public.declaration_import i WHERE i.organization_id=org AND i.id=job;
      IF phase='NEEDS_REVIEW' AND session_user='app_rw' THEN
        -- Existing review service compatibility; deliberately not worker proof.
        -- Atomic review/candidate/issue/revision commands are a separate closure.
        IF TG_TABLE_NAME='declaration_candidate' AND TG_OP='UPDATE'
          AND (to_jsonb(NEW)-ARRAY['review_status','reviewed_by','reviewed_at','corrected_value_json','review_comment','candidate_version','updated_at'])
            =(to_jsonb(OLD)-ARRAY['review_status','reviewed_by','reviewed_at','corrected_value_json','review_comment','candidate_version','updated_at'])
          AND public.property_import_runtime_access(org,job,'prepare') THEN RETURN NEW; END IF;
        IF TG_TABLE_NAME='declaration_candidate_issue' AND TG_OP IN ('INSERT','UPDATE')
          AND public.property_import_runtime_access(org,job,'prepare') THEN RETURN NEW; END IF;
      END IF;
      valid:=public.declaration_worker_effect_access(org,job,stages);
      IF valid AND TG_TABLE_NAME='declaration_candidate' THEN
        valid:=context->>'review_status'='PENDING' AND context->>'reviewed_by' IS NULL
          AND context->>'reviewed_at' IS NULL AND context->>'corrected_value_json' IS NULL AND context->>'review_comment' IS NULL;
      ELSIF valid AND TG_TABLE_NAME='declaration_candidate_issue' THEN
        valid:=context->>'resolution_status'='OPEN' AND context->>'resolved_by' IS NULL AND context->>'resolved_at' IS NULL;
      END IF;
      IF NOT COALESCE(valid,false) THEN RAISE EXCEPTION 'Current worker effect receipt required' USING ERRCODE='42501'; END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END $$;
    REVOKE ALL ON FUNCTION guard_declaration_worker_effect() FROM PUBLIC,app_rw,app_worker;
    """)
    for table in OUTPUTS:
        op.execute(f'CREATE TRIGGER g_guard_worker_effect BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION guard_declaration_worker_effect()')
    op.execute('CREATE TRIGGER g_guard_worker_effect BEFORE UPDATE ON property_document FOR EACH ROW EXECUTE FUNCTION guard_declaration_worker_effect()')


def downgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+',declaration_worker_context IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain strict worker effect fencing'; END IF; END $$;""")
    for table in OUTPUTS+('property_document',): op.execute(f'DROP TRIGGER g_guard_worker_effect ON {table}')
    op.execute("""DROP FUNCTION guard_declaration_worker_effect(),declaration_worker_effect_access(uuid,uuid,text[]),
      lock_declaration_lease_metadata(uuid,uuid,uuid,bigint,uuid,text[]);
    ALTER FUNCTION lock_declaration_lease_before_effects(uuid,uuid,uuid,bigint,uuid,text[]) RENAME TO lock_declaration_lease_metadata;
    GRANT EXECUTE ON FUNCTION lock_declaration_lease_metadata(uuid,uuid,uuid,bigint,uuid,text[]) TO app_rw;
    DROP TABLE declaration_worker_context;
    """)
