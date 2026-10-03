"""Complete retained correction impacts using exact trusted computation evidence."""
from alembic import op
from sqlalchemy import text

revision='0116'
down_revision='0115'
branch_labels=None
depends_on=None

COVERAGE_BEFORE="UNION ALL SELECT 'financial_computation_receipt',id,to_jsonb(r) FROM public.financial_computation_receipt r WHERE organization_id=org) evidence"
COVERAGE_AFTER=COVERAGE_BEFORE.replace(') evidence'," UNION ALL SELECT 'allocation_correction_context',id,to_jsonb(r) FROM public.allocation_correction_context r WHERE organization_id=org UNION ALL SELECT 'allocation_correction_completion',id,to_jsonb(r) FROM public.allocation_correction_completion r WHERE organization_id=org) evidence")


def patch_coverage(reverse=False):
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    before,after=(COVERAGE_AFTER,COVERAGE_BEFORE) if reverse else (COVERAGE_BEFORE,COVERAGE_AFTER)
    assert definition.count(before)==1
    op.execute(definition.replace(before,after))


def upgrade():
    op.execute('LOCK TABLE allocation_correction,allocation_correction_impact,allocation_set,settlement_run,economic_plan IN SHARE ROW EXCLUSIVE MODE NOWAIT')
    # INSERT RETURNING must authorize the new row from its typed parent, not
    # look the not-yet-visible child up through a STABLE helper by its own ID.
    op.execute("ALTER POLICY classified_financial_read ON coverage_declaration USING(financial_scope_access(organization_id,CASE WHEN settlement_run_id IS NOT NULL THEN 'settlement_run' ELSE 'economic_plan' END,COALESCE(settlement_run_id,economic_plan_id),'read'))")
    op.execute('''
      CREATE TABLE allocation_correction_context(
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,impact_id uuid NOT NULL,
        context jsonb NOT NULL,parent_digest text NOT NULL,scope_id uuid NOT NULL,
        captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(organization_id,id),UNIQUE(organization_id,impact_id),
        FOREIGN KEY(organization_id,impact_id) REFERENCES allocation_correction_impact(organization_id,id),
        FOREIGN KEY(organization_id,scope_id) REFERENCES accounting_scope(organization_id,id));
      CREATE TABLE allocation_correction_completion(
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,impact_id uuid NOT NULL,
        computation_id uuid NOT NULL,allocation_head_id uuid NOT NULL,actor_id uuid NOT NULL,
        request_key uuid NOT NULL,request_digest text NOT NULL,basis_digest text NOT NULL,basis jsonb NOT NULL,
        reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 2000),source_reviewed boolean NOT NULL,
        completed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(organization_id,id),UNIQUE(organization_id,actor_id,request_key),UNIQUE(organization_id,impact_id,basis_digest),
        FOREIGN KEY(organization_id,impact_id) REFERENCES allocation_correction_impact(organization_id,id),
        FOREIGN KEY(organization_id,computation_id) REFERENCES financial_computation_receipt(organization_id,id),
        FOREIGN KEY(organization_id,allocation_head_id) REFERENCES allocation_set(organization_id,id),
        FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
      CREATE INDEX correction_completion_history ON allocation_correction_completion(organization_id,impact_id,completed_at DESC,id);
    ''')
    for table in ('allocation_correction_context','allocation_correction_completion'):
        op.execute(f'''ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review;
          CREATE POLICY followup_private ON {table} USING(false) WITH CHECK(false);
          CREATE TRIGGER followup_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();''')
    op.execute(CONTEXT_SQL)
    op.execute('''INSERT INTO allocation_correction_context(organization_id,impact_id,context,parent_digest,scope_id)
      SELECT i.organization_id,i.id,correction_parent_context(p.parent),bounded_computation_digest(p.parent),s.scope_id
      FROM allocation_correction_impact i JOIN allocation_correction c ON c.organization_id=i.organization_id AND c.id=i.correction_id
      JOIN allocation_set s ON s.organization_id=c.organization_id AND s.id=c.successor_id
      CROSS JOIN LATERAL (SELECT correction_financial_parent(i.organization_id,CASE WHEN i.settlement_run_id IS NOT NULL THEN 'settlement_run' ELSE 'economic_plan' END,COALESCE(i.settlement_run_id,i.economic_plan_id)) AS parent) p
      WHERE EXISTS(SELECT 1 FROM jsonb_array_elements(c.impact_snapshot->'basis') b
        WHERE b->>'kind'=i.snapshot->>'kind' AND b->>'id'=i.snapshot->>'id'
          AND b->>'parent_digest'=bounded_computation_digest(p.parent) AND (b->>'scope')::uuid=s.scope_id)
        AND EXISTS(SELECT 1 FROM accounting_scope_binding binding WHERE binding.organization_id=i.organization_id AND binding.scope_id=s.scope_id
          AND CASE WHEN i.settlement_run_id IS NOT NULL THEN binding.settlement_run_id=i.settlement_run_id ELSE binding.economic_plan_id=i.economic_plan_id END);
      CREATE TRIGGER capture_correction_context AFTER INSERT ON allocation_correction_impact FOR EACH ROW EXECUTE FUNCTION capture_correction_context();
    ''')
    op.execute(STATUS_SQL)
    op.execute(COMMAND_SQL)
    patch_coverage()


