"""Expose only authorized pinned page evidence for manual context suggestions."""
from alembic import op

revision='0111'
down_revision='0110'
branch_labels=None
depends_on=None


def upgrade():
    op.execute('''
    CREATE FUNCTION assignment_candidate_source(org uuid,logical_id uuid,partition_id uuid,extraction_id uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE l public.logical_document;e public.extraction_revision;a public.intake_extraction_attempt;hint uuid;pages jsonb;
    BEGIN
      IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501';END IF;
      SELECT * INTO l FROM public.logical_document WHERE organization_id=org AND id=logical_id;
      IF l.id IS NULL OR NOT public.source_partition_access(org,l.document_ref_id,false) THEN
        RAISE EXCEPTION 'Source inaccessible' USING ERRCODE='42501';END IF;
      IF partition_id IS NULL OR extraction_id IS NULL THEN RAISE EXCEPTION 'Exact source basis required' USING ERRCODE='22023';END IF;
      IF partition_id IS DISTINCT FROM (SELECT id FROM public.source_partition_revision WHERE organization_id=org AND document_ref_id=l.document_ref_id ORDER BY revision_number DESC LIMIT 1)
        OR NOT EXISTS(SELECT 1 FROM public.source_partition_member WHERE organization_id=org AND partition_revision_id=partition_id AND logical_document_id=logical_id) THEN
        RAISE EXCEPTION 'Source partition changed' USING ERRCODE='23505';END IF;
      SELECT * INTO e FROM public.extraction_revision WHERE organization_id=org AND logical_document_id=logical_id ORDER BY created_at DESC,id DESC LIMIT 1;
      IF e.id IS DISTINCT FROM extraction_id THEN RAISE EXCEPTION 'Extraction changed' USING ERRCODE='23505';END IF;
      IF e.status NOT IN ('succeeded','needs_review') OR e.payload IS NULL OR e.content_sha256 IS DISTINCT FROM l.content_sha256
        OR e.document_ref_id IS DISTINCT FROM l.document_ref_id OR e.intake_attempt_id IS NULL THEN
        RAISE EXCEPTION 'Current extraction unavailable' USING ERRCODE='23514';END IF;
      SELECT attempt.* INTO a FROM public.intake_extraction_attempt attempt JOIN public.intake_extraction_job job ON job.organization_id=attempt.organization_id AND job.id=attempt.job_id
        WHERE attempt.organization_id=org AND attempt.id=e.intake_attempt_id AND job.logical_document_id=l.id;
      IF a.id IS NULL OR a.finished_at IS NULL OR a.result->>'content_sha256' IS DISTINCT FROM l.content_sha256
        OR a.result->'payload' IS DISTINCT FROM e.payload OR (a.result->>'page_start')::integer IS DISTINCT FROM l.page_start
        OR (a.result->>'page_end')::integer IS DISTINCT FROM l.page_end THEN
        RAISE EXCEPTION 'Pinned extraction source mismatch' USING ERRCODE='23514';END IF;
      SELECT COALESCE(jsonb_agg(jsonb_build_object('number',p->'number','text',p->'text','method',p->'method') ORDER BY (p->>'number')::integer),'[]')
        INTO pages FROM jsonb_array_elements(a.result->'pages') p
        WHERE (p->>'number')::integer BETWEEN l.page_start AND l.page_end;
      SELECT b.scope_hint_id INTO hint FROM public.document_ref r JOIN public.intake_original o ON o.organization_id=r.organization_id AND o.id=r.intake_original_id
        JOIN public.intake_item i ON i.organization_id=o.organization_id AND i.id=o.item_id JOIN public.intake_batch b ON b.organization_id=i.organization_id AND b.id=i.batch_id
        JOIN public.accounting_scope s ON s.organization_id=b.organization_id AND s.id=b.scope_hint_id
        WHERE r.organization_id=org AND r.id=l.document_ref_id AND public.has_scope_capability(org,s.id,'read') AND public.property_master_access(org,s.property_id,'read');
      RETURN jsonb_build_object('logical_document_id',l.id,'partition_revision_id',partition_id,'extraction_revision_id',e.id,'pages',pages,
        'service_period_from',e.payload->'service_period_from','service_period_to',e.payload->'service_period_to','scope_hint_id',hint,
        'can_prepare',public.source_partition_access(org,l.document_ref_id,true));
    END $$;
    REVOKE ALL ON FUNCTION assignment_candidate_source(uuid,uuid,uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION assignment_candidate_source(uuid,uuid,uuid,uuid) TO app_rw;
    ''')


def downgrade():
    op.execute('DROP FUNCTION assignment_candidate_source(uuid,uuid,uuid,uuid)')
