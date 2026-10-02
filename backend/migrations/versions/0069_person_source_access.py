"""Protect contact custody, profile disclosure and authoritative privacy effects."""
from alembic import op

revision='0069'
down_revision='0068'
branch_labels=None
depends_on=None

CONTACT_FIELDS=('first_name','last_name','company_name','email','phone','street','zip_code','city','iban','bic','bank_name')
TABLES=('property','coverage_declaration','dunning_scope_context','unit','"user"','membership','person','tenant_profile','owner_profile','vendor','person_data_domain','person_data_grant','property_source_security',
        'audit_event','lease_agreement','ownership_history','cost_entry','invoice_document','settlement_run','settlement_line',
        'economic_plan','economic_plan_line','dunning_notice')


def upgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute('''
    ALTER TABLE person ADD COLUMN created_by uuid;
    ALTER TABLE person ADD CONSTRAINT fk_person_creator FOREIGN KEY(organization_id,created_by) REFERENCES "user"(organization_id,id);
    CREATE FUNCTION person_source_row_access(org uuid,person uuid,creator uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; role text; domain uuid; actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      IF person IS NULL OR capability IS NULL OR capability NOT IN ('read','prepare','export') THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      role:=public.scope_actor_role(org);
      IF role IS NULL OR (capability='prepare' AND (role NOT IN ('OWNER_ADMIN','CLERK') OR mode='PAUSED')) THEN RETURN false; END IF;
      SELECT d.id INTO domain FROM public.person_data_domain d WHERE d.organization_id=org AND d.person_id=person;
      IF domain IS NOT NULL THEN RETURN public.has_person_data_capability(org,domain,capability); END IF;
      RETURN COALESCE(creator=actor,false);
    END $$;
    CREATE FUNCTION person_source_access(org uuid,person uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE p public.person;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN false; END IF;
      SELECT target_row.* INTO p FROM public.person target_row WHERE target_row.organization_id=org AND target_row.id=person;
      IF NOT FOUND OR (capability='prepare' AND p.anonymized_at IS NOT NULL) THEN RETURN false; END IF;
      RETURN public.person_source_row_access(org,person,p.created_by,capability);
    END $$;
    CREATE FUNCTION profile_source_access(org uuid,kind text,profile uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE person uuid;
    BEGIN
      IF kind='tenant_profile' THEN SELECT p.person_id INTO person FROM public.tenant_profile p WHERE p.organization_id=org AND p.id=profile;
      ELSIF kind='owner_profile' THEN SELECT p.person_id INTO person FROM public.owner_profile p WHERE p.organization_id=org AND p.id=profile;
      ELSIF kind='vendor' THEN SELECT p.person_id INTO person FROM public.vendor p WHERE p.organization_id=org AND p.id=profile;
      ELSE RETURN false; END IF;
      RETURN person IS NOT NULL AND public.person_source_access(org,person,capability);
    END $$;
    REVOKE ALL ON FUNCTION person_source_row_access(uuid,uuid,uuid,text),person_source_access(uuid,uuid,text),profile_source_access(uuid,text,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION person_source_row_access(uuid,uuid,uuid,text),person_source_access(uuid,uuid,text),profile_source_access(uuid,text,uuid,text) TO app_rw;

    CREATE FUNCTION protect_person_source() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      IF TG_OP='INSERT' THEN
        IF NEW.created_by IS NOT NULL AND NEW.created_by IS DISTINCT FROM actor THEN
          RAISE EXCEPTION 'Contact creator must be actual actor' USING ERRCODE='42501'; END IF;
        IF actor IS NOT NULL THEN
          IF NOT COALESCE(public.scope_actor_role(NEW.organization_id) IN ('OWNER_ADMIN','CLERK'),false) THEN
            RAISE EXCEPTION 'Live contact writer required' USING ERRCODE='42501'; END IF;
          NEW.created_by:=actor;
        END IF;
        IF current_user='app_rw' AND (NEW.anonymized_at IS NOT NULL OR NEW.deletion_requested_at IS NOT NULL OR NEW.processing_restricted) THEN
          RAISE EXCEPTION 'Use privacy command' USING ERRCODE='42501'; END IF;
      ELSE
        IF (NEW.organization_id,NEW.id,NEW.created_by) IS DISTINCT FROM (OLD.organization_id,OLD.id,OLD.created_by) THEN
          RAISE EXCEPTION 'Contact identity and custody are immutable' USING ERRCODE='23514'; END IF;
        IF current_user='app_rw' AND OLD.anonymized_at IS NOT NULL THEN
          RAISE EXCEPTION 'Anonymized contact is immutable' USING ERRCODE='23514'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION protect_person_source() FROM PUBLIC,app_rw;
    CREATE TRIGGER protect_person_source BEFORE INSERT OR UPDATE ON person FOR EACH ROW EXECUTE FUNCTION protect_person_source();
    CREATE FUNCTION protect_person_profile() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF (OLD.organization_id,OLD.id,OLD.person_id) IS DISTINCT FROM (NEW.organization_id,NEW.id,NEW.person_id) THEN
        RAISE EXCEPTION 'Contact profile identity is immutable' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION protect_person_profile() FROM PUBLIC,app_rw;
    CREATE TRIGGER protect_history_source_context BEFORE UPDATE ON vendor FOR EACH ROW EXECUTE FUNCTION protect_person_profile();
    ''')
    for table in ('person','tenant_profile','owner_profile','vendor'):
        read="person_source_row_access(organization_id,id,created_by,'read')" if table=='person' else "person_source_access(organization_id,person_id,'read')"
        write="person_source_row_access(organization_id,id,created_by,'prepare')" if table=='person' else "person_source_access(organization_id,person_id,'prepare')"
        op.execute(f'CREATE POLICY contact_source_read ON {table} AS RESTRICTIVE FOR SELECT USING ({read})')
        op.execute(f'CREATE POLICY contact_source_insert ON {table} AS RESTRICTIVE FOR INSERT WITH CHECK ({write})')
        op.execute(f'CREATE POLICY contact_source_update ON {table} AS RESTRICTIVE FOR UPDATE USING ({write}) WITH CHECK ({write})')
        delete="property_source_security_mode(organization_id)='PREPARATION'" if table=='person' else write
        op.execute(f'CREATE POLICY contact_source_delete ON {table} AS RESTRICTIVE FOR DELETE USING ({delete})')
        op.execute(f'CREATE TRIGGER a_lock_contact_source BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION lock_strict_property_source_binding()')
    op.execute('REVOKE UPDATE ON person FROM app_rw')
    op.execute('GRANT UPDATE('+','.join(CONTACT_FIELDS)+',updated_at) ON person TO app_rw')
    _privacy_command()
    _consumer_boundaries()
    op.execute('''
    ALTER FUNCTION property_source_audit_access(uuid,text,uuid) RENAME TO property_audit_before_contacts;
    CREATE FUNCTION property_source_audit_access(org uuid,kind text,resource uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog,pg_temp SET row_security=on AS $$
    BEGIN
      IF public.property_source_security_mode(org)='PREPARATION' THEN RETURN public.property_audit_before_contacts(org,kind,resource); END IF;
      IF kind='person' THEN RETURN EXISTS(SELECT 1 FROM public.person p WHERE p.organization_id=org AND p.id=resource);
      ELSIF kind='tenant_profile' THEN RETURN EXISTS(SELECT 1 FROM public.tenant_profile p WHERE p.organization_id=org AND p.id=resource);
      ELSIF kind='owner_profile' THEN RETURN EXISTS(SELECT 1 FROM public.owner_profile p WHERE p.organization_id=org AND p.id=resource);
      ELSIF kind='vendor' THEN RETURN EXISTS(SELECT 1 FROM public.vendor p WHERE p.organization_id=org AND p.id=resource);
      END IF;
      RETURN public.property_audit_before_contacts(org,kind,resource);
    END $$;
    REVOKE ALL ON FUNCTION property_source_audit_access(uuid,text,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_source_audit_access(uuid,text,uuid) TO app_rw;
    ALTER POLICY source_audit_read ON audit_event USING(property_source_audit_access(organization_id,entity_type,entity_id));
    ''')


