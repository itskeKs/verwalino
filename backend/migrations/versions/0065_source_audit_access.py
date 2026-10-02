"""Audit disclosure inherits current typed source RLS without changing history."""
from alembic import op

revision = '0065'
down_revision = '0064'
branch_labels = None
depends_on = None

# Closed registry: no caller-controlled identifier can escape this allowlist.
# Person/history and meeting policies are not integrated yet, so their audit
# payloads remain unavailable in strict mode until that boundary is complete.
RESOURCES = (
    'property','unit','property_document','declaration_import',
    'declaration_import_document','declaration_candidate','declaration_candidate_evidence',
    'declaration_candidate_issue','declaration_extraction_run','declaration_queue_request','declaration_import_pass',
    'approved_fact_source','property_legal_reference','property_assignment','property_rule',
    'property_rule_source','property_rule_relation','property_document_relation','property_knowledge_query_run',
    'bank_account','payment','cost_entry','invoice_document','mandate_document','dunning_notice',
    'settlement_run','economic_plan','coverage_declaration',
    'accounting_scope','accounting_scope_binding','scope_grant','scope_payment_reconciliation','scope_control_event',
    'property_data_domain','property_data_grant',
)
ADMIN = ('accounting_scope','accounting_scope_binding','scope_grant','property_data_domain','property_data_grant')
TABLES = ('audit_event','property_source_security','knowledge_budget_policy') + RESOURCES


def upgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    kinds=','.join("'"+kind+"'" for kind in RESOURCES)
    admin=','.join("'"+kind+"'" for kind in ADMIN)
    op.execute(f"""
    CREATE FUNCTION property_audit_control_access(org uuid,kind text,resource uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR resource IS DISTINCT FROM org THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      IF public.scope_actor_role(org) IS DISTINCT FROM 'OWNER_ADMIN' THEN RETURN false; END IF;
      IF kind='property_source_security' THEN
        RETURN EXISTS(SELECT 1 FROM public.property_source_security WHERE organization_id=org);
      ELSIF kind='knowledge_budget_policy' THEN
        RETURN EXISTS(SELECT 1 FROM public.knowledge_budget_policy WHERE organization_id=org);
      END IF;
      RETURN false;
    END $$;
    REVOKE ALL ON FUNCTION property_audit_control_access(uuid,text,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_audit_control_access(uuid,text,uuid) TO app_rw;
    CREATE FUNCTION property_source_audit_access(org uuid,kind text,resource uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog,pg_temp SET row_security=on AS $$
    DECLARE mode text; allowed boolean; property uuid;
    BEGIN
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      IF public.scope_actor_role(org) IS NULL OR resource IS NULL OR kind IS NULL THEN RETURN false; END IF;
      IF kind IN ('property_source_security','knowledge_budget_policy') THEN
        -- Internal control tables deliberately have no direct app SELECT grant.
        -- This narrow boolean does not expose source data or configuration rows.
        RETURN public.property_audit_control_access(org,kind,resource);
      END IF;
      IF kind NOT IN ({kinds}) THEN RETURN false; END IF;
      IF kind IN ({admin}) AND public.scope_actor_role(org)<>'OWNER_ADMIN' THEN RETURN false; END IF;
      -- Invoker execution deliberately uses the same restrictive policies as
      -- ordinary reads. No definer lookup or audit creator can bypass them.
      EXECUTE format('SELECT EXISTS(SELECT 1 FROM public.%I WHERE organization_id=$1 AND id=$2)',kind)
        INTO allowed USING org,resource;
      IF NOT allowed THEN RETURN false; END IF;
      IF kind IN ('settlement_run','economic_plan','coverage_declaration') THEN
        -- Even an empty draft may have audit copies from an earlier revision.
        -- The empty-draft reader exception cannot authorize historical payloads.
        EXECUTE format('SELECT property_id FROM public.%I WHERE organization_id=$1 AND id=$2',kind)
          INTO property USING org,resource;
        RETURN public.property_financial_disclosure_access(org,property,'read');
      END IF;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION property_source_audit_access(uuid,text,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_source_audit_access(uuid,text,uuid) TO app_rw;
    CREATE POLICY source_audit_read ON audit_event AS RESTRICTIVE FOR SELECT
      USING(property_source_audit_access(organization_id,entity_type,entity_id));
    """)


def downgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain strict audit source disclosure'; END IF; END $$;
    DROP POLICY source_audit_read ON audit_event;
    DROP FUNCTION IF EXISTS property_source_audit_access(uuid,text,uuid),property_audit_control_access(uuid,text,uuid);
    """)
