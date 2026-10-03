"""Atomically replace an entire adopted allocation and retain open impacts."""
from alembic import op
from sqlalchemy import text

revision='0114'
down_revision='0113'
branch_labels=None
depends_on=None


def apply_patches(reverse=False):
    grouped={}
    for signature,before,after in (reversed(PATCHES) if reverse else PATCHES):
        grouped.setdefault(signature,[]).append((after,before) if reverse else (before,after))
    for signature,changes in grouped.items():
        definition=op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':signature})
        for before,after in changes:
            assert definition.count(before)==1,(signature,before)
            definition=definition.replace(before,after)
        op.execute(definition)


def upgrade():
    op.execute('''DO $$ DECLARE row record;BEGIN
      FOR row IN SELECT id FROM organization ORDER BY id LOOP PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||row.id::text,0));END LOOP;
      FOR row IN SELECT id FROM organization ORDER BY id LOOP PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||row.id::text,0));END LOOP;
      END $$;
      LOCK TABLE allocation_set,document_cost_link,cost_entry,invoice_document IN SHARE ROW EXCLUSIVE MODE NOWAIT;
      ALTER TABLE allocation_set ADD COLUMN predecessor_id uuid,
        ADD CONSTRAINT allocation_predecessor_fk FOREIGN KEY(organization_id,predecessor_id) REFERENCES allocation_set(organization_id,id),
        ADD CONSTRAINT allocation_one_successor UNIQUE(organization_id,predecessor_id),
        ADD CONSTRAINT allocation_reviewed_correction CHECK(predecessor_id IS NULL OR kind='intake_reviewed');
      ALTER TABLE allocation_set DROP CONSTRAINT allocation_set_organization_id_intake_original_id_key,
        DROP CONSTRAINT allocation_set_organization_id_logical_document_id_key;
      DROP INDEX allocation_once_per_original_hash;
      CREATE UNIQUE INDEX allocation_once_per_original_hash ON allocation_set(organization_id,content_sha256) WHERE kind='intake_reviewed' AND predecessor_id IS NULL;
      CREATE UNIQUE INDEX allocation_initial_original ON allocation_set(organization_id,intake_original_id) WHERE predecessor_id IS NULL;
      CREATE UNIQUE INDEX allocation_initial_logical ON allocation_set(organization_id,logical_document_id) WHERE predecessor_id IS NULL;
      CREATE TABLE allocation_correction(
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,
        predecessor_id uuid NOT NULL,successor_id uuid NOT NULL,actor_id uuid NOT NULL,request_key uuid NOT NULL,request_digest text NOT NULL,
        preview_digest text NOT NULL,impact_snapshot jsonb NOT NULL,reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 2000),
        costs_confirmed boolean NOT NULL CHECK(costs_confirmed),whole_original_disclosure_reviewed boolean NOT NULL CHECK(whole_original_disclosure_reviewed),
        downstream_followups_reviewed boolean NOT NULL CHECK(downstream_followups_reviewed),confirmed_at timestamptz NOT NULL,
        UNIQUE(organization_id,id),UNIQUE(organization_id,predecessor_id),UNIQUE(organization_id,successor_id),UNIQUE(organization_id,actor_id,request_key),
        FOREIGN KEY(organization_id,predecessor_id) REFERENCES allocation_set(organization_id,id),
        FOREIGN KEY(organization_id,successor_id) REFERENCES allocation_set(organization_id,id),
        FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
      CREATE TABLE allocation_correction_impact(
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,correction_id uuid NOT NULL,
        settlement_run_id uuid,economic_plan_id uuid,snapshot jsonb NOT NULL,
        CHECK(num_nonnulls(settlement_run_id,economic_plan_id)=1),
        UNIQUE(organization_id,id),UNIQUE(organization_id,correction_id,settlement_run_id),UNIQUE(organization_id,correction_id,economic_plan_id),
        FOREIGN KEY(organization_id,correction_id) REFERENCES allocation_correction(organization_id,id),
        FOREIGN KEY(organization_id,settlement_run_id) REFERENCES settlement_run(organization_id,id),
        FOREIGN KEY(organization_id,economic_plan_id) REFERENCES economic_plan(organization_id,id));
      ''')
    for table in ('allocation_correction','allocation_correction_impact'):
        op.execute(f'''ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review;
          CREATE POLICY correction_private ON {table} USING(false) WITH CHECK(false);
          CREATE TRIGGER correction_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();''')
    op.execute(AUTHORITY_SQL)
    op.execute(PREPARE_SQL)
    op.execute(SEAL_SQL)
    apply_patches()
    op.execute(COMMAND_SQL)


