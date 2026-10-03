"""Retain exact human context comparisons without blanket case resolution."""
from alembic import op
from sqlalchemy import text

revision='0104'
down_revision='0103'
branch_labels=None
depends_on=None
BEFORE="UNION ALL SELECT 'invoice_review_revision',id,to_jsonb(r) FROM public.invoice_review_revision r WHERE organization_id=org) evidence"
AFTER=BEFORE.replace(') evidence'," UNION ALL SELECT 'context_decision',id,to_jsonb(r) FROM public.context_decision r WHERE organization_id=org) evidence")
APPEND_BEFORE='reason,actor_id,request_key,request_digest)'
APPEND_AFTER='reason,actor_id,request_key,request_digest,context_decision_id)'
VALUES_BEFORE='explanation,actor,request,digest) RETURNING'
VALUES_AFTER='explanation,actor,request,digest,current_event.context_decision_id) RETURNING'
ACCESS_BEFORE='AND public.assignment_proposal_access(org,c.document_ref_id,c.scope_id));'
ACCESS_AFTER="""AND public.assignment_proposal_access(org,c.document_ref_id,c.scope_id)
        AND NOT EXISTS(SELECT 1 FROM public.context_decision d WHERE d.organization_id=org AND d.case_id=c.id
          AND NOT public.context_comparison_access(org,d.document_ref_id,d.scope_id,d.property_id)));"""


def replace_function(signature,before,after):
    definition=op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':signature})
    assert definition.count(before)==1,(signature,before)
    op.execute(definition.replace(before,after))


