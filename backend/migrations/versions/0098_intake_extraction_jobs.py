"""Durable logical-source extraction with immutable located proposal history."""
from alembic import op
from sqlalchemy import text

revision='0098'
down_revision='0097'
branch_labels=None
depends_on=None
BEFORE="UNION ALL SELECT 'delivery_attempt',id,to_jsonb(r) FROM public.outbox_delivery_attempt r WHERE organization_id=org) evidence"
AFTER="UNION ALL SELECT 'delivery_attempt',id,to_jsonb(r) FROM public.outbox_delivery_attempt r WHERE organization_id=org UNION ALL SELECT 'intake_extraction_job',id,to_jsonb(r) FROM public.intake_extraction_job r WHERE organization_id=org UNION ALL SELECT 'intake_extraction_attempt',id,to_jsonb(r) FROM public.intake_extraction_attempt r WHERE organization_id=org UNION ALL SELECT 'intake_extraction_retry',id,to_jsonb(r) FROM public.intake_extraction_retry r WHERE organization_id=org) evidence"


def upgrade():
    op.execute('''
    CREATE TABLE intake_extraction_job(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),logical_document_id uuid NOT NULL,
      initiated_by uuid NOT NULL,scope_hint_id uuid,status text NOT NULL DEFAULT 'queued'
        CHECK(status IN ('queued','running','succeeded','needs_review','unsupported','unavailable','timeout','failed','canceled','blocked')),
      attempt_number integer NOT NULL DEFAULT 0 CHECK(attempt_number BETWEEN 0 AND 3),fence uuid,lease_expires_at timestamptz,error_code text,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(organization_id,id),UNIQUE(organization_id,logical_document_id),
      FOREIGN KEY(organization_id,logical_document_id) REFERENCES logical_document(organization_id,id),
      FOREIGN KEY(organization_id,initiated_by) REFERENCES "user"(organization_id,id),
      FOREIGN KEY(organization_id,scope_hint_id) REFERENCES accounting_scope(organization_id,id));
    CREATE INDEX ix_intake_extraction_claim ON intake_extraction_job(organization_id,status,created_at,id);
    CREATE TABLE intake_extraction_attempt(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),job_id uuid NOT NULL,
      attempt_number integer NOT NULL CHECK(attempt_number BETWEEN 1 AND 3),fence uuid NOT NULL,
      started_at timestamptz NOT NULL DEFAULT clock_timestamp(),finished_at timestamptz,result jsonb,
      UNIQUE(organization_id,id),UNIQUE(organization_id,job_id,attempt_number),UNIQUE(organization_id,job_id,fence),
      FOREIGN KEY(organization_id,job_id) REFERENCES intake_extraction_job(organization_id,id));
    CREATE TABLE intake_extraction_retry(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),job_id uuid NOT NULL,
      actor_id uuid NOT NULL,request_key uuid NOT NULL,prior_fence uuid NOT NULL,reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 1000),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(organization_id,actor_id,request_key),
      FOREIGN KEY(organization_id,job_id) REFERENCES intake_extraction_job(organization_id,id),
      FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
    CREATE TRIGGER protect_extraction_retry BEFORE UPDATE OR DELETE ON intake_extraction_retry FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();
    ALTER TABLE extraction_revision ALTER COLUMN invoice_attempt_id DROP NOT NULL;
    ALTER TABLE extraction_revision ADD COLUMN intake_attempt_id uuid,ADD COLUMN logical_document_id uuid,
      ADD UNIQUE(organization_id,intake_attempt_id),
      ADD FOREIGN KEY(organization_id,intake_attempt_id) REFERENCES intake_extraction_attempt(organization_id,id),
      ADD FOREIGN KEY(organization_id,logical_document_id) REFERENCES logical_document(organization_id,id);
    ALTER TABLE extraction_revision DROP CONSTRAINT extraction_revision_processor_version_check;
    ALTER TABLE extraction_revision ADD CONSTRAINT extraction_revision_typed_attempt CHECK(
      (invoice_attempt_id IS NOT NULL AND intake_attempt_id IS NULL AND logical_document_id IS NULL AND processor_version='invoice-local-v1') OR
      (invoice_attempt_id IS NULL AND intake_attempt_id IS NOT NULL AND logical_document_id IS NOT NULL AND processor_version='intake-local-v1'));
    ALTER TABLE field_evidence DROP CONSTRAINT field_evidence_locator_status_check;
    ALTER TABLE field_evidence DROP CONSTRAINT field_evidence_page_number_check;
    ALTER TABLE field_evidence DROP CONSTRAINT field_evidence_quotation_check;
    ALTER TABLE field_evidence DROP CONSTRAINT field_evidence_xml_path_check;
    ALTER TABLE field_evidence ADD COLUMN method text NOT NULL DEFAULT 'unlocated';
    ALTER TABLE field_evidence ADD CONSTRAINT field_evidence_locator CHECK(
      (locator_status='unlocated' AND page_number IS NULL AND quotation IS NULL AND xml_path IS NULL AND method='unlocated') OR
      (locator_status='page_quote' AND page_number IS NOT NULL AND page_number BETWEEN 1 AND 200 AND quotation IS NOT NULL AND length(quotation) BETWEEN 1 AND 4000 AND xml_path IS NULL AND method IN ('text_layer','ocr')) OR
      (locator_status='xml_path' AND page_number IS NULL AND quotation IS NOT NULL AND length(quotation) BETWEEN 1 AND 4000 AND xml_path IS NOT NULL AND length(xml_path) BETWEEN 3 AND 16000 AND method='xml'));
    ''')
    for table in ('intake_extraction_job','intake_extraction_attempt','intake_extraction_retry'):
        op.execute(f'''ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review;
          CREATE POLICY extraction_private ON {table} AS RESTRICTIVE FOR ALL USING(false) WITH CHECK(false);''')
    op.execute('''
    CREATE FUNCTION enqueue_logical_extraction() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      INSERT INTO public.intake_extraction_job(organization_id,logical_document_id,initiated_by,scope_hint_id)
        SELECT NEW.organization_id,NEW.id,NEW.created_by,b.scope_hint_id FROM public.document_ref r
        JOIN public.intake_original o ON o.organization_id=r.organization_id AND o.id=r.intake_original_id
        JOIN public.intake_item i ON i.organization_id=o.organization_id AND i.id=o.item_id
        JOIN public.intake_batch b ON b.organization_id=i.organization_id AND b.id=i.batch_id
        WHERE r.organization_id=NEW.organization_id AND r.id=NEW.document_ref_id;
      IF NOT FOUND THEN RAISE EXCEPTION 'Verified intake source required' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION enqueue_logical_extraction() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER enqueue_logical_extraction AFTER INSERT ON logical_document FOR EACH ROW EXECUTE FUNCTION enqueue_logical_extraction();
    INSERT INTO intake_extraction_job(organization_id,logical_document_id,initiated_by,scope_hint_id)
      SELECT l.organization_id,l.id,l.created_by,b.scope_hint_id FROM logical_document l
      JOIN document_ref r ON r.organization_id=l.organization_id AND r.id=l.document_ref_id
      JOIN intake_original o ON o.organization_id=r.organization_id AND o.id=r.intake_original_id
      JOIN intake_item i ON i.organization_id=o.organization_id AND i.id=o.item_id
      JOIN intake_batch b ON b.organization_id=i.organization_id AND b.id=i.batch_id;

    CREATE FUNCTION intake_extraction_context(org uuid,job uuid,token uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE work public.intake_extraction_job; logical public.logical_document; source public.intake_original; attempt uuid;
    BEGIN
      IF session_user<>'app_worker' THEN RAISE EXCEPTION 'Extraction runtime required' USING ERRCODE='42501'; END IF;
      PERFORM public.lock_intake(org);
      SELECT * INTO work FROM public.intake_extraction_job WHERE organization_id=org AND id=job FOR UPDATE;
      IF NOT FOUND OR work.status<>'running' OR token IS DISTINCT FROM work.fence OR token IS NULL OR work.lease_expires_at<=clock_timestamp() THEN RETURN NULL; END IF;
      SELECT * INTO logical FROM public.logical_document WHERE organization_id=org AND id=work.logical_document_id;
      SELECT o.* INTO source FROM public.intake_original o JOIN public.document_ref r ON r.organization_id=o.organization_id AND r.intake_original_id=o.id
        WHERE r.organization_id=org AND r.id=logical.document_ref_id;
      PERFORM set_config('app.current_user_id',work.initiated_by::text,true);
      PERFORM set_config('app.current_scope_id',COALESCE(work.scope_hint_id::text,''),true);
      IF NOT public.intake_actor_access(org,work.initiated_by,work.scope_hint_id,true) OR NOT public.intake_original_access(org,source.id,true) THEN
        UPDATE public.intake_extraction_job SET status='canceled',error_code='initiator_permission_changed',lease_expires_at=NULL,updated_at=clock_timestamp() WHERE id=job;
        UPDATE public.intake_extraction_attempt SET finished_at=clock_timestamp(),result=jsonb_build_object('status','canceled','error_code','initiator_permission_changed')
          WHERE organization_id=org AND job_id=job AND fence=token AND finished_at IS NULL;
        RETURN NULL;
      END IF;
      SELECT id INTO attempt FROM public.intake_extraction_attempt WHERE organization_id=org AND job_id=job AND fence=token AND finished_at IS NULL;
      IF attempt IS NULL THEN RAISE EXCEPTION 'Live attempt missing' USING ERRCODE='23514'; END IF;
      RETURN jsonb_build_object('job_id',job,'fence',token,'attempt_id',attempt,'logical_document_id',logical.id,'document_ref_id',logical.document_ref_id,
        'storage_uri',source.storage_uri,'content_sha256',source.content_sha256,'content_byte_size',source.content_byte_size,'mime_type',source.mime_type,
        'page_start',logical.page_start,'page_end',logical.page_end,'verified_page_count',logical.verified_page_count);
    END $$;
    CREATE FUNCTION claim_intake_extraction(org uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE work public.intake_extraction_job; token uuid:=gen_random_uuid();
    BEGIN
      IF session_user<>'app_worker' THEN RAISE EXCEPTION 'Extraction runtime required' USING ERRCODE='42501'; END IF;
      PERFORM public.lock_intake(org);
      SELECT * INTO work FROM public.intake_extraction_job WHERE organization_id=org AND
        (status='queued' OR status='running' AND lease_expires_at<=clock_timestamp()) ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED;
      IF NOT FOUND THEN RETURN NULL; END IF;
      IF work.status='running' THEN
        UPDATE public.intake_extraction_attempt SET finished_at=clock_timestamp(),result=jsonb_build_object('status','blocked','error_code','lease_expired')
          WHERE organization_id=org AND job_id=work.id AND fence=work.fence AND finished_at IS NULL;
      END IF;
      IF work.attempt_number>=3 THEN
        UPDATE public.intake_extraction_job SET status='blocked',error_code='attempt_limit',lease_expires_at=NULL,updated_at=clock_timestamp() WHERE id=work.id;
        RETURN NULL;
      END IF;
      UPDATE public.intake_extraction_job SET status='running',attempt_number=attempt_number+1,fence=token,
        lease_expires_at=clock_timestamp()+interval '180 seconds',error_code=NULL,updated_at=clock_timestamp() WHERE id=work.id;
      INSERT INTO public.intake_extraction_attempt(organization_id,job_id,attempt_number,fence) VALUES(org,work.id,work.attempt_number+1,token);
      RETURN public.intake_extraction_context(org,work.id,token);
    END $$;
    CREATE FUNCTION intake_extraction_status(org uuid,document uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE original uuid; answer jsonb;
    BEGIN
      PERFORM public.lock_intake(org);
      SELECT r.intake_original_id INTO original FROM public.logical_document l JOIN public.document_ref r ON r.organization_id=l.organization_id AND r.id=l.document_ref_id
        WHERE l.organization_id=org AND l.id=document;
      IF original IS NULL OR NOT public.intake_original_access(org,original,true) THEN RETURN NULL; END IF;
      SELECT jsonb_build_object('id',id,'logical_document_id',logical_document_id,'status',status,'attempt_number',attempt_number,'fence',fence,'error_code',error_code,'updated_at',updated_at,
        'can_retry',status IN ('unavailable','timeout','failed','canceled','blocked') AND attempt_number<3 AND public.intake_actor_access(org,initiated_by,scope_hint_id,true))
        INTO answer FROM public.intake_extraction_job WHERE organization_id=org AND logical_document_id=document;
      RETURN answer;
    END $$;
    REVOKE ALL ON FUNCTION intake_extraction_context(uuid,uuid,uuid),claim_intake_extraction(uuid),intake_extraction_status(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION intake_extraction_context(uuid,uuid,uuid),claim_intake_extraction(uuid) TO app_worker;
    GRANT EXECUTE ON FUNCTION intake_extraction_status(uuid,uuid) TO app_rw;
    CREATE FUNCTION retry_intake_extraction(org uuid,document uuid,request uuid,expected_fence uuid,explanation text) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE work public.intake_extraction_job; receipt public.intake_extraction_retry; original uuid; actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      PERFORM public.lock_intake(org);
      SELECT * INTO work FROM public.intake_extraction_job WHERE organization_id=org AND logical_document_id=document FOR UPDATE;
      SELECT r.intake_original_id INTO original FROM public.logical_document l JOIN public.document_ref r ON r.organization_id=l.organization_id AND r.id=l.document_ref_id
        WHERE l.organization_id=org AND l.id=document;
      IF original IS NULL OR NOT public.intake_original_access(org,original,true) OR NOT public.intake_actor_access(org,work.initiated_by,work.scope_hint_id,true) THEN
        RAISE EXCEPTION 'Intake context inaccessible' USING ERRCODE='42501'; END IF;
      explanation:=btrim(explanation);
      IF request IS NULL OR expected_fence IS NULL OR explanation IS NULL OR length(explanation) NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'Explicit retry reason and generation required' USING ERRCODE='22023'; END IF;
      SELECT * INTO receipt FROM public.intake_extraction_retry WHERE organization_id=org AND actor_id=actor AND request_key=request;
      IF FOUND THEN
        IF (receipt.job_id,receipt.prior_fence,receipt.reason) IS DISTINCT FROM (work.id,expected_fence,explanation) THEN
          RAISE EXCEPTION 'Retry replay conflict' USING ERRCODE='23505'; END IF;
      ELSE
        IF work.status NOT IN ('unavailable','timeout','failed','canceled','blocked') OR work.fence IS DISTINCT FROM expected_fence OR work.attempt_number>=3 THEN
          RAISE EXCEPTION 'Extraction retry unavailable or stale' USING ERRCODE='23505'; END IF;
        INSERT INTO public.intake_extraction_retry(organization_id,job_id,actor_id,request_key,prior_fence,reason)
          VALUES(org,work.id,actor,request,expected_fence,explanation) RETURNING * INTO receipt;
        UPDATE public.intake_extraction_job SET status='queued',error_code=NULL,lease_expires_at=NULL,updated_at=clock_timestamp() WHERE id=work.id;
      END IF;
      RETURN jsonb_build_object('id',receipt.id,'job_id',receipt.job_id,'created_at',receipt.created_at);
    END $$;
    REVOKE ALL ON FUNCTION retry_intake_extraction(uuid,uuid,uuid,uuid,text) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION retry_intake_extraction(uuid,uuid,uuid,uuid,text) TO app_rw;
    ''')
    op.execute(VALIDATE_AND_COMPLETE)
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(BEFORE)==1
    op.execute(definition.replace(BEFORE,AFTER))
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_disposition_work(uuid)'::regprocedure)"))
    op.execute(definition.replace('FUNCTION public.access_disposition_work(','FUNCTION public.access_disposition_work_pre_extraction(',1))
    op.execute('''REVOKE ALL ON FUNCTION access_disposition_work_pre_extraction(uuid) FROM PUBLIC,app_rw,app_worker,app_review;
      CREATE OR REPLACE FUNCTION access_disposition_work(org uuid) RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
        SELECT public.access_disposition_work_pre_extraction(org)||COALESCE(jsonb_agg(jsonb_build_object('kind','intake_extraction_job','resource_id',id) ORDER BY id),'[]')
        FROM public.intake_extraction_job WHERE organization_id=org AND (status IN ('queued','running') OR lease_expires_at>clock_timestamp());
      $$;
      REVOKE ALL ON FUNCTION access_disposition_work(uuid) FROM PUBLIC,app_rw,app_worker,app_review;''')


