"""Apply source audiences to review children, canonical facts and stored Q&A."""
from alembic import op

revision = '0062'
down_revision = '0061'
branch_labels = None
depends_on = None

CHILDREN = ('declaration_import_document','declaration_candidate','declaration_candidate_evidence',
            'declaration_candidate_issue','declaration_extraction_run','declaration_queue_request','declaration_import_pass')
CANONICAL = ('approved_fact_source','property_legal_reference','property_assignment','property_rule',
             'property_rule_source','property_rule_relation','property_document_relation')
TABLES = CHILDREN + CANONICAL + ('property_knowledge_query_run',)
CONTEXT = {table: ('import_id',) for table in CHILDREN}
CONTEXT.update({table: ('property_id','source_import_id') for table in CANONICAL})
CONTEXT.update({
    'declaration_import_pass': ('declaration_extraction_run_id',),
    'approved_fact_source': ('property_id','source_import_id','property_document_id','subject_kind','subject_id'),
    'property_assignment': ('property_id','source_import_id','unit_id'),
    'property_rule_source': ('property_id','property_rule_id','property_document_id'),
    'property_rule_relation': ('property_id','source_import_id','source_rule_id','target_rule_id'),
    'property_document_relation': ('property_id','source_import_id','source_document_id','target_document_id'),
    'property_knowledge_query_run': ('property_id',),
})


