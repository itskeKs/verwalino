"""Append immutable invoice extraction revisions and explicit unlocated fields."""
from alembic import op
from sqlalchemy import text

revision='0096'
down_revision='0095'
branch_labels=None
depends_on=None
BEFORE="UNION ALL SELECT 'logical',id,to_jsonb(r) FROM public.logical_document r WHERE organization_id=org) evidence"
AFTER="UNION ALL SELECT 'logical',id,to_jsonb(r) FROM public.logical_document r WHERE organization_id=org UNION ALL SELECT 'extraction',id,to_jsonb(r) FROM public.extraction_revision r WHERE organization_id=org UNION ALL SELECT 'field',id,to_jsonb(r) FROM public.field_evidence r WHERE organization_id=org) evidence"


def upgrade():
    op.execute('''
    ALTER TABLE invoice_processing_attempt ADD CONSTRAINT uq_invoice_attempt_org_id UNIQUE(organization_id,id);
    CREATE TABLE extraction_revision(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),
      document_ref_id uuid NOT NULL,invoice_attempt_id uuid NOT NULL,content_sha256 text NOT NULL,
      processor_version text NOT NULL CHECK(processor_version='invoice-local-v1'),
      schema_version text NOT NULL DEFAULT 'invoice-fields-v1' CHECK(schema_version='invoice-fields-v1'),
      status text NOT NULL CHECK(status IN ('succeeded','needs_review','unsupported','unavailable','timeout','failed','canceled')),
      error_code text,payload jsonb,validation_status text NOT NULL DEFAULT 'typed_only' CHECK(validation_status='typed_only'),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(organization_id,id),UNIQUE(organization_id,invoice_attempt_id),
      FOREIGN KEY(organization_id,document_ref_id) REFERENCES document_ref(organization_id,id),
      FOREIGN KEY(organization_id,invoice_attempt_id) REFERENCES invoice_processing_attempt(organization_id,id));
    CREATE INDEX ix_extraction_revision_source ON extraction_revision(organization_id,document_ref_id,created_at,id);
    CREATE TABLE field_evidence(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),extraction_revision_id uuid NOT NULL,
      field_name text NOT NULL CHECK(field_name IN ('vendor_name','document_number','suggested_category_code','invoice_date',
        'service_period_from','service_period_to','gross_amount','vat_amount','vat_rate')),
      value_type text NOT NULL CHECK(value_type IN ('text','date','decimal')),value text NOT NULL,confidence numeric CHECK(confidence BETWEEN 0 AND 1),
      locator_status text NOT NULL DEFAULT 'unlocated' CHECK(locator_status='unlocated'),
      validation_status text NOT NULL DEFAULT 'unverified' CHECK(validation_status='unverified'),
      page_number integer CHECK(page_number IS NULL),bbox jsonb CHECK(bbox IS NULL),
      quotation text CHECK(quotation IS NULL),xml_path text CHECK(xml_path IS NULL),
      UNIQUE(organization_id,id),UNIQUE(organization_id,extraction_revision_id,field_name),
      FOREIGN KEY(organization_id,extraction_revision_id) REFERENCES extraction_revision(organization_id,id));
    ''')
    for table in ('extraction_revision','field_evidence'):
        op.execute(f'''ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review;
          GRANT SELECT ON {table} TO app_rw;
          CREATE POLICY {table}_no_insert ON {table} FOR INSERT WITH CHECK(false);
          CREATE TRIGGER protect_{table} BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();''')
    op.execute('''
    CREATE POLICY extraction_revision_read ON extraction_revision FOR SELECT USING(
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid AND EXISTS(
        SELECT 1 FROM document_ref r WHERE r.organization_id=extraction_revision.organization_id AND r.id=extraction_revision.document_ref_id));
    CREATE POLICY field_evidence_read ON field_evidence FOR SELECT USING(
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid AND EXISTS(
        SELECT 1 FROM extraction_revision r WHERE r.organization_id=field_evidence.organization_id AND r.id=field_evidence.extraction_revision_id));
    ALTER TABLE outbox_event ALTER COLUMN resource_id DROP NOT NULL;
    ALTER TABLE outbox_event ADD COLUMN document_ref_id uuid,
      ADD FOREIGN KEY(organization_id,document_ref_id) REFERENCES document_ref(organization_id,id),
      ADD CONSTRAINT outbox_event_typed_source CHECK(
        (event_type='document.extracted' AND document_ref_id IS NOT NULL AND resource_id IS NULL) OR
        (event_type<>'document.extracted' AND document_ref_id IS NULL AND resource_id IS NOT NULL));
    ALTER TABLE outbox_event DROP CONSTRAINT outbox_event_event_type_check;
    ALTER TABLE outbox_event ADD CONSTRAINT outbox_event_event_type_check CHECK(event_type IN ('intake.received','intake.inspected','intake.cancelled','intake.logical_created','document.extracted'));

    CREATE FUNCTION invoice_evidence_value_valid(kind text,value text) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF value IS NULL OR length(value) NOT BETWEEN 1 AND 4000 THEN RETURN false; END IF;
      IF kind='text' THEN RETURN true;
      ELSIF kind='decimal' THEN RETURN value ~ '^-?(0|[1-9][0-9]{0,15})([.][0-9]{1,6})?$';
      ELSIF kind='date' THEN
        IF value !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN RETURN false; END IF;
        PERFORM value::date; RETURN true;
      END IF;
      RETURN false;
    EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RETURN false;
    END $$;
    CREATE FUNCTION record_invoice_extraction_revision(org uuid,attempt_id uuid,token uuid,result jsonb) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE job public.invoice_processing_attempt; source public.invoice_document; ref public.document_ref;
      prior public.extraction_revision; created uuid; proposal jsonb:=result->'payload'; field record; kind text; expected jsonb;
    BEGIN
      IF session_user<>'app_worker' THEN RAISE EXCEPTION 'Extraction runtime required' USING ERRCODE='42501'; END IF;
      PERFORM public.lock_intake(org);
      IF public.property_source_security_mode(org)='PAUSED' OR NOT public.invoice_attempt_authorized(org,attempt_id) THEN
        RAISE EXCEPTION 'Extraction principal inaccessible' USING ERRCODE='42501'; END IF;
      SELECT * INTO job FROM public.invoice_processing_attempt WHERE organization_id=org AND id=attempt_id;
      IF NOT FOUND OR job.token IS DISTINCT FROM token OR token IS NULL OR job.completed_at IS NULL OR job.lease_until<=clock_timestamp()
        OR job.status NOT IN ('succeeded','needs_review','unsupported','unavailable','timeout','failed','canceled') THEN
        RAISE EXCEPTION 'Extraction attempt changed or expired' USING ERRCODE='23514'; END IF;
      SELECT * INTO source FROM public.invoice_document WHERE organization_id=org AND id=job.document_id;
      SELECT * INTO ref FROM public.document_ref WHERE organization_id=org AND invoice_document_id=job.document_id;
      IF NOT FOUND OR ref.content_sha256 IS DISTINCT FROM source.content_sha256 THEN
        RAISE EXCEPTION 'Extraction original reference missing' USING ERRCODE='23514'; END IF;
      IF jsonb_typeof(result) IS DISTINCT FROM 'object' OR octet_length(result::text)>5242880 OR
        (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(result) key) IS DISTINCT FROM ARRAY['error_code','payload','processor_version','status']::text[]
        OR result->>'status' IS DISTINCT FROM job.status OR result->>'processor_version' IS DISTINCT FROM 'invoice-local-v1'
        OR result->>'processor_version' IS DISTINCT FROM job.processor_version OR result->>'error_code' IS DISTINCT FROM job.error_code
        OR (job.status='succeeded' AND (job.error_code IS NOT NULL OR proposal='null'))
        OR (job.status<>'succeeded' AND (job.error_code IS NULL OR job.error_code !~ '^[a-z_]{1,64}$')) THEN
        RAISE EXCEPTION 'Extraction outcome mismatch' USING ERRCODE='23514'; END IF;
      IF proposal<>'null'::jsonb THEN
        IF job.status NOT IN ('succeeded','needs_review') OR jsonb_typeof(proposal) IS DISTINCT FROM 'object' OR
          (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(proposal) key) IS DISTINCT FROM
            ARRAY['confidence','document_number','field_confidences','gross_amount','invoice_date','is_einvoice','raw_text','service_period_from','service_period_to','source_format','source_pages','suggested_category_code','vat_amount','vat_rate','vendor_name']::text[]
          OR jsonb_typeof(proposal->'confidence') IS DISTINCT FROM 'number' OR (proposal->>'confidence')::numeric NOT BETWEEN 0 AND 1
          OR jsonb_typeof(proposal->'field_confidences') IS DISTINCT FROM 'object' OR jsonb_typeof(proposal->'is_einvoice') IS DISTINCT FROM 'boolean'
          OR jsonb_typeof(proposal->'source_pages') IS DISTINCT FROM 'array' OR jsonb_array_length(proposal->'source_pages')>200 THEN
          RAISE EXCEPTION 'Invalid extraction proposal schema' USING ERRCODE='23514'; END IF;
        IF EXISTS(SELECT 1 FROM jsonb_array_elements(proposal->'source_pages') p WHERE jsonb_typeof(p)<>'number' OR p::text !~ '^[0-9]{1,3}$' OR p::text::integer NOT BETWEEN 1 AND 200)
          OR (SELECT count(*)<>count(DISTINCT p) FROM jsonb_array_elements(proposal->'source_pages') p) THEN
          RAISE EXCEPTION 'Invalid processor source pages' USING ERRCODE='23514'; END IF;
        FOR field IN SELECT * FROM jsonb_each(proposal) WHERE key IN ('raw_text','source_format') LOOP
          IF field.value<>'null' AND (jsonb_typeof(field.value)<>'string' OR length(field.value#>>'{}')>CASE WHEN field.key='raw_text' THEN 1000000 ELSE 100 END) THEN
            RAISE EXCEPTION 'Invalid extraction source text' USING ERRCODE='23514'; END IF;
        END LOOP;
        FOR field IN SELECT * FROM jsonb_each(proposal->'field_confidences') LOOP
          IF field.key NOT IN ('vendor_name','document_number','suggested_category_code','invoice_date','service_period_from','service_period_to','gross_amount','vat_amount','vat_rate')
            OR jsonb_typeof(field.value)<>'number' OR (field.value#>>'{}')::numeric NOT BETWEEN 0 AND 1 THEN
            RAISE EXCEPTION 'Invalid field confidence' USING ERRCODE='23514'; END IF;
        END LOOP;
        expected:=proposal||jsonb_build_object('attempt_id',attempt_id,'processor_version',job.processor_version);
        IF source.extraction_payload IS DISTINCT FROM expected THEN RAISE EXCEPTION 'Extraction payload differs from accepted source result' USING ERRCODE='23514'; END IF;
      END IF;
      SELECT * INTO prior FROM public.extraction_revision WHERE organization_id=org AND invoice_attempt_id=attempt_id;
      IF FOUND THEN
        IF (prior.status,prior.error_code,prior.processor_version,prior.payload) IS DISTINCT FROM
          (job.status,job.error_code,job.processor_version,NULLIF(proposal,'null')) THEN
          RAISE EXCEPTION 'Extraction revision conflicts' USING ERRCODE='23505'; END IF;
        RETURN prior.id;
      END IF;
      INSERT INTO public.extraction_revision(organization_id,document_ref_id,invoice_attempt_id,content_sha256,processor_version,status,error_code,payload)
        VALUES(org,ref.id,attempt_id,ref.content_sha256,job.processor_version,job.status,job.error_code,NULLIF(proposal,'null')) RETURNING id INTO created;
      IF proposal<>'null'::jsonb THEN
        FOR field IN SELECT * FROM jsonb_each(proposal) WHERE key IN ('vendor_name','document_number','suggested_category_code','invoice_date',
          'service_period_from','service_period_to','gross_amount','vat_amount','vat_rate') AND value<>'null'::jsonb LOOP
          kind:=CASE WHEN field.key IN ('gross_amount','vat_amount','vat_rate') THEN 'decimal'
            WHEN field.key IN ('invoice_date','service_period_from','service_period_to') THEN 'date' ELSE 'text' END;
          IF jsonb_typeof(field.value)<>'string' OR NOT public.invoice_evidence_value_valid(kind,field.value#>>'{}') THEN
            RAISE EXCEPTION 'Invalid typed field value' USING ERRCODE='23514'; END IF;
          INSERT INTO public.field_evidence(organization_id,extraction_revision_id,field_name,value_type,value,confidence)
            VALUES(org,created,field.key,kind,field.value#>>'{}',(proposal->'field_confidences'->>field.key)::numeric);
        END LOOP;
      END IF;
      INSERT INTO public.outbox_event(organization_id,document_ref_id,event_type,effect_key)
        VALUES(org,ref.id,'document.extracted','extraction:'||created::text);
      RETURN created;
    END $$;
    REVOKE ALL ON FUNCTION invoice_evidence_value_valid(text,text),record_invoice_extraction_revision(uuid,uuid,uuid,jsonb) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION record_invoice_extraction_revision(uuid,uuid,uuid,jsonb) TO app_worker;
    ''')
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(BEFORE)==1
    op.execute(definition.replace(BEFORE,AFTER))


