"""Expose tenant aggregates without disclosing source-restricted records.

Existing document sizes and durable ledgers supply storage and model usage.
Letters gain reconstructible size metadata; legacy originals remain unchanged.
"""
from alembic import op

revision = "0146"
down_revision = "0145"
branch_labels = None
depends_on = None


# Every persisted document key, including independently committed proof payloads.
# UNION ALL preserves references with unknown sizes; only usage deduplicates bytes.
STORAGE_REFERENCES = """
    SELECT storage_uri::text AS uri,content_byte_size AS bytes FROM public.attachment WHERE organization_id=org
    UNION ALL SELECT storage_uri,content_byte_size FROM public.invoice_document WHERE organization_id=org
    UNION ALL SELECT storage_uri,content_byte_size FROM public.mandate_document WHERE organization_id=org
    UNION ALL SELECT storage_uri,content_byte_size FROM public.property_document WHERE organization_id=org
    UNION ALL SELECT storage_uri,content_byte_size FROM public.intake_original WHERE organization_id=org
    UNION ALL SELECT storage_uri,size_bytes FROM public.letter_document WHERE organization_id=org
    UNION ALL SELECT storage_uri,content_byte_size FROM public.visual_extract_artifact WHERE organization_id=org
    UNION ALL SELECT payload->>'storage_uri',(payload->>'content_byte_size')::bigint
      FROM public.visual_extract_attestation WHERE organization_id=org AND payload->>'storage_uri' IS NOT NULL
    UNION ALL SELECT payload_diff->>'storage_uri',(payload_diff->>'bytes')::bigint
      FROM public.audit_event WHERE organization_id=org AND action IN ('mail_prepared','quota_recount')
        AND payload_diff->>'storage_uri' IS NOT NULL
"""
MEMBER_GUARD = """
    IF session_user NOT IN ('app_rw','app_worker') OR org IS NULL
      OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
      OR public.scope_actor_role(org) IS NULL THEN
      RAISE EXCEPTION 'Current organization member required' USING ERRCODE='42501'; END IF;
"""


def upgrade():
    op.execute(f"""
    ALTER TABLE public.letter_document ADD COLUMN size_bytes bigint CHECK (size_bytes>=0);
    CREATE FUNCTION organization_quota_usage(org uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE month_start timestamptz := date_trunc('month',clock_timestamp() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC';
      spent numeric;
      stored bigint;
    BEGIN
      {MEMBER_GUARD}
      -- One physical original may have references in several document kinds.
      -- Legacy sizes are reconciled through append-only administrative events.
      SELECT COALESCE(sum(bytes),0) INTO stored FROM (
        SELECT uri,max(bytes) AS bytes FROM (
          {STORAGE_REFERENCES}
        ) originals GROUP BY uri
      ) unique_originals;
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
        'storage_bytes',stored,
        'properties',(SELECT count(*) FROM public.property WHERE organization_id=org),
        'units',(SELECT count(*) FROM public.unit WHERE organization_id=org),
        'ai_budget_usd',spent::text);
    END $$;
    REVOKE ALL ON FUNCTION organization_quota_usage(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION organization_quota_usage(uuid) TO app_rw;
    CREATE FUNCTION organization_document_reference_count(org uuid,storage_key text) RETURNS bigint
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE references_count bigint;
    BEGIN
      {MEMBER_GUARD}
      SELECT count(*) INTO references_count FROM ({STORAGE_REFERENCES}) originals WHERE uri=storage_key;
      RETURN references_count;
    END $$;
    REVOKE ALL ON FUNCTION organization_document_reference_count(uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION organization_document_reference_count(uuid,text) TO app_rw;

    """)


def downgrade():
    op.execute("DROP FUNCTION organization_document_reference_count(uuid,text)")
    op.execute("DROP FUNCTION organization_quota_usage(uuid)")
    op.execute("ALTER TABLE public.letter_document DROP COLUMN IF EXISTS size_bytes")
