"""Materialize private access evidence and explicit administrative review receipts."""
from alembic import op
from sqlalchemy import text

revision = '0083'
down_revision = '0082'
branch_labels = None
depends_on = None

# Explicit dependencies, not a catalog-wide dump. No audit/operation receipts or
# passwords are returned. Hashes remain inside the private manifest.
EVIDENCE = (
    'property','unit','person','owner_profile','tenant_profile','vendor','membership',
    'ownership_history','lease_agreement','management_mandate','accounting_scope',
    'accounting_scope_unit','accounting_scope_binding','scope_grant','scope_migration_exception',
    'property_data_domain','property_data_grant','person_data_domain','person_data_grant',
    'bank_account','payment','cost_entry','settlement_run','settlement_line','coverage_declaration',
    'economic_plan','economic_plan_line','invoice_document','invoice_upload_admission','invoice_processing_attempt',
    'mandate_document','mandate_legacy_observation','mandate_quarantine_disposition',
    'dunning_notice','dunning_scope_context','dunning_quarantine_disposition',
    'owners_meeting','agenda_item','resolution','resolution_register_entry',
    'property_document','property_source_admission','property_source_admission_review',
    'property_source_classification','property_creation_receipt','declaration_import',
    'declaration_import_document','declaration_queue_request','declaration_extraction_run',
    'declaration_import_pass','declaration_candidate','declaration_candidate_evidence','declaration_candidate_issue',
    'property_legal_reference','property_assignment','property_document_relation',
    'property_rule','property_rule_relation','property_rule_source','approved_fact_source',
    'property_knowledge_query_run','knowledge_budget_policy','property_source_security',
)
STRUCTURAL = ('id','organization_id','uploaded_by','created_by','initiated_by','reviewed_by','admitted_by','approved_by',
    'changed_by','granted_by','revoked_by','attestor_id','scope_type','management_type','mandate_type','mode','origin',
    'capabilities','expires_at','revoked_at','valid_from','valid_to','start_date','end_date','period_start','period_end',
    'service_period_from','service_period_to','value_date','whole_property','status','state','decision','version',
    'archived_at','released_at','content_sha256','source_context_digest','snapshot_digest','payload_digest',
    'document_role','sequence_no','subject_kind','review_status','resolution_status','processing_status',
    'role','max_cost_usd','window_minutes','reservation_seconds','reason','is_active')
AUDIT_BRANCH = """      IF kind='access_coverage_snapshot' THEN RETURN public.access_coverage_audit_access(org,resource); END IF;
"""
CHILD_REFERENCES = {
    'owner_profile': ('person_id',), 'tenant_profile': ('person_id',), 'vendor': ('person_id',),
    'ownership_history': ('unit_id','owner_profile_id'), 'lease_agreement': ('unit_id','tenant_profile_id'),
    'management_mandate': (),
    'settlement_line': ('settlement_run_id','cost_category_id','addressee_owner_profile_id'),
    'economic_plan_line': ('economic_plan_id','cost_category_id','owner_profile_id','unit_id'),
    'coverage_declaration': ('settlement_run_id','economic_plan_id','property_id','attestor_id'),
    'owners_meeting': ('property_id',), 'agenda_item': ('meeting_id',),
    'resolution': ('meeting_id','agenda_item_id'), 'resolution_register_entry': ('property_id','resolution_id'),
    'declaration_import_document': ('import_id','document_id'),
    'declaration_queue_request': ('import_id','actor_user_id','previous_request_id'),
    'declaration_candidate': ('import_id','reviewed_by'),
    'declaration_candidate_evidence': ('candidate_id','document_id','import_id'),
    'declaration_candidate_issue': ('import_id','candidate_id','resolved_by'),
    'declaration_extraction_run': ('import_id',), 'declaration_import_pass': ('declaration_extraction_run_id',),
    'invoice_processing_attempt': ('document_id','initiated_by','requested_scope_id'),
    'property_knowledge_query_run': ('property_id','initiated_by'),
}


