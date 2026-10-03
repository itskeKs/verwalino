"""Scoped pair evidence; no financial duplicate clearance or source publication."""
from alembic import op
from sqlalchemy import text

revision='0106'
down_revision='0105'
branch_labels=None
depends_on=None
BEFORE="UNION ALL SELECT 'allocation_proposal_line',id,to_jsonb(r) FROM public.allocation_proposal_line r WHERE organization_id=org) evidence"
AFTER=BEFORE.replace(') evidence'," UNION ALL SELECT 'duplicate_comparison',id,to_jsonb(r) FROM public.duplicate_comparison r WHERE organization_id=org) evidence")


def upgrade():
    op.execute('''
    ALTER TABLE invoice_review_revision ADD CONSTRAINT invoice_duplicate_basis UNIQUE(organization_id,scope_id,document_ref_id,logical_document_id,id);
    CREATE TABLE duplicate_comparison(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,scope_id uuid NOT NULL,
      subject_document_ref_id uuid NOT NULL,subject_logical_id uuid NOT NULL,subject_review_id uuid NOT NULL,
      reference_document_ref_id uuid NOT NULL,reference_logical_id uuid NOT NULL,reference_review_id uuid NOT NULL,
      pair_low uuid GENERATED ALWAYS AS (least(subject_logical_id,reference_logical_id)) STORED,
      pair_high uuid GENERATED ALWAYS AS (greatest(subject_logical_id,reference_logical_id)) STORED,
      previous_revision_id uuid,revision_number integer NOT NULL CHECK(revision_number>0),
      verdict text NOT NULL CHECK(verdict IN ('same_document','distinct_documents','replacement_requested')),
      financial_state text NOT NULL DEFAULT 'evidence_only' CHECK(financial_state='evidence_only'),
      evidence jsonb NOT NULL CHECK(jsonb_typeof(evidence)='object'),
      reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 2000),comparison_reviewed boolean NOT NULL CHECK(comparison_reviewed),
      actor_id uuid NOT NULL,request_key uuid NOT NULL,request_digest text NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CHECK(subject_logical_id<>reference_logical_id),
      UNIQUE(organization_id,id),UNIQUE(organization_id,scope_id,pair_low,pair_high,id),
      UNIQUE(organization_id,scope_id,pair_low,pair_high,revision_number),
      UNIQUE NULLS NOT DISTINCT(organization_id,scope_id,pair_low,pair_high,previous_revision_id),
      UNIQUE(organization_id,actor_id,request_key),
      FOREIGN KEY(organization_id,scope_id,subject_document_ref_id,subject_logical_id,subject_review_id)
        REFERENCES invoice_review_revision(organization_id,scope_id,document_ref_id,logical_document_id,id),
      FOREIGN KEY(organization_id,scope_id,reference_document_ref_id,reference_logical_id,reference_review_id)
        REFERENCES invoice_review_revision(organization_id,scope_id,document_ref_id,logical_document_id,id),
      FOREIGN KEY(organization_id,scope_id,pair_low,pair_high,previous_revision_id)
        REFERENCES duplicate_comparison(organization_id,scope_id,pair_low,pair_high,id),
      FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));

    CREATE FUNCTION duplicate_comparison_access(org uuid,target uuid) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT EXISTS(SELECT 1 FROM public.duplicate_comparison d WHERE d.organization_id=org AND d.id=target
        AND public.assignment_proposal_access(org,d.subject_document_ref_id,d.scope_id)
        AND public.assignment_proposal_access(org,d.reference_document_ref_id,d.scope_id));
    $$;
    REVOKE ALL ON FUNCTION duplicate_comparison_access(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION duplicate_comparison_access(uuid,uuid) TO app_rw;
    ALTER TABLE duplicate_comparison ENABLE ROW LEVEL SECURITY; ALTER TABLE duplicate_comparison FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON duplicate_comparison FROM PUBLIC,app_rw,app_worker,app_review; GRANT SELECT ON duplicate_comparison TO app_rw;
    CREATE POLICY duplicate_read ON duplicate_comparison FOR SELECT USING(duplicate_comparison_access(organization_id,id));
    CREATE POLICY duplicate_no_insert ON duplicate_comparison FOR INSERT WITH CHECK(false);
    CREATE TRIGGER protect_duplicate_comparison BEFORE UPDATE OR DELETE ON duplicate_comparison FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();

    CREATE FUNCTION require_current_invoice_review(org uuid,target uuid,writing boolean) RETURNS public.invoice_review_revision
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE r public.invoice_review_revision; head public.invoice_review_revision; p public.assignment_proposal;
    BEGIN
      IF writing THEN PERFORM public.lock_intake(org); END IF;
      SELECT * INTO r FROM public.invoice_review_revision WHERE organization_id=org AND id=target;
      IF r.id IS NULL OR NOT public.assignment_proposal_access(org,r.document_ref_id,r.scope_id)
        OR (writing AND (NOT public.source_partition_access(org,r.document_ref_id,true) OR NOT public.has_scope_capability(org,r.scope_id,'prepare'))) THEN
        RAISE EXCEPTION 'Review inaccessible' USING ERRCODE='42501'; END IF;
      SELECT * INTO head FROM public.invoice_review_revision WHERE organization_id=org AND logical_document_id=r.logical_document_id ORDER BY revision_number DESC LIMIT 1;
      SELECT * INTO p FROM public.assignment_proposal WHERE organization_id=org AND logical_document_id=r.logical_document_id ORDER BY revision_number DESC LIMIT 1;
      IF NOT public.assignment_proposal_access(org,head.document_ref_id,head.scope_id) OR NOT public.assignment_proposal_access(org,p.document_ref_id,p.scope_id) THEN
        RAISE EXCEPTION 'Current review context inaccessible' USING ERRCODE='42501'; END IF;
      IF r.id IS DISTINCT FROM head.id OR r.assignment_proposal_id IS DISTINCT FROM p.id
        OR r.partition_revision_id IS DISTINCT FROM (SELECT id FROM public.source_partition_revision WHERE organization_id=org AND document_ref_id=r.document_ref_id ORDER BY revision_number DESC LIMIT 1)
        OR r.extraction_revision_id IS DISTINCT FROM (SELECT id FROM public.extraction_revision WHERE organization_id=org AND logical_document_id=r.logical_document_id ORDER BY created_at DESC,id DESC LIMIT 1) THEN
        RAISE EXCEPTION 'Review basis changed' USING ERRCODE='23505'; END IF;
      RETURN r;
    END $$;
    REVOKE ALL ON FUNCTION require_current_invoice_review(uuid,uuid,boolean) FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION duplicate_pair_evidence(org uuid,subject uuid,reference uuid) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      WITH pair AS (
        SELECT a,b,la,lb,
          a.content_sha256=b.content_sha256 AND la.page_start IS NOT DISTINCT FROM lb.page_start AND la.page_end IS NOT DISTINCT FROM lb.page_end AS same_source,
          lower(regexp_replace(btrim(a.seller_name),'[[:space:]]+',' ','g'))=lower(regexp_replace(btrim(b.seller_name),'[[:space:]]+',' ','g'))
            AND btrim(a.document_number)=btrim(b.document_number) AND a.document_kind=b.document_kind AND a.currency=b.currency
            AND a.gross_amount=b.gross_amount AND a.service_period_from=b.service_period_from AND a.service_period_to=b.service_period_to AS same_identity
        FROM public.invoice_review_revision a JOIN public.invoice_review_revision b ON b.organization_id=a.organization_id AND b.scope_id=a.scope_id
        JOIN public.logical_document la ON la.organization_id=a.organization_id AND la.id=a.logical_document_id
        JOIN public.logical_document lb ON lb.organization_id=b.organization_id AND lb.id=b.logical_document_id
        WHERE a.organization_id=org AND a.id=subject AND b.id=reference AND a.logical_document_id<>b.logical_document_id)
      SELECT jsonb_build_object('normalization_version','pg16-lower-space-v1',
        'match_reasons',to_jsonb(array_remove(ARRAY[CASE WHEN same_source THEN 'same_bytes_and_range' END,CASE WHEN same_identity THEN 'same_reviewed_identity' END],NULL)),
        'kind_conflict',same_source AND (a).document_kind<>(b).document_kind,
        'subject',jsonb_build_object('content_sha256',(a).content_sha256,'page_start',(la).page_start,'page_end',(la).page_end),
        'reference',jsonb_build_object('content_sha256',(b).content_sha256,'page_start',(lb).page_start,'page_end',(lb).page_end),
        'field_disagreements',COALESCE((SELECT jsonb_agg(k ORDER BY k) FROM unnest(ARRAY['seller_name','document_number','document_kind','currency','invoice_date','service_period_from','service_period_to','gross_amount','vat_amount','vat_rate']) k
          WHERE to_jsonb(a)->k IS DISTINCT FROM to_jsonb(b)->k),'[]'::jsonb)) FROM pair;
    $$;
    REVOKE ALL ON FUNCTION duplicate_pair_evidence(uuid,uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION duplicate_candidates(org uuid,subject uuid,after_logical uuid,take integer) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE r public.invoice_review_revision; result jsonb;
    BEGIN
      IF take IS NULL OR take NOT BETWEEN 1 AND 50 THEN RAISE EXCEPTION 'Bounded page required' USING ERRCODE='22023'; END IF;
      r:=public.require_current_invoice_review(org,subject,false);
      WITH heads AS MATERIALIZED (
        SELECT DISTINCT ON(logical_document_id) * FROM public.invoice_review_revision WHERE organization_id=org ORDER BY logical_document_id,revision_number DESC
      ), visible AS MATERIALIZED (
        SELECT h.* FROM heads h WHERE h.scope_id=r.scope_id AND h.logical_document_id<>r.logical_document_id
          AND (after_logical IS NULL OR h.logical_document_id>after_logical)
          AND public.assignment_proposal_access(org,h.document_ref_id,h.scope_id)
      ), matches AS MATERIALIZED (
        SELECT h.*,public.duplicate_pair_evidence(org,r.id,h.id) AS evidence FROM visible h
      ), page AS (
        SELECT * FROM matches WHERE jsonb_array_length(evidence->'match_reasons')>0 ORDER BY logical_document_id LIMIT take+1
      ) SELECT COALESCE(jsonb_agg(jsonb_build_object('review_id',h.id,'logical_document_id',h.logical_document_id,'document_ref_id',h.document_ref_id,
          'seller_name',h.seller_name,'document_number',h.document_number,'document_kind',h.document_kind,'gross_amount',h.gross_amount::text,'currency',h.currency,
          'can_compare',public.source_partition_access(org,r.document_ref_id,true) AND public.source_partition_access(org,h.document_ref_id,true) AND public.has_scope_capability(org,r.scope_id,'prepare'),
          'evidence',h.evidence,'latest_comparison',CASE WHEN public.duplicate_comparison_access(org,d.id) THEN
            to_jsonb(d)-ARRAY['organization_id','request_key','request_digest'] END) ORDER BY h.logical_document_id),'[]'::jsonb)
        INTO result FROM page h LEFT JOIN LATERAL(
          SELECT * FROM public.duplicate_comparison WHERE organization_id=org AND scope_id=r.scope_id
            AND pair_low=least(r.logical_document_id,h.logical_document_id) AND pair_high=greatest(r.logical_document_id,h.logical_document_id)
            ORDER BY revision_number DESC LIMIT 1) d ON true;
      RETURN jsonb_build_object('scope_id',r.scope_id,'items',result,'financial_state','evidence_only','normalization_version','pg16-lower-space-v1');
    END $$;
    REVOKE ALL ON FUNCTION duplicate_candidates(uuid,uuid,uuid,integer) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION duplicate_candidates(uuid,uuid,uuid,integer) TO app_rw;

    CREATE FUNCTION create_duplicate_comparison(org uuid,subject uuid,reference uuid,expected uuid,request uuid,verdict_value text,explanation text,ack boolean) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      a public.invoice_review_revision; b public.invoice_review_revision; prior public.duplicate_comparison; head public.duplicate_comparison;
      digest text; facts jsonb; created uuid;
    BEGIN
      IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501'; END IF;
      PERFORM public.lock_intake(org);
      SELECT * INTO a FROM public.invoice_review_revision WHERE organization_id=org AND id=subject;
      SELECT * INTO b FROM public.invoice_review_revision WHERE organization_id=org AND id=reference;
      IF a.id IS NULL OR b.id IS NULL OR NOT public.assignment_proposal_access(org,a.document_ref_id,a.scope_id)
        OR NOT public.assignment_proposal_access(org,b.document_ref_id,b.scope_id)
        OR NOT public.source_partition_access(org,a.document_ref_id,true) OR NOT public.source_partition_access(org,b.document_ref_id,true)
        OR NOT public.has_scope_capability(org,a.scope_id,'prepare') OR NOT public.has_scope_capability(org,b.scope_id,'prepare') THEN
        RAISE EXCEPTION 'Comparison sources inaccessible' USING ERRCODE='42501'; END IF;
      IF a.logical_document_id=b.logical_document_id OR a.scope_id<>b.scope_id OR request IS NULL OR ack IS DISTINCT FROM true
        OR verdict_value IS NULL OR verdict_value NOT IN ('same_document','distinct_documents','replacement_requested')
        OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 2000 THEN
        RAISE EXCEPTION 'Explicit same-scope distinct-source comparison required' USING ERRCODE='22023'; END IF;
      digest:=encode(sha256(convert_to(jsonb_build_array(subject,reference,expected,verdict_value,explanation,ack)::text,'UTF8')),'hex');
      SELECT * INTO prior FROM public.duplicate_comparison WHERE organization_id=org AND actor_id=actor AND request_key=request;
      IF FOUND THEN
        IF prior.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Comparison request changed' USING ERRCODE='23505'; END IF;
        RETURN prior.id;
      END IF;
      a:=public.require_current_invoice_review(org,subject,true);b:=public.require_current_invoice_review(org,reference,true);
      SELECT * INTO head FROM public.duplicate_comparison WHERE organization_id=org AND scope_id=a.scope_id
        AND pair_low=least(a.logical_document_id,b.logical_document_id) AND pair_high=greatest(a.logical_document_id,b.logical_document_id)
        ORDER BY revision_number DESC LIMIT 1;
      IF head.id IS NOT NULL AND NOT public.duplicate_comparison_access(org,head.id) THEN RAISE EXCEPTION 'Prior comparison inaccessible' USING ERRCODE='42501'; END IF;
      IF head.id IS DISTINCT FROM expected THEN RAISE EXCEPTION 'Comparison changed' USING ERRCODE='23505'; END IF;
      facts:=public.duplicate_pair_evidence(org,subject,reference);
      IF facts IS NULL OR jsonb_array_length(facts->'match_reasons')=0 OR (facts->>'kind_conflict')::boolean
        OR ((facts->'match_reasons') ? 'same_bytes_and_range' AND verdict_value<>'same_document') THEN
        RAISE EXCEPTION 'Matching nonconflicting evidence required for this disposition' USING ERRCODE='23514'; END IF;
      INSERT INTO public.duplicate_comparison(organization_id,scope_id,subject_document_ref_id,subject_logical_id,subject_review_id,
        reference_document_ref_id,reference_logical_id,reference_review_id,previous_revision_id,revision_number,verdict,evidence,reason,comparison_reviewed,actor_id,request_key,request_digest)
      VALUES(org,a.scope_id,a.document_ref_id,a.logical_document_id,a.id,b.document_ref_id,b.logical_document_id,b.id,
        expected,COALESCE(head.revision_number,0)+1,verdict_value,facts,explanation,true,actor,request,digest) RETURNING id INTO created;
      RETURN created;
    END $$;
    REVOKE ALL ON FUNCTION create_duplicate_comparison(uuid,uuid,uuid,uuid,uuid,text,text,boolean) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION create_duplicate_comparison(uuid,uuid,uuid,uuid,uuid,text,text,boolean) TO app_rw;
    ''')
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(BEFORE)==1
    op.execute(definition.replace(BEFORE,AFTER))


def downgrade():
    op.execute('LOCK TABLE duplicate_comparison IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM duplicate_comparison) THEN RAISE EXCEPTION 'Retain duplicate comparison evidence; repair forward'; END IF; END $$")
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(AFTER)==1
    op.execute(definition.replace(AFTER,BEFORE))
    op.execute('''DROP FUNCTION create_duplicate_comparison(uuid,uuid,uuid,uuid,uuid,text,text,boolean),duplicate_candidates(uuid,uuid,uuid,integer),duplicate_pair_evidence(uuid,uuid,uuid),require_current_invoice_review(uuid,uuid,boolean);
      DROP TABLE duplicate_comparison;
      DROP FUNCTION duplicate_comparison_access(uuid,uuid);
      ALTER TABLE invoice_review_revision DROP CONSTRAINT invoice_duplicate_basis;''')
