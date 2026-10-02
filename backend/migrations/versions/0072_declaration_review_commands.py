"""Atomically persist human decisions, revisions and canonical review audit."""
from alembic import op

revision='0072'
down_revision='0071'
branch_labels=None
depends_on=None
TABLES=('declaration_import','declaration_candidate','declaration_candidate_evidence','declaration_candidate_issue','audit_event',
        'property_source_security','property_data_domain','property_data_grant','"user"','membership')


def upgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""
    CREATE FUNCTION guard_declaration_review_command() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE owner name;
    BEGIN
      SELECT pg_get_userbyid(proowner) INTO owner FROM pg_proc WHERE oid='public.guard_declaration_review_command()'::regprocedure;
      IF current_user=owner OR public.property_source_security_mode(NEW.organization_id)='PREPARATION' THEN RETURN NEW; END IF;
      IF TG_TABLE_NAME='audit_event' THEN
        IF NEW.action<>'declaration_import.candidate_reviewed' THEN RETURN NEW; END IF;
      ELSIF TG_TABLE_NAME='declaration_import' THEN
        IF NEW.draft_revision IS NOT DISTINCT FROM OLD.draft_revision THEN RETURN NEW; END IF;
      ELSIF TG_TABLE_NAME='declaration_candidate' THEN
        IF ROW(NEW.review_status,NEW.reviewed_by,NEW.reviewed_at,NEW.corrected_value_json,NEW.review_comment,NEW.candidate_version)
          IS NOT DISTINCT FROM ROW(OLD.review_status,OLD.reviewed_by,OLD.reviewed_at,OLD.corrected_value_json,OLD.review_comment,OLD.candidate_version)
          THEN RETURN NEW; END IF;
      END IF;
      RAISE EXCEPTION 'Atomic declaration review command required' USING ERRCODE='42501';
    END $$;
    REVOKE ALL ON FUNCTION guard_declaration_review_command() FROM PUBLIC,app_rw,app_worker;
    CREATE TRIGGER b_guard_review_command BEFORE UPDATE ON declaration_import FOR EACH ROW EXECUTE FUNCTION guard_declaration_review_command();
    CREATE TRIGGER b_guard_review_command BEFORE UPDATE ON declaration_candidate FOR EACH ROW EXECUTE FUNCTION guard_declaration_review_command();
    CREATE TRIGGER b_guard_review_command BEFORE INSERT ON audit_event FOR EACH ROW EXECUTE FUNCTION guard_declaration_review_command();

    CREATE FUNCTION apply_declaration_review(org uuid,job uuid,expected_revision integer,ids uuid[],decision text,correction jsonb,comment text,bulk boolean)
      RETURNS integer
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      item public.declaration_import; candidate public.declaration_candidate; candidates public.declaration_candidate[];
      instant timestamptz; note text:=NULLIF(btrim(comment),''); changed boolean:=false; has_evidence boolean; flagged boolean;
      total integer; next_revision integer;
    BEGIN
      IF session_user<>'app_rw' OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Current human review authority required' USING ERRCODE='42501'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      IF COALESCE(public.scope_actor_role(org),'') NOT IN ('OWNER_ADMIN','CLERK')
        OR NOT COALESCE(public.property_import_runtime_access(org,job,'read'),false)
        OR NOT COALESCE(public.property_import_runtime_access(org,job,'prepare'),false) THEN
        RAISE EXCEPTION 'Current human review authority required' USING ERRCODE='42501'; END IF;
      SELECT * INTO item FROM public.declaration_import i WHERE i.organization_id=org AND i.id=job FOR UPDATE;
      IF NOT FOUND OR item.status<>'NEEDS_REVIEW' THEN RAISE EXCEPTION 'IMPORT_NOT_REVIEWABLE' USING ERRCODE='22023'; END IF;
      IF expected_revision IS DISTINCT FROM item.draft_revision THEN RAISE EXCEPTION 'STALE_DRAFT_REVISION' USING ERRCODE='40001'; END IF;
      total:=cardinality(ids);
      correction:=NULLIF(correction,'null'::jsonb);
      IF decision IS NULL OR decision NOT IN ('ACCEPTED','CORRECTED','REJECTED','NEEDS_CLARIFICATION')
        OR bulk IS NULL OR total IS NULL OR total<1 OR total>250 OR (NOT bulk AND total<>1)
        OR total<>(SELECT count(DISTINCT value) FROM unnest(ids) value)
        OR (bulk AND decision NOT IN ('ACCEPTED','REJECTED')) OR length(note)>2000
        OR (decision='CORRECTED') IS DISTINCT FROM (correction IS NOT NULL)
        OR (correction IS NOT NULL AND jsonb_typeof(correction)<>'object') THEN
        RAISE EXCEPTION 'INVALID_REVIEW_COMMAND' USING ERRCODE='22023'; END IF;
      -- Candidate identities/versions and evidence remain stable under parent lock.
      FOR candidate IN SELECT c.* FROM public.declaration_candidate c WHERE c.organization_id=org AND c.import_id=job
        AND c.id=ANY(ids) ORDER BY c.id FOR UPDATE
      LOOP
        candidates:=array_append(candidates,candidate);
      END LOOP;
      IF COALESCE(cardinality(candidates),0)<>total THEN RAISE EXCEPTION 'REVIEW_CANDIDATE_NOT_FOUND' USING ERRCODE='22023'; END IF;
      PERFORM 1 FROM public.declaration_candidate_issue q WHERE q.organization_id=org AND q.import_id=job ORDER BY q.id FOR UPDATE;
      FOREACH candidate IN ARRAY candidates LOOP
        has_evidence:=EXISTS(SELECT 1 FROM public.declaration_candidate_evidence e
          WHERE e.organization_id=org AND e.import_id=job AND e.candidate_id=candidate.id);
        flagged:=EXISTS(SELECT 1 FROM public.declaration_candidate_issue q
          WHERE q.organization_id=org AND q.import_id=job AND q.candidate_id=candidate.id AND q.resolution_status='OPEN' AND q.severity IN ('WARNING','BLOCKER'));
        IF decision IN ('ACCEPTED','CORRECTED') AND NOT has_evidence THEN
          RAISE EXCEPTION 'EVIDENCE_REQUIRED' USING ERRCODE='22023'; END IF;
        IF note IS NULL AND (decision='NEEDS_CLARIFICATION' OR (decision='REJECTED' AND
          (candidate.operation='UPDATE' OR candidate.candidate_type IN ('UPDATE','RULE','DOCUMENT_RELATION','MEA_RESCALE','WARNING','BLOCKER') OR flagged))) THEN
          RAISE EXCEPTION 'COMMENT_REQUIRED' USING ERRCODE='22023'; END IF;
        IF bulk AND (candidate.candidate_type<>'UNIT_FIELD' OR candidate.operation<>'CREATE'
          OR candidate.field_key IS NULL OR candidate.field_key NOT IN ('location','area','heated_area','building_part')
          OR NOT has_evidence OR candidate.confidence<.85 OR candidate.confidence>1
          OR jsonb_path_exists(candidate.proposed_value_json,'$.**.alternatives ? (@.type() == "array" && @.size() > 1)')
          OR EXISTS(SELECT 1 FROM public.declaration_candidate_issue q WHERE q.organization_id=org AND q.import_id=job
            AND q.candidate_id=candidate.id AND q.resolution_status='OPEN' AND q.severity<>'INFO')) THEN
          RAISE EXCEPTION 'INDIVIDUAL_REVIEW_REQUIRED' USING ERRCODE='22023'; END IF;
        changed:=changed OR candidate.review_status IS DISTINCT FROM decision
          OR candidate.corrected_value_json IS DISTINCT FROM correction OR NULLIF(btrim(candidate.review_comment),'') IS DISTINCT FROM note;
      END LOOP;
      IF NOT COALESCE(public.property_import_runtime_access(org,job,'prepare'),false) THEN
        RAISE EXCEPTION 'Current human review authority required' USING ERRCODE='42501'; END IF;
      IF NOT changed THEN RETURN item.draft_revision; END IF;
      instant:=clock_timestamp();next_revision:=item.draft_revision+1;
      FOREACH candidate IN ARRAY candidates LOOP
        UPDATE public.declaration_candidate c SET review_status=decision,corrected_value_json=correction,
          review_comment=note,reviewed_by=actor,reviewed_at=instant,candidate_version=c.candidate_version+1,updated_at=instant
          WHERE c.organization_id=org AND c.import_id=job AND c.id=candidate.id;
        INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
          VALUES(org,actor,'declaration_import',job,'declaration_import.candidate_reviewed',jsonb_build_object(
            'import_id',job,'candidate_id',candidate.id,'decision',decision,'revision',next_revision));
      END LOOP;
      -- A new effective value/decision cannot inherit stale managed resolutions.
      -- The caller may run the full Python graph validator in the same transaction;
      -- its raw issue-write authority remains an explicit activation-blocking gap.
      UPDATE public.declaration_candidate_issue q SET resolution_status='OPEN',resolved_by=NULL,resolved_at=NULL,updated_at=instant
        WHERE q.organization_id=org AND q.import_id=job AND q.resolution_status='RESOLVED' AND q.code IN (
          'CONFLICTING_ALTERNATIVES','CONFLICTING_FACT_ALTERNATIVES','MEA_DECLARED_DENOMINATOR_INVALID','MIXED_MEA_DENOMINATOR',
          'MEA_TOTAL_INVALID','DUPLICATE_UNIT_NUMBER','MEA_VALUE_TYPE_INVALID','DUPLICATE_RULE_SUBJECT',
          'ALLOCATION_TOTAL_INVALID','ALLOCATION_SHARE_TYPE_INVALID');
      UPDATE public.declaration_import SET draft_revision=next_revision,updated_at=instant WHERE organization_id=org AND id=job;
      IF NOT COALESCE(public.property_import_runtime_access(org,job,'prepare'),false) THEN
        RAISE EXCEPTION 'Current human review authority required' USING ERRCODE='42501'; END IF;
      RETURN next_revision;
    END $$;
    REVOKE ALL ON FUNCTION apply_declaration_review(uuid,uuid,integer,uuid[],text,jsonb,text,boolean) FROM PUBLIC,app_worker;
    GRANT EXECUTE ON FUNCTION apply_declaration_review(uuid,uuid,integer,uuid[],text,jsonb,text,boolean) TO app_rw;
    """)


def downgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain atomic declaration reviews'; END IF; END $$;
      DROP TRIGGER b_guard_review_command ON declaration_import;
      DROP TRIGGER b_guard_review_command ON declaration_candidate;
      DROP TRIGGER b_guard_review_command ON audit_event;
      DROP FUNCTION guard_declaration_review_command(),apply_declaration_review(uuid,uuid,integer,uuid[],text,jsonb,text,boolean);
    """)