def _privacy_command():
    op.execute("""
    CREATE FUNCTION privacy_retention_facts(org uuid,person uuid) RETURNS TABLE(kind text,entity uuid,until_date date,coherent boolean)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT 'cost_entry'::text,c.id,COALESCE((SELECT max(d.retention_until) FROM public.invoice_document d WHERE d.cost_entry_id=c.id),
          make_date(extract(year FROM c.service_period_to)::int+8,12,31)),
        c.organization_id=org AND v.organization_id=org
          AND NOT EXISTS(SELECT 1 FROM public.invoice_document d WHERE d.cost_entry_id=c.id AND d.organization_id<>org)
      FROM public.cost_entry c JOIN public.vendor v ON v.id=c.vendor_id
      WHERE v.person_id=person AND c.status='confirmed'
      UNION ALL
      SELECT 'settlement_run',r.id,make_date(extract(year FROM r.period_end)::int+8,12,31),r.organization_id=org AND t.organization_id=org
      FROM public.settlement_run r JOIN public.tenant_profile t ON t.id=r.tenant_profile_id
      WHERE t.person_id=person AND r.status='finalized' AND r.run_type='BK_MIETER'
      UNION ALL
      SELECT 'settlement_run',r.id,make_date(extract(year FROM r.period_end)::int+8,12,31),r.organization_id=org AND ln.organization_id=org AND p.organization_id=org
      FROM public.settlement_run r JOIN public.settlement_line ln ON ln.settlement_run_id=r.id
        JOIN public.owner_profile p ON p.id=ln.addressee_owner_profile_id
      WHERE p.person_id=person AND r.status='finalized' AND r.run_type IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT')
      UNION ALL
      SELECT 'economic_plan',e.id,make_date(extract(year FROM e.period_end)::int+8,12,31),e.organization_id=org AND ln.organization_id=org AND p.organization_id=org
      FROM public.economic_plan e JOIN public.economic_plan_line ln ON ln.economic_plan_id=e.id
        JOIN public.owner_profile p ON p.id=ln.owner_profile_id
      WHERE p.person_id=person AND e.status='finalized'
      UNION ALL
      SELECT 'dunning_notice',n.id,make_date(extract(year FROM n.issue_date)::int+8,12,31),n.organization_id=org AND COALESCE(t.organization_id,p.organization_id)=org
      FROM public.dunning_notice n LEFT JOIN public.tenant_profile t ON t.id=n.tenant_profile_id
        LEFT JOIN public.owner_profile p ON p.id=n.owner_profile_id
      WHERE (t.person_id=person OR p.person_id=person) AND n.status='issued'
    $$;
    REVOKE ALL ON FUNCTION privacy_retention_facts(uuid,uuid) FROM PUBLIC,app_rw;

    CREATE FUNCTION apply_person_privacy(org uuid,person uuid,command text) RETURNS text
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      target public.person; mode text; retained boolean; coherent boolean; bindings bigint; governing date;
      outcome text; now_utc timestamp;
    BEGIN
      IF command IS NULL OR command NOT IN ('REQUEST_DELETION','ANONYMIZE') THEN RETURN 'INVALID'; END IF;
      -- Mode lookup fences the tenant and acquires the shared ACL lock first.
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL OR public.scope_actor_role(org) IS DISTINCT FROM 'OWNER_ADMIN' THEN RETURN 'FORBIDDEN'; END IF;
      IF mode='PAUSED' THEN RETURN 'PAUSED'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      IF NOT public.privacy_financial_context_complete(org,person,'read') THEN RETURN 'INCOMPLETE'; END IF;
      SELECT * INTO target FROM public.person p WHERE p.organization_id=org AND p.id=person FOR UPDATE;
      IF NOT FOUND OR NOT public.person_source_access(org,person,'read') THEN RETURN 'NOT_FOUND'; END IF;
      IF target.anonymized_at IS NOT NULL THEN RETURN 'NONE_ALREADY_ANONYMIZED'; END IF;
      IF NOT public.person_source_access(org,person,'prepare') THEN RETURN 'FORBIDDEN'; END IF;
      -- Profiles/relationships are expected identities, never RLS-filtered rows.
      IF EXISTS(SELECT 1 FROM public.tenant_profile p WHERE p.person_id=person AND p.organization_id<>org
        UNION ALL SELECT 1 FROM public.owner_profile p WHERE p.person_id=person AND p.organization_id<>org
        UNION ALL SELECT 1 FROM public.vendor p WHERE p.person_id=person AND p.organization_id<>org) THEN RETURN 'INCOMPLETE'; END IF;
      IF EXISTS(SELECT 1 FROM public.lease_agreement h JOIN public.tenant_profile p ON p.id=h.tenant_profile_id
          WHERE p.person_id=person AND (h.valid_to IS NULL OR h.valid_to>CURRENT_DATE)
        UNION ALL SELECT 1 FROM public.ownership_history h JOIN public.owner_profile p ON p.id=h.owner_profile_id
          WHERE p.person_id=person AND (h.valid_to IS NULL OR h.valid_to>CURRENT_DATE)) THEN RETURN 'ACTIVE'; END IF;
      SELECT count(DISTINCT (f.kind,f.entity)),COALESCE(bool_or(f.until_date IS NULL OR f.until_date>=CURRENT_DATE),false),
        COALESCE(bool_and(f.coherent),true),max(f.until_date) INTO bindings,retained,coherent,governing
        FROM public.privacy_retention_facts(org,person) f;
      IF NOT coherent THEN RETURN 'INCOMPLETE'; END IF;
      IF retained AND command='ANONYMIZE' THEN RETURN 'RETAINED'; END IF;
      -- Check clock expiry once more immediately before effects, after discovery.
      IF NOT public.person_source_access(org,person,'prepare') OR NOT public.privacy_financial_context_complete(org,person,'read') THEN RETURN 'INCOMPLETE'; END IF;
      now_utc:=clock_timestamp() AT TIME ZONE 'UTC';
      IF retained THEN
        UPDATE public.person SET deletion_requested_at=now_utc,processing_restricted=true WHERE organization_id=org AND id=apply_person_privacy.person;
        outcome:='RESTRICTED';
      ELSE
        UPDATE public.person SET first_name='',last_name='Anonymisiert',company_name=NULL,email=NULL,phone=NULL,
          street='',zip_code='',city='',iban=NULL,bic=NULL,bank_name=NULL,anonymized_at=now_utc,processing_restricted=false,
          deletion_requested_at=CASE WHEN command='REQUEST_DELETION' THEN now_utc ELSE deletion_requested_at END
          WHERE organization_id=org AND id=apply_person_privacy.person;
        outcome:='ANONYMIZED';
      END IF;
      IF command='REQUEST_DELETION' THEN
        INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
          VALUES(org,actor,'person',person,'dsgvo.deletion_requested',jsonb_build_object('outcome',outcome,'is_bound',bindings>0,
            'purgeable_now',bindings>0 AND NOT retained,'governing_retention_until',governing,'binding_count',bindings));
      END IF;
      IF outcome='ANONYMIZED' THEN
        INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
          VALUES(org,actor,'person',person,'dsgvo.anonymized',jsonb_build_object('source',CASE WHEN command='REQUEST_DELETION' THEN 'request_deletion' ELSE 'explicit' END));
      END IF;
      RETURN outcome;
    END $$;
    REVOKE ALL ON FUNCTION apply_person_privacy(uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION apply_person_privacy(uuid,uuid,text) TO app_rw;
    """)


