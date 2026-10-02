"""Keep dunning snapshots and provenance within their complete source audience."""
from alembic import op

revision = '0075'
down_revision = '0074'
branch_labels = None
depends_on = None
TABLES = 'property_source_security,dunning_notice,dunning_scope_context,accounting_scope,person,tenant_profile,owner_profile,payment'


def upgrade():
    op.execute('LOCK TABLE ' + TABLES + ' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""
    CREATE FUNCTION report_scope_source_access(org uuid,sphere uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; property uuid; source record;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','prepare','export') THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      SELECT s.property_id INTO property FROM public.accounting_scope s WHERE s.organization_id=org AND s.id=sphere;
      IF property IS NULL THEN RETURN false; END IF;
      -- Owner statements may have payment-only recipients without any lease,
      -- ownership or plan line. Enumerate these expected contacts independently
      -- of ordinary RLS so revocation cannot produce a partial or zero total.
      FOR source IN
        SELECT 'tenant_profile'::text kind,p.tenant_profile_id id FROM public.payment p
          JOIN public.accounting_scope_binding b ON b.organization_id=p.organization_id AND b.payment_id=p.id
          WHERE b.organization_id=org AND b.scope_id=sphere AND p.tenant_profile_id IS NOT NULL
        UNION SELECT 'owner_profile',p.owner_profile_id FROM public.payment p
          JOIN public.accounting_scope_binding b ON b.organization_id=p.organization_id AND b.payment_id=p.id
          WHERE b.organization_id=org AND b.scope_id=sphere AND p.owner_profile_id IS NOT NULL
      LOOP
        IF NOT public.profile_source_access(org,source.kind,source.id,'read')
          OR (capability='export' AND NOT public.profile_source_access(org,source.kind,source.id,'export')) THEN RETURN false; END IF;
      END LOOP;
      IF capability='prepare' THEN RETURN public.property_financial_source_access(org,property); END IF;
      RETURN public.property_financial_disclosure_access(org,property,'read')
        AND (capability<>'export' OR public.property_financial_disclosure_access(org,property,'export'));
    END $$;
    CREATE FUNCTION dunning_source_access(org uuid,notice uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE sphere uuid;
    BEGIN
      SELECT c.scope_id INTO sphere FROM public.dunning_scope_context c
        WHERE c.organization_id=org AND c.dunning_notice_id=notice;
      RETURN public.report_scope_source_access(org,sphere,capability);
    END $$;
    REVOKE ALL ON FUNCTION report_scope_source_access(uuid,uuid,text),dunning_source_access(uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION report_scope_source_access(uuid,uuid,text),dunning_source_access(uuid,uuid,text) TO app_rw;

    ALTER FUNCTION privacy_source_context_complete(uuid,uuid,text) RENAME TO privacy_sources_before_reports;
    CREATE FUNCTION privacy_source_context_complete(org uuid,person uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE notice uuid;
    BEGIN
      IF NOT public.privacy_sources_before_reports(org,person,capability) THEN RETURN false; END IF;
      FOR notice IN SELECT n.id FROM public.dunning_notice n WHERE n.organization_id=org AND n.status='issued'
        AND (EXISTS(SELECT 1 FROM public.tenant_profile p WHERE p.organization_id=org AND p.id=n.tenant_profile_id AND p.person_id=person)
          OR EXISTS(SELECT 1 FROM public.owner_profile p WHERE p.organization_id=org AND p.id=n.owner_profile_id AND p.person_id=person))
      LOOP
        IF NOT public.dunning_source_access(org,notice,capability) THEN RETURN false; END IF;
      END LOOP;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION privacy_source_context_complete(uuid,uuid,text),privacy_sources_before_reports(uuid,uuid,text) FROM PUBLIC,app_rw;

    CREATE POLICY dunning_source_read ON dunning_notice AS RESTRICTIVE FOR SELECT USING(dunning_source_access(organization_id,id,'read'));
    CREATE POLICY dunning_source_insert ON dunning_notice AS RESTRICTIVE FOR INSERT WITH CHECK(dunning_source_access(organization_id,id,'prepare'));
    CREATE POLICY dunning_source_update ON dunning_notice AS RESTRICTIVE FOR UPDATE USING(dunning_source_access(organization_id,id,'prepare')) WITH CHECK(dunning_source_access(organization_id,id,'prepare'));
    CREATE POLICY dunning_source_delete ON dunning_notice AS RESTRICTIVE FOR DELETE USING(dunning_source_access(organization_id,id,'prepare'));
    CREATE POLICY dunning_context_source_read ON dunning_scope_context AS RESTRICTIVE FOR SELECT USING(report_scope_source_access(organization_id,scope_id,'read'));
    CREATE POLICY dunning_context_source_insert ON dunning_scope_context AS RESTRICTIVE FOR INSERT WITH CHECK(report_scope_source_access(organization_id,scope_id,'prepare'));
    """)


def downgrade():
    op.execute('LOCK TABLE ' + TABLES + ' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain strict report source access'; END IF; END $$;
      DROP POLICY dunning_source_read ON dunning_notice;
      DROP POLICY dunning_source_insert ON dunning_notice;
      DROP POLICY dunning_source_update ON dunning_notice;
      DROP POLICY dunning_source_delete ON dunning_notice;
      DROP POLICY dunning_context_source_read ON dunning_scope_context;
      DROP POLICY dunning_context_source_insert ON dunning_scope_context;
      DROP FUNCTION privacy_source_context_complete(uuid,uuid,text);
      ALTER FUNCTION privacy_sources_before_reports(uuid,uuid,text) RENAME TO privacy_source_context_complete;
      DROP FUNCTION dunning_source_access(uuid,uuid,text),report_scope_source_access(uuid,uuid,text);
    """)
