"""Read-only, authorized inventory of retained master-data references (T56)."""
from alembic import op

revision = '0137'
down_revision = '0136'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION master_delete_json_has_id(payload jsonb, resource uuid) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
    DECLARE item record; element jsonb;
    BEGIN
      IF jsonb_typeof(payload)='string' THEN RETURN payload=to_jsonb(resource::text);
      ELSIF jsonb_typeof(payload)='object' THEN
        FOR item IN SELECT * FROM jsonb_each(payload) LOOP
          IF item.key=resource::text OR public.master_delete_json_has_id(item.value,resource) THEN RETURN true; END IF;
        END LOOP;
      ELSIF jsonb_typeof(payload)='array' THEN
        FOR element IN SELECT value FROM jsonb_array_elements(payload) LOOP
          IF public.master_delete_json_has_id(element,resource) THEN RETURN true; END IF;
        END LOOP;
      END IF;
      RETURN false;
    END $$;
    REVOKE ALL ON FUNCTION master_delete_json_has_id(jsonb,uuid) FROM PUBLIC,app_rw;

    CREATE FUNCTION master_delete_reference_kind(org uuid, kind text, resource uuid) RETURNS text
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE target oid; context jsonb; allowed boolean; edge record; predicate text; present boolean;
    BEGIN
      IF org IS NULL OR resource IS NULL OR org IS DISTINCT FROM
        NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR kind IS NULL OR kind NOT IN ('property','unit','person','lease_agreement') THEN RETURN 'inaccessible'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      IF public.scope_actor_role(org) IS NULL OR public.scope_actor_role(org) NOT IN ('OWNER_ADMIN','CLERK') THEN
        RETURN 'inaccessible';
      END IF;
      -- Only these four canonical sources are admitted. No caller-controlled SQL identifiers.
      IF kind='property' THEN
        target:='public.property'::regclass;
        SELECT to_jsonb(p) INTO context FROM public.property p WHERE p.organization_id=org AND p.id=resource;
        allowed:=public.property_master_access(org,resource,'prepare');
      ELSIF kind='unit' THEN
        target:='public.unit'::regclass;
        SELECT to_jsonb(u) INTO context FROM public.unit u WHERE u.organization_id=org AND u.id=resource;
        allowed:=public.property_master_access(org,(context->>'property_id')::uuid,'prepare');
      ELSIF kind='person' THEN
        target:='public.person'::regclass;
        SELECT to_jsonb(p) INTO context FROM public.person p WHERE p.organization_id=org AND p.id=resource;
        allowed:=public.person_source_access(org,resource,'prepare');
      ELSE
        target:='public.lease_agreement'::regclass;
        SELECT to_jsonb(h) INTO context FROM public.lease_agreement h WHERE h.organization_id=org AND h.id=resource;
        allowed:=public.property_history_row_access(org,'lease_agreement',context,'prepare');
      END IF;
      IF context IS NULL OR allowed IS DISTINCT FROM true THEN RETURN 'inaccessible'; END IF;
      IF kind='person' AND (context->>'processing_restricted'='true'
        OR context->>'deletion_requested_at' IS NOT NULL OR context->>'anonymized_at' IS NOT NULL) THEN RETURN 'privacy'; END IF;

      -- Inspect EVERY physical incoming FK, including composite and CASCADE edges.
      -- Join all FK column pairs against the authorized tenant's exact target row;
      -- even a malformed cross-tenant incoming edge must prevent physical cascade.
      FOR edge IN
        SELECT c.oid, c.conrelid, n.nspname, s.relname,
          tn.nspname target_schema, t.relname target_table, c.conkey, c.confkey
        FROM pg_constraint c JOIN pg_class s ON s.oid=c.conrelid
        JOIN pg_namespace n ON n.oid=s.relnamespace
        JOIN pg_class t ON t.oid=c.confrelid JOIN pg_namespace tn ON tn.oid=t.relnamespace
        WHERE c.contype='f' AND c.confrelid=target AND n.nspname='public'
        ORDER BY CASE s.relname WHEN 'unit' THEN 0 WHEN 'lease_agreement' THEN 1
          WHEN 'tenant_profile' THEN 2 WHEN 'owner_profile' THEN 3 WHEN 'vendor' THEN 4
          WHEN 'lease_rent_change' THEN 5 ELSE 6 END, s.relname,c.oid
      LOOP
        SELECT string_agg(format('s.%I=t.%I',sa.attname,ta.attname),' AND ' ORDER BY k.ordinality)
          INTO predicate
          FROM unnest(edge.conkey,edge.confkey) WITH ORDINALITY AS k(source_att,target_att,ordinality)
          JOIN pg_attribute sa ON sa.attrelid=edge.conrelid AND sa.attnum=k.source_att
          JOIN pg_attribute ta ON ta.attrelid=target AND ta.attnum=k.target_att;
        EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.%I s JOIN %I.%I t ON %s WHERE t.organization_id=$1 AND t.id=$2)',
          edge.nspname,edge.relname,edge.target_schema,edge.target_table,predicate)
          INTO present USING org,resource;
        IF present THEN
          RETURN CASE edge.relname
            WHEN 'unit' THEN 'unit' WHEN 'lease_agreement' THEN 'lease'
            WHEN 'tenant_profile' THEN 'tenant_profile' WHEN 'owner_profile' THEN 'owner_profile'
            WHEN 'vendor' THEN 'vendor' WHEN 'lease_rent_change' THEN 'rent_history'
            WHEN 'property_data_domain' THEN 'audience' WHEN 'property_data_grant' THEN 'audience'
            WHEN 'property_creation_receipt' THEN 'receipt'
            WHEN 'payment' THEN 'financial' WHEN 'cost_entry' THEN 'financial'
            WHEN 'settlement_run' THEN 'financial' WHEN 'economic_plan' THEN 'financial'
            ELSE 'reference' END;
        END IF;
      END LOOP;
      IF EXISTS(SELECT 1 FROM public.approved_fact_source s WHERE s.organization_id=org
        AND s.subject_id=resource AND s.subject_kind=CASE kind WHEN 'lease_agreement' THEN 'LEASE_AGREEMENT' ELSE upper(kind) END)
        THEN RETURN 'source'; END IF;
      IF EXISTS(SELECT 1 FROM public.audit_event a WHERE a.organization_id=org AND a.entity_type=kind AND a.entity_id=resource)
        THEN RETURN 'audit'; END IF;
      IF EXISTS(SELECT 1 FROM public.master_import_batch b WHERE b.organization_id=org
        AND public.master_delete_json_has_id(b.result,resource)) THEN RETURN 'receipt'; END IF;
      IF EXISTS(SELECT 1 FROM public.settlement_run s WHERE s.organization_id=org
        AND public.master_delete_json_has_id(s.snapshot_payload,resource))
        OR EXISTS(SELECT 1 FROM public.economic_plan p WHERE p.organization_id=org
        AND public.master_delete_json_has_id(p.snapshot_payload,resource)) THEN RETURN 'snapshot'; END IF;
      IF EXISTS(SELECT 1 FROM public.dunning_batch b WHERE b.organization_id=org
        AND public.master_delete_json_has_id(b.result,resource)) THEN RETURN 'receipt'; END IF;
      RETURN NULL;
    END $$;
    REVOKE ALL ON FUNCTION master_delete_reference_kind(uuid,text,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION master_delete_reference_kind(uuid,text,uuid) TO app_rw;
    """)


def downgrade():
    op.execute("""
    DROP FUNCTION master_delete_reference_kind(uuid,text,uuid);
    DROP FUNCTION master_delete_json_has_id(jsonb,uuid);
    """)
