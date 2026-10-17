"""Materialize strict combined audiences without changing source-security mode."""
import hashlib
import importlib
import re
from alembic import op
from sqlalchemy import text

revision = '0087'
down_revision = '0086'
branch_labels = None
depends_on = None

EXPECTED = {
    'property_import_row_access': ('property_import_row_access(uuid,jsonb,text,boolean)', '83aabe75ad913c593db807622ceec89eed15319e40de57da28e8b72d82b15e6d'),
    'property_meeting_access': ('property_meeting_access(uuid,text,uuid,text)', 'b021b38a7f280b1cf927b72889d3405ba5870fe037828ba4cbba824fca92369a'),
    'property_meeting_property_access': ('property_meeting_property_access(uuid,uuid,text)', '81c0a9df93cf6d1d7bdbbcc2cffdfddbf1d2bd10d88aeb25e7519cb4b542b9c8'),
    'property_meeting_row_access': ('property_meeting_row_access(uuid,text,jsonb,text)', '5d095a6ddbde6f26ce8de5b5358ab30afdbe5c93710b4f116a1c14c812622e1e'),
    'property_original_row_access': ('property_original_row_access(uuid,uuid,uuid,uuid,text)', 'ffb16d635f48fe7830c03eb5df1b65a57f1ebb926d9adc97bb970c273892e466'),
    'scope_financial_inputs_classified': ('scope_financial_inputs_classified(uuid,uuid)', 'b76a11f6e4c17641afa9b06fb7e3855b2ff8b0587e2f26efa565756452650b29'),
}
PRIOR = importlib.import_module('migrations.versions.0086_combined_authority_projection')
EXPLICIT_ACTOR = set()
PROPERTY = PRIOR.PROPERTY
ROUTED = {**PRIOR.EXPECTED, **EXPECTED}
ACTOR = PRIOR.ACTOR
MODE = PRIOR.MODE
VALID = PRIOR.VALID
GUARD = PRIOR.GUARD
SQL_PREFIX = PRIOR.SQL_PREFIX
SQL_SUFFIX = PRIOR.SQL_SUFFIX

def _parts(definition):
    header,body,end = definition.split('$function$')
    assert end.strip() == ''
    match = re.search(r'FUNCTION public\.([a-z_]+)\(([^\n]*)\)\n',header)
    assert match
    return header,body,match


def _calls(body,forward):
    names = set(ROUTED) | set(PROPERTY)
    pattern = re.compile(r'public\.('+'|'.join(sorted(names))+')'+('' if forward else '_at_mode')+r'\(')
    for match in reversed(list(pattern.finditer(body))):
        pos = match.end(); depth = 1; quoted = False
        while depth:
            char = body[pos]
            if char == "'":
                if quoted and pos+1 < len(body) and body[pos+1] == "'":
                    pos += 2
                    continue
                quoted = not quoted
            if not quoted:
                if char == '(': depth += 1
                elif char == ')': depth -= 1
            pos += 1
        name = match.group(1)
        args = body[match.end():pos-1]
        extra = ',security_mode' if name in PROPERTY or name in PRIOR.EXPLICIT_ACTOR else ',evaluation_actor,security_mode'
        if forward:
            args += extra
        else:
            assert args.endswith(extra)
            args = args[:-len(extra)]
        body = body[:match.start()]+'public.'+name+('_at_mode' if forward else '')+'('+args+')'+body[pos:]
    return body


def _transform(definition,forward):
    header,body,match = _parts(definition)
    name = match.group(1) if forward else match.group(1).removesuffix('_at_mode')
    extra = ', evaluation_actor uuid DEFAULT NULL::uuid, security_mode text DEFAULT NULL::text' if name == 'property_import_row_access' else ', evaluation_actor uuid, security_mode text'
    if forward:
        assert hashlib.sha256(body.encode()).hexdigest() == EXPECTED[name][1]
        assert 'evaluation_actor' not in body and 'security_mode' not in body.replace(MODE,'')
        body = _calls(body,True).replace(MODE,'security_mode')
        body = body.replace('public.scope_actor_role(org)','public.property_source_actor_role(org,evaluation_actor)')
        body = body.replace(ACTOR,'evaluation_actor')
        if 'LANGUAGE plpgsql' in header:
            assert body.count('    BEGIN\n') == 1
            body = body.replace('    BEGIN\n','    BEGIN\n'+GUARD,1)
        else:
            assert 'LANGUAGE sql' in header
            body = SQL_PREFIX+body+SQL_SUFFIX
        args = match.group(2)+extra
    else:
        assert match.group(2).endswith(extra)
        args = match.group(2)[:-len(extra)]
        if 'LANGUAGE plpgsql' in header:
            assert body.count(GUARD) == 1
            body = body.replace(GUARD,'')
        else:
            assert body.startswith(SQL_PREFIX) and body.endswith(SQL_SUFFIX)
            body = body[len(SQL_PREFIX):-len(SQL_SUFFIX)]
        body = _calls(body,False).replace('security_mode',MODE)
        body = body.replace('public.property_source_actor_role(org,evaluation_actor)','public.scope_actor_role(org)')
        body = body.replace('evaluation_actor',ACTOR)
        assert hashlib.sha256(body.encode()).hexdigest() == EXPECTED[name][1]
    header = header[:match.start(1)]+name+('_at_mode' if forward else '')+'('+args+')\n'+header[match.end():]
    return header+'$function$'+body+'$function$\n'


