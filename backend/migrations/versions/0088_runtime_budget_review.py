"""Bind runtime configuration evidence to one transaction and review budgets explicitly."""
from alembic import op
from sqlalchemy import text

revision = '0088'
down_revision = '0087'
branch_labels = None
depends_on = None

BEFORE = '''          -- Supplied API configuration is not yet a trusted deployment attestation.
          item:=jsonb_set(item,'{findings}',(item->'findings')||'["DEPLOYMENT_POLICY_ATTESTATION_REQUIRED"]'::jsonb);'''
AFTER = '''          IF NOT public.runtime_budget_attested(org,policy) THEN
            item:=jsonb_set(item,'{findings}',(item->'findings')||'["DEPLOYMENT_POLICY_ATTESTATION_REQUIRED"]'::jsonb);
          ELSIF NOT public.runtime_budget_matches_review(org,policy) AND NOT (item->'findings') @> '["BUDGET_POLICY_REVIEW_REQUIRED"]'::jsonb THEN
            item:=jsonb_set(item,'{findings}',(item->'findings')||'["BUDGET_POLICY_REVIEW_REQUIRED"]'::jsonb);
          END IF;'''
RETURN_BEFORE = '      RETURN data;'
RETURN_AFTER = '''      data:=data||jsonb_build_object('runtime_policy_attested',public.runtime_budget_attested(org,policy),
        'runtime_budget_review_id',(SELECT r.id FROM public.runtime_budget_review r WHERE r.organization_id=org ORDER BY sequence DESC LIMIT 1),
        'budget_policy_matches',public.runtime_budget_attested(org,policy) AND public.runtime_budget_matches_review(org,policy));
      RETURN data;'''


