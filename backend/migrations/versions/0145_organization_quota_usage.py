"""Expose tenant aggregates without disclosing source-restricted records.

No new counters, tables or budget overrides. Existing durable ledgers retain
the model-call reservations and metered USD charges.
"""
from alembic import op

revision = "0145"
down_revision = "0144"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION organization_quota_usage(org uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE month_start timestamptz := date_trunc('month',clock_timestamp() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC';
      spent numeric;
    BEGIN
      IF session_user NOT IN ('app_rw','app_worker') OR org IS NULL
        OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR public.scope_actor_role(org) IS NULL THEN
        RAISE EXCEPTION 'Current organization member required' USING ERRCODE='42501'; END IF;
      -- Running imports reserve their full existing per-import ceiling. Unknown
      -- Q&A outcomes retain their reservation, even after its lease expires.
      SELECT COALESCE(sum(cost),0) INTO spent FROM (
        SELECT CASE WHEN q.status='FINALIZED' THEN q.estimated_cost_usd ELSE q.reserved_cost_usd END cost
          FROM public.property_knowledge_query_run q WHERE q.organization_id=org
            AND (q.created_at>=month_start OR q.finalized_at>=month_start
              OR (q.status='RESERVED' AND q.reservation_expires_at>=month_start))
        UNION ALL
        SELECT CASE WHEN r.status='RUNNING' THEN greatest(i.max_cost_usd,r.estimated_cost_usd)
          ELSE r.estimated_cost_usd END cost
          FROM public.declaration_extraction_run r JOIN public.declaration_import i
            ON i.organization_id=r.organization_id AND i.id=r.import_id
          WHERE r.organization_id=org AND (r.started_at>=month_start OR r.completed_at>=month_start
            OR (r.status='RUNNING' AND i.lease_expires_at>=month_start))
      ) charges;
      RETURN jsonb_build_object(
        'properties',(SELECT count(*) FROM public.property WHERE organization_id=org),
        'units',(SELECT count(*) FROM public.unit WHERE organization_id=org),
        'ai_budget_usd',spent::text);
    END $$;
    REVOKE ALL ON FUNCTION organization_quota_usage(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION organization_quota_usage(uuid) TO app_rw;
    """)


def downgrade():
    op.execute("DROP FUNCTION organization_quota_usage(uuid)")
