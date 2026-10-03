"""Retain private visual derivatives with transaction-bound trusted provenance."""
from alembic import op
from sqlalchemy import text

revision = '0117'
down_revision = '0116'
branch_labels = None
depends_on = None
BEFORE = "UNION ALL SELECT 'allocation_correction_completion',id,to_jsonb(r) FROM public.allocation_correction_completion r WHERE organization_id=org) evidence"
AFTER = BEFORE.replace(') evidence', " UNION ALL SELECT 'visual_extract_artifact',id,to_jsonb(r) FROM public.visual_extract_artifact r WHERE organization_id=org UNION ALL SELECT 'visual_extract_attestation',id,to_jsonb(r) FROM public.visual_extract_attestation r WHERE organization_id=org) evidence")


def coverage(reverse=False):
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    before, after = (AFTER, BEFORE) if reverse else (BEFORE, AFTER)
    assert definition.count(before) == 1
    op.execute(definition.replace(before, after))


def upgrade():
    op.execute("""
      CREATE TABLE visual_extract_attestation(
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),
        actor_id uuid NOT NULL,request_key uuid NOT NULL,request_digest text NOT NULL,
        backend_pid integer NOT NULL,transaction_id xid8 NOT NULL,payload jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(organization_id,id),
        UNIQUE(organization_id,actor_id,request_key,backend_pid,transaction_id),
        FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
      CREATE TABLE visual_extract_artifact(
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),
        logical_document_id uuid NOT NULL,document_ref_id uuid NOT NULL,partition_revision_id uuid NOT NULL,
        source_sha256 text NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
        page_start integer NOT NULL CHECK(page_start>0),page_end integer NOT NULL,
        verified_page_count integer NOT NULL CHECK(verified_page_count BETWEEN 1 AND 200),
        output_page_count integer NOT NULL CHECK(output_page_count BETWEEN 1 AND 20),
        content_sha256 text NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
        content_byte_size bigint NOT NULL CHECK(content_byte_size BETWEEN 1 AND 26214400),
        renderer_profile jsonb NOT NULL CHECK(jsonb_typeof(renderer_profile)='object'),
        storage_uri text NOT NULL,storage_backend text NOT NULL,storage_version text,
        attestation_id uuid NOT NULL,actor_id uuid NOT NULL,request_key uuid NOT NULL,request_digest text NOT NULL,
        reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 1000),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(organization_id,id),
        UNIQUE(organization_id,actor_id,request_key),UNIQUE(organization_id,attestation_id),
        CHECK(page_end>=page_start AND page_end<=verified_page_count AND output_page_count=page_end-page_start+1),
        FOREIGN KEY(organization_id,document_ref_id,logical_document_id) REFERENCES logical_document(organization_id,document_ref_id,id),
        FOREIGN KEY(organization_id,document_ref_id,partition_revision_id) REFERENCES source_partition_revision(organization_id,document_ref_id,id),
        FOREIGN KEY(organization_id,attestation_id) REFERENCES visual_extract_attestation(organization_id,id),
        FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
      CREATE INDEX visual_extract_history ON visual_extract_artifact(organization_id,logical_document_id,created_at DESC,id DESC);
    """)
    for table in ('visual_extract_attestation', 'visual_extract_artifact'):
        op.execute(f"""ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review;
          CREATE POLICY visual_extract_private ON {table} USING(false) WITH CHECK(false);
          CREATE TRIGGER visual_extract_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();""")
    op.execute(SQL)
    coverage()


def downgrade():
    op.execute("""LOCK TABLE visual_extract_artifact,visual_extract_attestation IN ACCESS EXCLUSIVE MODE NOWAIT;
      DO $$ BEGIN IF EXISTS(SELECT 1 FROM visual_extract_artifact) OR EXISTS(SELECT 1 FROM visual_extract_attestation) THEN
        RAISE EXCEPTION 'Retain visual extract evidence; repair forward';END IF;END $$;""")
    coverage(reverse=True)
    op.execute("""DROP FUNCTION visual_extract_content(uuid,uuid),visual_extract_history(uuid,uuid,timestamptz,uuid,integer),
      complete_visual_extract(uuid,uuid,uuid,uuid,text,uuid),attest_visual_extract(uuid,uuid,text,integer,text,jsonb),
      prepare_visual_extract(uuid,uuid,uuid,uuid,text),visual_extract_result(uuid,uuid),require_visual_extract_source(uuid,uuid,boolean);
      DROP TABLE visual_extract_artifact,visual_extract_attestation;""")


