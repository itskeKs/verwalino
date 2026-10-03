"""Explicit destination disclosure and withdrawal of exact visual derivatives."""
from alembic import op
from sqlalchemy import text

revision='0118'
down_revision='0117'
branch_labels=None
depends_on=None
BEFORE="UNION ALL SELECT 'visual_extract_attestation',id,to_jsonb(r) FROM public.visual_extract_attestation r WHERE organization_id=org) evidence"
AFTER=BEFORE.replace(') evidence'," UNION ALL SELECT 'visual_disclosure_event',id,to_jsonb(r) FROM public.visual_disclosure_event r WHERE organization_id=org UNION ALL SELECT 'visual_disclosure_attestation',id,to_jsonb(r) FROM public.visual_disclosure_attestation r WHERE organization_id=org) evidence")


def coverage(reverse=False):
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    before,after=(AFTER,BEFORE) if reverse else (BEFORE,AFTER)
    assert definition.count(before)==1
    op.execute(definition.replace(before,after))


def upgrade():
    op.execute("""
    CREATE TABLE visual_disclosure_attestation(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,artifact_id uuid NOT NULL,
      actor_id uuid NOT NULL,request_key uuid NOT NULL,request_digest text NOT NULL,backend_pid integer NOT NULL,
      transaction_id xid8 NOT NULL,content_sha256 text NOT NULL,content_byte_size bigint NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(organization_id,id),
      UNIQUE(organization_id,actor_id,request_key,backend_pid,transaction_id),
      FOREIGN KEY(organization_id,artifact_id) REFERENCES visual_extract_artifact(organization_id,id),
      FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
    CREATE TABLE visual_disclosure_event(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,artifact_id uuid NOT NULL,scope_id uuid NOT NULL,
      previous_event_id uuid,revision_number integer NOT NULL CHECK(revision_number BETWEEN 1 AND 100),
      kind text NOT NULL CHECK(kind IN ('published','withdrawn')),actor_id uuid NOT NULL,request_key uuid NOT NULL,request_digest text NOT NULL,
      reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 1000),visible_content_reviewed boolean NOT NULL,
      attestation_id uuid,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CHECK((kind='published' AND visible_content_reviewed AND attestation_id IS NOT NULL)
        OR (kind='withdrawn' AND NOT visible_content_reviewed AND attestation_id IS NULL)),
      UNIQUE(organization_id,id),UNIQUE(organization_id,artifact_id,scope_id,id),
      UNIQUE(organization_id,actor_id,request_key),UNIQUE(organization_id,attestation_id),
      UNIQUE(organization_id,artifact_id,scope_id,revision_number),
      UNIQUE NULLS NOT DISTINCT(organization_id,artifact_id,scope_id,previous_event_id),
      FOREIGN KEY(organization_id,artifact_id) REFERENCES visual_extract_artifact(organization_id,id),
      FOREIGN KEY(organization_id,scope_id) REFERENCES accounting_scope(organization_id,id),
      FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id),
      FOREIGN KEY(organization_id,attestation_id) REFERENCES visual_disclosure_attestation(organization_id,id),
      FOREIGN KEY(organization_id,artifact_id,scope_id,previous_event_id) REFERENCES visual_disclosure_event(organization_id,artifact_id,scope_id,id));
    CREATE INDEX visual_disclosure_scope_history ON visual_disclosure_event(organization_id,scope_id,created_at DESC,id DESC);
    CREATE INDEX visual_disclosure_source_history ON visual_disclosure_event(organization_id,artifact_id,created_at DESC,id DESC);
    """)
    for table in ('visual_disclosure_attestation','visual_disclosure_event'):
        op.execute(f"""ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review;
          CREATE POLICY visual_disclosure_private ON {table} USING(false) WITH CHECK(false);
          CREATE TRIGGER visual_disclosure_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();""")
    op.execute(HELPERS)
    op.execute(COMMANDS)
    op.execute(READERS)
    coverage()