def downgrade():
    op.execute('LOCK TABLE extraction_revision,field_evidence,outbox_event IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM extraction_revision) OR EXISTS(SELECT 1 FROM outbox_event WHERE document_ref_id IS NOT NULL) THEN RAISE EXCEPTION 'Retain extraction evidence; repair forward'; END IF; END $$")
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(AFTER)==1
    op.execute(definition.replace(AFTER,BEFORE))
    op.execute('''DROP FUNCTION record_invoice_extraction_revision(uuid,uuid,uuid,jsonb),invoice_evidence_value_valid(text,text);
      DROP TABLE field_evidence,extraction_revision;
      ALTER TABLE invoice_processing_attempt DROP CONSTRAINT uq_invoice_attempt_org_id;
      ALTER TABLE outbox_event DROP CONSTRAINT outbox_event_typed_source;
      ALTER TABLE outbox_event DROP COLUMN document_ref_id;
      ALTER TABLE outbox_event ALTER COLUMN resource_id SET NOT NULL;
      ALTER TABLE outbox_event DROP CONSTRAINT outbox_event_event_type_check;
      ALTER TABLE outbox_event ADD CONSTRAINT outbox_event_event_type_check CHECK(event_type IN ('intake.received','intake.inspected','intake.cancelled','intake.logical_created'));''')
