"""Conserve exact reviewed cents in immutable single-scope breakdown proposals."""
from alembic import op
from sqlalchemy import text

revision='0105'
down_revision='0104'
branch_labels=None
depends_on=None
BEFORE="UNION ALL SELECT 'context_decision',id,to_jsonb(r) FROM public.context_decision r WHERE organization_id=org) evidence"
AFTER=BEFORE.replace(') evidence'," UNION ALL SELECT 'allocation_proposal',id,to_jsonb(r) FROM public.allocation_proposal r WHERE organization_id=org UNION ALL SELECT 'allocation_proposal_line',id,to_jsonb(r) FROM public.allocation_proposal_line r WHERE organization_id=org) evidence")


def upgrade():
    op.execute('''
    ALTER TABLE context_decision ADD CONSTRAINT context_allocation_basis UNIQUE(organization_id,document_ref_id,logical_document_id,scope_id,property_id,assignment_proposal_id,invoice_review_id,id);
    ALTER TABLE cost_category ADD CONSTRAINT allocation_category_version UNIQUE(id,plan_version);
    CREATE TABLE allocation_proposal(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,
      document_ref_id uuid NOT NULL,logical_document_id uuid NOT NULL,scope_id uuid NOT NULL,property_id uuid NOT NULL,
      assignment_proposal_id uuid NOT NULL,invoice_review_id uuid NOT NULL,context_decision_id uuid NOT NULL,
      previous_revision_id uuid,revision_number integer NOT NULL CHECK(revision_number>0),line_count integer NOT NULL CHECK(line_count BETWEEN 1 AND 100),
      allocation_kind text NOT NULL DEFAULT 'single_scope_breakdown' CHECK(allocation_kind='single_scope_breakdown'),
      financial_state text NOT NULL DEFAULT 'proposal_only' CHECK(financial_state='proposal_only'),
      reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 2000),allocation_reviewed boolean NOT NULL CHECK(allocation_reviewed),
      actor_id uuid NOT NULL,request_key uuid NOT NULL,request_digest text NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(organization_id,id),UNIQUE(organization_id,logical_document_id,id),UNIQUE(organization_id,logical_document_id,revision_number),
      UNIQUE NULLS NOT DISTINCT(organization_id,logical_document_id,previous_revision_id),UNIQUE(organization_id,actor_id,request_key),
      FOREIGN KEY(organization_id,document_ref_id,logical_document_id,scope_id,property_id,assignment_proposal_id,invoice_review_id,context_decision_id)
        REFERENCES context_decision(organization_id,document_ref_id,logical_document_id,scope_id,property_id,assignment_proposal_id,invoice_review_id,id),
      FOREIGN KEY(organization_id,logical_document_id,previous_revision_id) REFERENCES allocation_proposal(organization_id,logical_document_id,id),
      FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
    CREATE TABLE allocation_proposal_line(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,allocation_proposal_id uuid NOT NULL,
      position integer NOT NULL CHECK(position BETWEEN 1 AND 100),description text NOT NULL CHECK(length(btrim(description)) BETWEEN 1 AND 1000),
      cost_category_id uuid NOT NULL,cost_category_plan_version smallint NOT NULL,category_code text NOT NULL,category_label text NOT NULL,
      gross_amount numeric NOT NULL CHECK(gross_amount::text NOT IN ('NaN','Infinity','-Infinity') AND gross_amount<>0 AND abs(gross_amount)<1000000000000 AND gross_amount=round(gross_amount,2)),
      vat_amount numeric CHECK(vat_amount::text NOT IN ('NaN','Infinity','-Infinity') AND abs(vat_amount)<=abs(gross_amount) AND vat_amount=round(vat_amount,2) AND (vat_amount=0 OR sign(vat_amount)=sign(gross_amount))),
      UNIQUE(organization_id,allocation_proposal_id,position),
      FOREIGN KEY(organization_id,allocation_proposal_id) REFERENCES allocation_proposal(organization_id,id),
      FOREIGN KEY(cost_category_id,cost_category_plan_version) REFERENCES cost_category(id,plan_version));
    CREATE FUNCTION allocation_proposal_access(org uuid,target uuid) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT EXISTS(SELECT 1 FROM public.allocation_proposal a WHERE a.organization_id=org AND a.id=target
        AND public.context_comparison_access(org,a.document_ref_id,a.scope_id,a.property_id));
    $$;
    REVOKE ALL ON FUNCTION allocation_proposal_access(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION allocation_proposal_access(uuid,uuid) TO app_rw;

    CREATE FUNCTION validate_allocation_balance() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE a public.allocation_proposal; r public.invoice_review_revision; total numeric; vat numeric; n integer; known integer; low integer; high integer; target uuid;
    BEGIN
      IF TG_TABLE_NAME='allocation_proposal' THEN target:=NEW.id; ELSE target:=NEW.allocation_proposal_id; END IF;
      SELECT * INTO STRICT a FROM public.allocation_proposal WHERE organization_id=NEW.organization_id AND id=target;
      SELECT * INTO STRICT r FROM public.invoice_review_revision WHERE organization_id=a.organization_id AND id=a.invoice_review_id;
      SELECT count(*),count(vat_amount),sum(gross_amount),sum(vat_amount),min(position),max(position) INTO n,known,total,vat,low,high
        FROM public.allocation_proposal_line WHERE organization_id=a.organization_id AND allocation_proposal_id=a.id;
      IF n<>a.line_count OR low<>1 OR high<>n OR total IS DISTINCT FROM r.gross_amount
        OR (r.vat_amount IS NULL AND known<>0) OR (r.vat_amount IS NOT NULL AND (known<>n OR vat IS DISTINCT FROM r.vat_amount))
        OR EXISTS(SELECT 1 FROM public.allocation_proposal_line WHERE organization_id=a.organization_id AND allocation_proposal_id=a.id AND sign(gross_amount)<>sign(r.gross_amount)) THEN
        RAISE EXCEPTION 'Allocation lines must completely conserve reviewed gross and VAT cents' USING ERRCODE='23514'; END IF;
      RETURN NULL;
    END $$;
    REVOKE ALL ON FUNCTION validate_allocation_balance() FROM PUBLIC,app_rw,app_worker,app_review;
    ''')
    for table in ('allocation_proposal','allocation_proposal_line'):
        target='id' if table=='allocation_proposal' else 'allocation_proposal_id'
        op.execute(f'''ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review; GRANT SELECT ON {table} TO app_rw;
          CREATE POLICY allocation_read ON {table} FOR SELECT USING(allocation_proposal_access(organization_id,{target}));
          CREATE POLICY allocation_no_insert ON {table} FOR INSERT WITH CHECK(false);
          CREATE TRIGGER protect_allocation BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();
          CREATE CONSTRAINT TRIGGER allocation_balance AFTER INSERT ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_allocation_balance();''')
    op.execute('''
    CREATE FUNCTION require_current_context_decision(org uuid,target uuid) RETURNS public.context_decision
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE d public.context_decision; latest public.context_decision; p public.assignment_proposal; r public.invoice_review_revision;
    BEGIN
      PERFORM public.lock_intake(org);
      SELECT * INTO d FROM public.context_decision WHERE organization_id=org AND id=target;
      IF d.id IS NULL OR NOT public.context_comparison_access(org,d.document_ref_id,d.scope_id,d.property_id)
        OR NOT public.source_partition_access(org,d.document_ref_id,true) OR NOT public.has_scope_capability(org,d.scope_id,'prepare') THEN
        RAISE EXCEPTION 'Allocation context inaccessible' USING ERRCODE='42501'; END IF;
      SELECT * INTO latest FROM public.context_decision WHERE organization_id=org AND logical_document_id=d.logical_document_id ORDER BY revision_number DESC LIMIT 1;
      SELECT * INTO p FROM public.assignment_proposal WHERE organization_id=org AND logical_document_id=d.logical_document_id ORDER BY revision_number DESC LIMIT 1;
      SELECT * INTO r FROM public.invoice_review_revision WHERE organization_id=org AND logical_document_id=d.logical_document_id ORDER BY revision_number DESC LIMIT 1;
      IF NOT public.context_comparison_access(org,latest.document_ref_id,latest.scope_id,latest.property_id)
        OR NOT public.assignment_proposal_access(org,p.document_ref_id,p.scope_id) OR NOT public.assignment_proposal_access(org,r.document_ref_id,r.scope_id) THEN
        RAISE EXCEPTION 'Current allocation context inaccessible' USING ERRCODE='42501'; END IF;
      IF d.id IS DISTINCT FROM latest.id OR d.assignment_proposal_id IS DISTINCT FROM p.id OR d.invoice_review_id IS DISTINCT FROM r.id
        OR p.partition_revision_id IS DISTINCT FROM (SELECT id FROM public.source_partition_revision WHERE organization_id=org AND document_ref_id=d.document_ref_id ORDER BY revision_number DESC LIMIT 1)
        OR p.extraction_revision_id IS DISTINCT FROM (SELECT id FROM public.extraction_revision WHERE organization_id=org AND logical_document_id=d.logical_document_id ORDER BY created_at DESC,id DESC LIMIT 1) THEN
        RAISE EXCEPTION 'Allocation context basis changed' USING ERRCODE='23505'; END IF;
      BEGIN
        PERFORM 1 FROM public.property WHERE organization_id=org AND id=d.property_id FOR SHARE NOWAIT;
        PERFORM 1 FROM public.accounting_scope WHERE organization_id=org AND id=d.scope_id FOR SHARE NOWAIT;
      EXCEPTION WHEN lock_not_available THEN RAISE EXCEPTION 'Allocation metadata busy' USING ERRCODE='23505'; END;
      IF d.snapshot_digest IS DISTINCT FROM encode(sha256(convert_to(public.context_comparison_snapshot(org,d.assignment_proposal_id)::text,'UTF8')),'hex') THEN
        RAISE EXCEPTION 'Allocation context snapshot changed' USING ERRCODE='23505'; END IF;
      RETURN d;
    END $$;
    REVOKE ALL ON FUNCTION require_current_context_decision(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION create_allocation_proposal(org uuid,logical_id uuid,decision_id uuid,expected uuid,request uuid,lines jsonb,explanation text,ack boolean) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; d public.context_decision;
      r public.invoice_review_revision; prior public.allocation_proposal; latest public.allocation_proposal; category public.cost_category;
      item jsonb; canonical jsonb:='[]'::jsonb; gross numeric; vat numeric; total numeric:=0; total_vat numeric:=0; known integer:=0;
      category_id uuid; version integer; digest text; created uuid; position integer:=0;
      names text[]:=ARRAY['cost_category_id','cost_category_plan_version','description','gross_amount','vat_amount'];
    BEGIN
      IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501'; END IF;
      PERFORM public.lock_intake(org);
      SELECT * INTO d FROM public.context_decision WHERE organization_id=org AND id=decision_id AND logical_document_id=logical_id;
      IF d.id IS NULL OR NOT public.context_comparison_access(org,d.document_ref_id,d.scope_id,d.property_id)
        OR NOT public.source_partition_access(org,d.document_ref_id,true) OR NOT public.has_scope_capability(org,d.scope_id,'prepare') THEN
        RAISE EXCEPTION 'Allocation context inaccessible' USING ERRCODE='42501'; END IF;
      IF request IS NULL OR ack IS DISTINCT FROM true OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 2000
        OR lines IS NULL OR jsonb_typeof(lines)<>'array' THEN RAISE EXCEPTION 'Explicit allocation lines required' USING ERRCODE='22023'; END IF;
      IF jsonb_array_length(lines) NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'One to one hundred allocation lines required' USING ERRCODE='22023'; END IF;
      SELECT * INTO STRICT r FROM public.invoice_review_revision WHERE organization_id=org AND id=d.invoice_review_id;
      FOR item IN SELECT value FROM jsonb_array_elements(lines) LOOP
        IF jsonb_typeof(item)<>'object' OR NOT item ?& names OR item-names<>'{}'::jsonb THEN RAISE EXCEPTION 'Exact allocation line shape required' USING ERRCODE='22023'; END IF;
        IF jsonb_typeof(item->'cost_category_id')<>'string' OR jsonb_typeof(item->'cost_category_plan_version')<>'number'
          OR jsonb_typeof(item->'description')<>'string' OR length(btrim(item->>'description')) NOT BETWEEN 1 AND 1000
          OR jsonb_typeof(item->'gross_amount')<>'string' OR jsonb_typeof(item->'vat_amount') NOT IN ('string','null') THEN
          RAISE EXCEPTION 'Typed allocation fields required' USING ERRCODE='22023'; END IF;
        gross:=(item->>'gross_amount')::numeric;vat:=(item->>'vat_amount')::numeric;
        category_id:=(item->>'cost_category_id')::uuid;version:=(item->>'cost_category_plan_version')::integer;
        IF gross::text IN ('NaN','Infinity','-Infinity') OR gross<>round(gross,2) OR gross=0 OR abs(gross)>=1000000000000 OR sign(gross)<>sign(r.gross_amount)
          OR (vat IS NOT NULL AND (vat::text IN ('NaN','Infinity','-Infinity') OR vat<>round(vat,2) OR abs(vat)>abs(gross) OR (vat<>0 AND sign(vat)<>sign(gross)))) THEN
          RAISE EXCEPTION 'Finite signed exact allocation cents required' USING ERRCODE='22023'; END IF;
        total:=total+gross;
        IF vat IS NOT NULL THEN total_vat:=total_vat+vat;known:=known+1; END IF;
        canonical:=canonical||jsonb_build_array(jsonb_build_object('cost_category_id',category_id,'cost_category_plan_version',version,
          'description',btrim(item->>'description'),'gross_amount',round(gross,2)::text,'vat_amount',round(vat,2)::text));
      END LOOP;
      IF total IS DISTINCT FROM r.gross_amount OR (r.vat_amount IS NULL AND known<>0)
        OR (r.vat_amount IS NOT NULL AND (known<>jsonb_array_length(lines) OR total_vat IS DISTINCT FROM r.vat_amount)) THEN
        RAISE EXCEPTION 'Allocation must conserve reviewed gross and VAT' USING ERRCODE='23514'; END IF;
      digest:=encode(sha256(convert_to(jsonb_build_array(logical_id,decision_id,expected,canonical,explanation,ack)::text,'UTF8')),'hex');
      SELECT * INTO prior FROM public.allocation_proposal WHERE organization_id=org AND actor_id=actor AND request_key=request;
      IF FOUND THEN
        IF prior.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Allocation request changed' USING ERRCODE='23505'; END IF;
        RETURN prior.id;
      END IF;
      SELECT * INTO latest FROM public.allocation_proposal WHERE organization_id=org AND logical_document_id=logical_id ORDER BY revision_number DESC LIMIT 1;
      IF latest.id IS NOT NULL AND NOT public.allocation_proposal_access(org,latest.id) THEN RAISE EXCEPTION 'Previous allocation inaccessible' USING ERRCODE='42501'; END IF;
      IF latest.id IS DISTINCT FROM expected THEN RAISE EXCEPTION 'Allocation proposal changed' USING ERRCODE='23505'; END IF;
      d:=public.require_current_context_decision(org,decision_id);
      INSERT INTO public.allocation_proposal(organization_id,document_ref_id,logical_document_id,scope_id,property_id,assignment_proposal_id,invoice_review_id,context_decision_id,
        previous_revision_id,revision_number,line_count,reason,allocation_reviewed,actor_id,request_key,request_digest)
        VALUES(org,d.document_ref_id,logical_id,d.scope_id,d.property_id,d.assignment_proposal_id,d.invoice_review_id,d.id,expected,COALESCE(latest.revision_number,0)+1,jsonb_array_length(canonical),explanation,true,actor,request,digest)
        RETURNING id INTO created;
      FOR item IN SELECT value FROM jsonb_array_elements(canonical) LOOP
        BEGIN
          SELECT * INTO category FROM public.cost_category WHERE id=(item->>'cost_category_id')::uuid AND plan_version=(item->>'cost_category_plan_version')::integer AND is_active FOR SHARE NOWAIT;
        EXCEPTION WHEN lock_not_available THEN RAISE EXCEPTION 'Allocation category busy' USING ERRCODE='23505'; END;
        IF category.id IS NULL THEN RAISE EXCEPTION 'Active category and exact version required' USING ERRCODE='23514'; END IF;
        position:=position+1;
        INSERT INTO public.allocation_proposal_line(organization_id,allocation_proposal_id,position,description,cost_category_id,cost_category_plan_version,category_code,category_label,gross_amount,vat_amount)
          VALUES(org,created,position,item->>'description',category.id,category.plan_version,category.code,category.label,(item->>'gross_amount')::numeric,(item->>'vat_amount')::numeric);
      END LOOP;
      RETURN created;
    END $$;
    REVOKE ALL ON FUNCTION create_allocation_proposal(uuid,uuid,uuid,uuid,uuid,jsonb,text,boolean) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION create_allocation_proposal(uuid,uuid,uuid,uuid,uuid,jsonb,text,boolean) TO app_rw;
    ''')
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(BEFORE)==1
    op.execute(definition.replace(BEFORE,AFTER))


def downgrade():
    op.execute('LOCK TABLE allocation_proposal,allocation_proposal_line IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM allocation_proposal) THEN RAISE EXCEPTION 'Retain allocation proposal evidence; repair forward'; END IF; END $$")
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(AFTER)==1
    op.execute(definition.replace(AFTER,BEFORE))
    op.execute('''DROP FUNCTION create_allocation_proposal(uuid,uuid,uuid,uuid,uuid,jsonb,text,boolean),require_current_context_decision(uuid,uuid);
      DROP TABLE allocation_proposal_line,allocation_proposal;
      DROP FUNCTION validate_allocation_balance(),allocation_proposal_access(uuid,uuid);
      ALTER TABLE cost_category DROP CONSTRAINT allocation_category_version;
      ALTER TABLE context_decision DROP CONSTRAINT context_allocation_basis;''')