def upgrade():
    op.execute('''
    CREATE TABLE runtime_budget_attestation (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL REFERENCES organization(id),
      actor_user_id uuid NOT NULL, backend_pid integer NOT NULL CHECK(backend_pid>0), transaction_id xid8 NOT NULL,
      policy jsonb NOT NULL, policy_digest text NOT NULL CHECK(policy_digest ~ '^[0-9a-f]{64}$'),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(organization_id,id), UNIQUE(organization_id,actor_user_id,backend_pid,transaction_id),
      FOREIGN KEY(organization_id,actor_user_id) REFERENCES "user"(organization_id,id));
    CREATE TABLE runtime_budget_review (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
      organization_id uuid NOT NULL REFERENCES organization(id), attestation_id uuid NOT NULL,
      previous_review_id uuid, reviewed_by uuid NOT NULL, reviewed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      policy jsonb NOT NULL, policy_digest text NOT NULL CHECK(policy_digest ~ '^[0-9a-f]{64}$'),
      reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 2000), request_key uuid NOT NULL, request_digest text NOT NULL,
      UNIQUE(organization_id,id), UNIQUE(organization_id,reviewed_by,request_key),
      FOREIGN KEY(organization_id,attestation_id) REFERENCES runtime_budget_attestation(organization_id,id),
      FOREIGN KEY(organization_id,previous_review_id) REFERENCES runtime_budget_review(organization_id,id),
      FOREIGN KEY(organization_id,reviewed_by) REFERENCES "user"(organization_id,id));
    CREATE INDEX ix_runtime_budget_current ON runtime_budget_review(organization_id,sequence DESC);
    ''')
    for table in ('runtime_budget_attestation','runtime_budget_review'):
        op.execute(f'''ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          CREATE POLICY runtime_budget_private ON {table} AS RESTRICTIVE FOR ALL USING(false) WITH CHECK(false);
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review;
          CREATE TRIGGER protect_runtime_budget_evidence BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();''')
    op.execute('''
    CREATE FUNCTION canonical_runtime_budget(policy jsonb) RETURNS jsonb
    LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF jsonb_typeof(policy) IS DISTINCT FROM 'object'
        OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(policy) k) IS DISTINCT FROM ARRAY['enabled','max_cost_usd','reservation_seconds','window_minutes']
        OR jsonb_typeof(policy->'enabled') IS DISTINCT FROM 'boolean'
        OR NOT COALESCE((policy->>'max_cost_usd')::numeric>0 AND (policy->>'max_cost_usd')::numeric::text NOT IN ('NaN','Infinity','-Infinity')
          AND (policy->>'window_minutes')::integer>0 AND (policy->>'reservation_seconds')::integer>0,false) THEN
        RAISE EXCEPTION 'Invalid runtime budget policy' USING ERRCODE='22023'; END IF;
      RETURN jsonb_build_object('enabled',(policy->>'enabled')::boolean,'max_cost_usd',trim_scale((policy->>'max_cost_usd')::numeric)::text,
        'window_minutes',(policy->>'window_minutes')::integer,'reservation_seconds',(policy->>'reservation_seconds')::integer);
    EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
      RAISE EXCEPTION 'Invalid runtime budget policy' USING ERRCODE='22023';
    END $$;
    CREATE FUNCTION attest_runtime_budget(org uuid,target_pid integer,target_transaction xid8,policy jsonb) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; normalized jsonb; digest text;
      receipt public.runtime_budget_attestation;
    BEGIN
      IF session_user<>'app_review' OR org IS NULL OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR public.scope_actor_role(org) IS NULL THEN RAISE EXCEPTION 'Trusted runtime policy registrar required' USING ERRCODE='42501'; END IF;
      -- The invoking HTTP transaction holds ACL. Do not acquire it on this second
      -- connection: queued revocation must not create an application-level cycle.
      -- The trusted runtime supplies its actual application connection PID/xid8;
      -- these identifiers confer nothing without the registrar's attestation.
      IF target_pid IS NULL OR target_pid<=0 OR target_transaction IS NULL THEN RAISE EXCEPTION 'Invalid runtime transaction' USING ERRCODE='22023'; END IF;
      normalized:=public.canonical_runtime_budget(policy);
      digest:=encode(sha256(convert_to(normalized::text,'UTF8')),'hex');
      INSERT INTO public.runtime_budget_attestation(organization_id,actor_user_id,backend_pid,transaction_id,policy,policy_digest)
        VALUES(org,actor,target_pid,target_transaction,normalized,digest) ON CONFLICT DO NOTHING;
      SELECT * INTO receipt FROM public.runtime_budget_attestation WHERE organization_id=org AND actor_user_id=actor
        AND backend_pid=target_pid AND transaction_id=target_transaction;
      IF receipt.policy_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Runtime policy changed within request' USING ERRCODE='40001'; END IF;
      RETURN jsonb_build_object('policy',receipt.policy,'policy_digest',receipt.policy_digest);
    END $$;
    CREATE FUNCTION current_runtime_budget_attestation(org uuid) RETURNS public.runtime_budget_attestation
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE receipt public.runtime_budget_attestation;
    BEGIN
      IF session_user<>'app_rw' OR org IS NULL OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN NULL; END IF;
      SELECT * INTO receipt FROM public.runtime_budget_attestation WHERE organization_id=org
        AND actor_user_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid
        AND backend_pid=pg_backend_pid() AND transaction_id=pg_current_xact_id();
      RETURN receipt;
    END $$;
    CREATE FUNCTION runtime_budget_attested(org uuid,policy jsonb) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE receipt public.runtime_budget_attestation;
    BEGIN
      receipt:=public.current_runtime_budget_attestation(org);
      RETURN COALESCE(receipt.id IS NOT NULL AND receipt.policy=public.canonical_runtime_budget(policy),false);
    END $$;
    CREATE FUNCTION runtime_budget_matches_review(org uuid,policy jsonb) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE normalized jsonb; reviewed jsonb;
    BEGIN
      normalized:=public.canonical_runtime_budget(policy);
      SELECT r.policy INTO reviewed FROM public.runtime_budget_review r WHERE r.organization_id=org ORDER BY sequence DESC LIMIT 1;
      RETURN COALESCE(reviewed=normalized AND EXISTS(SELECT 1 FROM public.knowledge_budget_policy p WHERE p.organization_id=org
        AND p.max_cost_usd=(normalized->>'max_cost_usd')::numeric AND p.window_minutes=(normalized->>'window_minutes')::integer
        AND p.reservation_seconds=(normalized->>'reservation_seconds')::integer),false);
    END $$;
    CREATE FUNCTION runtime_budget_review_json(receipt public.runtime_budget_review) RETURNS jsonb
    LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
      SELECT CASE WHEN (receipt).id IS NULL THEN NULL ELSE to_jsonb(receipt)-ARRAY['organization_id','attestation_id','sequence','request_key','request_digest'] END
    $$;
    CREATE FUNCTION runtime_budget_status(org uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE proof public.runtime_budget_attestation; receipt public.runtime_budget_review;
    BEGIN
      PERFORM public.require_access_coverage_admin(org,false);
      proof:=public.current_runtime_budget_attestation(org);
      IF proof.id IS NULL THEN RAISE EXCEPTION 'Trusted runtime policy required' USING ERRCODE='42501'; END IF;
      SELECT * INTO receipt FROM public.runtime_budget_review WHERE organization_id=org ORDER BY sequence DESC LIMIT 1;
      RETURN jsonb_build_object('policy',proof.policy,'policy_digest',proof.policy_digest,'review',public.runtime_budget_review_json(receipt),
        'matches_reviewed_policy',public.runtime_budget_matches_review(org,proof.policy));
    END $$;
    CREATE FUNCTION review_runtime_budget(org uuid,request uuid,expected uuid,digest text,explanation text) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; proof public.runtime_budget_attestation;
      receipt public.runtime_budget_review; prior uuid; request_hash text;
    BEGIN
      PERFORM public.require_access_coverage_admin(org,true);
      IF request IS NULL OR digest IS NULL OR digest !~ '^[0-9a-f]{64}$' OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 2000 THEN
        RAISE EXCEPTION 'Invalid runtime budget review' USING ERRCODE='22023'; END IF;
      explanation:=btrim(explanation);
      request_hash:=encode(sha256(convert_to(jsonb_build_object('expected_review_id',expected,'policy_digest',digest,'reason',explanation)::text,'UTF8')),'hex');
      SELECT * INTO receipt FROM public.runtime_budget_review WHERE organization_id=org AND reviewed_by=actor AND request_key=request;
      IF FOUND THEN
        IF receipt.request_digest<>request_hash THEN RAISE EXCEPTION 'Runtime budget review conflict' USING ERRCODE='23505'; END IF;
        RETURN public.runtime_budget_review_json(receipt);
      END IF;
      proof:=public.current_runtime_budget_attestation(org);
      IF proof.id IS NULL THEN RAISE EXCEPTION 'Trusted runtime policy required' USING ERRCODE='42501'; END IF;
      SELECT id INTO prior FROM public.runtime_budget_review WHERE organization_id=org ORDER BY sequence DESC LIMIT 1;
      IF prior IS DISTINCT FROM expected OR proof.policy_digest<>digest THEN RAISE EXCEPTION 'Runtime policy review is stale' USING ERRCODE='40001'; END IF;
      UPDATE public.knowledge_budget_policy SET max_cost_usd=(proof.policy->>'max_cost_usd')::numeric,
        window_minutes=(proof.policy->>'window_minutes')::integer,reservation_seconds=(proof.policy->>'reservation_seconds')::integer,
        changed_by=actor,reason=explanation WHERE organization_id=org;
      IF NOT FOUND THEN
        INSERT INTO public.knowledge_budget_policy(organization_id,max_cost_usd,window_minutes,reservation_seconds,changed_by,reason)
          VALUES(org,(proof.policy->>'max_cost_usd')::numeric,(proof.policy->>'window_minutes')::integer,(proof.policy->>'reservation_seconds')::integer,actor,explanation);
      END IF;
      INSERT INTO public.runtime_budget_review(organization_id,attestation_id,previous_review_id,reviewed_by,policy,policy_digest,reason,request_key,request_digest)
        VALUES(org,proof.id,prior,actor,proof.policy,proof.policy_digest,explanation,request,request_hash) RETURNING * INTO receipt;
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(org,actor,'knowledge_budget_policy',org,'knowledge.runtime_budget_reviewed',jsonb_build_object('review_id',receipt.id,'policy_digest',digest));
      RETURN public.runtime_budget_review_json(receipt);
    END $$;
    REVOKE ALL ON FUNCTION canonical_runtime_budget(jsonb),attest_runtime_budget(uuid,integer,xid8,jsonb),
      current_runtime_budget_attestation(uuid),runtime_budget_attested(uuid,jsonb),runtime_budget_matches_review(uuid,jsonb),
      runtime_budget_review_json(runtime_budget_review),runtime_budget_status(uuid),review_runtime_budget(uuid,uuid,uuid,text,text)
      FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION attest_runtime_budget(uuid,integer,xid8,jsonb) TO app_review;
    GRANT EXECUTE ON FUNCTION runtime_budget_status(uuid),review_runtime_budget(uuid,uuid,uuid,text,text) TO app_rw;
    ''')
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(BEFORE)==1 and definition.count(RETURN_BEFORE)==1
    version="'{checker_version}','2'"
    assert definition.count(version)==1
    op.execute(definition.replace(BEFORE,AFTER).replace(RETURN_BEFORE,RETURN_AFTER).replace(version,"'{checker_version}','3'"))
    op.execute('''ALTER TABLE access_coverage_snapshot DROP CONSTRAINT access_coverage_snapshot_checker_version_check;
      ALTER TABLE access_coverage_snapshot ADD CHECK(checker_version IN (1,2,3));
      ALTER TABLE access_coverage_snapshot ALTER COLUMN checker_version SET DEFAULT 3;''')


