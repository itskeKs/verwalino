"""Explicit advisory context reuse, with private evidence and immutable revocation."""
from alembic import op
from sqlalchemy import text

revision='0112'
down_revision='0111'
branch_labels=None
depends_on=None
BEFORE="UNION ALL SELECT 'exception_completion_member',id,to_jsonb(r) FROM public.exception_completion_member r WHERE organization_id=org) evidence"
AFTER=BEFORE.replace(') evidence'," UNION ALL SELECT 'mapping_version',id,to_jsonb(r) FROM public.mapping_version r WHERE organization_id=org) evidence")


def coverage(before,after):
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(before)==1
    op.execute(definition.replace(before,after))


def upgrade():
    op.execute('''
    CREATE TABLE mapping_version(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,property_id uuid NOT NULL,scope_id uuid NOT NULL,
      identifier_kind text NOT NULL CHECK(identifier_kind IN ('property_id','meter_number')),
      identifier_value text NOT NULL CHECK(length(identifier_value) BETWEEN 1 AND 100),
      revision_number integer NOT NULL CHECK(revision_number>0),predecessor_id uuid,
      state text NOT NULL CHECK(state IN ('active','revoked')),valid_from date NOT NULL,valid_to date NOT NULL CHECK(valid_to>=valid_from),
      policy text NOT NULL DEFAULT 'manual-context-reuse-v1' CHECK(policy='manual-context-reuse-v1'),
      allocation_set_id uuid,source_basis jsonb,meter_id uuid,meter_unit_id uuid,reuse_consented boolean NOT NULL,
      actor_id uuid NOT NULL,request_key uuid NOT NULL,request_digest text NOT NULL,reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 2000),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CHECK((revision_number=1)=(predecessor_id IS NULL)),
      CHECK((state='active' AND allocation_set_id IS NOT NULL AND source_basis IS NOT NULL AND reuse_consented
        AND ((identifier_kind='meter_number' AND meter_id IS NOT NULL) OR (identifier_kind='property_id' AND meter_id IS NULL AND meter_unit_id IS NULL)))
        OR (state='revoked' AND predecessor_id IS NOT NULL AND allocation_set_id IS NULL AND source_basis IS NULL AND NOT reuse_consented AND meter_id IS NULL AND meter_unit_id IS NULL)),
      UNIQUE(organization_id,id),UNIQUE(organization_id,property_id,identifier_kind,identifier_value,id),
      UNIQUE(organization_id,property_id,identifier_kind,identifier_value,revision_number),UNIQUE(organization_id,predecessor_id),
      UNIQUE(organization_id,actor_id,request_key),
      FOREIGN KEY(organization_id,property_id,scope_id) REFERENCES accounting_scope(organization_id,property_id,id),
      FOREIGN KEY(organization_id,property_id,identifier_kind,identifier_value,predecessor_id) REFERENCES mapping_version(organization_id,property_id,identifier_kind,identifier_value,id),
      FOREIGN KEY(organization_id,allocation_set_id) REFERENCES allocation_set(organization_id,id),
      FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
    ALTER TABLE mapping_version ENABLE ROW LEVEL SECURITY;ALTER TABLE mapping_version FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON mapping_version FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE POLICY mapping_private ON mapping_version USING(false) WITH CHECK(false);
    CREATE TRIGGER mapping_immutable BEFORE UPDATE OR DELETE ON mapping_version FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();
    ''')
    op.execute(HELPERS)
    op.execute(COMMANDS)
    op.execute(READS)
    coverage(BEFORE,AFTER)


