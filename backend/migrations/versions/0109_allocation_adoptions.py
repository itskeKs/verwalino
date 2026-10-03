"""Atomically adopt exact reviewed costs and publish one typed original bridge."""
from alembic import op
from sqlalchemy import text

revision='0109'
down_revision='0108'
branch_labels=None
depends_on=None
BEFORE="UNION ALL SELECT 'allocation_treatment_line',id,to_jsonb(r) FROM public.allocation_treatment_line r WHERE organization_id=org) evidence"
AFTER=BEFORE.replace(') evidence'," UNION ALL SELECT 'allocation_set',id,to_jsonb(r) FROM public.allocation_set r WHERE organization_id=org UNION ALL SELECT 'document_cost_link',id,to_jsonb(r) FROM public.document_cost_link r WHERE organization_id=org) evidence")


def upgrade():
    op.execute("""DO $$ DECLARE row record;BEGIN
      FOR row IN SELECT id FROM organization ORDER BY id LOOP PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||row.id::text,0));END LOOP;
      FOR row IN SELECT id FROM organization ORDER BY id LOOP PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||row.id::text,0));END LOOP;
      END $$;
      LOCK TABLE invoice_document,cost_entry,allocation_treatment_review,allocation_proposal IN SHARE ROW EXCLUSIVE MODE NOWAIT;""")
    op.execute('''
    CREATE TABLE allocation_integrity_attestation(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,intake_original_id uuid NOT NULL,
      actor_id uuid NOT NULL,request_key uuid NOT NULL,request_digest text NOT NULL,
      backend_pid integer NOT NULL,transaction_id xid8 NOT NULL,content_sha256 text NOT NULL,content_byte_size bigint NOT NULL,
      verification_method text NOT NULL DEFAULT 'uri_hash_size' CHECK(verification_method='uri_hash_size'),
      verified_at timestamptz NOT NULL DEFAULT clock_timestamp(),expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '5 minutes',
      UNIQUE(organization_id,id),UNIQUE(organization_id,actor_id,request_key,backend_pid,transaction_id),
      FOREIGN KEY(organization_id,intake_original_id) REFERENCES intake_original(organization_id,id),
      FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
    ALTER TABLE allocation_integrity_attestation ENABLE ROW LEVEL SECURITY;ALTER TABLE allocation_integrity_attestation FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON allocation_integrity_attestation FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE POLICY integrity_private ON allocation_integrity_attestation USING(false) WITH CHECK(false);
    CREATE TRIGGER integrity_immutable BEFORE UPDATE OR DELETE ON allocation_integrity_attestation FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();

    CREATE TABLE allocation_set(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,
      kind text NOT NULL CHECK(kind IN ('intake_reviewed','proven_legacy')),
      source_document_id uuid NOT NULL,primary_cost_id uuid NOT NULL,property_id uuid NOT NULL,scope_id uuid,
      cost_count integer NOT NULL CHECK(cost_count BETWEEN 1 AND 100),financial_state text NOT NULL DEFAULT 'confirmed' CHECK(financial_state='confirmed'),
      treatment_review_id uuid,allocation_proposal_id uuid,invoice_review_id uuid,document_ref_id uuid,logical_document_id uuid,intake_original_id uuid,
      integrity_attestation_id uuid,content_sha256 text NOT NULL,duplicate_snapshot jsonb,
      actor_id uuid,request_key uuid,request_digest text,reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 2000),
      whole_original_disclosure_reviewed boolean NOT NULL,confirmed_at timestamptz NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CHECK((kind='intake_reviewed' AND scope_id IS NOT NULL AND treatment_review_id IS NOT NULL AND allocation_proposal_id IS NOT NULL
        AND invoice_review_id IS NOT NULL AND document_ref_id IS NOT NULL AND logical_document_id IS NOT NULL AND intake_original_id IS NOT NULL
        AND integrity_attestation_id IS NOT NULL AND duplicate_snapshot IS NOT NULL AND actor_id IS NOT NULL AND request_key IS NOT NULL AND request_digest IS NOT NULL AND whole_original_disclosure_reviewed)
        OR (kind='proven_legacy' AND cost_count=1 AND treatment_review_id IS NULL AND allocation_proposal_id IS NULL AND invoice_review_id IS NULL
        AND document_ref_id IS NULL AND logical_document_id IS NULL AND intake_original_id IS NULL AND integrity_attestation_id IS NULL
        AND duplicate_snapshot IS NULL AND request_key IS NULL AND request_digest IS NULL AND NOT whole_original_disclosure_reviewed)),
      UNIQUE(organization_id,id),UNIQUE(organization_id,source_document_id),UNIQUE(organization_id,source_document_id,id),
      UNIQUE(organization_id,intake_original_id),UNIQUE(organization_id,logical_document_id),UNIQUE(organization_id,actor_id,request_key),
      UNIQUE(organization_id,integrity_attestation_id),UNIQUE(organization_id,allocation_proposal_id,id),
      FOREIGN KEY(organization_id,source_document_id) REFERENCES invoice_document(organization_id,id) DEFERRABLE INITIALLY DEFERRED,
      FOREIGN KEY(organization_id,primary_cost_id) REFERENCES cost_entry(organization_id,id) DEFERRABLE INITIALLY DEFERRED,
      FOREIGN KEY(organization_id,property_id,scope_id) REFERENCES accounting_scope(organization_id,property_id,id),
      FOREIGN KEY(organization_id,allocation_proposal_id,treatment_review_id) REFERENCES allocation_treatment_review(organization_id,allocation_proposal_id,id),
      FOREIGN KEY(organization_id,logical_document_id,allocation_proposal_id) REFERENCES allocation_proposal(organization_id,logical_document_id,id),
      FOREIGN KEY(organization_id,scope_id,document_ref_id,logical_document_id,invoice_review_id) REFERENCES invoice_review_revision(organization_id,scope_id,document_ref_id,logical_document_id,id),
      FOREIGN KEY(organization_id,intake_original_id) REFERENCES intake_original(organization_id,id),
      FOREIGN KEY(organization_id,integrity_attestation_id) REFERENCES allocation_integrity_attestation(organization_id,id),
      FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
    CREATE UNIQUE INDEX allocation_once_per_original_hash ON allocation_set(organization_id,content_sha256) WHERE kind='intake_reviewed';
    CREATE TABLE document_cost_link(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,allocation_set_id uuid NOT NULL,source_document_id uuid NOT NULL,
      cost_entry_id uuid NOT NULL,allocation_proposal_id uuid,allocation_line_id uuid,position integer NOT NULL CHECK(position BETWEEN 1 AND 100),
      link_role text NOT NULL DEFAULT 'primary_charge' CHECK(link_role='primary_charge'),
      CHECK((allocation_proposal_id IS NULL)=(allocation_line_id IS NULL)),
      UNIQUE(organization_id,source_document_id,cost_entry_id),UNIQUE(organization_id,allocation_set_id,position),UNIQUE(organization_id,allocation_set_id,allocation_line_id),
      FOREIGN KEY(organization_id,source_document_id,allocation_set_id) REFERENCES allocation_set(organization_id,source_document_id,id),
      FOREIGN KEY(organization_id,allocation_proposal_id,allocation_set_id) REFERENCES allocation_set(organization_id,allocation_proposal_id,id),
      FOREIGN KEY(organization_id,allocation_proposal_id,allocation_line_id) REFERENCES allocation_proposal_line(organization_id,allocation_proposal_id,id),
      FOREIGN KEY(organization_id,cost_entry_id) REFERENCES cost_entry(organization_id,id) DEFERRABLE INITIALLY DEFERRED);
    CREATE UNIQUE INDEX intake_cost_one_allocation ON document_cost_link(organization_id,cost_entry_id) WHERE allocation_line_id IS NOT NULL;
    CREATE FUNCTION allocation_set_access(org uuid,target uuid) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT EXISTS(SELECT 1 FROM public.allocation_set a WHERE a.organization_id=org AND a.id=target
        AND public.invoice_source_access(org,a.source_document_id,'read'));
    $$;
    REVOKE ALL ON FUNCTION allocation_set_access(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION allocation_set_access(uuid,uuid) TO app_rw;
    ''')
    for table,target in (('allocation_set','id'),('document_cost_link','allocation_set_id')):
        op.execute(f'''ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review;
          CREATE POLICY allocation_set_read ON {table} FOR SELECT USING(allocation_set_access(organization_id,{target}));
          CREATE POLICY allocation_set_no_insert ON {table} FOR INSERT WITH CHECK(false);
          CREATE TRIGGER allocation_set_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();''')
    op.execute("""GRANT SELECT(id,organization_id,kind,source_document_id,primary_cost_id,property_id,scope_id,cost_count,financial_state,content_sha256,actor_id,reason,whole_original_disclosure_reviewed,confirmed_at,created_at) ON allocation_set TO app_rw;
      GRANT SELECT(id,organization_id,allocation_set_id,source_document_id,cost_entry_id,position,link_role) ON document_cost_link TO app_rw;""")
    op.execute(READINESS_SQL)
    op.execute(ADMISSION_SQL)
    op.execute(GUARDS_SQL)
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('guard_invoice_custody()'::regprocedure)"))
    assert definition.count(GUARD_BEFORE)==1
    op.execute(definition.replace(GUARD_BEFORE,GUARD_AFTER))
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('privacy_retention_facts(uuid,uuid)'::regprocedure)"))
    assert definition.count(PRIVACY_BEFORE)==2
    op.execute(definition.replace(PRIVACY_BEFORE,PRIVACY_AFTER))
    op.execute(COMMAND_SQL)
    op.execute('''
      INSERT INTO allocation_set(organization_id,kind,source_document_id,primary_cost_id,property_id,scope_id,cost_count,content_sha256,actor_id,reason,whole_original_disclosure_reviewed,confirmed_at)
        SELECT d.organization_id,'proven_legacy',d.id,c.id,c.property_id,b.scope_id,1,d.content_sha256,d.released_by,
          'Proven retained legacy invoice primary link',false,d.released_at
        FROM invoice_document d JOIN cost_entry c ON c.organization_id=d.organization_id AND c.id=d.cost_entry_id
        LEFT JOIN accounting_scope_binding b ON b.organization_id=c.organization_id AND b.cost_entry_id=c.id
        WHERE d.released_at IS NOT NULL AND c.status='confirmed';
      INSERT INTO document_cost_link(organization_id,allocation_set_id,source_document_id,cost_entry_id,position)
        SELECT organization_id,id,source_document_id,primary_cost_id,1 FROM allocation_set WHERE kind='proven_legacy';
      ''')
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(BEFORE)==1
    op.execute(definition.replace(BEFORE,AFTER))


