"""Prove and atomically classify frozen legacy payments from their reviewed bank."""
from alembic import op

revision = '0040'
down_revision = '0039'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    ALTER TABLE accounting_scope_binding ADD CONSTRAINT uq_reconciliation_binding_org_id UNIQUE(organization_id,id);
    CREATE TABLE scope_payment_reconciliation (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL,
      scope_id uuid NOT NULL, actor_user_id uuid NOT NULL, request_key uuid NOT NULL,
      request_digest text NOT NULL CHECK(length(request_digest)=64), reason text NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 2000),
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(organization_id,id), UNIQUE(organization_id,actor_user_id,request_key),
      FOREIGN KEY(organization_id,scope_id) REFERENCES accounting_scope(organization_id,id),
      FOREIGN KEY(organization_id,actor_user_id) REFERENCES public."user"(organization_id,id)
    );
    CREATE TABLE scope_payment_reconciliation_item (
      organization_id uuid NOT NULL, reconciliation_id uuid NOT NULL, binding_id uuid NOT NULL,
      source_digest text NOT NULL CHECK(length(source_digest)=64),
      PRIMARY KEY(organization_id,reconciliation_id,binding_id),
      FOREIGN KEY(organization_id,reconciliation_id) REFERENCES scope_payment_reconciliation(organization_id,id),
      FOREIGN KEY(organization_id,binding_id) REFERENCES accounting_scope_binding(organization_id,id)
    );
    ALTER TABLE scope_payment_reconciliation ENABLE ROW LEVEL SECURITY;
    ALTER TABLE scope_payment_reconciliation FORCE ROW LEVEL SECURITY;
    ALTER TABLE scope_payment_reconciliation_item ENABLE ROW LEVEL SECURITY;
    ALTER TABLE scope_payment_reconciliation_item FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON scope_payment_reconciliation,scope_payment_reconciliation_item FROM app_rw;
    GRANT SELECT ON scope_payment_reconciliation,scope_payment_reconciliation_item TO app_rw;
    CREATE TRIGGER immutable_reconciliation BEFORE UPDATE OR DELETE ON scope_payment_reconciliation FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();
    CREATE TRIGGER immutable_reconciliation BEFORE UPDATE OR DELETE ON scope_payment_reconciliation_item FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();
    CREATE POLICY reconciliation_no_direct_insert ON scope_payment_reconciliation FOR INSERT WITH CHECK(false);
    CREATE POLICY reconciliation_item_no_direct_insert ON scope_payment_reconciliation_item FOR INSERT WITH CHECK(false);
    CREATE POLICY reconciliation_read ON scope_payment_reconciliation FOR SELECT USING(
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND actor_user_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid
      AND scope_actor_role(organization_id)='OWNER_ADMIN' AND has_scope_capability(organization_id,scope_id,'prepare')
      AND (NULLIF(current_setting('app.current_scope_id',true),'') IS NULL OR scope_id=NULLIF(current_setting('app.current_scope_id',true),'')::uuid));
    CREATE POLICY reconciliation_item_read ON scope_payment_reconciliation_item FOR SELECT USING(EXISTS(
      SELECT 1 FROM scope_payment_reconciliation r WHERE r.organization_id=scope_payment_reconciliation_item.organization_id AND r.id=scope_payment_reconciliation_item.reconciliation_id));
    CREATE POLICY reconciliation_audit ON audit_event AS RESTRICTIVE FOR SELECT USING(entity_type<>'scope_payment_reconciliation' OR EXISTS(
      SELECT 1 FROM scope_payment_reconciliation r WHERE r.organization_id=audit_event.organization_id AND r.id=audit_event.entity_id));

    CREATE FUNCTION authorize_payment_reconciliation(org uuid,sphere uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Reconciliation context not accessible' USING ERRCODE='42501'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      IF public.scope_actor_role(org) IS DISTINCT FROM 'OWNER_ADMIN'
        OR NOT public.has_scope_capability(org,sphere,'prepare')
        OR (NULLIF(current_setting('app.current_scope_id',true),'') IS NOT NULL
          AND sphere IS DISTINCT FROM NULLIF(current_setting('app.current_scope_id',true),'')::uuid) THEN
        RAISE EXCEPTION 'Reconciliation context not accessible' USING ERRCODE='42501'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      -- Time can expire while waiting even though ACL writers are serialized.
      IF NOT public.has_scope_capability(org,sphere,'prepare') THEN
        RAISE EXCEPTION 'Reconciliation context not accessible' USING ERRCODE='42501'; END IF;
    END $$;

    CREATE FUNCTION legacy_payment_proof(org uuid,sphere_id uuid,payment_id uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE p public.payment; bank_binding public.accounting_scope_binding; sphere public.accounting_scope;
      candidate public.accounting_scope_binding; source_hash text; blocker text;
    BEGIN
      PERFORM public.authorize_payment_reconciliation(org,sphere_id);
      SELECT * INTO p FROM public.payment WHERE organization_id=org AND id=payment_id FOR SHARE;
      SELECT * INTO bank_binding FROM public.accounting_scope_binding WHERE organization_id=org
        AND bank_account_id=p.bank_account_id AND scope_id=sphere_id;
      IF p.id IS NULL OR bank_binding.id IS NULL THEN
        RETURN jsonb_build_object('proof_status','review_required','blocker','not_available','source_digest',NULL); END IF;
      SELECT * INTO sphere FROM public.accounting_scope WHERE organization_id=org AND id=sphere_id;
      source_hash:=encode(sha256(convert_to(jsonb_build_object('payment',to_jsonb(p),'bank_binding',to_jsonb(bank_binding),'scope',to_jsonb(sphere))::text,'UTF8')),'hex');
      IF NOT sphere.whole_property OR sphere.scope_type NOT IN ('WEG','MIET') THEN blocker:='scope_units_not_supported';
      ELSIF p.status<>'booked' THEN blocker:='payment_not_booked';
      ELSIF EXISTS(SELECT 1 FROM public.accounting_scope_binding b WHERE b.organization_id=org AND b.payment_id=p.id) THEN blocker:='already_classified';
      ELSIF p.settlement_run_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.accounting_scope_binding b WHERE b.organization_id=org AND b.settlement_run_id=p.settlement_run_id AND b.scope_id=sphere_id) THEN blocker:='unproven_settlement_source';
      ELSIF (p.reverses_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.payment original WHERE original.organization_id=org AND original.id=p.reverses_id AND original.bank_account_id=p.bank_account_id))
        OR (p.reversed_by_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.payment correction WHERE correction.organization_id=org AND correction.id=p.reversed_by_id AND correction.bank_account_id=p.bank_account_id)) THEN blocker:='conflicting_correction_account';
      ELSE
        candidate.organization_id:=org; candidate.property_id:=p.property_id; candidate.scope_id:=sphere_id;
        candidate.payment_id:=p.id; candidate.payment_bank_account_id:=p.bank_account_id;
        BEGIN
          PERFORM public.validate_accounting_binding(candidate);
        EXCEPTION WHEN check_violation OR foreign_key_violation THEN blocker:='binding_semantics_conflict'; END;
      END IF;
      RETURN jsonb_build_object('proof_status',CASE WHEN blocker IS NULL THEN 'proven' ELSE 'review_required' END,
        'blocker',blocker,'source_digest',source_hash);
    END $$;

    CREATE FUNCTION reconcile_legacy_payments(org uuid,sphere uuid,request_key uuid,items jsonb,reason text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE actor uuid; canonical jsonb; request_hash text; receipt public.scope_payment_reconciliation;
      item jsonb; proof jsonb; payment public.payment; binding_uuid uuid; result jsonb;
    BEGIN
      PERFORM public.authorize_payment_reconciliation(org,sphere);
      actor:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      IF request_key IS NULL OR jsonb_typeof(items) IS DISTINCT FROM 'array' OR jsonb_array_length(items) NOT BETWEEN 1 AND 200
        OR reason IS NULL OR length(trim(reason)) NOT BETWEEN 1 AND 2000 THEN
        RAISE EXCEPTION 'Invalid reconciliation command' USING ERRCODE='22023'; END IF;
      IF EXISTS(SELECT 1 FROM jsonb_array_elements(items) i WHERE jsonb_typeof(i) IS DISTINCT FROM 'object'
          OR i->>'payment_id' IS NULL OR COALESCE(i->>'source_digest','') !~ '^[0-9a-f]{64}$'
          OR i - ARRAY['payment_id','source_digest'] <> '{}'::jsonb)
        OR (SELECT count(DISTINCT (i->>'payment_id')::uuid) FROM jsonb_array_elements(items) i) <> jsonb_array_length(items) THEN
        RAISE EXCEPTION 'Invalid reconciliation items' USING ERRCODE='22023'; END IF;
      SELECT jsonb_agg(jsonb_build_object('payment_id',(i->>'payment_id')::uuid,'source_digest',i->>'source_digest') ORDER BY (i->>'payment_id')::uuid)
        INTO canonical FROM jsonb_array_elements(items) i;
      request_hash:=encode(sha256(convert_to(jsonb_build_object('scope',sphere,'items',canonical,'reason',trim(reason))::text,'UTF8')),'hex');
      SELECT * INTO receipt FROM public.scope_payment_reconciliation r
        WHERE r.organization_id=org AND r.actor_user_id=actor AND r.request_key=reconcile_legacy_payments.request_key FOR UPDATE;
      IF receipt.id IS NOT NULL THEN
        IF receipt.request_digest<>request_hash OR receipt.scope_id<>sphere THEN
          RAISE EXCEPTION 'Reconciliation key already used for a different command' USING ERRCODE='P0001'; END IF;
      ELSE
        -- ACL and financial locks precede receipt and ordered source row locks.
        PERFORM p.id FROM public.payment p WHERE p.organization_id=org AND p.id IN (
          SELECT (i->>'payment_id')::uuid FROM jsonb_array_elements(canonical) i) ORDER BY p.id FOR UPDATE;
        FOR item IN SELECT value FROM jsonb_array_elements(canonical) LOOP
          proof:=public.legacy_payment_proof(org,sphere,(item->>'payment_id')::uuid);
          IF proof->>'proof_status'<>'proven' OR proof->>'source_digest' IS DISTINCT FROM item->>'source_digest' THEN
            RAISE EXCEPTION 'Payment evidence changed or requires review; reload the complete preview' USING ERRCODE='P0001'; END IF;
        END LOOP;
        INSERT INTO public.scope_payment_reconciliation(organization_id,scope_id,actor_user_id,request_key,request_digest,reason)
          VALUES(org,sphere,actor,request_key,request_hash,trim(reason)) RETURNING * INTO receipt;
        FOR item IN SELECT value FROM jsonb_array_elements(canonical) LOOP
          SELECT * INTO payment FROM public.payment p WHERE p.organization_id=org AND p.id=(item->>'payment_id')::uuid;
          INSERT INTO public.accounting_scope_binding(organization_id,property_id,scope_id,payment_id,payment_bank_account_id,origin,reason,reviewed_by)
            VALUES(org,payment.property_id,sphere,payment.id,payment.bank_account_id,'PROVEN_LEGACY',trim(reason),actor) RETURNING id INTO binding_uuid;
          INSERT INTO public.scope_payment_reconciliation_item(organization_id,reconciliation_id,binding_id,source_digest)
            VALUES(org,receipt.id,binding_uuid,item->>'source_digest');
        END LOOP;
        SET CONSTRAINTS public.validate_binding_semantics IMMEDIATE;
        SET CONSTRAINTS public.validate_binding_semantics DEFERRED;
        INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
          VALUES(org,actor,'scope_payment_reconciliation',receipt.id,'scope.legacy_payments_reconciled',
            jsonb_build_object('scope_id',sphere,'count',jsonb_array_length(canonical),'request_digest',request_hash));
      END IF;
      SELECT jsonb_build_object('id',receipt.id,'scope_id',receipt.scope_id,'reason',receipt.reason,'items',jsonb_agg(
        jsonb_build_object('payment_id',b.payment_id,'binding_id',b.id,'source_digest',ri.source_digest) ORDER BY b.payment_id)) INTO result
        FROM public.scope_payment_reconciliation_item ri JOIN public.accounting_scope_binding b ON b.organization_id=ri.organization_id AND b.id=ri.binding_id
        WHERE ri.organization_id=org AND ri.reconciliation_id=receipt.id;
      RETURN result;
    END $$;
    REVOKE ALL ON FUNCTION authorize_payment_reconciliation(uuid,uuid),legacy_payment_proof(uuid,uuid,uuid),reconcile_legacy_payments(uuid,uuid,uuid,jsonb,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION legacy_payment_proof(uuid,uuid,uuid),reconcile_legacy_payments(uuid,uuid,uuid,jsonb,text) TO app_rw;
    ''')


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM scope_payment_reconciliation) THEN
      RAISE EXCEPTION 'Cannot downgrade while payment reconciliation evidence exists'; END IF; END $$""")
    op.execute('''
    DROP POLICY reconciliation_audit ON audit_event;
    DROP FUNCTION reconcile_legacy_payments(uuid,uuid,uuid,jsonb,text),legacy_payment_proof(uuid,uuid,uuid),authorize_payment_reconciliation(uuid,uuid);
    DROP TABLE scope_payment_reconciliation_item,scope_payment_reconciliation;
    ALTER TABLE accounting_scope_binding DROP CONSTRAINT uq_reconciliation_binding_org_id;
    ''')