SQL = r"""
CREATE FUNCTION require_visual_extract_source(org uuid,logical_id uuid,writing boolean) RETURNS public.logical_document
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE logical public.logical_document;ref public.document_ref;
BEGIN
  IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501';END IF;
  PERFORM public.lock_intake(org);
  SELECT * INTO logical FROM public.logical_document WHERE organization_id=org AND id=logical_id;
  SELECT * INTO ref FROM public.document_ref WHERE organization_id=org AND id=logical.document_ref_id;
  IF logical.id IS NULL OR NOT public.source_partition_access(org,ref.id,writing)
    OR NOT public.intake_original_export_access(org,ref.intake_original_id) THEN
    RAISE EXCEPTION 'Visual source inaccessible' USING ERRCODE='42501';END IF;
  RETURN logical;
END $$;

CREATE FUNCTION visual_extract_result(org uuid,target uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
  SELECT jsonb_build_object('id',id,'logical_document_id',logical_document_id,'document_ref_id',document_ref_id,
    'partition_revision_id',partition_revision_id,'source_sha256',source_sha256,'page_start',page_start,'page_end',page_end,
    'verified_page_count',verified_page_count,'output_page_count',output_page_count,'content_sha256',content_sha256,
    'content_byte_size',content_byte_size,'renderer_profile',renderer_profile,'reason',reason,'created_at',created_at)
  FROM public.visual_extract_artifact WHERE organization_id=org AND id=target;
$$;

CREATE FUNCTION prepare_visual_extract(org uuid,logical_id uuid,partition_id uuid,request uuid,explanation text) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE logical public.logical_document;partition public.source_partition_revision;original public.intake_original;
  prior public.visual_extract_artifact;digest text;attempt public.job_attempt;
BEGIN
  IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501';END IF;
  logical:=public.require_visual_extract_source(org,logical_id,true);
  IF request IS NULL OR partition_id IS NULL OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 1000 THEN
    RAISE EXCEPTION 'Exact visual extract command required' USING ERRCODE='22023';END IF;
  digest:=encode(sha256(convert_to(jsonb_build_array(logical_id,partition_id,explanation)::text,'UTF8')),'hex');
  SELECT * INTO prior FROM public.visual_extract_artifact WHERE organization_id=org
    AND actor_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid AND request_key=request;
  IF FOUND THEN
    IF prior.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Visual extract request changed' USING ERRCODE='23505';END IF;
    RETURN jsonb_build_object('replay',public.visual_extract_result(org,prior.id));
  END IF;
  SELECT * INTO partition FROM public.source_partition_revision WHERE organization_id=org AND document_ref_id=logical.document_ref_id ORDER BY revision_number DESC LIMIT 1;
  IF partition.id IS DISTINCT FROM partition_id THEN RAISE EXCEPTION 'Visual partition changed' USING ERRCODE='23505';END IF;
  SELECT o.* INTO original FROM public.intake_original o JOIN public.document_ref r ON r.organization_id=o.organization_id AND r.intake_original_id=o.id
    WHERE r.organization_id=org AND r.id=logical.document_ref_id;
  SELECT a.* INTO attempt FROM public.job_attempt a JOIN public.workflow_job j ON j.organization_id=a.organization_id AND j.id=a.job_id
    WHERE j.organization_id=org AND j.original_id=original.id AND j.status='clean' AND j.fence=a.fence AND a.finished_at IS NOT NULL AND a.result->>'status'='clean';
  IF original.mime_type IS DISTINCT FROM 'application/pdf' OR logical.page_start IS NULL OR logical.page_end-logical.page_start+1>20
    OR attempt.id IS NULL OR partition.verified_attempt_id IS DISTINCT FROM attempt.id OR logical.verified_attempt_id IS DISTINCT FROM attempt.id
    OR partition.content_sha256 IS DISTINCT FROM original.content_sha256 OR logical.content_sha256 IS DISTINCT FROM original.content_sha256
    OR logical.verified_page_count IS DISTINCT FROM (attempt.result->>'page_count')::integer
    OR partition.verified_page_count IS DISTINCT FROM logical.verified_page_count
    OR NOT EXISTS(SELECT 1 FROM public.source_partition_member WHERE organization_id=org AND partition_revision_id=partition.id AND logical_document_id=logical.id)
    THEN RAISE EXCEPTION 'Current inspected partition member required' USING ERRCODE='23514';END IF;
  RETURN jsonb_build_object('replay',NULL,'source_sha256',original.content_sha256,'content_byte_size',original.content_byte_size,
    'storage_uri',original.storage_uri,'page_start',logical.page_start,'page_end',logical.page_end,'verified_page_count',logical.verified_page_count,
    'logical_document_id',logical.id,'partition_revision_id',partition.id,'document_ref_id',logical.document_ref_id,
    'backend_pid',pg_backend_pid(),'transaction_id',pg_current_xact_id()::text,'request_digest',digest);
END $$;

CREATE FUNCTION attest_visual_extract(org uuid,request uuid,digest text,caller_pid integer,caller_transaction text,observed jsonb) RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;created uuid;prior public.visual_extract_attestation;
BEGIN
  IF session_user<>'app_review' OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
    OR NOT COALESCE(public.scope_actor_role(org) IN ('OWNER_ADMIN','CLERK'),false) THEN
    RAISE EXCEPTION 'Trusted visual renderer required' USING ERRCODE='42501';END IF;
  PERFORM set_config('lock_timeout','200ms',true);
  IF request IS NULL OR digest IS NULL OR digest !~ '^[0-9a-f]{64}$' OR caller_pid IS NULL OR caller_transaction IS NULL
    OR observed IS NULL OR jsonb_typeof(observed) IS DISTINCT FROM 'object'
    OR COALESCE(observed->>'content_sha256','') !~ '^[0-9a-f]{64}$'
    OR COALESCE((observed->>'content_byte_size')::bigint,0) NOT BETWEEN 1 AND 26214400
    OR COALESCE(observed->'renderer_profile'->>'version','')<>'visual-extract-v1'
    OR COALESCE(observed->>'storage_uri','')='' OR COALESCE(observed->>'storage_backend','')='' THEN
    RAISE EXCEPTION 'Exact observed derivative required' USING ERRCODE='22023';END IF;
  SELECT * INTO prior FROM public.visual_extract_attestation WHERE organization_id=org AND actor_id=actor AND request_key=request
    AND backend_pid=caller_pid AND transaction_id=caller_transaction::xid8;
  IF FOUND THEN
    IF prior.request_digest IS DISTINCT FROM digest OR prior.payload IS DISTINCT FROM observed THEN RAISE EXCEPTION 'Renderer attestation changed' USING ERRCODE='23505';END IF;
    RETURN prior.id;
  END IF;
  INSERT INTO public.visual_extract_attestation(organization_id,actor_id,request_key,request_digest,backend_pid,transaction_id,payload)
    VALUES(org,actor,request,digest,caller_pid,caller_transaction::xid8,observed) RETURNING id INTO created;
  RETURN created;
END $$;

CREATE FUNCTION complete_visual_extract(org uuid,logical_id uuid,partition_id uuid,request uuid,explanation text,proof_id uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE prepared jsonb;proof public.visual_extract_attestation;value jsonb;created uuid;
BEGIN
  prepared:=public.prepare_visual_extract(org,logical_id,partition_id,request,explanation);
  IF prepared->'replay'<>'null'::jsonb THEN RETURN prepared->'replay';END IF;
  SELECT * INTO proof FROM public.visual_extract_attestation WHERE organization_id=org AND id=proof_id;
  value:=proof.payload;
  IF proof.id IS NULL OR proof.actor_id IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid
    OR proof.request_key IS DISTINCT FROM request OR proof.request_digest IS DISTINCT FROM prepared->>'request_digest'
    OR proof.backend_pid IS DISTINCT FROM pg_backend_pid() OR proof.transaction_id IS DISTINCT FROM pg_current_xact_id()
    OR value->>'source_sha256' IS DISTINCT FROM prepared->>'source_sha256'
    OR value->>'logical_document_id' IS DISTINCT FROM logical_id::text OR value->>'partition_revision_id' IS DISTINCT FROM partition_id::text
    OR value->>'page_start' IS DISTINCT FROM prepared->>'page_start' OR value->>'page_end' IS DISTINCT FROM prepared->>'page_end'
    OR value->>'verified_page_count' IS DISTINCT FROM prepared->>'verified_page_count' THEN
    RAISE EXCEPTION 'Current transaction renderer proof required' USING ERRCODE='23514';END IF;
  INSERT INTO public.visual_extract_artifact(organization_id,logical_document_id,document_ref_id,partition_revision_id,
    source_sha256,page_start,page_end,verified_page_count,output_page_count,content_sha256,content_byte_size,renderer_profile,
    storage_uri,storage_backend,storage_version,attestation_id,actor_id,request_key,request_digest,reason)
    VALUES(org,logical_id,(prepared->>'document_ref_id')::uuid,partition_id,value->>'source_sha256',(value->>'page_start')::integer,
      (value->>'page_end')::integer,(value->>'verified_page_count')::integer,(value->>'output_page_count')::integer,
      value->>'content_sha256',(value->>'content_byte_size')::bigint,value->'renderer_profile',value->>'storage_uri',value->>'storage_backend',
      value->>'storage_version',proof.id,proof.actor_id,request,proof.request_digest,explanation) RETURNING id INTO created;
  RETURN public.visual_extract_result(org,created);
END $$;

CREATE FUNCTION visual_extract_history(org uuid,logical_id uuid,before_time timestamptz,before_id uuid,take integer) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
BEGIN
  PERFORM public.require_visual_extract_source(org,logical_id,false);
  IF take IS NULL OR take NOT BETWEEN 1 AND 50 OR (before_time IS NULL)<>(before_id IS NULL) THEN RAISE EXCEPTION 'Bounded history required' USING ERRCODE='22023';END IF;
  RETURN COALESCE((SELECT jsonb_agg(public.visual_extract_result(org,id) ORDER BY created_at DESC,id DESC) FROM
    (SELECT id,created_at FROM public.visual_extract_artifact WHERE organization_id=org AND logical_document_id=logical_id
      AND (before_time IS NULL OR (created_at,id)<(before_time,before_id)) ORDER BY created_at DESC,id DESC LIMIT take+1) rows),'[]'::jsonb);
END $$;

CREATE FUNCTION visual_extract_content(org uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE artifact public.visual_extract_artifact;
BEGIN
  SELECT * INTO artifact FROM public.visual_extract_artifact WHERE organization_id=org AND id=target;
  IF artifact.id IS NULL THEN RAISE EXCEPTION 'Visual extract inaccessible' USING ERRCODE='42501';END IF;
  PERFORM public.require_visual_extract_source(org,artifact.logical_document_id,false);
  RETURN jsonb_build_object('storage_uri',artifact.storage_uri,'content_sha256',artifact.content_sha256,'content_byte_size',artifact.content_byte_size);
END $$;

REVOKE ALL ON FUNCTION require_visual_extract_source(uuid,uuid,boolean),visual_extract_result(uuid,uuid),
  prepare_visual_extract(uuid,uuid,uuid,uuid,text),attest_visual_extract(uuid,uuid,text,integer,text,jsonb),
  complete_visual_extract(uuid,uuid,uuid,uuid,text,uuid),visual_extract_history(uuid,uuid,timestamptz,uuid,integer),visual_extract_content(uuid,uuid)
  FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION prepare_visual_extract(uuid,uuid,uuid,uuid,text),complete_visual_extract(uuid,uuid,uuid,uuid,text,uuid),
  visual_extract_history(uuid,uuid,timestamptz,uuid,integer),visual_extract_content(uuid,uuid) TO app_rw;
GRANT EXECUTE ON FUNCTION attest_visual_extract(uuid,uuid,text,integer,text,jsonb) TO app_review;
"""
