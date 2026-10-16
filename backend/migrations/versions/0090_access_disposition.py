"""Assess retained legacy resources against actual strict denial conditions."""
from alembic import op

revision = '0090'
down_revision = '0089'
branch_labels = None
depends_on = None
# 0087 KINDS (inlined to avoid importlib):
KINDS = (
    'property','unit','person','owner_profile','tenant_profile','vendor','ownership_history','lease_agreement',
    'management_mandate','accounting_scope','bank_account','payment','cost_entry','settlement_run','settlement_line',
    'coverage_declaration','economic_plan','economic_plan_line','invoice_document','invoice_processing_attempt',
    'mandate_document','dunning_notice','owners_meeting','agenda_item','resolution','resolution_register_entry',
    'property_document','declaration_import','declaration_import_document','declaration_queue_request',
    'declaration_extraction_run','declaration_import_pass','declaration_candidate','declaration_candidate_evidence',
    'declaration_candidate_issue','property_legal_reference','property_assignment','property_document_relation',
    'property_rule','property_rule_relation','property_rule_source','approved_fact_source','property_knowledge_query_run',
)


def upgrade():
    kinds = 'ARRAY['+','.join("'"+kind+"'" for kind in KINDS)+']'
    op.execute('''
    CREATE TABLE access_disposition_acceptance (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL REFERENCES organization(id),
      snapshot_id uuid NOT NULL, manifest_digest text NOT NULL, assessment_digest text NOT NULL, assessment jsonb NOT NULL,
      accepted_by uuid NOT NULL, accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(), reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 2000),
      request_key uuid NOT NULL, request_digest text NOT NULL,
      UNIQUE(organization_id,id), UNIQUE(organization_id,snapshot_id), UNIQUE(organization_id,accepted_by,request_key),
      FOREIGN KEY(organization_id,snapshot_id) REFERENCES access_coverage_snapshot(organization_id,id),
      FOREIGN KEY(organization_id,accepted_by) REFERENCES "user"(organization_id,id));
    ALTER TABLE access_disposition_acceptance ENABLE ROW LEVEL SECURITY;
    ALTER TABLE access_disposition_acceptance FORCE ROW LEVEL SECURITY;
    CREATE POLICY disposition_private ON access_disposition_acceptance AS RESTRICTIVE FOR ALL USING(false) WITH CHECK(false);
    REVOKE ALL ON access_disposition_acceptance FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER protect_disposition BEFORE UPDATE OR DELETE ON access_disposition_acceptance
      FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();
    CREATE FUNCTION access_disposition_receipt(receipt public.access_disposition_acceptance) RETURNS jsonb
    LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
      SELECT CASE WHEN (receipt).id IS NULL THEN NULL ELSE
        (to_jsonb(receipt)-ARRAY['organization_id','assessment','request_key','request_digest'])||jsonb_build_object(
          'retained_count',(receipt).assessment->'retained_count','team_count',(receipt).assessment->'team_count','activation_available',false) END
    $$;
    REVOKE ALL ON FUNCTION access_disposition_receipt(public.access_disposition_acceptance) FROM PUBLIC,app_rw,app_worker,app_review;
    ''')
    op.execute(f'''
    CREATE FUNCTION access_item_disposition(item jsonb) RETURNS text
    LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
    DECLARE kind text:=item->>'kind'; findings jsonb:=item->'findings'; refs jsonb:=item->'references';
    BEGIN
      IF kind='member' AND findings IN ('["EXPLICIT_TEAM_ASSIGNMENT_REVIEW"]'::jsonb,'["INACTIVE_PRINCIPAL"]'::jsonb) THEN RETURN 'TEAM_REVIEW'; END IF;
      IF kind IS NULL OR NOT (kind=ANY({kinds}) OR kind='knowledge_budget_policy') OR jsonb_typeof(findings) IS DISTINCT FROM 'array' THEN RETURN 'BLOCKED'; END IF;
      IF findings='[]'::jsonb THEN RETURN 'READY'; END IF;
      -- A combined audience is stronger than ordinary row access. Its emptiness
      -- is only an additional condition, never the justification for retention.
      IF refs->'strict_audience' IS DISTINCT FROM '[]'::jsonb THEN RETURN 'BLOCKED'; END IF;
      IF kind IN ('bank_account','payment','cost_entry','settlement_run','economic_plan')
        AND refs->>'binding_id' IS NULL AND refs->>'scope_id' IS NULL
        AND findings @> '["FINANCIAL_BINDING_REQUIRED"]'::jsonb
        AND findings <@ '["FINANCIAL_BINDING_REQUIRED","COMBINED_READER_REQUIRED"]'::jsonb THEN RETURN 'RETAIN_BLOCKED'; END IF;
      IF findings='["REVIEWED_QUARANTINE"]'::jsonb AND (
        (kind='property_document' AND refs->>'classification'='QUARANTINED' AND refs->>'classification_id' IS NOT NULL)
        OR (kind='mandate_document' AND refs->>'scope_id' IS NULL AND refs->>'observation_id' IS NOT NULL AND refs->>'disposition_id' IS NOT NULL)
        OR (kind='dunning_notice' AND refs->>'context_id' IS NULL AND refs->>'disposition_id' IS NOT NULL)) THEN RETURN 'RETAIN_BLOCKED'; END IF;
      IF kind='invoice_document' AND refs->>'cost_entry_id' IS NULL AND refs->>'creator_id' IS NULL
        AND findings @> '["UNKNOWN_CUSTODY"]'::jsonb AND findings <@ '["UNKNOWN_CUSTODY","COMBINED_READER_REQUIRED"]'::jsonb THEN RETURN 'RETAIN_BLOCKED'; END IF;
      RETURN 'BLOCKED';
    END $$;
    CREATE FUNCTION access_disposition_work(org uuid) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT COALESCE(jsonb_agg(jsonb_build_object('kind',kind,'resource_id',id) ORDER BY kind COLLATE "C",id),'[]') FROM (
        SELECT 'declaration_import'::text kind,id FROM public.declaration_import WHERE organization_id=org
          AND (status IN ('QUEUED','PREFLIGHT','EXTRACTING','RECONCILING','RETRY_WAIT','APPLYING') OR lease_expires_at>clock_timestamp())
        UNION ALL SELECT 'declaration_extraction_run',id FROM public.declaration_extraction_run WHERE organization_id=org AND status='RUNNING'
        UNION ALL SELECT 'invoice_document',id FROM public.invoice_document WHERE organization_id=org AND processing_status IN ('queued','running')
        UNION ALL SELECT 'invoice_processing_attempt',id FROM public.invoice_processing_attempt WHERE organization_id=org AND status IN ('queued','running')
        UNION ALL SELECT 'property_knowledge_query_run',id FROM public.property_knowledge_query_run WHERE organization_id=org AND status='RESERVED'
      ) work;
    $$;
    CREATE FUNCTION access_disposition_data(org uuid,snapshot uuid,policy jsonb) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE source public.access_coverage_snapshot; data jsonb; entries jsonb; work jsonb; result jsonb; current_digest text;
    BEGIN
      PERFORM public.require_access_coverage_admin(org,true);
      SELECT * INTO source FROM public.access_coverage_snapshot WHERE organization_id=org AND id=snapshot;
      IF NOT FOUND THEN RAISE EXCEPTION 'Coverage snapshot inaccessible' USING ERRCODE='42501'; END IF;
      data:=public.access_coverage_manifest(org,policy);
      current_digest:=encode(sha256(convert_to(data::text,'UTF8')),'hex');
      SELECT COALESCE(jsonb_agg(jsonb_build_object('kind',item->>'kind','resource_id',item->>'resource_id',
        'findings',item->'findings','disposition',CASE WHEN source.checker_version=3 THEN public.access_item_disposition(item) ELSE 'BLOCKED' END)
        ORDER BY item->>'kind' COLLATE "C",(item->>'resource_id')::uuid),'[]') INTO entries FROM jsonb_array_elements(source.manifest->'items') item;
      work:=public.access_disposition_work(org);
      result:=jsonb_build_object('snapshot_id',source.id,'manifest_digest',source.manifest_digest,'items',entries,'active_work',work,
        'ready_count',(SELECT count(*) FROM jsonb_array_elements(entries) item WHERE item->>'disposition'='READY'),
        'retained_count',(SELECT count(*) FROM jsonb_array_elements(entries) item WHERE item->>'disposition'='RETAIN_BLOCKED'),
        'team_count',(SELECT count(*) FROM jsonb_array_elements(entries) item WHERE item->>'disposition'='TEAM_REVIEW'),
        'blocker_count',(SELECT count(*) FROM jsonb_array_elements(entries) item WHERE item->>'disposition'='BLOCKED'),
        'active_work_count',jsonb_array_length(work));
      result:=result||jsonb_build_object('assessment_digest',encode(sha256(convert_to(result::text,'UTF8')),'hex'),
        'current',source.checker_version=3 AND source.manifest_digest=current_digest AND public.runtime_budget_attested(org,policy),
        'reviewed',EXISTS(SELECT 1 FROM public.access_coverage_review WHERE organization_id=org AND snapshot_id=snapshot));
      RETURN result;
    END $$;
    CREATE FUNCTION access_disposition_status(org uuid,snapshot uuid,policy jsonb) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE data jsonb; receipt public.access_disposition_acceptance;
    BEGIN
      data:=public.access_disposition_data(org,snapshot,policy);
      SELECT * INTO receipt FROM public.access_disposition_acceptance WHERE organization_id=org AND snapshot_id=snapshot;
      RETURN (data-ARRAY['items','active_work'])||jsonb_build_object('activation_available',false,'acceptance',public.access_disposition_receipt(receipt),
        'acceptance_available',(data->>'current')::boolean AND (data->>'reviewed')::boolean
          AND (data->>'blocker_count')::integer=0 AND (data->>'active_work_count')::integer=0 AND receipt.id IS NULL);
    END $$;
    CREATE FUNCTION access_disposition_page(org uuid,snapshot uuid,after_kind text,after_id uuid,take integer) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE rows jsonb; version integer; result jsonb;
    BEGIN
      rows:=public.access_coverage_page(org,snapshot,after_kind,after_id,take);
      SELECT checker_version INTO version FROM public.access_coverage_snapshot WHERE organization_id=org AND id=snapshot;
      SELECT COALESCE(jsonb_agg(jsonb_build_object('kind',item->>'kind','resource_id',item->>'resource_id','findings',item->'findings',
        'disposition',CASE WHEN version=3 THEN public.access_item_disposition(item) ELSE 'BLOCKED' END)
        ORDER BY item->>'kind' COLLATE "C",(item->>'resource_id')::uuid),'[]') INTO result FROM jsonb_array_elements(rows) item;
      RETURN result;
    END $$;
    REVOKE ALL ON FUNCTION access_item_disposition(jsonb),access_disposition_work(uuid),access_disposition_data(uuid,uuid,jsonb),
      access_disposition_status(uuid,uuid,jsonb),access_disposition_page(uuid,uuid,text,uuid,integer) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION access_disposition_status(uuid,uuid,jsonb),access_disposition_page(uuid,uuid,text,uuid,integer) TO app_rw;
    ''')

    op.execute('''
    CREATE FUNCTION accept_access_disposition(org uuid,snapshot uuid,request uuid,manifest text,assessment text,explanation text,team_ack boolean,retention_ack boolean,policy jsonb) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; request_hash text; data jsonb; receipt public.access_disposition_acceptance;
    BEGIN
      PERFORM public.require_access_coverage_admin(org,true);
      IF request IS NULL OR manifest IS NULL OR manifest !~ '^[0-9a-f]{64}$' OR assessment IS NULL OR assessment !~ '^[0-9a-f]{64}$'
        OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 2000 OR team_ack IS DISTINCT FROM true OR retention_ack IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Invalid retained disposition' USING ERRCODE='22023'; END IF;
      explanation:=btrim(explanation);
      request_hash:=encode(sha256(convert_to(jsonb_build_object('snapshot',snapshot,'manifest',manifest,'assessment',assessment,'reason',explanation)::text,'UTF8')),'hex');
      SELECT * INTO receipt FROM public.access_disposition_acceptance WHERE organization_id=org AND accepted_by=actor AND request_key=request;
      IF FOUND THEN
        IF receipt.request_digest<>request_hash THEN RAISE EXCEPTION 'Disposition request conflict' USING ERRCODE='23505'; END IF;
        RETURN public.access_disposition_receipt(receipt);
      END IF;
      data:=public.access_disposition_data(org,snapshot,policy);
      IF NOT (data->>'current')::boolean OR NOT (data->>'reviewed')::boolean OR data->>'manifest_digest'<>manifest OR data->>'assessment_digest'<>assessment
        OR (data->>'blocker_count')::integer<>0 OR (data->>'active_work_count')::integer<>0 THEN
        RAISE EXCEPTION 'Disposition evidence is stale or unresolved' USING ERRCODE='40001'; END IF;
      INSERT INTO public.access_disposition_acceptance(organization_id,snapshot_id,manifest_digest,assessment_digest,assessment,accepted_by,reason,request_key,request_digest)
        VALUES(org,snapshot,manifest,assessment,data,actor,explanation,request,request_hash) RETURNING * INTO receipt;
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(org,actor,'access_coverage_snapshot',snapshot,'access.disposition_accepted',jsonb_build_object('acceptance_id',receipt.id,'assessment_digest',assessment,
          'retained_count',data->'retained_count','team_count',data->'team_count'));
      RETURN public.access_disposition_receipt(receipt);
    END $$;
    REVOKE ALL ON FUNCTION accept_access_disposition(uuid,uuid,uuid,text,text,text,boolean,boolean,jsonb) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION accept_access_disposition(uuid,uuid,uuid,text,text,text,boolean,boolean,jsonb) TO app_rw;
    ''')


def downgrade():
    op.execute('LOCK TABLE access_disposition_acceptance IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM access_disposition_acceptance) THEN RAISE EXCEPTION 'Retain disposition evidence; repair forward'; END IF; END $$")
    op.execute('''DROP FUNCTION accept_access_disposition(uuid,uuid,uuid,text,text,text,boolean,boolean,jsonb),access_disposition_receipt(public.access_disposition_acceptance),
      access_disposition_page(uuid,uuid,text,uuid,integer),access_disposition_status(uuid,uuid,jsonb),
      access_disposition_data(uuid,uuid,jsonb),access_disposition_work(uuid),access_item_disposition(jsonb);
      DROP TABLE access_disposition_acceptance;''')