VALIDATE_AND_COMPLETE='''
CREATE FUNCTION complete_intake_extraction(org uuid,job uuid,token uuid,result jsonb) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE context jsonb; proposal jsonb:=result->'payload'; evidence jsonb:=result->'evidence'; pages jsonb:=result->'pages';
  state text:=result->>'status'; identity_key text; field record; locator jsonb; kind text; page jsonb; wanted jsonb; created uuid;
BEGIN
  context:=public.intake_extraction_context(org,job,token);
  IF context IS NULL THEN RETURN false; END IF;
  IF jsonb_typeof(result) IS DISTINCT FROM 'object' OR octet_length(result::text)>5242880 OR
    (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(result) k) IS DISTINCT FROM
      ARRAY['content_byte_size','content_sha256','error_code','evidence','mime_type','page_end','page_start','pages','payload','processor_version','status','verified_page_count']::text[]
    OR result->>'processor_version' IS DISTINCT FROM 'intake-local-v1' OR state IS NULL OR
      state NOT IN ('succeeded','needs_review','unsupported','unavailable','timeout','failed','canceled')
    OR (state='succeeded' AND (result->>'error_code' IS NOT NULL OR proposal='null'))
    OR (state<>'succeeded' AND (jsonb_typeof(result->'error_code') IS DISTINCT FROM 'string' OR result->>'error_code' !~ '^[a-z_]{1,64}$')) THEN
    RAISE EXCEPTION 'Invalid extraction outcome' USING ERRCODE='22023'; END IF;
  FOREACH identity_key IN ARRAY ARRAY['content_sha256','content_byte_size','mime_type','page_start','page_end','verified_page_count'] LOOP
    IF result->identity_key IS DISTINCT FROM context->identity_key THEN RAISE EXCEPTION 'Extraction source mismatch' USING ERRCODE='22023'; END IF;
  END LOOP;
  IF jsonb_typeof(evidence) IS DISTINCT FROM 'object' OR jsonb_typeof(pages) IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'Invalid evidence schema' USING ERRCODE='22023'; END IF;
  IF proposal='null' THEN
    IF evidence<>'{}' OR pages<>'[]' THEN RAISE EXCEPTION 'Partial failed proposal' USING ERRCODE='22023'; END IF;
  ELSE
    IF state NOT IN ('succeeded','needs_review') OR jsonb_typeof(proposal) IS DISTINCT FROM 'object' OR
      (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(proposal) k) IS DISTINCT FROM
        ARRAY['confidence','document_number','field_confidences','gross_amount','invoice_date','is_einvoice','raw_text','service_period_from','service_period_to','source_format','source_pages','suggested_category_code','vat_amount','vat_rate','vendor_name']::text[]
      OR jsonb_typeof(proposal->'confidence') IS DISTINCT FROM 'number' OR (proposal->>'confidence')::numeric NOT BETWEEN 0 AND 1
      OR jsonb_typeof(proposal->'field_confidences') IS DISTINCT FROM 'object' OR jsonb_typeof(proposal->'is_einvoice') IS DISTINCT FROM 'boolean' THEN
      RAISE EXCEPTION 'Invalid proposal schema' USING ERRCODE='22023'; END IF;
    SELECT COALESCE(jsonb_agg(n ORDER BY n),'[]') INTO wanted FROM generate_series((context->>'page_start')::integer,(context->>'page_end')::integer) n;
    IF proposal->'source_pages' IS DISTINCT FROM wanted OR jsonb_array_length(pages)<>jsonb_array_length(wanted) THEN
      RAISE EXCEPTION 'Proposal page selection mismatch' USING ERRCODE='22023'; END IF;
    FOR field IN SELECT * FROM jsonb_each(proposal) WHERE key IN ('raw_text','source_format') LOOP
      IF field.value<>'null' AND (jsonb_typeof(field.value)<>'string' OR length(field.value#>>'{}')>CASE WHEN field.key='raw_text' THEN 1000000 ELSE 100 END) THEN
        RAISE EXCEPTION 'Invalid source text' USING ERRCODE='22023'; END IF;
    END LOOP;
    FOR field IN SELECT value,ordinality FROM jsonb_array_elements(pages) WITH ORDINALITY LOOP
      page:=field.value;
      IF jsonb_typeof(page) IS DISTINCT FROM 'object' OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(page) k) IS DISTINCT FROM ARRAY['method','number','text']::text[]
        OR page->'number' IS DISTINCT FROM wanted->(field.ordinality::integer-1) OR jsonb_typeof(page->'method') IS DISTINCT FROM 'string' OR page->>'method' NOT IN ('text_layer','ocr')
        OR jsonb_typeof(page->'text') IS DISTINCT FROM 'string' OR length(page->>'text') NOT BETWEEN 20 AND 1000000 THEN
        RAISE EXCEPTION 'Invalid extracted page' USING ERRCODE='22023'; END IF;
    END LOOP;
    IF context->>'mime_type'<>'application/xml' AND proposal->>'raw_text' IS DISTINCT FROM
      (SELECT string_agg(p->>'text',E'\\n' ORDER BY ordinality) FROM jsonb_array_elements(pages) WITH ORDINALITY AS t(p,ordinality)) THEN
      RAISE EXCEPTION 'Raw page text mismatch' USING ERRCODE='22023'; END IF;
    FOR field IN SELECT * FROM jsonb_each(proposal->'field_confidences') LOOP
      IF field.key NOT IN ('vendor_name','document_number','suggested_category_code','invoice_date','service_period_from','service_period_to','gross_amount','vat_amount','vat_rate')
        OR jsonb_typeof(field.value)<>'number' OR (field.value#>>'{}')::numeric NOT BETWEEN 0 AND 1 THEN
        RAISE EXCEPTION 'Invalid confidence' USING ERRCODE='22023'; END IF;
    END LOOP;
    IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(evidence) k) IS DISTINCT FROM
      (SELECT array_agg(k ORDER BY k) FROM jsonb_each(proposal) AS f(k,v) WHERE k IN ('vendor_name','document_number','suggested_category_code','invoice_date','service_period_from','service_period_to','gross_amount','vat_amount','vat_rate') AND v<>'null') THEN
      RAISE EXCEPTION 'Evidence fields mismatch' USING ERRCODE='22023'; END IF;
    FOR field IN SELECT * FROM jsonb_each(evidence) LOOP
      kind:=CASE WHEN field.key IN ('gross_amount','vat_amount','vat_rate') THEN 'decimal' WHEN field.key IN ('invoice_date','service_period_from','service_period_to') THEN 'date' ELSE 'text' END;
      IF jsonb_typeof(proposal->field.key)<>'string' OR NOT public.invoice_evidence_value_valid(kind,proposal->>field.key) THEN
        RAISE EXCEPTION 'Invalid typed value' USING ERRCODE='22023'; END IF;
      locator:=field.value;
      IF jsonb_typeof(locator) IS DISTINCT FROM 'object' OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(locator) k) IS DISTINCT FROM
        ARRAY['locator_status','method','page_number','quotation','xml_path']::text[] THEN RAISE EXCEPTION 'Invalid locator schema' USING ERRCODE='22023'; END IF;
      IF locator->>'locator_status'='unlocated' THEN
        IF locator IS DISTINCT FROM jsonb_build_object('locator_status','unlocated','method','unlocated','page_number',NULL,'quotation',NULL,'xml_path',NULL) THEN
          RAISE EXCEPTION 'Invalid unlocated evidence' USING ERRCODE='22023'; END IF;
      ELSIF locator->>'locator_status'='page_quote' THEN
        SELECT p INTO page FROM jsonb_array_elements(pages) p WHERE p->'number'=locator->'page_number';
        IF page IS NULL OR locator->>'xml_path' IS NOT NULL OR locator->>'method' IS DISTINCT FROM page->>'method' OR
          jsonb_typeof(locator->'quotation') IS DISTINCT FROM 'string' OR length(locator->>'quotation') NOT BETWEEN 1 AND 4000 OR strpos(page->>'text',locator->>'quotation')=0 THEN
          RAISE EXCEPTION 'Invalid page quotation' USING ERRCODE='22023'; END IF;
      ELSIF locator->>'locator_status'='xml_path' THEN
        IF context->>'mime_type'<>'application/xml' OR locator->>'page_number' IS NOT NULL OR locator->>'method' IS DISTINCT FROM 'xml' OR
          jsonb_typeof(locator->'quotation') IS DISTINCT FROM 'string' OR length(locator->>'quotation') NOT BETWEEN 1 AND 4000 OR
          jsonb_typeof(locator->'xml_path') IS DISTINCT FROM 'string' OR length(locator->>'xml_path') NOT BETWEEN 3 AND 16000 OR left(locator->>'xml_path',2)<>'./' THEN
          RAISE EXCEPTION 'Invalid XML locator' USING ERRCODE='22023'; END IF;
      ELSE RAISE EXCEPTION 'Unknown locator' USING ERRCODE='22023'; END IF;
    END LOOP;
  END IF;
  INSERT INTO public.extraction_revision(organization_id,document_ref_id,intake_attempt_id,logical_document_id,content_sha256,processor_version,status,error_code,payload)
    VALUES(org,(context->>'document_ref_id')::uuid,(context->>'attempt_id')::uuid,(context->>'logical_document_id')::uuid,
      context->>'content_sha256','intake-local-v1',state,result->>'error_code',NULLIF(proposal,'null')) RETURNING id INTO created;
  FOR field IN SELECT * FROM jsonb_each(evidence) LOOP
    kind:=CASE WHEN field.key IN ('gross_amount','vat_amount','vat_rate') THEN 'decimal' WHEN field.key IN ('invoice_date','service_period_from','service_period_to') THEN 'date' ELSE 'text' END;
    locator:=field.value;
    INSERT INTO public.field_evidence(organization_id,extraction_revision_id,field_name,value_type,value,confidence,locator_status,page_number,quotation,xml_path,method)
      VALUES(org,created,field.key,kind,proposal->>field.key,(proposal->'field_confidences'->>field.key)::numeric,
        locator->>'locator_status',(locator->>'page_number')::integer,locator->>'quotation',locator->>'xml_path',locator->>'method');
  END LOOP;
  UPDATE public.intake_extraction_attempt SET finished_at=clock_timestamp(),result=complete_intake_extraction.result WHERE organization_id=org AND id=(context->>'attempt_id')::uuid;
  UPDATE public.intake_extraction_job SET status=state,error_code=result->>'error_code',lease_expires_at=NULL,updated_at=clock_timestamp() WHERE organization_id=org AND id=job;
  INSERT INTO public.outbox_event(organization_id,document_ref_id,event_type,effect_key) VALUES(org,(context->>'document_ref_id')::uuid,'document.extracted','extraction:'||created::text);
  RETURN true;
END $$;
REVOKE ALL ON FUNCTION complete_intake_extraction(uuid,uuid,uuid,jsonb) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION complete_intake_extraction(uuid,uuid,uuid,jsonb) TO app_worker;
'''


