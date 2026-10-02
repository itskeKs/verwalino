"""Integrate complete source authority and safe Q&A budget/cancellation phases.

Revision ID: 0054
Revises: 0053
"""
from alembic import op

revision='0054'
down_revision='0053'
branch_labels=None
depends_on=None

PREVIOUS_GUARD = """
    CREATE OR REPLACE FUNCTION guard_knowledge_principal() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE actor uuid := NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      live boolean;
    BEGIN
      IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Knowledge organization mismatch' USING ERRCODE='42501'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
      live := public.scope_actor_role(NEW.organization_id) IS NOT NULL;
      IF TG_OP='INSERT' THEN
        IF actor IS NULL OR NEW.initiated_by IS DISTINCT FROM actor OR NOT live THEN
          RAISE EXCEPTION 'Knowledge reservation requires live actor' USING ERRCODE='42501'; END IF;
      ELSE
        IF NEW.initiated_by IS DISTINCT FROM OLD.initiated_by THEN
          RAISE EXCEPTION 'Knowledge actor is immutable' USING ERRCODE='23514'; END IF;
        IF NEW.status='EXPIRED' THEN
          IF NOT live OR OLD.reservation_expires_at > clock_timestamp()
            OR NEW.outcome IS DISTINCT FROM 'RESERVATION_EXPIRED'
            OR NEW.error_code IS DISTINCT FROM 'RESERVATION_EXPIRED'
            OR NEW.estimated_cost_usd<>0 OR NEW.input_tokens<>0 OR NEW.output_tokens<>0
            OR NEW.latency_ms IS NOT NULL THEN
            RAISE EXCEPTION 'Knowledge reservation cannot expire' USING ERRCODE='42501'; END IF;
        ELSE
          IF actor IS NULL OR actor IS DISTINCT FROM OLD.initiated_by THEN
            RAISE EXCEPTION 'Knowledge finalization actor mismatch' USING ERRCODE='42501'; END IF;
          IF NOT live OR NEW.error_code='QA_PERMISSION_CHANGED' OR NEW.outcome='QA_PERMISSION_CHANGED' THEN
            IF live OR NEW.status<>'FINALIZED'
              OR NEW.outcome IS DISTINCT FROM 'QA_PERMISSION_CHANGED'
              OR NEW.error_code IS DISTINCT FROM 'QA_PERMISSION_CHANGED'
              OR NEW.estimated_cost_usd IS DISTINCT FROM OLD.reserved_cost_usd
              OR NEW.input_tokens<>0 OR NEW.output_tokens<>0 OR NEW.latency_ms IS NOT NULL THEN
              RAISE EXCEPTION 'Knowledge permission cancellation metadata invalid' USING ERRCODE='42501'; END IF;
            INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
              VALUES(NEW.organization_id,actor,'property_knowledge_query_run',NEW.id,'knowledge.permission_cancelled',
                jsonb_build_object('actor_user_id',actor,'reason','QA_PERMISSION_CHANGED'));
          END IF;
        END IF;
      END IF;
      RETURN NEW;
    END $$;
"""

PREVIOUS_CANCEL = """
    CREATE OR REPLACE FUNCTION cancel_unauthorized_knowledge(org uuid, run uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE item public.property_knowledge_query_run;
      actor uuid := NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid OR actor IS NULL THEN
        RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      SELECT * INTO item FROM public.property_knowledge_query_run WHERE organization_id=org AND id=run;
      IF NOT FOUND OR item.initiated_by IS DISTINCT FROM actor OR item.status<>'RESERVED'
        OR public.scope_actor_role(org) IS NOT NULL THEN RETURN false; END IF;
      PERFORM public.lock_knowledge_budget(org,item.budget_window_start);
      UPDATE public.property_knowledge_query_run SET status='FINALIZED',outcome='QA_PERMISSION_CHANGED',
        error_code='QA_PERMISSION_CHANGED',estimated_cost_usd=reserved_cost_usd,
        input_tokens=0,output_tokens=0,latency_ms=NULL,finalized_at=clock_timestamp()
        WHERE organization_id=org AND id=run AND status='RESERVED';
      RETURN FOUND;
    END $$;
"""

