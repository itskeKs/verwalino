"""Retain transaction-bound evidence of actual financial engine computations."""
from alembic import op
from sqlalchemy import text

revision='0115'
down_revision='0114'
branch_labels=None
depends_on=None

SOURCE_TABLES=('property','unit','cost_entry','payment','bank_account','lease_agreement','ownership_history',
    'accounting_scope','accounting_scope_unit','accounting_scope_binding','allocation_set','person','cost_category','organization',
    'economic_plan','economic_plan_line','settlement_run','settlement_line','coverage_declaration','management_mandate')


COVERAGE_BEFORE="UNION ALL SELECT 'allocation_correction_impact',id,to_jsonb(r) FROM public.allocation_correction_impact r WHERE organization_id=org) evidence"
COVERAGE_AFTER=COVERAGE_BEFORE.replace(') evidence'," UNION ALL SELECT 'financial_computation_attestation',id,to_jsonb(r) FROM public.financial_computation_attestation r WHERE organization_id=org UNION ALL SELECT 'financial_computation_receipt',id,to_jsonb(r) FROM public.financial_computation_receipt r WHERE organization_id=org) evidence")


def patch_coverage(reverse=False):
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    before,after=(COVERAGE_AFTER,COVERAGE_BEFORE) if reverse else (COVERAGE_BEFORE,COVERAGE_AFTER)
    assert definition.count(before)==1
    op.execute(definition.replace(before,after))


def upgrade():
    op.execute('LOCK TABLE '+','.join(SOURCE_TABLES)+' IN SHARE ROW EXCLUSIVE MODE NOWAIT')
    op.execute('''
    ALTER TABLE coverage_declaration ADD CONSTRAINT coverage_computation_identity UNIQUE(organization_id,id);
    CREATE TABLE financial_computation_attestation(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),
      actor_id uuid NOT NULL,backend_pid integer NOT NULL,transaction_id xid8 NOT NULL,
      subject_kind text NOT NULL CHECK(subject_kind IN ('settlement_run','economic_plan')),subject_id uuid NOT NULL,
      operation text NOT NULL CHECK(operation IN ('compute','finalize')),state jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '2 minutes',
      UNIQUE(organization_id,id),FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id));
    CREATE TABLE financial_computation_receipt(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,
      receipt_number bigserial NOT NULL UNIQUE,attestation_id uuid NOT NULL UNIQUE,actor_id uuid NOT NULL,
      settlement_run_id uuid,economic_plan_id uuid,coverage_id uuid NOT NULL,
      operation text NOT NULL CHECK(operation IN ('compute','finalize')),state jsonb NOT NULL,
      recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CHECK(num_nonnulls(settlement_run_id,economic_plan_id)=1),UNIQUE(organization_id,id),
      FOREIGN KEY(organization_id,attestation_id) REFERENCES financial_computation_attestation(organization_id,id),
      FOREIGN KEY(organization_id,actor_id) REFERENCES "user"(organization_id,id),
      FOREIGN KEY(organization_id,settlement_run_id) REFERENCES settlement_run(organization_id,id),
      FOREIGN KEY(organization_id,economic_plan_id) REFERENCES economic_plan(organization_id,id),
      FOREIGN KEY(organization_id,coverage_id) REFERENCES coverage_declaration(organization_id,id));
    CREATE INDEX computation_current_transaction ON financial_computation_attestation(backend_pid,transaction_id);
    CREATE INDEX computation_settlement_history ON financial_computation_receipt(organization_id,settlement_run_id,receipt_number DESC);
    CREATE INDEX computation_plan_history ON financial_computation_receipt(organization_id,economic_plan_id,receipt_number DESC);
    ''')
    for table in ('financial_computation_attestation','financial_computation_receipt'):
        op.execute(f'''ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review;
          CREATE POLICY computation_private ON {table} USING(false) WITH CHECK(false);
          CREATE TRIGGER computation_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();''')
    patch_coverage()
    op.execute(STATE_SQL)
    op.execute(COMMAND_SQL)
    op.execute(MUTATION_SQL)
    for table in SOURCE_TABLES:
        op.execute(f'CREATE CONSTRAINT TRIGGER recheck_computation AFTER INSERT OR UPDATE OR DELETE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION recheck_transaction_computations()')