def downgrade():
    op.execute('LOCK TABLE allocation_set,document_cost_link,allocation_integrity_attestation IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM allocation_set WHERE kind='intake_reviewed') OR EXISTS(SELECT 1 FROM allocation_integrity_attestation) THEN RAISE EXCEPTION 'Retain adopted allocation evidence; repair forward';END IF;END $$")
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(AFTER)==1
    op.execute(definition.replace(AFTER,BEFORE))
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('guard_invoice_custody()'::regprocedure)"))
    assert definition.count(GUARD_AFTER)==1
    op.execute(definition.replace(GUARD_AFTER,GUARD_BEFORE))
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('privacy_retention_facts(uuid,uuid)'::regprocedure)"))
    assert definition.count(PRIVACY_AFTER)==2
    op.execute(definition.replace(PRIVACY_AFTER,PRIVACY_BEFORE))
    op.execute('DROP FUNCTION IF EXISTS allocation_adoption_result(uuid,uuid)')
    op.execute('''DROP TRIGGER a_protect_adopted_cost ON cost_entry;DROP TRIGGER a_protect_adopted_invoice ON invoice_document;
      DROP TABLE document_cost_link,allocation_set,allocation_integrity_attestation;
      DROP FUNCTION adopt_allocation(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,uuid),
        prepare_allocation_adoption(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean),attest_allocation_integrity(uuid,uuid,uuid,text,integer,text,text,bigint),
        allocation_adoption_preview(uuid,uuid),require_current_adoption_basis(uuid,uuid),require_adoption_authority(uuid,uuid),
        adoption_duplicate_snapshot(uuid,uuid),adoption_invoice_identity(public.invoice_review_revision),allocation_set_access(uuid,uuid),
        adoption_bridge_admission(public.invoice_document),validate_allocation_set_membership(),protect_adopted_cost(),protect_adopted_invoice();''')


READINESS_SQL='''
CREATE FUNCTION adoption_invoice_identity(r public.invoice_review_revision) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
  SELECT jsonb_build_array(lower(regexp_replace(btrim(r.seller_name),'[[:space:]]+',' ','g')),btrim(r.document_number),r.document_kind,
    r.currency,round(r.gross_amount,2)::text,r.service_period_from,r.service_period_to);
$$;
REVOKE ALL ON FUNCTION adoption_invoice_identity(public.invoice_review_revision) FROM PUBLIC,app_rw,app_worker,app_review;

CREATE FUNCTION adoption_duplicate_snapshot(org uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE subject public.invoice_review_revision;candidate public.invoice_review_revision;comparison public.duplicate_comparison;
  record_item record;entries jsonb:='[]'::jsonb;raw_sources jsonb:='[]'::jsonb;legacy jsonb:='[]'::jsonb;legacy_costs jsonb:='[]'::jsonb;
  allowed boolean;blocked boolean:=false;same_bytes boolean;consumed boolean;visible boolean;
BEGIN
  SELECT * INTO STRICT subject FROM public.invoice_review_revision WHERE organization_id=org AND id=target;
  FOR candidate IN WITH heads AS MATERIALIZED (
    SELECT DISTINCT ON(logical_document_id) * FROM public.invoice_review_revision WHERE organization_id=org ORDER BY logical_document_id,revision_number DESC
  ), retained AS (
    SELECT r.* FROM public.allocation_set s JOIN public.invoice_review_revision r ON r.organization_id=s.organization_id AND r.id=s.invoice_review_id WHERE s.organization_id=org
  ) SELECT * FROM (SELECT * FROM heads UNION SELECT * FROM retained) r
    WHERE r.document_ref_id<>subject.document_ref_id
      AND (r.content_sha256=subject.content_sha256 OR public.adoption_invoice_identity(r::public.invoice_review_revision)=public.adoption_invoice_identity(subject)) ORDER BY r.id
  LOOP
    same_bytes:=candidate.content_sha256=subject.content_sha256;
    consumed:=EXISTS(SELECT 1 FROM public.allocation_set WHERE organization_id=org AND kind='intake_reviewed' AND document_ref_id=candidate.document_ref_id);
    visible:=public.assignment_proposal_access(org,candidate.document_ref_id,candidate.scope_id);
    SELECT * INTO comparison FROM public.duplicate_comparison WHERE organization_id=org AND scope_id=subject.scope_id
      AND pair_low=least(subject.logical_document_id,candidate.logical_document_id) AND pair_high=greatest(subject.logical_document_id,candidate.logical_document_id)
      ORDER BY revision_number DESC LIMIT 1;
    allowed:=visible AND candidate.scope_id=subject.scope_id AND comparison.id IS NOT NULL
      AND ((comparison.subject_review_id=subject.id AND comparison.reference_review_id=candidate.id) OR (comparison.reference_review_id=subject.id AND comparison.subject_review_id=candidate.id))
      AND comparison.verdict=CASE WHEN same_bytes THEN 'same_document' ELSE 'distinct_documents' END
      AND NOT (same_bytes AND (consumed OR subject.document_kind<>candidate.document_kind));
    blocked:=blocked OR NOT COALESCE(allowed,false);
    entries:=entries||jsonb_build_array(jsonb_build_object('review',candidate.id,'source',candidate.document_ref_id,'identity',public.adoption_invoice_identity(candidate),
      'hash',candidate.content_sha256,'comparison',comparison.id,'verdict',comparison.verdict,'consumed',consumed,'visible',visible,'allowed',COALESCE(allowed,false)));
  END LOOP;
  FOR record_item IN SELECT o.id,r.id AS ref_id,j.status FROM public.intake_original o
    LEFT JOIN public.document_ref r ON r.organization_id=o.organization_id AND r.intake_original_id=o.id
    JOIN public.workflow_job j ON j.organization_id=o.organization_id AND j.original_id=o.id
    WHERE o.organization_id=org AND o.content_sha256=subject.content_sha256 AND o.id<>(SELECT intake_original_id FROM public.document_ref WHERE organization_id=org AND id=subject.document_ref_id) ORDER BY o.id
  LOOP
    allowed:=record_item.status='clean' AND EXISTS(SELECT 1 FROM jsonb_array_elements(entries) e WHERE e->>'source'=record_item.ref_id::text AND e->>'allowed'='true');
    blocked:=blocked OR NOT allowed;
    raw_sources:=raw_sources||jsonb_build_array(jsonb_build_object('original',record_item.id,'inspection',record_item.status,'allowed',allowed));
  END LOOP;
  FOR record_item IN SELECT d.id,d.content_sha256,d.released_at,d.cost_entry_id,c.status,c.gross_amount,c.document_number,c.service_period_from,c.service_period_to
    FROM public.invoice_document d LEFT JOIN public.cost_entry c ON c.organization_id=d.organization_id AND c.id=d.cost_entry_id
    WHERE d.organization_id=org AND NOT EXISTS(SELECT 1 FROM public.allocation_set a WHERE a.organization_id=org AND a.source_document_id=d.id AND a.kind='intake_reviewed')
      AND (d.content_sha256=subject.content_sha256 OR (c.status='confirmed' AND btrim(c.document_number)=btrim(subject.document_number)
        AND c.gross_amount=subject.gross_amount AND c.service_period_from=subject.service_period_from AND c.service_period_to=subject.service_period_to)) ORDER BY d.id
  LOOP
    blocked:=true;legacy:=legacy||jsonb_build_array(to_jsonb(record_item));
  END LOOP;
  FOR record_item IN SELECT c.id,c.document_number,c.gross_amount,c.service_period_from,c.service_period_to,c.superseded_by_id
    FROM public.cost_entry c WHERE c.organization_id=org AND c.status='confirmed'
      AND btrim(c.document_number)=btrim(subject.document_number) AND c.gross_amount=subject.gross_amount
      AND c.service_period_from=subject.service_period_from AND c.service_period_to=subject.service_period_to
      AND NOT EXISTS(SELECT 1 FROM public.document_cost_link l JOIN public.allocation_set a ON a.organization_id=l.organization_id AND a.id=l.allocation_set_id
        WHERE l.organization_id=org AND l.cost_entry_id=c.id AND a.kind='intake_reviewed') ORDER BY c.id
  LOOP blocked:=true;legacy_costs:=legacy_costs||jsonb_build_array(to_jsonb(record_item));END LOOP;
  IF EXISTS(SELECT 1 FROM public.allocation_set WHERE organization_id=org AND kind='intake_reviewed' AND content_sha256=subject.content_sha256) THEN blocked:=true;END IF;
  RETURN jsonb_build_object('policy','adoption-duplicates-v1','blocked',blocked,'reviews',entries,'same_byte_receipts',raw_sources,'legacy_sources',legacy,'legacy_costs',legacy_costs);
END $$;
REVOKE ALL ON FUNCTION adoption_duplicate_snapshot(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;

CREATE FUNCTION require_adoption_authority(org uuid,target uuid) RETURNS public.allocation_proposal
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE a public.allocation_proposal;original uuid;
BEGIN
  PERFORM public.lock_intake(org);
  SELECT p.* INTO a FROM public.allocation_treatment_review t JOIN public.allocation_proposal p ON p.organization_id=t.organization_id AND p.id=t.allocation_proposal_id WHERE t.organization_id=org AND t.id=target;
  SELECT intake_original_id INTO original FROM public.document_ref WHERE organization_id=org AND id=a.document_ref_id;
  IF a.id IS NULL OR NOT public.allocation_proposal_access(org,a.id) OR NOT public.source_partition_access(org,a.document_ref_id,true)
    OR NOT public.intake_original_export_access(org,original) OR NOT public.has_scope_capability(org,a.scope_id,'prepare')
    OR NOT public.has_scope_capability(org,a.scope_id,'confirm') THEN RAISE EXCEPTION 'Allocation confirmation inaccessible' USING ERRCODE='42501';END IF;
  RETURN a;
END $$;
REVOKE ALL ON FUNCTION require_adoption_authority(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;

CREATE FUNCTION require_current_adoption_basis(org uuid,target uuid) RETURNS public.allocation_proposal
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE a public.allocation_proposal;t public.allocation_treatment_review;latest public.allocation_treatment_review;current_allocation public.allocation_proposal;
  r public.invoice_review_revision;p public.source_partition_revision;snapshot jsonb;
BEGIN
  a:=public.require_adoption_authority(org,target);
  SELECT * INTO STRICT t FROM public.allocation_treatment_review WHERE organization_id=org AND id=target;
  SELECT * INTO latest FROM public.allocation_treatment_review WHERE organization_id=org AND logical_document_id=a.logical_document_id ORDER BY revision_number DESC LIMIT 1;
  SELECT * INTO current_allocation FROM public.allocation_proposal WHERE organization_id=org AND logical_document_id=a.logical_document_id ORDER BY revision_number DESC LIMIT 1;
  IF NOT public.allocation_proposal_access(org,latest.allocation_proposal_id) OR NOT public.allocation_proposal_access(org,current_allocation.id) THEN
    RAISE EXCEPTION 'Current adoption basis inaccessible' USING ERRCODE='42501';END IF;
  IF latest.id IS DISTINCT FROM t.id OR current_allocation.id IS DISTINCT FROM a.id THEN RAISE EXCEPTION 'Adoption basis changed' USING ERRCODE='23505';END IF;
  PERFORM public.require_current_context_decision(org,a.context_decision_id);
  SELECT * INTO STRICT r FROM public.invoice_review_revision WHERE organization_id=org AND id=a.invoice_review_id;
  IF extract(year FROM r.invoice_date)>9991 THEN RAISE EXCEPTION 'Invoice date exceeds supported retention range' USING ERRCODE='23514';END IF;
  SELECT * INTO STRICT p FROM public.source_partition_revision WHERE organization_id=org AND id=r.partition_revision_id;
  IF p.member_count<>1 OR NOT EXISTS(SELECT 1 FROM public.source_partition_member WHERE organization_id=org AND partition_revision_id=p.id AND logical_document_id=a.logical_document_id)
    THEN RAISE EXCEPTION 'Whole original single-scope adoption required' USING ERRCODE='23514';END IF;
  BEGIN
    PERFORM 1 FROM public.cost_category c WHERE c.id IN(SELECT cost_category_id FROM public.allocation_proposal_line WHERE organization_id=org AND allocation_proposal_id=a.id) ORDER BY c.id FOR SHARE NOWAIT;
  EXCEPTION WHEN lock_not_available THEN RAISE EXCEPTION 'Adoption categories busy' USING ERRCODE='23505';END;
  snapshot:=public.allocation_treatment_snapshot(org,a.id);
  IF t.snapshot_digest IS DISTINCT FROM encode(sha256(convert_to(snapshot::text,'UTF8')),'hex') THEN RAISE EXCEPTION 'Treatment category basis changed' USING ERRCODE='23505';END IF;
  IF EXISTS(SELECT 1 FROM public.allocation_treatment_line l WHERE l.organization_id=org AND l.treatment_review_id=t.id
    AND (l.markers<>'[]'::jsonb OR l.expense_intent<>'operating_expense' OR l.tax_intent<>'gross_without_input_vat'))
    OR EXISTS(SELECT 1 FROM public.allocation_proposal_line l JOIN public.cost_category c ON c.id=l.cost_category_id
      WHERE l.organization_id=org AND l.allocation_proposal_id=a.id AND (NOT c.is_active OR (snapshot->>'scope_type'='WEG' AND NOT c.owner_billing_relevant_default))) THEN
    RAISE EXCEPTION 'Unsupported treatment requires further review' USING ERRCODE='23514';END IF;
  RETURN a;
END $$;
REVOKE ALL ON FUNCTION require_current_adoption_basis(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
'''


ADMISSION_SQL='''
CREATE FUNCTION allocation_adoption_result(org uuid,logical_id uuid) RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE ref uuid;result uuid;
BEGIN
  SELECT document_ref_id INTO ref FROM public.logical_document WHERE organization_id=org AND id=logical_id;
  IF ref IS NULL OR NOT public.source_partition_access(org,ref,false) THEN RAISE EXCEPTION 'Logical source inaccessible' USING ERRCODE='42501';END IF;
  SELECT id INTO result FROM public.allocation_set WHERE organization_id=org AND logical_document_id=logical_id;
  IF result IS NOT NULL AND NOT public.allocation_set_access(org,result) THEN RAISE EXCEPTION 'Published allocation inaccessible' USING ERRCODE='42501';END IF;
  RETURN result;
END $$;
REVOKE ALL ON FUNCTION allocation_adoption_result(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION allocation_adoption_result(uuid,uuid) TO app_rw;

CREATE FUNCTION allocation_adoption_preview(org uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE a public.allocation_proposal;t public.allocation_treatment_review;duplicates jsonb;digest text;
BEGIN
  SELECT * INTO t FROM public.allocation_treatment_review WHERE organization_id=org AND id=target;
  IF t.id IS NULL OR NOT public.allocation_proposal_access(org,t.allocation_proposal_id) THEN RAISE EXCEPTION 'Adoption review inaccessible' USING ERRCODE='42501';END IF;
  BEGIN
    a:=public.require_current_adoption_basis(org,target);
  EXCEPTION WHEN insufficient_privilege OR unique_violation OR check_violation THEN
    RETURN jsonb_build_object('can_adopt',false,'preview_digest',NULL,'blocker','Current eligible source and confirmation authority required');
  END;
  duplicates:=public.adoption_duplicate_snapshot(org,a.invoice_review_id);
  digest:=encode(sha256(convert_to(jsonb_build_array(target,t.snapshot_digest,duplicates)::text,'UTF8')),'hex');
  RETURN jsonb_build_object('can_adopt',NOT (duplicates->>'blocked')::boolean,'preview_digest',digest,
    'blocker',CASE WHEN (duplicates->>'blocked')::boolean THEN 'Source conflict requires further review' END,
    'scope_id',a.scope_id,'scope_label',(SELECT snapshot->'scope'->>'label' FROM public.context_decision WHERE organization_id=org AND id=a.context_decision_id),
    'cost_count',a.line_count,'gross_amount',(SELECT gross_amount::text FROM public.invoice_review_revision WHERE organization_id=org AND id=a.invoice_review_id),
    'disclosure','Complete original published to the destination accounting scope');
END $$;
REVOKE ALL ON FUNCTION allocation_adoption_preview(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION allocation_adoption_preview(uuid,uuid) TO app_rw;

CREATE FUNCTION prepare_allocation_adoption(org uuid,logical_id uuid,target uuid,expected uuid,request uuid,preview_digest text,explanation text,confirmed boolean,disclosed boolean) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;a public.allocation_proposal;prior public.allocation_set;
  original public.intake_original;t public.allocation_treatment_review;duplicates jsonb;digest text;current_digest text;
BEGIN
  IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501';END IF;
  a:=public.require_adoption_authority(org,target);
  IF a.logical_document_id IS DISTINCT FROM logical_id THEN RAISE EXCEPTION 'Adoption logical source inaccessible' USING ERRCODE='42501';END IF;
  IF request IS NULL OR expected IS DISTINCT FROM target OR preview_digest IS NULL OR preview_digest !~ '^[0-9a-f]{64}$'
    OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 2000 OR confirmed IS DISTINCT FROM true OR disclosed IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Exact reviewed confirmation and original disclosure required' USING ERRCODE='22023';END IF;
  digest:=encode(sha256(convert_to(jsonb_build_array(logical_id,target,expected,preview_digest,explanation,confirmed,disclosed)::text,'UTF8')),'hex');
  SELECT * INTO prior FROM public.allocation_set WHERE organization_id=org AND actor_id=actor AND request_key=request;
  IF FOUND THEN
    IF prior.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Adoption request changed' USING ERRCODE='23505';END IF;
    RETURN jsonb_build_object('replay_id',prior.id);
  END IF;
  a:=public.require_current_adoption_basis(org,target);
  SELECT * INTO STRICT t FROM public.allocation_treatment_review WHERE organization_id=org AND id=target;
  duplicates:=public.adoption_duplicate_snapshot(org,a.invoice_review_id);
  current_digest:=encode(sha256(convert_to(jsonb_build_array(target,t.snapshot_digest,duplicates)::text,'UTF8')),'hex');
  IF current_digest IS DISTINCT FROM preview_digest THEN RAISE EXCEPTION 'Adoption evidence changed' USING ERRCODE='23505';END IF;
  IF (duplicates->>'blocked')::boolean THEN RAISE EXCEPTION 'Source conflict requires further review' USING ERRCODE='23505';END IF;
  SELECT o.* INTO STRICT original FROM public.intake_original o JOIN public.document_ref r ON r.organization_id=o.organization_id AND r.intake_original_id=o.id WHERE r.organization_id=org AND r.id=a.document_ref_id;
  IF EXISTS(SELECT 1 FROM public.allocation_set WHERE organization_id=org AND (intake_original_id=original.id OR logical_document_id=logical_id)) THEN
    RAISE EXCEPTION 'Source already adopted' USING ERRCODE='23505';END IF;
  RETURN jsonb_build_object('replay_id',NULL,'original_id',original.id,'storage_uri',original.storage_uri,'storage_backend',original.storage_backend,
    'storage_version',original.storage_version,'content_sha256',original.content_sha256,'content_byte_size',original.content_byte_size,
    'backend_pid',pg_backend_pid(),'transaction_id',pg_current_xact_id()::text,'request_digest',digest);
END $$;
REVOKE ALL ON FUNCTION prepare_allocation_adoption(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION prepare_allocation_adoption(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean) TO app_rw;

CREATE FUNCTION attest_allocation_integrity(org uuid,original_id uuid,request uuid,digest text,caller_pid integer,caller_transaction text,observed_hash text,observed_size bigint) RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;original public.intake_original;prior public.allocation_integrity_attestation;created uuid;
BEGIN
  IF session_user<>'app_review' OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
    RAISE EXCEPTION 'Trusted integrity runtime required' USING ERRCODE='42501';END IF;
  -- The caller holds ACL/financial locks. Do not acquire either on this connection.
  -- Bound any FK wait too; an issuer must never create an application-level lock cycle.
  PERFORM set_config('lock_timeout','200ms',true);
  IF COALESCE((SELECT mode FROM public.property_source_security WHERE organization_id=org),'PREPARATION')='PAUSED'
    OR NOT COALESCE(public.scope_actor_role(org) IN ('OWNER_ADMIN','CLERK'),false) THEN RAISE EXCEPTION 'Current integrity principal required' USING ERRCODE='42501';END IF;
  SELECT * INTO original FROM public.intake_original WHERE organization_id=org AND id=original_id;
  IF original.id IS NULL OR original.uploaded_by IS DISTINCT FROM actor OR observed_hash IS DISTINCT FROM original.content_sha256
    OR observed_size IS DISTINCT FROM original.content_byte_size OR request IS NULL OR digest IS NULL OR digest !~ '^[0-9a-f]{64}$'
    OR caller_pid IS NULL OR caller_transaction IS NULL THEN RAISE EXCEPTION 'Exact observed source integrity required' USING ERRCODE='22023';END IF;
  SELECT * INTO prior FROM public.allocation_integrity_attestation WHERE organization_id=org AND actor_id=actor AND request_key=request AND backend_pid=caller_pid AND transaction_id=caller_transaction::xid8;
  IF FOUND THEN
    IF prior.request_digest IS DISTINCT FROM digest OR prior.intake_original_id IS DISTINCT FROM original.id THEN RAISE EXCEPTION 'Integrity request changed' USING ERRCODE='23505';END IF;
    RETURN prior.id;
  END IF;
  INSERT INTO public.allocation_integrity_attestation(organization_id,intake_original_id,actor_id,request_key,request_digest,backend_pid,transaction_id,content_sha256,content_byte_size)
    VALUES(org,original.id,actor,request,digest,caller_pid,caller_transaction::xid8,observed_hash,observed_size) RETURNING id INTO created;
  RETURN created;
END $$;
REVOKE ALL ON FUNCTION attest_allocation_integrity(uuid,uuid,uuid,text,integer,text,text,bigint) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION attest_allocation_integrity(uuid,uuid,uuid,text,integer,text,text,bigint) TO app_review;
'''


GUARDS_SQL='''
CREATE FUNCTION adoption_bridge_admission(source public.invoice_document) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
  SELECT EXISTS(SELECT 1 FROM public.allocation_set s JOIN public.intake_original o ON o.organization_id=s.organization_id AND o.id=s.intake_original_id
    JOIN public.intake_item i ON i.organization_id=o.organization_id AND i.id=o.item_id
    JOIN public.invoice_review_revision r ON r.organization_id=s.organization_id AND r.id=s.invoice_review_id
    JOIN public.allocation_integrity_attestation proof ON proof.organization_id=s.organization_id AND proof.id=s.integrity_attestation_id
    WHERE s.organization_id=source.organization_id AND s.source_document_id=source.id AND s.kind='intake_reviewed'
      AND proof.backend_pid=pg_backend_pid() AND proof.transaction_id=pg_current_xact_id() AND proof.expires_at>clock_timestamp()
      AND s.actor_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid AND proof.actor_id=s.actor_id
      AND source.uploaded_by=o.uploaded_by AND source.cost_entry_id=s.primary_cost_id
      AND source.storage_uri=o.storage_uri AND source.storage_backend=o.storage_backend AND source.storage_version IS NOT DISTINCT FROM o.storage_version
      AND source.content_sha256=o.content_sha256 AND source.content_byte_size=o.content_byte_size AND source.mime_type=o.mime_type AND source.original_filename=i.filename
      AND source.version=1 AND source.supersedes_id IS NULL AND source.chain_root_id=source.id AND source.status='active'
      AND source.released_at=s.confirmed_at AND source.released_by=s.actor_id
      AND source.retention_until=make_date(extract(year FROM r.invoice_date)::integer+8,12,31)
      AND source.extraction_payload IS NULL AND source.processing_status='legacy' AND source.processing_error_code IS NULL);
$$;
REVOKE ALL ON FUNCTION adoption_bridge_admission(public.invoice_document) FROM PUBLIC,app_rw,app_worker,app_review;

CREATE FUNCTION validate_allocation_set_membership() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE target uuid;s public.allocation_set;d public.invoice_document;a public.allocation_proposal;r public.invoice_review_revision;n integer;
BEGIN
  IF TG_TABLE_NAME='allocation_set' THEN target:=NEW.id;ELSE target:=NEW.allocation_set_id;END IF;
  SELECT * INTO STRICT s FROM public.allocation_set WHERE organization_id=NEW.organization_id AND id=target;
  SELECT * INTO STRICT d FROM public.invoice_document WHERE organization_id=s.organization_id AND id=s.source_document_id;
  SELECT count(*) INTO n FROM public.document_cost_link WHERE organization_id=s.organization_id AND allocation_set_id=s.id;
  IF n<>s.cost_count OR d.cost_entry_id IS DISTINCT FROM s.primary_cost_id OR d.released_at IS NULL OR d.content_sha256 IS DISTINCT FROM s.content_sha256
    OR NOT EXISTS(SELECT 1 FROM public.document_cost_link WHERE organization_id=s.organization_id AND allocation_set_id=s.id AND position=1 AND cost_entry_id=s.primary_cost_id)
    OR EXISTS(SELECT 1 FROM public.document_cost_link l JOIN public.cost_entry c ON c.organization_id=l.organization_id AND c.id=l.cost_entry_id
      WHERE l.organization_id=s.organization_id AND l.allocation_set_id=s.id AND (c.status<>'confirmed' OR c.property_id<>s.property_id
        OR (SELECT scope_id FROM public.accounting_scope_binding WHERE organization_id=s.organization_id AND cost_entry_id=c.id) IS DISTINCT FROM s.scope_id)) THEN
    RAISE EXCEPTION 'Complete confirmed source allocation required' USING ERRCODE='23514';END IF;
  IF s.kind='proven_legacy' THEN
    IF EXISTS(SELECT 1 FROM public.document_cost_link WHERE organization_id=s.organization_id AND allocation_set_id=s.id AND allocation_line_id IS NOT NULL) THEN
      RAISE EXCEPTION 'Legacy source proof cannot invent reviewed lines' USING ERRCODE='23514';END IF;
    RETURN NULL;
  END IF;
  SELECT * INTO STRICT a FROM public.allocation_proposal WHERE organization_id=s.organization_id AND id=s.allocation_proposal_id;
  SELECT * INTO STRICT r FROM public.invoice_review_revision WHERE organization_id=s.organization_id AND id=s.invoice_review_id;
  IF a.invoice_review_id<>r.id OR a.line_count<>n OR a.property_id<>s.property_id OR a.scope_id<>s.scope_id
    OR NOT EXISTS(SELECT 1 FROM public.document_ref WHERE organization_id=s.organization_id AND id=s.document_ref_id AND intake_original_id=s.intake_original_id)
    OR EXISTS(SELECT 1 FROM public.document_cost_link WHERE organization_id=s.organization_id AND allocation_set_id=s.id AND allocation_line_id IS NULL)
    OR EXISTS(SELECT 1 FROM public.document_cost_link l
      JOIN public.allocation_proposal_line b ON b.organization_id=l.organization_id AND b.id=l.allocation_line_id
      JOIN public.allocation_treatment_line t ON t.organization_id=l.organization_id AND t.allocation_line_id=b.id AND t.treatment_review_id=s.treatment_review_id
      JOIN public.cost_entry c ON c.organization_id=l.organization_id AND c.id=l.cost_entry_id
      WHERE l.organization_id=s.organization_id AND l.allocation_set_id=s.id AND (l.position<>b.position OR c.gross_amount IS DISTINCT FROM b.gross_amount
        OR c.vat_amount IS DISTINCT FROM b.vat_amount OR c.vat_rate IS DISTINCT FROM r.vat_rate OR c.invoice_date<>r.invoice_date
        OR c.service_period_from<>r.service_period_from OR c.service_period_to<>r.service_period_to OR c.document_number IS DISTINCT FROM r.document_number
        OR c.cost_category_id<>b.cost_category_id OR c.cost_category_plan_version<>b.cost_category_plan_version
        OR c.tenant_apportionable IS DISTINCT FROM CASE t.tenant_treatment WHEN 'full' THEN 'UMLAGEFAEHIG' WHEN 'partial' THEN 'TEILWEISE_PRUEFEN' WHEN 'none' THEN 'NICHT_UMLAGEFAEHIG' END
        OR c.apportionable_tenant_amount IS DISTINCT FROM t.tenant_amount OR c.tenant_distribution_key IS DISTINCT FROM t.tenant_distribution_key
        OR c.owner_distribution_key IS DISTINCT FROM NULLIF(t.owner_distribution_key,'not_applicable') OR c.confirmed_at IS DISTINCT FROM s.confirmed_at OR c.confirmed_by IS DISTINCT FROM s.actor_id
        OR c.source IS DISTINCT FROM CASE WHEN d.mime_type='application/xml' THEN 'E_INVOICE' ELSE 'PDF_IMPORT' END)) THEN
    RAISE EXCEPTION 'Exact reviewed cost membership and amounts required' USING ERRCODE='23514';END IF;
  IF NOT EXISTS(SELECT 1 FROM public.intake_original o WHERE o.organization_id=s.organization_id AND o.id=s.intake_original_id
    AND (d.storage_uri,d.storage_backend,d.storage_version,d.content_sha256,d.content_byte_size,d.mime_type,d.uploaded_by)
      IS NOT DISTINCT FROM (o.storage_uri,o.storage_backend,o.storage_version,o.content_sha256,o.content_byte_size,o.mime_type,o.uploaded_by))
    OR d.released_at IS DISTINCT FROM s.confirmed_at OR d.released_by IS DISTINCT FROM s.actor_id OR d.extraction_payload IS NOT NULL THEN
    RAISE EXCEPTION 'Exact released original bridge required' USING ERRCODE='23514';END IF;
  RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION validate_allocation_set_membership() FROM PUBLIC,app_rw,app_worker,app_review;
CREATE CONSTRAINT TRIGGER seal_allocation_set AFTER INSERT ON allocation_set DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_allocation_set_membership();
CREATE CONSTRAINT TRIGGER seal_allocation_set AFTER INSERT ON document_cost_link DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_allocation_set_membership();

CREATE FUNCTION protect_adopted_cost() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE org uuid;target uuid;
BEGIN
  org:=CASE WHEN TG_OP='INSERT' THEN NEW.organization_id ELSE OLD.organization_id END;target:=CASE WHEN TG_OP='INSERT' THEN NEW.id ELSE OLD.id END;
  IF TG_OP<>'INSERT' AND EXISTS(SELECT 1 FROM public.document_cost_link l JOIN public.allocation_set s ON s.organization_id=l.organization_id AND s.id=l.allocation_set_id
    WHERE l.organization_id=org AND l.cost_entry_id=target AND s.kind='intake_reviewed') THEN
    RAISE EXCEPTION 'Reviewed allocation costs require an atomic set correction' USING ERRCODE='23514';END IF;
  IF TG_OP<>'DELETE' AND NEW.status='confirmed' AND NOT EXISTS(SELECT 1 FROM public.document_cost_link l JOIN public.allocation_set s ON s.organization_id=l.organization_id AND s.id=l.allocation_set_id
    WHERE l.organization_id=org AND l.cost_entry_id=target AND s.kind='intake_reviewed') THEN
    IF session_user='app_migrator' THEN
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
    ELSE PERFORM public.lock_intake(org);END IF;
    IF EXISTS(SELECT 1 FROM public.allocation_set s JOIN public.invoice_review_revision r ON r.organization_id=s.organization_id AND r.id=s.invoice_review_id
      WHERE s.organization_id=org AND s.kind='intake_reviewed' AND ((btrim(NEW.document_number)=btrim(r.document_number) AND NEW.gross_amount=r.gross_amount
        AND NEW.service_period_from=r.service_period_from AND NEW.service_period_to=r.service_period_to)
        OR EXISTS(SELECT 1 FROM public.invoice_document d WHERE d.organization_id=org AND d.cost_entry_id=target AND d.content_sha256=s.content_sha256))) THEN
      RAISE EXCEPTION 'Source conflict requires further review' USING ERRCODE='23505';END IF;
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD;ELSE RETURN NEW;END IF;
END $$;
REVOKE ALL ON FUNCTION protect_adopted_cost() FROM PUBLIC,app_rw,app_worker,app_review;
CREATE TRIGGER a_protect_adopted_cost BEFORE INSERT OR UPDATE OR DELETE ON cost_entry FOR EACH ROW EXECUTE FUNCTION protect_adopted_cost();

CREATE FUNCTION protect_adopted_invoice() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE org uuid;target uuid;
BEGIN
  org:=CASE WHEN TG_OP='INSERT' THEN NEW.organization_id ELSE OLD.organization_id END;target:=CASE WHEN TG_OP='INSERT' THEN NEW.id ELSE OLD.id END;
  IF TG_OP<>'INSERT' AND EXISTS(SELECT 1 FROM public.allocation_set WHERE organization_id=org AND source_document_id=target AND kind='intake_reviewed') THEN
    RAISE EXCEPTION 'Released allocation source requires an atomic set correction' USING ERRCODE='23514';END IF;
  IF TG_OP<>'DELETE' THEN
    IF NEW.cost_entry_id IS NOT NULL AND EXISTS(SELECT 1 FROM public.document_cost_link l JOIN public.allocation_set s ON s.organization_id=l.organization_id AND s.id=l.allocation_set_id
      WHERE l.organization_id=org AND l.cost_entry_id=NEW.cost_entry_id AND s.kind='intake_reviewed' AND s.source_document_id<>NEW.id) THEN
      RAISE EXCEPTION 'Allocation member already has its complete source' USING ERRCODE='23514';END IF;
    IF NEW.released_at IS NOT NULL AND EXISTS(SELECT 1 FROM public.allocation_set WHERE organization_id=org AND kind='intake_reviewed'
      AND content_sha256=NEW.content_sha256 AND source_document_id<>NEW.id) THEN
      RAISE EXCEPTION 'Source conflict requires further review' USING ERRCODE='23505';END IF;
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD;ELSE RETURN NEW;END IF;
END $$;
REVOKE ALL ON FUNCTION protect_adopted_invoice() FROM PUBLIC,app_rw,app_worker,app_review;
CREATE TRIGGER a_protect_adopted_invoice BEFORE INSERT OR UPDATE OR DELETE ON invoice_document FOR EACH ROW EXECUTE FUNCTION protect_adopted_invoice();
'''


COMMAND_SQL='''
CREATE FUNCTION adopt_allocation(org uuid,logical_id uuid,target uuid,expected uuid,request uuid,preview_digest text,explanation text,confirmed boolean,disclosed boolean,attestation uuid) RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;prepared jsonb;proof public.allocation_integrity_attestation;
  a public.allocation_proposal;r public.invoice_review_revision;original public.intake_original;basis record;
  duplicates jsonb;created uuid:=gen_random_uuid();document uuid:=gen_random_uuid();costs uuid[];cost uuid;moment timestamptz:=clock_timestamp();old_scope text;
BEGIN
  prepared:=public.prepare_allocation_adoption(org,logical_id,target,expected,request,preview_digest,explanation,confirmed,disclosed);
  IF prepared->>'replay_id' IS NOT NULL THEN RETURN (prepared->>'replay_id')::uuid;END IF;
  SELECT * INTO proof FROM public.allocation_integrity_attestation WHERE organization_id=org AND id=attestation;
  IF proof.id IS NULL OR proof.actor_id IS DISTINCT FROM actor OR proof.request_key IS DISTINCT FROM request
    OR proof.request_digest IS DISTINCT FROM prepared->>'request_digest' OR proof.intake_original_id IS DISTINCT FROM (prepared->>'original_id')::uuid
    OR proof.backend_pid IS DISTINCT FROM pg_backend_pid() OR proof.transaction_id IS DISTINCT FROM pg_current_xact_id()
    OR proof.expires_at<=clock_timestamp() OR proof.content_sha256 IS DISTINCT FROM prepared->>'content_sha256'
    OR proof.content_byte_size IS DISTINCT FROM (prepared->>'content_byte_size')::bigint THEN
    RAISE EXCEPTION 'Current transaction integrity attestation required' USING ERRCODE='42501';END IF;
  SELECT p.* INTO STRICT a FROM public.allocation_treatment_review t JOIN public.allocation_proposal p ON p.organization_id=t.organization_id AND p.id=t.allocation_proposal_id WHERE t.organization_id=org AND t.id=target;
  SELECT * INTO STRICT r FROM public.invoice_review_revision WHERE organization_id=org AND id=a.invoice_review_id;
  SELECT * INTO STRICT original FROM public.intake_original WHERE organization_id=org AND id=proof.intake_original_id;
  SELECT array_agg(gen_random_uuid() ORDER BY position) INTO costs FROM public.allocation_proposal_line WHERE organization_id=org AND allocation_proposal_id=a.id;
  duplicates:=public.adoption_duplicate_snapshot(org,r.id);
  INSERT INTO public.allocation_set(id,organization_id,kind,source_document_id,primary_cost_id,property_id,scope_id,cost_count,treatment_review_id,allocation_proposal_id,
    invoice_review_id,document_ref_id,logical_document_id,intake_original_id,integrity_attestation_id,content_sha256,duplicate_snapshot,actor_id,request_key,request_digest,reason,whole_original_disclosure_reviewed,confirmed_at)
    VALUES(created,org,'intake_reviewed',document,costs[1],a.property_id,a.scope_id,a.line_count,target,a.id,r.id,a.document_ref_id,logical_id,original.id,proof.id,
      original.content_sha256,duplicates,actor,request,prepared->>'request_digest',explanation,true,moment);
  INSERT INTO public.document_cost_link(organization_id,allocation_set_id,source_document_id,cost_entry_id,allocation_proposal_id,allocation_line_id,position)
    SELECT org,created,document,costs[position],a.id,id,position FROM public.allocation_proposal_line WHERE organization_id=org AND allocation_proposal_id=a.id;
  old_scope:=current_setting('app.current_scope_id',true);PERFORM set_config('app.current_scope_id',a.scope_id::text,true);
  FOR basis IN SELECT l.*,t.tenant_treatment,t.tenant_amount,t.tenant_distribution_key,t.owner_distribution_key FROM public.allocation_proposal_line l
    JOIN public.allocation_treatment_line t ON t.organization_id=l.organization_id AND t.allocation_line_id=l.id AND t.treatment_review_id=target
    WHERE l.organization_id=org AND l.allocation_proposal_id=a.id ORDER BY l.position
  LOOP
    cost:=costs[basis.position];
    INSERT INTO public.cost_entry(id,organization_id,property_id,cost_category_id,cost_category_plan_version,invoice_date,service_period_from,service_period_to,
      gross_amount,vat_amount,vat_rate,document_number,owner_distribution_key,tenant_distribution_key,tenant_apportionable,apportionable_tenant_amount,status,source,confirmed_at,confirmed_by)
      VALUES(cost,org,a.property_id,basis.cost_category_id,basis.cost_category_plan_version,r.invoice_date,r.service_period_from,r.service_period_to,
        basis.gross_amount,basis.vat_amount,r.vat_rate,r.document_number,NULLIF(basis.owner_distribution_key,'not_applicable'),basis.tenant_distribution_key,
        CASE basis.tenant_treatment WHEN 'full' THEN 'UMLAGEFAEHIG' WHEN 'partial' THEN 'TEILWEISE_PRUEFEN' WHEN 'none' THEN 'NICHT_UMLAGEFAEHIG' END,
        basis.tenant_amount,'confirmed',CASE WHEN original.mime_type='application/xml' THEN 'E_INVOICE' ELSE 'PDF_IMPORT' END,moment,actor);
    INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
      VALUES(org,actor,'cost_entry',cost,'cost_entry.allocation_confirmed',jsonb_build_object('allocation_set_id',created,'source_document_id',document,'gross_amount',round(basis.gross_amount,2)::text));
  END LOOP;
  INSERT INTO public.invoice_document(id,organization_id,cost_entry_id,uploaded_by,storage_uri,storage_backend,storage_version,original_filename,mime_type,content_byte_size,content_sha256,
    version,chain_root_id,status,released_at,released_by,retention_until,extraction_payload,processing_status)
    VALUES(document,org,costs[1],original.uploaded_by,original.storage_uri,original.storage_backend,original.storage_version,
      (SELECT filename FROM public.intake_item WHERE organization_id=org AND id=original.item_id),original.mime_type,original.content_byte_size,original.content_sha256,
      1,document,'active',moment,actor,make_date(extract(year FROM r.invoice_date)::integer+8,12,31),NULL,'legacy');
  INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
    VALUES(org,actor,'invoice_document',document,'document.allocation_released',jsonb_build_object('allocation_set_id',created,'cost_count',a.line_count,'content_sha256',original.content_sha256));
  PERFORM set_config('app.current_scope_id',COALESCE(old_scope,''),true);
  RETURN created;
END $$;
REVOKE ALL ON FUNCTION adopt_allocation(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION adopt_allocation(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,uuid) TO app_rw;
'''


GUARD_BEFORE="AND a.payload_digest=encode(sha256(convert_to(payload::text,'UTF8')),'hex')) THEN"
GUARD_AFTER=GUARD_BEFORE.replace(' THEN',' AND NOT public.adoption_bridge_admission(NEW) THEN')

PRIVACY_BEFORE="d.cost_entry_id=c.id"
PRIVACY_AFTER="(d.cost_entry_id=c.id OR EXISTS(SELECT 1 FROM public.document_cost_link l WHERE l.organization_id=d.organization_id AND l.source_document_id=d.id AND l.cost_entry_id=c.id))"
