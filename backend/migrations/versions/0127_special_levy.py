"""T32: scoped append-only special levies and frozen owner obligations.

Revision ID: 0127
Revises: 0126
"""
from alembic import op

revision = "0127"
down_revision = "0126"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE special_levy (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL REFERENCES organization(id),
      property_id uuid NOT NULL, accounting_scope_id uuid NOT NULL,
      amount numeric(14,2) NOT NULL CHECK(amount>0 AND amount<'Infinity'::numeric),
      due_date date NOT NULL, resolution_id uuid REFERENCES resolution(id),
      creation_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_special_levy_context UNIQUE(organization_id,property_id,id),
      FOREIGN KEY(organization_id,property_id,accounting_scope_id)
        REFERENCES accounting_scope(organization_id,property_id,id)
    );
    CREATE INDEX ix_special_levy_scope_due ON special_levy(organization_id,accounting_scope_id,due_date);
    CREATE TABLE special_levy_allocation (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL,
      property_id uuid NOT NULL, special_levy_id uuid NOT NULL,
      owner_profile_id uuid NOT NULL, unit_id uuid NOT NULL,
      ownership_history_id uuid NOT NULL REFERENCES ownership_history(id),
      mea numeric NOT NULL CHECK(mea>0 AND mea<'Infinity'::numeric),
      amount numeric(14,2) NOT NULL CHECK(amount>=0 AND amount<'Infinity'::numeric),
      FOREIGN KEY(organization_id,property_id,special_levy_id) REFERENCES special_levy(organization_id,property_id,id),
      FOREIGN KEY(organization_id,property_id,unit_id) REFERENCES unit(organization_id,property_id,id),
      FOREIGN KEY(organization_id,owner_profile_id) REFERENCES owner_profile(organization_id,id),
      CONSTRAINT uq_special_levy_owner_unit UNIQUE(special_levy_id,unit_id,owner_profile_id)
    );
    CREATE INDEX ix_special_levy_allocation_parent ON special_levy_allocation(special_levy_id);

    CREATE FUNCTION special_levy_sources(org uuid, property uuid, levy uuid, resolution uuid, capability text)
      RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE a record;
    BEGIN
      IF capability NOT IN ('read','prepare','export') OR capability IS NULL THEN RETURN false; END IF;
      IF capability='prepare' THEN
        IF NOT public.property_financial_source_access(org,property) THEN RETURN false; END IF;
      ELSIF NOT public.property_financial_disclosure_access(org,property,capability) THEN RETURN false;
      END IF;
      IF resolution IS NOT NULL AND NOT public.property_meeting_access(org,'resolution',resolution,'read') THEN RETURN false; END IF;
      FOR a IN SELECT x.*,to_jsonb(h) history FROM public.special_levy_allocation x
        JOIN public.ownership_history h ON h.id=x.ownership_history_id AND h.organization_id=x.organization_id
        WHERE x.organization_id=org AND x.special_levy_id=levy
      LOOP
        IF NOT public.profile_source_access(org,'owner_profile',a.owner_profile_id,'read')
          OR NOT public.property_history_row_access(org,'ownership_history',a.history,'read') THEN RETURN false; END IF;
      END LOOP;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION special_levy_sources(uuid,uuid,uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION special_levy_sources(uuid,uuid,uuid,uuid,text) TO app_rw;

    CREATE FUNCTION guard_special_levy() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE s public.accounting_scope; l public.special_levy;
    BEGIN
      IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Special levies are append-only' USING ERRCODE='23514'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||NEW.organization_id::text,0));
      IF TG_TABLE_NAME='special_levy' THEN
        SELECT * INTO s FROM public.accounting_scope WHERE organization_id=NEW.organization_id AND id=NEW.accounting_scope_id;
        IF s.scope_type IS DISTINCT FROM 'WEG' OR NOT s.whole_property OR s.property_id IS DISTINCT FROM NEW.property_id
          OR NEW.due_date<s.valid_from OR (s.valid_to IS NOT NULL AND NEW.due_date>s.valid_to) THEN
          RAISE EXCEPTION 'Special levy requires a whole-property WEG scope covering its due date' USING ERRCODE='23514'; END IF;
        IF NEW.resolution_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.resolution r JOIN public.owners_meeting m
          ON m.id=r.meeting_id AND m.organization_id=r.organization_id WHERE r.id=NEW.resolution_id
          AND r.organization_id=NEW.organization_id AND m.property_id=NEW.property_id) THEN
          RAISE EXCEPTION 'Resolution belongs to another property or organization' USING ERRCODE='23514'; END IF;
        NEW.creation_xid:=pg_current_xact_id();
      ELSE
        SELECT * INTO l FROM public.special_levy WHERE organization_id=NEW.organization_id AND id=NEW.special_levy_id;
        IF l.creation_xid IS DISTINCT FROM pg_current_xact_id() THEN
          RAISE EXCEPTION 'Allocation basis is frozen at creation' USING ERRCODE='23514'; END IF;
        IF NOT EXISTS(SELECT 1 FROM public.ownership_history h WHERE h.id=NEW.ownership_history_id
          AND h.organization_id=NEW.organization_id AND h.unit_id=NEW.unit_id AND h.owner_profile_id=NEW.owner_profile_id
          AND h.valid_from<=l.due_date AND (h.valid_to IS NULL OR h.valid_to>l.due_date)) THEN
          RAISE EXCEPTION 'Allocation does not match due-date ownership' USING ERRCODE='23514'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION guard_special_levy() FROM PUBLIC;
    CREATE FUNCTION validate_special_levy_total() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE target uuid; total numeric;
    BEGIN
      IF TG_TABLE_NAME='special_levy' THEN target:=NEW.id; ELSE target:=NEW.special_levy_id; END IF;
      SELECT amount INTO total FROM public.special_levy WHERE id=target;
      IF NOT EXISTS(SELECT 1 FROM public.special_levy_allocation a WHERE a.special_levy_id=target)
        OR total IS DISTINCT FROM (SELECT sum(a.amount) FROM public.special_levy_allocation a WHERE a.special_levy_id=target) THEN
        RAISE EXCEPTION 'Special levy allocations must conserve the exact total' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION validate_special_levy_total() FROM PUBLIC;
    CREATE CONSTRAINT TRIGGER special_levy_total AFTER INSERT ON special_levy
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_special_levy_total();
    CREATE CONSTRAINT TRIGGER special_levy_allocation_total AFTER INSERT ON special_levy_allocation
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_special_levy_total();

    ALTER FUNCTION financial_scope_id(uuid,text,uuid) RENAME TO financial_scope_before_special_levy;
    CREATE FUNCTION financial_scope_id(org uuid,kind text,resource uuid) RETURNS uuid
      LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN NULL; END IF;
      IF kind='special_levy' THEN
        RETURN (SELECT accounting_scope_id FROM public.special_levy WHERE organization_id=org AND id=resource);
      END IF;
      RETURN public.financial_scope_before_special_levy(org,kind,resource);
    END $$;
    REVOKE ALL ON FUNCTION financial_scope_id(uuid,text,uuid),financial_scope_before_special_levy(uuid,text,uuid) FROM PUBLIC,app_rw;
    ALTER FUNCTION property_has_classified_finance(uuid,uuid) RENAME TO classified_finance_before_special_levy;
    CREATE FUNCTION property_has_classified_finance(org uuid,property uuid) RETURNS boolean
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT org=NULLIF(current_setting('app.current_org_id',true),'')::uuid AND (
        public.classified_finance_before_special_levy(org,property) OR EXISTS(
          SELECT 1 FROM public.special_levy WHERE organization_id=org AND property_id=property))
    $$;
    REVOKE ALL ON FUNCTION property_has_classified_finance(uuid,uuid),classified_finance_before_special_levy(uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_has_classified_finance(uuid,uuid) TO app_rw;
    ALTER FUNCTION property_source_audit_access(uuid,text,uuid) RENAME TO audit_sources_before_special_levy;
    CREATE FUNCTION property_source_audit_access(org uuid,kind text,resource uuid) RETURNS boolean
      LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog,pg_temp SET row_security=on AS $$
    BEGIN
      IF kind='special_levy' THEN RETURN EXISTS(SELECT 1 FROM public.special_levy WHERE organization_id=org AND id=resource); END IF;
      RETURN public.audit_sources_before_special_levy(org,kind,resource);
    END $$;
    REVOKE ALL ON FUNCTION property_source_audit_access(uuid,text,uuid),audit_sources_before_special_levy(uuid,text,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_source_audit_access(uuid,text,uuid),audit_sources_before_special_levy(uuid,text,uuid) TO app_rw;
    ALTER POLICY source_audit_read ON audit_event USING(property_source_audit_access(organization_id,entity_type,entity_id));

    ALTER FUNCTION report_scope_source_access(uuid,uuid,text) RENAME TO report_sources_before_special_levy;
    CREATE FUNCTION report_scope_source_access(org uuid,sphere uuid,capability text) RETURNS boolean
      LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE l record;
    BEGIN
      IF NOT public.report_sources_before_special_levy(org,sphere,capability) THEN RETURN false; END IF;
      FOR l IN SELECT * FROM public.special_levy WHERE organization_id=org
        AND (sphere IS NULL OR accounting_scope_id=sphere)
      LOOP
        IF sphere IS NULL OR NOT public.special_levy_sources(org,l.property_id,l.id,l.resolution_id,capability) THEN RETURN false; END IF;
      END LOOP;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION report_scope_source_access(uuid,uuid,text),report_sources_before_special_levy(uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION report_scope_source_access(uuid,uuid,text) TO app_rw;
    """)
    for table in ("special_levy", "special_levy_allocation"):
        op.execute(f"""
        ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
        GRANT SELECT,INSERT ON {table} TO app_rw;
        CREATE TRIGGER guard_special_levy BEFORE INSERT OR UPDATE OR DELETE ON {table}
          FOR EACH ROW EXECUTE FUNCTION guard_special_levy();
        """)
    op.execute("""
    CREATE FUNCTION special_levy_list_scopes(org uuid,property uuid DEFAULT NULL) RETURNS uuid[]
      LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE result uuid[]:=ARRAY[]::uuid[]; s record;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR public.scope_actor_role(org) IS NULL THEN RETURN NULL; END IF;
      FOR s IN SELECT id FROM public.accounting_scope WHERE organization_id=org
        AND (property IS NULL OR property_id=property) AND scope_type='WEG' AND whole_property ORDER BY id
      LOOP
        IF NOT public.has_scope_capability(org,s.id,'read')
          OR NOT public.report_scope_source_access(org,s.id,'read') THEN RETURN NULL; END IF;
        result:=array_append(result,s.id);
      END LOOP;
      IF cardinality(result)=0 THEN RETURN NULL; END IF;
      RETURN result;
    END $$;
    REVOKE ALL ON FUNCTION special_levy_list_scopes(uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION special_levy_list_scopes(uuid,uuid) TO app_rw;
    CREATE POLICY special_levy_read ON special_levy FOR SELECT USING (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND has_scope_capability(organization_id,accounting_scope_id,'read')
      AND (NULLIF(current_setting('app.current_scope_id',true),'') IS NULL
        OR accounting_scope_id=NULLIF(current_setting('app.current_scope_id',true),'')::uuid)
      AND special_levy_sources(organization_id,property_id,id,resolution_id,'read'));
    CREATE POLICY special_levy_insert ON special_levy FOR INSERT WITH CHECK (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK')
      AND accounting_scope_id=NULLIF(current_setting('app.current_scope_id',true),'')::uuid
      AND has_scope_capability(organization_id,accounting_scope_id,'prepare')
      AND special_levy_sources(organization_id,property_id,id,resolution_id,'prepare'));
    CREATE POLICY special_levy_allocation_read ON special_levy_allocation FOR SELECT USING (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND EXISTS(SELECT 1 FROM special_levy l WHERE l.organization_id=special_levy_allocation.organization_id AND l.id=special_levy_id));
    CREATE POLICY special_levy_allocation_insert ON special_levy_allocation FOR INSERT WITH CHECK (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK')
      AND profile_source_access(organization_id,'owner_profile',owner_profile_id,'read')
      AND EXISTS(SELECT 1 FROM ownership_history h WHERE h.id=ownership_history_id AND h.organization_id=special_levy_allocation.organization_id)
      AND EXISTS(SELECT 1 FROM special_levy l WHERE l.organization_id=special_levy_allocation.organization_id AND l.id=special_levy_id
        AND has_scope_capability(l.organization_id,l.accounting_scope_id,'prepare')));
    """)


def downgrade():
    op.execute("""
    DROP FUNCTION report_scope_source_access(uuid,uuid,text);
    ALTER FUNCTION report_sources_before_special_levy(uuid,uuid,text) RENAME TO report_scope_source_access;
    GRANT EXECUTE ON FUNCTION report_scope_source_access(uuid,uuid,text) TO app_rw;
    DO $$ BEGIN
      IF to_regprocedure('audit_sources_before_special_levy(uuid,text,uuid)') IS NOT NULL THEN
        ALTER POLICY source_audit_read ON public.audit_event USING(public.audit_sources_before_special_levy(organization_id,entity_type,entity_id));
        DROP FUNCTION public.property_source_audit_access(uuid,text,uuid);
        ALTER FUNCTION public.audit_sources_before_special_levy(uuid,text,uuid) RENAME TO property_source_audit_access;
      END IF;
      IF to_regprocedure('classified_finance_before_special_levy(uuid,uuid)') IS NOT NULL THEN
        DROP FUNCTION public.property_has_classified_finance(uuid,uuid);
        ALTER FUNCTION public.classified_finance_before_special_levy(uuid,uuid) RENAME TO property_has_classified_finance;
      END IF;
      IF to_regprocedure('financial_scope_before_special_levy(uuid,text,uuid)') IS NOT NULL THEN
        DROP FUNCTION public.financial_scope_id(uuid,text,uuid);
        ALTER FUNCTION public.financial_scope_before_special_levy(uuid,text,uuid) RENAME TO financial_scope_id;
      END IF;
    END $$;
    DROP FUNCTION IF EXISTS special_levy_list_scopes(uuid,uuid);
    DROP TABLE special_levy_allocation;
    DROP TABLE special_levy;
    DROP FUNCTION guard_special_levy(),validate_special_levy_total(),special_levy_sources(uuid,uuid,uuid,uuid,text);
    """)