def _consumer_boundaries():
    op.execute('''
    CREATE FUNCTION property_financial_contact_access(org uuid,property uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE source record; mode text;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','export') THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      -- Enumerate every expected identity. Filtered profiles cannot imply vacancy,
      -- missing recipients or complete frozen display names.
      FOR source IN
        SELECT 'tenant_profile'::text kind,h.tenant_profile_id id,h.organization_id source_org
          FROM public.lease_agreement h JOIN public.unit u ON u.id=h.unit_id WHERE u.property_id=property
        UNION ALL SELECT 'owner_profile',h.owner_profile_id,h.organization_id
          FROM public.ownership_history h JOIN public.unit u ON u.id=h.unit_id WHERE u.property_id=property
        UNION ALL SELECT 'tenant_profile',r.tenant_profile_id,r.organization_id FROM public.settlement_run r
          WHERE r.property_id=property AND r.tenant_profile_id IS NOT NULL
        UNION ALL SELECT 'owner_profile',ln.addressee_owner_profile_id,ln.organization_id
          FROM public.settlement_line ln JOIN public.settlement_run r ON r.id=ln.settlement_run_id
          WHERE r.property_id=property AND ln.addressee_owner_profile_id IS NOT NULL
        UNION ALL SELECT 'owner_profile',ln.owner_profile_id,ln.organization_id
          FROM public.economic_plan_line ln JOIN public.economic_plan p ON p.id=ln.economic_plan_id
          WHERE p.property_id=property AND ln.owner_profile_id IS NOT NULL
        UNION ALL SELECT 'vendor',c.vendor_id,c.organization_id FROM public.cost_entry c
          WHERE c.property_id=property AND c.vendor_id IS NOT NULL
      LOOP
        IF source.source_org IS DISTINCT FROM org OR NOT public.profile_source_access(org,source.kind,source.id,'read')
          OR (capability='export' AND NOT public.profile_source_access(org,source.kind,source.id,'export')) THEN RETURN false; END IF;
      END LOOP;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION property_financial_contact_access(uuid,uuid,text) FROM PUBLIC,app_rw;
    ALTER FUNCTION property_financial_source_access(uuid,uuid) RENAME TO financial_sources_before_contacts;
    CREATE FUNCTION property_financial_source_access(org uuid,property uuid) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT public.financial_sources_before_contacts(org,property) AND public.property_financial_contact_access(org,property,'read')
    $$;
    ALTER FUNCTION property_financial_disclosure_access(uuid,uuid,text) RENAME TO financial_disclosure_before_contacts;
    CREATE FUNCTION property_financial_disclosure_access(org uuid,property uuid,capability text) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT public.financial_disclosure_before_contacts(org,property,capability) AND public.property_financial_contact_access(org,property,capability)
    $$;
    REVOKE ALL ON FUNCTION financial_sources_before_contacts(uuid,uuid),financial_disclosure_before_contacts(uuid,uuid,text) FROM PUBLIC,app_rw;
    REVOKE ALL ON FUNCTION property_financial_source_access(uuid,uuid),property_financial_disclosure_access(uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_financial_source_access(uuid,uuid),property_financial_disclosure_access(uuid,uuid,text) TO app_rw;
    ALTER POLICY financial_source_disclosure ON settlement_run USING((status='draft' AND snapshot_payload IS NULL) OR property_financial_disclosure_access(organization_id,property_id,'read'));
    ALTER POLICY financial_source_disclosure ON economic_plan USING((status='draft' AND snapshot_payload IS NULL) OR property_financial_disclosure_access(organization_id,property_id,'read'));

    ALTER FUNCTION property_history_row_access(uuid,text,jsonb,text) RENAME TO history_source_before_contacts;
    CREATE FUNCTION property_history_row_access(org uuid,kind text,context jsonb,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF NOT public.history_source_before_contacts(org,kind,context,capability) THEN RETURN false; END IF;
      IF public.property_source_security_mode(org)='PREPARATION' OR capability<>'prepare' THEN RETURN true; END IF;
      IF kind='lease_agreement' THEN RETURN public.profile_source_access(org,'tenant_profile',(context->>'tenant_profile_id')::uuid,'read'); END IF;
      RETURN public.profile_source_access(org,'owner_profile',(context->>'owner_profile_id')::uuid,'read');
    END $$;
    REVOKE ALL ON FUNCTION history_source_before_contacts(uuid,text,jsonb,text) FROM PUBLIC,app_rw;
    REVOKE ALL ON FUNCTION property_history_row_access(uuid,text,jsonb,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_history_row_access(uuid,text,jsonb,text) TO app_rw;

    CREATE FUNCTION profile_reference_access(org uuid,kind text,profile uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE person uuid; mode text;
    BEGIN
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      IF mode='PAUSED' OR NOT public.profile_source_access(org,kind,profile,'read') THEN RETURN false; END IF;
      IF kind='vendor' THEN SELECT p.person_id INTO person FROM public.vendor p WHERE p.organization_id=org AND p.id=profile;
      ELSIF kind='tenant_profile' THEN SELECT p.person_id INTO person FROM public.tenant_profile p WHERE p.organization_id=org AND p.id=profile;
      ELSIF kind='owner_profile' THEN SELECT p.person_id INTO person FROM public.owner_profile p WHERE p.organization_id=org AND p.id=profile;
      ELSE RETURN false; END IF;
      RETURN EXISTS(SELECT 1 FROM public.person p WHERE p.organization_id=org AND p.id=person AND p.anonymized_at IS NULL);
    END $$;
    REVOKE ALL ON FUNCTION profile_reference_access(uuid,text,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION profile_reference_access(uuid,text,uuid) TO app_rw;
    CREATE FUNCTION guard_cost_contact_reference() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM public.property_source_security s WHERE s.organization_id=NEW.organization_id AND s.mode<>'PREPARATION') THEN RETURN NEW; END IF;
      -- Statement ACL lock precedes existing row-level financial-input locks.
      -- Repeat after waiting so an erasure committed by the prior writer wins.
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||NEW.organization_id::text,0));
      IF NEW.vendor_id IS NOT NULL AND NOT public.profile_reference_access(NEW.organization_id,'vendor',NEW.vendor_id) THEN
        RAISE EXCEPTION 'Readable active contact required' USING ERRCODE='42501'; END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION guard_cost_contact_reference() FROM PUBLIC,app_rw;
    CREATE TRIGGER a_lock_cost_contact BEFORE INSERT OR UPDATE ON cost_entry FOR EACH STATEMENT EXECUTE FUNCTION lock_strict_property_source_binding();
    CREATE TRIGGER z_guard_cost_contact BEFORE INSERT OR UPDATE ON cost_entry FOR EACH ROW EXECUTE FUNCTION guard_cost_contact_reference();

    CREATE FUNCTION dunning_contact_access(org uuid,tenant uuid,owner uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; kind text; profile uuid;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','prepare','export') THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      IF (capability='prepare' AND mode='PAUSED') OR num_nonnulls(tenant,owner)<>1 THEN RETURN false; END IF;
      kind:=CASE WHEN tenant IS NOT NULL THEN 'tenant_profile' ELSE 'owner_profile' END;
      profile:=COALESCE(tenant,owner);
      IF capability='prepare' AND NOT public.profile_reference_access(org,kind,profile) THEN RETURN false; END IF;
      RETURN public.profile_source_access(org,kind,profile,'read')
        AND (capability<>'export' OR public.profile_source_access(org,kind,profile,'export'));
    END $$;
    REVOKE ALL ON FUNCTION dunning_contact_access(uuid,uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dunning_contact_access(uuid,uuid,uuid,text) TO app_rw;
    CREATE POLICY dunning_contact_read ON dunning_notice AS RESTRICTIVE FOR SELECT USING(dunning_contact_access(organization_id,tenant_profile_id,owner_profile_id,'read'));
    CREATE POLICY dunning_contact_insert ON dunning_notice AS RESTRICTIVE FOR INSERT WITH CHECK(dunning_contact_access(organization_id,tenant_profile_id,owner_profile_id,'prepare'));
    CREATE POLICY dunning_contact_update ON dunning_notice AS RESTRICTIVE FOR UPDATE USING(dunning_contact_access(organization_id,tenant_profile_id,owner_profile_id,'prepare')) WITH CHECK(dunning_contact_access(organization_id,tenant_profile_id,owner_profile_id,'prepare'));
    CREATE POLICY dunning_contact_delete ON dunning_notice AS RESTRICTIVE FOR DELETE USING(dunning_contact_access(organization_id,tenant_profile_id,owner_profile_id,'prepare'));

    ALTER FUNCTION privacy_source_context_complete(uuid,uuid,text) RENAME TO privacy_sources_before_contacts;
    CREATE FUNCTION privacy_source_context_complete(org uuid,person uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE source record;
    BEGIN
      IF NOT public.privacy_sources_before_contacts(org,person,capability)
        OR NOT public.person_source_access(org,person,'read') OR NOT public.person_source_access(org,person,capability) THEN RETURN false; END IF;
      FOR source IN
        SELECT r.property_id FROM public.settlement_run r WHERE r.organization_id=org AND r.status='finalized'
          AND (EXISTS(SELECT 1 FROM public.tenant_profile p WHERE p.id=r.tenant_profile_id AND p.person_id=person)
            OR EXISTS(SELECT 1 FROM public.settlement_line ln JOIN public.owner_profile p ON p.id=ln.addressee_owner_profile_id WHERE ln.settlement_run_id=r.id AND p.person_id=person))
        UNION ALL SELECT e.property_id FROM public.economic_plan e WHERE e.organization_id=org AND e.status='finalized'
          AND EXISTS(SELECT 1 FROM public.economic_plan_line ln JOIN public.owner_profile p ON p.id=ln.owner_profile_id WHERE ln.economic_plan_id=e.id AND p.person_id=person)
      LOOP
        IF NOT public.property_financial_contact_access(org,source.property_id,capability) THEN RETURN false; END IF;
      END LOOP;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION privacy_source_context_complete(uuid,uuid,text) FROM PUBLIC,app_rw;
    ''')
    for table,profile in (('lease_agreement','tenant_profile_id'),('ownership_history','owner_profile_id')):
        context=f"jsonb_build_object('organization_id',organization_id,'id',id,'unit_id',unit_id,'{profile}',{profile})"
        write=f"property_history_row_access(organization_id,'{table}',{context},'prepare')"
        op.execute(f"ALTER POLICY history_source_read ON {table} USING (property_history_row_access(organization_id,'{table}',{context},'read'))")
        op.execute(f'ALTER POLICY history_source_insert ON {table} WITH CHECK ({write})')
        op.execute(f'ALTER POLICY history_source_update ON {table} USING ({write}) WITH CHECK ({write})')
        op.execute(f'ALTER POLICY history_source_delete ON {table} USING ({write})')