def _internal(name,signature):
    return signature.replace(name+'(',name+'_at_mode(')[:-1]+',uuid,text)'


def _upgrade_predicates():
    db = op.get_bind()
    definitions = {name:db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':signature})
        for name,(signature,_) in EXPECTED.items()}
    for name,definition in definitions.items():
        assert db.scalar(text('SELECT pg_get_userbyid(proowner)=current_user FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'),{'signature':EXPECTED[name][0]})
        evaluator = _transform(definition,True)
        assert _transform(evaluator,False) == definition
        op.execute(evaluator)
        op.execute(f'REVOKE ALL ON FUNCTION {_internal(name,EXPECTED[name][0])} FROM PUBLIC,app_rw,app_worker,app_review')
    for name,definition in definitions.items():
        header,_,match = _parts(definition)
        args = ','.join(arg.strip().split(' ')[0] for arg in match.group(2).split(','))
        extra = '' if name in EXPLICIT_ACTOR else ','+ACTOR
        mode = MODE.replace('(org)','((source).organization_id)') if name == 'invoice_source_row_access' else MODE
        header = header.replace('LANGUAGE plpgsql','LANGUAGE sql')
        op.execute(header+f'$function$ SELECT public.{name}_at_mode({args}{extra},{mode}) $function$')


def _downgrade_predicates():
    db = op.get_bind()
    for name,(signature,_) in EXPECTED.items():
        definition = db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':_internal(name,signature)})
        op.execute(_transform(definition,False))
    for name,(signature,_) in reversed(list(EXPECTED.items())):
        op.execute(f'DROP FUNCTION {_internal(name,signature)}')

# Exact table allowlist for private row loading; opaque projections never return
# the loaded row. Financial descendants recurse only to a known parent family.
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
DERIVED = (
    'declaration_import_document','declaration_queue_request','declaration_extraction_run','declaration_import_pass',
    'declaration_candidate','declaration_candidate_evidence','declaration_candidate_issue','property_legal_reference',
    'property_assignment','property_document_relation','property_rule','property_rule_relation','property_rule_source',
    'approved_fact_source','property_knowledge_query_run',
)


def _array(values):
    return 'ARRAY['+','.join("'"+v+"'" for v in values)+']'