def downgrade():
    op.execute('LOCK TABLE '+','.join(SOURCE_TABLES)+',financial_computation_receipt,financial_computation_attestation IN ACCESS EXCLUSIVE MODE NOWAIT')
    patch_coverage(reverse=True)
    for table in SOURCE_TABLES:
        op.execute(f'DROP TRIGGER recheck_computation ON {table}')
    op.execute('''LOCK TABLE financial_computation_receipt,financial_computation_attestation IN ACCESS EXCLUSIVE MODE NOWAIT;
      DO $$ BEGIN IF EXISTS(SELECT 1 FROM financial_computation_receipt) OR EXISTS(SELECT 1 FROM financial_computation_attestation) THEN
        RAISE EXCEPTION 'Retain trusted financial computation evidence; repair forward';END IF;END $$;
      DROP TABLE financial_computation_receipt,financial_computation_attestation;
      ALTER TABLE coverage_declaration DROP CONSTRAINT coverage_computation_identity;
      DROP FUNCTION recheck_transaction_computations(),seal_financial_computation(),record_financial_computation(uuid,uuid),
        attest_financial_computation(uuid,text,uuid,text,jsonb,integer,text),prepare_financial_computation(uuid,text,uuid,text),
        require_computation_authority(uuid,text,uuid,text),financial_computation_state(uuid,text,uuid),
        financial_computation_source_digest(uuid,uuid,uuid),financial_computation_source_digest(uuid,uuid,uuid,boolean),
        computation_input_basis(uuid,text,uuid,boolean),begin_financial_computation(uuid,text,uuid,text),
        verify_financial_computation_basis(uuid,text,uuid,text,jsonb),bounded_computation_digest(jsonb);
    ''')


