"""Immutable dunning provenance and exact lifecycle, export and retention access."""
from alembic import op

revision = '0039'
down_revision = '0038'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    ALTER TABLE dunning_notice ADD CONSTRAINT uq_dunning_org_id UNIQUE(organization_id,id);
    ALTER TABLE lease_agreement ADD CONSTRAINT uq_dunning_lease_org_id UNIQUE(organization_id,id);
    ALTER TABLE dunning_notice ADD COLUMN cancelled_at timestamptz;
    ALTER TABLE dunning_notice ADD CONSTRAINT ck_dunning_cancelled_draft CHECK(cancelled_at IS NULL OR status='draft');
    CREATE TABLE dunning_scope_context (
      organization_id uuid NOT NULL, dunning_notice_id uuid NOT NULL, scope_id uuid NOT NULL,
      lease_agreement_id uuid, economic_plan_id uuid, period_start date NOT NULL, period_end date NOT NULL,
      origin text NOT NULL DEFAULT 'PREPARED' CHECK(origin='PREPARED'),
      created_by uuid NOT NULL, reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(organization_id,dunning_notice_id),
      FOREIGN KEY(organization_id,dunning_notice_id) REFERENCES dunning_notice(organization_id,id) DEFERRABLE INITIALLY DEFERRED,
      FOREIGN KEY(organization_id,scope_id) REFERENCES accounting_scope(organization_id,id),
      FOREIGN KEY(organization_id,lease_agreement_id) REFERENCES lease_agreement(organization_id,id),
      FOREIGN KEY(organization_id,economic_plan_id) REFERENCES economic_plan(organization_id,id),
      FOREIGN KEY(organization_id,created_by) REFERENCES public."user"(organization_id,id),
      CHECK(num_nonnulls(lease_agreement_id,economic_plan_id)=1),
      CHECK(period_start<=period_end AND length(trim(reason))>0)
    );
    CREATE INDEX ix_dunning_context_scope ON dunning_scope_context(organization_id,scope_id);
    ALTER TABLE dunning_scope_context ENABLE ROW LEVEL SECURITY;
    ALTER TABLE dunning_scope_context FORCE ROW LEVEL SECURITY;
    GRANT SELECT,INSERT ON dunning_scope_context TO app_rw;
    REVOKE UPDATE,DELETE ON dunning_scope_context FROM app_rw;

    CREATE FUNCTION dunning_legacy_subject_clear(org uuid, tenant uuid, owner uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT org=NULLIF(current_setting('app.current_org_id',true),'')::uuid AND NOT EXISTS(
        SELECT 1 FROM public.accounting_scope_binding b WHERE b.organization_id=org AND (
          EXISTS(SELECT 1 FROM public.lease_agreement l JOIN public.unit u ON u.organization_id=l.organization_id AND u.id=l.unit_id
            WHERE l.organization_id=org AND l.tenant_profile_id=tenant AND u.property_id=b.property_id)
          OR EXISTS(SELECT 1 FROM public.ownership_history h JOIN public.unit u ON u.organization_id=h.organization_id AND u.id=h.unit_id
            WHERE h.organization_id=org AND h.owner_profile_id=owner AND u.property_id=b.property_id)
          OR EXISTS(SELECT 1 FROM public.payment p WHERE p.organization_id=org AND p.property_id=b.property_id
            AND (p.tenant_profile_id=tenant OR p.owner_profile_id=owner))
          OR EXISTS(SELECT 1 FROM public.settlement_run r WHERE r.organization_id=org AND r.property_id=b.property_id AND r.tenant_profile_id=tenant)
          OR EXISTS(SELECT 1 FROM public.economic_plan_line ln JOIN public.economic_plan p ON p.organization_id=ln.organization_id AND p.id=ln.economic_plan_id
            WHERE p.organization_id=org AND p.property_id=b.property_id AND ln.owner_profile_id=owner)))
    $$;
    CREATE FUNCTION dunning_scope_access(org uuid, resource uuid, capability text, tenant uuid DEFAULT NULL, owner uuid DEFAULT NULL) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE sphere uuid; selected uuid; recipient public.dunning_notice;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR capability NOT IN ('read','prepare','approve','export') THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      selected:=NULLIF(current_setting('app.current_scope_id',true),'')::uuid;
      SELECT scope_id INTO sphere FROM public.dunning_scope_context WHERE organization_id=org AND dunning_notice_id=resource;
      IF sphere IS NOT NULL THEN
        RETURN (selected IS NULL OR selected=sphere) AND public.has_scope_capability(org,sphere,capability);
      END IF;
      SELECT * INTO recipient FROM public.dunning_notice WHERE organization_id=org AND id=resource;
      IF NOT FOUND THEN
        -- INSERT ... RETURNING evaluates SELECT policy before the new row is
        -- visible to a lookup. Its row values are a safe fallback only when no
        -- canonical context exists (the branch above already handles that).
        RETURN selected IS NULL AND num_nonnulls(tenant,owner)=1 AND public.dunning_legacy_subject_clear(org,tenant,owner);
      END IF;
      RETURN selected IS NULL AND public.dunning_legacy_subject_clear(org,recipient.tenant_profile_id,recipient.owner_profile_id);
    END $$;
    CREATE FUNCTION dunning_history_complete(org uuid, tenant uuid, owner uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE selected uuid;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      selected:=NULLIF(current_setting('app.current_scope_id',true),'')::uuid;
      IF selected IS NULL THEN RETURN public.dunning_legacy_subject_clear(org,tenant,owner); END IF;
      RETURN public.has_scope_capability(org,selected,'prepare') AND NOT EXISTS(
        SELECT 1 FROM public.dunning_notice n WHERE n.organization_id=org AND n.status='issued'
          AND (n.tenant_profile_id=tenant OR n.owner_profile_id=owner)
          AND NOT EXISTS(SELECT 1 FROM public.dunning_scope_context c WHERE c.organization_id=org AND c.dunning_notice_id=n.id));
    END $$;

    CREATE FUNCTION validate_dunning_context() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE ctx public.dunning_scope_context; notice public.dunning_notice; sphere public.accounting_scope;
      lease public.lease_agreement; plan public.economic_plan; resource uuid; org uuid;
    BEGIN
      org:=NEW.organization_id;
      resource:=(to_jsonb(NEW)->>CASE WHEN TG_TABLE_NAME='dunning_notice' THEN 'id' ELSE 'dunning_notice_id' END)::uuid;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      SELECT * INTO ctx FROM public.dunning_scope_context WHERE organization_id=org AND dunning_notice_id=resource;
      SELECT * INTO notice FROM public.dunning_notice WHERE organization_id=org AND id=resource;
      IF notice.id IS NULL THEN RETURN NEW; END IF; -- deferred FK rejects orphan contexts
      -- The BEFORE guard checks unbound rows using their write-time principal;
      -- deferred validation must not confuse a later transaction-local context.
      IF ctx.scope_id IS NULL THEN RETURN NEW; END IF;
      SELECT * INTO sphere FROM public.accounting_scope WHERE organization_id=org AND id=ctx.scope_id;
      IF ctx.period_start<sphere.valid_from OR (sphere.valid_to IS NOT NULL AND ctx.period_end>sphere.valid_to) THEN
        RAISE EXCEPTION 'Dunning period outside scope' USING ERRCODE='23514';
      END IF;
      IF notice.tenant_profile_id IS NOT NULL THEN
        SELECT * INTO lease FROM public.lease_agreement WHERE organization_id=org AND id=ctx.lease_agreement_id FOR SHARE;
        IF sphere.scope_type<>'MIET' OR lease.id IS NULL OR lease.tenant_profile_id<>notice.tenant_profile_id
          OR lease.valid_from>ctx.period_end OR (lease.valid_to IS NOT NULL AND lease.valid_to<=ctx.period_start)
          OR NOT EXISTS(SELECT 1 FROM public.unit u WHERE u.organization_id=org AND u.id=lease.unit_id AND u.property_id=sphere.property_id
            AND (sphere.whole_property OR EXISTS(SELECT 1 FROM public.accounting_scope_unit su WHERE su.organization_id=org AND su.scope_id=sphere.id AND su.unit_id=u.id))) THEN
          RAISE EXCEPTION 'Dunning lease and counterparty must match scope' USING ERRCODE='23514';
        END IF;
      ELSE
        SELECT * INTO plan FROM public.economic_plan WHERE organization_id=org AND id=ctx.economic_plan_id FOR SHARE;
        IF sphere.scope_type<>'WEG' OR plan.id IS NULL OR plan.status<>'finalized'
          OR ctx.period_start<plan.period_start OR ctx.period_end>plan.period_end
          OR NOT EXISTS(SELECT 1 FROM public.accounting_scope_binding b WHERE b.organization_id=org AND b.economic_plan_id=plan.id AND b.scope_id=sphere.id)
          OR NOT EXISTS(SELECT 1 FROM public.economic_plan_line ln WHERE ln.organization_id=org AND ln.economic_plan_id=plan.id AND ln.owner_profile_id=notice.owner_profile_id AND ln.line_type='OWNER_ADVANCE') THEN
          RAISE EXCEPTION 'Dunning owner requires a finalized plan in the same scope' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION protect_dunning_context() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Dunning scope context is immutable' USING ERRCODE='23514'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||NEW.organization_id::text,0));
      IF NULLIF(current_setting('app.current_org_id',true),'') IS NOT NULL AND (
        NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR NEW.created_by IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid
        OR NEW.scope_id IS DISTINCT FROM NULLIF(current_setting('app.current_scope_id',true),'')::uuid
        OR NOT public.has_scope_capability(NEW.organization_id,NEW.scope_id,'prepare')
        OR EXISTS(SELECT 1 FROM public.dunning_notice n WHERE n.organization_id=NEW.organization_id AND n.id=NEW.dunning_notice_id)) THEN
        RAISE EXCEPTION 'Explicit preparation context required for a new notice' USING ERRCODE='42501';
      END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION authorize_dunning_write() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE original jsonb; replacement jsonb; org uuid; resource uuid; capability text;
    BEGIN
      original:=CASE WHEN TG_OP='INSERT' THEN to_jsonb(NEW) ELSE to_jsonb(OLD) END;
      replacement:=CASE WHEN TG_OP='DELETE' THEN original ELSE to_jsonb(NEW) END;
      org:=(original->>'organization_id')::uuid; resource:=(original->>'id')::uuid;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      IF TG_OP='UPDATE' AND EXISTS(SELECT 1 FROM public.dunning_scope_context c WHERE c.organization_id=org AND c.dunning_notice_id=resource)
        AND (NEW.organization_id IS DISTINCT FROM OLD.organization_id OR NEW.id IS DISTINCT FROM OLD.id
          OR NEW.tenant_profile_id IS DISTINCT FROM OLD.tenant_profile_id OR NEW.owner_profile_id IS DISTINCT FROM OLD.owner_profile_id) THEN
        RAISE EXCEPTION 'Dunning scope and counterparty are immutable' USING ERRCODE='23514';
      END IF;
      IF TG_OP='UPDATE' AND OLD.cancelled_at IS NOT NULL AND (to_jsonb(NEW)-'updated_at')<>(to_jsonb(OLD)-'updated_at') THEN
        RAISE EXCEPTION 'Cancelled dunning notice is immutable' USING ERRCODE='23514';
      END IF;
      IF NULLIF(current_setting('app.current_org_id',true),'') IS NULL THEN
        IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
      END IF;
      IF TG_OP='UPDATE' AND NOT EXISTS(SELECT 1 FROM public.dunning_scope_context c WHERE c.organization_id=org AND c.dunning_notice_id=resource)
        AND NOT public.dunning_legacy_subject_clear(org,NEW.tenant_profile_id,NEW.owner_profile_id) THEN
        RAISE EXCEPTION 'Reclassified dunning recipient requires immutable context' USING ERRCODE='42501';
      END IF;
      capability:=CASE WHEN replacement->>'status'='issued' OR original->>'status'='issued' THEN 'approve' ELSE 'prepare' END;
      IF NOT public.dunning_scope_access(org,resource,capability) AND NOT (TG_OP='INSERT'
        AND NULLIF(current_setting('app.current_scope_id',true),'') IS NULL
        AND public.dunning_legacy_subject_clear(org,(replacement->>'tenant_profile_id')::uuid,(replacement->>'owner_profile_id')::uuid)) THEN
        RAISE EXCEPTION 'Dunning scope capability required' USING ERRCODE='42501';
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END $$;
    CREATE TRIGGER dunning_context_immutable BEFORE INSERT OR UPDATE OR DELETE ON dunning_scope_context FOR EACH ROW EXECUTE FUNCTION protect_dunning_context();
    CREATE CONSTRAINT TRIGGER dunning_context_valid AFTER INSERT ON dunning_scope_context DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_dunning_context();
    CREATE CONSTRAINT TRIGGER dunning_notice_context_valid AFTER INSERT ON dunning_notice DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_dunning_context();
    CREATE TRIGGER dunning_scope_write BEFORE INSERT OR UPDATE OR DELETE ON dunning_notice FOR EACH ROW EXECUTE FUNCTION authorize_dunning_write();
    REVOKE ALL ON FUNCTION dunning_legacy_subject_clear(uuid,uuid,uuid),dunning_scope_access(uuid,uuid,text,uuid,uuid),dunning_history_complete(uuid,uuid,uuid),validate_dunning_context(),protect_dunning_context(),authorize_dunning_write() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dunning_legacy_subject_clear(uuid,uuid,uuid),dunning_scope_access(uuid,uuid,text,uuid,uuid),dunning_history_complete(uuid,uuid,uuid) TO app_rw;
    CREATE POLICY context_read ON dunning_scope_context FOR SELECT USING(organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid AND has_scope_capability(organization_id,scope_id,'read') AND (NULLIF(current_setting('app.current_scope_id',true),'') IS NULL OR scope_id=NULLIF(current_setting('app.current_scope_id',true),'')::uuid));
    CREATE POLICY context_insert ON dunning_scope_context FOR INSERT WITH CHECK(organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid AND has_scope_capability(organization_id,scope_id,'prepare'));
    CREATE POLICY dunning_bound_read ON dunning_notice AS RESTRICTIVE FOR SELECT USING(dunning_scope_access(organization_id,id,'read',tenant_profile_id,owner_profile_id));
    CREATE POLICY dunning_bound_update ON dunning_notice AS RESTRICTIVE FOR UPDATE USING(dunning_scope_access(organization_id,id,'read')) WITH CHECK(dunning_scope_access(organization_id,id,'read'));
    CREATE POLICY dunning_bound_delete ON dunning_notice AS RESTRICTIVE FOR DELETE USING(dunning_scope_access(organization_id,id,'prepare'));
    CREATE POLICY dunning_bound_audit ON audit_event AS RESTRICTIVE FOR SELECT USING(entity_type<>'dunning_notice' OR dunning_scope_access(organization_id,entity_id,'read'));

    ALTER FUNCTION privacy_financial_context_complete(uuid,uuid,text) RENAME TO privacy_context_before_dunning;
    REVOKE ALL ON FUNCTION privacy_context_before_dunning(uuid,uuid,text) FROM app_rw;
    CREATE FUNCTION privacy_financial_context_complete(org uuid, person uuid, capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      RETURN public.privacy_context_before_dunning(org,person,capability) AND NOT EXISTS(
        SELECT 1 FROM public.dunning_notice n WHERE n.organization_id=org AND n.status='issued'
          AND (EXISTS(SELECT 1 FROM public.tenant_profile t WHERE t.organization_id=org AND t.id=n.tenant_profile_id AND t.person_id=person)
            OR EXISTS(SELECT 1 FROM public.owner_profile o WHERE o.organization_id=org AND o.id=n.owner_profile_id AND o.person_id=person))
          AND NOT public.dunning_scope_access(org,n.id,capability));
    END $$;
    REVOKE ALL ON FUNCTION privacy_financial_context_complete(uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION privacy_financial_context_complete(uuid,uuid,text) TO app_rw;
    ''')
    # Preserve the existing function/OID and its dependent audit policy. Extend
    # only canonical lookup; the additional audit policy quarantines unknowns.
    op.execute("""DO $$ DECLARE definition text; BEGIN
      SELECT pg_get_functiondef('financial_scope_id(uuid,text,uuid)'::regprocedure) INTO definition;
      IF array_length(string_to_array(definition, 'IF kind=''invoice_document'' THEN'),1)<>2 THEN
        RAISE EXCEPTION 'Unexpected financial scope function definition'; END IF;
      definition:=replace(definition, 'IF kind=''invoice_document'' THEN',
        'IF kind=''dunning_notice'' THEN SELECT scope_id INTO sphere FROM public.dunning_scope_context WHERE organization_id=org AND dunning_notice_id=resource; RETURN sphere; ELSIF kind=''invoice_document'' THEN');
      EXECUTE definition;
    END $$""")


def downgrade():
    # Rollback must never remove scope protection from persisted financial letters.
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM dunning_scope_context) THEN
      RAISE EXCEPTION 'Cannot downgrade while scoped dunning notices exist'; END IF; END $$""")
    op.execute("""DO $$ DECLARE definition text; BEGIN
      SELECT pg_get_functiondef('financial_scope_id(uuid,text,uuid)'::regprocedure) INTO definition;
      IF array_length(string_to_array(definition, 'IF kind=''dunning_notice'' THEN SELECT scope_id INTO sphere FROM public.dunning_scope_context WHERE organization_id=org AND dunning_notice_id=resource; RETURN sphere; ELSIF kind=''invoice_document'' THEN'),1)<>2 THEN
        RAISE EXCEPTION 'Unexpected dunning scope function definition'; END IF;
      definition:=replace(definition,
        'IF kind=''dunning_notice'' THEN SELECT scope_id INTO sphere FROM public.dunning_scope_context WHERE organization_id=org AND dunning_notice_id=resource; RETURN sphere; ELSIF kind=''invoice_document'' THEN',
        'IF kind=''invoice_document'' THEN'); EXECUTE definition;
    END $$""")
    op.execute('''
    DROP FUNCTION privacy_financial_context_complete(uuid,uuid,text);
    ALTER FUNCTION privacy_context_before_dunning(uuid,uuid,text) RENAME TO privacy_financial_context_complete;
    GRANT EXECUTE ON FUNCTION privacy_financial_context_complete(uuid,uuid,text) TO app_rw;
    DROP POLICY dunning_bound_audit ON audit_event;
    DROP POLICY dunning_bound_read ON dunning_notice;
    DROP POLICY dunning_bound_update ON dunning_notice;
    DROP POLICY dunning_bound_delete ON dunning_notice;
    DROP TRIGGER dunning_scope_write ON dunning_notice;
    DROP TRIGGER dunning_notice_context_valid ON dunning_notice;
    DROP TABLE dunning_scope_context;
    DROP FUNCTION authorize_dunning_write(),validate_dunning_context(),protect_dunning_context();
    DROP FUNCTION IF EXISTS dunning_scope_access(uuid,uuid,text);
    DROP FUNCTION IF EXISTS dunning_scope_access(uuid,uuid,text,uuid,uuid);
    DROP FUNCTION dunning_history_complete(uuid,uuid,uuid),dunning_legacy_subject_clear(uuid,uuid,uuid);
    ALTER TABLE dunning_notice DROP COLUMN cancelled_at;
    ALTER TABLE dunning_notice DROP CONSTRAINT uq_dunning_org_id;
    ALTER TABLE lease_agreement DROP CONSTRAINT uq_dunning_lease_org_id;
    ''')
