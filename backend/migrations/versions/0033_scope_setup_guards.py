"""Validate SEV owner periods and order principal changes against scope effects."""
from alembic import op

revision = '0033'
down_revision = '0032'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    CREATE FUNCTION lock_scope_principal_change() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE org uuid;
    BEGIN
      FOR org IN SELECT DISTINCT value FROM unnest(ARRAY[OLD.organization_id,
        CASE WHEN TG_OP='DELETE' THEN NULL::uuid ELSE NEW.organization_id END]) value
        WHERE value IS NOT NULL ORDER BY value LOOP
        PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||org::text,0));
      END LOOP;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER lock_scope_principal_change BEFORE UPDATE OR DELETE ON membership
      FOR EACH ROW EXECUTE FUNCTION lock_scope_principal_change();
    CREATE TRIGGER lock_scope_principal_change BEFORE UPDATE OF is_active,organization_id OR DELETE ON "user"
      FOR EACH ROW EXECUTE FUNCTION lock_scope_principal_change();

    CREATE FUNCTION lock_scope_unit_history() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE key text;
    BEGIN
      FOR key IN SELECT DISTINCT value FROM unnest(ARRAY[
        CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.organization_id::text||':'||OLD.unit_id::text END,
        CASE WHEN TG_OP='DELETE' THEN NULL ELSE NEW.organization_id::text||':'||NEW.unit_id::text END
      ]) value WHERE value IS NOT NULL ORDER BY value LOOP
        PERFORM pg_advisory_xact_lock(hashtextextended('scope-unit-owner:'||key,0));
      END LOOP;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER lock_scope_unit_history BEFORE INSERT OR UPDATE OR DELETE ON ownership_history
      FOR EACH ROW EXECUTE FUNCTION lock_scope_unit_history();

    CREATE FUNCTION validate_sev_owner(org uuid, sphere_id uuid, unit uuid, owner uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE sphere public.accounting_scope; coverage datemultirange;
    BEGIN
      SELECT * INTO sphere FROM public.accounting_scope WHERE organization_id=org AND id=sphere_id;
      IF sphere.scope_type<>'SEV' THEN RETURN; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('scope-unit-owner:'||org::text||':'||unit::text,0));
      SELECT range_agg(daterange(h.valid_from,h.valid_to,'[)')) INTO coverage
        FROM public.ownership_history h WHERE h.organization_id=org AND h.unit_id=unit AND h.owner_profile_id=owner;
      IF coverage IS NULL OR NOT coverage @> daterange(sphere.valid_from,sphere.valid_to,'[]') THEN
        RAISE EXCEPTION 'SEV owner history must cover the complete scope period for each unit' USING ERRCODE='23514';
      END IF;
    END $$;
    CREATE FUNCTION validate_scope_unit_owner() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      PERFORM public.validate_sev_owner(NEW.organization_id,NEW.scope_id,NEW.unit_id,NEW.owner_profile_id);
      RETURN NEW;
    END $$;
    CREATE TRIGGER validate_scope_unit_owner BEFORE INSERT ON accounting_scope_unit
      FOR EACH ROW EXECUTE FUNCTION validate_scope_unit_owner();

    ALTER FUNCTION validate_accounting_binding(public.accounting_scope_binding)
      RENAME TO validate_accounting_binding_baseline;
    CREATE FUNCTION validate_accounting_binding(binding public.accounting_scope_binding) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF EXISTS(SELECT 1 FROM public.accounting_scope WHERE organization_id=binding.organization_id
        AND id=binding.scope_id AND scope_type='SEV') THEN
        RAISE EXCEPTION 'SEV financial binding awaits explicit resource owner and unit validation' USING ERRCODE='23514';
      END IF;
      PERFORM public.validate_accounting_binding_baseline(binding);
    END $$;
    REVOKE ALL ON FUNCTION lock_scope_principal_change(),lock_scope_unit_history(),
      validate_sev_owner(uuid,uuid,uuid,uuid),validate_scope_unit_owner(),
      validate_accounting_binding(public.accounting_scope_binding) FROM PUBLIC;
    ''')
    # Existing explicit SEV evidence must also be valid; never bless an unrelated
    # owner silently merely because the scope predates this guard.
    op.execute('''SELECT validate_sev_owner(organization_id,scope_id,unit_id,owner_profile_id)
                  FROM accounting_scope_unit''')
    op.execute('SELECT validate_accounting_binding(b) FROM accounting_scope_binding b')


def downgrade():
    op.execute('''
    DROP TRIGGER validate_scope_unit_owner ON accounting_scope_unit;
    DROP TRIGGER lock_scope_unit_history ON ownership_history;
    DROP TRIGGER lock_scope_principal_change ON membership;
    DROP TRIGGER lock_scope_principal_change ON "user";
    DROP FUNCTION validate_accounting_binding(public.accounting_scope_binding);
    ALTER FUNCTION validate_accounting_binding_baseline(public.accounting_scope_binding)
      RENAME TO validate_accounting_binding;
    DROP FUNCTION validate_scope_unit_owner(),validate_sev_owner(uuid,uuid,uuid,uuid),
      lock_scope_unit_history(),lock_scope_principal_change();
    ''')