STATE_SQL='''
CREATE FUNCTION bounded_computation_digest(value jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF value IS NULL OR octet_length(value::text)>8388608 THEN
    RAISE EXCEPTION 'Complete computation evidence exceeds supported bounds' USING ERRCODE='23514';END IF;
  RETURN encode(sha256(convert_to(value::text,'UTF8')),'hex');
END $$;
CREATE FUNCTION financial_computation_source_digest(org uuid,property uuid,linked_plan uuid,lock_sources boolean) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE kind text;condition text;rows jsonb;parts jsonb:='[]';
BEGIN
  -- Private complete-property fingerprint: conservative across financial scopes,
  -- never an audience-filtered source list. Only digests leave this helper.
  FOREACH kind IN ARRAY ARRAY['property','unit','cost_entry','payment','bank_account','lease_agreement','ownership_history',
    'accounting_scope','accounting_scope_unit','accounting_scope_binding','allocation_set','person','cost_category','organization','economic_plan','economic_plan_line'] LOOP
    condition:=CASE
      WHEN kind='property' THEN 't.organization_id=$1 AND t.id=$2'
      WHEN kind IN ('lease_agreement','ownership_history') THEN 't.organization_id=$1 AND EXISTS(SELECT 1 FROM public.unit u WHERE u.organization_id=$1 AND u.id=t.unit_id AND u.property_id=$2)'
      WHEN kind='person' THEN 't.organization_id=$1 AND t.id IN (
        SELECT p.person_id FROM public.tenant_profile p JOIN public.lease_agreement h ON h.organization_id=p.organization_id AND h.tenant_profile_id=p.id JOIN public.unit u ON u.organization_id=h.organization_id AND u.id=h.unit_id WHERE p.organization_id=$1 AND u.property_id=$2
        UNION SELECT p.person_id FROM public.owner_profile p JOIN public.ownership_history h ON h.organization_id=p.organization_id AND h.owner_profile_id=p.id JOIN public.unit u ON u.organization_id=h.organization_id AND u.id=h.unit_id WHERE p.organization_id=$1 AND u.property_id=$2
        UNION SELECT p.person_id FROM public.vendor p JOIN public.cost_entry c ON c.organization_id=p.organization_id AND c.vendor_id=p.id WHERE p.organization_id=$1 AND c.property_id=$2)'
      WHEN kind='accounting_scope_binding' THEN 't.organization_id=$1 AND t.property_id=$2 AND (t.cost_entry_id IS NOT NULL OR t.payment_id IS NOT NULL OR t.bank_account_id IS NOT NULL)'
      WHEN kind='cost_category' THEN 'true'
      WHEN kind='organization' THEN 't.id=$1'
      WHEN kind='economic_plan' THEN 't.organization_id=$1 AND t.id=$3'
      WHEN kind='economic_plan_line' THEN 't.organization_id=$1 AND t.economic_plan_id=$3'
      ELSE 't.organization_id=$1 AND t.property_id=$2' END;
    EXECUTE format($query$SELECT COALESCE(jsonb_agg(item ORDER BY item::text),'[]') FROM (SELECT to_jsonb(t) AS item FROM public.%I t WHERE %s ORDER BY to_jsonb(t)::text LIMIT 20001 %s) bounded$query$,kind,condition,CASE WHEN lock_sources THEN 'FOR SHARE NOWAIT' ELSE '' END)
      INTO rows USING org,property,linked_plan;
    IF jsonb_array_length(rows)>20000 THEN RAISE EXCEPTION 'Complete computation source set exceeds supported bounds' USING ERRCODE='23514';END IF;
    parts:=parts||jsonb_build_array(jsonb_build_array(kind,public.bounded_computation_digest(rows)));
  END LOOP;
  RETURN public.bounded_computation_digest(parts);
END $$;
CREATE FUNCTION financial_computation_source_digest(org uuid,property uuid,linked_plan uuid) RETURNS text
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
  SELECT public.financial_computation_source_digest(org,property,linked_plan,false)
$$;
CREATE FUNCTION financial_computation_state(org uuid,kind text,target uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE parent jsonb;lines jsonb;coverage public.coverage_declaration;latest uuid;scope uuid;payload jsonb;property uuid;linked_plan uuid;
BEGIN
  IF kind='settlement_run' THEN
    SELECT to_jsonb(r) INTO parent FROM public.settlement_run r WHERE r.organization_id=org AND r.id=target;
    SELECT COALESCE(jsonb_agg(to_jsonb(l) ORDER BY l.id),'[]') INTO lines FROM public.settlement_line l WHERE l.organization_id=org AND l.settlement_run_id=target;
  ELSIF kind='economic_plan' THEN
    SELECT to_jsonb(p) INTO parent FROM public.economic_plan p WHERE p.organization_id=org AND p.id=target;
    SELECT COALESCE(jsonb_agg(to_jsonb(l) ORDER BY l.id),'[]') INTO lines FROM public.economic_plan_line l WHERE l.organization_id=org AND l.economic_plan_id=target;
  ELSE RAISE EXCEPTION 'Supported financial parent required' USING ERRCODE='22023';END IF;
  payload:=parent->'snapshot_payload';property:=(parent->>'property_id')::uuid;linked_plan:=(parent->>'economic_plan_id')::uuid;
  IF parent IS NULL OR payload IS NULL OR jsonb_typeof(payload)<>'object' OR parent->>'status' NOT IN ('draft','finalized')
    OR parent->>'superseded_by_id' IS NOT NULL THEN RAISE EXCEPTION 'Current computed financial parent required' USING ERRCODE='23514';END IF;
  SELECT * INTO coverage FROM public.coverage_declaration WHERE organization_id=org AND id=(payload#>>'{financial_input_coverage,id}')::uuid;
  SELECT id INTO latest FROM public.coverage_declaration WHERE organization_id=org
    AND CASE kind WHEN 'settlement_run' THEN settlement_run_id=target ELSE economic_plan_id=target END ORDER BY revision DESC LIMIT 1;
  IF coverage.id IS NULL OR coverage.id IS DISTINCT FROM latest OR coverage.property_id IS DISTINCT FROM property
    OR coverage.period_start::text IS DISTINCT FROM parent->>'period_start' OR coverage.period_end::text IS DISTINCT FROM parent->>'period_end'
    OR coverage.source_revision IS DISTINCT FROM payload#>>'{financial_input_coverage,source_revision}' THEN
    RAISE EXCEPTION 'Exact current computation coverage required' USING ERRCODE='23514';END IF;
  scope:=public.financial_scope_id(org,kind,target);
  RETURN jsonb_build_object('version',1,'subject_kind',kind,'subject_id',target,'property_id',property,'scope_id',scope,
    'status',parent->>'status','engine_version',parent->>'engine_version','coverage_id',coverage.id,
    'parent_digest',public.bounded_computation_digest(parent-'updated_at'),
    'lines_digest',public.bounded_computation_digest(lines),'coverage_digest',public.bounded_computation_digest(to_jsonb(coverage)),
    'source_digest',public.financial_computation_source_digest(org,property,linked_plan),
    'mandate_digest',public.bounded_computation_digest(COALESCE((SELECT to_jsonb(m) FROM public.management_mandate m WHERE m.organization_id=org AND m.id=(parent->>'management_mandate_id')::uuid),'null'::jsonb)));
END $$;
CREATE FUNCTION require_computation_authority(org uuid,kind text,target uuid,operation text) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE property uuid;scope uuid;
BEGIN
  IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
    OR kind NOT IN ('settlement_run','economic_plan') OR operation NOT IN ('compute','finalize')
    OR NOT COALESCE(public.scope_actor_role(org) IN ('OWNER_ADMIN','CLERK'),false) THEN
    RAISE EXCEPTION 'Computation principal inaccessible' USING ERRCODE='42501';END IF;
  PERFORM public.lock_intake(org);
  IF kind='settlement_run' THEN SELECT property_id INTO property FROM public.settlement_run WHERE organization_id=org AND id=target FOR UPDATE;
  ELSE SELECT property_id INTO property FROM public.economic_plan WHERE organization_id=org AND id=target FOR UPDATE;END IF;
  scope:=public.financial_scope_id(org,kind,target);
  IF property IS NULL OR NOT public.financial_scope_access(org,kind,target,CASE operation WHEN 'finalize' THEN 'approve' ELSE 'prepare' END)
    OR NOT public.property_financial_source_access(org,property) OR NOT public.property_financial_disclosure_access(org,property,'read')
    OR public.property_source_security_mode(org)='PAUSED'
    OR (scope IS NULL AND public.property_has_classified_finance(org,property))
    OR (scope IS NOT NULL AND NOT public.scope_financial_inputs_classified(org,scope)) THEN
    RAISE EXCEPTION 'Complete computation authority required' USING ERRCODE='42501';END IF;
END $$;
CREATE FUNCTION computation_input_basis(org uuid,kind text,target uuid,lock_sources boolean) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE parent jsonb;context jsonb;mandate jsonb;source_digest text;
BEGIN
  IF kind='settlement_run' THEN SELECT to_jsonb(r) INTO parent FROM public.settlement_run r WHERE r.organization_id=org AND r.id=target;
  ELSIF kind='economic_plan' THEN SELECT to_jsonb(p) INTO parent FROM public.economic_plan p WHERE p.organization_id=org AND p.id=target;
  ELSE RAISE EXCEPTION 'Supported financial parent required' USING ERRCODE='22023';END IF;
  IF parent IS NULL THEN RAISE EXCEPTION 'Financial parent inaccessible' USING ERRCODE='42501';END IF;
  SELECT jsonb_object_agg(key,value) INTO context FROM jsonb_each(parent) WHERE key IN ('property_id','management_mandate_id','period_start','period_end',
    'run_type','unit_id','lease_agreement_id','tenant_profile_id','economic_plan_id','plan_year','advance_payments_total','advance_payments_source');
  IF lock_sources THEN
    SELECT to_jsonb(m) INTO mandate FROM public.management_mandate m WHERE m.organization_id=org AND m.id=(parent->>'management_mandate_id')::uuid FOR SHARE NOWAIT;
  ELSE SELECT to_jsonb(m) INTO mandate FROM public.management_mandate m WHERE m.organization_id=org AND m.id=(parent->>'management_mandate_id')::uuid;END IF;
  source_digest:=public.financial_computation_source_digest(org,(parent->>'property_id')::uuid,(parent->>'economic_plan_id')::uuid,lock_sources);
  RETURN jsonb_build_object('version',1,'context',context,'scope_id',public.financial_scope_id(org,kind,target),
    'source_digest',source_digest,'mandate_digest',public.bounded_computation_digest(COALESCE(mandate,'null'::jsonb)));
END $$;
CREATE FUNCTION begin_financial_computation(org uuid,kind text,target uuid,operation text) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
BEGIN
  IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive computation runtime required' USING ERRCODE='42501';END IF;
  PERFORM public.require_computation_authority(org,kind,target,operation);
  -- Row locks close contact/catalog ABA races that financial advisory locks do
  -- not cover. NOWAIT prevents introducing a reverse-lock application cycle.
  RETURN public.computation_input_basis(org,kind,target,true);
END $$;
CREATE FUNCTION verify_financial_computation_basis(org uuid,kind text,target uuid,operation text,basis jsonb) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
BEGIN
  IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive computation runtime required' USING ERRCODE='42501';END IF;
  PERFORM public.require_computation_authority(org,kind,target,operation);
  IF basis IS NULL OR basis IS DISTINCT FROM public.computation_input_basis(org,kind,target,false) THEN
    RAISE EXCEPTION 'Computation input basis changed' USING ERRCODE='23505';END IF;
END $$;
REVOKE ALL ON FUNCTION financial_computation_source_digest(uuid,uuid,uuid,boolean),computation_input_basis(uuid,text,uuid,boolean),
  begin_financial_computation(uuid,text,uuid,text),verify_financial_computation_basis(uuid,text,uuid,text,jsonb) FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION begin_financial_computation(uuid,text,uuid,text),verify_financial_computation_basis(uuid,text,uuid,text,jsonb) TO app_rw;
REVOKE ALL ON FUNCTION bounded_computation_digest(jsonb),financial_computation_source_digest(uuid,uuid,uuid),financial_computation_state(uuid,text,uuid),
  require_computation_authority(uuid,text,uuid,text) FROM PUBLIC,app_rw,app_worker,app_review;
'''