def upgrade():
    op.execute('''
    ALTER TABLE invoice_review_revision ADD CONSTRAINT invoice_context_basis UNIQUE(organization_id,document_ref_id,logical_document_id,scope_id,assignment_proposal_id,id);
    ALTER TABLE exception_dependency ADD CONSTRAINT dependency_context_basis UNIQUE(organization_id,case_id,assignment_proposal_id,id);
    CREATE TABLE context_decision(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,
      document_ref_id uuid NOT NULL,logical_document_id uuid NOT NULL,scope_id uuid NOT NULL,property_id uuid NOT NULL,
      assignment_proposal_id uuid NOT NULL,invoice_review_id uuid NOT NULL,
      case_id uuid,dependency_id uuid,previous_revision_id uuid,revision_number integer NOT NULL CHECK(revision_number>0),
      method text NOT NULL DEFAULT 'manual_document_comparison' CHECK(method='manual_document_comparison'),
      snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object'),snapshot_digest text NOT NULL CHECK(snapshot_digest ~ '^[0-9a-f]{64}$'),
      quotation text NOT NULL CHECK(length(btrim(quotation)) BETWEEN 1 AND 2000),page_number integer,xml_path text,
      comparison_note text NOT NULL CHECK(length(btrim(comparison_note)) BETWEEN 1 AND 2000),
      context_reviewed boolean NOT NULL CHECK(context_reviewed),actor_id uuid NOT NULL,request_key uuid NOT NULL,request_digest text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CHECK((page_number IS NOT NULL AND page_number BETWEEN 1 AND 200 AND xml_path IS NULL)
        OR (page_number IS NULL AND xml_path IS NOT NULL AND length(btrim(xml_path)) BETWEEN 1 AND 500)),
      CHECK((case_id IS NULL)=(dependency_id IS NULL)),
      UNIQUE(organization_id,id),UNIQUE(organization_id,logical_document_id,id),UNIQUE(organization_id,case_id,id),
      UNIQUE(organization_id,logical_document_id,revision_number),UNIQUE NULLS NOT DISTINCT(organization_id,logical_document_id,previous_revision_id),
      UNIQUE(organization_id,actor_id,request_key),
      FOREIGN KEY(organization_id,document_ref_id,logical_document_id,scope_id,assignment_proposal_id,invoice_review_id)
        REFERENCES invoice_review_revision(organization_id,document_ref_id,logical_document_id,scope_id,assignment_proposal_id,id),
      FOREIGN KEY(organization_id,property_id,scope_id) REFERENCES accounting_scope(organization_id,property_id,id),
      FOREIGN KEY(organization_id,case_id,assignment_proposal_id,dependency_id) REFERENCES exception_dependency(organization_id,case_id,assignment_proposal_id,id),
      FOREIGN KEY(organization_id,logical_document_id,previous_revision_id) REFERENCES context_decision(organization_id,logical_document_id,id),
      FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
    CREATE FUNCTION context_comparison_access(org uuid,ref uuid,sphere uuid,property uuid) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT public.assignment_proposal_access(org,ref,sphere) AND public.property_master_access(org,property,'read');
    $$;
    REVOKE ALL ON FUNCTION context_comparison_access(uuid,uuid,uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION context_comparison_access(uuid,uuid,uuid,uuid) TO app_rw;
    ALTER TABLE context_decision ENABLE ROW LEVEL SECURITY; ALTER TABLE context_decision FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON context_decision FROM PUBLIC,app_rw,app_worker,app_review; GRANT SELECT ON context_decision TO app_rw;
    CREATE POLICY context_decision_read ON context_decision FOR SELECT USING(context_comparison_access(organization_id,document_ref_id,scope_id,property_id));
    CREATE POLICY context_decision_no_insert ON context_decision FOR INSERT WITH CHECK(false);
    CREATE TRIGGER protect_context_decision BEFORE UPDATE OR DELETE ON context_decision FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();
    ALTER TABLE exception_case_event ADD COLUMN context_decision_id uuid;
    ALTER TABLE exception_case_event ADD CONSTRAINT event_context_decision FOREIGN KEY(organization_id,case_id,context_decision_id) REFERENCES context_decision(organization_id,case_id,id);
    ALTER TABLE exception_case_event DROP CONSTRAINT exception_case_event_action_check;
    ALTER TABLE exception_case_event ADD CONSTRAINT exception_case_event_action_check CHECK(action IN ('evidence_added','claim','release','snooze','reopen','context_reviewed'));
    ALTER TABLE exception_case_event ADD CONSTRAINT typed_context_event CHECK((action<>'context_reviewed' OR context_decision_id IS NOT NULL) AND (action<>'evidence_added' OR context_decision_id IS NULL));

    CREATE FUNCTION context_comparison_snapshot(org uuid,proposal_id uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE p public.assignment_proposal; s public.accounting_scope; property public.property;
    BEGIN
      SELECT * INTO p FROM public.assignment_proposal WHERE organization_id=org AND id=proposal_id;
      SELECT * INTO s FROM public.accounting_scope WHERE organization_id=org AND id=p.scope_id;
      IF p.id IS NULL OR s.id IS NULL OR NOT public.context_comparison_access(org,p.document_ref_id,p.scope_id,s.property_id) THEN
        RAISE EXCEPTION 'Context comparison inaccessible' USING ERRCODE='42501'; END IF;
      SELECT * INTO STRICT property FROM public.property WHERE organization_id=org AND id=s.property_id;
      RETURN jsonb_build_object('property',jsonb_build_object('id',property.id,'name',property.name,'street',property.street,'zip_code',property.zip_code,'city',property.city),
        'scope',jsonb_build_object('id',s.id,'property_id',s.property_id,'management_mandate_id',s.management_mandate_id,'scope_type',s.scope_type,
          'label',s.label,'valid_from',s.valid_from,'valid_to',s.valid_to,'whole_property',s.whole_property));
    END $$;
    REVOKE ALL ON FUNCTION context_comparison_snapshot(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION context_comparison_snapshot(uuid,uuid) TO app_rw;

    CREATE FUNCTION create_context_decision(org uuid,logical_id uuid,proposal_id uuid,review_id uuid,expected uuid,request uuid,
      preview_digest text,expected_event uuid,quote_text text,page integer,path text,note text,ack boolean) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      p public.assignment_proposal; current_p public.assignment_proposal; r public.invoice_review_revision; current_r public.invoice_review_revision;
      s public.accounting_scope; l public.logical_document; prior public.context_decision; current_d public.context_decision;
      dep public.exception_dependency; event public.exception_case_event; snapshot_value jsonb; digest text; created uuid;
    BEGIN
      IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501'; END IF;
      PERFORM public.lock_intake(org);
      SELECT * INTO p FROM public.assignment_proposal WHERE organization_id=org AND id=proposal_id AND logical_document_id=logical_id;
      SELECT * INTO s FROM public.accounting_scope WHERE organization_id=org AND id=p.scope_id;
      IF p.id IS NULL OR s.id IS NULL OR NOT public.context_comparison_access(org,p.document_ref_id,p.scope_id,s.property_id)
        OR NOT public.source_partition_access(org,p.document_ref_id,true) OR NOT public.has_scope_capability(org,p.scope_id,'prepare') THEN
        RAISE EXCEPTION 'Context comparison inaccessible' USING ERRCODE='42501'; END IF;
      IF request IS NULL OR review_id IS NULL OR ack IS DISTINCT FROM true OR preview_digest IS NULL OR preview_digest !~ '^[0-9a-f]{64}$'
        OR quote_text IS NULL OR length(btrim(quote_text)) NOT BETWEEN 1 AND 2000 OR note IS NULL OR length(btrim(note)) NOT BETWEEN 1 AND 2000 THEN
        RAISE EXCEPTION 'Explicit comparison required' USING ERRCODE='22023'; END IF;
      digest:=encode(sha256(convert_to(jsonb_build_array(logical_id,proposal_id,review_id,expected,preview_digest,expected_event,quote_text,page,path,note,ack)::text,'UTF8')),'hex');
      SELECT * INTO prior FROM public.context_decision WHERE organization_id=org AND actor_id=actor AND request_key=request;
      IF FOUND THEN
        IF prior.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Context comparison request changed' USING ERRCODE='23505'; END IF;
        IF prior.case_id IS NOT NULL AND NOT public.exception_case_access(org,prior.case_id) THEN RAISE EXCEPTION 'Case inaccessible' USING ERRCODE='42501'; END IF;
        RETURN prior.id;
      END IF;
      SELECT * INTO current_d FROM public.context_decision WHERE organization_id=org AND logical_document_id=logical_id ORDER BY revision_number DESC LIMIT 1;
      IF current_d.id IS NOT NULL AND NOT public.context_comparison_access(org,current_d.document_ref_id,current_d.scope_id,current_d.property_id) THEN
        RAISE EXCEPTION 'Previous context inaccessible' USING ERRCODE='42501'; END IF;
      IF current_d.id IS DISTINCT FROM expected THEN RAISE EXCEPTION 'Context decision changed' USING ERRCODE='23505'; END IF;
      SELECT * INTO current_p FROM public.assignment_proposal WHERE organization_id=org AND logical_document_id=logical_id ORDER BY revision_number DESC LIMIT 1;
      SELECT * INTO current_r FROM public.invoice_review_revision WHERE organization_id=org AND logical_document_id=logical_id ORDER BY revision_number DESC LIMIT 1;
      IF NOT public.assignment_proposal_access(org,current_p.document_ref_id,current_p.scope_id)
        OR (current_r.id IS NOT NULL AND NOT public.assignment_proposal_access(org,current_r.document_ref_id,current_r.scope_id)) THEN
        RAISE EXCEPTION 'Current comparison basis inaccessible' USING ERRCODE='42501'; END IF;
      IF proposal_id IS DISTINCT FROM current_p.id OR review_id IS DISTINCT FROM current_r.id OR current_r.assignment_proposal_id IS DISTINCT FROM proposal_id
        OR p.partition_revision_id IS DISTINCT FROM (SELECT id FROM public.source_partition_revision WHERE organization_id=org AND document_ref_id=p.document_ref_id ORDER BY revision_number DESC LIMIT 1)
        OR p.extraction_revision_id IS DISTINCT FROM (SELECT id FROM public.extraction_revision WHERE organization_id=org AND logical_document_id=logical_id ORDER BY created_at DESC,id DESC LIMIT 1) THEN
        RAISE EXCEPTION 'Context comparison basis changed' USING ERRCODE='23505'; END IF;
      r:=current_r;
      IF s.scope_type NOT IN ('WEG','MIET') OR NOT s.whole_property OR r.service_period_from<s.valid_from OR (s.valid_to IS NOT NULL AND r.service_period_to>s.valid_to) THEN
        RAISE EXCEPTION 'Unsupported context or period outside scope' USING ERRCODE='23514'; END IF;
      SELECT * INTO STRICT l FROM public.logical_document WHERE organization_id=org AND id=logical_id;
      IF (l.page_start IS NOT NULL AND (page IS NULL OR page NOT BETWEEN l.page_start AND l.page_end OR path IS NOT NULL))
        OR (l.page_start IS NULL AND (page IS NOT NULL OR path IS NULL OR length(btrim(path)) NOT BETWEEN 1 AND 500)) THEN
        RAISE EXCEPTION 'Locator outside selected logical original' USING ERRCODE='22023'; END IF;
      -- Bounded row locks cover property writers that do not take financial-inputs.
      -- NOWAIT also avoids inversion with a writer already holding a row lock.
      BEGIN
        PERFORM 1 FROM public.property WHERE organization_id=org AND id=s.property_id FOR SHARE NOWAIT;
        PERFORM 1 FROM public.accounting_scope WHERE organization_id=org AND id=s.id FOR SHARE NOWAIT;
      EXCEPTION WHEN lock_not_available THEN RAISE EXCEPTION 'Context metadata busy; refresh preview' USING ERRCODE='23505'; END;
      snapshot_value:=public.context_comparison_snapshot(org,proposal_id);
      IF encode(sha256(convert_to(snapshot_value::text,'UTF8')),'hex') IS DISTINCT FROM preview_digest THEN
        RAISE EXCEPTION 'Context preview changed' USING ERRCODE='23505'; END IF;
      SELECT * INTO dep FROM public.exception_dependency WHERE organization_id=org AND assignment_proposal_id=proposal_id AND cause='scope_hint_conflict';
      IF dep.id IS NOT NULL THEN
        IF NOT public.exception_case_access(org,dep.case_id) THEN RAISE EXCEPTION 'Case inaccessible' USING ERRCODE='42501'; END IF;
        SELECT * INTO STRICT event FROM public.exception_case_event WHERE organization_id=org AND case_id=dep.case_id ORDER BY revision_number DESC LIMIT 1;
        IF event.id IS DISTINCT FROM expected_event THEN RAISE EXCEPTION 'Context exception changed' USING ERRCODE='23505'; END IF;
      ELSIF expected_event IS NOT NULL THEN RAISE EXCEPTION 'Unexpected context exception' USING ERRCODE='22023'; END IF;
      INSERT INTO public.context_decision(organization_id,document_ref_id,logical_document_id,scope_id,property_id,assignment_proposal_id,invoice_review_id,
        case_id,dependency_id,previous_revision_id,revision_number,snapshot,snapshot_digest,quotation,page_number,xml_path,comparison_note,context_reviewed,actor_id,request_key,request_digest)
        VALUES(org,p.document_ref_id,logical_id,p.scope_id,s.property_id,proposal_id,review_id,dep.case_id,dep.id,expected,COALESCE(current_d.revision_number,0)+1,
          snapshot_value,preview_digest,quote_text,page,path,note,true,actor,request,digest) RETURNING id INTO created;
      IF dep.id IS NOT NULL THEN
        INSERT INTO public.exception_case_event(organization_id,case_id,previous_event_id,revision_number,action,workflow_state,claimant_id,reason,actor_id,request_key,request_digest,context_decision_id)
          VALUES(org,dep.case_id,event.id,event.revision_number+1,'context_reviewed','open',event.claimant_id,'Human context comparison recorded for one exact proposal',actor,request,digest,created);
      END IF;
      RETURN created;
    END $$;
    REVOKE ALL ON FUNCTION create_context_decision(uuid,uuid,uuid,uuid,uuid,uuid,text,uuid,text,integer,text,text,boolean) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION create_context_decision(uuid,uuid,uuid,uuid,uuid,uuid,text,uuid,text,integer,text,text,boolean) TO app_rw;
    ''')
    replace_function('append_exception_event(uuid,uuid,uuid,uuid,text,text,timestamptz)',APPEND_BEFORE,APPEND_AFTER)
    replace_function('append_exception_event(uuid,uuid,uuid,uuid,text,text,timestamptz)',VALUES_BEFORE,VALUES_AFTER)
    replace_function('exception_case_access(uuid,uuid)',ACCESS_BEFORE,ACCESS_AFTER)
    replace_function('access_coverage_manifest(uuid,jsonb)',BEFORE,AFTER)