def downgrade():
    op.execute('LOCK TABLE mapping_version IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM mapping_version) THEN RAISE EXCEPTION 'Retain opted-in mapping evidence; repair forward';END IF;END $$")
    coverage(AFTER,BEFORE)
    op.execute('''DROP TRIGGER mapping_lineage ON mapping_version;DROP TRIGGER mapping_meter_lock ON meter;
      DROP FUNCTION context_mapping_candidates(uuid,uuid,uuid,uuid),context_mapping_options(uuid,uuid,text,text),context_mapping_status(uuid,uuid),
      activate_context_mapping(uuid,uuid,uuid,uuid,text,text,integer,date,date,text,boolean),revoke_context_mapping(uuid,uuid,uuid,uuid,text),
      mapping_source_authority(uuid,uuid),mapping_source_basis(uuid,uuid),mapping_descriptor(public.mapping_version),mapping_master_current(public.mapping_version),
      mapping_target_access(uuid,uuid,text,text,boolean),mapping_source_signals(jsonb),seal_mapping_version(),lock_mapping_meter();
      DROP TABLE mapping_version;''')


HELPERS=r'''
CREATE FUNCTION lock_mapping_meter() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE org uuid;
BEGIN
  -- Include inserts: a concurrent duplicate number also changes master proof.
  FOR org IN SELECT DISTINCT x FROM unnest(CASE WHEN TG_OP='INSERT' THEN ARRAY[NEW.organization_id]
    WHEN TG_OP='DELETE' THEN ARRAY[OLD.organization_id] ELSE ARRAY[OLD.organization_id,NEW.organization_id] END) x ORDER BY x LOOP
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
    PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
  END LOOP;
  IF TG_OP='DELETE' THEN RETURN OLD;END IF;RETURN NEW;
END $$;
CREATE TRIGGER mapping_meter_lock BEFORE INSERT OR UPDATE OR DELETE ON meter FOR EACH ROW EXECUTE FUNCTION lock_mapping_meter();

CREATE FUNCTION mapping_source_signals(pages jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
DECLARE page jsonb;line text;parts text[];kind text;value text;result jsonb:='[]';count integer:=0;
BEGIN
  IF (SELECT COALESCE(sum(octet_length(p->>'text')),0) FROM jsonb_array_elements(pages) p)>1000000 THEN RETURN '[]';END IF;
  FOR page IN SELECT * FROM jsonb_array_elements(pages) LOOP
    FOR line IN SELECT regexp_split_to_table(page->>'text',E'\r\n|\n|\r') LOOP
      parts:=regexp_match(line,'^[[:space:]]*(Objekt[- ]?ID|Zählernummer|Zähler[- ]?Nr\.?)\s*:\s*(\S.*?)\s*$','i');
      IF parts IS NULL THEN CONTINUE;END IF;
      count:=count+1;IF count>50 OR length(line)>1000 THEN RETURN '[]';END IF;
      kind:=CASE WHEN lower(parts[1]) LIKE 'objekt%' THEN 'property_id' ELSE 'meter_number' END;value:=parts[2];
      IF kind='property_id' THEN
        -- Only unambiguous UUID spellings, with the same canonical form as Python.
        IF value !~ '^(urn:uuid:)?(\{[0-9a-fA-F-]+\}|[0-9a-fA-F-]+)$' THEN CONTINUE;END IF;
        BEGIN value:=replace(value,'urn:uuid:','')::uuid::text;EXCEPTION WHEN invalid_text_representation THEN CONTINUE;END;
      ELSIF length(value) NOT BETWEEN 1 AND 100 THEN CONTINUE;END IF;
      result:=result||jsonb_build_array(jsonb_build_object('kind',kind,'value',value,'page_number',page->'number','quotation',line,'method',page->'method'));
    END LOOP;
  END LOOP;
  RETURN result;
END $$;

CREATE FUNCTION mapping_target_access(org uuid,property uuid,kind text,value text,writing boolean) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
  SELECT COALESCE(org=NULLIF(current_setting('app.current_org_id',true),'')::uuid
    AND public.scope_actor_role(org) IS NOT NULL AND public.property_master_access(org,property,'read')
    AND NOT EXISTS(SELECT 1 FROM public.mapping_version v WHERE v.organization_id=org AND v.property_id=property AND v.identifier_kind=kind AND v.identifier_value=value
      AND (NOT public.has_scope_capability(org,v.scope_id,'read') OR (writing AND NOT public.has_scope_capability(org,v.scope_id,'prepare')))),false);
$$;

CREATE FUNCTION mapping_descriptor(v public.mapping_version) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
  SELECT jsonb_build_object('id',v.id,'property_id',v.property_id,'scope_id',v.scope_id,'identifier_kind',v.identifier_kind,
    'identifier_value',v.identifier_value,'revision_number',v.revision_number,'state',v.state,'valid_from',v.valid_from,'valid_to',v.valid_to,'policy',v.policy);
$$;

CREATE FUNCTION mapping_master_current(v public.mapping_version) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
  SELECT v.state='active' AND EXISTS(SELECT 1 FROM public.accounting_scope s WHERE s.organization_id=v.organization_id AND s.id=v.scope_id
    AND s.property_id=v.property_id AND s.scope_type IN ('WEG','MIET') AND s.whole_property AND v.valid_from>=s.valid_from AND (s.valid_to IS NULL OR v.valid_to<=s.valid_to))
    AND ((v.identifier_kind='property_id' AND v.identifier_value=v.property_id::text) OR (v.identifier_kind='meter_number'
      AND (SELECT count(*) FROM public.meter m WHERE m.organization_id=v.organization_id AND m.property_id=v.property_id AND m.meter_number=v.identifier_value)=1
      AND EXISTS(SELECT 1 FROM public.meter m WHERE m.organization_id=v.organization_id AND m.id=v.meter_id AND m.property_id=v.property_id
        AND m.meter_number=v.identifier_value AND m.unit_id IS NOT DISTINCT FROM v.meter_unit_id
        AND (m.unit_id IS NULL OR EXISTS(SELECT 1 FROM public.unit u WHERE u.organization_id=m.organization_id AND u.id=m.unit_id AND u.property_id=m.property_id)))));
$$;

CREATE FUNCTION mapping_source_authority(org uuid,target uuid) RETURNS public.allocation_set
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE a public.allocation_set;
BEGIN
  IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501';END IF;
  PERFORM public.lock_intake(org);
  SELECT * INTO a FROM public.allocation_set WHERE organization_id=org AND id=target;
  IF a.id IS NULL OR a.kind<>'intake_reviewed' OR NOT public.source_partition_access(org,a.document_ref_id,true)
    OR NOT public.intake_original_export_access(org,a.intake_original_id) OR NOT public.has_scope_capability(org,a.scope_id,'prepare')
    OR NOT public.has_scope_capability(org,a.scope_id,'read') OR NOT public.property_master_access(org,a.property_id,'read')
    OR (NULLIF(current_setting('app.current_scope_id',true),'') IS NOT NULL AND a.scope_id IS DISTINCT FROM NULLIF(current_setting('app.current_scope_id',true),'')::uuid) THEN
    RAISE EXCEPTION 'Mapping origin inaccessible' USING ERRCODE='42501';END IF;
  RETURN a;
END $$;

CREATE FUNCTION mapping_source_basis(org uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE a public.allocation_set;p public.allocation_proposal;r public.invoice_review_revision;source jsonb;
BEGIN
  a:=public.mapping_source_authority(org,target);
  SELECT * INTO STRICT p FROM public.allocation_proposal WHERE organization_id=org AND id=a.allocation_proposal_id;
  PERFORM public.require_current_context_decision(org,p.context_decision_id);
  IF a.allocation_proposal_id IS DISTINCT FROM (SELECT id FROM public.allocation_proposal WHERE organization_id=org AND logical_document_id=a.logical_document_id ORDER BY revision_number DESC LIMIT 1)
    OR a.treatment_review_id IS DISTINCT FROM (SELECT id FROM public.allocation_treatment_review WHERE organization_id=org AND logical_document_id=a.logical_document_id ORDER BY revision_number DESC LIMIT 1) THEN
    RAISE EXCEPTION 'Mapping source basis changed' USING ERRCODE='23505';END IF;
  SELECT * INTO STRICT r FROM public.invoice_review_revision WHERE organization_id=org AND id=a.invoice_review_id;
  source:=public.assignment_candidate_source(org,a.logical_document_id,r.partition_revision_id,r.extraction_revision_id);
  RETURN jsonb_build_object('document_ref_id',a.document_ref_id,'logical_document_id',a.logical_document_id,'content_sha256',a.content_sha256,
    'partition_revision_id',r.partition_revision_id,'extraction_revision_id',r.extraction_revision_id,'invoice_review_id',r.id,
    'context_decision_id',p.context_decision_id,'signals',public.mapping_source_signals(source->'pages'));
END $$;

CREATE FUNCTION seal_mapping_version() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE prior public.mapping_version;
BEGIN
  PERFORM public.lock_intake(NEW.organization_id);
  SELECT * INTO prior FROM public.mapping_version WHERE organization_id=NEW.organization_id AND property_id=NEW.property_id
    AND identifier_kind=NEW.identifier_kind AND identifier_value=NEW.identifier_value ORDER BY revision_number DESC LIMIT 1;
  IF NEW.predecessor_id IS DISTINCT FROM prior.id OR NEW.revision_number<>COALESCE(prior.revision_number,0)+1 THEN
    RAISE EXCEPTION 'Mapping predecessor changed' USING ERRCODE='23505';END IF;
  IF NEW.state='revoked' AND (prior.state IS DISTINCT FROM 'active' OR NEW.scope_id<>prior.scope_id OR NEW.valid_from<>prior.valid_from OR NEW.valid_to<>prior.valid_to) THEN
    RAISE EXCEPTION 'Revocation must retain its active target' USING ERRCODE='23514';END IF;
  IF NEW.state='active' AND NOT EXISTS(SELECT 1 FROM public.allocation_set a WHERE a.organization_id=NEW.organization_id AND a.id=NEW.allocation_set_id
    AND a.kind='intake_reviewed' AND a.property_id=NEW.property_id AND a.scope_id=NEW.scope_id
    AND a.document_ref_id::text=NEW.source_basis->>'document_ref_id' AND a.logical_document_id::text=NEW.source_basis->>'logical_document_id'
    AND a.content_sha256=NEW.source_basis->>'content_sha256' AND a.invoice_review_id::text=NEW.source_basis->>'invoice_review_id') THEN
    RAISE EXCEPTION 'Mapping origin does not match target' USING ERRCODE='23514';END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER mapping_lineage BEFORE INSERT ON mapping_version FOR EACH ROW EXECUTE FUNCTION seal_mapping_version();
REVOKE ALL ON FUNCTION mapping_source_signals(jsonb),mapping_target_access(uuid,uuid,text,text,boolean),mapping_descriptor(public.mapping_version),
  mapping_master_current(public.mapping_version),mapping_source_authority(uuid,uuid),mapping_source_basis(uuid,uuid),seal_mapping_version(),lock_mapping_meter() FROM PUBLIC,app_rw,app_worker,app_review;
'''


COMMANDS='''
CREATE FUNCTION activate_context_mapping(org uuid,target uuid,expected uuid,request uuid,kind text,value text,page integer,start_date date,end_date date,explanation text,consent boolean) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE a public.allocation_set;prior public.mapping_version;receipt public.mapping_version;created public.mapping_version;
  actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;basis jsonb;proof jsonb;digest text;proof_meter_id uuid;proof_unit_id uuid;s public.accounting_scope;
BEGIN
  a:=public.mapping_source_authority(org,target);
  IF request IS NULL OR kind IS NULL OR kind NOT IN ('property_id','meter_number') OR value IS NULL OR length(value) NOT BETWEEN 1 AND 100
    OR page IS NULL OR page<1 OR start_date IS NULL OR end_date IS NULL OR end_date<start_date OR NOT isfinite(start_date) OR NOT isfinite(end_date)
    OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 2000 OR consent IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Explicit mapping evidence, interval and consent required' USING ERRCODE='22023';END IF;
  SELECT * INTO prior FROM public.mapping_version WHERE organization_id=org AND property_id=a.property_id AND identifier_kind=kind AND identifier_value=value ORDER BY revision_number DESC LIMIT 1;
  IF NOT public.mapping_target_access(org,a.property_id,kind,value,true) THEN RAISE EXCEPTION 'Retained mapping targets inaccessible' USING ERRCODE='42501';END IF;
  digest:=encode(sha256(convert_to(jsonb_build_array('activate',target,expected,kind,value,page,start_date,end_date,explanation,consent)::text,'UTF8')),'hex');
  SELECT * INTO receipt FROM public.mapping_version WHERE organization_id=org AND actor_id=actor AND request_key=request;
  IF FOUND THEN
    IF receipt.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Mapping request changed' USING ERRCODE='23505';END IF;
    RETURN public.mapping_descriptor(receipt);
  END IF;
  IF expected IS DISTINCT FROM prior.id THEN RAISE EXCEPTION 'Mapping predecessor changed' USING ERRCODE='23505';END IF;
  basis:=public.mapping_source_basis(org,target);
  SELECT e.signal INTO proof FROM jsonb_array_elements(basis->'signals') e(signal) WHERE e.signal->>'kind'=kind AND e.signal->>'value'=value AND (e.signal->>'page_number')::integer=page LIMIT 1;
  IF proof IS NULL THEN RAISE EXCEPTION 'Exact labelled source proof required' USING ERRCODE='23514';END IF;
  SELECT * INTO STRICT s FROM public.accounting_scope WHERE organization_id=org AND id=a.scope_id;
  IF s.scope_type NOT IN ('WEG','MIET') OR NOT s.whole_property OR start_date<s.valid_from OR (s.valid_to IS NOT NULL AND end_date>s.valid_to) THEN
    RAISE EXCEPTION 'Mapping interval outside supported scope' USING ERRCODE='23514';END IF;
  IF kind='property_id' THEN
    IF value<>a.property_id::text THEN RAISE EXCEPTION 'Property assertion does not match adopted target' USING ERRCODE='23514';END IF;
  ELSE
    BEGIN
      PERFORM 1 FROM public.meter WHERE organization_id=org AND property_id=a.property_id AND meter_number=value ORDER BY id FOR SHARE NOWAIT;
      SELECT id,unit_id INTO proof_meter_id,proof_unit_id FROM public.meter WHERE organization_id=org AND property_id=a.property_id AND meter_number=value;
      IF proof_unit_id IS NOT NULL THEN PERFORM 1 FROM public.unit WHERE organization_id=org AND id=proof_unit_id FOR SHARE NOWAIT;END IF;
    EXCEPTION WHEN lock_not_available THEN RAISE EXCEPTION 'Mapping master data busy' USING ERRCODE='23505';END;
    IF (SELECT count(*) FROM public.meter WHERE organization_id=org AND property_id=a.property_id AND meter_number=value)<>1
      OR (proof_unit_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.unit WHERE organization_id=org AND id=proof_unit_id AND property_id=a.property_id)) THEN
      RAISE EXCEPTION 'Unambiguous current meter association required' USING ERRCODE='23514';END IF;
  END IF;
  INSERT INTO public.mapping_version(organization_id,property_id,scope_id,identifier_kind,identifier_value,revision_number,predecessor_id,state,
    valid_from,valid_to,allocation_set_id,source_basis,meter_id,meter_unit_id,reuse_consented,actor_id,request_key,request_digest,reason)
    VALUES(org,a.property_id,a.scope_id,kind,value,COALESCE(prior.revision_number,0)+1,prior.id,'active',start_date,end_date,target,
      (basis-'signals')||jsonb_build_object('evidence',proof),proof_meter_id,proof_unit_id,true,actor,request,digest,explanation) RETURNING * INTO created;
  RETURN public.mapping_descriptor(created);
END $$;

CREATE FUNCTION revoke_context_mapping(org uuid,target uuid,expected uuid,request uuid,explanation text) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE v public.mapping_version;head public.mapping_version;receipt public.mapping_version;created public.mapping_version;digest text;
  actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;selected uuid:=NULLIF(current_setting('app.current_scope_id',true),'')::uuid;
BEGIN
  IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501';END IF;
  PERFORM public.lock_intake(org);
  SELECT * INTO v FROM public.mapping_version WHERE organization_id=org AND id=target;
  IF v.id IS NULL OR NOT public.mapping_target_access(org,v.property_id,v.identifier_kind,v.identifier_value,true) THEN
    RAISE EXCEPTION 'Mapping targets inaccessible' USING ERRCODE='42501';END IF;
  SELECT * INTO head FROM public.mapping_version WHERE organization_id=org AND property_id=v.property_id AND identifier_kind=v.identifier_kind AND identifier_value=v.identifier_value ORDER BY revision_number DESC LIMIT 1;
  IF selected IS NOT NULL AND selected<>head.scope_id THEN RAISE EXCEPTION 'Selected mapping target inaccessible' USING ERRCODE='42501';END IF;
  IF request IS NULL OR expected IS DISTINCT FROM target OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 2000 THEN
    RAISE EXCEPTION 'Explicit mapping revocation required' USING ERRCODE='22023';END IF;
  digest:=encode(sha256(convert_to(jsonb_build_array('revoke',target,expected,explanation)::text,'UTF8')),'hex');
  SELECT * INTO receipt FROM public.mapping_version WHERE organization_id=org AND actor_id=actor AND request_key=request;
  IF FOUND THEN
    IF receipt.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Mapping request changed' USING ERRCODE='23505';END IF;
    RETURN public.mapping_descriptor(receipt);
  END IF;
  IF target<>head.id OR head.state<>'active' THEN RAISE EXCEPTION 'Mapping predecessor changed' USING ERRCODE='23505';END IF;
  INSERT INTO public.mapping_version(organization_id,property_id,scope_id,identifier_kind,identifier_value,revision_number,predecessor_id,state,
    valid_from,valid_to,reuse_consented,actor_id,request_key,request_digest,reason)
    VALUES(org,v.property_id,v.scope_id,v.identifier_kind,v.identifier_value,head.revision_number+1,head.id,'revoked',head.valid_from,head.valid_to,false,actor,request,digest,explanation) RETURNING * INTO created;
  RETURN public.mapping_descriptor(created);
END $$;
REVOKE ALL ON FUNCTION activate_context_mapping(uuid,uuid,uuid,uuid,text,text,integer,date,date,text,boolean),revoke_context_mapping(uuid,uuid,uuid,uuid,text) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION activate_context_mapping(uuid,uuid,uuid,uuid,text,text,integer,date,date,text,boolean),revoke_context_mapping(uuid,uuid,uuid,uuid,text) TO app_rw;
'''


READS='''
CREATE FUNCTION context_mapping_status(org uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE v public.mapping_version;head public.mapping_version;selected uuid:=NULLIF(current_setting('app.current_scope_id',true),'')::uuid;
BEGIN
  IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501';END IF;
  SELECT * INTO v FROM public.mapping_version WHERE organization_id=org AND id=target;
  IF v.id IS NULL OR NOT public.mapping_target_access(org,v.property_id,v.identifier_kind,v.identifier_value,false) THEN
    RAISE EXCEPTION 'Mapping inaccessible' USING ERRCODE='42501';END IF;
  SELECT * INTO head FROM public.mapping_version WHERE organization_id=org AND property_id=v.property_id AND identifier_kind=v.identifier_kind AND identifier_value=v.identifier_value ORDER BY revision_number DESC LIMIT 1;
  IF selected IS NOT NULL AND selected<>head.scope_id THEN RAISE EXCEPTION 'Selected mapping inaccessible' USING ERRCODE='42501';END IF;
  RETURN jsonb_build_object('receipt',public.mapping_descriptor(v),'current',public.mapping_descriptor(head),'master_current',public.mapping_master_current(head),
    'can_prepare',public.mapping_target_access(org,v.property_id,v.identifier_kind,v.identifier_value,true));
END $$;

CREATE FUNCTION context_mapping_options(org uuid,target uuid,retained_kind text DEFAULT NULL,retained_value text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE a public.allocation_set;basis jsonb;signal jsonb;options jsonb:='[]';head public.mapping_version;s public.accounting_scope;valid boolean;
BEGIN
  a:=public.mapping_source_authority(org,target);
  IF (retained_kind IS NULL)<>(retained_value IS NULL) THEN RAISE EXCEPTION 'Complete retained identifier required' USING ERRCODE='22023';END IF;
  IF retained_kind IS NOT NULL AND NOT public.mapping_target_access(org,a.property_id,retained_kind,retained_value,true) THEN
    RAISE EXCEPTION 'Retained mapping targets inaccessible' USING ERRCODE='42501';END IF;
  BEGIN basis:=public.mapping_source_basis(org,target);
  EXCEPTION WHEN unique_violation OR check_violation OR insufficient_privilege THEN RETURN jsonb_build_object('allocation_set_id',target,'can_activate',false,'options','[]'::jsonb);END;
  SELECT * INTO STRICT s FROM public.accounting_scope WHERE organization_id=org AND id=a.scope_id;
  FOR signal IN SELECT * FROM jsonb_array_elements(basis->'signals') LOOP
    valid:=signal->>'kind'='property_id' AND signal->>'value'=a.property_id::text;
    IF signal->>'kind'='meter_number' THEN
      valid:=(SELECT count(*)=1 AND bool_and(m.unit_id IS NULL OR EXISTS(SELECT 1 FROM public.unit u WHERE u.organization_id=m.organization_id AND u.id=m.unit_id AND u.property_id=m.property_id))
        FROM public.meter m WHERE m.organization_id=org AND m.property_id=a.property_id AND m.meter_number=signal->>'value');
    END IF;
    IF NOT COALESCE(valid,false) OR NOT public.mapping_target_access(org,a.property_id,signal->>'kind',signal->>'value',true) THEN CONTINUE;END IF;
    SELECT * INTO head FROM public.mapping_version WHERE organization_id=org AND property_id=a.property_id AND identifier_kind=signal->>'kind' AND identifier_value=signal->>'value' ORDER BY revision_number DESC LIMIT 1;
    options:=options||jsonb_build_array(signal||jsonb_build_object('current',CASE WHEN head.id IS NOT NULL THEN public.mapping_descriptor(head) END));
  END LOOP;
  RETURN jsonb_build_object('allocation_set_id',target,'property_id',a.property_id,'scope_id',a.scope_id,'scope_label',s.label,
    'can_activate',s.scope_type IN ('WEG','MIET') AND s.whole_property,'options',options);
END $$;

CREATE FUNCTION context_mapping_candidates(org uuid,logical_id uuid,partition_id uuid,extraction_id uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE source jsonb;signals jsonb;start_date date;end_date date;v public.mapping_version;items jsonb:='[]';
  selected uuid:=NULLIF(current_setting('app.current_scope_id',true),'')::uuid;
BEGIN
  source:=public.assignment_candidate_source(org,logical_id,partition_id,extraction_id);signals:=public.mapping_source_signals(source->'pages');
  BEGIN start_date:=(source->>'service_period_from')::date;end_date:=(source->>'service_period_to')::date;
  EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RETURN jsonb_build_object('items',items,'status','missing_period');END;
  IF start_date IS NULL OR end_date IS NULL OR start_date>end_date THEN RETURN jsonb_build_object('items',items,'status','missing_period');END IF;
  FOR v IN WITH heads AS MATERIALIZED (
    SELECT DISTINCT ON(property_id,identifier_kind,identifier_value) * FROM public.mapping_version WHERE organization_id=org
      ORDER BY property_id,identifier_kind,identifier_value,revision_number DESC
  ) SELECT h.* FROM heads h WHERE h.state='active' AND (selected IS NULL OR selected=h.scope_id)
    AND public.mapping_target_access(org,h.property_id,h.identifier_kind,h.identifier_value,true)
    AND start_date>=h.valid_from AND end_date<=h.valid_to AND public.mapping_master_current(h)
    AND EXISTS(SELECT 1 FROM jsonb_array_elements(signals) e WHERE e->>'kind'=h.identifier_kind AND e->>'value'=h.identifier_value)
    ORDER BY h.id LIMIT 501
  LOOP
    items:=items||jsonb_build_array(public.mapping_descriptor(v));
    IF jsonb_array_length(items)>500 THEN RETURN jsonb_build_object('items','[]'::jsonb,'status','comparison_limit');END IF;
  END LOOP;
  RETURN jsonb_build_object('items',items,'status','manual_review');
END $$;
REVOKE ALL ON FUNCTION context_mapping_status(uuid,uuid),context_mapping_options(uuid,uuid,text,text),context_mapping_candidates(uuid,uuid,uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION context_mapping_status(uuid,uuid),context_mapping_options(uuid,uuid,text,text),context_mapping_candidates(uuid,uuid,uuid,uuid) TO app_rw;
'''
