"""Exact access to classified financial resources; legacy rollout remains staged."""
from alembic import op

revision = '0034'
down_revision = '0033'
branch_labels = None
depends_on = None

FINANCIAL = ('bank_account', 'payment', 'cost_entry', 'settlement_run', 'economic_plan')
CHILDREN = {'settlement_line': ('settlement_run', 'settlement_run_id'),
            'economic_plan_line': ('economic_plan', 'economic_plan_id'),
            'invoice_document': ('invoice_document', 'id'),
            'coverage_declaration': ('coverage_declaration', 'id')}


def upgrade():
    op.execute('''
    CREATE FUNCTION financial_scope_id(org uuid, kind text, resource uuid) RETURNS uuid
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE sphere uuid; parent uuid; parent_kind text;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN NULL; END IF;
      IF kind='invoice_document' THEN
        SELECT cost_entry_id INTO parent FROM public.invoice_document WHERE organization_id=org AND id=resource;
        RETURN public.financial_scope_id(org,'cost_entry',parent);
      ELSIF kind='coverage_declaration' THEN
        SELECT COALESCE(settlement_run_id,economic_plan_id),CASE WHEN settlement_run_id IS NOT NULL THEN 'settlement_run' ELSE 'economic_plan' END
          INTO parent,parent_kind FROM public.coverage_declaration WHERE organization_id=org AND id=resource;
        RETURN public.financial_scope_id(org,parent_kind,parent);
      END IF;
      SELECT b.scope_id INTO sphere FROM public.accounting_scope_binding b WHERE b.organization_id=org AND (
        (kind='bank_account' AND b.bank_account_id=resource) OR (kind='payment' AND b.payment_id=resource)
        OR (kind='cost_entry' AND b.cost_entry_id=resource) OR (kind='settlement_run' AND b.settlement_run_id=resource)
        OR (kind='economic_plan' AND b.economic_plan_id=resource));
      RETURN sphere;
    END $$;
    CREATE FUNCTION financial_scope_access(org uuid, kind text, resource uuid, capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE sphere uuid; selected uuid;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN false; END IF;
      IF capability NOT IN ('read','prepare','confirm','approve','send','export') THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      selected:=NULLIF(current_setting('app.current_scope_id',true),'')::uuid;
      sphere:=public.financial_scope_id(org,kind,resource);
      IF sphere IS NULL THEN RETURN selected IS NULL; END IF; -- legacy compatibility only
      IF selected IS NOT NULL AND selected<>sphere THEN RETURN false; END IF;
      RETURN public.has_scope_capability(org,sphere,capability);
    END $$;
    CREATE FUNCTION authorize_bound_financial_write() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE original jsonb; replacement jsonb; record_id uuid; org uuid; capability text; sphere uuid;
    BEGIN
      original:=CASE WHEN TG_OP='INSERT' THEN to_jsonb(NEW) ELSE to_jsonb(OLD) END;
      replacement:=CASE WHEN TG_OP='DELETE' THEN original ELSE to_jsonb(NEW) END;
      org:=(original->>'organization_id')::uuid; record_id:=(original->>'id')::uuid;
      sphere:=public.financial_scope_id(org,TG_TABLE_NAME,record_id);
      IF sphere IS NULL THEN
        IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
      END IF;
      capability:='prepare';
      IF TG_TABLE_NAME='cost_entry' AND (original->>'status'='confirmed' OR replacement->>'status'='confirmed') THEN capability:='confirm'; END IF;
      IF TG_TABLE_NAME IN ('settlement_run','economic_plan') AND (original->>'status'='finalized' OR replacement->>'status'='finalized') THEN capability:='approve'; END IF;
      IF NOT public.financial_scope_access(org,TG_TABLE_NAME,record_id,capability) THEN
        RAISE EXCEPTION 'Financial scope capability required' USING ERRCODE='42501';
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END $$;
    REVOKE ALL ON FUNCTION financial_scope_id(uuid,text,uuid),financial_scope_access(uuid,text,uuid,text),authorize_bound_financial_write() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION financial_scope_access(uuid,text,uuid,text) TO app_rw;
    ''')
    op.execute('''
    CREATE FUNCTION property_has_classified_finance(org uuid, property uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT org=NULLIF(current_setting('app.current_org_id',true),'')::uuid AND EXISTS(
        SELECT 1 FROM public.accounting_scope_binding WHERE organization_id=org AND property_id=property)
    $$;
    CREATE FUNCTION scope_financial_inputs_classified(org uuid, sphere uuid) RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE property uuid; missing boolean; resource text;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR NOT public.has_scope_capability(org,sphere,'read') THEN RETURN false; END IF;
      SELECT property_id INTO property FROM public.accounting_scope WHERE organization_id=org AND id=sphere;
      IF property IS NULL THEN RETURN false; END IF;
      FOREACH resource IN ARRAY ARRAY['bank_account','cost_entry','payment','settlement_run','economic_plan'] LOOP
        EXECUTE format('SELECT EXISTS(SELECT 1 FROM public.%I r WHERE r.organization_id=$1 AND r.property_id=$2 '
          ||'AND NOT EXISTS(SELECT 1 FROM public.accounting_scope_binding b WHERE b.organization_id=r.organization_id AND b.%I=r.id))',resource,resource||'_id')
          INTO missing USING org,property;
        IF missing THEN RETURN false; END IF;
      END LOOP;
      RETURN true;
    END $$;
    CREATE FUNCTION unit_in_financial_context(org uuid, unit uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT CASE WHEN NULLIF(current_setting('app.current_scope_id',true),'') IS NULL THEN true ELSE EXISTS(
        SELECT 1 FROM public.accounting_scope s JOIN public.unit u ON u.organization_id=s.organization_id AND u.property_id=s.property_id
        WHERE s.organization_id=org AND org=NULLIF(current_setting('app.current_org_id',true),'')::uuid AND u.id=unit
          AND s.id=NULLIF(current_setting('app.current_scope_id',true),'')::uuid AND (s.whole_property OR EXISTS(
            SELECT 1 FROM public.accounting_scope_unit su WHERE su.organization_id=org AND su.scope_id=s.id AND su.unit_id=unit))) END
    $$;
    REVOKE ALL ON FUNCTION property_has_classified_finance(uuid,uuid),scope_financial_inputs_classified(uuid,uuid),unit_in_financial_context(uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_has_classified_finance(uuid,uuid),scope_financial_inputs_classified(uuid,uuid),unit_in_financial_context(uuid,uuid) TO app_rw;
    ''')
    for table, column in (('unit', 'id'), ('ownership_history', 'unit_id'), ('lease_agreement', 'unit_id')):
        op.execute(f"CREATE POLICY financial_unit_context ON {table} AS RESTRICTIVE FOR SELECT USING (unit_in_financial_context(organization_id,{column}))")
    for table in FINANCIAL:
        read = f"financial_scope_access(organization_id,'{table}',id,'read')"
        # Existing permissive org policies continue to apply. These restrictions
        # cannot be bypassed by adding another permissive organization policy.
        op.execute(f'CREATE POLICY classified_financial_read ON {table} AS RESTRICTIVE FOR SELECT USING ({read})')
        op.execute(f'CREATE POLICY classified_financial_update ON {table} AS RESTRICTIVE FOR UPDATE USING ({read}) WITH CHECK ({read})')
        op.execute(f'CREATE POLICY classified_financial_delete ON {table} AS RESTRICTIVE FOR DELETE USING ({read})')
        if table != 'payment':  # 0032 already separates payment preparation and confirmation.
            op.execute(f'CREATE TRIGGER authorize_bound_financial_write BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION authorize_bound_financial_write()')
    for table, (kind, column) in CHILDREN.items():
        expression = f"financial_scope_access(organization_id,'{kind}',{column},'read')"
        op.execute(f'CREATE POLICY classified_financial_read ON {table} AS RESTRICTIVE FOR SELECT USING ({expression})')
    for table, parent, column in (('settlement_line','settlement_run','settlement_run_id'), ('economic_plan_line','economic_plan','economic_plan_id')):
        permission = f"(financial_scope_access(organization_id,'{parent}',{column},'prepare') OR financial_scope_access(organization_id,'{parent}',{column},'approve'))"
        op.execute(f'CREATE POLICY classified_financial_insert ON {table} AS RESTRICTIVE FOR INSERT WITH CHECK ({permission})')
        op.execute(f'CREATE POLICY classified_financial_update ON {table} AS RESTRICTIVE FOR UPDATE USING ({permission}) WITH CHECK ({permission})')
        op.execute(f'CREATE POLICY classified_financial_delete ON {table} AS RESTRICTIVE FOR DELETE USING ({permission})')
    coverage = "financial_scope_access(organization_id,CASE WHEN settlement_run_id IS NOT NULL THEN 'settlement_run' ELSE 'economic_plan' END,COALESCE(settlement_run_id,economic_plan_id),'confirm')"
    op.execute(f'CREATE POLICY classified_financial_insert ON coverage_declaration AS RESTRICTIVE FOR INSERT WITH CHECK ({coverage})')
    op.execute('''
    CREATE FUNCTION authorize_scoped_invoice_write() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE original_scope uuid; replacement_scope uuid; capability text;
    BEGIN
      capability:=CASE WHEN NEW.released_at IS NOT NULL THEN 'confirm' ELSE 'prepare' END;
      replacement_scope:=public.financial_scope_id(NEW.organization_id,'cost_entry',NEW.cost_entry_id);
      IF (replacement_scope IS NOT NULL OR NULLIF(current_setting('app.current_scope_id',true),'') IS NOT NULL) AND NOT public.financial_scope_access(NEW.organization_id,'cost_entry',NEW.cost_entry_id,capability) THEN
        RAISE EXCEPTION 'Source scope capability required' USING ERRCODE='42501';
      END IF;
      IF TG_OP='UPDATE' THEN
        original_scope:=public.financial_scope_id(OLD.organization_id,'cost_entry',OLD.cost_entry_id);
        replacement_scope:=public.financial_scope_id(NEW.organization_id,'cost_entry',NEW.cost_entry_id);
        IF original_scope IS NOT NULL AND original_scope IS DISTINCT FROM replacement_scope THEN
          RAISE EXCEPTION 'A classified source cannot leave its accounting scope' USING ERRCODE='23514';
        END IF;
        IF original_scope IS NOT NULL AND NOT public.financial_scope_access(OLD.organization_id,'cost_entry',OLD.cost_entry_id,capability) THEN
          RAISE EXCEPTION 'Source scope capability required' USING ERRCODE='42501';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION authorize_scoped_invoice_write() FROM PUBLIC;
    CREATE TRIGGER authorize_scoped_invoice_write BEFORE INSERT OR UPDATE ON invoice_document
      FOR EACH ROW EXECUTE FUNCTION authorize_scoped_invoice_write();
    ''')
    op.execute("CREATE POLICY classified_financial_read ON audit_event AS RESTRICTIVE FOR SELECT USING (financial_scope_access(organization_id,entity_type,entity_id,'read'))")