GUARD = """
    CREATE OR REPLACE FUNCTION guard_knowledge_principal() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid := NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      live boolean; authorized boolean;
    BEGIN
      IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Knowledge organization mismatch' USING ERRCODE='42501'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
      live := public.scope_actor_role(NEW.organization_id) IS NOT NULL;
      authorized:=live AND public.knowledge_query_actor_access(NEW.organization_id,actor,NEW.property_id);
      IF TG_OP='INSERT' THEN
        IF actor IS NULL OR NEW.initiated_by IS DISTINCT FROM actor OR NOT authorized THEN
          RAISE EXCEPTION 'Knowledge reservation requires live actor' USING ERRCODE='42501'; END IF;
      ELSE
        IF NEW.initiated_by IS DISTINCT FROM OLD.initiated_by THEN
          RAISE EXCEPTION 'Knowledge actor is immutable' USING ERRCODE='23514'; END IF;
        IF NEW.status='EXPIRED' THEN
          IF NOT live OR OLD.reservation_expires_at > clock_timestamp()
            OR NEW.outcome IS DISTINCT FROM 'RESERVATION_EXPIRED'
            OR NEW.error_code IS DISTINCT FROM 'RESERVATION_EXPIRED'
            OR NEW.estimated_cost_usd<>0 OR NEW.input_tokens<>0 OR NEW.output_tokens<>0
            OR NEW.latency_ms IS NOT NULL THEN
            RAISE EXCEPTION 'Knowledge reservation cannot expire' USING ERRCODE='42501'; END IF;
        ELSE
          IF actor IS NULL OR actor IS DISTINCT FROM OLD.initiated_by THEN
            RAISE EXCEPTION 'Knowledge finalization actor mismatch' USING ERRCODE='42501'; END IF;
          IF NOT authorized OR NEW.error_code='QA_PERMISSION_CHANGED' OR NEW.outcome='QA_PERMISSION_CHANGED' THEN
            IF authorized OR NEW.status<>'FINALIZED'
              OR NEW.outcome IS DISTINCT FROM 'QA_PERMISSION_CHANGED'
              OR NEW.error_code IS DISTINCT FROM 'QA_PERMISSION_CHANGED'
              OR NEW.estimated_cost_usd IS DISTINCT FROM OLD.reserved_cost_usd
              OR NEW.input_tokens<>0 OR NEW.output_tokens<>0 OR NEW.latency_ms IS NOT NULL THEN
              RAISE EXCEPTION 'Knowledge permission cancellation metadata invalid' USING ERRCODE='42501'; END IF;
            INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
              VALUES(NEW.organization_id,actor,'property_knowledge_query_run',NEW.id,'knowledge.permission_cancelled',
                jsonb_build_object('actor_user_id',actor,'reason','QA_PERMISSION_CHANGED'));
          END IF;
        END IF;
      END IF;
      RETURN NEW;
    END $$;
"""

CANCEL = """
    CREATE OR REPLACE FUNCTION cancel_unauthorized_knowledge(org uuid, run uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE item public.property_knowledge_query_run;
      actor uuid := NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid OR actor IS NULL THEN
        RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      SELECT * INTO item FROM public.property_knowledge_query_run WHERE organization_id=org AND id=run;
      IF NOT FOUND OR item.initiated_by IS DISTINCT FROM actor OR item.status<>'RESERVED'
        OR public.knowledge_query_actor_access(org,actor,item.property_id) THEN RETURN false; END IF;
      PERFORM public.lock_knowledge_budget(org,item.budget_window_start);
      UPDATE public.property_knowledge_query_run SET status='FINALIZED',outcome='QA_PERMISSION_CHANGED',
        error_code='QA_PERMISSION_CHANGED',estimated_cost_usd=reserved_cost_usd,
        input_tokens=0,output_tokens=0,latency_ms=NULL,finalized_at=clock_timestamp()
        WHERE organization_id=org AND id=run AND status='RESERVED';
      RETURN FOUND;
    END $$;
"""

