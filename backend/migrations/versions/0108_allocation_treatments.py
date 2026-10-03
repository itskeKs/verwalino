"""Retain explicit treatment assertions for every exact allocation line."""
from alembic import op
from sqlalchemy import text

revision='0108'
down_revision='0107'
branch_labels=None
depends_on=None
BEFORE="UNION ALL SELECT 'duplicate_exception_dependency',id,to_jsonb(r) FROM public.duplicate_exception_dependency r WHERE organization_id=org) evidence"
AFTER=BEFORE.replace(') evidence'," UNION ALL SELECT 'allocation_treatment_review',id,to_jsonb(r) FROM public.allocation_treatment_review r WHERE organization_id=org UNION ALL SELECT 'allocation_treatment_line',id,to_jsonb(r) FROM public.allocation_treatment_line r WHERE organization_id=org) evidence")


def upgrade():
    op.execute('''
    ALTER TABLE allocation_proposal_line ADD CONSTRAINT treatment_line_basis UNIQUE(organization_id,allocation_proposal_id,id);
    CREATE TABLE allocation_treatment_review(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,logical_document_id uuid NOT NULL,allocation_proposal_id uuid NOT NULL,
      previous_revision_id uuid,revision_number integer NOT NULL CHECK(revision_number>0),
      financial_state text NOT NULL DEFAULT 'review_only' CHECK(financial_state='review_only'),
      marker_policy text NOT NULL DEFAULT 'allocation-treatment-v1' CHECK(marker_policy='allocation-treatment-v1'),
      snapshot jsonb NOT NULL,snapshot_digest text NOT NULL,
      reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 2000),treatment_reviewed boolean NOT NULL CHECK(treatment_reviewed),
      actor_id uuid NOT NULL,request_key uuid NOT NULL,request_digest text NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(organization_id,id),UNIQUE(organization_id,logical_document_id,id),UNIQUE(organization_id,allocation_proposal_id,id),
      UNIQUE(organization_id,logical_document_id,revision_number),UNIQUE NULLS NOT DISTINCT(organization_id,logical_document_id,previous_revision_id),
      UNIQUE(organization_id,actor_id,request_key),
      FOREIGN KEY(organization_id,logical_document_id,allocation_proposal_id) REFERENCES allocation_proposal(organization_id,logical_document_id,id),
      FOREIGN KEY(organization_id,logical_document_id,previous_revision_id) REFERENCES allocation_treatment_review(organization_id,logical_document_id,id),
      FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
    CREATE TABLE allocation_treatment_line(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,allocation_proposal_id uuid NOT NULL,treatment_review_id uuid NOT NULL,allocation_line_id uuid NOT NULL,
      expense_intent text NOT NULL CHECK(expense_intent IN ('operating_expense','reserve_related','capitalization_related','unresolved')),
      tax_intent text NOT NULL CHECK(tax_intent IN ('gross_without_input_vat','input_vat_requested','unresolved')),
      tenant_treatment text NOT NULL CHECK(tenant_treatment IN ('full','partial','none','unresolved')),
      tenant_amount numeric CHECK(tenant_amount::text NOT IN ('NaN','Infinity','-Infinity') AND tenant_amount=round(tenant_amount,2) AND abs(tenant_amount)<1000000000000),
      tenant_distribution_key text,owner_distribution_key text NOT NULL,
      documentary_basis text NOT NULL CHECK(length(btrim(documentary_basis)) BETWEEN 1 AND 2000),markers jsonb NOT NULL CHECK(jsonb_typeof(markers)='array'),
      UNIQUE(organization_id,treatment_review_id,allocation_line_id),
      FOREIGN KEY(organization_id,allocation_proposal_id,treatment_review_id) REFERENCES allocation_treatment_review(organization_id,allocation_proposal_id,id),
      FOREIGN KEY(organization_id,allocation_proposal_id,allocation_line_id) REFERENCES allocation_proposal_line(organization_id,allocation_proposal_id,id));
    CREATE FUNCTION validate_treatment_membership() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE target uuid;r public.allocation_treatment_review;n integer;
    BEGIN
      IF TG_TABLE_NAME='allocation_treatment_review' THEN target:=NEW.id;ELSE target:=NEW.treatment_review_id;END IF;
      SELECT * INTO STRICT r FROM public.allocation_treatment_review WHERE organization_id=NEW.organization_id AND id=target;
      SELECT count(*) INTO n FROM public.allocation_treatment_line WHERE organization_id=r.organization_id AND treatment_review_id=r.id;
      IF n<>(SELECT line_count FROM public.allocation_proposal WHERE organization_id=r.organization_id AND id=r.allocation_proposal_id) THEN
        RAISE EXCEPTION 'Every exact allocation line requires treatment' USING ERRCODE='23514';END IF;
      RETURN NULL;
    END $$;
    REVOKE ALL ON FUNCTION validate_treatment_membership() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE FUNCTION allocation_treatment_snapshot(org uuid,target uuid) RETURNS jsonb
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT jsonb_build_object('allocation_proposal_id',a.id,'scope_type',d.snapshot->'scope'->>'scope_type','lines',
        (SELECT jsonb_agg(jsonb_build_object('allocation_line_id',l.id,'position',l.position,'gross_amount',round(l.gross_amount,2)::text,
          'vat_amount',round(l.vat_amount,2)::text,'category',jsonb_build_object('id',c.id,'plan_version',c.plan_version,'code',c.code,'label',c.label,
          'is_active',c.is_active,'cost_group',c.cost_group,'reserve_relevant',c.reserve_relevant,
          'tenant_apportionability_default',c.tenant_apportionability_default,'owner_billing_relevant_default',c.owner_billing_relevant_default,
          'owner_distribution_key_default',c.owner_distribution_key_default,'tenant_distribution_key_default',c.tenant_distribution_key_default)) ORDER BY l.position)
         FROM public.allocation_proposal_line l JOIN public.cost_category c ON c.id=l.cost_category_id AND c.plan_version=l.cost_category_plan_version
         WHERE l.organization_id=org AND l.allocation_proposal_id=a.id))
      FROM public.allocation_proposal a JOIN public.context_decision d ON d.organization_id=org AND d.id=a.context_decision_id
      WHERE a.organization_id=org AND a.id=target AND public.allocation_proposal_access(org,a.id);
    $$;
    REVOKE ALL ON FUNCTION allocation_treatment_snapshot(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION allocation_treatment_snapshot(uuid,uuid) TO app_rw;
    ''')
    for table in ('allocation_treatment_review','allocation_treatment_line'):
        op.execute(f'''ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review;GRANT SELECT ON {table} TO app_rw;
          CREATE POLICY treatment_read ON {table} FOR SELECT USING(allocation_proposal_access(organization_id,allocation_proposal_id));
          CREATE POLICY treatment_no_insert ON {table} FOR INSERT WITH CHECK(false);
          CREATE TRIGGER protect_treatment BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();
          CREATE CONSTRAINT TRIGGER treatment_membership AFTER INSERT ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_treatment_membership();''')
    op.execute('''
    CREATE FUNCTION create_allocation_treatment(org uuid,logical_id uuid,allocation_id uuid,expected uuid,request uuid,preview_digest text,lines jsonb,explanation text,ack boolean) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      a public.allocation_proposal;latest_a public.allocation_proposal;prior public.allocation_treatment_review;latest public.allocation_treatment_review;
      basis public.allocation_proposal_line;c public.cost_category;snapshot jsonb;item jsonb;canonical jsonb:='[]'::jsonb;markers jsonb;
      digest text;created uuid;amount numeric;line_id uuid;seen uuid[]:='{}';scope_type text;
      names text[]:=ARRAY['allocation_line_id','expense_intent','tax_intent','tenant_treatment','tenant_amount','tenant_distribution_key','owner_distribution_key','documentary_basis'];
      keys text[]:=ARRAY['MEA','WOHNFLAECHE','BEHEIZTE_FLAECHE','EINHEIT','PERSONENZAHL','VERBRAUCH','EXTERNE_ABRECHNUNG','MANUELLER_BETRAG'];
      evidence_keys text[]:=ARRAY['VERBRAUCH','EXTERNE_ABRECHNUNG','MANUELLER_BETRAG'];
    BEGIN
      IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501';END IF;
      PERFORM public.lock_intake(org);
      SELECT * INTO a FROM public.allocation_proposal WHERE organization_id=org AND id=allocation_id AND logical_document_id=logical_id;
      IF a.id IS NULL OR NOT public.allocation_proposal_access(org,a.id) OR NOT public.source_partition_access(org,a.document_ref_id,true)
        OR NOT public.has_scope_capability(org,a.scope_id,'prepare') THEN RAISE EXCEPTION 'Treatment basis inaccessible' USING ERRCODE='42501';END IF;
      IF request IS NULL OR ack IS DISTINCT FROM true OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 2000
        OR preview_digest IS NULL OR length(preview_digest)<>64 OR lines IS NULL OR jsonb_typeof(lines)<>'array' THEN
        RAISE EXCEPTION 'Explicit treatment review required' USING ERRCODE='22023';END IF;
      IF jsonb_array_length(lines)<>a.line_count THEN RAISE EXCEPTION 'Complete treatment required' USING ERRCODE='22023';END IF;
      digest:=encode(sha256(convert_to(jsonb_build_array(logical_id,allocation_id,expected,preview_digest,lines,explanation,ack)::text,'UTF8')),'hex');
      SELECT * INTO prior FROM public.allocation_treatment_review WHERE organization_id=org AND actor_id=actor AND request_key=request;
      IF FOUND THEN
        IF prior.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Treatment request changed' USING ERRCODE='23505';END IF;
        RETURN prior.id;
      END IF;
      SELECT * INTO latest FROM public.allocation_treatment_review WHERE organization_id=org AND logical_document_id=logical_id ORDER BY revision_number DESC LIMIT 1;
      IF latest.id IS NOT NULL AND NOT public.allocation_proposal_access(org,latest.allocation_proposal_id) THEN RAISE EXCEPTION 'Previous treatment inaccessible' USING ERRCODE='42501';END IF;
      IF latest.id IS DISTINCT FROM expected THEN RAISE EXCEPTION 'Treatment review changed' USING ERRCODE='23505';END IF;
      SELECT * INTO latest_a FROM public.allocation_proposal WHERE organization_id=org AND logical_document_id=logical_id ORDER BY revision_number DESC LIMIT 1;
      IF NOT public.allocation_proposal_access(org,latest_a.id) THEN RAISE EXCEPTION 'Current allocation inaccessible' USING ERRCODE='42501';END IF;
      IF latest_a.id IS DISTINCT FROM a.id THEN RAISE EXCEPTION 'Allocation basis changed' USING ERRCODE='23505';END IF;
      PERFORM public.require_current_context_decision(org,a.context_decision_id);
      BEGIN
        PERFORM 1 FROM public.cost_category c2 WHERE c2.id IN(SELECT cost_category_id FROM public.allocation_proposal_line WHERE organization_id=org AND allocation_proposal_id=a.id) ORDER BY c2.id FOR SHARE NOWAIT;
      EXCEPTION WHEN lock_not_available THEN RAISE EXCEPTION 'Treatment categories busy' USING ERRCODE='23505';END;
      IF EXISTS(SELECT 1 FROM public.allocation_proposal_line l JOIN public.cost_category c2 ON c2.id=l.cost_category_id WHERE l.organization_id=org AND l.allocation_proposal_id=a.id AND NOT c2.is_active) THEN
        RAISE EXCEPTION 'Active treatment categories required' USING ERRCODE='23514';END IF;
      snapshot:=public.allocation_treatment_snapshot(org,a.id);scope_type:=snapshot->>'scope_type';
      IF preview_digest IS DISTINCT FROM encode(sha256(convert_to(snapshot::text,'UTF8')),'hex') THEN RAISE EXCEPTION 'Treatment category snapshot changed' USING ERRCODE='23505';END IF;
      FOR item IN SELECT value FROM jsonb_array_elements(lines) LOOP
        IF jsonb_typeof(item)<>'object' OR NOT item ?& names OR item-names<>'{}'::jsonb THEN RAISE EXCEPTION 'Exact treatment line shape required' USING ERRCODE='22023';END IF;
        IF EXISTS(SELECT 1 FROM unnest(ARRAY['allocation_line_id','expense_intent','tax_intent','tenant_treatment','owner_distribution_key','documentary_basis']) k WHERE jsonb_typeof(item->k)<>'string')
          OR jsonb_typeof(item->'tenant_amount') NOT IN ('null','string') OR jsonb_typeof(item->'tenant_distribution_key') NOT IN ('null','string')
          OR length(btrim(item->>'documentary_basis')) NOT BETWEEN 1 AND 2000 THEN RAISE EXCEPTION 'Typed treatment fields required' USING ERRCODE='22023';END IF;
        line_id:=(item->>'allocation_line_id')::uuid;
        SELECT * INTO basis FROM public.allocation_proposal_line WHERE organization_id=org AND allocation_proposal_id=a.id AND id=line_id;
        IF basis.id IS NULL OR line_id=ANY(seen) THEN RAISE EXCEPTION 'Unique exact allocation lines required' USING ERRCODE='22023';END IF;
        seen:=array_append(seen,line_id);amount:=(item->>'tenant_amount')::numeric;
        IF (amount IS NOT NULL AND (amount::text IN ('NaN','Infinity','-Infinity') OR amount<>round(amount,2) OR abs(amount)>=1000000000000))
          OR item->>'expense_intent' NOT IN ('operating_expense','reserve_related','capitalization_related','unresolved')
          OR item->>'tax_intent' NOT IN ('gross_without_input_vat','input_vat_requested','unresolved')
          OR item->>'tenant_treatment' NOT IN ('full','partial','none','unresolved') THEN RAISE EXCEPTION 'Valid exact treatment assertions required' USING ERRCODE='22023';END IF;
        IF (item->>'tenant_treatment'='unresolved' AND (amount IS NOT NULL OR item->>'tenant_distribution_key' IS NOT NULL))
          OR (item->>'tenant_treatment'='none' AND (amount IS DISTINCT FROM 0 OR item->>'tenant_distribution_key' IS NOT NULL))
          OR (item->>'tenant_treatment' IN ('full','partial') AND (amount IS NULL OR item->>'tenant_distribution_key' IS NULL OR NOT (item->>'tenant_distribution_key'=ANY(keys))))
          OR (item->>'tenant_treatment'='full' AND amount IS DISTINCT FROM basis.gross_amount)
          OR (item->>'tenant_treatment'='partial' AND (amount=0 OR sign(amount)<>sign(basis.gross_amount) OR abs(amount)>=abs(basis.gross_amount)))
          OR (scope_type='WEG' AND item->>'owner_distribution_key'<>'unresolved' AND NOT(item->>'owner_distribution_key'=ANY(keys)))
          OR (scope_type='MIET' AND item->>'owner_distribution_key'<>'not_applicable') THEN RAISE EXCEPTION 'Explicit scope-compatible signed treatment required' USING ERRCODE='22023';END IF;
        SELECT * INTO STRICT c FROM public.cost_category WHERE id=basis.cost_category_id;
        markers:='[]'::jsonb;
        IF item->>'expense_intent'='unresolved' THEN markers:=markers||'"expense_unresolved"'::jsonb;END IF;
        IF item->>'expense_intent'='reserve_related' THEN markers:=markers||'"reserve_treatment_unsupported"'::jsonb;END IF;
        IF item->>'expense_intent'='capitalization_related' THEN markers:=markers||'"capitalization_treatment_unsupported"'::jsonb;END IF;
        IF item->>'tax_intent'='unresolved' THEN markers:=markers||'"tax_unresolved"'::jsonb;END IF;
        IF item->>'tax_intent'='input_vat_requested' THEN markers:=markers||'"input_vat_treatment_unsupported"'::jsonb;END IF;
        IF item->>'tenant_treatment'='unresolved' THEN markers:=markers||'"tenant_treatment_unresolved"'::jsonb;END IF;
        IF item->>'owner_distribution_key'='unresolved' THEN markers:=markers||'"owner_distribution_unresolved"'::jsonb;END IF;
        IF item->>'tenant_distribution_key'=ANY(evidence_keys) THEN markers:=markers||'"tenant_distribution_evidence_required"'::jsonb;END IF;
        IF item->>'owner_distribution_key'=ANY(evidence_keys) THEN markers:=markers||'"owner_distribution_evidence_required"'::jsonb;END IF;
        IF item->>'expense_intent'<>'unresolved' AND (item->>'expense_intent'='reserve_related') IS DISTINCT FROM c.reserve_relevant THEN markers:=markers||'"category_expense_conflict"'::jsonb;END IF;
        IF item->>'tenant_treatment' IN ('full','partial') AND c.cost_group='NICHT_UMLAGEFAEHIG' THEN markers:=markers||'"category_tenant_conflict"'::jsonb;END IF;
        canonical:=canonical||jsonb_build_array(item||jsonb_build_object('tenant_amount',round(amount,2)::text,'markers',markers));
      END LOOP;
      INSERT INTO public.allocation_treatment_review(organization_id,logical_document_id,allocation_proposal_id,previous_revision_id,revision_number,snapshot,snapshot_digest,reason,treatment_reviewed,actor_id,request_key,request_digest)
        VALUES(org,logical_id,a.id,expected,COALESCE(latest.revision_number,0)+1,snapshot,preview_digest,explanation,true,actor,request,digest) RETURNING id INTO created;
      FOR item IN SELECT value FROM jsonb_array_elements(canonical) LOOP
        INSERT INTO public.allocation_treatment_line(organization_id,allocation_proposal_id,treatment_review_id,allocation_line_id,expense_intent,tax_intent,tenant_treatment,tenant_amount,tenant_distribution_key,owner_distribution_key,documentary_basis,markers)
          VALUES(org,a.id,created,(item->>'allocation_line_id')::uuid,item->>'expense_intent',item->>'tax_intent',item->>'tenant_treatment',(item->>'tenant_amount')::numeric,item->>'tenant_distribution_key',item->>'owner_distribution_key',item->>'documentary_basis',item->'markers');
      END LOOP;
      RETURN created;
    END $$;
    REVOKE ALL ON FUNCTION create_allocation_treatment(uuid,uuid,uuid,uuid,uuid,text,jsonb,text,boolean) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION create_allocation_treatment(uuid,uuid,uuid,uuid,uuid,text,jsonb,text,boolean) TO app_rw;
    ''')
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(BEFORE)==1
    op.execute(definition.replace(BEFORE,AFTER))


def downgrade():
    op.execute('LOCK TABLE allocation_treatment_review,allocation_treatment_line IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM allocation_treatment_review) THEN RAISE EXCEPTION 'Retain treatment evidence; repair forward';END IF;END $$")
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(AFTER)==1
    op.execute(definition.replace(AFTER,BEFORE))
    op.execute('''DROP FUNCTION create_allocation_treatment(uuid,uuid,uuid,uuid,uuid,text,jsonb,text,boolean),allocation_treatment_snapshot(uuid,uuid);
      DROP TABLE allocation_treatment_line,allocation_treatment_review;DROP FUNCTION validate_treatment_membership();
      ALTER TABLE allocation_proposal_line DROP CONSTRAINT treatment_line_basis;''')