def upgrade():
    _upgrade_predicates()
    op.execute(f'''
    CREATE FUNCTION access_coverage_actor_access(org uuid,actor uuid,kind text,resource uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE context jsonb; parent uuid; property uuid; sphere uuid; profile uuid; profile_kind text; document jsonb;
    BEGIN
      IF org IS NULL OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR actor IS NULL OR resource IS NULL OR kind IS NULL OR NOT kind=ANY({_array(KINDS)})
        OR capability IS NULL OR capability NOT IN ('read','prepare','confirm','approve','send','export')
        OR public.property_source_actor_role(org,actor) IS NULL THEN RETURN false; END IF;
      EXECUTE format('SELECT to_jsonb(r) FROM public.%I r WHERE r.organization_id=$1 AND r.id=$2',kind)
        INTO context USING org,resource;
      IF context IS NULL THEN RETURN false; END IF;
      property:=(context->>'property_id')::uuid;
      IF kind='property' THEN
        RETURN public.property_master_access_at_mode(org,resource,capability,actor,'ENFORCED');
      ELSIF kind='unit' THEN
        RETURN public.property_source_actor_permitted_at_mode(org,actor,kind,resource,capability,'ENFORCED')
          AND public.property_master_access_at_mode(org,property,'read',actor,'ENFORCED');
      ELSIF kind='person' THEN
        RETURN public.person_source_access_at_mode(org,resource,capability,actor,'ENFORCED');
      ELSIF kind IN ('owner_profile','tenant_profile','vendor') THEN
        RETURN public.profile_source_access_at_mode(org,kind,resource,capability,actor,'ENFORCED');
      ELSIF kind IN ('lease_agreement','ownership_history') THEN
        profile_kind:=CASE kind WHEN 'lease_agreement' THEN 'tenant_profile' ELSE 'owner_profile' END;
        profile:=(context->>(profile_kind||'_id'))::uuid;
        RETURN public.property_history_row_access_at_mode(org,kind,context,capability,actor,'ENFORCED')
          AND public.profile_source_access_at_mode(org,profile_kind,profile,'read',actor,'ENFORCED')
          AND (capability<>'export' OR public.profile_source_access_at_mode(org,profile_kind,profile,'export',actor,'ENFORCED'));
      ELSIF kind='property_document' THEN
        RETURN public.property_original_row_access_at_mode(org,resource,property,(context->>'uploaded_by')::uuid,capability,actor,'ENFORCED');
      ELSIF kind='declaration_import' THEN
        RETURN public.property_import_row_access_at_mode(org,context,capability,false,actor,'ENFORCED');
      ELSIF kind=ANY({_array(DERIVED)}) THEN
        RETURN public.property_derived_row_access_at_mode(org,kind,context,capability,actor,'ENFORCED');
      ELSIF kind IN ('owners_meeting','agenda_item','resolution','resolution_register_entry') THEN
        RETURN public.property_meeting_row_access_at_mode(org,kind,context,capability,actor,'ENFORCED');
      ELSIF kind='management_mandate' THEN
        -- Organizational mandate metadata has no separate source audience.
        -- This reports its read gate only, not authorization to mutate a mandate.
        RETURN capability='read';
      ELSIF kind='accounting_scope' THEN
        IF context->>'scope_type'='SEV' THEN RETURN false; END IF;
        RETURN public.has_scope_capability_at_mode(org,resource,capability,actor,'ENFORCED')
          AND public.scope_financial_inputs_classified_at_mode(org,resource,actor,'ENFORCED')
          AND public.report_scope_source_access_at_mode(org,resource,
            CASE WHEN capability IN ('read','export') THEN capability ELSE 'prepare' END,actor,'ENFORCED');
      ELSIF kind IN ('bank_account','payment','cost_entry','settlement_run','economic_plan') THEN
        sphere:=public.financial_scope_id(org,kind,resource);
        IF sphere IS NULL OR NOT EXISTS(SELECT 1 FROM public.accounting_scope s
          WHERE s.organization_id=org AND s.id=sphere AND s.property_id=property AND s.scope_type IN ('MIET','WEG')) THEN RETURN false; END IF;
        RETURN public.financial_scope_access_at_mode(org,kind,resource,capability,actor,'ENFORCED')
          AND public.report_scope_source_access_at_mode(org,sphere,
            CASE WHEN capability IN ('read','export') THEN capability ELSE 'prepare' END,actor,'ENFORCED');
      ELSIF kind IN ('settlement_line','economic_plan_line') THEN
        IF kind='settlement_line' THEN parent:=(context->>'settlement_run_id')::uuid;
          RETURN public.access_coverage_actor_access(org,actor,'settlement_run',parent,capability);
        ELSE parent:=(context->>'economic_plan_id')::uuid;
          RETURN public.access_coverage_actor_access(org,actor,'economic_plan',parent,capability); END IF;
      ELSIF kind='coverage_declaration' THEN
        IF (context->>'settlement_run_id' IS NULL)=(context->>'economic_plan_id' IS NULL) THEN RETURN false; END IF;
        IF property IS NULL OR NOT (EXISTS(SELECT 1 FROM public.settlement_run r WHERE r.organization_id=org
          AND r.id=(context->>'settlement_run_id')::uuid AND r.property_id=property) OR EXISTS(SELECT 1 FROM public.economic_plan p
          WHERE p.organization_id=org AND p.id=(context->>'economic_plan_id')::uuid AND p.property_id=property)) THEN RETURN false; END IF;
        IF context->>'settlement_run_id' IS NOT NULL THEN
          IF NOT public.access_coverage_actor_access(org,actor,'settlement_run',(context->>'settlement_run_id')::uuid,capability) THEN RETURN false; END IF;
        ELSIF context->>'economic_plan_id' IS NOT NULL THEN
          IF NOT public.access_coverage_actor_access(org,actor,'economic_plan',(context->>'economic_plan_id')::uuid,capability) THEN RETURN false; END IF;
        ELSE RETURN false; END IF;
        IF jsonb_typeof(context->'source_document_ids') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
        FOR document IN SELECT value FROM jsonb_array_elements(context->'source_document_ids') LOOP
          IF jsonb_typeof(document) IS DISTINCT FROM 'string' OR document#>>'{{}}' !~* '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
            THEN RETURN false; END IF;
          IF NOT public.access_coverage_actor_access(org,actor,'invoice_document',(document#>>'{{}}')::uuid,
            CASE WHEN capability='export' THEN 'export' ELSE 'read' END) THEN RETURN false; END IF;
        END LOOP;
        RETURN true;
      ELSIF kind='invoice_document' THEN
        IF NOT public.invoice_source_access_at_mode(org,resource,capability,actor,'ENFORCED') THEN RETURN false; END IF;
        parent:=(context->>'cost_entry_id')::uuid;
        RETURN parent IS NULL OR public.access_coverage_actor_access(org,actor,'cost_entry',parent,capability);
      ELSIF kind='invoice_processing_attempt' THEN
        RETURN public.access_coverage_actor_access(org,actor,'invoice_document',(context->>'document_id')::uuid,capability);
      ELSIF kind='mandate_document' THEN
        sphere:=(context->>'accounting_scope_id')::uuid;
        RETURN public.mandate_source_access_at_mode(org,sphere,capability,actor,'ENFORCED')
          AND public.access_coverage_actor_access(org,actor,'accounting_scope',sphere,capability);
      ELSIF kind='dunning_notice' THEN
        sphere:=public.financial_scope_id(org,kind,resource);
        RETURN sphere IS NOT NULL AND public.financial_scope_access_at_mode(org,kind,resource,capability,actor,'ENFORCED')
          AND public.dunning_source_access_at_mode(org,resource,CASE WHEN capability IN ('read','export') THEN capability ELSE 'prepare' END,actor,'ENFORCED')
          AND public.dunning_contact_access_at_mode(org,(context->>'tenant_profile_id')::uuid,(context->>'owner_profile_id')::uuid,
            CASE WHEN capability='export' THEN 'export' WHEN capability='read' THEN 'read' ELSE 'prepare' END,actor,'ENFORCED');
      END IF;
      RETURN false;
    END $$;
    REVOKE ALL ON FUNCTION access_coverage_actor_access(uuid,uuid,text,uuid,text) FROM PUBLIC,app_rw,app_worker,app_review;
    ''')
    op.execute('''
    CREATE FUNCTION access_coverage_strict_audience(org uuid,kind text,resource uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE result jsonb;
    BEGIN
      SELECT COALESCE(jsonb_agg(jsonb_build_object('user_id',id,'capabilities',caps) ORDER BY id),'[]') INTO result
      FROM (SELECT u.id,(SELECT COALESCE(jsonb_agg(cap ORDER BY cap),'[]') FROM
          unnest(ARRAY['read','prepare','confirm','approve','send','export']) cap
          WHERE public.access_coverage_actor_access(org,u.id,kind,resource,cap)) caps
        FROM public."user" u JOIN public.membership m ON m.organization_id=u.organization_id AND m.user_id=u.id
        WHERE u.organization_id=org AND u.is_active) principals WHERE jsonb_array_length(caps)>0;
      RETURN result;
    END $$;
    REVOKE ALL ON FUNCTION access_coverage_strict_audience(uuid,text,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    ALTER FUNCTION access_coverage_manifest(uuid,jsonb) RENAME TO access_coverage_manifest_v1;
    ''')
    op.execute(f'''
    CREATE FUNCTION access_coverage_manifest(org uuid,policy jsonb) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE data jsonb; item jsonb; result jsonb:='[]'; audience jsonb; findings jsonb; context jsonb; kind text; resource uuid;
    BEGIN
      IF org IS NULL OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR NULLIF(current_setting('app.current_scope_id',true),'') IS NOT NULL THEN
        RAISE EXCEPTION 'Coverage requires an organization-wide context' USING ERRCODE='22023'; END IF;
      data:=public.access_coverage_manifest_v1(org,policy);
      FOR item IN SELECT value FROM jsonb_array_elements(data->'items') LOOP
        kind:=item->>'kind'; resource:=(item->>'resource_id')::uuid;
        IF kind=ANY({_array(KINDS)}) THEN
          audience:=public.access_coverage_strict_audience(org,kind,resource);
          item:=jsonb_set(item,'{{references,strict_audience}}',audience,true);
          SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]') INTO findings FROM jsonb_array_elements_text(item->'findings') value
            WHERE value NOT IN ('PARENT_CONTEXT_REVIEW_REQUIRED','RECIPIENT_ACCESS_REVIEW_REQUIRED','DERIVED_AUDIENCE_REVIEW_REQUIRED',
              'SOURCE_AUDIENCE_REVIEW_REQUIRED','IMPORT_AUDIENCE_REVIEW_REQUIRED');
          IF NOT jsonb_path_exists(audience,'$[*].capabilities[*] ? (@ == "read")')
            AND NOT findings @> '["REVIEWED_QUARANTINE"]'::jsonb THEN findings:=findings||'["COMBINED_READER_REQUIRED"]'::jsonb; END IF;
          IF kind='invoice_processing_attempt' THEN
            SELECT to_jsonb(r) INTO context FROM public.invoice_processing_attempt r WHERE r.organization_id=org AND r.id=resource;
            IF context->>'status' IN ('queued','running') AND
              ((context->>'initiated_by')::uuid IS NULL OR
               NOT public.access_coverage_actor_access(org,(context->>'initiated_by')::uuid,'invoice_document',(context->>'document_id')::uuid,'prepare') OR
               ((context->>'requested_scope_id')::uuid IS NOT NULL AND (context->>'requested_scope_id')::uuid IS DISTINCT FROM
                 public.financial_scope_id(org,'invoice_document',(context->>'document_id')::uuid))) THEN
              findings:=findings||'["PROCESSING_PRINCIPAL_DENIED"]'::jsonb; END IF;
          END IF;
          item:=jsonb_set(item,'{{findings}}',findings);
        ELSIF kind='knowledge_budget_policy' THEN
          -- Supplied API configuration is not yet a trusted deployment attestation.
          item:=jsonb_set(item,'{{findings}}',(item->'findings')||'["DEPLOYMENT_POLICY_ATTESTATION_REQUIRED"]'::jsonb);
        END IF;
        result:=result||jsonb_build_array(item);
      END LOOP;
      data:=jsonb_set(jsonb_set(data,'{{items}}',result),'{{checker_version}}','2');
      data:=jsonb_set(data,'{{dependencies}}',(data->'dependencies')||jsonb_build_array(jsonb_build_object(
        'kind','scope_control_event','digest',(SELECT encode(sha256(convert_to(COALESCE(string_agg(to_jsonb(e)::text,'' ORDER BY sequence),''),'UTF8')),'hex')
          FROM public.scope_control_event e WHERE e.organization_id=org))));
      RETURN data;
    END $$;
    REVOKE ALL ON FUNCTION access_coverage_manifest(uuid,jsonb) FROM PUBLIC,app_rw,app_worker,app_review;
    ALTER TABLE access_coverage_snapshot DROP CONSTRAINT access_coverage_snapshot_checker_version_check;
    ALTER TABLE access_coverage_snapshot ADD CHECK(checker_version IN (1,2));
    ALTER TABLE access_coverage_snapshot ALTER COLUMN checker_version SET DEFAULT 2;
    ''')


def downgrade():
    op.execute('LOCK TABLE access_coverage_snapshot IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM access_coverage_snapshot WHERE checker_version=2) THEN RAISE EXCEPTION 'Retain projected coverage snapshots; repair forward'; END IF; END $$")
    op.execute('''DROP FUNCTION access_coverage_manifest(uuid,jsonb);
      ALTER FUNCTION access_coverage_manifest_v1(uuid,jsonb) RENAME TO access_coverage_manifest;
      DROP FUNCTION access_coverage_strict_audience(uuid,text,uuid),access_coverage_actor_access(uuid,uuid,text,uuid,text);
      ALTER TABLE access_coverage_snapshot DROP CONSTRAINT access_coverage_snapshot_checker_version_check;
      ALTER TABLE access_coverage_snapshot ADD CHECK(checker_version=1);
      ALTER TABLE access_coverage_snapshot ALTER COLUMN checker_version SET DEFAULT 1;''')
    _downgrade_predicates()
