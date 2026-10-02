"""Isolate trusted Python review validation from ordinary runtime SQL."""
from alembic import op

revision='0073'
down_revision='0072'
branch_labels=None
depends_on=None
GRAPH=('declaration_import','declaration_candidate','declaration_candidate_evidence','declaration_candidate_issue')
TABLES=GRAPH+('audit_event','property_source_security','property_data_domain','property_data_grant','"user"','membership')
READ_FUNCTIONS=('scope_actor_role(uuid)','property_import_runtime_access(uuid,uuid,text)',
                'property_import_row_access(uuid,jsonb,text,boolean)','property_derived_row_access(uuid,text,jsonb,text)')
SIGNATURE='(uuid,uuid,integer,uuid[],text,jsonb,text,boolean)'
MANAGED="'CONFLICTING_ALTERNATIVES','CONFLICTING_FACT_ALTERNATIVES','MEA_DECLARED_DENOMINATOR_INVALID','MIXED_MEA_DENOMINATOR','MEA_TOTAL_INVALID','DUPLICATE_UNIT_NUMBER','MEA_VALUE_TYPE_INVALID','DUPLICATE_RULE_SUBJECT','ALLOCATION_TOTAL_INVALID','ALLOCATION_SHARE_TYPE_INVALID'"


def upgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname='app_review' AND r.rolcanlogin
        AND NOT r.rolinherit AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication
        AND NOT EXISTS(SELECT 1 FROM pg_auth_members WHERE roleid=r.oid OR member=r.oid)
        AND NOT EXISTS(SELECT 1 FROM pg_class WHERE relowner=r.oid)
        AND NOT EXISTS(SELECT 1 FROM pg_proc WHERE proowner=r.oid)
        AND NOT EXISTS(SELECT 1 FROM pg_namespace WHERE nspowner=r.oid)
        AND NOT EXISTS(SELECT 1 FROM pg_database WHERE datdba=r.oid)
        AND NOT has_schema_privilege(r.oid,'public','CREATE')
    AND NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') AND (
        has_table_privilege(r.oid,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
        OR has_any_column_privilege(r.oid,c.oid,'INSERT,UPDATE,REFERENCES')
        OR (c.relname NOT IN ('declaration_import','declaration_candidate','declaration_candidate_evidence','declaration_candidate_issue')
          AND has_any_column_privilege(r.oid,c.oid,'SELECT'))))
        ) THEN
        RAISE EXCEPTION 'Provision isolated app_review without inherited roles or ownership'; END IF; END $$;""")
    op.execute('GRANT USAGE ON SCHEMA public TO app_review')
    op.execute('GRANT SELECT ON '+','.join(GRAPH)+' TO app_review')
    op.execute('GRANT EXECUTE ON FUNCTION '+','.join(READ_FUNCTIONS)+' TO app_review')
    op.execute("""
    CREATE TABLE declaration_review_context (
      organization_id uuid NOT NULL,import_id uuid NOT NULL,actor_user_id uuid NOT NULL,
      backend_pid integer NOT NULL,transaction_id xid8 NOT NULL,draft_revision integer NOT NULL,
      prior_resolutions jsonb NOT NULL,expires_at timestamptz NOT NULL,
      PRIMARY KEY(organization_id,import_id,backend_pid,transaction_id),
      FOREIGN KEY(organization_id,import_id) REFERENCES declaration_import(organization_id,id) ON DELETE CASCADE
    );
    CREATE INDEX ix_review_context_expiry ON declaration_review_context(organization_id,expires_at);
    ALTER TABLE declaration_review_context ENABLE ROW LEVEL SECURITY;
    ALTER TABLE declaration_review_context FORCE ROW LEVEL SECURITY;
    CREATE POLICY review_context_private ON declaration_review_context USING(false) WITH CHECK(false);
    REVOKE ALL ON declaration_review_context FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION lock_declaration_review_graph(org uuid,job uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF session_user<>'app_review' OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      IF COALESCE(public.scope_actor_role(org),'') NOT IN ('OWNER_ADMIN','CLERK')
        OR NOT COALESCE(public.property_import_runtime_access(org,job,'read'),false)
        OR NOT COALESCE(public.property_import_runtime_access(org,job,'prepare'),false) THEN RETURN false; END IF;
      PERFORM 1 FROM public.declaration_import WHERE organization_id=org AND id=job FOR UPDATE;
      IF NOT FOUND THEN RETURN false; END IF;
      PERFORM 1 FROM public.declaration_candidate WHERE organization_id=org AND import_id=job ORDER BY id FOR UPDATE;
      PERFORM 1 FROM public.declaration_candidate_evidence WHERE organization_id=org AND import_id=job ORDER BY id FOR UPDATE;
      PERFORM 1 FROM public.declaration_candidate_issue WHERE organization_id=org AND import_id=job ORDER BY id FOR UPDATE;
      RETURN COALESCE(public.property_import_runtime_access(org,job,'prepare'),false);
    END $$;
    REVOKE ALL ON FUNCTION lock_declaration_review_graph(uuid,uuid) FROM PUBLIC,app_rw,app_worker;
    GRANT EXECUTE ON FUNCTION lock_declaration_review_graph(uuid,uuid) TO app_review;

    ALTER FUNCTION apply_declaration_review(uuid,uuid,integer,uuid[],text,jsonb,text,boolean) RENAME TO apply_declaration_review_before_context;
    DO $$ DECLARE definition text; BEGIN
      SELECT pg_get_functiondef('public.apply_declaration_review_before_context(uuid,uuid,integer,uuid[],text,jsonb,text,boolean)'::regprocedure) INTO definition;
      IF position('session_user<>''app_rw''' IN definition)=0 THEN RAISE EXCEPTION 'Expected human command identity guard'; END IF;
      EXECUTE replace(definition,'session_user<>''app_rw''','session_user<>''app_review''');
    END $$;
    REVOKE ALL ON FUNCTION apply_declaration_review_before_context(uuid,uuid,integer,uuid[],text,jsonb,text,boolean) FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE FUNCTION apply_declaration_review(org uuid,job uuid,expected_revision integer,ids uuid[],decision text,correction jsonb,comment text,bulk boolean)
      RETURNS integer
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE prior jsonb; resulting_revision integer; actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      IF NOT public.lock_declaration_review_graph(org,job) THEN
        RAISE EXCEPTION 'Current human review authority required' USING ERRCODE='42501'; END IF;
      SELECT COALESCE(jsonb_object_agg(q.id::text,jsonb_build_object('actor',q.resolved_by,'at',q.resolved_at)), '{}'::jsonb)
        INTO prior FROM public.declaration_candidate_issue q WHERE q.organization_id=org AND q.import_id=job AND q.resolution_status='RESOLVED';
      resulting_revision:=public.apply_declaration_review_before_context(org,job,expected_revision,ids,decision,correction,comment,bulk);
      DELETE FROM public.declaration_review_context c WHERE c.ctid IN (
        SELECT old.ctid FROM public.declaration_review_context old WHERE old.organization_id=org AND old.expires_at<clock_timestamp()
        ORDER BY old.expires_at LIMIT 1000 FOR UPDATE SKIP LOCKED);
      INSERT INTO public.declaration_review_context(organization_id,import_id,actor_user_id,backend_pid,transaction_id,draft_revision,prior_resolutions,expires_at)
        VALUES(org,job,actor,pg_backend_pid(),pg_current_xact_id(),resulting_revision,prior,clock_timestamp()+interval '5 minutes')
        ON CONFLICT(organization_id,import_id,backend_pid,transaction_id) DO UPDATE SET actor_user_id=EXCLUDED.actor_user_id,
          draft_revision=EXCLUDED.draft_revision,prior_resolutions=EXCLUDED.prior_resolutions,expires_at=EXCLUDED.expires_at;
      RETURN resulting_revision;
    END $$;
    REVOKE ALL ON FUNCTION apply_declaration_review(uuid,uuid,integer,uuid[],text,jsonb,text,boolean) FROM PUBLIC,app_rw,app_worker;
    GRANT EXECUTE ON FUNCTION apply_declaration_review(uuid,uuid,integer,uuid[],text,jsonb,text,boolean) TO app_review;

    CREATE FUNCTION guard_declaration_issue_review() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE owner name; org uuid; job uuid;
    BEGIN
      SELECT pg_get_userbyid(proowner) INTO owner FROM pg_proc WHERE oid='public.guard_declaration_issue_review()'::regprocedure;
      IF current_user=owner THEN IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF; END IF;
      org:=CASE WHEN TG_OP='DELETE' THEN OLD.organization_id ELSE NEW.organization_id END;
      job:=CASE WHEN TG_OP='DELETE' THEN OLD.import_id ELSE NEW.import_id END;
      IF public.property_source_security_mode(org)<>'PREPARATION'
        AND EXISTS(SELECT 1 FROM public.declaration_import WHERE organization_id=org AND id=job AND status='NEEDS_REVIEW') THEN
        RAISE EXCEPTION 'Trusted issue revalidation command required' USING ERRCODE='42501'; END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END $$;
    REVOKE ALL ON FUNCTION guard_declaration_issue_review() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER b_guard_issue_review BEFORE INSERT OR UPDATE OR DELETE ON declaration_candidate_issue
      FOR EACH ROW EXECUTE FUNCTION guard_declaration_issue_review();
    """)
    op.execute(f"""
    CREATE FUNCTION apply_declaration_issue_revalidation(org uuid,job uuid,revision integer,effects jsonb) RETURNS void
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE receipt public.declaration_review_context; item public.declaration_import; issue public.declaration_candidate_issue;
      effect jsonb; issue_id uuid; candidate uuid; desired text; issue_code text; prior jsonb; actor uuid; instant timestamptz;
    BEGIN
      IF NOT public.lock_declaration_review_graph(org,job) THEN
        RAISE EXCEPTION 'Current human review authority required' USING ERRCODE='42501'; END IF;
      actor:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      SELECT * INTO receipt FROM public.declaration_review_context c WHERE c.organization_id=org AND c.import_id=job
        AND c.backend_pid=pg_backend_pid() AND c.transaction_id=pg_current_xact_id() AND c.actor_user_id=actor
        AND c.draft_revision=revision AND c.expires_at>clock_timestamp();
      IF NOT FOUND THEN RAISE EXCEPTION 'Current review transaction context required' USING ERRCODE='42501'; END IF;
      SELECT * INTO item FROM public.declaration_import WHERE organization_id=org AND id=job;
      IF item.status<>'NEEDS_REVIEW' OR item.draft_revision<>revision THEN
        RAISE EXCEPTION 'STALE_DRAFT_REVISION' USING ERRCODE='40001'; END IF;
      IF effects IS NULL OR jsonb_typeof(effects)<>'array' OR jsonb_array_length(effects)>10000 THEN
        RAISE EXCEPTION 'INVALID_ISSUE_EFFECTS' USING ERRCODE='22023'; END IF;
      instant:=clock_timestamp();
      FOR effect IN SELECT value FROM jsonb_array_elements(effects) LOOP
        IF jsonb_typeof(effect)<>'object' THEN RAISE EXCEPTION 'INVALID_ISSUE_EFFECTS' USING ERRCODE='22023'; END IF;
        IF effect ? 'id' THEN
          IF effect-ARRAY['id','resolution_status']<>'{{}}'::jsonb THEN RAISE EXCEPTION 'INVALID_ISSUE_EFFECTS' USING ERRCODE='22023'; END IF;
          issue_id:=(effect->>'id')::uuid;desired:=effect->>'resolution_status';
          SELECT * INTO issue FROM public.declaration_candidate_issue WHERE organization_id=org AND import_id=job AND id=issue_id;
          IF NOT FOUND OR issue.code NOT IN ({MANAGED}) OR desired IS NULL OR desired NOT IN ('OPEN','RESOLVED') THEN
            RAISE EXCEPTION 'INVALID_ISSUE_EFFECTS' USING ERRCODE='22023'; END IF;
          prior:=receipt.prior_resolutions->issue_id::text;
          UPDATE public.declaration_candidate_issue SET resolution_status=desired,
            resolved_by=CASE WHEN desired='OPEN' THEN NULL ELSE COALESCE((prior->>'actor')::uuid,actor) END,
            resolved_at=CASE WHEN desired='OPEN' THEN NULL ELSE COALESCE((prior->>'at')::timestamptz,instant) END,updated_at=instant
            WHERE organization_id=org AND import_id=job AND id=issue_id;
        ELSE
          IF effect-ARRAY['candidate_id','code']<>'{{}}'::jsonb THEN RAISE EXCEPTION 'INVALID_ISSUE_EFFECTS' USING ERRCODE='22023'; END IF;
          candidate:=(effect->>'candidate_id')::uuid;issue_code:=effect->>'code';
          IF issue_code IS NULL OR issue_code NOT IN ('ALLOCATION_TOTAL_INVALID','ALLOCATION_SHARE_TYPE_INVALID') OR NOT EXISTS(
            SELECT 1 FROM public.declaration_candidate c WHERE c.organization_id=org AND c.import_id=job AND c.id=candidate AND c.candidate_type='RULE') THEN
            RAISE EXCEPTION 'INVALID_ISSUE_EFFECTS' USING ERRCODE='22023'; END IF;
          IF EXISTS(SELECT 1 FROM public.declaration_candidate_issue q WHERE q.organization_id=org AND q.import_id=job AND q.candidate_id=candidate AND q.code=issue_code) THEN
            RAISE EXCEPTION 'DUPLICATE_MANAGED_ISSUE' USING ERRCODE='22023'; END IF;
          INSERT INTO public.declaration_candidate_issue(organization_id,import_id,candidate_id,code,severity,message,resolution_status)
            VALUES(org,job,candidate,issue_code,'BLOCKER',CASE issue_code WHEN 'ALLOCATION_TOTAL_INVALID'
              THEN 'Die als vollständig erklärte Kostenverteilung ergibt rechnerisch nicht 100 Prozent.'
              ELSE 'Die Kostenverteilung enthält gemischte oder nicht rechnerisch prüfbare Anteile.' END,'OPEN');
        END IF;
      END LOOP;
      IF receipt.expires_at<=clock_timestamp() OR NOT COALESCE(public.property_import_runtime_access(org,job,'prepare'),false) THEN
        RAISE EXCEPTION 'Current human review authority required' USING ERRCODE='42501'; END IF;
    END $$;
    REVOKE ALL ON FUNCTION apply_declaration_issue_revalidation(uuid,uuid,integer,jsonb) FROM PUBLIC,app_rw,app_worker;
    GRANT EXECUTE ON FUNCTION apply_declaration_issue_revalidation(uuid,uuid,integer,jsonb) TO app_review;
    """)


def downgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+',declaration_review_context IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain isolated review validation'; END IF; END $$;
      DROP TRIGGER b_guard_issue_review ON declaration_candidate_issue;
      DROP FUNCTION guard_declaration_issue_review(),apply_declaration_issue_revalidation(uuid,uuid,integer,jsonb),
        apply_declaration_review(uuid,uuid,integer,uuid[],text,jsonb,text,boolean),lock_declaration_review_graph(uuid,uuid);
      DROP TABLE declaration_review_context;
      ALTER FUNCTION apply_declaration_review_before_context(uuid,uuid,integer,uuid[],text,jsonb,text,boolean) RENAME TO apply_declaration_review;
      DO $$ DECLARE definition text; BEGIN
        SELECT pg_get_functiondef('public.apply_declaration_review(uuid,uuid,integer,uuid[],text,jsonb,text,boolean)'::regprocedure) INTO definition;
        EXECUTE replace(definition,'session_user<>''app_review''','session_user<>''app_rw''');
      END $$;
      GRANT EXECUTE ON FUNCTION apply_declaration_review(uuid,uuid,integer,uuid[],text,jsonb,text,boolean) TO app_rw;
    """)
    op.execute('REVOKE SELECT ON '+','.join(GRAPH)+' FROM app_review')
    op.execute('REVOKE EXECUTE ON FUNCTION '+','.join(READ_FUNCTIONS)+' FROM app_review')
