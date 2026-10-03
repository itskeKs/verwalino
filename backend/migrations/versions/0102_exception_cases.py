"""Immutable shared conflict evidence and advisory, versioned workflow events."""
from alembic import op
from sqlalchemy import text

revision='0102'
down_revision='0101'
branch_labels=None
depends_on=None
BEFORE="UNION ALL SELECT 'assignment_proposal',id,to_jsonb(r) FROM public.assignment_proposal r WHERE organization_id=org) evidence"
AFTER=BEFORE.replace(') evidence'," UNION ALL SELECT 'exception_case',id,to_jsonb(r) FROM public.exception_case r WHERE organization_id=org UNION ALL SELECT 'exception_dependency',id,to_jsonb(r) FROM public.exception_dependency r WHERE organization_id=org UNION ALL SELECT 'exception_case_event',id,to_jsonb(r) FROM public.exception_case_event r WHERE organization_id=org) evidence")


def upgrade():
    op.execute('''
    ALTER TABLE assignment_proposal ADD CONSTRAINT assignment_exception_source UNIQUE(organization_id,scope_id,extraction_revision_id,id);
    CREATE TABLE exception_case(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),
      document_ref_id uuid NOT NULL,logical_document_id uuid NOT NULL,extraction_revision_id uuid NOT NULL,scope_id uuid NOT NULL,
      cause text NOT NULL CHECK(cause IN ('scope_hint_conflict','unsupported_scope_type')),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(organization_id,id),UNIQUE(organization_id,scope_id,cause,extraction_revision_id),
      UNIQUE(organization_id,id,scope_id,cause,extraction_revision_id),
      FOREIGN KEY(organization_id,scope_id) REFERENCES accounting_scope(organization_id,id),
      FOREIGN KEY(organization_id,document_ref_id,logical_document_id,extraction_revision_id) REFERENCES extraction_revision(organization_id,document_ref_id,logical_document_id,id));
    CREATE TABLE exception_dependency(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,case_id uuid NOT NULL,
      scope_id uuid NOT NULL,cause text NOT NULL,extraction_revision_id uuid NOT NULL,assignment_proposal_id uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(organization_id,case_id,id),UNIQUE(organization_id,case_id,assignment_proposal_id),
      FOREIGN KEY(organization_id,case_id,scope_id,cause,extraction_revision_id) REFERENCES exception_case(organization_id,id,scope_id,cause,extraction_revision_id),
      FOREIGN KEY(organization_id,scope_id,extraction_revision_id,assignment_proposal_id) REFERENCES assignment_proposal(organization_id,scope_id,extraction_revision_id,id));
    CREATE TABLE exception_case_event(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,case_id uuid NOT NULL,
      previous_event_id uuid,revision_number integer NOT NULL CHECK(revision_number>0),
      action text NOT NULL CHECK(action IN ('evidence_added','claim','release','snooze','reopen')),
      resolution_state text NOT NULL DEFAULT 'unresolved' CHECK(resolution_state='unresolved'),
      workflow_state text NOT NULL CHECK(workflow_state IN ('open','snoozed')),claimant_id uuid,snoozed_until timestamptz,
      reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 1000),
      actor_id uuid,dependency_id uuid,request_key uuid,request_digest text,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CHECK((workflow_state='snoozed')=(snoozed_until IS NOT NULL)),CHECK(snoozed_until IS NULL OR isfinite(snoozed_until)),
      CHECK((action='evidence_added' AND actor_id IS NULL AND dependency_id IS NOT NULL AND request_key IS NULL AND request_digest IS NULL)
         OR (action<>'evidence_added' AND actor_id IS NOT NULL AND dependency_id IS NULL AND request_key IS NOT NULL AND request_digest IS NOT NULL)),
      UNIQUE(organization_id,case_id,id),UNIQUE(organization_id,case_id,revision_number),
      UNIQUE NULLS NOT DISTINCT(organization_id,case_id,previous_event_id),UNIQUE(organization_id,dependency_id),UNIQUE(organization_id,actor_id,request_key),
      FOREIGN KEY(organization_id,case_id) REFERENCES exception_case(organization_id,id),
      FOREIGN KEY(organization_id,case_id,previous_event_id) REFERENCES exception_case_event(organization_id,case_id,id),
      FOREIGN KEY(organization_id,case_id,dependency_id) REFERENCES exception_dependency(organization_id,case_id,id),
      FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id),
      FOREIGN KEY(organization_id,claimant_id) REFERENCES "user"(organization_id,id));
    CREATE FUNCTION exception_case_access(org uuid,target uuid) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT EXISTS(SELECT 1 FROM public.exception_case c WHERE c.organization_id=org AND c.id=target
        AND public.assignment_proposal_access(org,c.document_ref_id,c.scope_id));
    $$;
    REVOKE ALL ON FUNCTION exception_case_access(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION exception_case_access(uuid,uuid) TO app_rw;
    ''')
    for table in ('exception_case','exception_dependency','exception_case_event'):
        target='id' if table=='exception_case' else 'case_id'
        op.execute(f'''ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review; GRANT SELECT ON {table} TO app_rw;
          CREATE POLICY exception_read ON {table} FOR SELECT USING(public.exception_case_access(organization_id,{target}));
          CREATE POLICY exception_no_insert ON {table} FOR INSERT WITH CHECK(false);
          CREATE TRIGGER protect_exception_evidence BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();''')
    op.execute('''
    CREATE FUNCTION validate_exception_dependency() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$ BEGIN
      IF NOT EXISTS(SELECT 1 FROM public.assignment_proposal p WHERE p.organization_id=NEW.organization_id AND p.id=NEW.assignment_proposal_id
        AND NEW.cause=ANY(p.blockers)) THEN RAISE EXCEPTION 'Exception cause missing from proposal' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER validate_exception_dependency BEFORE INSERT ON exception_dependency FOR EACH ROW EXECUTE FUNCTION validate_exception_dependency();

    CREATE FUNCTION record_assignment_exceptions(org uuid,proposal_id uuid) RETURNS void
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE proposal public.assignment_proposal; prior public.exception_case_event; cause_code text; target uuid; dependency uuid;
    BEGIN
      -- Same order as lock_intake, also usable by the privileged migration backfill without actor GUCs.
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      SELECT * INTO STRICT proposal FROM public.assignment_proposal WHERE organization_id=org AND id=proposal_id;
      FOREACH cause_code IN ARRAY proposal.blockers LOOP
        INSERT INTO public.exception_case(organization_id,document_ref_id,logical_document_id,extraction_revision_id,scope_id,cause)
          VALUES(org,proposal.document_ref_id,proposal.logical_document_id,proposal.extraction_revision_id,proposal.scope_id,cause_code)
          ON CONFLICT(organization_id,scope_id,cause,extraction_revision_id) DO NOTHING;
        SELECT id INTO STRICT target FROM public.exception_case WHERE organization_id=org AND scope_id=proposal.scope_id AND cause=cause_code AND extraction_revision_id=proposal.extraction_revision_id;
        INSERT INTO public.exception_dependency(organization_id,case_id,scope_id,cause,extraction_revision_id,assignment_proposal_id)
          VALUES(org,target,proposal.scope_id,cause_code,proposal.extraction_revision_id,proposal_id)
          ON CONFLICT(organization_id,case_id,assignment_proposal_id) DO NOTHING RETURNING id INTO dependency;
        IF dependency IS NOT NULL THEN
          SELECT * INTO prior FROM public.exception_case_event WHERE organization_id=org AND case_id=target ORDER BY revision_number DESC LIMIT 1;
          INSERT INTO public.exception_case_event(organization_id,case_id,previous_event_id,revision_number,action,workflow_state,claimant_id,reason,dependency_id)
            VALUES(org,target,prior.id,COALESCE(prior.revision_number,0)+1,'evidence_added','open',prior.claimant_id,'Blocked assignment evidence recorded',dependency);
        END IF;
      END LOOP;
    END $$;
    CREATE FUNCTION assignment_exception_trigger() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$ BEGIN
      PERFORM public.record_assignment_exceptions(NEW.organization_id,NEW.id); RETURN NEW;
    END $$;
    CREATE TRIGGER assignment_exceptions AFTER INSERT ON assignment_proposal FOR EACH ROW EXECUTE FUNCTION assignment_exception_trigger();
    REVOKE ALL ON FUNCTION validate_exception_dependency(),record_assignment_exceptions(uuid,uuid),assignment_exception_trigger() FROM PUBLIC,app_rw,app_worker,app_review;
    DO $$ DECLARE p record; BEGIN
      FOR p IN SELECT organization_id,id FROM public.assignment_proposal WHERE state='blocked' ORDER BY organization_id,created_at,id LOOP
        PERFORM public.record_assignment_exceptions(p.organization_id,p.id);
      END LOOP;
    END $$;

    CREATE FUNCTION append_exception_event(org uuid,target uuid,expected uuid,request uuid,verb text,explanation text,until_time timestamptz) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; c public.exception_case;
      prior public.exception_case_event; current_event public.exception_case_event; digest text; created uuid;
      claimant uuid; workflow text; snooze timestamptz;
    BEGIN
      IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501'; END IF;
      PERFORM public.lock_intake(org);
      SELECT * INTO c FROM public.exception_case WHERE organization_id=org AND id=target;
      IF NOT FOUND OR NOT public.exception_case_access(org,target) OR NOT public.source_partition_access(org,c.document_ref_id,true)
        OR NOT public.has_scope_capability(org,c.scope_id,'prepare') THEN RAISE EXCEPTION 'Exception inaccessible' USING ERRCODE='42501'; END IF;
      IF request IS NULL OR expected IS NULL OR verb IS NULL OR verb NOT IN ('claim','release','snooze','reopen') OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 1000
        OR (verb='snooze') IS DISTINCT FROM (until_time IS NOT NULL) OR (until_time IS NOT NULL AND NOT isfinite(until_time)) THEN
        RAISE EXCEPTION 'Explicit exception action required' USING ERRCODE='22023'; END IF;
      -- Epoch gives equivalent timezone representations the same immutable request digest.
      digest:=encode(sha256(convert_to(jsonb_build_array(target,expected,verb,explanation,extract(epoch FROM until_time))::text,'UTF8')),'hex');
      SELECT * INTO prior FROM public.exception_case_event WHERE organization_id=org AND actor_id=actor AND request_key=request;
      IF FOUND THEN
        IF prior.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Exception request changed' USING ERRCODE='23505'; END IF;
        RETURN prior.id;
      END IF;
      SELECT * INTO STRICT current_event FROM public.exception_case_event WHERE organization_id=org AND case_id=target ORDER BY revision_number DESC LIMIT 1;
      IF current_event.id IS DISTINCT FROM expected THEN RAISE EXCEPTION 'Exception changed' USING ERRCODE='23505'; END IF;
      claimant:=current_event.claimant_id;
      snooze:=CASE WHEN current_event.snoozed_until>clock_timestamp() THEN current_event.snoozed_until END;
      IF verb='claim' THEN claimant:=actor;
      ELSIF verb='release' THEN
        IF claimant IS DISTINCT FROM actor THEN RAISE EXCEPTION 'Only current claimant can release' USING ERRCODE='23505'; END IF;
        claimant:=NULL;
      ELSIF verb='snooze' THEN
        IF until_time<=clock_timestamp() THEN RAISE EXCEPTION 'Future snooze time required' USING ERRCODE='22023'; END IF;
        snooze:=until_time;
      ELSIF verb='reopen' THEN snooze:=NULL;
      END IF;
      workflow:=CASE WHEN snooze IS NULL THEN 'open' ELSE 'snoozed' END;
      INSERT INTO public.exception_case_event(organization_id,case_id,previous_event_id,revision_number,action,workflow_state,claimant_id,snoozed_until,reason,actor_id,request_key,request_digest)
        VALUES(org,target,expected,current_event.revision_number+1,verb,workflow,claimant,snooze,explanation,actor,request,digest) RETURNING id INTO created;
      RETURN created;
    END $$;
    REVOKE ALL ON FUNCTION append_exception_event(uuid,uuid,uuid,uuid,text,text,timestamptz) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION append_exception_event(uuid,uuid,uuid,uuid,text,text,timestamptz) TO app_rw;
    ''')
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(BEFORE)==1
    op.execute(definition.replace(BEFORE,AFTER))


def downgrade():
    op.execute('LOCK TABLE assignment_proposal,exception_case,exception_dependency,exception_case_event IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM exception_case) THEN RAISE EXCEPTION 'Retain exception evidence; repair forward'; END IF; END $$")
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(AFTER)==1
    op.execute(definition.replace(AFTER,BEFORE))
    op.execute('''DROP TRIGGER assignment_exceptions ON assignment_proposal;
      DROP FUNCTION assignment_exception_trigger(),record_assignment_exceptions(uuid,uuid),append_exception_event(uuid,uuid,uuid,uuid,text,text,timestamptz);
      DROP TABLE exception_case_event,exception_dependency,exception_case;
      DROP FUNCTION exception_case_access(uuid,uuid),validate_exception_dependency();
      ALTER TABLE assignment_proposal DROP CONSTRAINT assignment_exception_source;''')