COMMAND_SQL='''
CREATE FUNCTION prepare_financial_computation(org uuid,kind text,target uuid,operation text) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE state jsonb;
BEGIN
  IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive computation runtime required' USING ERRCODE='42501';END IF;
  PERFORM public.require_computation_authority(org,kind,target,operation);
  state:=public.financial_computation_state(org,kind,target);
  IF state->>'status' IS DISTINCT FROM (CASE operation WHEN 'compute' THEN 'draft' ELSE 'finalized' END) THEN
    RAISE EXCEPTION 'Exact computation operation required' USING ERRCODE='23514';END IF;
  RETURN jsonb_build_object('state',state,'backend_pid',pg_backend_pid(),'transaction_id',pg_current_xact_id()::text);
END $$;
CREATE FUNCTION attest_financial_computation(org uuid,kind text,target uuid,operation text,observed_state jsonb,caller_pid integer,caller_transaction text) RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;created uuid;
BEGIN
  IF session_user<>'app_review' OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
    RAISE EXCEPTION 'Trusted computation runtime required' USING ERRCODE='42501';END IF;
  -- Do not read/lock the outer transaction's uncommitted parent or coverage.
  PERFORM set_config('lock_timeout','200ms',true);
  IF NOT COALESCE(public.scope_actor_role(org) IN ('OWNER_ADMIN','CLERK'),false)
    OR COALESCE((SELECT mode FROM public.property_source_security WHERE organization_id=org),'PREPARATION')='PAUSED' THEN
    RAISE EXCEPTION 'Current computation principal required' USING ERRCODE='42501';END IF;
  IF kind IS NULL OR kind NOT IN ('settlement_run','economic_plan') OR target IS NULL OR operation IS NULL OR operation NOT IN ('compute','finalize')
    OR observed_state IS NULL OR observed_state->>'subject_kind' IS DISTINCT FROM kind OR observed_state->>'subject_id' IS DISTINCT FROM target::text
    OR observed_state->>'status' IS DISTINCT FROM (CASE operation WHEN 'compute' THEN 'draft' ELSE 'finalized' END)
    OR observed_state->>'version' IS DISTINCT FROM '1' OR caller_pid IS NULL OR caller_transaction IS NULL THEN
    RAISE EXCEPTION 'Exact observed computation required' USING ERRCODE='22023';END IF;
  PERFORM public.bounded_computation_digest(observed_state);
  INSERT INTO public.financial_computation_attestation(organization_id,actor_id,backend_pid,transaction_id,subject_kind,subject_id,operation,state)
    VALUES(org,actor,caller_pid,caller_transaction::xid8,kind,target,operation,observed_state) RETURNING id INTO created;
  RETURN created;
END $$;
CREATE FUNCTION record_financial_computation(org uuid,proof_id uuid) RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE proof public.financial_computation_attestation;state jsonb;created uuid;
BEGIN
  IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive computation runtime required' USING ERRCODE='42501';END IF;
  SELECT * INTO proof FROM public.financial_computation_attestation WHERE organization_id=org AND id=proof_id;
  IF proof.id IS NULL OR proof.actor_id IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid
    OR proof.backend_pid<>pg_backend_pid() OR proof.transaction_id<>pg_current_xact_id() OR proof.expires_at<=clock_timestamp() THEN
    RAISE EXCEPTION 'Current transaction computation attestation required' USING ERRCODE='42501';END IF;
  PERFORM public.require_computation_authority(org,proof.subject_kind,proof.subject_id,proof.operation);
  state:=public.financial_computation_state(org,proof.subject_kind,proof.subject_id);
  IF state IS DISTINCT FROM proof.state THEN RAISE EXCEPTION 'Computed state changed' USING ERRCODE='23505';END IF;
  SELECT id INTO created FROM public.financial_computation_receipt WHERE organization_id=org AND attestation_id=proof.id;
  IF created IS NOT NULL THEN RETURN created;END IF;
  INSERT INTO public.financial_computation_receipt(organization_id,attestation_id,actor_id,settlement_run_id,economic_plan_id,coverage_id,operation,state)
    VALUES(org,proof.id,proof.actor_id,CASE WHEN proof.subject_kind='settlement_run' THEN proof.subject_id END,
      CASE WHEN proof.subject_kind='economic_plan' THEN proof.subject_id END,(state->>'coverage_id')::uuid,proof.operation,state) RETURNING id INTO created;
  RETURN created;
END $$;
CREATE FUNCTION seal_financial_computation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE proof public.financial_computation_attestation;kind text;target uuid;
BEGIN
  SELECT * INTO STRICT proof FROM public.financial_computation_attestation WHERE organization_id=NEW.organization_id AND id=NEW.attestation_id;
  kind:=CASE WHEN NEW.settlement_run_id IS NOT NULL THEN 'settlement_run' ELSE 'economic_plan' END;target:=COALESCE(NEW.settlement_run_id,NEW.economic_plan_id);
  IF (proof.actor_id,proof.subject_kind,proof.subject_id,proof.operation,proof.state) IS DISTINCT FROM (NEW.actor_id,kind,target,NEW.operation,NEW.state)
    OR proof.backend_pid<>pg_backend_pid() OR proof.transaction_id<>pg_current_xact_id() OR proof.expires_at<=clock_timestamp() THEN
    RAISE EXCEPTION 'Sealed computation proof required' USING ERRCODE='23514';END IF;
  -- Multiple legitimate computations in one transaction retain intermediate
  -- receipts; only its last state may be the final persisted state.
  IF NOT EXISTS(SELECT 1 FROM public.financial_computation_receipt newer JOIN public.financial_computation_attestation p
    ON p.organization_id=newer.organization_id AND p.id=newer.attestation_id
    WHERE newer.organization_id=NEW.organization_id AND (newer.settlement_run_id,newer.economic_plan_id) IS NOT DISTINCT FROM (NEW.settlement_run_id,NEW.economic_plan_id)
      AND newer.receipt_number>NEW.receipt_number AND p.backend_pid=pg_backend_pid() AND p.transaction_id=pg_current_xact_id())
    AND NEW.state IS DISTINCT FROM public.financial_computation_state(NEW.organization_id,kind,target) THEN
    RAISE EXCEPTION 'Computation changed before commit' USING ERRCODE='23514';END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER seal_financial_computation AFTER INSERT ON financial_computation_receipt DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION seal_financial_computation();
REVOKE ALL ON FUNCTION prepare_financial_computation(uuid,text,uuid,text),attest_financial_computation(uuid,text,uuid,text,jsonb,integer,text),
  record_financial_computation(uuid,uuid),seal_financial_computation() FROM PUBLIC,app_rw,app_worker,app_review;
GRANT EXECUTE ON FUNCTION prepare_financial_computation(uuid,text,uuid,text),record_financial_computation(uuid,uuid) TO app_rw;
GRANT EXECUTE ON FUNCTION attest_financial_computation(uuid,text,uuid,text,jsonb,integer,text) TO app_review;
'''