def _downgrade_consumers():
    for table,profile in (('lease_agreement','tenant_profile_id'),('ownership_history','owner_profile_id')):
        context=f"jsonb_build_object('organization_id',organization_id,'id',id,'unit_id',unit_id,'{profile}',{profile})"
        write=f"history_source_before_contacts(organization_id,'{table}',{context},'prepare')"
        op.execute(f"ALTER POLICY history_source_read ON {table} USING (history_source_before_contacts(organization_id,'{table}',{context},'read'))")
        op.execute(f'ALTER POLICY history_source_insert ON {table} WITH CHECK ({write})')
        op.execute(f'ALTER POLICY history_source_update ON {table} USING ({write}) WITH CHECK ({write})')
        op.execute(f'ALTER POLICY history_source_delete ON {table} USING ({write})')
    for table in ('settlement_run','economic_plan'):
        op.execute(f"ALTER POLICY financial_source_disclosure ON {table} USING((status='draft' AND snapshot_payload IS NULL) OR financial_disclosure_before_contacts(organization_id,property_id,'read'))")
    for action in ('read','insert','update','delete'):
        op.execute(f'DROP POLICY dunning_contact_{action} ON dunning_notice')
    op.execute('''
    DROP FUNCTION property_history_row_access(uuid,text,jsonb,text),property_financial_source_access(uuid,uuid),property_financial_disclosure_access(uuid,uuid,text),privacy_source_context_complete(uuid,uuid,text);
    ALTER FUNCTION history_source_before_contacts(uuid,text,jsonb,text) RENAME TO property_history_row_access;
    ALTER FUNCTION financial_sources_before_contacts(uuid,uuid) RENAME TO property_financial_source_access;
    ALTER FUNCTION financial_disclosure_before_contacts(uuid,uuid,text) RENAME TO property_financial_disclosure_access;
    ALTER FUNCTION privacy_sources_before_contacts(uuid,uuid,text) RENAME TO privacy_source_context_complete;
    GRANT EXECUTE ON FUNCTION property_history_row_access(uuid,text,jsonb,text),property_financial_source_access(uuid,uuid),property_financial_disclosure_access(uuid,uuid,text) TO app_rw;
    DROP TRIGGER IF EXISTS a_lock_cost_contact ON cost_entry;
    DROP TRIGGER IF EXISTS z_guard_cost_contact ON cost_entry;
    DROP FUNCTION IF EXISTS guard_cost_contact_reference(),profile_reference_access(uuid,text,uuid);
    DROP FUNCTION property_financial_contact_access(uuid,uuid,text),dunning_contact_access(uuid,uuid,uuid,text);
    ''')


def downgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute('''DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED'))
      OR EXISTS(SELECT 1 FROM person WHERE created_by IS NOT NULL) THEN
      RAISE EXCEPTION 'Retain contact custody and enforcement'; END IF; END $$;
    ALTER POLICY source_audit_read ON audit_event USING(property_audit_before_contacts(organization_id,entity_type,entity_id));
    DROP FUNCTION property_source_audit_access(uuid,text,uuid);
    ALTER FUNCTION property_audit_before_contacts(uuid,text,uuid) RENAME TO property_source_audit_access;
    DROP TRIGGER protect_person_source ON person;
    DROP TRIGGER protect_history_source_context ON vendor;
    DROP FUNCTION IF EXISTS protect_person_profile();
    DROP FUNCTION protect_person_source(),apply_person_privacy(uuid,uuid,text),privacy_retention_facts(uuid,uuid);
    REVOKE UPDATE ON person FROM app_rw;
    GRANT UPDATE ON person TO app_rw;
    ''')
    _downgrade_consumers()
    for table in ('person','tenant_profile','owner_profile','vendor'):
        for action in ('read','insert','update','delete'):
            op.execute(f'DROP POLICY contact_source_{action} ON {table}')
        op.execute(f'DROP TRIGGER a_lock_contact_source ON {table}')
    op.execute('''DROP FUNCTION profile_source_access(uuid,text,uuid,text),person_source_access(uuid,uuid,text),person_source_row_access(uuid,uuid,uuid,text);
      ALTER TABLE person DROP COLUMN created_by;''')
