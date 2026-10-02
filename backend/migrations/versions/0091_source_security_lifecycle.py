"""Authorize explicit source activation, pause and resumption atomically."""
from alembic import op

revision='0091'
down_revision='0090'
branch_labels=None
depends_on=None


def upgrade():
    op.execute('''
    CREATE TABLE source_security_transition (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), sequence bigint GENERATED ALWAYS AS IDENTITY,
      organization_id uuid NOT NULL REFERENCES organization(id), actor_user_id uuid NOT NULL,
      previous_transition_id uuid, previous_mode text NOT NULL, previous_changed_at timestamptz,
      target_mode text NOT NULL CHECK(target_mode IN ('ENFORCED','PAUSED')), changed_at timestamptz NOT NULL,
      acceptance_id uuid, manifest_digest text, assessment_digest text,
      reason text NOT NULL, request_key uuid NOT NULL, request_digest text NOT NULL,
      UNIQUE(organization_id,id), UNIQUE(organization_id,actor_user_id,request_key),
      UNIQUE NULLS NOT DISTINCT(organization_id,previous_transition_id),
      FOREIGN KEY(organization_id,actor_user_id) REFERENCES "user"(organization_id,id),
      FOREIGN KEY(organization_id,previous_transition_id) REFERENCES source_security_transition(organization_id,id),
      FOREIGN KEY(organization_id,acceptance_id) REFERENCES access_disposition_acceptance(organization_id,id));
    ALTER TABLE source_security_transition ENABLE ROW LEVEL SECURITY;
    ALTER TABLE source_security_transition FORCE ROW LEVEL SECURITY;
    CREATE POLICY transition_private ON source_security_transition AS RESTRICTIVE FOR ALL USING(false) WITH CHECK(false);
    REVOKE ALL ON source_security_transition FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER protect_source_transition BEFORE UPDATE OR DELETE ON source_security_transition
      FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();
    -- Minted and removed within one command. No committed bearer authorization.
    CREATE TABLE source_security_authorization (
      organization_id uuid PRIMARY KEY, actor_user_id uuid NOT NULL, backend_pid integer NOT NULL,
      transaction_id xid8 NOT NULL, previous_mode text NOT NULL, previous_changed_at timestamptz,
      target_mode text NOT NULL CHECK(target_mode='ENFORCED'));
    ALTER TABLE source_security_authorization ENABLE ROW LEVEL SECURITY;
    ALTER TABLE source_security_authorization FORCE ROW LEVEL SECURITY;
    CREATE POLICY authorization_private ON source_security_authorization AS RESTRICTIVE FOR ALL USING(false) WITH CHECK(false);
    REVOKE ALL ON source_security_authorization FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION source_security_receipt(receipt public.source_security_transition) RETURNS jsonb
    LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
      SELECT CASE WHEN (receipt).id IS NULL THEN NULL ELSE to_jsonb(receipt)-ARRAY['organization_id','sequence','request_key','request_digest'] END
    $$;
    CREATE FUNCTION source_security_request(payload jsonb) RETURNS jsonb
    LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
    DECLARE result jsonb; expected_time timestamptz; expected_text text;
    BEGIN
      IF jsonb_typeof(payload) IS DISTINCT FROM 'object' OR
        (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(payload) k) IS DISTINCT FROM
        ARRAY['acceptance_id','acknowledged','assessment_digest','expected_changed_at','expected_mode','expected_transition_id','manifest_digest','reason','target_mode']
        OR payload->'acknowledged' IS DISTINCT FROM 'true'::jsonb
        OR NOT COALESCE(payload->>'target_mode' IN ('ENFORCED','PAUSED'),false)
        OR NOT COALESCE(payload->>'expected_mode' IN ('PREPARATION','ENFORCED','PAUSED'),false)
        OR jsonb_typeof(payload->'reason') IS DISTINCT FROM 'string'
        OR length(btrim(payload->>'reason')) NOT BETWEEN 1 AND 2000 THEN
        RAISE EXCEPTION 'Invalid source transition' USING ERRCODE='22023'; END IF;
      expected_text:=payload->>'expected_changed_at';
      IF expected_text IS NOT NULL AND (jsonb_typeof(payload->'expected_changed_at')<>'string'
        OR expected_text !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$') THEN
        RAISE EXCEPTION 'Expected timestamp must be absolute with timezone' USING ERRCODE='22023'; END IF;
      expected_time:=expected_text::timestamptz;
      IF expected_time IS NOT NULL AND NOT isfinite(expected_time) THEN RAISE EXCEPTION 'Invalid source timestamp' USING ERRCODE='22023'; END IF;
      result:=payload||jsonb_build_object('reason',btrim(payload->>'reason'),'expected_changed_at',to_char(expected_time AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'expected_transition_id',(payload->>'expected_transition_id')::uuid,'acceptance_id',(payload->>'acceptance_id')::uuid);
      IF result->>'target_mode'='ENFORCED' THEN
        IF result->>'acceptance_id' IS NULL OR NOT COALESCE(result->>'manifest_digest' ~ '^[0-9a-f]{64}$',false)
          OR NOT COALESCE(result->>'assessment_digest' ~ '^[0-9a-f]{64}$',false) THEN
          RAISE EXCEPTION 'Accepted coverage required' USING ERRCODE='22023'; END IF;
      ELSIF result->>'acceptance_id' IS NOT NULL OR result->>'manifest_digest' IS NOT NULL OR result->>'assessment_digest' IS NOT NULL THEN
        RAISE EXCEPTION 'Pause does not accept activation evidence' USING ERRCODE='22023'; END IF;
      RETURN result;
    EXCEPTION WHEN invalid_text_representation OR invalid_datetime_format OR datetime_field_overflow THEN
      RAISE EXCEPTION 'Invalid source transition' USING ERRCODE='22023';
    END $$;

    CREATE FUNCTION source_security_status(org uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE state public.property_source_security; receipt public.source_security_transition;
    BEGIN
      PERFORM public.require_access_coverage_admin(org,false);
      SELECT * INTO state FROM public.property_source_security WHERE organization_id=org;
      SELECT * INTO receipt FROM public.source_security_transition WHERE organization_id=org ORDER BY sequence DESC LIMIT 1;
      RETURN jsonb_build_object('mode',COALESCE(state.mode,'PREPARATION'),'changed_at',state.changed_at,
        'last_transition',public.source_security_receipt(receipt));
    END $$;
    CREATE FUNCTION replay_source_security_transition(org uuid,request uuid,payload jsonb) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE normalized jsonb; receipt public.source_security_transition;
    BEGIN
      PERFORM public.require_access_coverage_admin(org,true);
      normalized:=public.source_security_request(payload);
      IF request IS NULL THEN RAISE EXCEPTION 'Request key required' USING ERRCODE='22023'; END IF;
      SELECT * INTO receipt FROM public.source_security_transition WHERE organization_id=org
        AND actor_user_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid AND request_key=request;
      IF NOT FOUND THEN RETURN NULL; END IF;
      IF receipt.request_digest<>encode(sha256(convert_to(normalized::text,'UTF8')),'hex') THEN
        RAISE EXCEPTION 'Source transition request conflict' USING ERRCODE='23505'; END IF;
      RETURN public.source_security_receipt(receipt);
    END $$;

    CREATE OR REPLACE FUNCTION property_source_activation_ready(org uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF session_user<>'app_rw' OR org IS NULL OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN false; END IF;
      RETURN EXISTS(SELECT 1 FROM public.source_security_authorization a
        LEFT JOIN public.property_source_security s ON s.organization_id=a.organization_id
        WHERE a.organization_id=org AND a.actor_user_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid
          AND a.backend_pid=pg_backend_pid() AND a.transaction_id=pg_current_xact_id() AND a.target_mode='ENFORCED'
          AND a.previous_mode=COALESCE(s.mode,'PREPARATION') AND a.previous_changed_at IS NOT DISTINCT FROM s.changed_at);
    END $$;

    CREATE FUNCTION transition_source_security(org uuid,request uuid,payload jsonb) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE normalized jsonb; replay jsonb; state public.property_source_security; prior public.source_security_transition;
      receipt public.source_security_transition; decision public.access_disposition_acceptance; source public.access_coverage_snapshot;
      proof public.runtime_budget_attestation; data jsonb; actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      target text; changed timestamptz;
    BEGIN
      replay:=public.replay_source_security_transition(org,request,payload);
      IF replay IS NOT NULL THEN RETURN replay; END IF;
      normalized:=public.source_security_request(payload); target:=normalized->>'target_mode';
      SELECT * INTO state FROM public.property_source_security WHERE organization_id=org;
      SELECT * INTO prior FROM public.source_security_transition WHERE organization_id=org ORDER BY sequence DESC LIMIT 1;
      IF COALESCE(state.mode,'PREPARATION') IS DISTINCT FROM normalized->>'expected_mode'
        OR state.changed_at IS DISTINCT FROM (normalized->>'expected_changed_at')::timestamptz
        OR prior.id IS DISTINCT FROM (normalized->>'expected_transition_id')::uuid
        OR (prior.id IS NOT NULL AND (state.mode IS DISTINCT FROM prior.target_mode OR state.changed_at IS DISTINCT FROM prior.changed_at))
        OR (prior.id IS NULL AND state.mode IN ('ENFORCED','PAUSED'))
        OR NOT ((COALESCE(state.mode,'PREPARATION') IN ('PREPARATION','PAUSED') AND target='ENFORCED') OR (state.mode='ENFORCED' AND target='PAUSED')) THEN
        RAISE EXCEPTION 'Source security state changed' USING ERRCODE='40001'; END IF;
      IF target='ENFORCED' THEN
        SELECT * INTO decision FROM public.access_disposition_acceptance WHERE organization_id=org AND id=(normalized->>'acceptance_id')::uuid;
        IF NOT FOUND THEN RAISE EXCEPTION 'Disposition inaccessible' USING ERRCODE='42501'; END IF;
        SELECT * INTO source FROM public.access_coverage_snapshot WHERE organization_id=org AND id=decision.snapshot_id;
        proof:=public.current_runtime_budget_attestation(org);
        IF proof.id IS NULL THEN RAISE EXCEPTION 'Trusted current runtime required' USING ERRCODE='42501'; END IF;
        data:=public.access_disposition_data(org,source.id,proof.policy);
        IF decision.manifest_digest<>normalized->>'manifest_digest' OR decision.assessment_digest<>normalized->>'assessment_digest'
          OR data->>'manifest_digest'<>decision.manifest_digest OR data->>'assessment_digest'<>decision.assessment_digest
          OR NOT (data->>'current')::boolean OR NOT (data->>'reviewed')::boolean
          OR (data->>'blocker_count')::integer<>0 OR (data->>'active_work_count')::integer<>0
          OR (state.changed_at IS NOT NULL AND source.generated_at<=state.changed_at) THEN
          RAISE EXCEPTION 'Activation evidence is stale or unresolved' USING ERRCODE='40001'; END IF;
        INSERT INTO public.source_security_authorization(organization_id,actor_user_id,backend_pid,transaction_id,previous_mode,previous_changed_at,target_mode)
          VALUES(org,actor,pg_backend_pid(),pg_current_xact_id(),COALESCE(state.mode,'PREPARATION'),state.changed_at,target);
      END IF;
      UPDATE public.property_source_security SET mode=target,changed_by=actor,reason=normalized->>'reason'
        WHERE organization_id=org RETURNING changed_at INTO changed;
      IF NOT FOUND THEN
        INSERT INTO public.property_source_security(organization_id,mode,changed_by,reason)
          VALUES(org,target,actor,normalized->>'reason') RETURNING changed_at INTO changed;
      END IF;
      DELETE FROM public.source_security_authorization WHERE organization_id=org;
      INSERT INTO public.source_security_transition(organization_id,actor_user_id,previous_transition_id,previous_mode,previous_changed_at,
        target_mode,changed_at,acceptance_id,manifest_digest,assessment_digest,reason,request_key,request_digest)
        VALUES(org,actor,prior.id,COALESCE(state.mode,'PREPARATION'),state.changed_at,target,changed,
          (normalized->>'acceptance_id')::uuid,normalized->>'manifest_digest',normalized->>'assessment_digest',normalized->>'reason',request,
          encode(sha256(convert_to(normalized::text,'UTF8')),'hex')) RETURNING * INTO receipt;
      RETURN public.source_security_receipt(receipt);
    END $$;
    REVOKE ALL ON FUNCTION source_security_receipt(public.source_security_transition),source_security_request(jsonb),source_security_status(uuid),
      replay_source_security_transition(uuid,uuid,jsonb),transition_source_security(uuid,uuid,jsonb),property_source_activation_ready(uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION source_security_status(uuid),replay_source_security_transition(uuid,uuid,jsonb),transition_source_security(uuid,uuid,jsonb) TO app_rw;
    ''')


def downgrade():
    op.execute('LOCK TABLE property_source_security,source_security_transition,source_security_authorization IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM source_security_transition) OR EXISTS(SELECT 1 FROM source_security_authorization)
      OR EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain source lifecycle evidence; pause and repair forward'; END IF; END $$""")
    op.execute('''
    CREATE OR REPLACE FUNCTION property_source_activation_ready(org uuid) RETURNS boolean
      LANGUAGE sql SET search_path=pg_catalog,pg_temp AS $$ SELECT false $$;
    ALTER FUNCTION property_source_activation_ready(uuid) SECURITY INVOKER;
    ALTER FUNCTION property_source_activation_ready(uuid) RESET row_security;
    DROP FUNCTION transition_source_security(uuid,uuid,jsonb),replay_source_security_transition(uuid,uuid,jsonb),source_security_status(uuid),
      source_security_request(jsonb),source_security_receipt(public.source_security_transition);
    DROP TABLE source_security_authorization,source_security_transition;
    ''')