MUTATION_SQL='''
CREATE FUNCTION recheck_transaction_computations() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE receipt public.financial_computation_receipt;kind text;target uuid;
BEGIN
  -- INSERT-only deferred checks can be forced early with SET CONSTRAINTS.
  -- Every subsequent source/output mutation queues a new check, including
  -- when constraints have already been changed to immediate mode.
  FOR receipt IN SELECT r.* FROM public.financial_computation_receipt r
    JOIN public.financial_computation_attestation p ON p.organization_id=r.organization_id AND p.id=r.attestation_id
    WHERE p.backend_pid=pg_backend_pid() AND p.transaction_id=pg_current_xact_id()
      AND NOT EXISTS(SELECT 1 FROM public.financial_computation_receipt newer JOIN public.financial_computation_attestation np
        ON np.organization_id=newer.organization_id AND np.id=newer.attestation_id
        WHERE newer.organization_id=r.organization_id AND (newer.settlement_run_id,newer.economic_plan_id) IS NOT DISTINCT FROM (r.settlement_run_id,r.economic_plan_id)
          AND newer.receipt_number>r.receipt_number AND np.backend_pid=pg_backend_pid() AND np.transaction_id=pg_current_xact_id())
  LOOP
    kind:=CASE WHEN receipt.settlement_run_id IS NOT NULL THEN 'settlement_run' ELSE 'economic_plan' END;
    target:=COALESCE(receipt.settlement_run_id,receipt.economic_plan_id);
    IF receipt.state IS DISTINCT FROM public.financial_computation_state(receipt.organization_id,kind,target) THEN
      RAISE EXCEPTION 'Computation changed before commit' USING ERRCODE='23514';END IF;
  END LOOP;
  RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION recheck_transaction_computations() FROM PUBLIC,app_rw,app_worker,app_review;
'''