def _items_sql():
    rows = []
    def add(kind, source, references, findings, audience="'[]'::jsonb"):
        rows.append(f"SELECT '{kind}'::text kind,r.id resource_id,{references} refs,{findings} findings,{audience} audience FROM {source} WHERE r.organization_id=org")
    for kind, domain, grant in [('property','property_data_domain','property_data_grant'),('person','person_data_domain','person_data_grant')]:
        owned = "r.created_by IS NOT NULL AND EXISTS(SELECT 1 FROM public.\"user\" u JOIN public.membership m ON m.organization_id=u.organization_id AND m.user_id=u.id WHERE u.organization_id=org AND u.id=r.created_by AND u.is_active)" if kind=='person' else 'false'
        audience = f"public.access_coverage_grants(org,'{grant}',d.id,instant)"
        checks = [f"CASE WHEN d.id IS NULL AND NOT ({owned}) THEN 'DOMAIN_REQUIRED' END",
                  f"CASE WHEN d.id IS NOT NULL AND NOT jsonb_path_exists({audience},'$[*].effective_capabilities[*] ? (@ == \"read\")') THEN 'LIVE_READER_REQUIRED' END"]
        if kind == 'property':
            checks += ["CASE WHEN NOT public.property_history_graph_valid(org,r.id) THEN 'HISTORY_GRAPH_INVALID' END",
                       "CASE WHEN NOT public.property_meeting_graph_valid(org,r.id) THEN 'MEETING_GRAPH_INVALID' END",
                       "CASE WHEN EXISTS(SELECT 1 FROM public.property_source_classification c WHERE c.organization_id=org AND c.property_id=r.id AND c.decision='QUARANTINED') THEN 'QUARANTINED_INPUTS_BLOCK_OPERATIONS' END"]
        add(kind, f'public.{kind} r LEFT JOIN public.{domain} d ON d.organization_id=r.organization_id AND d.{kind}_id=r.id',
            "jsonb_build_object('domain_id',d.id)" + (" || jsonb_build_object('creator_id',r.created_by)" if kind=='person' else ''),
            'array_remove(ARRAY['+','.join(checks)+'],NULL)', audience)
    add('accounting_scope','public.accounting_scope r',
        "jsonb_build_object('property_id',r.property_id,'mandate_id',r.management_mandate_id,'scope_type',r.scope_type,'whole_property',r.whole_property,'valid_from',r.valid_from,'valid_to',r.valid_to,'unit_ids',(SELECT COALESCE(jsonb_agg(s.unit_id ORDER BY s.unit_id),'[]') FROM public.accounting_scope_unit s WHERE s.organization_id=org AND s.scope_id=r.id))",
        "array_remove(ARRAY[CASE WHEN r.scope_type='SEV' THEN 'SEV_FINANCIAL_ADAPTER_UNSUPPORTED' END,CASE WHEN NOT jsonb_path_exists(public.access_coverage_grants(org,'scope_grant',r.id,instant),'$[*].effective_capabilities[*] ? (@ == \"read\")') THEN 'LIVE_READER_REQUIRED' END],NULL)",
        "public.access_coverage_grants(org,'scope_grant',r.id,instant)")
    for kind in ('bank_account','payment','cost_entry','settlement_run','economic_plan'):
        references = "jsonb_build_object('property_id',r.property_id,'scope_id',b.scope_id,'binding_id',b.id)"
        findings = "CASE WHEN b.id IS NULL THEN ARRAY['FINANCIAL_BINDING_REQUIRED'] ELSE ARRAY[]::text[] END"
        if kind == 'payment':
            references += " || jsonb_build_object('bank_account_id',r.bank_account_id,'tenant_profile_id',r.tenant_profile_id,'owner_profile_id',r.owner_profile_id,'lease_agreement_id',r.lease_agreement_id)"
            findings += " || CASE WHEN r.tenant_profile_id IS NOT NULL OR r.owner_profile_id IS NOT NULL THEN ARRAY['RECIPIENT_ACCESS_REVIEW_REQUIRED'] ELSE ARRAY[]::text[] END"
        add(kind,f'public.{kind} r LEFT JOIN public.accounting_scope_binding b ON b.organization_id=r.organization_id AND b.{kind}_id=r.id',
            references,findings)
    add('property_document','public.property_document r LEFT JOIN public.property_source_classification c ON c.organization_id=r.organization_id AND c.document_id=r.id',
        "jsonb_build_object('property_id',r.property_id,'creator_id',r.uploaded_by,'classification_id',c.id,'classification',c.decision,'import_ids',(SELECT COALESCE(jsonb_agg(l.import_id ORDER BY l.import_id),'[]') FROM public.declaration_import_document l WHERE l.organization_id=org AND l.document_id=r.id))",
        "array_remove(ARRAY[CASE WHEN c.decision='QUARANTINED' THEN 'REVIEWED_QUARANTINE' WHEN r.property_id IS NOT NULL AND NOT public.property_source_bound_proven(org,r.id) THEN 'VALID_BOUND_PROOF_REQUIRED' WHEN r.property_id IS NULL AND r.uploaded_by IS NULL THEN 'UNKNOWN_CUSTODY' END,CASE WHEN (SELECT count(*) FROM public.declaration_import_document l WHERE l.organization_id=org AND l.document_id=r.id)>1 THEN 'MULTIPLE_IMPORT_CONTEXTS' END,CASE WHEN c.decision IS DISTINCT FROM 'QUARANTINED' AND NOT EXISTS(SELECT 1 FROM public.\"user\" u WHERE u.organization_id=org AND u.is_active AND public.property_source_document_actor_access(org,u.id,r.id,'read')) THEN 'SOURCE_AUDIENCE_REVIEW_REQUIRED' END],NULL)")
    add('declaration_import','public.declaration_import r',
        "jsonb_build_object('property_id',COALESCE(r.applied_property_id,r.target_property_id),'creator_id',r.initiated_by,'publication_grant_id',r.publication_grant_id)",
        "array_remove(ARRAY[CASE WHEN r.initiated_by IS NULL THEN 'UNKNOWN_CUSTODY' END,CASE WHEN r.lease_expires_at>instant OR r.status IN ('QUEUED','PREFLIGHT','EXTRACTING','RECONCILING','RETRY_WAIT','APPLYING') THEN 'WORK_IN_PROGRESS' END,CASE WHEN NOT EXISTS(SELECT 1 FROM public.\"user\" u WHERE u.organization_id=org AND u.is_active AND public.property_source_import_actor_access(org,u.id,r.id,'read')) THEN 'IMPORT_AUDIENCE_REVIEW_REQUIRED' END,CASE WHEN EXISTS(SELECT 1 FROM public.declaration_import_document l JOIN public.property_document d ON d.organization_id=l.organization_id AND d.id=l.document_id WHERE l.organization_id=org AND l.import_id=r.id AND d.property_id IS NOT NULL AND NOT public.property_source_bound_proven(org,d.id)) THEN 'SOURCE_DEPENDENCY_BLOCKS_OPERATIONS' END],NULL)")
    add('invoice_document','public.invoice_document r LEFT JOIN public.invoice_document root ON root.organization_id=r.organization_id AND root.id=r.chain_root_id',
        "jsonb_build_object('cost_entry_id',r.cost_entry_id,'creator_id',r.uploaded_by,'chain_root_id',r.chain_root_id)",
        "array_remove(ARRAY[CASE WHEN r.cost_entry_id IS NULL AND (r.uploaded_by IS NULL OR root.uploaded_by IS DISTINCT FROM r.uploaded_by) THEN 'UNKNOWN_CUSTODY' END,CASE WHEN r.cost_entry_id IS NULL AND r.uploaded_by IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.\"user\" u JOIN public.membership m ON m.organization_id=u.organization_id AND m.user_id=u.id WHERE u.organization_id=org AND u.id=r.uploaded_by AND u.is_active) THEN 'LIVE_CREATOR_REQUIRED' END,CASE WHEN r.cost_entry_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.accounting_scope_binding b WHERE b.organization_id=org AND b.cost_entry_id=r.cost_entry_id) THEN 'FINANCIAL_BINDING_REQUIRED' END,CASE WHEN r.processing_status IN ('queued','running') THEN 'WORK_IN_PROGRESS' END],NULL)")
    add('mandate_document','public.mandate_document r LEFT JOIN public.mandate_legacy_observation o ON o.organization_id=r.organization_id AND o.document_id=r.id LEFT JOIN public.mandate_quarantine_disposition q ON q.organization_id=r.organization_id AND q.document_id=r.id',
        "jsonb_build_object('scope_id',r.accounting_scope_id,'observation_id',o.document_id,'disposition_id',q.id)",
        "CASE WHEN r.accounting_scope_id IS NOT NULL THEN ARRAY[]::text[] WHEN o.document_id IS NOT NULL AND q.id IS NOT NULL AND o.snapshot_digest=q.snapshot_digest THEN ARRAY['REVIEWED_QUARANTINE'] ELSE ARRAY['QUARANTINE_REVIEW_REQUIRED'] END")
    add('dunning_notice','public.dunning_notice r LEFT JOIN public.dunning_scope_context c ON c.organization_id=r.organization_id AND c.dunning_notice_id=r.id LEFT JOIN public.dunning_quarantine_disposition q ON q.organization_id=r.organization_id AND q.notice_id=r.id',
        "jsonb_build_object('context_id',c.dunning_notice_id,'scope_id',c.scope_id,'disposition_id',q.id)",
        "CASE WHEN c.dunning_notice_id IS NOT NULL THEN ARRAY[]::text[] WHEN q.id IS NOT NULL AND q.snapshot_digest=encode(sha256(convert_to(to_jsonb(r)::text,'UTF8')),'hex') THEN ARRAY['REVIEWED_QUARANTINE'] ELSE ARRAY['DUNNING_CONTEXT_REVIEW_REQUIRED'] END")
    for kind in ('unit','property_legal_reference','property_assignment','property_document_relation','property_rule','property_rule_relation','property_rule_source','approved_fact_source'):
        add(kind,f'public.{kind} r',"jsonb_build_object('property_id',r.property_id)",
            f"array_remove(ARRAY[CASE WHEN NOT EXISTS(SELECT 1 FROM public.\"user\" u WHERE u.organization_id=org AND u.is_active AND public.property_source_actor_access(org,u.id,'{kind}',r.id,'read')) THEN 'DERIVED_AUDIENCE_REVIEW_REQUIRED' END,CASE WHEN EXISTS(SELECT 1 FROM public.property_document d WHERE d.organization_id=org AND d.property_id=r.property_id AND NOT public.property_source_bound_proven(org,d.id)) THEN 'SOURCE_DEPENDENCY_BLOCKS_OPERATIONS' END],NULL)")
    add('member','public."user" r LEFT JOIN public.membership m ON m.organization_id=r.organization_id AND m.user_id=r.id',
        "jsonb_build_object('role',m.role,'active',r.is_active)",
        "CASE WHEN r.is_active AND m.role IS NOT NULL THEN ARRAY['EXPLICIT_TEAM_ASSIGNMENT_REVIEW'] ELSE ARRAY['INACTIVE_PRINCIPAL'] END")
    add('knowledge_budget_policy','(SELECT id,id organization_id FROM public.organization) r LEFT JOIN public.knowledge_budget_policy p ON p.organization_id=r.id',
        "jsonb_build_object('reviewed_by',p.changed_by,'reviewed_at',p.changed_at,'expected_policy',policy)",
        "CASE WHEN p.organization_id IS NULL THEN ARRAY['BUDGET_POLICY_REVIEW_REQUIRED'] WHEN p.max_cost_usd IS DISTINCT FROM (policy->>'max_cost_usd')::numeric OR p.window_minutes IS DISTINCT FROM (policy->>'window_minutes')::integer OR p.reservation_seconds IS DISTINCT FROM (policy->>'reservation_seconds')::integer THEN ARRAY['BUDGET_POLICY_CONFIGURATION_MISMATCH'] ELSE ARRAY[]::text[] END")
    # These rows expose their exact parent edges for human review. Their combined
    # strict source/recipient authority is deliberately an unresolved finding,
    # never inferred from an existing parent or PREPARATION visibility.
    for kind, columns in CHILD_REFERENCES.items():
        # Explicit UUID relationship allowlist: suffixes also match protected
        # business identifiers (vendor tax_id/vat_id) and must not drive output.
        refs = 'jsonb_build_object('+','.join(f"'{column}',r.{column}" for column in columns)+')'
        findings = "ARRAY['PARENT_CONTEXT_REVIEW_REQUIRED']"
        if kind == 'invoice_processing_attempt':
            findings += " || array_remove(ARRAY[CASE WHEN r.initiated_by IS NULL OR NOT EXISTS(SELECT 1 FROM public.\"user\" u JOIN public.membership m ON m.organization_id=u.organization_id AND m.user_id=u.id WHERE u.organization_id=org AND u.id=r.initiated_by AND u.is_active AND m.role IN ('OWNER_ADMIN','CLERK')) THEN 'PROCESSING_PRINCIPAL_REVIEW_REQUIRED' END,CASE WHEN r.status IN ('queued','running') THEN 'WORK_IN_PROGRESS' END],NULL)"
        if kind == 'coverage_declaration':
            refs += " || jsonb_build_object('source_document_ids',(SELECT COALESCE(jsonb_agg(value),'[]') FROM jsonb_array_elements(CASE WHEN jsonb_typeof(r.source_document_ids)='array' THEN r.source_document_ids ELSE '[]'::jsonb END) a WHERE jsonb_typeof(value)='string' AND value#>>'{}' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'))"
            findings += " || CASE WHEN NOT EXISTS(SELECT 1 FROM public.settlement_run s WHERE s.organization_id=org AND s.id=r.settlement_run_id AND s.property_id=r.property_id) AND NOT EXISTS(SELECT 1 FROM public.economic_plan p WHERE p.organization_id=org AND p.id=r.economic_plan_id AND p.property_id=r.property_id) THEN ARRAY['PARENT_PROPERTY_MISMATCH'] ELSE ARRAY[]::text[] END"
            findings += " || CASE WHEN jsonb_typeof(r.source_document_ids) IS DISTINCT FROM 'array' OR EXISTS(SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(r.source_document_ids)='array' THEN r.source_document_ids ELSE '[]'::jsonb END) a WHERE jsonb_typeof(value) IS DISTINCT FROM 'string' OR value#>>'{}' !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') THEN ARRAY['INVALID_REFERENCE_ARRAY'] ELSE ARRAY[]::text[] END"
        add(kind,f'public.{kind} r',refs,findings)
    return '\nUNION ALL\n'.join(rows)


