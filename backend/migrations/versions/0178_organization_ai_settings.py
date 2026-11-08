"""Store encrypted organization AI settings and tenant-local monthly usage."""
from alembic import op
import sqlalchemy as sa

revision = '0178'
down_revision = '0177'
branch_labels = None
depends_on = None


def _reasoning_metadata(restore=False):
    definition = op.get_bind().scalar(sa.text("SELECT pg_get_functiondef('public.declaration_import_pass_safe_metadata_is_valid(jsonb,integer)'::regprocedure)"))
    old = "('none', 'low', 'medium', 'high', 'xhigh', 'max')"
    new = "('default', 'none', 'low', 'medium', 'high', 'xhigh', 'max')"
    before, after = (new, old) if restore else (old, new)
    if definition.count(after) == 1:
        return
    if definition.count(before) != 1:
        raise RuntimeError('Unexpected declaration metadata validator')
    op.execute(definition.replace(before, after))


def upgrade():
    _reasoning_metadata()
    op.execute("""
      CREATE TABLE public.organization_ai_settings (
        organization_id uuid PRIMARY KEY REFERENCES public.organization(id) ON DELETE RESTRICT,
        provider varchar(32) NOT NULL DEFAULT 'openai' CHECK (provider='openai'),
        encrypted_api_key text,
        model varchar(255),
        import_enabled boolean NOT NULL DEFAULT false,
        qa_enabled boolean NOT NULL DEFAULT false,
        monthly_limit_usd numeric(14,2) CHECK (monthly_limit_usd >= 0 AND monthly_limit_usd <> 'NaN'),
        CHECK (NOT(import_enabled OR qa_enabled) OR (encrypted_api_key IS NOT NULL AND model IS NOT NULL))
      );
      ALTER TABLE public.organization_ai_settings ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.organization_ai_settings FORCE ROW LEVEL SECURITY;
      CREATE POLICY organization_ai_settings_read ON public.organization_ai_settings FOR SELECT TO app_rw
        USING (organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid);
      CREATE POLICY organization_ai_settings_admin ON public.organization_ai_settings FOR ALL TO app_rw
        USING (organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
          AND public.scope_actor_role(organization_id)='OWNER_ADMIN')
        WITH CHECK (organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
          AND public.scope_actor_role(organization_id)='OWNER_ADMIN');
      REVOKE ALL ON public.organization_ai_settings FROM PUBLIC,app_rw,app_worker,app_review;
      GRANT SELECT,INSERT,UPDATE,DELETE ON public.organization_ai_settings TO app_rw;
      CREATE TRIGGER a_organization_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON public.organization_ai_settings
        FOR EACH ROW EXECUTE FUNCTION public.organization_lifecycle_guard();

      CREATE FUNCTION public.organization_ai_monthly_usage(org uuid,priced_models text[]) RETURNS numeric
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      DECLARE spent numeric; month_start timestamptz:=date_trunc('month',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC';
      BEGIN
        IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
          OR (session_user<>'app_worker' AND public.scope_actor_role(org) IS NULL) THEN
          RAISE EXCEPTION 'Organization not found' USING ERRCODE='42501';
        END IF;
        SELECT COALESCE(sum(cost),0) INTO spent FROM (
          SELECT CASE WHEN q.status='FINALIZED' THEN q.estimated_cost_usd ELSE q.reserved_cost_usd END cost
            FROM public.property_knowledge_query_run q WHERE q.organization_id=org
              AND (q.created_at>=month_start OR q.finalized_at>=month_start
                OR (q.status='RESERVED' AND q.reservation_expires_at>=month_start))
          UNION ALL
          SELECT CASE WHEN r.status='RUNNING' AND r.model_name=ANY(priced_models) THEN greatest(i.max_cost_usd,r.estimated_cost_usd)
            ELSE r.estimated_cost_usd END cost
            FROM public.declaration_extraction_run r JOIN public.declaration_import i
              ON i.organization_id=r.organization_id AND i.id=r.import_id
            WHERE r.organization_id=org AND (r.started_at>=month_start OR r.completed_at>=month_start
              OR (r.status='RUNNING' AND i.lease_expires_at>=month_start))
        ) charges;
        RETURN spent;
      END $$;
      REVOKE ALL ON FUNCTION public.organization_ai_monthly_usage(uuid,text[]) FROM PUBLIC,app_review;
      GRANT EXECUTE ON FUNCTION public.organization_ai_monthly_usage(uuid,text[]) TO app_rw;
    """)


def downgrade():
    op.execute('LOCK TABLE public.organization_ai_settings IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.organization_ai_settings) THEN
        RAISE EXCEPTION 'Organization AI settings require this release';
      END IF;
    END $$""")
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM declaration_import_pass WHERE safe_metadata->>'reasoning_effort'='default') THEN RAISE EXCEPTION 'Default reasoning metadata needs this release'; END IF; END $$""")
    _reasoning_metadata(restore=True)
    op.execute('DROP FUNCTION IF EXISTS public.organization_ai_monthly_usage(uuid,text[])')
    op.execute('DROP FUNCTION IF EXISTS public.organization_ai_monthly_usage(uuid)')
    op.drop_table('organization_ai_settings')