def downgrade():
    patch_coverage(reverse=True)
    op.execute("ALTER POLICY classified_financial_read ON coverage_declaration USING(financial_scope_access(organization_id,'coverage_declaration',id,'read'))")
    op.execute('''LOCK TABLE allocation_correction_context,allocation_correction_completion IN ACCESS EXCLUSIVE MODE NOWAIT;
      DO $$ BEGIN IF EXISTS(SELECT 1 FROM allocation_correction_context) OR EXISTS(SELECT 1 FROM allocation_correction_completion) THEN
        RAISE EXCEPTION 'Retain correction follow-up evidence; repair forward';END IF;END $$;
      DROP TRIGGER capture_correction_context ON allocation_correction_impact;
      DROP TABLE allocation_correction_completion,allocation_correction_context;
      DROP FUNCTION capture_correction_context(),correction_parent_context(jsonb),correction_financial_parent(uuid,text,uuid),
        correction_followup_basis(uuid,uuid,boolean),correction_followup_status(uuid,uuid),
        correction_completion_result(uuid,uuid),complete_correction_followup(uuid,uuid,uuid,text,text,boolean,boolean),
        correction_context_matches(uuid,jsonb,jsonb,uuid);
    ''')


CONTEXT_SQL='''
CREATE FUNCTION correction_financial_parent(org uuid,kind text,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
BEGIN
  IF kind='settlement_run' THEN RETURN (SELECT to_jsonb(r) FROM public.settlement_run r WHERE r.organization_id=org AND r.id=target);
  ELSIF kind='economic_plan' THEN RETURN (SELECT to_jsonb(p) FROM public.economic_plan p WHERE p.organization_id=org AND p.id=target);
  ELSE RAISE EXCEPTION 'Supported financial parent required' USING ERRCODE='22023';END IF;
END $$;
CREATE FUNCTION correction_parent_context(parent jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
  SELECT jsonb_object_agg(key,value) FROM jsonb_each(parent) WHERE key IN ('property_id','management_mandate_id','period_start','period_end',
    'run_type','unit_id','lease_agreement_id','tenant_profile_id','economic_plan_id','plan_year','monthly_due_day')
$$;
CREATE FUNCTION capture_correction_context() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE parent jsonb;receipt public.allocation_correction;scope uuid;
BEGIN
  SELECT * INTO STRICT receipt FROM public.allocation_correction WHERE organization_id=NEW.organization_id AND id=NEW.correction_id;
  parent:=public.correction_financial_parent(NEW.organization_id,NEW.snapshot->>'kind',(NEW.snapshot->>'id')::uuid);
  scope:=public.financial_scope_id(NEW.organization_id,NEW.snapshot->>'kind',(NEW.snapshot->>'id')::uuid);
  IF parent IS NULL OR scope IS NULL OR (NEW.snapshot->>'id')::uuid IS DISTINCT FROM COALESCE(NEW.settlement_run_id,NEW.economic_plan_id)
    OR NEW.snapshot->>'kind' IS DISTINCT FROM (CASE WHEN NEW.settlement_run_id IS NOT NULL THEN 'settlement_run' ELSE 'economic_plan' END)
    OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(receipt.impact_snapshot->'basis') b
    WHERE b->>'kind'=NEW.snapshot->>'kind' AND b->>'id'=NEW.snapshot->>'id'
      AND b->>'parent_digest'=public.bounded_computation_digest(parent) AND (b->>'scope')::uuid=scope) THEN
    RAISE EXCEPTION 'Exact original correction context required' USING ERRCODE='23514';END IF;
  INSERT INTO public.allocation_correction_context(organization_id,impact_id,context,parent_digest,scope_id)
    VALUES(NEW.organization_id,NEW.id,public.correction_parent_context(parent),public.bounded_computation_digest(parent),scope);
  RETURN NULL;
END $$;
CREATE FUNCTION correction_context_matches(org uuid,expected jsonb,actual jsonb,scope uuid) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE target uuid:=(expected->>'economic_plan_id')::uuid;wanted uuid:=(actual->>'economic_plan_id')::uuid;
  seen uuid[]:='{}';parent jsonb;original_context jsonb;
BEGIN
  IF expected IS NOT DISTINCT FROM actual THEN RETURN true;END IF;
  IF expected-'economic_plan_id' IS DISTINCT FROM actual-'economic_plan_id' OR target IS NULL OR wanted IS NULL THEN RETURN false;END IF;
  -- A WEG correction may use the finalized successor of its original plan.
  -- Every hop must preserve the plan's own obligation context and exact scope.
  LOOP
    IF target=ANY(seen) OR cardinality(seen)>=100 THEN RETURN false;END IF;
    seen:=array_append(seen,target);parent:=public.correction_financial_parent(org,'economic_plan',target);
    IF parent IS NULL OR parent->>'status'<>'finalized' OR public.financial_scope_id(org,'economic_plan',target) IS DISTINCT FROM scope
      OR NOT public.financial_scope_access(org,'economic_plan',target,'read') THEN RETURN false;END IF;
    IF original_context IS NULL THEN original_context:=public.correction_parent_context(parent);
    ELSIF public.correction_parent_context(parent) IS DISTINCT FROM original_context THEN RETURN false;END IF;
    IF target=wanted THEN RETURN parent->>'superseded_by_id' IS NULL;END IF;
    target:=(parent->>'superseded_by_id')::uuid;
    IF target IS NULL THEN RETURN false;END IF;
  END LOOP;
END $$;
REVOKE ALL ON FUNCTION correction_financial_parent(uuid,text,uuid),correction_parent_context(jsonb),capture_correction_context() FROM PUBLIC,app_rw,app_worker,app_review;
REVOKE ALL ON FUNCTION correction_context_matches(uuid,jsonb,jsonb,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
'''


