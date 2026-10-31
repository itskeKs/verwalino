"""Durable erasure and import admission; lifecycle guards never acquire locks.

At READ COMMITTED, a write before cooling-off becomes visible is allowed.
Once cooling-off commits, every later statement is refused. In-flight writes
are not drained: the lookup uses statement visibility, without advisory locks
or transaction-local caching that could hide a newly committed state.

Only voucher ancestry binds person fields: confirmed/adopted costs, booked
payments, finalized settlements/plans and their lines, issued/quarantined dunning,
retained invoice originals and issued letter documents. Configuration/evidence
FK closure preserves structure but cannot extend these personal deadlines.
"""
from alembic import op
from sqlalchemy import text

revision = '0147'
down_revision = '0146'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    CREATE TABLE public.organization_deletion (
      organization_id uuid PRIMARY KEY REFERENCES public.organization(id),
      status text NOT NULL CHECK(status IN ('requested','cooling_off','cancelled','deleted_pending_retention')),
      requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      confirmed_at timestamptz, execute_after timestamptz, completed_at timestamptz,
      retention jsonb NOT NULL DEFAULT '{}'::jsonb
    );
    ALTER TABLE public.organization_deletion ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.organization_deletion FORCE ROW LEVEL SECURITY;
    CREATE POLICY deletion_read ON public.organization_deletion FOR SELECT USING (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND public.scope_actor_role(organization_id) IS NOT NULL);
    CREATE POLICY deletion_insert ON public.organization_deletion FOR INSERT WITH CHECK (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND public.scope_actor_role(organization_id)='OWNER_ADMIN');
    CREATE POLICY deletion_update ON public.organization_deletion FOR UPDATE USING (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND public.scope_actor_role(organization_id)='OWNER_ADMIN') WITH CHECK (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND public.scope_actor_role(organization_id)='OWNER_ADMIN');
    REVOKE ALL ON public.organization_deletion FROM PUBLIC,app_rw;
    GRANT SELECT,INSERT,UPDATE ON public.organization_deletion TO app_rw;

    -- The only cross-visibility import data is admission metadata: an empty
    -- tenant flag and collisions of UUIDs supplied by this administrator.
    -- No source rows, hidden counts or values are returned or imported here.
    CREATE FUNCTION public.organization_import_admission(org uuid, identities jsonb DEFAULT '{}'::jsonb)
      RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE tab record; occupied boolean; collisions jsonb:='{}'; ids jsonb;
    BEGIN
      IF session_user<>'app_rw' OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR public.scope_actor_role(org) IS DISTINCT FROM 'OWNER_ADMIN' THEN
        RAISE EXCEPTION 'Current administrator required' USING ERRCODE='42501'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||org::text,0));
      IF public.scope_actor_role(org) IS DISTINCT FROM 'OWNER_ADMIN' THEN
        RAISE EXCEPTION 'Current administrator required' USING ERRCODE='42501'; END IF;
      FOR tab IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r' AND EXISTS(SELECT 1 FROM pg_attribute a
          WHERE a.attrelid=c.oid AND a.attname='organization_id' AND NOT a.attisdropped)
        AND c.relname NOT IN ('user','membership','audit_event','organization_deletion','password_reset_request')
        ORDER BY c.relname LOOP
        EXECUTE format('SELECT EXISTS(SELECT 1 FROM public.%I WHERE organization_id=$1)',tab.relname)
          INTO occupied USING org;
        IF occupied THEN RETURN jsonb_build_object('empty',false,'collisions','{}'::jsonb); END IF;
      END LOOP;
      FOR tab IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r'
          AND identities ? c.relname
          AND EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='id' AND a.atttypid='uuid'::regtype)
          AND EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='organization_id')
          AND c.relname<>'password_reset_request' LOOP
        EXECUTE format('SELECT COALESCE(jsonb_agg(id),''[]''::jsonb) FROM public.%I WHERE id IN
          (SELECT value::uuid FROM jsonb_array_elements_text($1))',tab.relname)
          INTO ids USING identities->tab.relname;
        collisions:=collisions||jsonb_build_object(tab.relname,ids);
      END LOOP;
      RETURN jsonb_build_object('empty',true,'collisions',collisions);
    END $$;
    REVOKE ALL ON FUNCTION public.organization_import_admission(uuid,jsonb) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.organization_import_admission(uuid,jsonb) TO app_rw;
    ''')

    _registration_adapter(True)
    _erasure_authorization(True)
    _lifecycle()
    _erasure_payloads(True)
    _auth_filter(True)


def downgrade():
    _erasure_payloads(False)
    _erasure_authorization(False)
    _registration_adapter(False)
    _auth_filter(False)
    op.execute('''
    LOCK TABLE public.organization_deletion IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.organization_deletion) THEN
      RAISE EXCEPTION 'Retain organization erasure decisions'; END IF; END $$;
    DO $$ DECLARE tab record; BEGIN
      FOR tab IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r' LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS a_organization_lifecycle ON public.%I',tab.relname);
      END LOOP;
    END $$;
    DROP FUNCTION public.organization_lifecycle_guard();
    DROP FUNCTION public.organization_deletion_preview(uuid);
    DROP FUNCTION public.organization_retention_plan(uuid);
    DROP FUNCTION IF EXISTS public.organization_retention_catalog();
    DROP FUNCTION public.organization_import_admission(uuid,jsonb);
    DROP TABLE public.organization_deletion;
    ''')


def _lifecycle():
    op.execute(r'''
    -- Derive table coverage, keys and delete protection from the live schema.
    -- Unknown DELETE triggers are conservatively retained, never bypassed.
    -- The exceptions below are reviewed locks/access checks, not immutable
    -- evidence guards. Row-conditional GoBD guards keep their exact predicates.
    CREATE FUNCTION public.organization_retention_catalog()
      RETURNS TABLE(entity text,pk text[],disposition text,retain_when text)
      LANGUAGE sql STABLE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      WITH catalog AS (
        SELECT c.relname::text entity, keys.pk,
          -- Live SEPA consent/configuration has no runtime DELETE action,
          -- but that alone is not a voucher binding (ADR-0012). Any actual
          -- immutable DELETE trigger/policy still protects it below.
          (c.relname NOT IN ('sepa_creditor','sepa_mandate') AND (
          NOT has_table_privilege('app_rw',c.oid,'DELETE')
          OR (c.relrowsecurity AND NOT EXISTS(SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid
            AND p.polcmd IN ('d','*') AND p.polpermissive AND pg_get_expr(p.polqual,p.polrelid)<>'false'))))
          OR EXISTS(SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid AND p.polcmd IN ('d','*')
            AND NOT p.polpermissive AND pg_get_expr(p.polqual,p.polrelid)='false')
          OR EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_proc f ON f.oid=t.tgfoid
            WHERE t.tgrelid=c.oid AND NOT t.tgisinternal AND t.tgenabled<>'D' AND (t.tgtype & 8)>0
            AND f.proname NOT IN (
              'organization_lifecycle_guard','lock_scope_principal_change','lock_scope_unit_history',
              'lock_financial_input_sources','lock_strict_property_source_binding',
              'lock_property_source_security_change','lock_knowledge_budget_policy',
              'lock_invoice_sources','lock_mapping_meter','task_acl_lock','sepa_input_lock','t55_input_lock',
              'recheck_transaction_computations','guard_declaration_worker_effect','guard_declaration_issue_review',
              'authorize_bound_financial_write','authorize_bound_payment_write','authorize_dunning_write',
              'guard_financial_source_input','guard_strict_financial_resource',
              'guard_property_source_security','guard_knowledge_budget_policy',
              'trg_cost_entry_immutability','protect_adopted_cost','trg_payment_immutability',
              'trg_settlement_run_immutability','trg_settlement_line_immutability',
              'trg_economic_plan_immutability','trg_economic_plan_line_immutability',
              'trg_dunning_notice_immutability','protect_dunning_quarantine')) protected
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN LATERAL (SELECT array_agg(a.attname::text ORDER BY k.pos) pk
          FROM pg_constraint p CROSS JOIN LATERAL unnest(p.conkey) WITH ORDINALITY k(num,pos)
          JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.num
          WHERE p.conrelid=c.oid AND p.contype='p') keys ON true
        WHERE n.nspname='public' AND c.relkind='r' AND EXISTS(SELECT 1 FROM pg_attribute a
          WHERE a.attrelid=c.oid AND a.attname='organization_id' AND NOT a.attisdropped)
      ), classified AS (
        SELECT *, CASE
          WHEN pk IS NULL THEN 'unclassified'
          WHEN entity='organization_deletion' THEN 'lifecycle'
          WHEN entity IN ('user','membership','person') THEN 'anonymize'
          WHEN entity='password_reset_request' THEN 'delete'
          WHEN protected THEN 'retain'
          WHEN entity IN ('cost_entry','payment','settlement_run','settlement_line',
            'economic_plan','economic_plan_line','dunning_notice') THEN 'conditional'
          ELSE 'delete' END disposition FROM catalog
      ) SELECT entity,pk,disposition,CASE
        WHEN disposition='retain' THEN 'true'
        WHEN disposition<>'conditional' THEN 'false'
        WHEN entity='cost_entry' THEN $sql$t.status='confirmed' OR EXISTS(SELECT 1 FROM public.document_cost_link l JOIN public.allocation_set s ON s.id=l.allocation_set_id AND s.organization_id=l.organization_id WHERE l.organization_id=t.organization_id AND l.cost_entry_id=t.id AND s.kind='intake_reviewed')$sql$
        WHEN entity='payment' THEN $sql$t.status='booked'$sql$
        WHEN entity IN ('settlement_run','economic_plan') THEN $sql$t.status='finalized'$sql$
        WHEN entity='dunning_notice' THEN $sql$t.status='issued' OR EXISTS(SELECT 1 FROM public.dunning_quarantine_disposition q WHERE q.organization_id=t.organization_id AND q.notice_id=t.id)$sql$
        WHEN entity='settlement_line' THEN $sql$EXISTS(SELECT 1 FROM public.settlement_run r WHERE r.organization_id=t.organization_id AND r.id=t.settlement_run_id AND r.status='finalized')$sql$
        WHEN entity='economic_plan_line' THEN $sql$EXISTS(SELECT 1 FROM public.economic_plan r WHERE r.organization_id=t.organization_id AND r.id=t.economic_plan_id AND r.status='finalized')$sql$
        END FROM classified ORDER BY entity;
    $$;
    REVOKE ALL ON FUNCTION public.organization_retention_catalog() FROM PUBLIC,app_rw,app_worker,app_review;

    -- Returns only keys/deadlines to maintenance. Runtime receives aggregates
    -- through the member-checked preview wrapper, never hidden source values.
    CREATE FUNCTION public.organization_retention_plan(org uuid)
      RETURNS TABLE(entity text,identity jsonb,retention_until date,legal_basis text)
      LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    #variable_conflict use_column
    DECLARE tab record; fk record; key_sql text; parent_key text; join_sql text;
      deadline text; altered integer; changes integer;
    BEGIN
      IF EXISTS(SELECT 1 FROM public.organization_retention_catalog() WHERE disposition='unclassified') THEN
        RAISE EXCEPTION 'Unclassified organization retention table' USING ERRCODE='23514'; END IF;
      DROP TABLE IF EXISTS pg_temp.organization_retention_work;
      CREATE TEMP TABLE organization_retention_work(
        entity text NOT NULL,identity jsonb NOT NULL,retention_until date NOT NULL,legal_basis text NOT NULL,
        person_until date, PRIMARY KEY(entity,identity)) ON COMMIT DROP;
      FOR tab IN SELECT entity relname,pk,retain_when FROM public.organization_retention_catalog()
        WHERE disposition IN ('retain','conditional') LOOP
        SELECT string_agg(format('%L,t.%I',col,col),',') INTO key_sql FROM unnest(tab.pk) col;
        key_sql:='jsonb_build_object('||key_sql||')';
        deadline:=$sql$COALESCE(NULLIF(to_jsonb(t)->>'retention_until','')::date,
          make_date(extract(year FROM COALESCE(NULLIF(to_jsonb(t)->>'period_end','')::date,
            GREATEST(NULLIF(to_jsonb(t)->>'invoice_date','')::date,NULLIF(to_jsonb(t)->>'service_period_to','')::date),NULLIF(to_jsonb(t)->>'issue_date','')::date,
            NULLIF(to_jsonb(t)->>'value_date','')::date,NULLIF(to_jsonb(t)->>'created_at','')::timestamptz::date,
            NULLIF(to_jsonb(t)->>'registered_at','')::timestamptz::date,CURRENT_DATE))::integer+8,12,31))$sql$;
        EXECUTE format('INSERT INTO pg_temp.organization_retention_work SELECT %L,%s,%s,%L,%s
          FROM public.%I t WHERE t.organization_id=$1 AND (%s)',tab.relname,key_sql,deadline,
          'GoBD / ADR-0003 / ADR-0012: voucher, original or delete-protected evidence',
          CASE WHEN tab.relname IN ('cost_entry','payment','settlement_run','settlement_line',
            'economic_plan','economic_plan_line','dunning_notice','invoice_document','letter_document')
            THEN deadline ELSE 'NULL::date' END,tab.relname,tab.retain_when) USING org;
      END LOOP;
      -- Legacy WEG/plan rendering resolves every historical owner of the
      -- property through ownership_history, a semantic edge without a run FK.
      INSERT INTO pg_temp.organization_retention_work AS existing
        SELECT 'ownership_history',jsonb_build_object('id',h.id),max(w.retention_until),
          'ADR-0012: permanent legacy owner-name fallback',max(w.person_until)
        FROM public.ownership_history h JOIN public.unit u ON u.id=h.unit_id AND u.organization_id=h.organization_id
        JOIN (SELECT organization_id,id,property_id FROM public.settlement_run WHERE status='finalized' AND run_type='WEG_JAHRESABRECHNUNG'
          UNION ALL SELECT organization_id,id,property_id FROM public.economic_plan WHERE status='finalized') r
          ON r.organization_id=u.organization_id AND r.property_id=u.property_id
        JOIN pg_temp.organization_retention_work w ON w.entity IN ('settlement_run','economic_plan')
          AND w.identity=jsonb_build_object('id',r.id)
        WHERE h.organization_id=org GROUP BY h.id
        ON CONFLICT(entity,identity) DO UPDATE SET retention_until=GREATEST(existing.retention_until,EXCLUDED.retention_until),
          person_until=GREATEST(existing.person_until,EXCLUDED.person_until);
      -- Retain required ancestors (including live fallback display names), with
      -- the latest dependent deadline. No immutable record is rewritten.
      LOOP
        changes:=0;
        FOR fk IN SELECT child.relname child,parent.relname parent,
          array_agg(ca.attname ORDER BY k.pos) child_cols,array_agg(pa.attname ORDER BY k.pos) parent_cols
          FROM pg_constraint f JOIN pg_class child ON child.oid=f.conrelid
          JOIN pg_class parent ON parent.oid=f.confrelid JOIN pg_namespace ns ON ns.oid=child.relnamespace
          CROSS JOIN LATERAL unnest(f.conkey,f.confkey) WITH ORDINALITY k(cnum,pnum,pos)
          JOIN pg_attribute ca ON ca.attrelid=child.oid AND ca.attnum=k.cnum
          JOIN pg_attribute pa ON pa.attrelid=parent.oid AND pa.attnum=k.pnum
          WHERE f.contype='f' AND ns.nspname='public' AND parent.relname<>'organization'
          AND EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=parent.oid AND a.attname='organization_id')
          GROUP BY f.oid,child.relname,parent.relname LOOP
          SELECT 'jsonb_build_object('||string_agg(format('%L,p.%I',a.attname,a.attname),',' ORDER BY k.pos)||')'
            INTO parent_key FROM pg_constraint c CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY k(num,pos)
            JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.num
            WHERE c.conrelid=format('public.%I',fk.parent)::regclass AND c.contype='p';
          SELECT string_agg(format('c.%I=p.%I',fk.child_cols[i],fk.parent_cols[i]),' AND ') INTO join_sql
            FROM generate_subscripts(fk.child_cols,1) i;
          EXECUTE format('INSERT INTO pg_temp.organization_retention_work AS existing
            SELECT %L,%s,max(CASE WHEN %L=''person'' THEN w.person_until ELSE w.retention_until END),%L,
              max(w.person_until) FROM public.%I c
            JOIN pg_temp.organization_retention_work w ON w.entity=%L AND to_jsonb(c) @> w.identity
            JOIN public.%I p ON %s WHERE c.organization_id=$1
              AND (%L<>''person'' OR w.person_until IS NOT NULL) GROUP BY %s
            ON CONFLICT(entity,identity) DO UPDATE SET
              retention_until=GREATEST(existing.retention_until,EXCLUDED.retention_until),
              person_until=GREATEST(existing.person_until,EXCLUDED.person_until)
              WHERE existing.retention_until<EXCLUDED.retention_until
                OR existing.person_until IS DISTINCT FROM GREATEST(existing.person_until,EXCLUDED.person_until)',
            fk.parent,parent_key,fk.parent,'ADR-0012: required dependency of retained voucher or evidence',
            fk.child,fk.child,fk.parent,join_sql,fk.parent,parent_key) USING org;
          GET DIAGNOSTICS altered=ROW_COUNT; changes:=changes+altered;
        END LOOP;
        EXIT WHEN changes=0;
      END LOOP;
      RETURN QUERY SELECT w.entity,w.identity,w.retention_until,w.legal_basis
        FROM pg_temp.organization_retention_work w ORDER BY w.entity,w.identity;
    END $$;
    REVOKE ALL ON FUNCTION public.organization_retention_plan(uuid) FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION public.organization_deletion_preview(org uuid) RETURNS jsonb
      LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE result jsonb; people bigint; bound bigint;
    BEGIN
      IF session_user<>'app_rw' OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR public.scope_actor_role(org) IS DISTINCT FROM 'OWNER_ADMIN' THEN
        RAISE EXCEPTION 'Current administrator required' USING ERRCODE='42501'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||org::text,0));
      IF public.scope_actor_role(org) IS DISTINCT FROM 'OWNER_ADMIN' THEN
        RAISE EXCEPTION 'Current administrator required' USING ERRCODE='42501'; END IF;
      WITH plan AS MATERIALIZED (SELECT * FROM public.organization_retention_plan(org)),
        grouped AS (SELECT entity,count(*) count,max(retention_until) until FROM plan GROUP BY entity)
      SELECT jsonb_build_object('retained',COALESCE(jsonb_object_agg(entity,jsonb_build_object(
        'count',count,'until',until,'basis','GoBD / ADR-0012; required dependencies')),'{}'::jsonb))
        INTO result FROM grouped;
      SELECT count(*) INTO people FROM public.person WHERE organization_id=org;
      SELECT count(*) INTO bound FROM pg_temp.organization_retention_work WHERE entity='person';
      RETURN result||jsonb_build_object('erased',jsonb_build_object('person',people-bound),
        'erased_fields',jsonb_build_array('Ungebundene Namen, Anschriften, E-Mail, Telefon und Bankdaten',
          'Anmeldedaten und Nutzerkontakte','Nicht aufbewahrungspflichtige Entwürfe und Freitexte','Nicht gebundene Dateien und Exporte'));
    END $$;
    REVOKE ALL ON FUNCTION public.organization_deletion_preview(uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.organization_deletion_preview(uuid) TO app_rw;

    CREATE FUNCTION public.organization_lifecycle_guard() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE org uuid;
    BEGIN
      org:=COALESCE(to_jsonb(NEW),to_jsonb(OLD))->>CASE WHEN TG_TABLE_NAME='organization' THEN 'id' ELSE 'organization_id' END;
      IF session_user<>'app_migrator' THEN
        -- Plain statement-visible lookup. Never wait on another connection's
        -- ACL/lifecycle lock; review attestations use a second connection.
        IF EXISTS(SELECT 1 FROM public.organization_deletion d WHERE d.organization_id=org
          AND d.status IN ('cooling_off','deleted_pending_retention')) THEN
          RAISE EXCEPTION 'Organization is read-only during erasure' USING ERRCODE='P0023'; END IF;
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END $$;
    REVOKE ALL ON FUNCTION public.organization_lifecycle_guard() FROM PUBLIC,app_rw,app_worker,app_review;
    DO $$ DECLARE tab record; BEGIN
      FOR tab IN SELECT c.relname FROM pg_class c JOIN pg_namespace ns ON ns.oid=c.relnamespace
        WHERE ns.nspname='public' AND c.relkind='r' AND (c.relname='organization' OR EXISTS(
          SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='organization_id'))
        AND c.relname NOT IN ('organization_deletion','audit_event','password_reset_request') LOOP
        EXECUTE format('CREATE TRIGGER a_organization_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON public.%I
          FOR EACH ROW EXECUTE FUNCTION public.organization_lifecycle_guard()',tab.relname);
      END LOOP;
    END $$;
    ''')


def _registration_adapter(install):
    # Preserve the existing validated source-registration trigger. Only the
    # identity is supplied during import; the registration clock stays native;
    # source kind/content/size still come from the just-inserted native original.
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('public.register_original_document()'::regprocedure)"))
    before_columns = 'public.document_ref(organization_id,'
    after_columns = 'public.document_ref(id,organization_id,'
    before_values = 'VALUES(NEW.organization_id,'
    after_values = """VALUES(
          COALESCE((NULLIF(current_setting('app.organization_import_registrations',true),'')::jsonb
            ->TG_TABLE_NAME->NEW.id::text->>'id')::uuid,gen_random_uuid()),
          NEW.organization_id,"""
    for old, new in [(before_columns, after_columns), (before_values, after_values)]:
        source, target = (old, new) if install else (new, old)
        assert definition.count(source) == 1
        definition = definition.replace(source, target)
    op.execute(definition)


def _erasure_authorization(install):
    # SECURITY DEFINER sets current_user to the function owner even for app_rw.
    # Require the actual login too. Only DELETE capability checks, or a reason-
    # only UPDATE of mutable policy configuration, receive an exemption.
    # Reversing this exact insertion restores the previous bodies verbatim.
    checks = {
        'authorize_bound_financial_write': "TG_OP='DELETE'",
        'authorize_dunning_write': "TG_OP='DELETE'",
        'guard_financial_source_input': "TG_OP='DELETE'",
        'guard_strict_financial_resource': "TG_OP='DELETE'",
        'guard_knowledge_budget_policy': "TG_OP='UPDATE' AND (to_jsonb(NEW)-'reason')=(to_jsonb(OLD)-'reason')",
        'guard_property_source_security': "TG_OP='UPDATE' AND (to_jsonb(NEW)-'reason')=(to_jsonb(OLD)-'reason')",
    }
    for function, operation in checks.items():
        definition = op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:name AS regprocedure))'),
                                          {'name': f'public.{function}()'})
        anchor = '    BEGIN\n'
        exemption = ("      IF current_user='app_migrator' AND session_user='app_migrator'\n"
                     "        AND current_setting('app.organization_erasure',true)='on'\n"
                     f"        AND {operation} THEN\n"
                     "        IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;\n"
                     "      END IF;\n")
        before, after = anchor, anchor + exemption
        # This branch's pre-release 0147 had no erasure exemption yet.
        if not install and exemption not in definition:
            continue
        source, target = (before, after) if install else (after, before)
        assert definition.count(source) == 1, function
        op.execute(definition.replace(source, target, 1))


def _erasure_payloads(install):
    # One payload-column contract for the operator engine and all guards.
    # audit_event previously had only revoked runtime grants, no trigger body.
    if install:
        op.execute('''
        CREATE FUNCTION public.organization_erasure_payload_columns(tab text) RETURNS text[]
          LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
          SELECT columns FROM (VALUES
            ('audit_event',ARRAY['payload_diff']),
            ('declaration_review_receipt',ARRAY['response']),
            ('dunning_batch',ARRAY['result']),
            ('exception_case_event',ARRAY['reason']),
            ('financial_computation_receipt',ARRAY['state']),
            ('intake_batch',ARRAY['manifest']),
            ('intake_extraction_attempt',ARRAY['result']),
            ('intake_retry_receipt',ARRAY['reason']),
            ('job_attempt',ARRAY['result']),
            ('master_import_batch',ARRAY['result']),
            ('outbox_delivery_attempt',ARRAY['result']),
            ('scope_control_event',ARRAY['reason']),
            ('visual_disclosure_event',ARRAY['reason'])
          ) allowed(entity,columns) WHERE entity=tab;
        $$;
        REVOKE ALL ON FUNCTION public.organization_erasure_payload_columns(text)
          FROM PUBLIC,app_rw,app_worker,app_review;
        CREATE OR REPLACE FUNCTION public.guard_audit_event_payload() RETURNS trigger
          LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      RAISE EXCEPTION 'Audit events are immutable' USING ERRCODE='23514';
    END $$;
        REVOKE ALL ON FUNCTION public.guard_audit_event_payload() FROM PUBLIC,app_rw,app_worker,app_review;
        CREATE TRIGGER audit_event_immutable BEFORE UPDATE OR DELETE ON public.audit_event
          FOR EACH ROW EXECUTE FUNCTION public.guard_audit_event_payload();
        ''')
    exemption = """      IF current_user='app_migrator' AND session_user='app_migrator'
        AND current_setting('app.organization_erasure',true)='on'
        AND TG_TABLE_SCHEMA='public' AND TG_OP='UPDATE' THEN
        IF public.organization_erasure_payload_columns(TG_TABLE_NAME) IS NOT NULL
          AND (to_jsonb(NEW)-public.organization_erasure_payload_columns(TG_TABLE_NAME))
            =(to_jsonb(OLD)-public.organization_erasure_payload_columns(TG_TABLE_NAME))
          AND OLD.organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
          AND EXISTS(SELECT 1 FROM public.organization_deletion d
            WHERE d.organization_id=OLD.organization_id AND d.status='cooling_off') THEN
          RETURN NEW;
        END IF;
      END IF;
"""
    for function in ('guard_audit_event_payload', 'protect_scope_evidence',
                     'protect_master_import_batch', 'protect_dunning_batch'):
        definition = op.get_bind().scalar(text('SELECT pg_get_functiondef(to_regprocedure(:name))'),
                                          {'name': f'public.{function}()'})
        if not install and (definition is None or exemption not in definition):
            continue  # Also admit this branch's pre-amendment 0147 in test resets.
        before, after = '    BEGIN\n', '    BEGIN\n' + exemption
        source, target = (before, after) if install else (after, before)
        assert definition.count(source) == 1, function
        op.execute(definition.replace(source, target, 1))
    if not install:
        op.execute('''DROP TRIGGER IF EXISTS audit_event_immutable ON public.audit_event;
          DROP FUNCTION IF EXISTS public.guard_audit_event_payload();
          DROP FUNCTION IF EXISTS public.organization_erasure_payload_columns(text);''')


def _auth_filter(install):
    for signature, clause, org in [
        ('app_auth_lookup_versioned(text)', 'u.organization_id=p.organization_id', 'p.organization_id'),
        ('app_validate_session_versioned(uuid,uuid,integer)', 'u.session_version=session_generation', 'session_organization_id'),
    ]:
        definition = op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:name AS regprocedure))'), {'name': signature})
        filtered = clause + " AND NOT EXISTS(SELECT 1 FROM public.organization_deletion d WHERE d.organization_id=" + org + " AND d.status='deleted_pending_retention')"
        source, target = (clause, filtered) if install else (filtered, clause)
        assert definition.count(source) == 1
        op.execute(definition.replace(source, target))
