"""Read-only, complete correction readiness and downstream impact preview."""
from alembic import op
from sqlalchemy import text

revision='0113'
down_revision='0112'
branch_labels=None
depends_on=None


def upgrade():
    # Own-source exclusion applies only to this private correction evaluator.
    # Ordinary adoption retains its unconditional consumed-original guard.
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('adoption_duplicate_snapshot(uuid,uuid)'::regprocedure)"))
    before="AND content_sha256=subject.content_sha256) THEN blocked:=true;END IF;"
    assert definition.count(before)==1
    definition=definition.replace('FUNCTION public.adoption_duplicate_snapshot(', 'FUNCTION public.correction_duplicate_snapshot(')
    definition=definition.replace(before,"AND content_sha256=subject.content_sha256 AND document_ref_id<>subject.document_ref_id) THEN blocked:=true;END IF;")
    op.execute(definition)
    op.execute('REVOKE ALL ON FUNCTION correction_duplicate_snapshot(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review')
    op.execute(SNAPSHOT_SQL)
    op.execute(IMPACT_SQL)
    op.execute(PREVIEW_SQL)


def downgrade():
    op.execute('''DROP FUNCTION IF EXISTS allocation_correction_preview(uuid,uuid,uuid),allocation_correction_impacts(uuid,uuid,uuid),correction_duplicate_snapshot(uuid,uuid),correction_snapshot_cost_ids(jsonb)''')


SNAPSHOT_SQL='''
CREATE FUNCTION correction_snapshot_cost_ids(payload jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
DECLARE refs jsonb;container jsonb;member jsonb;reference jsonb;
BEGIN
  IF payload IS NULL THEN RETURN '[]'::jsonb;END IF;
  -- Enforce the bound before any recursive discovery, including other-property
  -- snapshots that may carry retained references to this source.
  IF octet_length(payload::text)>2097152 OR jsonb_typeof(payload)<>'object' THEN
    RAISE EXCEPTION 'Complete correction dependencies require further review' USING ERRCODE='23514';END IF;
  IF (payload ? 'inputs' AND jsonb_typeof(payload->'inputs')<>'object') OR (payload ? 'result' AND jsonb_typeof(payload->'result')<>'object') THEN
    RAISE EXCEPTION 'Malformed retained snapshot structure' USING ERRCODE='23514';END IF;
  FOREACH container IN ARRAY ARRAY[payload#>'{inputs,cost_positions}',payload->'computation',payload->'vacancy_attribution'] LOOP
    IF container IS NULL THEN CONTINUE;END IF;
    IF jsonb_typeof(container)<>'array' THEN
      RAISE EXCEPTION 'Malformed retained cost container' USING ERRCODE='23514';END IF;
    FOR member IN SELECT value FROM jsonb_array_elements(container) LOOP
      IF jsonb_typeof(member)<>'object' OR jsonb_typeof(member->'cost_entry_id') IS DISTINCT FROM 'string' THEN
        RAISE EXCEPTION 'Malformed retained cost position' USING ERRCODE='23514';END IF;
    END LOOP;
  END LOOP;
  FOREACH container IN ARRAY ARRAY[payload#>'{result,lines}',payload#>'{result,owners}'] LOOP
    IF container IS NULL THEN CONTINUE;END IF;
    IF jsonb_typeof(container)<>'array' THEN
      RAISE EXCEPTION 'Malformed retained result container' USING ERRCODE='23514';END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(container) e WHERE jsonb_typeof(e)<>'object') THEN
      RAISE EXCEPTION 'Malformed retained result lines' USING ERRCODE='23514';END IF;
  END LOOP;
  FOR container IN SELECT value->'cost_lines' FROM jsonb_array_elements(COALESCE(payload#>'{result,owners}','[]')) LOOP
    IF container IS NULL THEN CONTINUE;END IF;
    IF jsonb_typeof(container)<>'array' THEN
      RAISE EXCEPTION 'Malformed retained owner container' USING ERRCODE='23514';END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(container) e WHERE jsonb_typeof(e)<>'object') THEN
      RAISE EXCEPTION 'Malformed retained owner lines' USING ERRCODE='23514';END IF;
  END LOOP;
  refs:=jsonb_path_query_array(payload,'$.**.cost_entry_id');
  FOR reference IN SELECT value FROM jsonb_array_elements(refs) LOOP
    IF reference='null'::jsonb THEN CONTINUE;END IF; -- optional result-line link
    IF jsonb_typeof(reference)<>'string' OR (reference#>>'{}') !~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' THEN
      RAISE EXCEPTION 'Malformed retained cost reference' USING ERRCODE='23514';END IF;
  END LOOP;
  RETURN (SELECT COALESCE(jsonb_agg(DISTINCT lower(value#>>'{}')),'[]') FROM jsonb_array_elements(refs) WHERE value<>'null'::jsonb);
END $$;
REVOKE ALL ON FUNCTION correction_snapshot_cost_ids(jsonb) FROM PUBLIC,app_rw,app_worker,app_review;
'''