def downgrade():
    op.execute('LOCK TABLE access_coverage_snapshot,runtime_budget_attestation,runtime_budget_review IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM runtime_budget_attestation) OR EXISTS(SELECT 1 FROM runtime_budget_review)
      OR EXISTS(SELECT 1 FROM access_coverage_snapshot WHERE checker_version=3) THEN RAISE EXCEPTION 'Retain runtime budget evidence; repair forward'; END IF; END $$""")
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(AFTER)==1 and definition.count(RETURN_AFTER)==1
    op.execute(definition.replace(AFTER,BEFORE).replace(RETURN_AFTER,RETURN_BEFORE).replace("'{checker_version}','3'","'{checker_version}','2'"))
    op.execute('''ALTER TABLE access_coverage_snapshot DROP CONSTRAINT access_coverage_snapshot_checker_version_check;
      ALTER TABLE access_coverage_snapshot ADD CHECK(checker_version IN (1,2));
      ALTER TABLE access_coverage_snapshot ALTER COLUMN checker_version SET DEFAULT 2;
      DROP FUNCTION review_runtime_budget(uuid,uuid,uuid,text,text),runtime_budget_status(uuid),runtime_budget_review_json(runtime_budget_review),
        runtime_budget_matches_review(uuid,jsonb),runtime_budget_attested(uuid,jsonb),current_runtime_budget_attestation(uuid),
        attest_runtime_budget(uuid,integer,xid8,jsonb),canonical_runtime_budget(jsonb);
      DROP TABLE runtime_budget_review,runtime_budget_attestation;''')