def downgrade():
    op.execute('LOCK TABLE allocation_correction,allocation_correction_impact,allocation_set IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM allocation_correction) OR EXISTS(SELECT 1 FROM allocation_set WHERE predecessor_id IS NOT NULL) THEN RAISE EXCEPTION 'Retain allocation correction evidence; repair forward';END IF;END $$")
    apply_patches(reverse=True)
    op.execute('''DROP TRIGGER seal_allocation_correction ON allocation_set;
      DROP TABLE allocation_correction_impact,allocation_correction;
      DROP FUNCTION correct_allocation(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,boolean,uuid),
        prepare_allocation_correction(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,boolean),
        allocation_correction_detail(uuid,uuid),allocation_correction_for_set(uuid,uuid),require_correction_authority(uuid,uuid,uuid),correction_impact_access(uuid,jsonb),
        correction_transaction_admission(uuid,uuid),seal_allocation_correction();
      DROP INDEX allocation_once_per_original_hash,allocation_initial_original,allocation_initial_logical;
      ALTER TABLE allocation_set DROP COLUMN predecessor_id;
      ALTER TABLE allocation_set ADD CONSTRAINT allocation_set_organization_id_intake_original_id_key UNIQUE(organization_id,intake_original_id),
        ADD CONSTRAINT allocation_set_organization_id_logical_document_id_key UNIQUE(organization_id,logical_document_id);
      CREATE UNIQUE INDEX allocation_once_per_original_hash ON allocation_set(organization_id,content_sha256) WHERE kind='intake_reviewed';''')


AUTHORITY_SQL='''
CREATE FUNCTION require_correction_authority(org uuid,predecessor uuid,target uuid) RETURNS public.allocation_set
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE old public.allocation_set;a public.allocation_proposal;
BEGIN
  a:=public.require_adoption_authority(org,target);
  SELECT * INTO old FROM public.allocation_set WHERE organization_id=org AND id=predecessor;
  IF old.id IS NULL OR old.kind<>'intake_reviewed' OR NOT public.allocation_set_access(org,old.id)
    OR NOT public.invoice_source_access(org,old.source_document_id,'prepare') OR NOT public.invoice_source_access(org,old.source_document_id,'export')
    OR NOT public.source_partition_access(org,old.document_ref_id,true) OR NOT public.intake_original_export_access(org,old.intake_original_id)
    OR (a.document_ref_id,a.logical_document_id,a.property_id,a.scope_id) IS DISTINCT FROM (old.document_ref_id,old.logical_document_id,old.property_id,old.scope_id) THEN
    RAISE EXCEPTION 'Correction source inaccessible' USING ERRCODE='42501';END IF;
  RETURN old;
END $$;
CREATE FUNCTION correction_impact_access(org uuid,impact jsonb) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE item jsonb;property uuid;kind text;target uuid;needs_graph boolean;
BEGIN
  FOR item IN SELECT value FROM jsonb_array_elements(impact->'items') LOOP
    kind:=item->>'kind';target:=(item->>'id')::uuid;property:=NULL;needs_graph:=true;
    IF kind='settlement_run' THEN SELECT property_id,status<>'draft' OR snapshot_payload IS NOT NULL INTO property,needs_graph FROM public.settlement_run WHERE organization_id=org AND id=target;
    ELSIF kind='economic_plan' THEN SELECT property_id,status<>'draft' OR snapshot_payload IS NOT NULL INTO property,needs_graph FROM public.economic_plan WHERE organization_id=org AND id=target;
    ELSE RETURN false;END IF;
    IF property IS NULL OR NOT public.financial_scope_access(org,kind,target,'read')
      OR ((needs_graph OR EXISTS(SELECT 1 FROM public.coverage_declaration WHERE organization_id=org AND COALESCE(settlement_run_id,economic_plan_id)=target))
        AND NOT public.property_financial_disclosure_access(org,property,'read')) THEN RETURN false;END IF;
  END LOOP;
  RETURN true;
END $$;
CREATE FUNCTION allocation_correction_detail(org uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE receipt public.allocation_correction;successor public.allocation_set;old public.allocation_set;
BEGIN
  PERFORM public.lock_intake(org);
  SELECT * INTO receipt FROM public.allocation_correction WHERE organization_id=org AND id=target;
  SELECT * INTO successor FROM public.allocation_set WHERE organization_id=org AND id=receipt.successor_id;
  IF receipt.id IS NULL THEN RAISE EXCEPTION 'Correction inaccessible' USING ERRCODE='42501';END IF;
  old:=public.require_correction_authority(org,receipt.predecessor_id,successor.treatment_review_id);
  IF NOT public.allocation_set_access(org,successor.id) OR NOT public.correction_impact_access(org,receipt.impact_snapshot) THEN
    RAISE EXCEPTION 'Correction evidence inaccessible' USING ERRCODE='42501';END IF;
  RETURN jsonb_build_object('id',receipt.id,'predecessor_id',receipt.predecessor_id,'successor_id',receipt.successor_id,
    'reason',receipt.reason,'actor_id',receipt.actor_id,'confirmed_at',receipt.confirmed_at,'preview_digest',receipt.preview_digest,
    'impacts',(SELECT COALESCE(jsonb_agg(jsonb_build_object('id',i.id,'snapshot',i.snapshot) ORDER BY i.snapshot->>'kind',i.snapshot->>'id'),'[]')
      FROM public.allocation_correction_impact i WHERE i.organization_id=org AND i.correction_id=receipt.id));
END $$;
CREATE FUNCTION correction_transaction_admission(org uuid,successor uuid) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
  SELECT EXISTS(SELECT 1 FROM public.allocation_correction c JOIN public.allocation_set s ON s.organization_id=c.organization_id AND s.id=c.successor_id
    JOIN public.allocation_integrity_attestation p ON p.organization_id=s.organization_id AND p.id=s.integrity_attestation_id
    WHERE c.organization_id=org AND c.successor_id=successor AND s.predecessor_id=c.predecessor_id
      AND c.actor_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid AND c.actor_id=s.actor_id AND p.actor_id=c.actor_id
      AND c.request_key=s.request_key AND p.request_key=c.request_key AND c.request_digest=s.request_digest AND p.request_digest=c.request_digest
      AND p.backend_pid=pg_backend_pid() AND p.transaction_id=pg_current_xact_id() AND p.expires_at>clock_timestamp());
$$;
CREATE FUNCTION allocation_correction_for_set(org uuid,target uuid) RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE receipt uuid;
BEGIN
  PERFORM public.lock_intake(org);
  IF NOT public.allocation_set_access(org,target) THEN RAISE EXCEPTION 'Allocation inaccessible' USING ERRCODE='42501';END IF;
  SELECT id INTO receipt FROM public.allocation_correction WHERE organization_id=org AND successor_id=target;
  IF receipt IS NOT NULL THEN
    BEGIN PERFORM public.allocation_correction_detail(org,receipt);
    EXCEPTION WHEN insufficient_privilege THEN RETURN NULL;END;
  END IF;
  RETURN receipt;
END $$;
REVOKE ALL ON FUNCTION allocation_correction_for_set(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION allocation_correction_for_set(uuid,uuid) TO app_rw;
REVOKE ALL ON FUNCTION require_correction_authority(uuid,uuid,uuid),correction_impact_access(uuid,jsonb),allocation_correction_detail(uuid,uuid),correction_transaction_admission(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION allocation_correction_detail(uuid,uuid) TO app_rw;
'''


PREPARE_SQL='''
CREATE FUNCTION prepare_allocation_correction(org uuid,predecessor uuid,target uuid,expected uuid,request uuid,preview_digest text,explanation text,confirmed boolean,disclosed boolean,followups boolean) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;old public.allocation_set;prior public.allocation_correction;
  original public.intake_original;preview jsonb;digest text;
BEGIN
  IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501';END IF;
  old:=public.require_correction_authority(org,predecessor,target);
  IF request IS NULL OR expected IS DISTINCT FROM predecessor OR preview_digest IS NULL OR preview_digest !~ '^[0-9a-f]{64}$'
    OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 2000 OR confirmed IS DISTINCT FROM true
    OR disclosed IS DISTINCT FROM true OR followups IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Exact replacement, source disclosure and downstream review required' USING ERRCODE='22023';END IF;
  digest:=encode(sha256(convert_to(jsonb_build_array(predecessor,target,expected,preview_digest,explanation,confirmed,disclosed,followups)::text,'UTF8')),'hex');
  SELECT * INTO prior FROM public.allocation_correction WHERE organization_id=org AND actor_id=actor AND request_key=request;
  IF FOUND THEN
    IF prior.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Correction request changed' USING ERRCODE='23505';END IF;
    PERFORM public.allocation_correction_detail(org,prior.id);
    RETURN jsonb_build_object('replay_id',prior.id);
  END IF;
  IF EXISTS(SELECT 1 FROM public.allocation_set WHERE organization_id=org AND actor_id=actor AND request_key=request) THEN
    RAISE EXCEPTION 'Financial request key already used' USING ERRCODE='23505';END IF;
  preview:=public.allocation_correction_preview(org,predecessor,target);
  IF preview->>'can_review' IS DISTINCT FROM 'true' OR preview->>'preview_digest' IS DISTINCT FROM preview_digest
    OR EXISTS(SELECT 1 FROM public.allocation_set WHERE organization_id=org AND predecessor_id=predecessor) THEN
    RAISE EXCEPTION 'Correction evidence or predecessor changed' USING ERRCODE='23505';END IF;
  SELECT * INTO STRICT original FROM public.intake_original WHERE organization_id=org AND id=old.intake_original_id;
  RETURN jsonb_build_object('replay_id',NULL,'original_id',original.id,'storage_uri',original.storage_uri,'storage_backend',original.storage_backend,
    'storage_version',original.storage_version,'content_sha256',original.content_sha256,'content_byte_size',original.content_byte_size,
    'backend_pid',pg_backend_pid(),'transaction_id',pg_current_xact_id()::text,'request_digest',digest);
END $$;
REVOKE ALL ON FUNCTION prepare_allocation_correction(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,boolean) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION prepare_allocation_correction(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,boolean) TO app_rw;
'''


SEAL_SQL='''
CREATE FUNCTION seal_allocation_correction() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE receipt public.allocation_correction;old public.allocation_set;successor public.allocation_set;
  source public.invoice_document;prior_source public.invoice_document;items jsonb;target uuid;
BEGIN
  IF TG_TABLE_NAME='allocation_set' THEN
    IF NEW.predecessor_id IS NULL THEN RETURN NULL;END IF;
    SELECT * INTO receipt FROM public.allocation_correction WHERE organization_id=NEW.organization_id AND successor_id=NEW.id;
  ELSE
    IF TG_TABLE_NAME='allocation_correction' THEN target:=NEW.id;ELSE target:=NEW.correction_id;END IF;
    SELECT * INTO receipt FROM public.allocation_correction WHERE organization_id=NEW.organization_id AND id=target;
  END IF;
  IF receipt.id IS NULL THEN RAISE EXCEPTION 'Complete correction receipt required' USING ERRCODE='23514';END IF;
  SELECT * INTO STRICT old FROM public.allocation_set WHERE organization_id=receipt.organization_id AND id=receipt.predecessor_id;
  SELECT * INTO STRICT successor FROM public.allocation_set WHERE organization_id=receipt.organization_id AND id=receipt.successor_id;
  SELECT * INTO STRICT source FROM public.invoice_document WHERE organization_id=receipt.organization_id AND id=successor.source_document_id;
  SELECT * INTO STRICT prior_source FROM public.invoice_document WHERE organization_id=receipt.organization_id AND id=old.source_document_id;
  SELECT COALESCE(jsonb_agg(i.snapshot ORDER BY i.snapshot->>'kind',i.snapshot->>'id'),'[]') INTO items
    FROM public.allocation_correction_impact i WHERE i.organization_id=receipt.organization_id AND i.correction_id=receipt.id;
  IF old.kind<>'intake_reviewed' OR successor.kind<>'intake_reviewed' OR successor.predecessor_id IS DISTINCT FROM old.id
    OR (old.property_id,old.scope_id,old.document_ref_id,old.logical_document_id,old.intake_original_id,old.content_sha256)
      IS DISTINCT FROM (successor.property_id,successor.scope_id,successor.document_ref_id,successor.logical_document_id,successor.intake_original_id,successor.content_sha256)
    OR old.allocation_proposal_id=successor.allocation_proposal_id OR old.treatment_review_id=successor.treatment_review_id
    OR (receipt.actor_id,receipt.request_key,receipt.request_digest,receipt.reason,receipt.confirmed_at)
      IS DISTINCT FROM (successor.actor_id,successor.request_key,successor.request_digest,successor.reason,successor.confirmed_at)
    OR NOT public.correction_transaction_admission(receipt.organization_id,successor.id)
    OR EXISTS(SELECT 1 FROM public.document_cost_link l JOIN public.cost_entry c ON c.organization_id=l.organization_id AND c.id=l.cost_entry_id
      WHERE l.organization_id=old.organization_id AND l.allocation_set_id=old.id AND c.superseded_by_id IS DISTINCT FROM successor.primary_cost_id)
    OR source.supersedes_id IS DISTINCT FROM prior_source.id OR source.chain_root_id IS DISTINCT FROM prior_source.chain_root_id
    OR source.version<>prior_source.version+1 OR source.status<>'active' OR prior_source.status<>'superseded'
    OR prior_source.superseded_at IS DISTINCT FROM receipt.confirmed_at OR source.retention_until<prior_source.retention_until
    OR items IS DISTINCT FROM receipt.impact_snapshot->'items'
    OR EXISTS(SELECT 1 FROM public.allocation_correction_impact i WHERE i.organization_id=receipt.organization_id AND i.correction_id=receipt.id
      AND (i.snapshot->>'id' IS DISTINCT FROM COALESCE(i.settlement_run_id,i.economic_plan_id)::text
        OR i.snapshot->>'kind' IS DISTINCT FROM CASE WHEN i.settlement_run_id IS NOT NULL THEN 'settlement_run' ELSE 'economic_plan' END)) THEN
    RAISE EXCEPTION 'Complete atomic allocation replacement required' USING ERRCODE='23514';END IF;
  RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION seal_allocation_correction() FROM PUBLIC,app_rw,app_worker,app_review;
CREATE CONSTRAINT TRIGGER seal_allocation_correction AFTER INSERT ON allocation_set DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION seal_allocation_correction();
CREATE CONSTRAINT TRIGGER seal_allocation_correction AFTER INSERT ON allocation_correction DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION seal_allocation_correction();
CREATE CONSTRAINT TRIGGER seal_allocation_correction AFTER INSERT ON allocation_correction_impact DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION seal_allocation_correction();
'''


COST_BEFORE="""    RAISE EXCEPTION 'Reviewed allocation costs require an atomic set correction' USING ERRCODE='23514';END IF;"""
COST_AFTER="""    IF TG_OP='UPDATE' AND OLD.superseded_by_id IS NULL AND NEW.superseded_by_id IS NOT NULL
      AND (to_jsonb(NEW)-ARRAY['superseded_by_id','updated_at'])=(to_jsonb(OLD)-ARRAY['superseded_by_id','updated_at'])
      AND EXISTS(SELECT 1 FROM public.document_cost_link l JOIN public.allocation_set s ON s.organization_id=l.organization_id AND s.predecessor_id=l.allocation_set_id
        WHERE l.organization_id=org AND l.cost_entry_id=target AND s.primary_cost_id=NEW.superseded_by_id AND public.correction_transaction_admission(org,s.id)) THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Reviewed allocation costs require an atomic set correction' USING ERRCODE='23514';END IF;"""
INVOICE_BEFORE="""    RAISE EXCEPTION 'Released allocation source requires an atomic set correction' USING ERRCODE='23514';END IF;"""
INVOICE_AFTER="""    IF TG_OP='UPDATE' AND OLD.status='active' AND NEW.status='superseded' AND OLD.superseded_at IS NULL AND NEW.superseded_at IS NOT NULL
      AND (to_jsonb(NEW)-ARRAY['status','superseded_at','updated_at'])=(to_jsonb(OLD)-ARRAY['status','superseded_at','updated_at'])
      AND EXISTS(SELECT 1 FROM public.allocation_set prior JOIN public.allocation_set s ON s.organization_id=prior.organization_id AND s.predecessor_id=prior.id
        JOIN public.allocation_correction c ON c.organization_id=s.organization_id AND c.successor_id=s.id
        WHERE prior.organization_id=org AND prior.source_document_id=target AND c.confirmed_at=NEW.superseded_at AND public.correction_transaction_admission(org,s.id)) THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Released allocation source requires an atomic set correction' USING ERRCODE='23514';END IF;"""
BRIDGE_BEFORE="source.version=1 AND source.supersedes_id IS NULL AND source.chain_root_id=source.id AND source.status='active'"
BRIDGE_AFTER="""((s.predecessor_id IS NULL AND source.version=1 AND source.supersedes_id IS NULL AND source.chain_root_id=source.id)
        OR EXISTS(SELECT 1 FROM public.allocation_set prior JOIN public.invoice_document d ON d.organization_id=prior.organization_id AND d.id=prior.source_document_id
          WHERE prior.organization_id=s.organization_id AND prior.id=s.predecessor_id AND source.supersedes_id=d.id AND source.chain_root_id=d.chain_root_id
            AND source.version=d.version+1 AND public.correction_transaction_admission(s.organization_id,s.id))) AND source.status='active'"""
RETENTION_BEFORE="source.retention_until=make_date(extract(year FROM r.invoice_date)::integer+8,12,31)"
RETENTION_AFTER="""source.retention_until=greatest(make_date(extract(year FROM r.invoice_date)::integer+8,12,31),
        (SELECT d.retention_until FROM public.allocation_set prior JOIN public.invoice_document d ON d.organization_id=prior.organization_id AND d.id=prior.source_document_id
          WHERE prior.organization_id=s.organization_id AND prior.id=s.predecessor_id))"""
COVERAGE_BEFORE="UNION ALL SELECT 'mapping_version',id,to_jsonb(r) FROM public.mapping_version r WHERE organization_id=org) evidence"
COVERAGE_AFTER=COVERAGE_BEFORE.replace(') evidence'," UNION ALL SELECT 'allocation_correction',id,to_jsonb(r) FROM public.allocation_correction r WHERE organization_id=org UNION ALL SELECT 'allocation_correction_impact',id,to_jsonb(r) FROM public.allocation_correction_impact r WHERE organization_id=org) evidence")
PATCHES=[
    ('protect_adopted_cost()',COST_BEFORE,COST_AFTER),
    ('protect_adopted_invoice()',INVOICE_BEFORE,INVOICE_AFTER),
    ('protect_adopted_invoice()',"AND content_sha256=NEW.content_sha256 AND source_document_id<>NEW.id) THEN",
      "AND content_sha256=NEW.content_sha256 AND source_document_id<>NEW.id) AND NOT public.adoption_bridge_admission(NEW) THEN"),
    ('adoption_bridge_admission(public.invoice_document)',BRIDGE_BEFORE,BRIDGE_AFTER),
    ('adoption_bridge_admission(public.invoice_document)',RETENTION_BEFORE,RETENTION_AFTER),
    ('allocation_adoption_result(uuid,uuid)',
      'SELECT id INTO result FROM public.allocation_set WHERE organization_id=org AND logical_document_id=logical_id;',
      'SELECT s.id INTO result FROM public.allocation_set s WHERE s.organization_id=org AND s.logical_document_id=logical_id AND NOT EXISTS(SELECT 1 FROM public.allocation_set successor WHERE successor.organization_id=org AND successor.predecessor_id=s.id);'),
    ('evaluate_exception_completion(uuid,uuid)',
      "SELECT * INTO s FROM public.allocation_set WHERE organization_id=org AND kind='intake_reviewed' AND content_sha256=r.content_sha256;",
      "SELECT head_set.* INTO s FROM public.allocation_set head_set WHERE head_set.organization_id=org AND head_set.kind='intake_reviewed' AND head_set.content_sha256=r.content_sha256 AND NOT EXISTS(SELECT 1 FROM public.allocation_set successor WHERE successor.organization_id=org AND successor.predecessor_id=head_set.id);"),
    ('mapping_source_basis(uuid,uuid)',
      'IF a.allocation_proposal_id IS DISTINCT FROM',
      'IF EXISTS(SELECT 1 FROM public.allocation_set WHERE organization_id=org AND predecessor_id=a.id) OR a.allocation_proposal_id IS DISTINCT FROM'),
    ('allocation_correction_impacts(uuid,uuid,uuid)','source_id text;','source_ids text[];ancestry uuid[];'),
    ('allocation_correction_impacts(uuid,uuid,uuid)',
      "SELECT array_agg(cost_entry_id::text ORDER BY position) INTO cost_ids FROM public.document_cost_link WHERE organization_id=org AND allocation_set_id=old.id;\n  source_id:=old.source_document_id::text;",
      """WITH RECURSIVE chain AS (
        SELECT id,predecessor_id,1 AS depth FROM public.allocation_set WHERE organization_id=org AND id=old.id
        UNION ALL SELECT s.id,s.predecessor_id,c.depth+1 FROM public.allocation_set s JOIN chain c ON c.predecessor_id=s.id
          WHERE s.organization_id=org AND c.depth<=100
      ) SELECT array_agg(id ORDER BY depth) INTO ancestry FROM chain;
      IF cardinality(ancestry)>100 THEN RAISE EXCEPTION 'Complete correction ancestry requires further review' USING ERRCODE='23514';END IF;
      SELECT array_agg(cost_entry_id::text ORDER BY allocation_set_id,position) INTO cost_ids FROM public.document_cost_link WHERE organization_id=org AND allocation_set_id=ANY(ancestry);
      SELECT array_agg(source_document_id::text ORDER BY id) INTO source_ids FROM public.allocation_set WHERE organization_id=org AND id=ANY(ancestry);"""),
    ('allocation_correction_impacts(uuid,uuid,uuid)',
      'AND COALESCE(d.settlement_run_id,d.economic_plan_id)=p.id AND d.source_document_ids ? source_id)',
      'AND COALESCE(d.settlement_run_id,d.economic_plan_id)=p.id AND d.source_document_ids ?| source_ids)'),
    ('allocation_correction_impacts(uuid,uuid,uuid)',
      'AND d.source_document_ids ? source_id);','AND d.source_document_ids ?| source_ids);'),
    ('allocation_correction_impacts(uuid,uuid,uuid)',
      "((row.period_start<=old_review.service_period_to AND row.period_end>=old_review.service_period_from)\n          OR (row.period_start<=new_review.service_period_to AND row.period_end>=new_review.service_period_from))",
      """(EXISTS(SELECT 1 FROM public.allocation_set retained JOIN public.invoice_review_revision r ON r.organization_id=retained.organization_id AND r.id=retained.invoice_review_id
          WHERE retained.organization_id=org AND retained.id=ANY(ancestry) AND row.period_start<=r.service_period_to AND row.period_end>=r.service_period_from)
          OR (row.period_start<=new_review.service_period_to AND row.period_end>=new_review.service_period_from))"""),
    ('access_coverage_manifest(uuid,jsonb)',COVERAGE_BEFORE,COVERAGE_AFTER),
]


COMMAND_SQL='''
CREATE FUNCTION correct_allocation(org uuid,predecessor uuid,target uuid,expected uuid,request uuid,preview_digest text,explanation text,confirmed boolean,disclosed boolean,followups boolean,attestation uuid) RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;prepared jsonb;proof public.allocation_integrity_attestation;
  a public.allocation_proposal;r public.invoice_review_revision;original public.intake_original;basis record;
  old public.allocation_set;prior_source public.invoice_document;impacts jsonb;receipt uuid:=gen_random_uuid();
  duplicates jsonb;created uuid:=gen_random_uuid();document uuid:=gen_random_uuid();costs uuid[];cost uuid;moment timestamptz:=clock_timestamp();old_scope text;
BEGIN
  prepared:=public.prepare_allocation_correction(org,predecessor,target,expected,request,preview_digest,explanation,confirmed,disclosed,followups);
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
  SELECT * INTO STRICT old FROM public.allocation_set WHERE organization_id=org AND id=predecessor;
  SELECT * INTO STRICT prior_source FROM public.invoice_document WHERE organization_id=org AND id=old.source_document_id;
  impacts:=public.allocation_correction_impacts(org,predecessor,target);
  SELECT array_agg(gen_random_uuid() ORDER BY position) INTO costs FROM public.allocation_proposal_line WHERE organization_id=org AND allocation_proposal_id=a.id;
  duplicates:=public.correction_duplicate_snapshot(org,r.id);
  INSERT INTO public.allocation_set(id,organization_id,kind,predecessor_id,source_document_id,primary_cost_id,property_id,scope_id,cost_count,treatment_review_id,allocation_proposal_id,
    invoice_review_id,document_ref_id,logical_document_id,intake_original_id,integrity_attestation_id,content_sha256,duplicate_snapshot,actor_id,request_key,request_digest,reason,whole_original_disclosure_reviewed,confirmed_at)
    VALUES(created,org,'intake_reviewed',predecessor,document,costs[1],a.property_id,a.scope_id,a.line_count,target,a.id,r.id,a.document_ref_id,a.logical_document_id,original.id,proof.id,
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
  INSERT INTO public.allocation_correction(id,organization_id,predecessor_id,successor_id,actor_id,request_key,request_digest,preview_digest,
    impact_snapshot,reason,costs_confirmed,whole_original_disclosure_reviewed,downstream_followups_reviewed,confirmed_at)
    VALUES(receipt,org,predecessor,created,actor,request,prepared->>'request_digest',preview_digest,impacts,explanation,true,true,true,moment);
  INSERT INTO public.allocation_correction_impact(organization_id,correction_id,settlement_run_id,economic_plan_id,snapshot)
    SELECT org,receipt,CASE WHEN i->>'kind'='settlement_run' THEN (i->>'id')::uuid END,
      CASE WHEN i->>'kind'='economic_plan' THEN (i->>'id')::uuid END,i FROM jsonb_array_elements(impacts->'items') i;
  UPDATE public.cost_entry c SET superseded_by_id=costs[1] FROM public.document_cost_link l
    WHERE l.organization_id=org AND l.allocation_set_id=predecessor AND c.organization_id=org AND c.id=l.cost_entry_id;
  INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
    SELECT org,actor,'cost_entry',l.cost_entry_id,'cost_entry.allocation_superseded',
      jsonb_build_object('correction_id',receipt,'predecessor_allocation_set_id',predecessor,'successor_allocation_set_id',created,'successor_primary_cost_id',costs[1])
      FROM public.document_cost_link l WHERE l.organization_id=org AND l.allocation_set_id=predecessor;
  UPDATE public.invoice_document SET status='superseded',superseded_at=moment WHERE organization_id=org AND id=old.source_document_id;
  INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
    VALUES(org,actor,'invoice_document',old.source_document_id,'document.allocation_superseded',jsonb_build_object('correction_id',receipt,'successor_document_id',document));
  INSERT INTO public.invoice_document(id,organization_id,cost_entry_id,uploaded_by,storage_uri,storage_backend,storage_version,original_filename,mime_type,content_byte_size,content_sha256,
    version,chain_root_id,supersedes_id,status,released_at,released_by,retention_until,extraction_payload,processing_status)
    VALUES(document,org,costs[1],original.uploaded_by,original.storage_uri,original.storage_backend,original.storage_version,
      (SELECT filename FROM public.intake_item WHERE organization_id=org AND id=original.item_id),original.mime_type,original.content_byte_size,original.content_sha256,
      prior_source.version+1,prior_source.chain_root_id,prior_source.id,'active',moment,actor,greatest(prior_source.retention_until,make_date(extract(year FROM r.invoice_date)::integer+8,12,31)),NULL,'legacy');
  INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
    VALUES(org,actor,'invoice_document',document,'document.allocation_corrected',jsonb_build_object('allocation_set_id',created,'correction_id',receipt,'cost_count',a.line_count,'content_sha256',original.content_sha256,'open_impact_count',jsonb_array_length(impacts->'items')));
  PERFORM set_config('app.current_scope_id',COALESCE(old_scope,''),true);
  RETURN receipt;
END $$;
REVOKE ALL ON FUNCTION correct_allocation(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,boolean,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION correct_allocation(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,boolean,uuid) TO app_rw;
'''