def downgrade():
    op.execute('DROP TRIGGER IF EXISTS authorize_scoped_invoice_write ON invoice_document')
    op.execute('DROP FUNCTION IF EXISTS authorize_scoped_invoice_write()')
    for table in ('settlement_line','economic_plan_line'):
        for operation in ('insert','update','delete'):
            op.execute(f'DROP POLICY IF EXISTS classified_financial_{operation} ON {table}')
    op.execute('DROP POLICY IF EXISTS classified_financial_insert ON coverage_declaration')
    for table in ('unit', 'ownership_history', 'lease_agreement'):
        op.execute(f'DROP POLICY IF EXISTS financial_unit_context ON {table}')
    op.execute('DROP FUNCTION IF EXISTS property_has_classified_finance(uuid,uuid),scope_financial_inputs_classified(uuid,uuid),unit_in_financial_context(uuid,uuid)')
    for table in (*FINANCIAL, *CHILDREN, 'audit_event'):
        op.execute(f'DROP POLICY classified_financial_read ON {table}')
    for table in FINANCIAL:
        op.execute(f'DROP POLICY classified_financial_update ON {table}')
        op.execute(f'DROP POLICY classified_financial_delete ON {table}')
        if table != 'payment':
            op.execute(f'DROP TRIGGER authorize_bound_financial_write ON {table}')
    op.execute('DROP FUNCTION authorize_bound_financial_write(),financial_scope_access(uuid,text,uuid,text),financial_scope_id(uuid,text,uuid)')