def upgrade():
    op.execute('LOCK TABLE property_source_security,'+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""
    CREATE FUNCTION property_import_runtime_access(org uuid,job uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text;
    BEGIN
      IF job IS NULL OR capability IS NULL OR capability NOT IN ('read','prepare','approve','export') THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      RETURN public.property_source_permitted(org,'declaration_import',job,capability);
    END $$;

    CREATE FUNCTION property_derived_row_access(org uuid,kind text,context jsonb,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      property uuid; job uuid; document uuid; other uuid; rule uuid; subject uuid; subject_kind text; source record;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','prepare','approve','export')
        OR context->>'organization_id' IS DISTINCT FROM org::text OR context->>'id' IS NULL
        OR kind IS NULL OR kind NOT IN ('declaration_import_document','declaration_candidate','declaration_candidate_evidence',
          'declaration_candidate_issue','declaration_extraction_run','declaration_queue_request','declaration_import_pass',
          'approved_fact_source','property_legal_reference','property_assignment','property_rule','property_rule_source',
          'property_rule_relation','property_document_relation','property_knowledge_query_run') THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      IF public.property_source_actor_role(org,actor) IS NULL THEN RETURN false; END IF;
      IF kind IN ('declaration_import_document','declaration_candidate','declaration_candidate_evidence',
          'declaration_candidate_issue','declaration_extraction_run','declaration_queue_request') THEN
        RETURN public.property_import_runtime_access(org,(context->>'import_id')::uuid,capability);
      ELSIF kind='declaration_import_pass' THEN
        SELECT r.import_id INTO job FROM public.declaration_extraction_run r
          WHERE r.organization_id=org AND r.id=(context->>'declaration_extraction_run_id')::uuid;
        RETURN public.property_import_runtime_access(org,job,capability);
      END IF;
      property:=(context->>'property_id')::uuid;
      IF kind='property_knowledge_query_run' THEN
        -- Stored answers disclose the complete source graph even while effects
        -- are paused. Existing trusted budget/fencing commands still own writes.
        RETURN capability='read' AND public.property_complete_graph_actor_access(org,actor,property,'read',false);
      END IF;
      IF NOT public.property_source_actor_permitted(org,actor,'property',property,capability)
        OR NOT public.property_complete_graph_actor_access(org,actor,property,'read',false) THEN RETURN false; END IF;
      job:=(context->>'source_import_id')::uuid;
      document:=(context->>'property_document_id')::uuid;
      IF kind='approved_fact_source' THEN
        subject:=(context->>'subject_id')::uuid; subject_kind:=context->>'subject_kind';
        IF ((subject_kind='PROPERTY' AND subject=property)
          OR (subject_kind='UNIT' AND EXISTS(SELECT 1 FROM public.unit s WHERE s.organization_id=org AND s.property_id=property AND s.id=subject))
          OR (subject_kind='RULE' AND EXISTS(SELECT 1 FROM public.property_rule s WHERE s.organization_id=org AND s.property_id=property AND s.id=subject))
          OR (subject_kind='LEGAL_REFERENCE' AND EXISTS(SELECT 1 FROM public.property_legal_reference s WHERE s.organization_id=org AND s.property_id=property AND s.id=subject))
          OR (subject_kind='ASSIGNMENT' AND EXISTS(SELECT 1 FROM public.property_assignment s WHERE s.organization_id=org AND s.property_id=property AND s.id=subject))
          OR (subject_kind='DOCUMENT_RELATION' AND EXISTS(SELECT 1 FROM public.property_document_relation s WHERE s.organization_id=org AND s.property_id=property AND s.id=subject))) IS NOT TRUE THEN RETURN false; END IF;
        IF subject_kind IN ('RULE','LEGAL_REFERENCE','ASSIGNMENT','DOCUMENT_RELATION') AND NOT public.property_source_actor_permitted(org,actor,
          CASE subject_kind WHEN 'RULE' THEN 'property_rule' WHEN 'LEGAL_REFERENCE' THEN 'property_legal_reference'
            WHEN 'ASSIGNMENT' THEN 'property_assignment' ELSE 'property_document_relation' END,subject,capability) THEN RETURN false; END IF;
      END IF;
      IF kind='property_assignment' AND context->>'unit_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.unit u
        WHERE u.organization_id=org AND u.property_id=property AND u.id=(context->>'unit_id')::uuid) THEN RETURN false; END IF;
      IF kind='property_rule' THEN
        FOR source IN SELECT s.property_document_id FROM public.property_rule_source s
          WHERE s.organization_id=org AND s.property_rule_id=(context->>'id')::uuid
        LOOP
          IF NOT public.property_source_actor_permitted(org,actor,'property_document',source.property_document_id,capability) THEN RETURN false; END IF;
        END LOOP;
      ELSIF kind='property_rule_source' THEN
        rule:=(context->>'property_rule_id')::uuid;
        IF NOT public.property_source_actor_permitted(org,actor,'property_rule',rule,capability) THEN RETURN false; END IF;
        SELECT r.source_import_id INTO job FROM public.property_rule r WHERE r.organization_id=org AND r.property_id=property AND r.id=rule;
        IF job IS NULL THEN RETURN false; END IF;
      ELSIF kind='property_rule_relation' THEN
        IF NOT public.property_source_actor_permitted(org,actor,'property_rule',(context->>'source_rule_id')::uuid,capability)
          OR NOT public.property_source_actor_permitted(org,actor,'property_rule',(context->>'target_rule_id')::uuid,capability) THEN RETURN false; END IF;
      ELSIF kind='property_document_relation' THEN
        document:=(context->>'source_document_id')::uuid; other:=(context->>'target_document_id')::uuid;
        IF NOT public.property_source_actor_permitted(org,actor,'property_document',other,capability) THEN RETURN false; END IF;
      END IF;
      IF job IS NULL OR NOT EXISTS(SELECT 1 FROM public.declaration_import i WHERE i.organization_id=org AND i.id=job
        AND COALESCE(i.applied_property_id,i.target_property_id)=property
        AND (capability IN ('read','export') OR i.status='APPLIED'))
        OR NOT public.property_import_runtime_access(org,job,capability) THEN RETURN false; END IF;
      IF document IS NOT NULL AND NOT public.property_source_actor_permitted(org,actor,'property_document',document,capability) THEN RETURN false; END IF;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION property_import_runtime_access(uuid,uuid,text),property_derived_row_access(uuid,text,jsonb,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_import_runtime_access(uuid,uuid,text),property_derived_row_access(uuid,text,jsonb,text) TO app_rw;
    """)
    for table in TABLES:
        fields=('organization_id','id')+CONTEXT[table]
        context='jsonb_build_object('+','.join(f"'{field}',{field}" for field in fields)+')'
        predicate=f"property_derived_row_access(organization_id,'{table}',{context},'read')"
        op.execute(f'CREATE POLICY derived_source_read ON {table} AS RESTRICTIVE FOR SELECT USING ({predicate})')
        if table=='property_knowledge_query_run':
            continue
        capability='approve' if table in CANONICAL else 'prepare'
        predicate=f"property_derived_row_access(organization_id,'{table}',{context},'{capability}')"
        op.execute(f'CREATE POLICY derived_source_insert ON {table} AS RESTRICTIVE FOR INSERT WITH CHECK ({predicate})')
        op.execute(f'CREATE POLICY derived_source_update ON {table} AS RESTRICTIVE FOR UPDATE USING ({predicate}) WITH CHECK ({predicate})')
        op.execute(f'CREATE POLICY derived_source_delete ON {table} AS RESTRICTIVE FOR DELETE USING ({predicate})')
        op.execute(f'CREATE TRIGGER a_lock_derived_source BEFORE INSERT OR UPDATE OR DELETE ON {table} '
                   'FOR EACH STATEMENT EXECUTE FUNCTION lock_strict_property_source_binding()')


def downgrade():
    op.execute('LOCK TABLE property_source_security,'+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain strict derived source authority'; END IF; END $$""")
    for table in TABLES:
        op.execute(f'DROP POLICY derived_source_read ON {table}')
        if table=='property_knowledge_query_run':
            continue
        for action in ('insert','update','delete'):
            op.execute(f'DROP POLICY derived_source_{action} ON {table}')
        op.execute(f'DROP TRIGGER a_lock_derived_source ON {table}')
    op.execute('DROP FUNCTION property_import_runtime_access(uuid,uuid,text),property_derived_row_access(uuid,text,jsonb,text)')
