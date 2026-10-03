"""Manual context proposals pin exact reviewed source and extraction revisions."""
from alembic import op
from sqlalchemy import text

revision='0101'
down_revision='0100'
branch_labels=None
depends_on=None
BEFORE="UNION ALL SELECT 'source_partition_member',id,to_jsonb(r) FROM public.source_partition_member r WHERE organization_id=org) evidence"
AFTER="UNION ALL SELECT 'source_partition_member',id,to_jsonb(r) FROM public.source_partition_member r WHERE organization_id=org UNION ALL SELECT 'assignment_proposal',id,to_jsonb(r) FROM public.assignment_proposal r WHERE organization_id=org) evidence"


def upgrade():
    op.execute('''
    ALTER TABLE extraction_revision ADD CONSTRAINT extraction_assignment_source UNIQUE(organization_id,document_ref_id,logical_document_id,id);
    CREATE TABLE assignment_proposal(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),document_ref_id uuid NOT NULL,
      logical_document_id uuid NOT NULL,partition_revision_id uuid NOT NULL,extraction_revision_id uuid NOT NULL,
      scope_id uuid NOT NULL,scope_hint_id uuid,previous_proposal_id uuid,revision_number integer NOT NULL CHECK(revision_number>0),
      method text NOT NULL DEFAULT 'manual_selection' CHECK(method='manual_selection'),
      state text NOT NULL CHECK(state IN ('proposed','blocked')),blockers text[] NOT NULL,
      reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 1000),actor_id uuid NOT NULL,
      request_key uuid NOT NULL,request_digest text NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CHECK(blockers<@ARRAY['scope_hint_conflict','unsupported_scope_type']::text[] AND (state='blocked')=(cardinality(blockers)>0)),
      UNIQUE(organization_id,id),UNIQUE(organization_id,logical_document_id,id),UNIQUE(organization_id,logical_document_id,revision_number),
      UNIQUE NULLS NOT DISTINCT(organization_id,logical_document_id,previous_proposal_id),UNIQUE(organization_id,actor_id,request_key),
      FOREIGN KEY(organization_id,document_ref_id,logical_document_id) REFERENCES logical_document(organization_id,document_ref_id,id),
      FOREIGN KEY(organization_id,partition_revision_id,logical_document_id) REFERENCES source_partition_member(organization_id,partition_revision_id,logical_document_id),
      FOREIGN KEY(organization_id,document_ref_id,logical_document_id,extraction_revision_id) REFERENCES extraction_revision(organization_id,document_ref_id,logical_document_id,id),
      FOREIGN KEY(organization_id,scope_id) REFERENCES accounting_scope(organization_id,id),
      FOREIGN KEY(organization_id,scope_hint_id) REFERENCES accounting_scope(organization_id,id),
      FOREIGN KEY(organization_id,logical_document_id,previous_proposal_id) REFERENCES assignment_proposal(organization_id,logical_document_id,id),
      FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
    CREATE FUNCTION assignment_proposal_access(org uuid,ref_id uuid,sphere uuid) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT public.source_partition_access(org,ref_id,false) AND public.has_scope_capability(org,sphere,'read')
        AND (NULLIF(current_setting('app.current_scope_id',true),'') IS NULL OR sphere=NULLIF(current_setting('app.current_scope_id',true),'')::uuid);
    $$;
    ALTER TABLE assignment_proposal ENABLE ROW LEVEL SECURITY; ALTER TABLE assignment_proposal FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON assignment_proposal FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT SELECT ON assignment_proposal TO app_rw;
    CREATE POLICY assignment_proposal_read ON assignment_proposal FOR SELECT USING(assignment_proposal_access(organization_id,document_ref_id,scope_id));
    CREATE POLICY assignment_proposal_no_insert ON assignment_proposal FOR INSERT WITH CHECK(false);
    CREATE TRIGGER protect_assignment_proposal BEFORE UPDATE OR DELETE ON assignment_proposal FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();

    CREATE FUNCTION create_assignment_proposal(org uuid,logical_id uuid,partition_id uuid,extraction_id uuid,sphere uuid,expected uuid,request uuid,explanation text) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      logical public.logical_document; extraction public.extraction_revision; scope public.accounting_scope;
      prior public.assignment_proposal; current_proposal public.assignment_proposal;
      current_partition uuid; hint uuid; digest text; created uuid; conflicts text[]:=ARRAY[]::text[];
    BEGIN
      IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501'; END IF;
      PERFORM public.lock_intake(org);
      SELECT * INTO logical FROM public.logical_document WHERE organization_id=org AND id=logical_id;
      IF NOT FOUND OR NOT public.source_partition_access(org,logical.document_ref_id,true)
        OR NOT public.assignment_proposal_access(org,logical.document_ref_id,sphere) OR NOT public.has_scope_capability(org,sphere,'prepare') THEN
        RAISE EXCEPTION 'Assignment context inaccessible' USING ERRCODE='42501'; END IF;
      IF request IS NULL OR partition_id IS NULL OR extraction_id IS NULL OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'Explicit context and evidence required' USING ERRCODE='22023'; END IF;
      digest:=encode(sha256(convert_to(jsonb_build_array(logical_id,partition_id,extraction_id,sphere,expected,explanation)::text,'UTF8')),'hex');
      SELECT * INTO prior FROM public.assignment_proposal WHERE organization_id=org AND actor_id=actor AND request_key=request;
      IF FOUND THEN
        IF prior.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Assignment request changed' USING ERRCODE='23505'; END IF;
        RETURN prior.id;
      END IF;
      SELECT * INTO current_proposal FROM public.assignment_proposal WHERE organization_id=org AND logical_document_id=logical_id ORDER BY revision_number DESC LIMIT 1;
      IF current_proposal.id IS NOT NULL AND NOT public.assignment_proposal_access(org,logical.document_ref_id,current_proposal.scope_id) THEN
        RAISE EXCEPTION 'Previous assignment context inaccessible' USING ERRCODE='42501'; END IF;
      IF current_proposal.id IS DISTINCT FROM expected THEN RAISE EXCEPTION 'Assignment changed' USING ERRCODE='23505'; END IF;
      IF NOT EXISTS(SELECT 1 FROM public.source_partition_member WHERE organization_id=org AND document_ref_id=logical.document_ref_id
        AND partition_revision_id=partition_id AND logical_document_id=logical_id) THEN
        RAISE EXCEPTION 'Partition member mismatch' USING ERRCODE='23514'; END IF;
      SELECT id INTO current_partition FROM public.source_partition_revision WHERE organization_id=org AND document_ref_id=logical.document_ref_id ORDER BY revision_number DESC LIMIT 1;
      IF current_partition IS DISTINCT FROM partition_id THEN RAISE EXCEPTION 'Source partition changed' USING ERRCODE='23505'; END IF;
      SELECT * INTO extraction FROM public.extraction_revision WHERE organization_id=org AND id=extraction_id AND logical_document_id=logical_id
        AND document_ref_id=logical.document_ref_id AND content_sha256=logical.content_sha256 AND status IN ('succeeded','needs_review') AND payload IS NOT NULL;
      IF NOT FOUND THEN RAISE EXCEPTION 'Extraction source mismatch' USING ERRCODE='23514'; END IF;
      IF extraction_id IS DISTINCT FROM (SELECT id FROM public.extraction_revision WHERE organization_id=org AND logical_document_id=logical_id ORDER BY created_at DESC,id DESC LIMIT 1) THEN
        RAISE EXCEPTION 'Extraction revision changed' USING ERRCODE='23505'; END IF;
      SELECT * INTO scope FROM public.accounting_scope WHERE organization_id=org AND id=sphere;
      SELECT b.scope_hint_id INTO hint FROM public.document_ref r JOIN public.intake_original o ON o.organization_id=r.organization_id AND o.id=r.intake_original_id
        JOIN public.intake_item i ON i.organization_id=o.organization_id AND i.id=o.item_id JOIN public.intake_batch b ON b.organization_id=i.organization_id AND b.id=i.batch_id
        WHERE r.organization_id=org AND r.id=logical.document_ref_id;
      IF hint IS NOT NULL AND hint<>sphere THEN conflicts:=array_append(conflicts,'scope_hint_conflict'); END IF;
      IF scope.scope_type='SEV' THEN conflicts:=array_append(conflicts,'unsupported_scope_type'); END IF;
      INSERT INTO public.assignment_proposal(organization_id,document_ref_id,logical_document_id,partition_revision_id,extraction_revision_id,
        scope_id,scope_hint_id,previous_proposal_id,revision_number,state,blockers,reason,actor_id,request_key,request_digest)
        VALUES(org,logical.document_ref_id,logical_id,partition_id,extraction_id,sphere,hint,expected,COALESCE(current_proposal.revision_number,0)+1,
          CASE WHEN cardinality(conflicts)=0 THEN 'proposed' ELSE 'blocked' END,conflicts,explanation,actor,request,digest) RETURNING id INTO created;
      RETURN created;
    END $$;
    REVOKE ALL ON FUNCTION assignment_proposal_access(uuid,uuid,uuid),create_assignment_proposal(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION assignment_proposal_access(uuid,uuid,uuid),create_assignment_proposal(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text) TO app_rw;
    ''')
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(BEFORE)==1
    op.execute(definition.replace(BEFORE,AFTER))


def downgrade():
    op.execute('LOCK TABLE assignment_proposal IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM assignment_proposal) THEN RAISE EXCEPTION 'Retain assignment proposal evidence; repair forward'; END IF; END $$")
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(AFTER)==1
    op.execute(definition.replace(AFTER,BEFORE))
    op.execute('''DROP FUNCTION create_assignment_proposal(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text);
      DROP TABLE assignment_proposal;
      DROP FUNCTION assignment_proposal_access(uuid,uuid,uuid);
      ALTER TABLE extraction_revision DROP CONSTRAINT extraction_assignment_source;''')
