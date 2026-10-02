"""Restrict copied financial result reads and export by current source authority.

Revision ID: 0060
Revises: 0059
"""
from alembic import op

revision = '0060'
down_revision = '0059'
branch_labels = None
depends_on = None

PREVIOUS_QUERY_ACCESS = """    CREATE OR REPLACE FUNCTION knowledge_query_actor_access(org uuid,actor uuid,property uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; source record;
    BEGIN
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL OR public.property_source_actor_role(org,actor) IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN EXISTS(SELECT 1 FROM public.property p WHERE p.organization_id=org AND p.id=property); END IF;
      IF mode='PAUSED' OR NOT public.property_source_actor_access(org,actor,'property',property,'read') THEN RETURN false; END IF;
      -- Enumerate the expected graph with the trusted definer. Ordinary RLS
      -- filtered hits cannot establish completeness, including historical rows.
      FOR source IN
        SELECT 'property_document'::text kind,d.id FROM public.property_document d WHERE d.organization_id=org AND d.property_id=property
        UNION ALL SELECT 'approved_fact_source',s.id FROM public.approved_fact_source s WHERE s.organization_id=org AND s.property_id=property
        UNION ALL SELECT 'property_rule',s.id FROM public.property_rule s WHERE s.organization_id=org AND s.property_id=property
        UNION ALL SELECT 'property_rule_source',s.id FROM public.property_rule_source s WHERE s.organization_id=org AND s.property_id=property
        UNION ALL SELECT 'property_rule_relation',s.id FROM public.property_rule_relation s WHERE s.organization_id=org AND s.property_id=property
        UNION ALL SELECT 'property_document_relation',s.id FROM public.property_document_relation s WHERE s.organization_id=org AND s.property_id=property
        UNION ALL SELECT 'property_legal_reference',s.id FROM public.property_legal_reference s WHERE s.organization_id=org AND s.property_id=property
        UNION ALL SELECT 'property_assignment',s.id FROM public.property_assignment s WHERE s.organization_id=org AND s.property_id=property
        UNION ALL SELECT 'declaration_import',i.id FROM public.declaration_import i WHERE i.organization_id=org AND i.applied_property_id=property
      LOOP
        IF NOT public.property_source_actor_access(org,actor,source.kind,source.id,'read') THEN RETURN false; END IF;
      END LOOP;
      RETURN true;
    END $$;
"""


TABLES = ('settlement_run','settlement_line','economic_plan','economic_plan_line','coverage_declaration')


def upgrade():
    op.execute('LOCK TABLE property,unit,property_source_security,' + ','.join(TABLES) + ' IN ACCESS EXCLUSIVE MODE NOWAIT')
    # Share the expected graph with effect and disclosure consumers. An export
    # checks every source edge's export capability; PAUSED only rejects effects.
    op.execute(PREVIOUS_QUERY_ACCESS.replace(
        'knowledge_query_actor_access(org uuid,actor uuid,property uuid)',
        'property_complete_graph_actor_access(org uuid,actor uuid,property uuid,capability text,for_effect boolean)'
    ).replace(
        "      mode:=public.property_source_security_mode(org);",
        "      IF capability IS NULL OR capability NOT IN ('read','export') OR for_effect IS NULL THEN RETURN false; END IF;\n"
        "      mode:=public.property_source_security_mode(org);"
    ).replace("IF mode='PAUSED' OR", "IF (mode='PAUSED' AND for_effect) OR").replace(
        "'property',property,'read'", "'property',property,capability"
    ).replace("source.kind,source.id,'read'", "source.kind,source.id,capability").replace(
        "SELECT 'property_document'::text kind,d.id",
        "SELECT 'unit'::text kind,u.id FROM public.unit u WHERE u.organization_id=org AND u.property_id=property\n"
        "        UNION ALL SELECT 'property_document'::text kind,d.id"
    ))
    op.execute("""
    REVOKE ALL ON FUNCTION property_complete_graph_actor_access(uuid,uuid,uuid,text,boolean) FROM PUBLIC,app_rw;
    CREATE OR REPLACE FUNCTION knowledge_query_actor_access(org uuid,actor uuid,property uuid) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT public.property_complete_graph_actor_access(org,actor,property,'read',true)
    $$;

    CREATE FUNCTION property_financial_disclosure_access(org uuid,property uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','export') THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      RETURN public.property_complete_graph_actor_access(org,NULLIF(current_setting('app.current_user_id',true),'')::uuid,property,capability,false);
    END $$;
    CREATE FUNCTION financial_parent_source_access(org uuid,kind text,parent uuid,declared_property uuid DEFAULT NULL) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE target uuid; mode text;
    BEGIN
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      IF kind='settlement_run' THEN
        SELECT r.property_id INTO target FROM public.settlement_run r WHERE r.organization_id=org AND r.id=parent;
      ELSIF kind='economic_plan' THEN
        SELECT p.property_id INTO target FROM public.economic_plan p WHERE p.organization_id=org AND p.id=parent;
      ELSE RETURN false; END IF;
      IF target IS NULL OR (declared_property IS NOT NULL AND declared_property IS DISTINCT FROM target) THEN RETURN false; END IF;
      RETURN public.property_financial_disclosure_access(org,target,'read');
    END $$;
    REVOKE ALL ON FUNCTION property_financial_disclosure_access(uuid,uuid,text),financial_parent_source_access(uuid,text,uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_financial_disclosure_access(uuid,uuid,text),financial_parent_source_access(uuid,text,uuid,uuid) TO app_rw;
    """)
    for table in ('settlement_run','economic_plan'):
        op.execute(f"CREATE POLICY financial_source_disclosure ON {table} AS RESTRICTIVE FOR SELECT USING "
                   "((status='draft' AND snapshot_payload IS NULL) OR property_financial_disclosure_access(organization_id,property_id,'read'))")
    for table, parent in (('settlement_line','settlement_run'),('economic_plan_line','economic_plan')):
        op.execute(f"CREATE POLICY financial_source_disclosure ON {table} AS RESTRICTIVE FOR SELECT USING "
                   f"(financial_parent_source_access(organization_id,'{parent}',{parent}_id))")
    op.execute("""CREATE POLICY financial_source_disclosure ON coverage_declaration AS RESTRICTIVE FOR SELECT USING
      (CASE WHEN settlement_run_id IS NOT NULL THEN financial_parent_source_access(organization_id,'settlement_run',settlement_run_id,property_id)
        ELSE financial_parent_source_access(organization_id,'economic_plan',economic_plan_id,property_id) END)
    """)


def downgrade():
    op.execute('LOCK TABLE property_source_security,' + ','.join(TABLES) + ' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain strict financial source disclosure'; END IF; END $$""")
    for table in TABLES:
        op.execute(f'DROP POLICY financial_source_disclosure ON {table}')
    op.execute(PREVIOUS_QUERY_ACCESS)
    op.execute('DROP FUNCTION property_financial_disclosure_access(uuid,uuid,text),financial_parent_source_access(uuid,text,uuid,uuid),property_complete_graph_actor_access(uuid,uuid,uuid,text,boolean)')