def upgrade():
    op.execute('''
    CREATE TABLE access_coverage_snapshot (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL REFERENCES organization(id),
      predecessor_id uuid, checker_version integer NOT NULL DEFAULT 1 CHECK(checker_version=1),
      generated_by uuid NOT NULL, generated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      request_key uuid NOT NULL, request_digest text NOT NULL,
      manifest_digest text NOT NULL CHECK(manifest_digest ~ '^[0-9a-f]{64}$'), manifest jsonb NOT NULL,
      item_count integer NOT NULL, finding_count integer NOT NULL,
      UNIQUE(organization_id,id), UNIQUE(organization_id,generated_by,request_key),
      FOREIGN KEY(organization_id,predecessor_id) REFERENCES access_coverage_snapshot(organization_id,id),
      FOREIGN KEY(organization_id,generated_by) REFERENCES "user"(organization_id,id));
    CREATE TABLE access_coverage_review (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL,
      snapshot_id uuid NOT NULL, manifest_digest text NOT NULL, finding_count integer NOT NULL,
      reviewed_by uuid NOT NULL, reviewed_at timestamptz NOT NULL DEFAULT clock_timestamp(), request_key uuid NOT NULL,
      UNIQUE(organization_id,snapshot_id), UNIQUE(organization_id,reviewed_by,request_key),
      FOREIGN KEY(organization_id,snapshot_id) REFERENCES access_coverage_snapshot(organization_id,id),
      FOREIGN KEY(organization_id,reviewed_by) REFERENCES "user"(organization_id,id));
    ''')
    for table in ('access_coverage_snapshot','access_coverage_review'):
        op.execute(f'''ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          CREATE POLICY coverage_private ON {table} AS RESTRICTIVE FOR ALL USING(false) WITH CHECK(false);
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review;
          CREATE TRIGGER protect_coverage BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();''')
    op.execute('''
    CREATE FUNCTION access_coverage_grants(org uuid,kind text,audience uuid,instant timestamptz) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE result jsonb;
    BEGIN
      IF kind NOT IN ('scope_grant','property_data_grant','person_data_grant') THEN RAISE EXCEPTION 'Unsupported audience'; END IF;
      EXECUTE format('SELECT COALESCE(jsonb_agg(jsonb_build_object(''grant_id'',g.id,''user_id'',g.user_id,
        ''capabilities'',g.capabilities,''expires_at'',g.expires_at,''revoked_at'',g.revoked_at,
        ''role'',m.role,''active'',u.is_active,''effective_capabilities'',
        CASE WHEN u.is_active AND m.role IS NOT NULL AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at>$3)
          THEN (SELECT COALESCE(jsonb_agg(cap ORDER BY cap),''[]'') FROM unnest(g.capabilities) cap
            WHERE m.role IN (''OWNER_ADMIN'',''CLERK'') OR cap=''read'' OR ($4<>''scope_grant'' AND cap=''export''))
          ELSE ''[]''::jsonb END) ORDER BY g.id),''[]'') FROM public.%I g
        JOIN public."user" u ON u.organization_id=g.organization_id AND u.id=g.user_id
        JOIN public.membership m ON m.organization_id=g.organization_id AND m.user_id=g.user_id
        WHERE g.organization_id=$1 AND g.%I=$2',kind,CASE WHEN kind='scope_grant' THEN 'scope_id' ELSE 'domain_id' END)
        INTO result USING org,audience,instant,kind;
      RETURN result;
    END $$;
    REVOKE ALL ON FUNCTION access_coverage_grants(uuid,text,uuid,timestamptz) FROM PUBLIC,app_rw,app_worker,app_review;
    ''')
    keys = 'ARRAY['+','.join("'"+key+"'" for key in STRUCTURAL)+']'
    evidence = '\nUNION ALL\n'.join(
        f"SELECT '{table}'::text kind,encode(sha256(convert_to(COALESCE(string_agg(encode(sha256(convert_to(s.data::text,'UTF8')),'hex'),'' ORDER BY s.data::text COLLATE \"C\"),''),'UTF8')),'hex') digest FROM public.{table} r CROSS JOIN LATERAL (SELECT jsonb_object_agg(key,value) data FROM jsonb_each(to_jsonb(r)) WHERE key=ANY({keys}) OR right(key,3)='_id' OR right(key,4)='_ids') s WHERE r.organization_id=org"
        for table in EVIDENCE)
    op.execute(f'''
    CREATE FUNCTION access_coverage_manifest(org uuid,policy jsonb) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE instant timestamptz:=clock_timestamp(); result jsonb;
    BEGIN
      WITH entries AS MATERIALIZED ({_items_sql()}), dependencies AS MATERIALIZED ({evidence})
      SELECT jsonb_build_object('checker_version',1,'expected_policy',policy,
        'items',COALESCE((SELECT jsonb_agg(jsonb_build_object('kind',kind,'resource_id',resource_id,
          'references',jsonb_strip_nulls(refs),'findings',findings,'audience',audience) ORDER BY kind COLLATE "C",resource_id) FROM entries),'[]'),
        'dependencies',(SELECT jsonb_agg(jsonb_build_object('kind',kind,'digest',digest) ORDER BY kind COLLATE "C") FROM dependencies),
        'principals',(SELECT COALESCE(jsonb_agg(jsonb_build_object('id',u.id,'active',u.is_active,'role',m.role) ORDER BY u.id),'[]')
          FROM public."user" u LEFT JOIN public.membership m ON m.organization_id=u.organization_id AND m.user_id=u.id WHERE u.organization_id=org),
        'budget_policy_matches',EXISTS(SELECT 1 FROM public.knowledge_budget_policy p WHERE p.organization_id=org
          AND p.max_cost_usd=(policy->>'max_cost_usd')::numeric AND p.window_minutes=(policy->>'window_minutes')::integer
          AND p.reservation_seconds=(policy->>'reservation_seconds')::integer)) INTO result;
      RETURN result;
    END $$;
    REVOKE ALL ON FUNCTION access_coverage_manifest(uuid,jsonb) FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE FUNCTION require_access_coverage_admin(org uuid,writing boolean) RETURNS void
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF session_user<>'app_rw' OR org IS NULL OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Current coverage administrator required' USING ERRCODE='42501'; END IF;
      IF writing THEN
        PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||org::text,0));
        PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      ELSE PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0)); END IF;
      IF public.scope_actor_role(org) IS DISTINCT FROM 'OWNER_ADMIN' THEN RAISE EXCEPTION 'Current coverage administrator required' USING ERRCODE='42501'; END IF;
    END $$;
    REVOKE ALL ON FUNCTION require_access_coverage_admin(uuid,boolean) FROM PUBLIC,app_rw,app_worker,app_review;
    ''')
    op.execute('''
    CREATE FUNCTION capture_access_coverage(org uuid,request uuid,predecessor uuid,policy jsonb) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; data jsonb; digest text; row public.access_coverage_snapshot;
    BEGIN
      PERFORM public.require_access_coverage_admin(org,true);
      IF request IS NULL OR jsonb_typeof(policy) IS DISTINCT FROM 'object'
        OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(policy) k) IS DISTINCT FROM ARRAY['enabled','max_cost_usd','reservation_seconds','window_minutes']
        OR jsonb_typeof(policy->'enabled') IS DISTINCT FROM 'boolean'
        OR NOT COALESCE((policy->>'max_cost_usd')::numeric>0 AND (policy->>'max_cost_usd')::numeric::text NOT IN ('NaN','Infinity','-Infinity')
          AND (policy->>'window_minutes')::integer>0 AND (policy->>'reservation_seconds')::integer>0,false) THEN
        RAISE EXCEPTION 'Invalid coverage request' USING ERRCODE='22023'; END IF;
      digest:=encode(sha256(convert_to(jsonb_build_object('predecessor',predecessor,'policy',policy)::text,'UTF8')),'hex');
      SELECT * INTO row FROM public.access_coverage_snapshot WHERE organization_id=org AND generated_by=actor AND request_key=request;
      IF FOUND THEN
        IF row.request_digest<>digest THEN RAISE EXCEPTION 'Coverage request conflict' USING ERRCODE='23505'; END IF;
        RETURN (to_jsonb(row)-ARRAY['organization_id','request_key','request_digest','manifest'])||jsonb_build_object('activation_available',false);
      END IF;
      IF predecessor IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.access_coverage_snapshot WHERE organization_id=org AND id=predecessor) THEN
        RAISE EXCEPTION 'Coverage predecessor inaccessible' USING ERRCODE='42501'; END IF;
      data:=public.access_coverage_manifest(org,policy);
      INSERT INTO public.access_coverage_snapshot(organization_id,predecessor_id,generated_by,request_key,request_digest,manifest_digest,manifest,item_count,finding_count)
        VALUES(org,predecessor,actor,request,digest,encode(sha256(convert_to(data::text,'UTF8')),'hex'),data,jsonb_array_length(data->'items'),
          (SELECT count(*) FROM jsonb_array_elements(data->'items') item WHERE jsonb_array_length(item->'findings')>0)) RETURNING * INTO row;
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(org,actor,'access_coverage_snapshot',row.id,'access.coverage_captured',jsonb_build_object('manifest_digest',row.manifest_digest,'item_count',row.item_count,'finding_count',row.finding_count));
      RETURN (to_jsonb(row)-ARRAY['organization_id','request_key','request_digest','manifest'])||jsonb_build_object('activation_available',false);
    END $$;
    CREATE FUNCTION access_coverage_page(org uuid,snapshot uuid,after_kind text,after_id uuid,take integer) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE data jsonb; result jsonb;
    BEGIN
      PERFORM public.require_access_coverage_admin(org,false);
      IF take IS NULL OR take NOT BETWEEN 1 AND 201 OR (after_kind IS NULL)<>(after_id IS NULL) THEN RAISE EXCEPTION 'Invalid coverage page' USING ERRCODE='22023'; END IF;
      SELECT manifest INTO data FROM public.access_coverage_snapshot WHERE organization_id=org AND id=snapshot;
      IF NOT FOUND THEN RAISE EXCEPTION 'Coverage snapshot inaccessible' USING ERRCODE='42501'; END IF;
      SELECT COALESCE(jsonb_agg(item ORDER BY item->>'kind' COLLATE "C",(item->>'resource_id')::uuid),'[]') INTO result
      FROM (SELECT item FROM jsonb_array_elements(data->'items') item WHERE after_kind IS NULL OR
        (item->>'kind' COLLATE "C",(item->>'resource_id')::uuid)>(after_kind COLLATE "C",after_id)
        ORDER BY item->>'kind' COLLATE "C",(item->>'resource_id')::uuid LIMIT take) page;
      RETURN result;
    END $$;
    CREATE FUNCTION review_access_coverage(org uuid,snapshot uuid,request uuid,digest text,policy jsonb) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; source public.access_coverage_snapshot; receipt public.access_coverage_review; data jsonb;
    BEGIN
      PERFORM public.require_access_coverage_admin(org,true);
      IF request IS NULL OR digest IS NULL OR digest !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'Invalid coverage review' USING ERRCODE='22023'; END IF;
      SELECT * INTO receipt FROM public.access_coverage_review WHERE organization_id=org AND reviewed_by=actor AND request_key=request;
      IF FOUND THEN
        IF (receipt.snapshot_id,receipt.manifest_digest) IS DISTINCT FROM (snapshot,digest) THEN RAISE EXCEPTION 'Coverage review conflict' USING ERRCODE='23505'; END IF;
        RETURN (to_jsonb(receipt)-ARRAY['organization_id','request_key'])||jsonb_build_object('activation_available',false);
      END IF;
      SELECT * INTO source FROM public.access_coverage_snapshot WHERE organization_id=org AND id=snapshot;
      IF NOT FOUND THEN RAISE EXCEPTION 'Coverage snapshot inaccessible' USING ERRCODE='42501'; END IF;
      data:=public.access_coverage_manifest(org,policy);
      IF source.manifest_digest<>digest OR encode(sha256(convert_to(data::text,'UTF8')),'hex')<>digest THEN
        RAISE EXCEPTION 'Coverage snapshot is stale' USING ERRCODE='40001'; END IF;
      INSERT INTO public.access_coverage_review(organization_id,snapshot_id,manifest_digest,finding_count,reviewed_by,request_key)
        VALUES(org,snapshot,digest,source.finding_count,actor,request) RETURNING * INTO receipt;
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(org,actor,'access_coverage_snapshot',snapshot,'access.coverage_reviewed',jsonb_build_object('review_id',receipt.id,'manifest_digest',digest,'finding_count',source.finding_count));
      RETURN (to_jsonb(receipt)-ARRAY['organization_id','request_key'])||jsonb_build_object('activation_available',false);
    END $$;
    CREATE FUNCTION access_coverage_headers(org uuid,before_time timestamptz,before_id uuid,take integer) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE result jsonb;
    BEGIN
      PERFORM public.require_access_coverage_admin(org,false);
      IF take IS NULL OR take NOT BETWEEN 1 AND 201 OR (before_time IS NULL)<>(before_id IS NULL) THEN RAISE EXCEPTION 'Invalid coverage page' USING ERRCODE='22023'; END IF;
      SELECT COALESCE(jsonb_agg((to_jsonb(s)-ARRAY['organization_id','request_key','request_digest','manifest'])||
        jsonb_build_object('activation_available',false,'review',CASE WHEN r.id IS NULL THEN NULL ELSE
          (to_jsonb(r)-ARRAY['organization_id','request_key'])||jsonb_build_object('activation_available',false) END)
        ORDER BY s.generated_at DESC,s.id DESC),'[]') INTO result
      FROM (SELECT * FROM public.access_coverage_snapshot WHERE organization_id=org
        AND (before_time IS NULL OR (generated_at,id)<(before_time,before_id)) ORDER BY generated_at DESC,id DESC LIMIT take) s
      LEFT JOIN public.access_coverage_review r ON r.organization_id=s.organization_id AND r.snapshot_id=s.id;
      RETURN result;
    END $$;
    REVOKE ALL ON FUNCTION capture_access_coverage(uuid,uuid,uuid,jsonb),access_coverage_page(uuid,uuid,text,uuid,integer),review_access_coverage(uuid,uuid,uuid,text,jsonb) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION capture_access_coverage(uuid,uuid,uuid,jsonb),access_coverage_page(uuid,uuid,text,uuid,integer),review_access_coverage(uuid,uuid,uuid,text,jsonb) TO app_rw;
    REVOKE ALL ON FUNCTION access_coverage_headers(uuid,timestamptz,uuid,integer) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION access_coverage_headers(uuid,timestamptz,uuid,integer) TO app_rw;
    CREATE FUNCTION access_coverage_audit_access(org uuid,snapshot uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      RETURN public.scope_actor_role(org)='OWNER_ADMIN' AND EXISTS(SELECT 1 FROM public.access_coverage_snapshot s WHERE s.organization_id=org AND s.id=snapshot);
    END $$;
    REVOKE ALL ON FUNCTION access_coverage_audit_access(uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION access_coverage_audit_access(uuid,uuid) TO app_rw;
    ''')
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('property_source_audit_access(uuid,text,uuid)'::regprocedure)"))
    marker = "      IF public.property_source_security_mode(org)='PREPARATION'"
    assert definition.count(marker) == 1
    op.execute(definition.replace(marker,AUDIT_BRANCH+marker))