def downgrade():
    op.execute('LOCK TABLE context_decision,exception_case_event IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM context_decision) THEN RAISE EXCEPTION 'Retain context comparison evidence; repair forward'; END IF; END $$")
    replace_function('access_coverage_manifest(uuid,jsonb)',AFTER,BEFORE)
    replace_function('exception_case_access(uuid,uuid)',ACCESS_AFTER,ACCESS_BEFORE)
    replace_function('append_exception_event(uuid,uuid,uuid,uuid,text,text,timestamptz)',APPEND_AFTER,APPEND_BEFORE)
    replace_function('append_exception_event(uuid,uuid,uuid,uuid,text,text,timestamptz)',VALUES_AFTER,VALUES_BEFORE)
    op.execute('''
      DROP FUNCTION create_context_decision(uuid,uuid,uuid,uuid,uuid,uuid,text,uuid,text,integer,text,text,boolean),context_comparison_snapshot(uuid,uuid);
      ALTER TABLE exception_case_event DROP CONSTRAINT typed_context_event;
      ALTER TABLE exception_case_event DROP COLUMN context_decision_id;
      ALTER TABLE exception_case_event DROP CONSTRAINT exception_case_event_action_check;
      ALTER TABLE exception_case_event ADD CONSTRAINT exception_case_event_action_check CHECK(action IN ('evidence_added','claim','release','snooze','reopen'));
      DROP TABLE context_decision;
      DROP FUNCTION context_comparison_access(uuid,uuid,uuid,uuid);
      ALTER TABLE invoice_review_revision DROP CONSTRAINT invoice_context_basis;
      ALTER TABLE exception_dependency DROP CONSTRAINT dependency_context_basis;
    ''')
