"""Pin explicit human invoice field reviews without adopting costs or resolving cases."""
from alembic import op
from sqlalchemy import text

revision='0103'
down_revision='0102'
branch_labels=None
depends_on=None
BEFORE="UNION ALL SELECT 'exception_case_event',id,to_jsonb(r) FROM public.exception_case_event r WHERE organization_id=org) evidence"
AFTER=BEFORE.replace(') evidence'," UNION ALL SELECT 'invoice_review_revision',id,to_jsonb(r) FROM public.invoice_review_revision r WHERE organization_id=org) evidence")


def upgrade():
    op.execute('''
    ALTER TABLE assignment_proposal ADD CONSTRAINT assignment_review_basis UNIQUE(organization_id,document_ref_id,logical_document_id,partition_revision_id,extraction_revision_id,scope_id,id);
    ALTER TABLE extraction_revision ADD CONSTRAINT extraction_review_hash UNIQUE(organization_id,id,content_sha256);
    CREATE TABLE invoice_review_revision(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),
      document_ref_id uuid NOT NULL,logical_document_id uuid NOT NULL,partition_revision_id uuid NOT NULL,extraction_revision_id uuid NOT NULL,
      scope_id uuid NOT NULL,assignment_proposal_id uuid NOT NULL,content_sha256 text NOT NULL,
      previous_revision_id uuid,revision_number integer NOT NULL CHECK(revision_number>0),
      seller_name text NOT NULL CHECK(length(btrim(seller_name)) BETWEEN 1 AND 255),
      document_number text NOT NULL CHECK(length(btrim(document_number)) BETWEEN 1 AND 100),
      document_kind text NOT NULL CHECK(document_kind IN ('invoice','credit_note')),currency text NOT NULL CHECK(currency='EUR'),
      invoice_date date NOT NULL CHECK(invoice_date BETWEEN DATE '0001-01-01' AND DATE '9999-12-31'),service_period_from date NOT NULL CHECK(service_period_from BETWEEN DATE '0001-01-01' AND DATE '9999-12-31'),
      service_period_to date NOT NULL CHECK(service_period_to BETWEEN DATE '0001-01-01' AND DATE '9999-12-31' AND service_period_to>=service_period_from),
      gross_amount numeric(14,2) NOT NULL CHECK(gross_amount::text NOT IN ('NaN','Infinity','-Infinity') AND ((document_kind='invoice' AND gross_amount>0) OR (document_kind='credit_note' AND gross_amount<0))),
      vat_amount numeric(14,2) CHECK(vat_amount::text NOT IN ('NaN','Infinity','-Infinity') AND abs(vat_amount)<=abs(gross_amount) AND (vat_amount=0 OR sign(vat_amount)=sign(gross_amount))),
      vat_rate numeric(5,2) CHECK(vat_rate BETWEEN 0 AND 100),
      fields_reviewed boolean NOT NULL CHECK(fields_reviewed),review_scope text NOT NULL DEFAULT 'invoice_fields' CHECK(review_scope='invoice_fields'),
      reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 1000),actor_id uuid NOT NULL,
      request_key uuid NOT NULL,request_digest text NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(organization_id,id),UNIQUE(organization_id,logical_document_id,id),UNIQUE(organization_id,logical_document_id,revision_number),
      UNIQUE NULLS NOT DISTINCT(organization_id,logical_document_id,previous_revision_id),UNIQUE(organization_id,actor_id,request_key),
      FOREIGN KEY(organization_id,document_ref_id,logical_document_id,partition_revision_id,extraction_revision_id,scope_id,assignment_proposal_id)
        REFERENCES assignment_proposal(organization_id,document_ref_id,logical_document_id,partition_revision_id,extraction_revision_id,scope_id,id),
      FOREIGN KEY(organization_id,extraction_revision_id,content_sha256) REFERENCES extraction_revision(organization_id,id,content_sha256),
      FOREIGN KEY(organization_id,logical_document_id,previous_revision_id) REFERENCES invoice_review_revision(organization_id,logical_document_id,id),
      FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
    ALTER TABLE invoice_review_revision ENABLE ROW LEVEL SECURITY; ALTER TABLE invoice_review_revision FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON invoice_review_revision FROM PUBLIC,app_rw,app_worker,app_review; GRANT SELECT ON invoice_review_revision TO app_rw;
    CREATE POLICY invoice_review_read ON invoice_review_revision FOR SELECT USING(assignment_proposal_access(organization_id,document_ref_id,scope_id));
    CREATE POLICY invoice_review_no_insert ON invoice_review_revision FOR INSERT WITH CHECK(false);
    CREATE TRIGGER protect_invoice_review BEFORE UPDATE OR DELETE ON invoice_review_revision FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();

    CREATE FUNCTION create_invoice_review(org uuid,logical_id uuid,proposal_id uuid,expected uuid,request uuid,fields jsonb,explanation text,acknowledged boolean) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      proposal public.assignment_proposal; current_proposal public.assignment_proposal; extraction public.extraction_revision; prior public.invoice_review_revision; current_review public.invoice_review_revision;
      gross numeric; vat numeric; rate numeric; invoice_day date; period_from date; period_to date; canonical jsonb; digest text; created uuid;
      names text[]:=ARRAY['seller_name','document_number','document_kind','currency','invoice_date','service_period_from','service_period_to','gross_amount','vat_amount','vat_rate'];
    BEGIN
      IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501'; END IF;
      PERFORM public.lock_intake(org);
      SELECT * INTO proposal FROM public.assignment_proposal WHERE organization_id=org AND id=proposal_id AND logical_document_id=logical_id;
      IF NOT FOUND OR NOT public.assignment_proposal_access(org,proposal.document_ref_id,proposal.scope_id)
        OR NOT public.source_partition_access(org,proposal.document_ref_id,true) OR NOT public.has_scope_capability(org,proposal.scope_id,'prepare') THEN
        RAISE EXCEPTION 'Invoice review context inaccessible' USING ERRCODE='42501'; END IF;
      IF request IS NULL OR acknowledged IS DISTINCT FROM true OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 1000
        OR fields IS NULL OR jsonb_typeof(fields)<>'object' OR NOT fields ?& names OR fields-names<>'{}'::jsonb THEN
        RAISE EXCEPTION 'Explicit reviewed invoice fields required' USING ERRCODE='22023'; END IF;
      IF EXISTS(SELECT 1 FROM jsonb_each(fields) WHERE key NOT IN ('vat_amount','vat_rate') AND jsonb_typeof(value)<>'string')
        OR EXISTS(SELECT 1 FROM jsonb_each(fields) WHERE key IN ('vat_amount','vat_rate') AND jsonb_typeof(value) NOT IN ('string','null')) THEN
        RAISE EXCEPTION 'Reviewed fields require strings or explicit unknown VAT' USING ERRCODE='22023'; END IF;
      gross:=(fields->>'gross_amount')::numeric;vat:=(fields->>'vat_amount')::numeric;rate:=(fields->>'vat_rate')::numeric;
      IF gross IS NULL OR EXISTS(SELECT 1 FROM unnest(ARRAY[gross,vat,rate]) amount WHERE amount IS NOT NULL
        AND (amount::text IN ('NaN','Infinity','-Infinity') OR round(amount,2)<>amount OR abs(amount)>=1000000000000)) THEN
        RAISE EXCEPTION 'Finite exact cents required without rounding' USING ERRCODE='22023'; END IF;
      IF EXISTS(SELECT 1 FROM unnest(ARRAY['invoice_date','service_period_from','service_period_to']) name WHERE fields->>name !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$') THEN
        RAISE EXCEPTION 'Explicit ISO dates required' USING ERRCODE='22023'; END IF;
      invoice_day:=(fields->>'invoice_date')::date;period_from:=(fields->>'service_period_from')::date;period_to:=(fields->>'service_period_to')::date;
      canonical:=jsonb_build_object('seller_name',btrim(fields->>'seller_name'),'document_number',btrim(fields->>'document_number'),
        'document_kind',fields->>'document_kind','currency',fields->>'currency','invoice_date',invoice_day,'service_period_from',period_from,'service_period_to',period_to,
        'gross_amount',round(gross,2)::text,'vat_amount',round(vat,2)::text,'vat_rate',round(rate,2)::text);
      digest:=encode(sha256(convert_to(jsonb_build_array(logical_id,proposal_id,expected,canonical,explanation,acknowledged)::text,'UTF8')),'hex');
      SELECT * INTO prior FROM public.invoice_review_revision WHERE organization_id=org AND actor_id=actor AND request_key=request;
      IF FOUND THEN
        IF prior.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Invoice review request changed' USING ERRCODE='23505'; END IF;
        RETURN prior.id;
      END IF;
      SELECT * INTO current_review FROM public.invoice_review_revision WHERE organization_id=org AND logical_document_id=logical_id ORDER BY revision_number DESC LIMIT 1;
      IF current_review.id IS NOT NULL AND NOT public.assignment_proposal_access(org,current_review.document_ref_id,current_review.scope_id) THEN
        RAISE EXCEPTION 'Previous invoice review inaccessible' USING ERRCODE='42501'; END IF;
      IF current_review.id IS DISTINCT FROM expected THEN RAISE EXCEPTION 'Invoice review changed' USING ERRCODE='23505'; END IF;
      SELECT * INTO current_proposal FROM public.assignment_proposal WHERE organization_id=org AND logical_document_id=logical_id ORDER BY revision_number DESC LIMIT 1;
      IF NOT public.assignment_proposal_access(org,current_proposal.document_ref_id,current_proposal.scope_id) THEN
        RAISE EXCEPTION 'Current assignment context inaccessible' USING ERRCODE='42501'; END IF;
      IF proposal_id IS DISTINCT FROM current_proposal.id
        OR proposal.partition_revision_id IS DISTINCT FROM (SELECT id FROM public.source_partition_revision WHERE organization_id=org AND document_ref_id=proposal.document_ref_id ORDER BY revision_number DESC LIMIT 1)
        OR proposal.extraction_revision_id IS DISTINCT FROM (SELECT id FROM public.extraction_revision WHERE organization_id=org AND logical_document_id=logical_id ORDER BY created_at DESC,id DESC LIMIT 1) THEN
        RAISE EXCEPTION 'Invoice review basis changed' USING ERRCODE='23505'; END IF;
      SELECT * INTO extraction FROM public.extraction_revision WHERE organization_id=org AND id=proposal.extraction_revision_id AND status IN ('succeeded','needs_review') AND payload IS NOT NULL;
      IF NOT FOUND THEN RAISE EXCEPTION 'Usable extraction required' USING ERRCODE='23514'; END IF;
      INSERT INTO public.invoice_review_revision(organization_id,document_ref_id,logical_document_id,partition_revision_id,extraction_revision_id,scope_id,
        assignment_proposal_id,content_sha256,previous_revision_id,revision_number,seller_name,document_number,document_kind,currency,
        invoice_date,service_period_from,service_period_to,gross_amount,vat_amount,vat_rate,fields_reviewed,reason,actor_id,request_key,request_digest)
      VALUES(org,proposal.document_ref_id,logical_id,proposal.partition_revision_id,proposal.extraction_revision_id,proposal.scope_id,proposal_id,extraction.content_sha256,
        expected,COALESCE(current_review.revision_number,0)+1,canonical->>'seller_name',canonical->>'document_number',canonical->>'document_kind',canonical->>'currency',
        invoice_day,period_from,period_to,gross,vat,rate,true,explanation,actor,request,digest) RETURNING id INTO created;
      RETURN created;
    END $$;
    REVOKE ALL ON FUNCTION create_invoice_review(uuid,uuid,uuid,uuid,uuid,jsonb,text,boolean) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION create_invoice_review(uuid,uuid,uuid,uuid,uuid,jsonb,text,boolean) TO app_rw;
    ''')
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(BEFORE)==1
    op.execute(definition.replace(BEFORE,AFTER))


def downgrade():
    op.execute('LOCK TABLE invoice_review_revision IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM invoice_review_revision) THEN RAISE EXCEPTION 'Retain invoice field review evidence; repair forward'; END IF; END $$")
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(AFTER)==1
    op.execute(definition.replace(AFTER,BEFORE))
    op.execute('''DROP FUNCTION create_invoice_review(uuid,uuid,uuid,uuid,uuid,jsonb,text,boolean);
      DROP TABLE invoice_review_revision;
      ALTER TABLE assignment_proposal DROP CONSTRAINT assignment_review_basis;
      ALTER TABLE extraction_revision DROP CONSTRAINT extraction_review_hash;''')
