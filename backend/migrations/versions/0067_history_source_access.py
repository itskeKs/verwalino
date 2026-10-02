"""Protect relationship history without turning hidden rows into false absence."""
from alembic import op

revision = '0067'
down_revision = '0066'
branch_labels = None
depends_on = None

HISTORY = {'lease_agreement':'tenant_profile_id','ownership_history':'owner_profile_id'}
TABLES = ('property_source_security','property','unit','person','tenant_profile','owner_profile','audit_event',*HISTORY)


def upgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""
    CREATE FUNCTION property_history_graph_valid(org uuid,property uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT NOT EXISTS(SELECT 1 FROM public.lease_agreement h JOIN public.unit u ON u.id=h.unit_id
        LEFT JOIN public.tenant_profile t ON t.id=h.tenant_profile_id LEFT JOIN public.person p ON p.id=t.person_id
        WHERE u.property_id=property AND (h.organization_id<>org OR u.organization_id<>org
          OR t.id IS NULL OR t.organization_id<>org OR p.id IS NULL OR p.organization_id<>org))
      AND NOT EXISTS(SELECT 1 FROM public.ownership_history h JOIN public.unit u ON u.id=h.unit_id
        LEFT JOIN public.owner_profile t ON t.id=h.owner_profile_id LEFT JOIN public.person p ON p.id=t.person_id
        WHERE u.property_id=property AND (h.organization_id<>org OR u.organization_id<>org
          OR t.id IS NULL OR t.organization_id<>org OR p.id IS NULL OR p.organization_id<>org))
    $$;
    REVOKE ALL ON FUNCTION property_history_graph_valid(uuid,uuid) FROM PUBLIC,app_rw;
    ALTER FUNCTION property_complete_graph_actor_access(uuid,uuid,uuid,text,boolean) RENAME TO property_source_graph_before_history;
    CREATE FUNCTION property_complete_graph_actor_access(org uuid,actor uuid,property uuid,capability text,for_effect boolean) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      RETURN public.property_source_graph_before_history(org,actor,property,capability,for_effect)
        AND (public.property_source_security_mode(org)='PREPARATION' OR public.property_history_graph_valid(org,property));
    END $$;
    REVOKE ALL ON FUNCTION property_complete_graph_actor_access(uuid,uuid,uuid,text,boolean) FROM PUBLIC,app_rw;

    CREATE FUNCTION property_history_row_access(org uuid,kind text,context jsonb,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; property uuid; profile uuid; actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      IF context->>'organization_id' IS DISTINCT FROM org::text OR context->>'id' IS NULL
        OR kind IS NULL OR kind NOT IN ('lease_agreement','ownership_history')
        OR capability IS NULL OR capability NOT IN ('read','prepare','export') THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      SELECT u.property_id INTO property FROM public.unit u WHERE u.organization_id=org AND u.id=(context->>'unit_id')::uuid;
      IF property IS NULL THEN RETURN false; END IF;
      IF kind='lease_agreement' THEN
        profile:=(context->>'tenant_profile_id')::uuid;
        IF NOT EXISTS(SELECT 1 FROM public.tenant_profile t JOIN public.person p ON p.id=t.person_id AND p.organization_id=t.organization_id
          WHERE t.organization_id=org AND t.id=profile AND (capability<>'prepare' OR p.anonymized_at IS NULL)) THEN RETURN false; END IF;
      ELSE
        profile:=(context->>'owner_profile_id')::uuid;
        IF NOT EXISTS(SELECT 1 FROM public.owner_profile t JOIN public.person p ON p.id=t.person_id AND p.organization_id=t.organization_id
          WHERE t.organization_id=org AND t.id=profile AND (capability<>'prepare' OR p.anonymized_at IS NULL)) THEN RETURN false; END IF;
      END IF;
      RETURN public.property_source_actor_permitted(org,actor,'property',property,capability)
        AND public.property_complete_graph_actor_access(org,actor,property,'read',false)
        AND (capability<>'export' OR public.property_complete_graph_actor_access(org,actor,property,'export',false));
    END $$;
    REVOKE ALL ON FUNCTION property_history_row_access(uuid,text,jsonb,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_history_row_access(uuid,text,jsonb,text) TO app_rw;

    ALTER FUNCTION privacy_source_context_complete(uuid,uuid,text) RENAME TO privacy_output_source_complete;
    CREATE FUNCTION privacy_source_context_complete(org uuid,person uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; source record; actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      IF NOT public.privacy_output_source_complete(org,person,capability) THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode='PREPARATION' THEN RETURN true; END IF;
      -- Inspect expected history even when ordinary RLS hides every relationship.
      -- A malformed profile/tenant edge is not evidence of no active contract.
      FOR source IN
        SELECT h.organization_id history_org,t.organization_id profile_org,u.organization_id unit_org,u.property_id
          FROM public.lease_agreement h JOIN public.tenant_profile t ON t.id=h.tenant_profile_id
          LEFT JOIN public.unit u ON u.id=h.unit_id WHERE t.person_id=person
        UNION ALL
        SELECT h.organization_id,t.organization_id,u.organization_id,u.property_id
          FROM public.ownership_history h JOIN public.owner_profile t ON t.id=h.owner_profile_id
          LEFT JOIN public.unit u ON u.id=h.unit_id WHERE t.person_id=person
      LOOP
        IF source.history_org IS DISTINCT FROM org OR source.profile_org IS DISTINCT FROM org OR source.unit_org IS DISTINCT FROM org
          OR NOT public.property_complete_graph_actor_access(org,actor,source.property_id,capability,false) THEN RETURN false; END IF;
      END LOOP;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION privacy_source_context_complete(uuid,uuid,text) FROM PUBLIC,app_rw;

    CREATE FUNCTION protect_history_source_context() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE before jsonb:=to_jsonb(OLD); after jsonb:=to_jsonb(NEW); field text;
    BEGIN
      FOREACH field IN ARRAY ARRAY['organization_id','id','unit_id','tenant_profile_id','owner_profile_id','person_id'] LOOP
        IF before->field IS DISTINCT FROM after->field THEN
          RAISE EXCEPTION 'History source context is immutable' USING ERRCODE='23514';
        END IF;
      END LOOP;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION protect_history_source_context() FROM PUBLIC,app_rw;
    """)
    for table in ('tenant_profile','owner_profile'):
        op.execute(f'CREATE TRIGGER protect_history_source_context BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_history_source_context()')
    for table,profile in HISTORY.items():
        context=f"jsonb_build_object('organization_id',organization_id,'id',id,'unit_id',unit_id,'{profile}',{profile})"
        read=f"property_history_row_access(organization_id,'{table}',{context},'read')"
        write=f"property_history_row_access(organization_id,'{table}',{context},'prepare')"
        op.execute(f'CREATE POLICY history_source_read ON {table} AS RESTRICTIVE FOR SELECT USING ({read})')
        op.execute(f'CREATE POLICY history_source_insert ON {table} AS RESTRICTIVE FOR INSERT WITH CHECK ({write})')
        op.execute(f'CREATE POLICY history_source_update ON {table} AS RESTRICTIVE FOR UPDATE USING ({write}) WITH CHECK ({write})')
        op.execute(f'CREATE POLICY history_source_delete ON {table} AS RESTRICTIVE FOR DELETE USING ({write})')
        op.execute(f'CREATE TRIGGER a_lock_history_source BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION lock_strict_property_source_binding()')
        op.execute(f'CREATE TRIGGER protect_history_source_context BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_history_source_context()')
    op.execute("""
    ALTER FUNCTION property_source_audit_access(uuid,text,uuid) RENAME TO property_audit_before_history;
    CREATE FUNCTION property_source_audit_access(org uuid,kind text,resource uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog,pg_temp SET row_security=on AS $$
    BEGIN
      IF public.property_source_security_mode(org)='PREPARATION' THEN RETURN public.property_audit_before_history(org,kind,resource); END IF;
      IF kind='lease_agreement' THEN
        RETURN EXISTS(SELECT 1 FROM public.lease_agreement h WHERE h.organization_id=org AND h.id=resource);
      ELSIF kind='ownership_history' THEN
        RETURN EXISTS(SELECT 1 FROM public.ownership_history h WHERE h.organization_id=org AND h.id=resource);
      END IF;
      RETURN public.property_audit_before_history(org,kind,resource);
    END $$;
    REVOKE ALL ON FUNCTION property_source_audit_access(uuid,text,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_source_audit_access(uuid,text,uuid) TO app_rw;
    ALTER POLICY source_audit_read ON audit_event USING(property_source_audit_access(organization_id,entity_type,entity_id));
    """)


def downgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain strict history source access'; END IF; END $$;
    ALTER POLICY source_audit_read ON audit_event USING(property_audit_before_history(organization_id,entity_type,entity_id));
    DROP FUNCTION property_source_audit_access(uuid,text,uuid);
    ALTER FUNCTION property_audit_before_history(uuid,text,uuid) RENAME TO property_source_audit_access;
    DROP FUNCTION privacy_source_context_complete(uuid,uuid,text);
    ALTER FUNCTION privacy_output_source_complete(uuid,uuid,text) RENAME TO privacy_source_context_complete;
    DROP FUNCTION property_complete_graph_actor_access(uuid,uuid,uuid,text,boolean);
    ALTER FUNCTION property_source_graph_before_history(uuid,uuid,uuid,text,boolean) RENAME TO property_complete_graph_actor_access;
    """)
    for table in ('tenant_profile','owner_profile'):
        op.execute(f'DROP TRIGGER IF EXISTS protect_history_source_context ON {table}')
    for table in HISTORY:
        for action in ('read','insert','update','delete'):
            op.execute(f'DROP POLICY history_source_{action} ON {table}')
        op.execute(f'DROP TRIGGER a_lock_history_source ON {table}')
        op.execute(f'DROP TRIGGER protect_history_source_context ON {table}')
    op.execute('DROP FUNCTION property_history_row_access(uuid,text,jsonb,text),property_history_graph_valid(uuid,uuid),protect_history_source_context()')
