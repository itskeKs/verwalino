"""Immutable reviews of exact visual-page partitions, without financial approval."""
from alembic import op
from sqlalchemy import text

revision='0100'
down_revision='0099'
branch_labels=None
depends_on=None
BEFORE="UNION ALL SELECT 'intake_extraction_retry',id,to_jsonb(r) FROM public.intake_extraction_retry r WHERE organization_id=org) evidence"
AFTER="UNION ALL SELECT 'intake_extraction_retry',id,to_jsonb(r) FROM public.intake_extraction_retry r WHERE organization_id=org UNION ALL SELECT 'source_partition',id,to_jsonb(r) FROM public.source_partition_revision r WHERE organization_id=org UNION ALL SELECT 'source_partition_member',id,to_jsonb(r) FROM public.source_partition_member r WHERE organization_id=org) evidence"


def upgrade():
    op.execute('''
    ALTER TABLE logical_document ADD CONSTRAINT logical_document_partition_source UNIQUE(organization_id,document_ref_id,id);
    CREATE TABLE source_partition_revision(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),document_ref_id uuid NOT NULL,
      previous_revision_id uuid,revision_number integer NOT NULL CHECK(revision_number>0),
      content_sha256 text NOT NULL,verified_attempt_id uuid NOT NULL,verified_page_count integer,
      member_count integer NOT NULL CHECK(member_count BETWEEN 1 AND 200),
      visual_pages_reviewed boolean NOT NULL CHECK(visual_pages_reviewed),
      actor_id uuid NOT NULL,request_key uuid NOT NULL,request_digest text NOT NULL,
      reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 1000),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(organization_id,document_ref_id,id),UNIQUE(organization_id,document_ref_id,revision_number),
      UNIQUE NULLS NOT DISTINCT(organization_id,document_ref_id,previous_revision_id),UNIQUE(organization_id,actor_id,request_key),
      FOREIGN KEY(organization_id,document_ref_id) REFERENCES document_ref(organization_id,id),
      FOREIGN KEY(organization_id,document_ref_id,previous_revision_id) REFERENCES source_partition_revision(organization_id,document_ref_id,id),
      FOREIGN KEY(organization_id,verified_attempt_id) REFERENCES job_attempt(organization_id,id),
      FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
    CREATE TABLE source_partition_member(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,document_ref_id uuid NOT NULL,
      partition_revision_id uuid NOT NULL,logical_document_id uuid NOT NULL,
      UNIQUE(organization_id,partition_revision_id,logical_document_id),
      FOREIGN KEY(organization_id,document_ref_id,partition_revision_id) REFERENCES source_partition_revision(organization_id,document_ref_id,id),
      FOREIGN KEY(organization_id,document_ref_id,logical_document_id) REFERENCES logical_document(organization_id,document_ref_id,id));
    CREATE FUNCTION source_partition_access(org uuid,ref_id uuid,writing boolean) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE ref public.document_ref; batch uuid;
    BEGIN
      SELECT * INTO ref FROM public.document_ref WHERE organization_id=org AND id=ref_id;
      IF NOT FOUND OR ref.source_kind<>'intake_original' OR NOT public.intake_original_access(org,ref.intake_original_id,true) THEN RETURN false; END IF;
      SELECT i.batch_id INTO batch FROM public.intake_original o JOIN public.intake_item i ON i.organization_id=o.organization_id AND i.id=o.item_id
        WHERE o.organization_id=org AND o.id=ref.intake_original_id;
      RETURN public.intake_batch_access(org,batch,writing);
    END $$;
    ''')
    for table in ('source_partition_revision','source_partition_member'):
        op.execute(f'''ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review;
          GRANT SELECT ON {table} TO app_rw;
          CREATE POLICY source_partition_read ON {table} FOR SELECT USING(public.source_partition_access(organization_id,document_ref_id,false));
          CREATE POLICY source_partition_no_insert ON {table} FOR INSERT WITH CHECK(false);
          CREATE TRIGGER protect_partition_evidence BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();''')
    op.execute('''
    CREATE FUNCTION source_partition_result(org uuid,partition_id uuid) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT jsonb_build_object('id',r.id,'document_ref_id',r.document_ref_id,'previous_revision_id',r.previous_revision_id,
        'revision_number',r.revision_number,'content_sha256',r.content_sha256,'verified_page_count',r.verified_page_count,
        'visual_pages_reviewed',r.visual_pages_reviewed,'reason',r.reason,'created_at',r.created_at,
        'logical_document_ids',(SELECT jsonb_agg(m.logical_document_id ORDER BY l.page_start NULLS FIRST,l.id)
          FROM public.source_partition_member m JOIN public.logical_document l ON l.organization_id=m.organization_id AND l.id=m.logical_document_id
          WHERE m.organization_id=r.organization_id AND m.partition_revision_id=r.id))
      FROM public.source_partition_revision r WHERE r.organization_id=org AND r.id=partition_id;
    $$;
    CREATE FUNCTION source_partition_status(org uuid,ref_id uuid,partition_id uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE chosen uuid;
    BEGIN
      PERFORM public.lock_intake(org);
      IF NOT public.source_partition_access(org,ref_id,false) THEN RAISE EXCEPTION 'Source inaccessible' USING ERRCODE='42501'; END IF;
      SELECT id INTO chosen FROM public.source_partition_revision WHERE organization_id=org AND document_ref_id=ref_id
        AND (partition_id IS NULL OR id=partition_id) ORDER BY revision_number DESC LIMIT 1;
      IF partition_id IS NOT NULL THEN
        IF chosen IS NULL THEN RAISE EXCEPTION 'Partition inaccessible' USING ERRCODE='42501'; END IF;
        RETURN public.source_partition_result(org,chosen);
      END IF;
      RETURN jsonb_build_object('current',public.source_partition_result(org,chosen),'can_review',public.source_partition_access(org,ref_id,true));
    END $$;
    CREATE FUNCTION review_source_partition(org uuid,ref_id uuid,request uuid,expected uuid,selected uuid[],explanation text,acknowledged boolean) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      prior public.source_partition_revision; current_revision public.source_partition_revision; ref public.document_ref; attempt public.job_attempt;
      digest text; normalized uuid[]; created uuid; pages integer; n integer;
    BEGIN
      PERFORM public.lock_intake(org);
      IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501'; END IF;
      IF NOT public.source_partition_access(org,ref_id,true) THEN RAISE EXCEPTION 'Source inaccessible' USING ERRCODE='42501'; END IF;
      IF request IS NULL OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 1000 OR acknowledged IS DISTINCT FROM true
        OR selected IS NULL OR cardinality(selected) NOT BETWEEN 1 AND 200 OR array_ndims(selected)<>1
        OR array_position(selected,NULL) IS NOT NULL OR cardinality(selected)<>(SELECT count(DISTINCT value) FROM unnest(selected) value) THEN
        RAISE EXCEPTION 'Explicit bounded visual partition required' USING ERRCODE='22023'; END IF;
      SELECT array_agg(value ORDER BY value) INTO normalized FROM unnest(selected) value;
      digest:=encode(sha256(convert_to(jsonb_build_array(ref_id,expected,normalized,explanation,acknowledged)::text,'UTF8')),'hex');
      SELECT * INTO prior FROM public.source_partition_revision WHERE organization_id=org AND actor_id=actor AND request_key=request;
      IF FOUND THEN
        IF prior.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Partition request changed' USING ERRCODE='23505'; END IF;
        RETURN public.source_partition_result(org,prior.id);
      END IF;
      SELECT * INTO current_revision FROM public.source_partition_revision WHERE organization_id=org AND document_ref_id=ref_id ORDER BY revision_number DESC LIMIT 1;
      IF current_revision.id IS DISTINCT FROM expected THEN RAISE EXCEPTION 'Partition changed' USING ERRCODE='23505'; END IF;
      SELECT * INTO ref FROM public.document_ref WHERE organization_id=org AND id=ref_id;
      SELECT a.* INTO attempt FROM public.job_attempt a JOIN public.workflow_job j ON j.organization_id=a.organization_id AND j.id=a.job_id
        WHERE j.organization_id=org AND j.original_id=ref.intake_original_id AND j.status='clean' AND j.fence=a.fence
          AND a.finished_at IS NOT NULL AND a.result->>'status'='clean';
      IF NOT FOUND THEN RAISE EXCEPTION 'Exact inspection required' USING ERRCODE='23514'; END IF;
      pages:=(attempt.result->>'page_count')::integer;
      SELECT count(*) INTO n FROM public.logical_document WHERE organization_id=org AND document_ref_id=ref_id AND id=ANY(selected)
        AND verified_attempt_id=attempt.id AND content_sha256=ref.content_sha256 AND verified_page_count IS NOT DISTINCT FROM pages;
      IF n<>cardinality(selected) THEN RAISE EXCEPTION 'Partition source mismatch' USING ERRCODE='23514'; END IF;
      IF (pages IS NULL AND (ref.mime_type<>'application/xml' OR n<>1)) OR
        (pages IS NOT NULL AND EXISTS(SELECT p FROM generate_series(1,pages) p LEFT JOIN public.logical_document l
          ON l.organization_id=org AND l.id=ANY(selected) AND p BETWEEN l.page_start AND l.page_end GROUP BY p HAVING count(l.id)<>1)) THEN
        RAISE EXCEPTION 'Partition must cover every verified page exactly once' USING ERRCODE='23514'; END IF;
      INSERT INTO public.source_partition_revision(organization_id,document_ref_id,previous_revision_id,revision_number,content_sha256,
        verified_attempt_id,verified_page_count,member_count,visual_pages_reviewed,actor_id,request_key,request_digest,reason)
        VALUES(org,ref_id,expected,COALESCE(current_revision.revision_number,0)+1,ref.content_sha256,attempt.id,pages,n,true,actor,request,digest,explanation) RETURNING id INTO created;
      INSERT INTO public.source_partition_member(organization_id,document_ref_id,partition_revision_id,logical_document_id)
        SELECT org,ref_id,created,value FROM unnest(normalized) value;
      RETURN public.source_partition_result(org,created);
    END $$;
    REVOKE ALL ON FUNCTION source_partition_access(uuid,uuid,boolean),source_partition_result(uuid,uuid),source_partition_status(uuid,uuid,uuid),
      review_source_partition(uuid,uuid,uuid,uuid,uuid[],text,boolean) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION source_partition_access(uuid,uuid,boolean),source_partition_status(uuid,uuid,uuid),
      review_source_partition(uuid,uuid,uuid,uuid,uuid[],text,boolean) TO app_rw;
    ''')
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(BEFORE)==1
    op.execute(definition.replace(BEFORE,AFTER))


def downgrade():
    op.execute('LOCK TABLE source_partition_revision,source_partition_member IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM source_partition_revision) THEN RAISE EXCEPTION 'Retain reviewed source partitions; repair forward'; END IF; END $$")
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(AFTER)==1
    op.execute(definition.replace(AFTER,BEFORE))
    op.execute('''DROP FUNCTION review_source_partition(uuid,uuid,uuid,uuid,uuid[],text,boolean),source_partition_status(uuid,uuid,uuid),source_partition_result(uuid,uuid);
      DROP TABLE source_partition_member,source_partition_revision;
      DROP FUNCTION source_partition_access(uuid,uuid,boolean);
      ALTER TABLE logical_document DROP CONSTRAINT logical_document_partition_source;''')
