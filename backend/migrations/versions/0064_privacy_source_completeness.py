"""Do not mistake hidden retained source-backed outputs for absent retention."""
from alembic import op

revision = '0064'
down_revision = '0063'
branch_labels = None
depends_on = None

TABLES = ('property_source_security','person','tenant_profile','owner_profile',
          'settlement_run','settlement_line','economic_plan','economic_plan_line')


def upgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""
    CREATE FUNCTION privacy_source_context_complete(org uuid,person uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; source record;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','export') THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      IF public.property_source_actor_role(org,actor) IS NULL THEN RETURN false; END IF;
      -- Enumerate retained outputs with the trusted definer. Ordinary result
      -- RLS must not turn a retained account into an apparently absent binding.
      FOR source IN
        SELECT r.property_id FROM public.settlement_run r WHERE r.organization_id=org AND r.status='finalized'
          AND (EXISTS(SELECT 1 FROM public.tenant_profile t WHERE t.organization_id=org AND t.id=r.tenant_profile_id AND t.person_id=person)
            OR EXISTS(SELECT 1 FROM public.settlement_line ln JOIN public.owner_profile o ON o.organization_id=ln.organization_id AND o.id=ln.addressee_owner_profile_id
              WHERE ln.organization_id=org AND ln.settlement_run_id=r.id AND o.person_id=person))
        UNION
        SELECT p.property_id FROM public.economic_plan p WHERE p.organization_id=org AND p.status='finalized'
          AND EXISTS(SELECT 1 FROM public.economic_plan_line ln JOIN public.owner_profile o ON o.organization_id=ln.organization_id AND o.id=ln.owner_profile_id
            WHERE ln.organization_id=org AND ln.economic_plan_id=p.id AND o.person_id=person)
      LOOP
        IF NOT public.property_complete_graph_actor_access(org,actor,source.property_id,capability,false) THEN RETURN false; END IF;
      END LOOP;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION privacy_source_context_complete(uuid,uuid,text) FROM PUBLIC,app_rw;
    ALTER FUNCTION privacy_financial_context_complete(uuid,uuid,text) RENAME TO privacy_context_before_source;
    REVOKE ALL ON FUNCTION privacy_context_before_source(uuid,uuid,text) FROM PUBLIC,app_rw;
    CREATE FUNCTION privacy_financial_context_complete(org uuid,person uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      -- Keep the existing financial completeness and retention serialization.
      -- Its read/export grant never substitutes for the source audience.
      RETURN public.privacy_context_before_source(org,person,capability)
        AND public.privacy_source_context_complete(org,person,capability);
    END $$;
    REVOKE ALL ON FUNCTION privacy_financial_context_complete(uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION privacy_financial_context_complete(uuid,uuid,text) TO app_rw;
    """)


def downgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain strict privacy source completeness'; END IF; END $$;
    DROP FUNCTION privacy_financial_context_complete(uuid,uuid,text),privacy_source_context_complete(uuid,uuid,text);
    ALTER FUNCTION privacy_context_before_source(uuid,uuid,text) RENAME TO privacy_financial_context_complete;
    GRANT EXECUTE ON FUNCTION privacy_financial_context_complete(uuid,uuid,text) TO app_rw;
    """)
