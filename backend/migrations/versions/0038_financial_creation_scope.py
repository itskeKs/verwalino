"""Bind new financial resources atomically to their validated creation context."""
from alembic import op

revision = '0038'
down_revision = '0037'
branch_labels = None
depends_on = None
TABLES = ('bank_account', 'cost_entry', 'settlement_run', 'economic_plan')


def upgrade():
    op.execute('''CREATE FUNCTION bind_new_financial_resource() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE selected uuid; sphere public.accounting_scope; payload jsonb; capability text;
    BEGIN
      -- Trusted migrations may create unclassified evidence before reconciliation.
      -- Runtime inserts still require the existing organization RLS policy.
      IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN NEW; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
      selected:=NULLIF(current_setting('app.current_scope_id',true),'')::uuid;
      IF selected IS NULL THEN
        IF public.property_has_classified_finance(NEW.organization_id,NEW.property_id) THEN
          RAISE EXCEPTION 'An exact accounting scope is required for this property' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END IF;
      IF NOT public.has_scope_capability(NEW.organization_id,selected,'prepare') THEN
        RAISE EXCEPTION 'Accounting scope preparation capability required' USING ERRCODE='42501';
      END IF;
      SELECT * INTO sphere FROM public.accounting_scope WHERE organization_id=NEW.organization_id AND id=selected;
      IF sphere.property_id IS DISTINCT FROM NEW.property_id THEN
        RAISE EXCEPTION 'Financial property must match accounting scope' USING ERRCODE='23514';
      END IF;
      payload:=to_jsonb(NEW);
      capability:=CASE WHEN TG_TABLE_NAME='cost_entry' AND payload->>'status'='confirmed' THEN 'confirm'
        WHEN TG_TABLE_NAME IN ('settlement_run','economic_plan') AND payload->>'status'='finalized' THEN 'approve' ELSE 'prepare' END;
      IF NOT public.has_scope_capability(NEW.organization_id,selected,capability) THEN
        RAISE EXCEPTION 'Accounting scope finalization capability required' USING ERRCODE='42501';
      END IF;
      IF payload ? 'management_mandate_id' THEN
        IF payload->>'management_mandate_id' IS NOT NULL AND (payload->>'management_mandate_id')::uuid IS DISTINCT FROM sphere.management_mandate_id THEN
          RAISE EXCEPTION 'Financial mandate must follow accounting scope' USING ERRCODE='23514';
        END IF;
        NEW:=jsonb_populate_record(NEW,jsonb_build_object('management_mandate_id',sphere.management_mandate_id));
      END IF;
      EXECUTE format('INSERT INTO public.accounting_scope_binding(organization_id,property_id,scope_id,%I,origin,reason,reviewed_by) '
        ||'VALUES($1,$2,$3,$4,''REVIEWED'',''Inherited from the exact creation context'',$5)',TG_TABLE_NAME||'_id')
        USING NEW.organization_id,NEW.property_id,selected,NEW.id,NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION bind_new_financial_resource() FROM PUBLIC;
    ''')
    for table in TABLES:
        op.execute(f'CREATE TRIGGER bind_new_financial_resource BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION bind_new_financial_resource()')
    op.execute('''ALTER FUNCTION validate_accounting_binding(public.accounting_scope_binding) RENAME TO validate_accounting_binding_before_creation;
    CREATE FUNCTION validate_accounting_binding(binding public.accounting_scope_binding) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE resource jsonb; kind text; target uuid;
    BEGIN
      PERFORM public.validate_accounting_binding_before_creation(binding);
      IF binding.cost_entry_id IS NOT NULL THEN kind:='cost_entry'; target:=binding.cost_entry_id;
      ELSIF binding.settlement_run_id IS NOT NULL THEN kind:='settlement_run'; target:=binding.settlement_run_id;
      ELSIF binding.economic_plan_id IS NOT NULL THEN kind:='economic_plan'; target:=binding.economic_plan_id;
      ELSE RETURN; END IF;
      EXECUTE format('SELECT to_jsonb(r) FROM public.%I r WHERE organization_id=$1 AND id=$2',kind)
        INTO resource USING binding.organization_id,target;
      IF resource->>'superseded_by_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.accounting_scope_binding b
        WHERE b.organization_id=binding.organization_id AND b.scope_id=binding.scope_id AND (
          (kind='cost_entry' AND b.cost_entry_id=(resource->>'superseded_by_id')::uuid)
          OR (kind='settlement_run' AND b.settlement_run_id=(resource->>'superseded_by_id')::uuid)
          OR (kind='economic_plan' AND b.economic_plan_id=(resource->>'superseded_by_id')::uuid))) THEN
        RAISE EXCEPTION 'Financial successor must retain original accounting scope' USING ERRCODE='23514';
      END IF;
      IF kind='settlement_run' AND resource->>'economic_plan_id' IS NOT NULL AND NOT EXISTS(
        SELECT 1 FROM public.accounting_scope_binding b WHERE b.organization_id=binding.organization_id
          AND b.scope_id=binding.scope_id AND b.economic_plan_id=(resource->>'economic_plan_id')::uuid) THEN
        RAISE EXCEPTION 'Linked economic plan must share accounting scope' USING ERRCODE='23514';
      END IF;
    END $$;
    REVOKE ALL ON FUNCTION validate_accounting_binding(public.accounting_scope_binding) FROM PUBLIC;
    ''')


def downgrade():
    op.execute('''DO $$ BEGIN
      IF to_regprocedure('validate_accounting_binding_before_creation(public.accounting_scope_binding)') IS NOT NULL THEN
        DROP FUNCTION validate_accounting_binding(public.accounting_scope_binding);
        ALTER FUNCTION validate_accounting_binding_before_creation(public.accounting_scope_binding) RENAME TO validate_accounting_binding;
      END IF;
    END $$;''')
    for table in TABLES:
        op.execute(f'DROP TRIGGER bind_new_financial_resource ON {table}')
    op.execute('DROP FUNCTION bind_new_financial_resource()')