PREVIOUS_SOURCE_ACCESS = """
    CREATE OR REPLACE FUNCTION property_source_actor_access(org uuid, actor uuid, kind text, resource uuid, capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE property uuid; job uuid; document uuid; rule uuid; other uuid; subject uuid; subject_kind text;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','prepare','approve','export')
        OR public.property_source_actor_role(org,actor) IS NULL THEN RETURN false; END IF;
      CASE kind
        WHEN 'property' THEN
          RETURN public.property_data_property_actor_access(org,actor,resource,capability);
        WHEN 'property_document' THEN
          RETURN public.property_source_document_actor_access(org,actor,resource,capability);
        WHEN 'declaration_import' THEN
          RETURN public.property_source_import_actor_access(org,actor,resource,capability);
        WHEN 'unit' THEN SELECT u.property_id INTO property FROM public.unit u WHERE u.organization_id=org AND u.id=resource;
        WHEN 'declaration_import_document' THEN SELECT l.import_id INTO job FROM public.declaration_import_document l WHERE l.organization_id=org AND l.id=resource;
        WHEN 'declaration_candidate' THEN SELECT c.import_id INTO job FROM public.declaration_candidate c WHERE c.organization_id=org AND c.id=resource;
        WHEN 'declaration_candidate_evidence' THEN SELECT e.import_id INTO job FROM public.declaration_candidate_evidence e WHERE e.organization_id=org AND e.id=resource;
        WHEN 'declaration_candidate_issue' THEN SELECT i.import_id INTO job FROM public.declaration_candidate_issue i WHERE i.organization_id=org AND i.id=resource;
        WHEN 'declaration_extraction_run' THEN SELECT r.import_id INTO job FROM public.declaration_extraction_run r WHERE r.organization_id=org AND r.id=resource;
        WHEN 'declaration_queue_request' THEN SELECT q.import_id INTO job FROM public.declaration_queue_request q WHERE q.organization_id=org AND q.id=resource;
        WHEN 'declaration_import_pass' THEN SELECT r.import_id INTO job FROM public.declaration_import_pass p
          JOIN public.declaration_extraction_run r ON r.organization_id=p.organization_id AND r.id=p.declaration_extraction_run_id
          WHERE p.organization_id=org AND p.id=resource;
        WHEN 'property_knowledge_query_run' THEN SELECT q.property_id INTO property FROM public.property_knowledge_query_run q WHERE q.organization_id=org AND q.id=resource;
        WHEN 'property_legal_reference' THEN SELECT l.property_id,l.source_import_id INTO property,job FROM public.property_legal_reference l WHERE l.organization_id=org AND l.id=resource;
        WHEN 'property_assignment' THEN SELECT a.property_id,a.source_import_id INTO property,job FROM public.property_assignment a WHERE a.organization_id=org AND a.id=resource;
        WHEN 'approved_fact_source' THEN
          SELECT f.property_id,f.source_import_id,f.property_document_id,f.subject_id,f.subject_kind
            INTO property,job,document,subject,subject_kind FROM public.approved_fact_source f WHERE f.organization_id=org AND f.id=resource;
          IF NOT FOUND OR NOT ((subject_kind='PROPERTY' AND subject=property) OR (subject_kind='UNIT' AND EXISTS(
            SELECT 1 FROM public.unit u WHERE u.organization_id=org AND u.property_id=property AND u.id=subject))) THEN RETURN false; END IF;
        WHEN 'property_rule' THEN
          SELECT r.property_id,r.source_import_id INTO property,job FROM public.property_rule r WHERE r.organization_id=org AND r.id=resource;
          IF EXISTS(SELECT 1 FROM public.property_rule_source s WHERE s.organization_id=org AND s.property_rule_id=resource
            AND NOT public.property_source_document_actor_access(org,actor,s.property_document_id,capability)) THEN RETURN false; END IF;
        WHEN 'property_rule_source' THEN
          SELECT s.property_id,s.property_document_id,s.property_rule_id INTO property,document,rule FROM public.property_rule_source s WHERE s.organization_id=org AND s.id=resource;
          IF NOT public.property_source_actor_access(org,actor,'property_rule',rule,capability) THEN RETURN false; END IF;
        WHEN 'property_rule_relation' THEN
          SELECT r.property_id,r.source_import_id,r.source_rule_id,r.target_rule_id INTO property,job,rule,other FROM public.property_rule_relation r WHERE r.organization_id=org AND r.id=resource;
          IF NOT public.property_source_actor_access(org,actor,'property_rule',rule,capability)
            OR NOT public.property_source_actor_access(org,actor,'property_rule',other,capability) THEN RETURN false; END IF;
        WHEN 'property_document_relation' THEN
          SELECT r.property_id,r.source_import_id,r.source_document_id,r.target_document_id INTO property,job,document,other FROM public.property_document_relation r WHERE r.organization_id=org AND r.id=resource;
          IF NOT public.property_source_document_actor_access(org,actor,other,capability) THEN RETURN false; END IF;
        ELSE RETURN false;
      END CASE;
      IF property IS NULL AND job IS NULL THEN RETURN false; END IF;
      IF property IS NOT NULL AND NOT public.property_data_property_actor_access(org,actor,property,capability) THEN RETURN false; END IF;
      IF job IS NOT NULL THEN
        -- Derived rows may never publish a different import audience, even when
        -- the current actor happens to hold both grants.
        IF property IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.declaration_import i WHERE i.organization_id=org AND i.id=job
          AND COALESCE(i.applied_property_id,i.target_property_id)=property) THEN RETURN false; END IF;
        IF NOT public.property_source_import_actor_access(org,actor,job,capability) THEN RETURN false; END IF;
      END IF;
      IF document IS NOT NULL AND NOT public.property_source_document_actor_access(org,actor,document,capability) THEN RETURN false; END IF;
      RETURN true;
    END $$;
"""