def downgrade():
    op.execute("""LOCK TABLE visual_disclosure_event,visual_disclosure_attestation IN ACCESS EXCLUSIVE MODE NOWAIT;
      DO $$ BEGIN IF EXISTS(SELECT 1 FROM visual_disclosure_event) OR EXISTS(SELECT 1 FROM visual_disclosure_attestation) THEN
        RAISE EXCEPTION 'Retain visual disclosure evidence; repair forward';END IF;END $$;""")
    coverage(reverse=True)
    op.execute("""DROP FUNCTION visual_disclosure_outgoing(uuid,timestamptz,uuid,integer),visual_disclosure_list(uuid,uuid,uuid,timestamptz,uuid,integer),visual_disclosure_detail(uuid,uuid),
      visual_disclosure_content(uuid,uuid),visual_disclosure_target(uuid,uuid,uuid),
      withdraw_visual_disclosure(uuid,uuid,uuid,uuid,text,boolean),publish_visual_disclosure(uuid,uuid,uuid,uuid,uuid,text,uuid,text,boolean,uuid),
      attest_visual_disclosure(uuid,uuid,uuid,text,integer,text,text,bigint),prepare_visual_disclosure(uuid,uuid,uuid,uuid,uuid,text,uuid,text,boolean),
      visual_disclosure_result(uuid,uuid),require_visual_disclosure_publish(uuid,uuid,uuid,boolean),
      visual_disclosure_source_access(uuid,uuid),visual_disclosure_withdraw_access(uuid,uuid,uuid),visual_disclosure_lock(uuid);
      DROP TABLE visual_disclosure_event,visual_disclosure_attestation;""")


