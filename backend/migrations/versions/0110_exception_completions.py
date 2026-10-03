"""Retain acknowledged case outcomes and evaluate their current applicability."""
from alembic import op
from sqlalchemy import text

revision='0110'
down_revision='0109'
branch_labels=None
depends_on=None
BEFORE="UNION ALL SELECT 'document_cost_link',id,to_jsonb(r) FROM public.document_cost_link r WHERE organization_id=org) evidence"
AFTER=BEFORE.replace(') evidence'," UNION ALL SELECT 'exception_completion',id,to_jsonb(r) FROM public.exception_completion r WHERE organization_id=org UNION ALL SELECT 'exception_completion_member',id,to_jsonb(r) FROM public.exception_completion_member r WHERE organization_id=org) evidence")
ACCESS_BEFORE='AND public.assignment_proposal_access(org,c.document_ref_id,c.scope_id)'
ACCESS_AFTER=ACCESS_BEFORE+' AND public.completion_case_audience(org,target,false)'
WORK_BEFORE="AND public.has_scope_capability(org,c.scope_id,'prepare')"
WORK_AFTER=WORK_BEFORE+' AND public.completion_case_audience(org,target,true)'
APPEND_BEFORE='reason,actor_id,request_key,request_digest,context_decision_id)'
APPEND_AFTER='reason,actor_id,request_key,request_digest,context_decision_id,completion_id,resolution_state)'
VALUES_BEFORE='explanation,actor,request,digest,current_event.context_decision_id) RETURNING'
VALUES_AFTER="explanation,actor,request,digest,current_event.context_decision_id,CASE WHEN verb IN ('claim','release') THEN current_event.completion_id END,CASE WHEN workflow='completed' THEN 'resolved' ELSE 'unresolved' END) RETURNING"
FLOW_BEFORE="workflow:=CASE WHEN snooze IS NULL THEN 'open' ELSE 'snoozed' END;"
FLOW_AFTER=FLOW_BEFORE+"\n      IF verb='snooze' AND public.exception_completion_current(org,current_event.completion_id) THEN RAISE EXCEPTION 'Reopen completed case before snoozing' USING ERRCODE='23505';END IF;\n      IF verb IN ('claim','release') AND public.exception_completion_current(org,current_event.completion_id) THEN workflow:='completed';snooze:=NULL;END IF;"
LEGACY_SHAPE="""(
 (action IN ('duplicate_evidence_added','duplicate_comparison_recorded') AND duplicate_dependency_id IS NOT NULL
  AND ((action='duplicate_comparison_recorded')=(duplicate_comparison_id IS NOT NULL))
  AND actor_id IS NULL AND request_key IS NULL AND request_digest IS NULL AND dependency_id IS NULL AND context_decision_id IS NULL
  AND workflow_state='open' AND snoozed_until IS NULL)
 OR (action='evidence_added' AND actor_id IS NULL AND dependency_id IS NOT NULL AND request_key IS NULL AND request_digest IS NULL
  AND duplicate_dependency_id IS NULL AND duplicate_comparison_id IS NULL)
 OR (action IN ('claim','release','snooze','reopen','context_reviewed') AND actor_id IS NOT NULL AND dependency_id IS NULL AND request_key IS NOT NULL AND request_digest IS NOT NULL
  AND duplicate_dependency_id IS NULL AND duplicate_comparison_id IS NULL))"""
ACTIONS="'evidence_added','claim','release','snooze','reopen','context_reviewed','duplicate_evidence_added','duplicate_comparison_recorded'"


def replace(signature,before,after):
    definition=op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':signature})
    assert definition.count(before)==1,(signature,before)
    op.execute(definition.replace(before,after))