STATUS_SQL='''
CREATE FUNCTION correction_followup_basis(org uuid,impact_id uuid,lock_sources boolean) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE impact public.allocation_correction_impact;correction public.allocation_correction;context public.allocation_correction_context;
  head public.allocation_set;next_id uuid;parent jsonb;kind text;target uuid;origin uuid;seen uuid[]:='{}';heads uuid[]:='{}';
  proof public.financial_computation_receipt;state jsonb;coverage public.coverage_declaration;basis jsonb;review_source boolean;
BEGIN
  SELECT * INTO impact FROM public.allocation_correction_impact i WHERE i.organization_id=org AND i.id=impact_id;
  IF impact.id IS NULL THEN RAISE EXCEPTION 'Correction impact inaccessible' USING ERRCODE='42501';END IF;
  PERFORM public.allocation_correction_detail(org,impact.correction_id);
  SELECT * INTO STRICT correction FROM public.allocation_correction WHERE organization_id=org AND id=impact.correction_id;
  SELECT * INTO context FROM public.allocation_correction_context c WHERE c.organization_id=org AND c.impact_id=impact.id;
  IF context.id IS NULL THEN RETURN jsonb_build_object('ready',false,'blocker','Original financial context could not be proven');END IF;
  kind:=impact.snapshot->>'kind';origin:=COALESCE(impact.settlement_run_id,impact.economic_plan_id);target:=origin;
  LOOP
    IF target=ANY(seen) OR cardinality(seen)>=100 THEN RAISE EXCEPTION 'Complete financial successor chain required' USING ERRCODE='23514';END IF;
    seen:=array_append(seen,target);parent:=public.correction_financial_parent(org,kind,target);
    IF parent IS NULL OR NOT public.financial_scope_access(org,kind,target,'read')
      OR NOT public.property_financial_disclosure_access(org,(parent->>'property_id')::uuid,'read') THEN
      RAISE EXCEPTION 'Complete successor evidence inaccessible' USING ERRCODE='42501';END IF;
    IF NOT public.correction_context_matches(org,context.context,public.correction_parent_context(parent),context.scope_id)
      OR public.financial_scope_id(org,kind,target) IS DISTINCT FROM context.scope_id THEN
      RETURN jsonb_build_object('ready',false,'blocker','Financial successor context changed');END IF;
    next_id:=(parent->>'superseded_by_id')::uuid;
    IF next_id IS NULL THEN EXIT;END IF;
    IF parent->>'status'<>'finalized' THEN RETURN jsonb_build_object('ready',false,'blocker','Finalized financial predecessor required');END IF;
    target:=next_id;
  END LOOP;
  IF (impact.snapshot->>'status'='finalized' OR target<>origin) AND (target=origin OR parent->>'status'<>'finalized') THEN
    RETURN jsonb_build_object('ready',false,'blocker','Finalize the financial correction successor');END IF;
  SELECT * INTO head FROM public.allocation_set WHERE organization_id=org AND id=correction.successor_id;
  LOOP
    IF head.id=ANY(heads) OR cardinality(heads)>=100 THEN RAISE EXCEPTION 'Complete allocation successor chain required' USING ERRCODE='23514';END IF;
    heads:=array_append(heads,head.id);
    SELECT id INTO next_id FROM public.allocation_set WHERE organization_id=org AND predecessor_id=head.id;
    IF next_id IS NULL THEN EXIT;END IF;
    SELECT * INTO head FROM public.allocation_set WHERE organization_id=org AND id=next_id;
  END LOOP;
  IF NOT public.allocation_set_access(org,head.id) OR NOT public.invoice_source_access(org,head.source_document_id,'read') THEN
    RAISE EXCEPTION 'Current correction source inaccessible' USING ERRCODE='42501';END IF;
  PERFORM public.require_computation_authority(org,kind,target,CASE WHEN parent->>'status'='finalized' THEN 'finalize' ELSE 'compute' END);
  IF lock_sources THEN PERFORM public.computation_input_basis(org,kind,target,true);END IF;
  SELECT r.* INTO proof FROM public.financial_computation_receipt r WHERE r.organization_id=org
    AND CASE kind WHEN 'settlement_run' THEN r.settlement_run_id=target ELSE r.economic_plan_id=target END
    ORDER BY r.receipt_number DESC LIMIT 1;
  IF proof.id IS NULL OR proof.recorded_at<=correction.confirmed_at THEN
    RETURN jsonb_build_object('ready',false,'blocker','Recompute with current inputs after the allocation correction');END IF;
  BEGIN state:=public.financial_computation_state(org,kind,target);
    EXCEPTION WHEN check_violation THEN RETURN jsonb_build_object('ready',false,'blocker','Refresh financial computation and coverage');END;
  IF state IS DISTINCT FROM proof.state THEN RETURN jsonb_build_object('ready',false,'blocker','Financial computation is no longer current');END IF;
  review_source:=COALESCE((impact.snapshot#>>'{effects,source_evidence_review}')::boolean,false);
  SELECT * INTO STRICT coverage FROM public.coverage_declaration WHERE organization_id=org AND id=proof.coverage_id;
  IF review_source AND NOT coverage.source_document_ids ? head.source_document_id::text THEN
    RETURN jsonb_build_object('ready',false,'blocker','Review the current correction source in the consumed coverage declaration');END IF;
  basis:=jsonb_build_object('version',1,'impact_id',impact.id,'context_id',context.id,'allocation_head_id',head.id,
    'source_document_id',head.source_document_id,'computation_id',proof.id,'kind',kind,'target_id',target,'chain',seen,
    'state',state,'source_review_required',review_source);
  RETURN jsonb_build_object('ready',true,'blocker',NULL,'basis',basis,'basis_digest',public.bounded_computation_digest(basis));
END $$;
REVOKE ALL ON FUNCTION correction_followup_basis(uuid,uuid,boolean) FROM PUBLIC,app_rw,app_worker,app_review;
'''