HELPERS=r"""
CREATE FUNCTION visual_disclosure_lock(org uuid) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
BEGIN
  IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501';END IF;
  PERFORM public.lock_intake(org);
  IF public.scope_actor_role(org) IS NULL THEN RAISE EXCEPTION 'Live disclosure principal required' USING ERRCODE='42501';END IF;
END $$;

CREATE FUNCTION visual_disclosure_source_access(org uuid,artifact_id uuid) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
  -- Withdrawal and opaque source management must survive a changed inspection.
  -- New publication separately requires the current clean inspection basis.
  SELECT COALESCE((SELECT public.intake_original_access(org,r.intake_original_id,false)
    AND (b.scope_hint_id IS NULL OR public.has_scope_capability(org,b.scope_hint_id,'export'))
    FROM public.visual_extract_artifact a JOIN public.document_ref r ON r.organization_id=a.organization_id AND r.id=a.document_ref_id
    JOIN public.intake_original o ON o.organization_id=r.organization_id AND o.id=r.intake_original_id
    JOIN public.intake_item i ON i.organization_id=o.organization_id AND i.id=o.item_id
    JOIN public.intake_batch b ON b.organization_id=i.organization_id AND b.id=i.batch_id
    WHERE a.organization_id=org AND a.id=artifact_id),false);
$$;

CREATE FUNCTION visual_disclosure_withdraw_access(org uuid,artifact_id uuid,destination uuid) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
  SELECT COALESCE(public.scope_actor_role(org) IN ('OWNER_ADMIN','CLERK'),false) AND
    (public.visual_disclosure_source_access(org,artifact_id)
      OR (public.has_scope_capability(org,destination,'read') AND public.has_scope_capability(org,destination,'approve')));
$$;

CREATE FUNCTION require_visual_disclosure_publish(org uuid,target uuid,destination uuid,current_basis boolean) RETURNS public.visual_extract_artifact
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE artifact public.visual_extract_artifact;logical public.logical_document;partition public.source_partition_revision;ref public.document_ref;attempt public.job_attempt;
BEGIN
  PERFORM public.visual_disclosure_lock(org);
  SELECT * INTO artifact FROM public.visual_extract_artifact WHERE organization_id=org AND id=target;
  IF artifact.id IS NULL OR NOT public.visual_disclosure_source_access(org,target)
    OR NOT EXISTS(SELECT 1 FROM public.document_ref r JOIN public.intake_original o ON o.organization_id=r.organization_id AND o.id=r.intake_original_id
      JOIN public.intake_item i ON i.organization_id=o.organization_id AND i.id=o.item_id
      WHERE r.organization_id=org AND r.id=artifact.document_ref_id AND public.intake_batch_access(org,i.batch_id,true))
    OR NOT public.has_scope_capability(org,destination,'read') OR NOT public.has_scope_capability(org,destination,'prepare')
    OR NOT public.has_scope_capability(org,destination,'approve') OR NOT public.has_scope_capability(org,destination,'export') THEN
    RAISE EXCEPTION 'Visual disclosure inaccessible' USING ERRCODE='42501';END IF;
  IF current_basis THEN
    SELECT * INTO logical FROM public.logical_document WHERE organization_id=org AND id=artifact.logical_document_id;
    SELECT * INTO partition FROM public.source_partition_revision WHERE organization_id=org AND document_ref_id=artifact.document_ref_id ORDER BY revision_number DESC LIMIT 1;
    SELECT * INTO ref FROM public.document_ref WHERE organization_id=org AND id=artifact.document_ref_id;
    SELECT a.* INTO attempt FROM public.job_attempt a JOIN public.workflow_job j ON j.organization_id=a.organization_id AND j.id=a.job_id
      WHERE j.organization_id=org AND j.original_id=ref.intake_original_id AND j.status='clean' AND j.fence=a.fence
        AND a.finished_at IS NOT NULL AND a.result->>'status'='clean';
    IF partition.id IS DISTINCT FROM artifact.partition_revision_id OR partition.verified_attempt_id IS DISTINCT FROM attempt.id
      OR attempt.id IS NULL OR logical.verified_attempt_id IS DISTINCT FROM attempt.id
      OR artifact.source_sha256 IS DISTINCT FROM ref.content_sha256 OR artifact.source_sha256 IS DISTINCT FROM logical.content_sha256
      OR artifact.verified_page_count IS DISTINCT FROM (attempt.result->>'page_count')::integer
      OR NOT EXISTS(SELECT 1 FROM public.source_partition_member WHERE organization_id=org AND partition_revision_id=partition.id AND logical_document_id=logical.id) THEN
      RAISE EXCEPTION 'Visual disclosure source basis changed' USING ERRCODE='23505';END IF;
  END IF;
  RETURN artifact;
END $$;

CREATE FUNCTION visual_disclosure_result(org uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE chosen public.visual_disclosure_event;head public.visual_disclosure_event;artifact public.visual_extract_artifact;
BEGIN
  SELECT * INTO chosen FROM public.visual_disclosure_event WHERE organization_id=org AND id=target;
  SELECT * INTO head FROM public.visual_disclosure_event WHERE organization_id=org AND artifact_id=chosen.artifact_id AND scope_id=chosen.scope_id ORDER BY revision_number DESC LIMIT 1;
  SELECT * INTO artifact FROM public.visual_extract_artifact WHERE organization_id=org AND id=chosen.artifact_id;
  RETURN jsonb_build_object('destination',CASE WHEN public.has_scope_capability(org,chosen.scope_id,'read')
      THEN (SELECT jsonb_build_object('id',id,'label',label,'scope_type',scope_type) FROM public.accounting_scope WHERE organization_id=org AND id=chosen.scope_id) ELSE NULL END,
    'receipt',jsonb_build_object('id',chosen.id,'kind',chosen.kind,
    'scope_id',CASE WHEN public.has_scope_capability(org,chosen.scope_id,'read') THEN chosen.scope_id ELSE NULL END,
    'created_at',chosen.created_at,'content_sha256',artifact.content_sha256,'content_byte_size',artifact.content_byte_size,'output_page_count',artifact.output_page_count),
    'current',jsonb_build_object('id',head.id,'kind',head.kind,'available',head.id=chosen.id AND head.kind='published',
      'can_download',head.id=chosen.id AND head.kind='published' AND public.has_scope_capability(org,chosen.scope_id,'read') AND public.has_scope_capability(org,chosen.scope_id,'export'),
      'can_withdraw',head.kind='published' AND public.visual_disclosure_withdraw_access(org,head.artifact_id,head.scope_id)));
END $$;

REVOKE ALL ON FUNCTION visual_disclosure_lock(uuid),visual_disclosure_source_access(uuid,uuid),visual_disclosure_withdraw_access(uuid,uuid,uuid),
  require_visual_disclosure_publish(uuid,uuid,uuid,boolean),visual_disclosure_result(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
"""