def upgrade():
    op.execute('''DO $$ DECLARE r record;BEGIN
      FOR r IN SELECT id FROM organization ORDER BY id LOOP PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||r.id::text,0));END LOOP;
      FOR r IN SELECT id FROM organization ORDER BY id LOOP PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||r.id::text,0));END LOOP;
      END $$;
      LOCK TABLE exception_case_event,allocation_set IN SHARE ROW EXCLUSIVE MODE NOWAIT;
      CREATE TABLE exception_completion(
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,case_id uuid NOT NULL,expected_event_id uuid NOT NULL,
        policy text NOT NULL DEFAULT 'exception-completion-v1' CHECK(policy='exception-completion-v1'),
        basis jsonb NOT NULL CHECK(jsonb_typeof(basis)='object'),dispositions jsonb NOT NULL CHECK(dispositions=basis->'dispositions'),comparisons jsonb NOT NULL CHECK(comparisons=basis->'comparisons'),basis_digest text NOT NULL CHECK(basis_digest~'^[0-9a-f]{64}$'),
        member_count integer NOT NULL CHECK(member_count BETWEEN 1 AND 100),
        actor_id uuid NOT NULL,request_key uuid NOT NULL,request_digest text NOT NULL,reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 1000),
        completion_reviewed boolean NOT NULL CHECK(completion_reviewed),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(organization_id,id),UNIQUE(organization_id,case_id,id),UNIQUE(organization_id,actor_id,request_key),
        FOREIGN KEY(organization_id,case_id,expected_event_id) REFERENCES exception_case_event(organization_id,case_id,id),
        FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
      CREATE TABLE exception_completion_member(
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,case_id uuid NOT NULL,completion_id uuid NOT NULL,
        document_ref_id uuid NOT NULL,logical_document_id uuid NOT NULL,invoice_review_id uuid NOT NULL,scope_id uuid NOT NULL,property_id uuid NOT NULL,
        allocation_set_id uuid NOT NULL,disposition text NOT NULL CHECK(disposition IN ('adopted_document','covered_copy')),
        UNIQUE(organization_id,completion_id,logical_document_id),
        FOREIGN KEY(organization_id,case_id,completion_id) REFERENCES exception_completion(organization_id,case_id,id),
        FOREIGN KEY(organization_id,scope_id,document_ref_id,logical_document_id,invoice_review_id) REFERENCES invoice_review_revision(organization_id,scope_id,document_ref_id,logical_document_id,id),
        FOREIGN KEY(organization_id,property_id,scope_id) REFERENCES accounting_scope(organization_id,property_id,id),
        FOREIGN KEY(organization_id,allocation_set_id) REFERENCES allocation_set(organization_id,id));
      ALTER TABLE exception_case_event ADD COLUMN completion_id uuid;
      ALTER TABLE exception_case_event ADD CONSTRAINT event_completion FOREIGN KEY(organization_id,case_id,completion_id) REFERENCES exception_completion(organization_id,case_id,id);
      CREATE UNIQUE INDEX completion_recorded_once ON exception_case_event(organization_id,completion_id) WHERE action='completion_recorded';
      ALTER TABLE exception_case_event DROP CONSTRAINT exception_case_event_resolution_state_check;
      ALTER TABLE exception_case_event ADD CONSTRAINT exception_case_event_resolution_state_check CHECK(resolution_state IN ('unresolved','resolved'));
      ALTER TABLE exception_case_event DROP CONSTRAINT exception_case_event_workflow_state_check;
      ALTER TABLE exception_case_event ADD CONSTRAINT exception_case_event_workflow_state_check CHECK(workflow_state IN ('open','snoozed','completed'));
      ALTER TABLE exception_case_event ADD CONSTRAINT completion_state_shape CHECK(
        (resolution_state='resolved')=(workflow_state='completed') AND (resolution_state<>'resolved' OR completion_id IS NOT NULL)
        AND (completion_id IS NULL OR action IN ('completion_recorded','claim','release'))
        AND (action<>'completion_recorded' OR (completion_id IS NOT NULL AND resolution_state='resolved')));
    ''')
    op.execute('ALTER TABLE exception_case_event DROP CONSTRAINT exception_case_event_action_check')
    op.execute("ALTER TABLE exception_case_event ADD CONSTRAINT exception_case_event_action_check CHECK(action IN ("+ACTIONS+",'completion_recorded'))")
    op.execute('ALTER TABLE exception_case_event DROP CONSTRAINT exception_case_event_check1')
    op.execute('ALTER TABLE exception_case_event ADD CONSTRAINT exception_case_event_check1 CHECK('+LEGACY_SHAPE+" OR (action='completion_recorded' AND actor_id IS NOT NULL AND request_key IS NOT NULL AND request_digest IS NOT NULL AND dependency_id IS NULL AND context_decision_id IS NULL AND duplicate_dependency_id IS NULL AND duplicate_comparison_id IS NULL))")
    op.execute(AUDIENCE_SQL)
    for table in ('exception_completion','exception_completion_member'):
        op.execute(f'''ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review;
          CREATE POLICY completion_read ON {table} FOR SELECT USING(exception_case_access(organization_id,case_id));
          CREATE POLICY completion_no_insert ON {table} FOR INSERT WITH CHECK(false);
          CREATE TRIGGER protect_completion BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();''')
    op.execute('''GRANT SELECT(id,organization_id,case_id,expected_event_id,policy,basis_digest,dispositions,comparisons,member_count,actor_id,reason,completion_reviewed,created_at) ON exception_completion TO app_rw;
      GRANT SELECT ON exception_completion_member TO app_rw;''')
    replace('exception_case_access(uuid,uuid)',ACCESS_BEFORE,ACCESS_AFTER)
    replace('exception_case_work_access(uuid,uuid)',WORK_BEFORE,WORK_AFTER)
    op.execute(EVALUATOR_SQL)
    op.execute(COMMAND_SQL)
    op.execute(SEAL_SQL)
    replace('append_exception_event(uuid,uuid,uuid,uuid,text,text,timestamptz)',APPEND_BEFORE,APPEND_AFTER)
    replace('append_exception_event(uuid,uuid,uuid,uuid,text,text,timestamptz)',VALUES_BEFORE,VALUES_AFTER)
    replace('append_exception_event(uuid,uuid,uuid,uuid,text,text,timestamptz)',FLOW_BEFORE,FLOW_AFTER)
    replace('access_coverage_manifest(uuid,jsonb)',BEFORE,AFTER)