SOURCE_ACCESS = """
    CREATE OR REPLACE FUNCTION property_source_actor_access(org uuid, actor uuid, kind text, resource uuid, capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE property uuid; job uuid; document uuid; rule uuid; other uuid; subject uuid; subject_kind text;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','prepare','approve','export')
        OR public.property_source_actor_role(org,actor) IS NULL THEN RETURN false; END IF;
      CASE kind
        WHEN 'property' THEN
          RETURN public.property_data_property_actor_access(org,actor,resource,capability);
        WHEN 'property_document' THEN
          RETURN public.property_source_document_actor_access(org,actor,resource,capability);
        WHEN 'declaration_import' THEN
          RETURN public.property_source_import_actor_access(org,actor,resource,capability);
        WHEN 'unit' THEN SELECT u.property_id INTO property FROM public.unit u WHERE u.organization_id=org AND u.id=resource;
        WHEN 'declaration_import_document' THEN SELECT l.import_id INTO job FROM public.declaration_import_document l WHERE l.organization_id=org AND l.id=resource;
        WHEN 'declaration_candidate' THEN SELECT c.import_id INTO job FROM public.declaration_candidate c WHERE c.organization_id=org AND c.id=resource;
        WHEN 'declaration_candidate_evidence' THEN SELECT e.import_id INTO job FROM public.declaration_candidate_evidence e WHERE e.organization_id=org AND e.id=resource;
        WHEN 'declaration_candidate_issue' THEN SELECT i.import_id INTO job FROM public.declaration_candidate_issue i WHERE i.organization_id=org AND i.id=resource;
        WHEN 'declaration_extraction_run' THEN SELECT r.import_id INTO job FROM public.declaration_extraction_run r WHERE r.organization_id=org AND r.id=resource;
        WHEN 'declaration_queue_request' THEN SELECT q.import_id INTO job FROM public.declaration_queue_request q WHERE q.organization_id=org AND q.id=resource;
        WHEN 'declaration_import_pass' THEN SELECT r.import_id INTO job FROM public.declaration_import_pass p
          JOIN public.declaration_extraction_run r ON r.organization_id=p.organization_id AND r.id=p.declaration_extraction_run_id
          WHERE p.organization_id=org AND p.id=resource;
        WHEN 'property_knowledge_query_run' THEN SELECT q.property_id INTO property FROM public.property_knowledge_query_run q WHERE q.organization_id=org AND q.id=resource;
        WHEN 'property_legal_reference' THEN SELECT l.property_id,l.source_import_id INTO property,job FROM public.property_legal_reference l WHERE l.organization_id=org AND l.id=resource;
        WHEN 'property_assignment' THEN SELECT a.property_id,a.source_import_id INTO property,job FROM public.property_assignment a WHERE a.organization_id=org AND a.id=resource;
        WHEN 'approved_fact_source' THEN
          SELECT f.property_id,f.source_import_id,f.property_document_id,f.subject_id,f.subject_kind
            INTO property,job,document,subject,subject_kind FROM public.approved_fact_source f WHERE f.organization_id=org AND f.id=resource;
          IF NOT FOUND OR NOT ((subject_kind='PROPERTY' AND subject=property) OR (subject_kind='UNIT' AND EXISTS(
            SELECT 1 FROM public.unit u WHERE u.organization_id=org AND u.property_id=property AND u.id=subject))
            OR (subject_kind='RULE' AND EXISTS(SELECT 1 FROM public.property_rule s WHERE s.organization_id=org AND s.property_id=property AND s.id=subject))
            OR (subject_kind='LEGAL_REFERENCE' AND EXISTS(SELECT 1 FROM public.property_legal_reference s WHERE s.organization_id=org AND s.property_id=property AND s.id=subject))
            OR (subject_kind='ASSIGNMENT' AND EXISTS(SELECT 1 FROM public.property_assignment s WHERE s.organization_id=org AND s.property_id=property AND s.id=subject))
            OR (subject_kind='DOCUMENT_RELATION' AND EXISTS(SELECT 1 FROM public.property_document_relation s WHERE s.organization_id=org AND s.property_id=property AND s.id=subject))) THEN RETURN false; END IF;
          IF subject_kind IN ('RULE','LEGAL_REFERENCE','ASSIGNMENT','DOCUMENT_RELATION') AND NOT public.property_source_actor_access(org,actor,
            CASE subject_kind WHEN 'RULE' THEN 'property_rule' WHEN 'LEGAL_REFERENCE' THEN 'property_legal_reference' WHEN 'ASSIGNMENT' THEN 'property_assignment' ELSE 'property_document_relation' END,
            subject,capability) THEN RETURN false; END IF;
        WHEN 'property_rule' THEN
          SELECT r.property_id,r.source_import_id INTO property,job FROM public.property_rule r WHERE r.organization_id=org AND r.id=resource;
          IF EXISTS(SELECT 1 FROM public.property_rule_source s WHERE s.organization_id=org AND s.property_rule_id=resource
            AND NOT public.property_source_document_actor_access(org,actor,s.property_document_id,capability)) THEN RETURN false; END IF;
        WHEN 'property_rule_source' THEN
          SELECT s.property_id,s.property_document_id,s.property_rule_id INTO property,document,rule FROM public.property_rule_source s WHERE s.organization_id=org AND s.id=resource;
          IF NOT public.property_source_actor_access(org,actor,'property_rule',rule,capability) THEN RETURN false; END IF;
        WHEN 'property_rule_relation' THEN
          SELECT r.property_id,r.source_import_id,r.source_rule_id,r.target_rule_id INTO property,job,rule,other FROM public.property_rule_relation r WHERE r.organization_id=org AND r.id=resource;
          IF NOT public.property_source_actor_access(org,actor,'property_rule',rule,capability)
            OR NOT public.property_source_actor_access(org,actor,'property_rule',other,capability) THEN RETURN false; END IF;
        WHEN 'property_document_relation' THEN
          SELECT r.property_id,r.source_import_id,r.source_document_id,r.target_document_id INTO property,job,document,other FROM public.property_document_relation r WHERE r.organization_id=org AND r.id=resource;
          IF NOT public.property_source_document_actor_access(org,actor,other,capability) THEN RETURN false; END IF;
        ELSE RETURN false;
      END CASE;
      IF property IS NULL AND job IS NULL THEN RETURN false; END IF;
      IF property IS NOT NULL AND NOT public.property_data_property_actor_access(org,actor,property,capability) THEN RETURN false; END IF;
      IF job IS NOT NULL THEN
        -- Derived rows may never publish a different import audience, even when
        -- the current actor happens to hold both grants.
        IF property IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.declaration_import i WHERE i.organization_id=org AND i.id=job
          AND COALESCE(i.applied_property_id,i.target_property_id)=property) THEN RETURN false; END IF;
        IF NOT public.property_source_import_actor_access(org,actor,job,capability) THEN RETURN false; END IF;
      END IF;
      IF document IS NOT NULL AND NOT public.property_source_document_actor_access(org,actor,document,capability) THEN RETURN false; END IF;
      RETURN true;
    END $$;
"""