def downgrade():
    op.execute('LOCK TABLE access_coverage_snapshot,access_coverage_review IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM access_coverage_snapshot) THEN RAISE EXCEPTION 'Retain reviewed coverage snapshots; repair forward'; END IF; END $$")
    if op.get_bind().scalar(text("SELECT to_regprocedure('access_coverage_audit_access(uuid,uuid)')")) is not None:
        definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('property_source_audit_access(uuid,text,uuid)'::regprocedure)"))
        assert definition.count(AUDIT_BRANCH) == 1
        op.execute(definition.replace(AUDIT_BRANCH,''))
        op.execute('DROP FUNCTION access_coverage_audit_access(uuid,uuid)')
    op.execute('''DROP FUNCTION IF EXISTS access_coverage_headers(uuid,timestamptz,uuid,integer);
      DROP FUNCTION review_access_coverage(uuid,uuid,uuid,text,jsonb),capture_access_coverage(uuid,uuid,uuid,jsonb),access_coverage_page(uuid,uuid,text,uuid,integer);
      DROP FUNCTION require_access_coverage_admin(uuid,boolean),access_coverage_manifest(uuid,jsonb),access_coverage_grants(uuid,text,uuid,timestamptz);
      DROP TABLE access_coverage_review,access_coverage_snapshot;''')
