"""Apply explicitly accepted, snapshot-bound grant changes as one transaction."""
from alembic import op
from sqlalchemy import text

revision = '0084'
down_revision = '0083'
branch_labels = None
depends_on = None


def upgrade():
    # Retained grants still need to be visible/revocable after membership removal.
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_grants(uuid,text,uuid,timestamptz)'::regprocedure)"))
    assert definition.count('JOIN public.membership m') == 1
    op.execute(definition.replace('JOIN public.membership m','LEFT JOIN public.membership m'))
    op.execute('''
    CREATE TABLE access_grant_proposal (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL,
      snapshot_id uuid NOT NULL, manifest_digest text NOT NULL, proposal_digest text NOT NULL,
      changes jsonb NOT NULL CHECK(jsonb_typeof(changes)='array' AND jsonb_array_length(changes) BETWEEN 1 AND 100),
      proposed_by uuid NOT NULL, proposed_at timestamptz NOT NULL DEFAULT clock_timestamp(), request_key uuid NOT NULL,
      UNIQUE(organization_id,id), UNIQUE(organization_id,snapshot_id), UNIQUE(organization_id,proposed_by,request_key),
      FOREIGN KEY(organization_id,snapshot_id) REFERENCES access_coverage_snapshot(organization_id,id),
      FOREIGN KEY(organization_id,proposed_by) REFERENCES "user"(organization_id,id));
    CREATE TABLE access_grant_acceptance (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL,
      proposal_id uuid NOT NULL, proposal_digest text NOT NULL, results jsonb NOT NULL,
      accepted_by uuid NOT NULL, accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(), request_key uuid NOT NULL,
      UNIQUE(organization_id,proposal_id), UNIQUE(organization_id,accepted_by,request_key),
      FOREIGN KEY(organization_id,proposal_id) REFERENCES access_grant_proposal(organization_id,id),
      FOREIGN KEY(organization_id,accepted_by) REFERENCES "user"(organization_id,id));
    ''')
    for table in ('access_grant_proposal','access_grant_acceptance'):
        op.execute(f'''ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          CREATE POLICY coverage_private ON {table} AS RESTRICTIVE FOR ALL USING(false) WITH CHECK(false);
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review;
          CREATE TRIGGER protect_proposal BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();''')
    op.execute('''
    CREATE FUNCTION normalize_access_grant_changes(changes jsonb) RETURNS jsonb
    LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
    DECLARE item jsonb; result jsonb:='[]'; expiry_text text; expiry timestamptz; caps jsonb;
    BEGIN
      IF jsonb_typeof(changes) IS DISTINCT FROM 'array' OR jsonb_array_length(changes) NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'Invalid grant changes' USING ERRCODE='22023'; END IF;
      FOR item IN SELECT value FROM jsonb_array_elements(changes) LOOP
        IF jsonb_typeof(item) IS DISTINCT FROM 'object' OR
          (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(item) k) IS DISTINCT FROM
            ARRAY['audience_id','capabilities','domain_kind','expected_grant_id','expires_at','user_id']
          OR jsonb_typeof(item->'capabilities') IS DISTINCT FROM 'array' THEN
          RAISE EXCEPTION 'Invalid grant change fields' USING ERRCODE='22023'; END IF;
        expiry_text:=item->>'expires_at';
        IF expiry_text IS NOT NULL THEN
          IF jsonb_typeof(item->'expires_at')<>'string' OR expiry_text !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
            RAISE EXCEPTION 'Expiry must be absolute with timezone' USING ERRCODE='22023'; END IF;
          expiry:=expiry_text::timestamptz;
          item:=jsonb_set(item,'{expires_at}',to_jsonb(to_char(expiry AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')));
        END IF;
        SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]') INTO caps FROM jsonb_array_elements(item->'capabilities');
        item:=jsonb_set(item,'{capabilities}',caps);
        item:=item||jsonb_build_object('audience_id',(item->>'audience_id')::uuid,'user_id',(item->>'user_id')::uuid,
          'expected_grant_id',(item->>'expected_grant_id')::uuid);
        result:=result||jsonb_build_array(item);
      END LOOP;
      SELECT jsonb_agg(value ORDER BY value->>'domain_kind',value->>'audience_id',value->>'user_id') INTO result FROM jsonb_array_elements(result);
      RETURN result;
    END $$;
    REVOKE ALL ON FUNCTION normalize_access_grant_changes(jsonb) FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION validate_access_grant_changes(org uuid,changes jsonb) RETURNS void
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE item jsonb; kind text; audience uuid; member uuid; expected uuid; actual uuid; expiry timestamptz;
      caps text[]; allowed text[]; role text; active boolean; target_exists boolean; family text; target text; column_name text;
    BEGIN
      IF jsonb_typeof(changes) IS DISTINCT FROM 'array' OR jsonb_array_length(changes) NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'Invalid grant changes' USING ERRCODE='22023'; END IF;
      IF (SELECT count(*) FROM jsonb_array_elements(changes))<>(SELECT count(DISTINCT (v->>'domain_kind',v->>'audience_id',v->>'user_id')) FROM jsonb_array_elements(changes) v) THEN
        RAISE EXCEPTION 'Repeated grant target' USING ERRCODE='22023'; END IF;
      FOR item IN SELECT value FROM jsonb_array_elements(changes) LOOP
        IF jsonb_typeof(item) IS DISTINCT FROM 'object' OR
          (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(item) k) IS DISTINCT FROM
            ARRAY['audience_id','capabilities','domain_kind','expected_grant_id','expires_at','user_id']
          OR jsonb_typeof(item->'capabilities') IS DISTINCT FROM 'array' THEN
          RAISE EXCEPTION 'Invalid grant change fields' USING ERRCODE='22023'; END IF;
        kind:=item->>'domain_kind'; audience:=(item->>'audience_id')::uuid; member:=(item->>'user_id')::uuid;
        expected:=(item->>'expected_grant_id')::uuid; expiry:=(item->>'expires_at')::timestamptz;
        SELECT COALESCE(array_agg(value ORDER BY value),'{}') INTO caps FROM jsonb_array_elements_text(item->'capabilities');
        IF kind='financial' THEN family:='scope_grant'; target:='accounting_scope'; column_name:='scope_id'; allowed:=ARRAY['read','prepare','confirm','approve','send','export'];
        ELSIF kind='property' THEN family:='property_data_grant'; target:='property_data_domain'; column_name:='domain_id'; allowed:=ARRAY['read','prepare','approve','export'];
        ELSIF kind='person' THEN family:='person_data_grant'; target:='person_data_domain'; column_name:='domain_id'; allowed:=ARRAY['read','prepare','export'];
        ELSE RAISE EXCEPTION 'Unsupported grant domain' USING ERRCODE='22023'; END IF;
        IF audience IS NULL OR member IS NULL OR array_position(caps,NULL) IS NOT NULL
          OR NOT caps<@allowed OR cardinality(caps)<>(SELECT count(DISTINCT c) FROM unnest(caps) c)
          OR (cardinality(caps)>0 AND NOT 'read'=ANY(caps))
          OR (cardinality(caps)=0 AND (expected IS NULL OR expiry IS NOT NULL))
          OR (expiry IS NOT NULL AND (NOT isfinite(expiry) OR expiry<=clock_timestamp())) THEN
          RAISE EXCEPTION 'Invalid capabilities or expiry' USING ERRCODE='22023'; END IF;
        SELECT m.role,u.is_active INTO role,active FROM public."user" u LEFT JOIN public.membership m
          ON u.organization_id=m.organization_id AND u.id=m.user_id WHERE u.organization_id=org AND u.id=member;
        IF NOT FOUND OR (cardinality(caps)>0 AND (NOT active OR role IS NULL)) THEN RAISE EXCEPTION 'Grant member inaccessible' USING ERRCODE='42501'; END IF;
        IF cardinality(caps)>0 AND role NOT IN ('OWNER_ADMIN','CLERK') AND NOT caps<@
          (CASE WHEN kind='financial' THEN ARRAY['read'] ELSE ARRAY['read','export'] END) THEN
          RAISE EXCEPTION 'Capabilities exceed current member role' USING ERRCODE='22023'; END IF;
        EXECUTE format('SELECT EXISTS(SELECT 1 FROM public.%I WHERE organization_id=$1 AND id=$2)',target)
          INTO target_exists USING org,audience;
        IF NOT target_exists THEN RAISE EXCEPTION 'Grant audience inaccessible' USING ERRCODE='42501'; END IF;
        EXECUTE format('SELECT id FROM public.%I WHERE organization_id=$1 AND %I=$2 AND user_id=$3 AND revoked_at IS NULL',family,column_name)
          INTO actual USING org,audience,member;
        IF actual IS DISTINCT FROM expected THEN RAISE EXCEPTION 'Grant predecessor changed' USING ERRCODE='40001'; END IF;
      END LOOP;
    END $$;
    REVOKE ALL ON FUNCTION validate_access_grant_changes(uuid,jsonb) FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION propose_access_grants(org uuid,snapshot uuid,request uuid,digest text,changes jsonb,policy jsonb) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; source public.access_coverage_snapshot;
      proposal public.access_grant_proposal; fingerprint text;
    BEGIN
      PERFORM public.require_access_coverage_admin(org,true);
      IF request IS NULL OR digest IS NULL OR digest !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'Invalid proposal' USING ERRCODE='22023'; END IF;
      changes:=public.normalize_access_grant_changes(changes);
      fingerprint:=encode(sha256(convert_to(jsonb_build_object('snapshot_id',snapshot,'manifest_digest',digest,'changes',changes)::text,'UTF8')),'hex');
      SELECT * INTO proposal FROM public.access_grant_proposal WHERE organization_id=org AND proposed_by=actor AND request_key=request;
      IF FOUND THEN
        IF proposal.proposal_digest<>fingerprint THEN RAISE EXCEPTION 'Proposal request conflict' USING ERRCODE='23505'; END IF;
        RETURN to_jsonb(proposal)-ARRAY['organization_id','request_key'];
      END IF;
      SELECT * INTO source FROM public.access_coverage_snapshot WHERE organization_id=org AND id=snapshot;
      IF NOT FOUND THEN RAISE EXCEPTION 'Snapshot inaccessible' USING ERRCODE='42501'; END IF;
      IF NOT EXISTS(SELECT 1 FROM public.access_coverage_review WHERE organization_id=org AND snapshot_id=snapshot)
        OR source.manifest_digest<>digest OR encode(sha256(convert_to(public.access_coverage_manifest(org,policy)::text,'UTF8')),'hex')<>digest THEN
        RAISE EXCEPTION 'Review current snapshot before proposing changes' USING ERRCODE='40001'; END IF;
      PERFORM public.validate_access_grant_changes(org,changes);
      INSERT INTO public.access_grant_proposal(organization_id,snapshot_id,manifest_digest,proposal_digest,changes,proposed_by,request_key)
        VALUES(org,snapshot,digest,fingerprint,changes,actor,request) RETURNING * INTO proposal;
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(org,actor,'access_coverage_snapshot',snapshot,'access.grants_proposed',jsonb_build_object('proposal_id',proposal.id,'proposal_digest',fingerprint,'change_count',jsonb_array_length(changes)));
      RETURN to_jsonb(proposal)-ARRAY['organization_id','request_key'];
    END $$;

    CREATE FUNCTION accept_access_grants(org uuid,proposal_id uuid,request uuid,digest text,policy jsonb) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; proposal public.access_grant_proposal;
      receipt public.access_grant_acceptance; results jsonb:='[]'; item jsonb; kind text; family text; column_name text;
      audience uuid; member uuid; previous uuid; created uuid; caps varchar(16)[]; expiry timestamptz;
    BEGIN
      PERFORM public.require_access_coverage_admin(org,true);
      IF request IS NULL OR digest IS NULL OR digest !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'Invalid acceptance' USING ERRCODE='22023'; END IF;
      SELECT * INTO receipt FROM public.access_grant_acceptance a WHERE a.organization_id=org AND a.accepted_by=actor AND a.request_key=request;
      IF FOUND THEN
        IF (receipt.proposal_id,receipt.proposal_digest) IS DISTINCT FROM (proposal_id,digest) THEN RAISE EXCEPTION 'Acceptance request conflict' USING ERRCODE='23505'; END IF;
        RETURN to_jsonb(receipt)-ARRAY['organization_id','request_key'];
      END IF;
      SELECT * INTO proposal FROM public.access_grant_proposal p WHERE p.organization_id=org AND p.id=proposal_id;
      IF NOT FOUND THEN RAISE EXCEPTION 'Proposal inaccessible' USING ERRCODE='42501'; END IF;
      IF proposal.proposal_digest<>digest OR EXISTS(SELECT 1 FROM public.access_grant_acceptance a WHERE a.organization_id=org AND a.proposal_id=proposal.id)
        OR encode(sha256(convert_to(public.access_coverage_manifest(org,policy)::text,'UTF8')),'hex')<>proposal.manifest_digest THEN
        RAISE EXCEPTION 'Proposal already accepted or underlying evidence changed' USING ERRCODE='40001'; END IF;
      PERFORM public.validate_access_grant_changes(org,proposal.changes);
      FOR item IN SELECT value FROM jsonb_array_elements(proposal.changes) LOOP
        kind:=item->>'domain_kind'; audience:=(item->>'audience_id')::uuid; member:=(item->>'user_id')::uuid;
        previous:=(item->>'expected_grant_id')::uuid; expiry:=(item->>'expires_at')::timestamptz; created:=NULL;
        SELECT COALESCE(array_agg(value ORDER BY value),'{}') INTO caps FROM jsonb_array_elements_text(item->'capabilities');
        family:=CASE kind WHEN 'financial' THEN 'scope_grant' WHEN 'property' THEN 'property_data_grant' ELSE 'person_data_grant' END;
        column_name:=CASE kind WHEN 'financial' THEN 'scope_id' ELSE 'domain_id' END;
        IF previous IS NOT NULL THEN
          EXECUTE format('UPDATE public.%I SET revoked_at=clock_timestamp(),revoked_by=$1 WHERE organization_id=$2 AND id=$3',family) USING actor,org,previous;
        END IF;
        IF cardinality(caps)>0 THEN
          created:=gen_random_uuid();
          IF kind='financial' THEN
            INSERT INTO public.scope_grant(id,organization_id,scope_id,user_id,capabilities,granted_by,reason,expires_at)
              VALUES(created,org,audience,member,caps,actor,'Explicitly accepted access proposal',expiry);
          ELSE
            EXECUTE format('INSERT INTO public.%I(id,organization_id,domain_id,user_id,capabilities,granted_by,request_key,%I,expires_at) VALUES($1,$2,$3,$4,$5,$6,$1,$7,$8)',family,
              CASE WHEN kind='person' THEN 'reason_code' ELSE 'reason' END)
              USING created,org,audience,member,caps,actor,CASE WHEN kind='person' THEN 'TEAM_ACCESS' ELSE 'Explicitly accepted access proposal' END,expiry;
          END IF;
        END IF;
        results:=results||jsonb_build_array(jsonb_build_object('domain_kind',kind,'audience_id',audience,'user_id',member,'revoked_grant_id',previous,'grant_id',created));
      END LOOP;
      INSERT INTO public.access_grant_acceptance(organization_id,proposal_id,proposal_digest,results,accepted_by,request_key)
        VALUES(org,proposal.id,digest,results,actor,request) RETURNING * INTO receipt;
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(org,actor,'access_coverage_snapshot',proposal.snapshot_id,'access.grants_accepted',jsonb_build_object('acceptance_id',receipt.id,'proposal_id',proposal.id,'proposal_digest',digest,'results',results));
      RETURN to_jsonb(receipt)-ARRAY['organization_id','request_key'];
    END $$;

    CREATE FUNCTION access_grant_proposal_for_snapshot(org uuid,snapshot uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE result jsonb;
    BEGIN
      PERFORM public.require_access_coverage_admin(org,false);
      IF NOT EXISTS(SELECT 1 FROM public.access_coverage_snapshot WHERE organization_id=org AND id=snapshot) THEN
        RAISE EXCEPTION 'Snapshot inaccessible' USING ERRCODE='42501'; END IF;
      SELECT (to_jsonb(p)-ARRAY['organization_id','request_key'])||jsonb_build_object('acceptance',to_jsonb(a)-ARRAY['organization_id','request_key']) INTO result
        FROM public.access_grant_proposal p LEFT JOIN public.access_grant_acceptance a ON a.organization_id=p.organization_id AND a.proposal_id=p.id
        WHERE p.organization_id=org AND p.snapshot_id=snapshot;
      RETURN result;
    END $$;
    REVOKE ALL ON FUNCTION propose_access_grants(uuid,uuid,uuid,text,jsonb,jsonb),accept_access_grants(uuid,uuid,uuid,text,jsonb),access_grant_proposal_for_snapshot(uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION propose_access_grants(uuid,uuid,uuid,text,jsonb,jsonb),accept_access_grants(uuid,uuid,uuid,text,jsonb),access_grant_proposal_for_snapshot(uuid,uuid) TO app_rw;
    ''')


def downgrade():
    op.execute('LOCK TABLE access_grant_proposal,access_grant_acceptance IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM access_grant_proposal) THEN RAISE EXCEPTION 'Retain access grant proposals; repair forward'; END IF; END $$")
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_grants(uuid,text,uuid,timestamptz)'::regprocedure)"))
    assert definition.count('JOIN public.membership m') == 1
    op.execute(definition.replace('LEFT JOIN public.membership m','JOIN public.membership m'))
    op.execute('''DROP FUNCTION propose_access_grants(uuid,uuid,uuid,text,jsonb,jsonb),accept_access_grants(uuid,uuid,uuid,text,jsonb),access_grant_proposal_for_snapshot(uuid,uuid),validate_access_grant_changes(uuid,jsonb);
      DROP FUNCTION IF EXISTS normalize_access_grant_changes(jsonb);
      DROP TABLE access_grant_acceptance,access_grant_proposal;''')