BUDGET_ADMISSION = """
    CREATE TABLE knowledge_budget_policy (
      organization_id uuid PRIMARY KEY REFERENCES organization(id),
      max_cost_usd numeric NOT NULL CHECK(max_cost_usd>0 AND max_cost_usd::text NOT IN ('NaN','Infinity','-Infinity')),
      window_minutes integer NOT NULL CHECK(window_minutes>0),
      reservation_seconds integer NOT NULL CHECK(reservation_seconds>0),
      changed_by uuid NOT NULL, reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 2000),
      changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      FOREIGN KEY(organization_id,changed_by) REFERENCES "user"(organization_id,id)
    );
    ALTER TABLE knowledge_budget_policy ENABLE ROW LEVEL SECURITY;
    ALTER TABLE knowledge_budget_policy FORCE ROW LEVEL SECURITY;
    CREATE POLICY knowledge_budget_policy_read ON knowledge_budget_policy FOR SELECT USING(
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND scope_actor_role(organization_id)='OWNER_ADMIN');
    CREATE POLICY knowledge_budget_policy_insert ON knowledge_budget_policy FOR INSERT WITH CHECK(false);
    REVOKE ALL ON knowledge_budget_policy FROM PUBLIC,app_rw;
    CREATE TRIGGER lock_knowledge_budget_policy BEFORE INSERT OR UPDATE OR DELETE ON knowledge_budget_policy
      FOR EACH STATEMENT EXECUTE FUNCTION lock_property_source_security_change();
    CREATE FUNCTION guard_knowledge_budget_policy() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Knowledge budget policy evidence cannot be deleted'; END IF;
      IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR NEW.changed_by IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid
        OR public.scope_actor_role(NEW.organization_id) IS DISTINCT FROM 'OWNER_ADMIN' THEN
        RAISE EXCEPTION 'Current knowledge budget administrator required' USING ERRCODE='42501'; END IF;
      IF TG_OP='UPDATE' AND NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
        RAISE EXCEPTION 'Knowledge budget organization is immutable'; END IF;
      NEW.reason:=btrim(NEW.reason); NEW.changed_at:=clock_timestamp();
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(NEW.organization_id,NEW.changed_by,'knowledge_budget_policy',NEW.organization_id,'knowledge.budget_policy_changed',
          jsonb_build_object('max_cost_usd',NEW.max_cost_usd,'window_minutes',NEW.window_minutes,
            'reservation_seconds',NEW.reservation_seconds,'reason',NEW.reason));
      RETURN NEW;
    END $$;
    CREATE TRIGGER guard_knowledge_budget_policy BEFORE INSERT OR UPDATE OR DELETE ON knowledge_budget_policy
      FOR EACH ROW EXECUTE FUNCTION guard_knowledge_budget_policy();
    REVOKE ALL ON FUNCTION guard_knowledge_budget_policy() FROM PUBLIC,app_rw;

    -- This trigger is deliberately INVOKER. Nested SQL from the trusted definer
    -- runs as its owner; direct app SQL cannot impersonate that role using GUCs.
    CREATE FUNCTION guard_knowledge_budget_admission() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF public.property_source_security_mode(NEW.organization_id) IS DISTINCT FROM 'PREPARATION'
        AND current_user IS DISTINCT FROM pg_get_userbyid((SELECT p.proowner FROM pg_proc p
          WHERE p.oid='public.reserve_knowledge_query(uuid,uuid,text,text,numeric)'::regprocedure)) THEN
        RAISE EXCEPTION 'Knowledge reservation command required' USING ERRCODE='42501'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER guard_knowledge_budget_admission BEFORE INSERT ON property_knowledge_query_run
      FOR EACH ROW EXECUTE FUNCTION guard_knowledge_budget_admission();
    REVOKE ALL ON FUNCTION guard_knowledge_budget_admission() FROM PUBLIC,app_rw;

    CREATE FUNCTION reserve_knowledge_query(org uuid,property uuid,provider_name text,model_name text,amount numeric)
      RETURNS TABLE(run_id uuid,budget_window_start timestamptz,reserved_cost_usd numeric)
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE policy public.knowledge_budget_policy; window_start timestamptz; moment timestamptz;
      actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      IF public.property_source_security_mode(org) IS DISTINCT FROM 'ENFORCED'
        OR NOT public.knowledge_query_source_access(org,property) THEN
        RAISE EXCEPTION 'Knowledge source not accessible' USING ERRCODE='42501'; END IF;
      SELECT * INTO policy FROM public.knowledge_budget_policy WHERE organization_id=org;
      IF NOT FOUND THEN RAISE EXCEPTION 'Knowledge budget policy unavailable' USING ERRCODE='42501'; END IF;
      IF amount IS NULL OR amount<0 OR amount::text IN ('NaN','Infinity','-Infinity') THEN
        RAISE EXCEPTION 'Invalid knowledge budget request' USING ERRCODE='22023'; END IF;
      -- Round upward to the ledger precision before admission, never below the
      -- requested reservation. The database owns both window and expiration.
      amount:=ceil(amount*1000000)/1000000;
      moment:=clock_timestamp();
      window_start:=to_timestamp(floor(extract(epoch FROM moment)/(policy.window_minutes::numeric*60))*(policy.window_minutes::numeric*60));
      IF NOT public.knowledge_budget_available(org,property,window_start,amount,policy.max_cost_usd) THEN RETURN; END IF;
      RETURN QUERY INSERT INTO public.property_knowledge_query_run AS q
        (organization_id,property_id,initiated_by,budget_window_start,provider,model,reserved_cost_usd,reservation_expires_at)
        VALUES(org,property,actor,window_start,left(provider_name,120),left(model_name,120),amount,
          clock_timestamp()+make_interval(secs=>policy.reservation_seconds))
        RETURNING q.id,q.budget_window_start,q.reserved_cost_usd;
    END $$;
    CREATE FUNCTION reserve_knowledge_query_compat(org uuid,property uuid,provider_name text,model_name text,amount numeric,
        window_start timestamptz,cap numeric,expires_at timestamptz)
      RETURNS TABLE(run_id uuid,budget_window_start timestamptz,reserved_cost_usd numeric)
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF public.property_source_security_mode(org) IS DISTINCT FROM 'PREPARATION' THEN
        RAISE EXCEPTION 'Knowledge compatibility mode required' USING ERRCODE='42501'; END IF;
      amount:=ceil(amount*1000000)/1000000;
      IF NOT public.knowledge_budget_available(org,property,window_start,amount,cap) THEN RETURN; END IF;
      RETURN QUERY INSERT INTO public.property_knowledge_query_run AS q
        (organization_id,property_id,initiated_by,budget_window_start,provider,model,reserved_cost_usd,reservation_expires_at)
        VALUES(org,property,NULLIF(current_setting('app.current_user_id',true),'')::uuid,window_start,
          left(provider_name,120),left(model_name,120),amount,expires_at)
        RETURNING q.id,q.budget_window_start,q.reserved_cost_usd;
    END $$;
    REVOKE ALL ON FUNCTION reserve_knowledge_query(uuid,uuid,text,text,numeric),
      reserve_knowledge_query_compat(uuid,uuid,text,text,numeric,timestamptz,numeric,timestamptz) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION reserve_knowledge_query(uuid,uuid,text,text,numeric),
      reserve_knowledge_query_compat(uuid,uuid,text,text,numeric,timestamptz,numeric,timestamptz) TO app_rw;
"""