def downgrade():
    op.execute('LOCK TABLE exception_completion,exception_completion_member,exception_case_event IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM exception_completion) THEN RAISE EXCEPTION 'Retain completion receipts; repair forward';END IF;END $$")
    replace('access_coverage_manifest(uuid,jsonb)',AFTER,BEFORE)
    replace('append_exception_event(uuid,uuid,uuid,uuid,text,text,timestamptz)',FLOW_AFTER,FLOW_BEFORE)
    replace('append_exception_event(uuid,uuid,uuid,uuid,text,text,timestamptz)',VALUES_AFTER,VALUES_BEFORE)
    replace('append_exception_event(uuid,uuid,uuid,uuid,text,text,timestamptz)',APPEND_AFTER,APPEND_BEFORE)
    replace('exception_case_work_access(uuid,uuid)',WORK_AFTER,WORK_BEFORE)
    replace('exception_case_access(uuid,uuid)',ACCESS_AFTER,ACCESS_BEFORE)
    op.execute('''ALTER TABLE exception_case_event DROP CONSTRAINT completion_state_shape;
      ALTER TABLE exception_case_event DROP COLUMN completion_id;
      ALTER TABLE exception_case_event DROP CONSTRAINT exception_case_event_resolution_state_check;
      ALTER TABLE exception_case_event ADD CONSTRAINT exception_case_event_resolution_state_check CHECK(resolution_state='unresolved');
      ALTER TABLE exception_case_event DROP CONSTRAINT exception_case_event_workflow_state_check;
      ALTER TABLE exception_case_event ADD CONSTRAINT exception_case_event_workflow_state_check CHECK(workflow_state IN ('open','snoozed'));
      ALTER TABLE exception_case_event DROP CONSTRAINT exception_case_event_action_check;
      ALTER TABLE exception_case_event DROP CONSTRAINT exception_case_event_check1;''')
    op.execute('ALTER TABLE exception_case_event ADD CONSTRAINT exception_case_event_check1 CHECK('+LEGACY_SHAPE+')')
    op.execute('ALTER TABLE exception_case_event ADD CONSTRAINT exception_case_event_action_check CHECK(action IN ('+ACTIONS+'))')
    op.execute('''DROP TABLE exception_completion_member,exception_completion;
      DROP FUNCTION completion_case_audience(uuid,uuid,boolean),evaluate_exception_completion(uuid,uuid),exception_completion_current(uuid,uuid),
        exception_completion_state(uuid,uuid),exception_completion_preview(uuid,uuid),create_exception_completion(uuid,uuid,uuid,uuid,text,text,boolean),
        validate_exception_completion();''')


AUDIENCE_SQL='''
CREATE FUNCTION completion_case_audience(org uuid,target uuid,work boolean) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
  SELECT NOT EXISTS(SELECT 1 FROM public.exception_completion_member m WHERE m.organization_id=org AND m.case_id=target
    AND (NOT public.context_comparison_access(org,m.document_ref_id,m.scope_id,m.property_id)
      OR NOT public.allocation_set_access(org,m.allocation_set_id)
      OR (work AND (NOT public.source_partition_access(org,m.document_ref_id,true) OR NOT public.has_scope_capability(org,m.scope_id,'prepare')))));
$$;
REVOKE ALL ON FUNCTION completion_case_audience(uuid,uuid,boolean) FROM PUBLIC,app_rw,app_worker,app_review;
'''


EVALUATOR_SQL='''
CREATE FUNCTION evaluate_exception_completion(org uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE c public.exception_case;r public.invoice_review_revision;s public.allocation_set;p public.assignment_proposal;
  a public.allocation_proposal;ctx public.context_decision;pair public.duplicate_comparison;dep record;
  logical uuid;component uuid[];heads jsonb;member jsonb;left_member jsonb;right_member jsonb;facts jsonb;basis jsonb;
  members jsonb:='[]'::jsonb;comparisons jsonb:='[]'::jsonb;dispositions jsonb:='[]'::jsonb;private_sources jsonb:='[]'::jsonb;
  eligible boolean:=true;readable boolean:=true;can_work boolean:=true;needed boolean;matches boolean;property_id uuid;count_sources integer;
BEGIN
  SELECT * INTO STRICT c FROM public.exception_case WHERE organization_id=org AND id=target;
  IF c.cause NOT IN ('scope_hint_conflict','duplicate_candidate') THEN eligible:=false;END IF;
  -- Discover the whole connected component before applying actor visibility. Include
  -- retained adopted identities so a later edited review cannot erase a claim.
  WITH RECURSIVE heads AS MATERIALIZED (
    SELECT DISTINCT ON(logical_document_id) * FROM public.invoice_review_revision WHERE organization_id=org ORDER BY logical_document_id,revision_number DESC
  ), reviews AS MATERIALIZED (
    SELECT * FROM heads UNION SELECT retained_review.* FROM public.invoice_review_revision retained_review JOIN public.allocation_set retained_set ON retained_set.organization_id=retained_review.organization_id AND retained_set.invoice_review_id=retained_review.id WHERE retained_set.organization_id=org
  ), connected(logical_id) AS (
    SELECT c.logical_document_id UNION
    SELECT unnest(ARRAY[d.anchor_logical_id,d.reference_logical_id]) FROM public.duplicate_exception_dependency d WHERE d.organization_id=org AND d.case_id=target
    UNION
    SELECT right_review.logical_document_id FROM connected n JOIN reviews left_review ON left_review.logical_document_id=n.logical_id JOIN reviews right_review
      ON (right_review.content_sha256=left_review.content_sha256 OR public.adoption_invoice_identity(right_review::public.invoice_review_revision)=public.adoption_invoice_identity(left_review::public.invoice_review_revision))
  ) SELECT array_agg(logical_id ORDER BY logical_id),count(*) INTO component,count_sources FROM (SELECT logical_id FROM connected LIMIT 101) bounded_component;
  IF count_sources NOT BETWEEN 1 AND 100 OR (SELECT count(*) FROM (SELECT 1 FROM public.duplicate_exception_dependency WHERE organization_id=org AND case_id=target LIMIT 1001) bounded_dependencies)>1000 THEN
    RETURN jsonb_build_object('eligible',false,'readable',false,'can_work',false,'basis',jsonb_build_object('policy','exception-completion-v1','unsupported_component',true));
  END IF;
  FOREACH logical IN ARRAY component LOOP
    SELECT * INTO r FROM public.invoice_review_revision WHERE organization_id=org AND logical_document_id=logical ORDER BY revision_number DESC LIMIT 1;
    SELECT * INTO p FROM public.assignment_proposal WHERE organization_id=org AND logical_document_id=logical ORDER BY revision_number DESC LIMIT 1;
    SELECT * INTO ctx FROM public.context_decision WHERE organization_id=org AND logical_document_id=logical ORDER BY revision_number DESC LIMIT 1;
    SELECT * INTO a FROM public.allocation_proposal WHERE organization_id=org AND logical_document_id=logical ORDER BY revision_number DESC LIMIT 1;
    SELECT sc.property_id INTO property_id FROM public.accounting_scope sc WHERE sc.organization_id=org AND sc.id=r.scope_id;
    IF r.id IS NULL OR p.id IS NULL OR r.scope_id IS DISTINCT FROM c.scope_id THEN eligible:=false;END IF;
    IF r.id IS NULL OR NOT public.context_comparison_access(org,r.document_ref_id,r.scope_id,property_id) THEN readable:=false;END IF;
    can_work:=can_work AND r.id IS NOT NULL AND public.source_partition_access(org,r.document_ref_id,true) AND public.has_scope_capability(org,r.scope_id,'prepare');
    heads:=jsonb_build_object('partition',(SELECT id FROM public.source_partition_revision WHERE organization_id=org AND document_ref_id=r.document_ref_id ORDER BY revision_number DESC LIMIT 1),
      'extraction',(SELECT id FROM public.extraction_revision WHERE organization_id=org AND logical_document_id=logical ORDER BY created_at DESC,id DESC LIMIT 1),
      'proposal',p.id,'review',r.id,'context',ctx.id,'allocation',a.id,
      'treatment',(SELECT id FROM public.allocation_treatment_review WHERE organization_id=org AND logical_document_id=logical ORDER BY revision_number DESC LIMIT 1));
    IF r.assignment_proposal_id IS DISTINCT FROM p.id OR r.partition_revision_id IS DISTINCT FROM (heads->>'partition')::uuid
      OR r.extraction_revision_id IS DISTINCT FROM (heads->>'extraction')::uuid
      OR NOT EXISTS(SELECT 1 FROM public.source_partition_revision part JOIN public.source_partition_member m ON m.organization_id=part.organization_id AND m.partition_revision_id=part.id
        WHERE part.organization_id=org AND part.id=r.partition_revision_id AND part.member_count=1 AND m.logical_document_id=logical) THEN eligible:=false;END IF;
    SELECT * INTO s FROM public.allocation_set WHERE organization_id=org AND kind='intake_reviewed' AND content_sha256=r.content_sha256;
    IF s.id IS NULL OR s.scope_id IS DISTINCT FROM c.scope_id OR s.property_id IS DISTINCT FROM property_id THEN eligible:=false;
    ELSE
      IF NOT public.allocation_set_access(org,s.id) THEN readable:=false;END IF;
      IF s.logical_document_id=logical THEN
        IF s.invoice_review_id IS DISTINCT FROM r.id OR s.allocation_proposal_id IS DISTINCT FROM a.id OR s.treatment_review_id IS DISTINCT FROM (heads->>'treatment')::uuid
          OR a.context_decision_id IS DISTINCT FROM ctx.id OR ctx.invoice_review_id IS DISTINCT FROM r.id OR ctx.assignment_proposal_id IS DISTINCT FROM p.id THEN eligible:=false;END IF;
      ELSIF public.adoption_invoice_identity(r) IS DISTINCT FROM (SELECT public.adoption_invoice_identity(v) FROM public.invoice_review_revision v WHERE v.organization_id=org AND v.id=s.invoice_review_id) THEN
        eligible:=false;
      END IF;
    END IF;
    member:=jsonb_build_object('logical_document_id',logical,'document_ref_id',r.document_ref_id,'invoice_review_id',r.id,'scope_id',r.scope_id,'property_id',property_id,
      'allocation_set_id',s.id,'disposition',CASE WHEN s.logical_document_id=logical THEN 'adopted_document' ELSE 'covered_copy' END,
      'content_sha256',r.content_sha256,'identity',public.adoption_invoice_identity(r),'heads',heads,
      'source_document_id',s.source_document_id,'document_number',r.document_number,'gross_amount',round(r.gross_amount,2)::text,'cost_count',s.cost_count);
    members:=members||jsonb_build_array(member);
    IF r.id IS NOT NULL THEN
      facts:=public.adoption_duplicate_snapshot(org,r.id);
      IF facts->'legacy_sources'<>'[]'::jsonb OR facts->'legacy_costs'<>'[]'::jsonb THEN eligible:=false;END IF;
      private_sources:=private_sources||jsonb_build_array(jsonb_build_object('review',r.id,'facts',facts));
    END IF;
  END LOOP;
  -- Every matching pair and every retained dependency pair needs a current exact
  -- comparison. Same-byte copies share one outcome; distinct documents do not.
  FOR left_member,right_member IN SELECT left_json.value,right_json.value FROM jsonb_array_elements(members) left_json CROSS JOIN jsonb_array_elements(members) right_json
    WHERE left_json.value->>'logical_document_id'<right_json.value->>'logical_document_id' LOOP
    matches:=left_member->>'content_sha256'=right_member->>'content_sha256' OR left_member->'identity'=right_member->'identity';
    needed:=matches OR EXISTS(SELECT 1 FROM public.duplicate_exception_dependency d WHERE d.organization_id=org AND d.case_id=target
      AND d.anchor_logical_id=(left_member->>'logical_document_id')::uuid AND d.reference_logical_id=(right_member->>'logical_document_id')::uuid);
    IF NOT needed THEN CONTINUE;END IF;
    SELECT * INTO pair FROM public.duplicate_comparison WHERE organization_id=org AND scope_id=c.scope_id
      AND pair_low=(left_member->>'logical_document_id')::uuid AND pair_high=(right_member->>'logical_document_id')::uuid ORDER BY revision_number DESC LIMIT 1;
    IF NOT matches OR pair.id IS NULL OR NOT ((pair.subject_review_id=(left_member->>'invoice_review_id')::uuid AND pair.reference_review_id=(right_member->>'invoice_review_id')::uuid)
      OR (pair.reference_review_id=(left_member->>'invoice_review_id')::uuid AND pair.subject_review_id=(right_member->>'invoice_review_id')::uuid)) THEN eligible:=false;
    ELSIF left_member->>'content_sha256'=right_member->>'content_sha256' THEN
      IF pair.verdict<>'same_document' OR left_member->'identity' IS DISTINCT FROM right_member->'identity'
        OR left_member->>'allocation_set_id' IS DISTINCT FROM right_member->>'allocation_set_id' THEN eligible:=false;END IF;
    ELSIF pair.verdict<>'distinct_documents' OR left_member->>'allocation_set_id' IS NOT DISTINCT FROM right_member->>'allocation_set_id' THEN eligible:=false;
    END IF;
    comparisons:=comparisons||jsonb_build_array(jsonb_build_object('left_logical_id',left_member->>'logical_document_id','right_logical_id',right_member->>'logical_document_id',
      'comparison_id',pair.id,'verdict',pair.verdict,'subject_review_id',pair.subject_review_id,'reference_review_id',pair.reference_review_id));
  END LOOP;
  -- Include receipts without registry rows. Unknown copies cannot be disposed by
  -- a comparison between only the previously visible sources.
  FOR dep IN SELECT o.id,o.content_sha256,raw_ref.id AS ref,j.status FROM public.intake_original o
    LEFT JOIN public.document_ref raw_ref ON raw_ref.organization_id=o.organization_id AND raw_ref.intake_original_id=o.id
    JOIN public.workflow_job j ON j.organization_id=o.organization_id AND j.original_id=o.id
    WHERE o.organization_id=org AND o.content_sha256 IN(SELECT value->>'content_sha256' FROM jsonb_array_elements(members)) ORDER BY o.id LOOP
    IF dep.status<>'clean' OR dep.ref IS NULL OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(members) m WHERE m->>'document_ref_id'=dep.ref::text) THEN eligible:=false;END IF;
    private_sources:=private_sources||jsonb_build_array(jsonb_build_object('receipt',to_jsonb(dep)));
  END LOOP;
  FOR dep IN SELECT * FROM public.exception_dependency WHERE organization_id=org AND case_id=target ORDER BY id LOOP
    SELECT value INTO member FROM jsonb_array_elements(members) WHERE value->>'logical_document_id'=c.logical_document_id::text;
    SELECT * INTO ctx FROM public.context_decision WHERE organization_id=org AND id=(member->'heads'->>'context')::uuid;
    IF c.cause<>'scope_hint_conflict' OR ctx.case_id IS DISTINCT FROM c.id OR member->>'disposition'<>'adopted_document'
      OR ctx.scope_id IS DISTINCT FROM c.scope_id OR (SELECT extraction_revision_id FROM public.invoice_review_revision WHERE organization_id=org AND id=ctx.invoice_review_id) IS DISTINCT FROM dep.extraction_revision_id THEN eligible:=false;END IF;
    dispositions:=dispositions||jsonb_build_array(jsonb_build_object('dependency_id',dep.id,'kind','context',
      'disposition',CASE WHEN dep.assignment_proposal_id=ctx.assignment_proposal_id THEN 'fulfilled_by_adoption' ELSE 'superseded_by_adopted_context' END,
      'context_decision_id',ctx.id,'assignment_proposal_id',ctx.assignment_proposal_id,'allocation_set_id',member->>'allocation_set_id'));
  END LOOP;
  FOR dep IN SELECT * FROM public.duplicate_exception_dependency WHERE organization_id=org AND case_id=target ORDER BY id LOOP
    SELECT value INTO left_member FROM jsonb_array_elements(members) WHERE value->>'logical_document_id'=dep.anchor_logical_id::text;
    SELECT value INTO right_member FROM jsonb_array_elements(members) WHERE value->>'logical_document_id'=dep.reference_logical_id::text;
    SELECT value INTO facts FROM jsonb_array_elements(comparisons) WHERE value->>'left_logical_id'=dep.anchor_logical_id::text AND value->>'right_logical_id'=dep.reference_logical_id::text;
    IF facts->>'comparison_id' IS NULL THEN eligible:=false;END IF;
    dispositions:=dispositions||jsonb_build_array(jsonb_build_object('dependency_id',dep.id,'kind','duplicate',
      'disposition',CASE WHEN dep.anchor_review_id=(left_member->>'invoice_review_id')::uuid AND dep.reference_review_id=(right_member->>'invoice_review_id')::uuid
        THEN 'fulfilled_by_current_comparison' ELSE 'superseded_by_current_comparison' END,
      'anchor_review_id',left_member->>'invoice_review_id','reference_review_id',right_member->>'invoice_review_id','comparison_id',facts->>'comparison_id',
      'anchor_allocation_set_id',left_member->>'allocation_set_id','reference_allocation_set_id',right_member->>'allocation_set_id','verdict',facts->>'verdict'));
  END LOOP;
  basis:=jsonb_build_object('policy','exception-completion-v1','case_id',target,'cause',c.cause,'members',members,'comparisons',comparisons,'dispositions',dispositions,'private_sources',private_sources);
  RETURN jsonb_build_object('basis',basis,'eligible',eligible,'readable',readable,'can_work',can_work);
END $$;
REVOKE ALL ON FUNCTION evaluate_exception_completion(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
'''


COMMAND_SQL='''
CREATE FUNCTION exception_completion_current(org uuid,target uuid) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE receipt public.exception_completion;evaluation jsonb;
BEGIN
  IF target IS NULL THEN RETURN false;END IF;
  SELECT * INTO receipt FROM public.exception_completion WHERE organization_id=org AND id=target;
  IF receipt.id IS NULL OR NOT public.exception_case_access(org,receipt.case_id) THEN RETURN false;END IF;
  evaluation:=public.evaluate_exception_completion(org,receipt.case_id);
  RETURN COALESCE((evaluation->>'readable')::boolean AND (evaluation->>'eligible')::boolean
    AND receipt.basis_digest=encode(sha256(convert_to((evaluation->'basis')::text,'UTF8')),'hex'),false);
END $$;
REVOKE ALL ON FUNCTION exception_completion_current(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;

CREATE FUNCTION exception_completion_state(org uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE e public.exception_case_event;current_completion boolean;
BEGIN
  IF NOT public.exception_case_access(org,target) THEN RAISE EXCEPTION 'Exception inaccessible' USING ERRCODE='42501';END IF;
  SELECT * INTO STRICT e FROM public.exception_case_event WHERE organization_id=org AND case_id=target ORDER BY revision_number DESC LIMIT 1;
  current_completion:=public.exception_completion_current(org,e.completion_id);
  RETURN jsonb_build_object('resolution_state',CASE WHEN current_completion THEN 'resolved' ELSE 'unresolved' END,
    'workflow_state',CASE WHEN current_completion THEN 'completed' WHEN e.completion_id IS NOT NULL THEN 'open' WHEN e.snoozed_until>statement_timestamp() THEN 'snoozed' ELSE 'open' END,
    'completion_id',e.completion_id,'completion_changed',e.completion_id IS NOT NULL AND NOT current_completion);
END $$;
REVOKE ALL ON FUNCTION exception_completion_state(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION exception_completion_state(uuid,uuid) TO app_rw;

CREATE FUNCTION exception_completion_preview(org uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE evaluation jsonb;members jsonb;allowed boolean;
BEGIN
  IF NOT public.exception_case_access(org,target) THEN RAISE EXCEPTION 'Exception inaccessible' USING ERRCODE='42501';END IF;
  evaluation:=public.evaluate_exception_completion(org,target);
  allowed:=COALESCE((evaluation->>'readable')::boolean,false);
  SELECT jsonb_agg(value-ARRAY['identity','content_sha256','heads'] ORDER BY value->>'logical_document_id') INTO members FROM jsonb_array_elements(evaluation->'basis'->'members');
  RETURN jsonb_build_object('can_complete',allowed AND COALESCE((evaluation->>'eligible')::boolean,false) AND COALESCE((evaluation->>'can_work')::boolean,false) AND public.exception_case_work_access(org,target),
    'expected_event_id',(SELECT id FROM public.exception_case_event WHERE organization_id=org AND case_id=target ORDER BY revision_number DESC LIMIT 1),
    'preview_digest',CASE WHEN allowed THEN encode(sha256(convert_to((evaluation->'basis')::text,'UTF8')),'hex') END,
    'members',CASE WHEN allowed THEN COALESCE(members,'[]'::jsonb) ELSE '[]'::jsonb END,
    'dispositions',CASE WHEN allowed THEN evaluation->'basis'->'dispositions' ELSE '[]'::jsonb END,
    'comparisons',CASE WHEN allowed THEN evaluation->'basis'->'comparisons' ELSE '[]'::jsonb END,
    'blocker',CASE WHEN NOT allowed OR NOT COALESCE((evaluation->>'eligible')::boolean,false) THEN 'Complete current evidence and confirmed outcomes required' END);
END $$;
REVOKE ALL ON FUNCTION exception_completion_preview(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION exception_completion_preview(uuid,uuid) TO app_rw;

CREATE FUNCTION create_exception_completion(org uuid,target uuid,expected uuid,request uuid,preview_digest text,explanation text,ack boolean) RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;prior public.exception_completion;
  e public.exception_case_event;evaluation jsonb;basis jsonb;digest text;created uuid:=gen_random_uuid();current_digest text;
BEGIN
  IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501';END IF;
  PERFORM public.lock_intake(org);
  IF NOT public.exception_case_work_access(org,target) THEN RAISE EXCEPTION 'Exception completion inaccessible' USING ERRCODE='42501';END IF;
  IF expected IS NULL OR request IS NULL OR preview_digest IS NULL OR preview_digest !~ '^[0-9a-f]{64}$' OR ack IS DISTINCT FROM true
    OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'Exact reviewed completion required' USING ERRCODE='22023';END IF;
  digest:=encode(sha256(convert_to(jsonb_build_array(target,expected,preview_digest,explanation,ack)::text,'UTF8')),'hex');
  SELECT * INTO prior FROM public.exception_completion WHERE organization_id=org AND actor_id=actor AND request_key=request;
  IF FOUND THEN
    IF prior.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Completion request changed' USING ERRCODE='23505';END IF;
    RETURN prior.id;
  END IF;
  SELECT * INTO STRICT e FROM public.exception_case_event WHERE organization_id=org AND case_id=target ORDER BY revision_number DESC LIMIT 1;
  IF e.id IS DISTINCT FROM expected THEN RAISE EXCEPTION 'Exception changed' USING ERRCODE='23505';END IF;
  evaluation:=public.evaluate_exception_completion(org,target);basis:=evaluation->'basis';
  IF NOT COALESCE((evaluation->>'readable')::boolean,false) OR NOT COALESCE((evaluation->>'can_work')::boolean,false) THEN
    RAISE EXCEPTION 'Complete source audience required' USING ERRCODE='42501';END IF;
  current_digest:=encode(sha256(convert_to(basis::text,'UTF8')),'hex');
  IF current_digest IS DISTINCT FROM preview_digest OR NOT COALESCE((evaluation->>'eligible')::boolean,false) THEN
    RAISE EXCEPTION 'Completion evidence changed or remains unresolved' USING ERRCODE='23505';END IF;
  INSERT INTO public.exception_completion(id,organization_id,case_id,expected_event_id,basis,dispositions,comparisons,basis_digest,member_count,actor_id,request_key,request_digest,reason,completion_reviewed)
    VALUES(created,org,target,expected,basis,basis->'dispositions',basis->'comparisons',current_digest,jsonb_array_length(basis->'members'),actor,request,digest,explanation,true);
  INSERT INTO public.exception_completion_member(organization_id,case_id,completion_id,logical_document_id,document_ref_id,invoice_review_id,scope_id,property_id,allocation_set_id,disposition)
    SELECT org,target,created,(m->>'logical_document_id')::uuid,(m->>'document_ref_id')::uuid,(m->>'invoice_review_id')::uuid,(m->>'scope_id')::uuid,
      (m->>'property_id')::uuid,(m->>'allocation_set_id')::uuid,m->>'disposition' FROM jsonb_array_elements(basis->'members') m;
  INSERT INTO public.exception_case_event(organization_id,case_id,previous_event_id,revision_number,action,resolution_state,workflow_state,claimant_id,reason,actor_id,request_key,request_digest,completion_id)
    VALUES(org,target,expected,e.revision_number+1,'completion_recorded','resolved','completed',e.claimant_id,explanation,actor,request,digest,created);
  RETURN created;
END $$;
REVOKE ALL ON FUNCTION create_exception_completion(uuid,uuid,uuid,uuid,text,text,boolean) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION create_exception_completion(uuid,uuid,uuid,uuid,text,text,boolean) TO app_rw;
'''


SEAL_SQL='''
CREATE FUNCTION validate_exception_completion() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE receipt public.exception_completion;target uuid;count_members integer;member public.exception_completion_member;recorded jsonb;
BEGIN
  IF TG_TABLE_NAME='exception_completion' THEN target:=NEW.id;ELSE target:=NEW.completion_id;END IF;
  SELECT * INTO STRICT receipt FROM public.exception_completion WHERE organization_id=NEW.organization_id AND id=target;
  SELECT count(*) INTO count_members FROM public.exception_completion_member WHERE organization_id=receipt.organization_id AND completion_id=receipt.id;
  IF count_members<>receipt.member_count OR jsonb_array_length(receipt.basis->'members')<>count_members
    OR receipt.basis_digest<>encode(sha256(convert_to(receipt.basis::text,'UTF8')),'hex')
    OR NOT EXISTS(SELECT 1 FROM public.exception_case_event e WHERE e.organization_id=receipt.organization_id AND e.case_id=receipt.case_id
      AND e.completion_id=receipt.id AND e.action='completion_recorded' AND e.previous_event_id=receipt.expected_event_id
      AND e.actor_id=receipt.actor_id AND e.request_key=receipt.request_key AND e.request_digest=receipt.request_digest AND e.reason=receipt.reason) THEN
    RAISE EXCEPTION 'Complete immutable outcome receipt required' USING ERRCODE='23514';END IF;
  FOR member IN SELECT * FROM public.exception_completion_member WHERE organization_id=receipt.organization_id AND completion_id=receipt.id LOOP
    SELECT value INTO recorded FROM jsonb_array_elements(receipt.basis->'members') WHERE value->>'logical_document_id'=member.logical_document_id::text;
    IF recorded IS NULL OR recorded->>'document_ref_id' IS DISTINCT FROM member.document_ref_id::text OR recorded->>'invoice_review_id' IS DISTINCT FROM member.invoice_review_id::text
      OR recorded->>'scope_id' IS DISTINCT FROM member.scope_id::text OR recorded->>'property_id' IS DISTINCT FROM member.property_id::text
      OR recorded->>'allocation_set_id' IS DISTINCT FROM member.allocation_set_id::text OR recorded->>'disposition' IS DISTINCT FROM member.disposition
      OR NOT EXISTS(SELECT 1 FROM public.allocation_set s JOIN public.invoice_review_revision r ON r.organization_id=s.organization_id AND r.id=member.invoice_review_id
        WHERE s.organization_id=member.organization_id AND s.id=member.allocation_set_id AND s.kind='intake_reviewed'
          AND s.content_sha256=r.content_sha256 AND s.scope_id=member.scope_id AND s.property_id=member.property_id
          AND ((member.disposition='adopted_document' AND s.logical_document_id=member.logical_document_id AND s.invoice_review_id=member.invoice_review_id)
            OR (member.disposition='covered_copy' AND s.logical_document_id<>member.logical_document_id))) THEN
      RAISE EXCEPTION 'Exact completion source and adoption membership required' USING ERRCODE='23514';END IF;
  END LOOP;
  RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION validate_exception_completion() FROM PUBLIC,app_rw,app_worker,app_review;
CREATE CONSTRAINT TRIGGER seal_exception_completion AFTER INSERT ON exception_completion DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_exception_completion();
CREATE CONSTRAINT TRIGGER seal_exception_completion AFTER INSERT ON exception_completion_member DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_exception_completion();
'''