def downgrade():
    op.execute('LOCK TABLE intake_extraction_job,intake_extraction_attempt,intake_extraction_retry,logical_document,extraction_revision,field_evidence IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM intake_extraction_job) THEN RAISE EXCEPTION 'Retain intake extraction evidence; repair forward'; END IF; END $$")
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(AFTER)==1
    op.execute(definition.replace(AFTER,BEFORE))
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_disposition_work_pre_extraction(uuid)'::regprocedure)"))
    op.execute(definition.replace('FUNCTION public.access_disposition_work_pre_extraction(','FUNCTION public.access_disposition_work(',1))
    op.execute('''DROP FUNCTION access_disposition_work_pre_extraction(uuid);
      DROP TRIGGER enqueue_logical_extraction ON logical_document;
      DROP FUNCTION enqueue_logical_extraction(),intake_extraction_context(uuid,uuid,uuid),claim_intake_extraction(uuid),intake_extraction_status(uuid,uuid),complete_intake_extraction(uuid,uuid,uuid,jsonb),retry_intake_extraction(uuid,uuid,uuid,uuid,text);
      ALTER TABLE extraction_revision DROP CONSTRAINT extraction_revision_typed_attempt;
      ALTER TABLE extraction_revision DROP COLUMN intake_attempt_id,DROP COLUMN logical_document_id;
      ALTER TABLE extraction_revision ALTER COLUMN invoice_attempt_id SET NOT NULL;
      ALTER TABLE extraction_revision ADD CONSTRAINT extraction_revision_processor_version_check CHECK(processor_version='invoice-local-v1');
      ALTER TABLE field_evidence DROP CONSTRAINT field_evidence_locator;
      ALTER TABLE field_evidence DROP COLUMN method;
      ALTER TABLE field_evidence ADD CHECK(locator_status='unlocated'),ADD CHECK(page_number IS NULL),ADD CHECK(quotation IS NULL),ADD CHECK(xml_path IS NULL);
      DROP TABLE intake_extraction_retry,intake_extraction_attempt,intake_extraction_job;''')