IMPACT_SQL='''
CREATE FUNCTION allocation_correction_impacts(org uuid,predecessor uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE old public.allocation_set;new_review public.invoice_review_revision;old_review public.invoice_review_revision;
  row record;declarations jsonb;cost_ids text[];source_id text;items jsonb:='[]';facts jsonb:='[]';
  consumed boolean;potential boolean;renewal boolean;source_review boolean;scope uuid;n integer:=0;flags jsonb;
BEGIN
  -- Private helper; its only caller has checked source and destination authority
  -- and holds the organization ACL/financial locks before discovering parents.
  SELECT * INTO STRICT old FROM public.allocation_set WHERE organization_id=org AND id=predecessor;
  SELECT * INTO STRICT old_review FROM public.invoice_review_revision WHERE organization_id=org AND id=old.invoice_review_id;
  SELECT r.* INTO STRICT new_review FROM public.allocation_treatment_review t
    JOIN public.allocation_proposal a ON a.organization_id=t.organization_id AND a.id=t.allocation_proposal_id
    JOIN public.invoice_review_revision r ON r.organization_id=a.organization_id AND r.id=a.invoice_review_id WHERE t.organization_id=org AND t.id=target;
  SELECT array_agg(cost_entry_id::text ORDER BY position) INTO cost_ids FROM public.document_cost_link WHERE organization_id=org AND allocation_set_id=old.id;
  source_id:=old.source_document_id::text;
  FOR row IN
    WITH parents AS (
      SELECT 'settlement_run'::text AS kind,r.id,r.property_id,r.run_type,r.period_start,r.period_end,r.status,r.snapshot_payload,
        r.superseded_by_id,to_jsonb(r) AS record FROM public.settlement_run r WHERE r.organization_id=org
      UNION ALL
      SELECT 'economic_plan',p.id,p.property_id,'WIRTSCHAFTSPLAN',p.period_start,p.period_end,p.status,p.snapshot_payload,
        p.superseded_by_id,to_jsonb(p) FROM public.economic_plan p WHERE p.organization_id=org
    ) SELECT p.* FROM parents p WHERE p.property_id=old.property_id
      OR public.correction_snapshot_cost_ids(p.snapshot_payload) ?| cost_ids
      OR EXISTS(SELECT 1 FROM public.coverage_declaration d WHERE d.organization_id=org
        AND COALESCE(d.settlement_run_id,d.economic_plan_id)=p.id AND d.source_document_ids ? source_id)
    ORDER BY p.kind,p.id
  LOOP
    scope:=public.financial_scope_id(org,row.kind,row.id);
    SELECT COALESCE(jsonb_agg(to_jsonb(d) ORDER BY d.revision,d.id),'[]') INTO declarations FROM (
      SELECT * FROM public.coverage_declaration d WHERE d.organization_id=org
        AND CASE row.kind WHEN 'settlement_run' THEN d.settlement_run_id=row.id ELSE d.economic_plan_id=row.id END
      ORDER BY d.revision DESC LIMIT 1001) d;
    consumed:=public.correction_snapshot_cost_ids(row.snapshot_payload) ?| cost_ids;
    source_review:=EXISTS(SELECT 1 FROM public.coverage_declaration d WHERE d.organization_id=org
      AND CASE row.kind WHEN 'settlement_run' THEN d.settlement_run_id=row.id ELSE d.economic_plan_id=row.id END
      AND d.source_document_ids ? source_id);
    potential:=row.run_type IN ('BK_MIETER','WEG_JAHRESABRECHNUNG') AND row.property_id=old.property_id
      AND (scope=old.scope_id OR scope IS NULL) AND
        ((row.period_start<=old_review.service_period_to AND row.period_end>=old_review.service_period_from)
          OR (row.period_start<=new_review.service_period_to AND row.period_end>=new_review.service_period_from));
    renewal:=row.status='draft' AND row.property_id=old.property_id AND (scope=old.scope_id OR scope IS NULL) AND jsonb_array_length(declarations)>0;
    IF NOT (consumed OR source_review OR COALESCE(potential,false) OR COALESCE(renewal,false)) THEN CONTINUE;END IF;
    n:=n+1;
    -- No parent identifiers or partial results escape any failure below.
    IF n>100 OR jsonb_array_length(declarations)>1000 OR scope IS DISTINCT FROM old.scope_id OR row.property_id<>old.property_id
      OR NOT public.financial_scope_access(org,row.kind,row.id,'read')
      OR ((row.snapshot_payload IS NOT NULL OR row.status<>'draft' OR jsonb_array_length(declarations)>0)
        AND NOT public.property_financial_disclosure_access(org,row.property_id,'read'))
      OR row.status NOT IN ('draft','finalized')
      OR (row.status='finalized' AND row.snapshot_payload IS NULL)
      OR (row.snapshot_payload IS NOT NULL AND (octet_length(row.snapshot_payload::text)>2097152
        OR jsonb_typeof(row.snapshot_payload)<>'object'
        OR (CASE row.kind WHEN 'economic_plan' THEN row.snapshot_payload->>'document_type' ELSE row.snapshot_payload->>'run_type' END) IS DISTINCT FROM row.run_type
        OR (row.run_type IN ('BK_MIETER','WEG_JAHRESABRECHNUNG')
          AND jsonb_typeof(row.snapshot_payload#>'{inputs,cost_positions}') IS DISTINCT FROM 'array'
          AND jsonb_typeof(row.snapshot_payload->'computation') IS DISTINCT FROM 'array')))
      OR (consumed AND row.run_type NOT IN ('BK_MIETER','WEG_JAHRESABRECHNUNG')) THEN
      RAISE EXCEPTION 'Complete correction dependencies require further review' USING ERRCODE='23514';END IF;
    flags:=jsonb_build_object('exact_cost_consumption',consumed,'potential_calculation_change',COALESCE(potential,false),
      'coverage_renewal',COALESCE(renewal,false),'source_evidence_review',source_review);
    items:=items||jsonb_build_array(jsonb_build_object('kind',row.kind,'id',row.id,'run_type',row.run_type,'status',row.status,
      'period_start',row.period_start,'period_end',row.period_end,'superseded_by_id',row.superseded_by_id,'effects',flags,
      'follow_up',CASE WHEN row.status='draft' THEN 'refresh_and_revalidate' ELSE 'review_finalized_successor' END));
    facts:=facts||jsonb_build_array(jsonb_build_object('kind',row.kind,'id',row.id,'scope',scope,'effects',flags,
      'parent_digest',encode(sha256(convert_to(row.record::text,'UTF8')),'hex'),
      'coverage_digest',encode(sha256(convert_to(declarations::text,'UTF8')),'hex')));
  END LOOP;
  RETURN jsonb_build_object('items',items,'basis',facts);
END $$;
REVOKE ALL ON FUNCTION allocation_correction_impacts(uuid,uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
'''