COMMANDS=r"""
CREATE FUNCTION prepare_visual_disclosure(org uuid,target uuid,destination uuid,partition_id uuid,expected uuid,observed_hash text,request uuid,explanation text,reviewed boolean) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE artifact public.visual_extract_artifact;head public.visual_disclosure_event;prior public.visual_disclosure_event;digest text;
BEGIN
  artifact:=public.require_visual_disclosure_publish(org,target,destination,false);
  IF request IS NULL OR partition_id IS DISTINCT FROM artifact.partition_revision_id OR observed_hash IS DISTINCT FROM artifact.content_sha256
    OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 1000 OR reviewed IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Exact destination visual review required' USING ERRCODE='22023';END IF;
  digest:=encode(sha256(convert_to(jsonb_build_array('publish',target,destination,partition_id,expected,observed_hash,explanation,reviewed)::text,'UTF8')),'hex');
  SELECT * INTO prior FROM public.visual_disclosure_event WHERE organization_id=org AND actor_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid AND request_key=request;
  IF FOUND THEN
    IF prior.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Disclosure request changed' USING ERRCODE='23505';END IF;
    RETURN jsonb_build_object('replay',public.visual_disclosure_result(org,prior.id));
  END IF;
  artifact:=public.require_visual_disclosure_publish(org,target,destination,true);
  SELECT * INTO head FROM public.visual_disclosure_event WHERE organization_id=org AND artifact_id=target AND scope_id=destination ORDER BY revision_number DESC LIMIT 1;
  IF head.id IS DISTINCT FROM expected OR head.kind='published' OR COALESCE(head.revision_number,0)>=99 THEN
    RAISE EXCEPTION 'Disclosure head changed or exhausted' USING ERRCODE='23505';END IF;
  RETURN jsonb_build_object('replay',NULL,'content_sha256',artifact.content_sha256,'content_byte_size',artifact.content_byte_size,'storage_uri',artifact.storage_uri,
    'request_digest',digest,'backend_pid',pg_backend_pid(),'transaction_id',pg_current_xact_id()::text);
END $$;

CREATE FUNCTION attest_visual_disclosure(org uuid,target uuid,request uuid,digest text,caller_pid integer,caller_transaction text,observed_hash text,observed_size bigint) RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE artifact public.visual_extract_artifact;prior public.visual_disclosure_attestation;created uuid;
  actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
BEGIN
  IF session_user<>'app_review' OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
    OR NOT COALESCE(public.scope_actor_role(org) IN ('OWNER_ADMIN','CLERK'),false) THEN
    RAISE EXCEPTION 'Trusted disclosure byte verifier required' USING ERRCODE='42501';END IF;
  PERFORM set_config('lock_timeout','200ms',true);
  SELECT * INTO artifact FROM public.visual_extract_artifact WHERE organization_id=org AND id=target;
  IF artifact.id IS NULL OR artifact.content_sha256 IS DISTINCT FROM observed_hash OR artifact.content_byte_size IS DISTINCT FROM observed_size
    OR request IS NULL OR digest IS NULL OR digest !~ '^[0-9a-f]{64}$' OR caller_pid IS NULL OR caller_transaction IS NULL THEN
    RAISE EXCEPTION 'Exact stored visual bytes required' USING ERRCODE='22023';END IF;
  SELECT * INTO prior FROM public.visual_disclosure_attestation WHERE organization_id=org AND actor_id=actor AND request_key=request
    AND backend_pid=caller_pid AND transaction_id=caller_transaction::xid8;
  IF FOUND THEN
    IF prior.request_digest IS DISTINCT FROM digest OR prior.artifact_id IS DISTINCT FROM target THEN RAISE EXCEPTION 'Disclosure proof changed' USING ERRCODE='23505';END IF;
    RETURN prior.id;
  END IF;
  INSERT INTO public.visual_disclosure_attestation(organization_id,artifact_id,actor_id,request_key,request_digest,backend_pid,transaction_id,content_sha256,content_byte_size)
    VALUES(org,target,actor,request,digest,caller_pid,caller_transaction::xid8,observed_hash,observed_size) RETURNING id INTO created;
  RETURN created;
END $$;

CREATE FUNCTION publish_visual_disclosure(org uuid,target uuid,destination uuid,partition_id uuid,expected uuid,observed_hash text,request uuid,explanation text,reviewed boolean,proof_id uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE prepared jsonb;proof public.visual_disclosure_attestation;head public.visual_disclosure_event;artifact public.visual_extract_artifact;created uuid;
BEGIN
  prepared:=public.prepare_visual_disclosure(org,target,destination,partition_id,expected,observed_hash,request,explanation,reviewed);
  IF prepared->'replay'<>'null'::jsonb THEN RETURN prepared->'replay';END IF;
  SELECT * INTO proof FROM public.visual_disclosure_attestation WHERE organization_id=org AND id=proof_id;
  SELECT * INTO artifact FROM public.visual_extract_artifact WHERE organization_id=org AND id=target;
  IF proof.id IS NULL OR proof.actor_id IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid
    OR proof.artifact_id IS DISTINCT FROM target OR proof.request_key IS DISTINCT FROM request
    OR proof.request_digest IS DISTINCT FROM prepared->>'request_digest' OR proof.backend_pid IS DISTINCT FROM pg_backend_pid()
    OR proof.transaction_id IS DISTINCT FROM pg_current_xact_id() OR proof.content_sha256 IS DISTINCT FROM artifact.content_sha256
    OR proof.content_byte_size IS DISTINCT FROM artifact.content_byte_size THEN
    RAISE EXCEPTION 'Current transaction disclosure proof required' USING ERRCODE='23514';END IF;
  SELECT * INTO head FROM public.visual_disclosure_event WHERE organization_id=org AND artifact_id=target AND scope_id=destination ORDER BY revision_number DESC LIMIT 1;
  INSERT INTO public.visual_disclosure_event(organization_id,artifact_id,scope_id,previous_event_id,revision_number,kind,actor_id,request_key,request_digest,reason,visible_content_reviewed,attestation_id)
    VALUES(org,target,destination,expected,COALESCE(head.revision_number,0)+1,'published',proof.actor_id,request,proof.request_digest,explanation,true,proof.id) RETURNING id INTO created;
  RETURN public.visual_disclosure_result(org,created);
END $$;

CREATE FUNCTION withdraw_visual_disclosure(org uuid,target uuid,expected uuid,request uuid,explanation text,confirmed boolean) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE chosen public.visual_disclosure_event;head public.visual_disclosure_event;prior public.visual_disclosure_event;digest text;created uuid;
  actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
BEGIN
  PERFORM public.visual_disclosure_lock(org);
  SELECT * INTO chosen FROM public.visual_disclosure_event WHERE organization_id=org AND id=target;
  IF chosen.id IS NULL OR NOT public.visual_disclosure_withdraw_access(org,chosen.artifact_id,chosen.scope_id) THEN
    RAISE EXCEPTION 'Disclosure withdrawal inaccessible' USING ERRCODE='42501';END IF;
  IF request IS NULL OR expected IS DISTINCT FROM target OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 1000 OR confirmed IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Explicit current withdrawal required' USING ERRCODE='22023';END IF;
  digest:=encode(sha256(convert_to(jsonb_build_array('withdraw',target,expected,explanation,confirmed)::text,'UTF8')),'hex');
  SELECT * INTO prior FROM public.visual_disclosure_event WHERE organization_id=org AND actor_id=actor AND request_key=request;
  IF FOUND THEN
    IF prior.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Withdrawal request changed' USING ERRCODE='23505';END IF;
    RETURN public.visual_disclosure_result(org,prior.id);
  END IF;
  SELECT * INTO head FROM public.visual_disclosure_event WHERE organization_id=org AND artifact_id=chosen.artifact_id AND scope_id=chosen.scope_id ORDER BY revision_number DESC LIMIT 1;
  IF head.id IS DISTINCT FROM expected OR head.kind<>'published' THEN RAISE EXCEPTION 'Withdrawal head changed' USING ERRCODE='23505';END IF;
  INSERT INTO public.visual_disclosure_event(organization_id,artifact_id,scope_id,previous_event_id,revision_number,kind,actor_id,request_key,request_digest,reason,visible_content_reviewed)
    VALUES(org,head.artifact_id,head.scope_id,head.id,head.revision_number+1,'withdrawn',actor,request,digest,explanation,false) RETURNING id INTO created;
  RETURN public.visual_disclosure_result(org,created);
END $$;

REVOKE ALL ON FUNCTION prepare_visual_disclosure(uuid,uuid,uuid,uuid,uuid,text,uuid,text,boolean),
  attest_visual_disclosure(uuid,uuid,uuid,text,integer,text,text,bigint),publish_visual_disclosure(uuid,uuid,uuid,uuid,uuid,text,uuid,text,boolean,uuid),
  withdraw_visual_disclosure(uuid,uuid,uuid,uuid,text,boolean) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION prepare_visual_disclosure(uuid,uuid,uuid,uuid,uuid,text,uuid,text,boolean),
  publish_visual_disclosure(uuid,uuid,uuid,uuid,uuid,text,uuid,text,boolean,uuid),withdraw_visual_disclosure(uuid,uuid,uuid,uuid,text,boolean) TO app_rw;
GRANT EXECUTE ON FUNCTION attest_visual_disclosure(uuid,uuid,uuid,text,integer,text,text,bigint) TO app_review;
"""
READERS=r"""
CREATE FUNCTION visual_disclosure_target(org uuid,target uuid,destination uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE artifact public.visual_extract_artifact;head uuid;
BEGIN
  artifact:=public.require_visual_disclosure_publish(org,target,destination,true);
  SELECT id INTO head FROM public.visual_disclosure_event WHERE organization_id=org AND artifact_id=target AND scope_id=destination ORDER BY revision_number DESC LIMIT 1;
  RETURN jsonb_build_object('current',CASE WHEN head IS NOT NULL THEN public.visual_disclosure_result(org,head) ELSE NULL END,
    'partition_revision_id',artifact.partition_revision_id,'content_sha256',artifact.content_sha256,'output_page_count',artifact.output_page_count);
END $$;

CREATE FUNCTION visual_disclosure_detail(org uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE chosen public.visual_disclosure_event;
BEGIN
  PERFORM public.visual_disclosure_lock(org);
  SELECT * INTO chosen FROM public.visual_disclosure_event WHERE organization_id=org AND id=target;
  IF chosen.id IS NULL OR NOT (public.has_scope_capability(org,chosen.scope_id,'read') OR public.visual_disclosure_source_access(org,chosen.artifact_id)) THEN
    RAISE EXCEPTION 'Disclosure inaccessible' USING ERRCODE='42501';END IF;
  RETURN public.visual_disclosure_result(org,target);
END $$;

CREATE FUNCTION visual_disclosure_content(org uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE chosen public.visual_disclosure_event;head public.visual_disclosure_event;artifact public.visual_extract_artifact;
BEGIN
  PERFORM public.visual_disclosure_lock(org);
  SELECT * INTO chosen FROM public.visual_disclosure_event WHERE organization_id=org AND id=target;
  IF chosen.id IS NULL OR NOT public.has_scope_capability(org,chosen.scope_id,'read') OR NOT public.has_scope_capability(org,chosen.scope_id,'export') THEN
    RAISE EXCEPTION 'Disclosure content inaccessible' USING ERRCODE='42501';END IF;
  SELECT * INTO head FROM public.visual_disclosure_event WHERE organization_id=org AND artifact_id=chosen.artifact_id AND scope_id=chosen.scope_id ORDER BY revision_number DESC LIMIT 1;
  IF head.id IS DISTINCT FROM target OR head.kind<>'published' THEN RAISE EXCEPTION 'Disclosure no longer available' USING ERRCODE='42501';END IF;
  SELECT * INTO artifact FROM public.visual_extract_artifact WHERE organization_id=org AND id=chosen.artifact_id;
  RETURN jsonb_build_object('storage_uri',artifact.storage_uri,'content_sha256',artifact.content_sha256,'content_byte_size',artifact.content_byte_size);
END $$;

CREATE FUNCTION visual_disclosure_list(org uuid,destination uuid,source_artifact uuid,before_time timestamptz,before_id uuid,take integer) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
BEGIN
  PERFORM public.visual_disclosure_lock(org);
  IF (destination IS NULL)=(source_artifact IS NULL) OR take IS NULL OR take NOT BETWEEN 1 AND 50
    OR (before_time IS NULL)<>(before_id IS NULL) THEN RAISE EXCEPTION 'Bounded disclosure list required' USING ERRCODE='22023';END IF;
  IF (destination IS NOT NULL AND NOT public.has_scope_capability(org,destination,'read'))
    OR (source_artifact IS NOT NULL AND NOT public.visual_disclosure_source_access(org,source_artifact)) THEN
    RAISE EXCEPTION 'Disclosure list inaccessible' USING ERRCODE='42501';END IF;
  RETURN COALESCE((SELECT jsonb_agg(public.visual_disclosure_result(org,id) ORDER BY created_at DESC,id DESC) FROM
    (SELECT e.id,e.created_at FROM public.visual_disclosure_event e WHERE e.organization_id=org
      AND (destination IS NULL OR e.scope_id=destination) AND (source_artifact IS NULL OR e.artifact_id=source_artifact)
      AND (source_artifact IS NOT NULL OR e.kind='published')
      AND NOT EXISTS(SELECT 1 FROM public.visual_disclosure_event later WHERE later.organization_id=e.organization_id AND later.previous_event_id=e.id)
      AND (before_time IS NULL OR (e.created_at,e.id)<(before_time,before_id)) ORDER BY e.created_at DESC,e.id DESC LIMIT take+1) heads),'[]'::jsonb);
END $$;

CREATE FUNCTION visual_disclosure_outgoing(org uuid,before_time timestamptz,before_id uuid,take integer) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
BEGIN
  PERFORM public.visual_disclosure_lock(org);
  IF take IS NULL OR take NOT BETWEEN 1 AND 50 OR (before_time IS NULL)<>(before_id IS NULL) THEN
    RAISE EXCEPTION 'Bounded outgoing disclosure list required' USING ERRCODE='22023';END IF;
  RETURN COALESCE((SELECT jsonb_agg(public.visual_disclosure_result(org,id) ORDER BY created_at DESC,id DESC) FROM
    (SELECT e.id,e.created_at FROM public.visual_disclosure_event e JOIN public.visual_extract_artifact a ON a.organization_id=e.organization_id AND a.id=e.artifact_id
      WHERE e.organization_id=org AND a.actor_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid
      AND public.visual_disclosure_source_access(org,a.id)
      AND NOT EXISTS(SELECT 1 FROM public.visual_disclosure_event later WHERE later.organization_id=e.organization_id AND later.previous_event_id=e.id)
      AND (before_time IS NULL OR (e.created_at,e.id)<(before_time,before_id)) ORDER BY e.created_at DESC,e.id DESC LIMIT take+1) heads),'[]'::jsonb);
END $$;

REVOKE ALL ON FUNCTION visual_disclosure_outgoing(uuid,timestamptz,uuid,integer),visual_disclosure_target(uuid,uuid,uuid),visual_disclosure_detail(uuid,uuid),visual_disclosure_content(uuid,uuid),
  visual_disclosure_list(uuid,uuid,uuid,timestamptz,uuid,integer) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION visual_disclosure_outgoing(uuid,timestamptz,uuid,integer),visual_disclosure_target(uuid,uuid,uuid),visual_disclosure_detail(uuid,uuid),visual_disclosure_content(uuid,uuid),
  visual_disclosure_list(uuid,uuid,uuid,timestamptz,uuid,integer) TO app_rw;
"""