def upgrade():
    op.execute("""
    LOCK TABLE organization,"user",property_knowledge_query_run IN ACCESS EXCLUSIVE MODE NOWAIT;
    CREATE FUNCTION knowledge_query_actor_access(org uuid,actor uuid,property uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; source record;
    BEGIN
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL OR public.property_source_actor_role(org,actor) IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN EXISTS(SELECT 1 FROM public.property p WHERE p.organization_id=org AND p.id=property); END IF;
      IF mode='PAUSED' OR NOT public.property_source_actor_access(org,actor,'property',property,'read') THEN RETURN false; END IF;
      -- Enumerate the expected graph with the trusted definer. Ordinary RLS
      -- filtered hits cannot establish completeness, including historical rows.
      FOR source IN
        SELECT 'property_document'::text kind,d.id FROM public.property_document d WHERE d.organization_id=org AND d.property_id=property
        UNION ALL SELECT 'approved_fact_source',s.id FROM public.approved_fact_source s WHERE s.organization_id=org AND s.property_id=property
        UNION ALL SELECT 'property_rule',s.id FROM public.property_rule s WHERE s.organization_id=org AND s.property_id=property
        UNION ALL SELECT 'property_rule_source',s.id FROM public.property_rule_source s WHERE s.organization_id=org AND s.property_id=property
        UNION ALL SELECT 'property_rule_relation',s.id FROM public.property_rule_relation s WHERE s.organization_id=org AND s.property_id=property
        UNION ALL SELECT 'property_document_relation',s.id FROM public.property_document_relation s WHERE s.organization_id=org AND s.property_id=property
        UNION ALL SELECT 'property_legal_reference',s.id FROM public.property_legal_reference s WHERE s.organization_id=org AND s.property_id=property
        UNION ALL SELECT 'property_assignment',s.id FROM public.property_assignment s WHERE s.organization_id=org AND s.property_id=property
        UNION ALL SELECT 'declaration_import',i.id FROM public.declaration_import i WHERE i.organization_id=org AND i.applied_property_id=property
      LOOP
        IF NOT public.property_source_actor_access(org,actor,source.kind,source.id,'read') THEN RETURN false; END IF;
      END LOOP;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION knowledge_query_actor_access(uuid,uuid,uuid) FROM PUBLIC,app_rw;

    CREATE FUNCTION knowledge_query_source_access(org uuid,property uuid) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT public.knowledge_query_actor_access(org,NULLIF(current_setting('app.current_user_id',true),'')::uuid,property)
    $$;
    CREATE FUNCTION knowledge_query_actor(org uuid,run uuid) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      IF org IS NULL OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid OR actor IS NULL THEN RETURN NULL; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      RETURN (SELECT q.initiated_by FROM public.property_knowledge_query_run q
        WHERE q.organization_id=org AND q.id=run AND q.initiated_by=actor);
    END $$;
    REVOKE ALL ON FUNCTION knowledge_query_source_access(uuid,uuid),knowledge_query_actor(uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION knowledge_query_source_access(uuid,uuid),knowledge_query_actor(uuid,uuid) TO app_rw;

    CREATE FUNCTION knowledge_budget_available(org uuid,property uuid,window_start timestamptz,amount numeric,cap numeric) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE spent numeric;
    BEGIN
      IF NOT public.knowledge_query_source_access(org,property) THEN
        RAISE EXCEPTION 'Knowledge source not accessible' USING ERRCODE='42501'; END IF;
      IF window_start IS NULL OR amount IS NULL OR cap IS NULL OR amount<0 OR cap<0
        OR amount::text IN ('NaN','Infinity','-Infinity') OR cap::text IN ('NaN','Infinity','-Infinity') THEN
        RAISE EXCEPTION 'Invalid knowledge budget request' USING ERRCODE='22023'; END IF;
      PERFORM public.lock_knowledge_budget(org,window_start);
      UPDATE public.property_knowledge_query_run SET status='EXPIRED',finalized_at=clock_timestamp(),
        outcome='RESERVATION_EXPIRED',error_code='RESERVATION_EXPIRED'
        WHERE organization_id=org AND budget_window_start=window_start AND status='RESERVED'
          AND reservation_expires_at<=clock_timestamp();
      SELECT COALESCE(sum(CASE WHEN q.status='FINALIZED' THEN q.estimated_cost_usd ELSE q.reserved_cost_usd END),0)
        INTO spent FROM public.property_knowledge_query_run q WHERE q.organization_id=org
          AND q.budget_window_start=window_start AND q.status IN ('FINALIZED','RESERVED');
      RETURN spent+amount<=cap;
    END $$;
    REVOKE ALL ON FUNCTION knowledge_budget_available(uuid,uuid,timestamptz,numeric,numeric) FROM PUBLIC,app_rw;
    """)
    op.execute(BUDGET_ADMISSION)
    op.execute(SOURCE_ACCESS)
    op.execute(GUARD)
    op.execute(CANCEL)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain enforced knowledge source boundary'; END IF;
      IF EXISTS(SELECT 1 FROM knowledge_budget_policy) THEN RAISE EXCEPTION 'Retain knowledge budget policy evidence'; END IF; END $$""")
    op.execute("""DROP TRIGGER guard_knowledge_budget_admission ON property_knowledge_query_run;
      DROP FUNCTION guard_knowledge_budget_admission(),reserve_knowledge_query(uuid,uuid,text,text,numeric),
        reserve_knowledge_query_compat(uuid,uuid,text,text,numeric,timestamptz,numeric,timestamptz);
      DROP TABLE knowledge_budget_policy;
      DROP FUNCTION guard_knowledge_budget_policy();""")
    op.execute(PREVIOUS_SOURCE_ACCESS)
    op.execute(PREVIOUS_GUARD)
    op.execute('ALTER FUNCTION guard_knowledge_principal() SECURITY INVOKER')
    op.execute(PREVIOUS_CANCEL)
    op.execute("""DROP FUNCTION knowledge_budget_available(uuid,uuid,timestamptz,numeric,numeric),
      knowledge_query_actor(uuid,uuid),knowledge_query_source_access(uuid,uuid),knowledge_query_actor_access(uuid,uuid,uuid)""")