PREVIEW_SQL='''
CREATE FUNCTION allocation_correction_preview(org uuid,predecessor uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE old public.allocation_set;a public.allocation_proposal;r public.invoice_review_revision;prior public.invoice_review_revision;
  t public.allocation_treatment_review;impacts jsonb;duplicates jsonb;digest text;old_members jsonb;new_members jsonb;
BEGIN
  PERFORM public.lock_intake(org);
  SELECT * INTO old FROM public.allocation_set WHERE organization_id=org AND id=predecessor;
  a:=public.require_adoption_authority(org,target);
  IF old.id IS NULL OR old.kind<>'intake_reviewed' OR NOT public.allocation_set_access(org,old.id)
    OR NOT public.invoice_source_access(org,old.source_document_id,'prepare') OR NOT public.invoice_source_access(org,old.source_document_id,'export')
    OR NOT public.source_partition_access(org,old.document_ref_id,true)
    OR NOT public.intake_original_export_access(org,old.intake_original_id) THEN
    RAISE EXCEPTION 'Correction source inaccessible' USING ERRCODE='42501';END IF;
  BEGIN
    a:=public.require_current_adoption_basis(org,target);
    IF (a.document_ref_id,a.logical_document_id,a.property_id,a.scope_id) IS DISTINCT FROM (old.document_ref_id,old.logical_document_id,old.property_id,old.scope_id)
      OR a.id=old.allocation_proposal_id OR target=old.treatment_review_id
      OR EXISTS(SELECT 1 FROM public.document_cost_link l JOIN public.cost_entry c ON c.organization_id=l.organization_id AND c.id=l.cost_entry_id
        WHERE l.organization_id=org AND l.allocation_set_id=old.id AND c.superseded_by_id IS NOT NULL)
      OR NOT EXISTS(SELECT 1 FROM public.invoice_document WHERE organization_id=org AND id=old.source_document_id AND status='active') THEN
      RAISE EXCEPTION 'New current complete review of the same source and scope required' USING ERRCODE='23505';END IF;
    duplicates:=public.correction_duplicate_snapshot(org,a.invoice_review_id);
    IF (duplicates->>'blocked')::boolean THEN RAISE EXCEPTION 'Source conflict requires review' USING ERRCODE='23505';END IF;
    impacts:=public.allocation_correction_impacts(org,old.id,target);
  EXCEPTION WHEN insufficient_privilege OR unique_violation OR check_violation THEN
    RETURN jsonb_build_object('can_review',false,'preview_digest',NULL,'blocker','Complete current correction basis and accessible dependencies required','impacts','[]'::jsonb);
  END;
  SELECT * INTO STRICT r FROM public.invoice_review_revision WHERE organization_id=org AND id=a.invoice_review_id;
  SELECT * INTO STRICT prior FROM public.invoice_review_revision WHERE organization_id=org AND id=old.invoice_review_id;
  SELECT * INTO STRICT t FROM public.allocation_treatment_review WHERE organization_id=org AND id=target;
  SELECT jsonb_agg(jsonb_build_object('cost_entry_id',c.id,'position',l.position,'gross_amount',c.gross_amount::text,
    'vat_amount',c.vat_amount::text,'tenant_amount',c.apportionable_tenant_amount::text) ORDER BY l.position)
    INTO old_members FROM public.document_cost_link l JOIN public.cost_entry c ON c.organization_id=l.organization_id AND c.id=l.cost_entry_id
    WHERE l.organization_id=org AND l.allocation_set_id=old.id;
  SELECT jsonb_agg(jsonb_build_object('allocation_line_id',l.id,'position',l.position,'gross_amount',l.gross_amount::text,
    'vat_amount',l.vat_amount::text,'tenant_amount',treatment_line.tenant_amount::text) ORDER BY l.position)
    INTO new_members FROM public.allocation_proposal_line l JOIN public.allocation_treatment_line treatment_line
      ON treatment_line.organization_id=l.organization_id AND treatment_line.allocation_line_id=l.id AND treatment_line.treatment_review_id=target
    WHERE l.organization_id=org AND l.allocation_proposal_id=a.id;
  digest:=encode(sha256(convert_to(jsonb_build_array('allocation-correction-preview-v1',old.id,target,t.snapshot_digest,
    old_members,new_members,duplicates,impacts->'basis')::text,'UTF8')),'hex');
  RETURN jsonb_build_object('can_review',true,'preview_digest',digest,'blocker',NULL,'predecessor_id',old.id,'treatment_review_id',target,
    'property_id',old.property_id,'scope_id',old.scope_id,'source_document_id',old.source_document_id,
    'old',jsonb_build_object('gross_amount',prior.gross_amount::text,'service_period_from',prior.service_period_from,'service_period_to',prior.service_period_to,'costs',old_members),
    'proposed',jsonb_build_object('gross_amount',r.gross_amount::text,'service_period_from',r.service_period_from,'service_period_to',r.service_period_to,'costs',new_members),
    'impacts',impacts->'items','financial_state','preview_only');
END $$;
REVOKE ALL ON FUNCTION allocation_correction_preview(uuid,uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION allocation_correction_preview(uuid,uuid,uuid) TO app_rw;
'''