COMMAND_SQL='''
CREATE FUNCTION correction_completion_result(org uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE receipt public.allocation_correction_completion;impact public.allocation_correction_impact;parent jsonb;
BEGIN
  SELECT * INTO receipt FROM public.allocation_correction_completion WHERE organization_id=org AND id=target;
  SELECT * INTO impact FROM public.allocation_correction_impact WHERE organization_id=org AND id=receipt.impact_id;
  IF receipt.id IS NULL THEN RAISE EXCEPTION 'Completion inaccessible' USING ERRCODE='42501';END IF;
  PERFORM public.allocation_correction_detail(org,impact.correction_id);
  parent:=public.correction_financial_parent(org,receipt.basis->>'kind',(receipt.basis->>'target_id')::uuid);
  IF parent IS NULL OR NOT public.financial_scope_access(org,receipt.basis->>'kind',(receipt.basis->>'target_id')::uuid,'read')
    OR NOT public.property_financial_disclosure_access(org,(parent->>'property_id')::uuid,'read')
    OR NOT public.allocation_set_access(org,receipt.allocation_head_id) THEN
    RAISE EXCEPTION 'Retained completion evidence inaccessible' USING ERRCODE='42501';END IF;
  RETURN jsonb_build_object('id',receipt.id,'impact_id',receipt.impact_id,'computation_id',receipt.computation_id,
    'allocation_head_id',receipt.allocation_head_id,'kind',receipt.basis->>'kind','target_id',receipt.basis->>'target_id',
    'source_document_id',receipt.basis->>'source_document_id','basis_digest',receipt.basis_digest,
    'source_reviewed',receipt.source_reviewed,'reason',receipt.reason,'actor_id',receipt.actor_id,'completed_at',receipt.completed_at);
END $$;
CREATE FUNCTION correction_followup_status(org uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE current_basis jsonb;receipt public.allocation_correction_completion;history jsonb;
BEGIN
  IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501';END IF;
  PERFORM public.lock_intake(org);
  current_basis:=public.correction_followup_basis(org,target,false);
  SELECT * INTO receipt FROM public.allocation_correction_completion WHERE organization_id=org AND impact_id=target ORDER BY completed_at DESC,id DESC LIMIT 1;
  SELECT COALESCE(jsonb_agg(public.correction_completion_result(org,id) ORDER BY completed_at DESC,id DESC),'[]') INTO history
    FROM (SELECT id,completed_at FROM public.allocation_correction_completion WHERE organization_id=org AND impact_id=target ORDER BY completed_at DESC,id DESC LIMIT 101) recent;
  IF jsonb_array_length(history)>100 THEN RAISE EXCEPTION 'Complete follow-up history requires further review' USING ERRCODE='23514';END IF;
  RETURN jsonb_build_object('impact_id',target,'status',CASE WHEN receipt.id IS NULL THEN 'open'
    WHEN current_basis->>'basis_digest'=receipt.basis_digest THEN 'completed' ELSE 'changed' END,
    'ready',(current_basis->>'ready')::boolean,'blocker',current_basis->>'blocker','preview_digest',current_basis->>'basis_digest',
    'kind',current_basis#>>'{basis,kind}','target_id',current_basis#>>'{basis,target_id}',
    'source_document_id',current_basis#>>'{basis,source_document_id}',
    'source_review_required',COALESCE((current_basis#>>'{basis,source_review_required}')::boolean,false),'history',history);
END $$;
CREATE FUNCTION complete_correction_followup(org uuid,target uuid,request uuid,preview_digest text,explanation text,source_reviewed boolean,confirmed boolean) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;digest text;prior public.allocation_correction_completion;
  current_basis jsonb;basis jsonb;created uuid;
BEGIN
  IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501';END IF;
  PERFORM public.lock_intake(org);
  IF target IS NULL OR request IS NULL OR preview_digest IS NULL OR preview_digest !~ '^[0-9a-f]{64}$'
    OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 2000 OR confirmed IS DISTINCT FROM true OR source_reviewed IS NULL THEN
    RAISE EXCEPTION 'Explicit reviewed follow-up completion required' USING ERRCODE='22023';END IF;
  digest:=public.bounded_computation_digest(jsonb_build_array('correction-followup-v1',target,preview_digest,explanation,source_reviewed,confirmed));
  SELECT * INTO prior FROM public.allocation_correction_completion WHERE organization_id=org AND actor_id=actor AND request_key=request;
  IF prior.id IS NOT NULL THEN
    PERFORM public.correction_completion_result(org,prior.id);
    IF prior.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Request key already used' USING ERRCODE='23505';END IF;
    RETURN public.correction_completion_result(org,prior.id);
  END IF;
  current_basis:=public.correction_followup_basis(org,target,true);
  IF current_basis->>'ready' IS DISTINCT FROM 'true' OR current_basis->>'basis_digest' IS DISTINCT FROM preview_digest THEN
    RAISE EXCEPTION 'Follow-up evidence changed; review current financial processing' USING ERRCODE='23505';END IF;
  basis:=current_basis->'basis';
  IF (basis->>'source_review_required')::boolean AND source_reviewed IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Explicit current source evidence review required' USING ERRCODE='22023';END IF;
  INSERT INTO public.allocation_correction_completion(organization_id,impact_id,computation_id,allocation_head_id,actor_id,
    request_key,request_digest,basis_digest,basis,reason,source_reviewed)
    VALUES(org,target,(basis->>'computation_id')::uuid,(basis->>'allocation_head_id')::uuid,actor,
      request,digest,preview_digest,basis,explanation,source_reviewed) RETURNING id INTO created;
  INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
    VALUES(org,actor,'allocation_correction_impact',target,'allocation_correction.followup_completed',
      jsonb_build_object('completion_id',created,'computation_id',basis->>'computation_id','allocation_head_id',basis->>'allocation_head_id'));
  RETURN public.correction_completion_result(org,created);
EXCEPTION WHEN lock_not_available THEN
  RAISE EXCEPTION 'Financial evidence busy; refresh and retry' USING ERRCODE='23505';
END $$;
REVOKE ALL ON FUNCTION correction_completion_result(uuid,uuid),correction_followup_status(uuid,uuid),
  complete_correction_followup(uuid,uuid,uuid,text,text,boolean,boolean) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION correction_followup_status(uuid,uuid),complete_correction_followup(uuid,uuid,uuid,text,text,boolean,boolean) TO app_rw;
'''
