"""Keep meetings, register copies and exports within their source audience."""
from alembic import op

revision = '0066'
down_revision = '0065'
branch_labels = None
depends_on = None

CONTEXT = {
    'owners_meeting': ('property_id',),
    'agenda_item': ('meeting_id',),
    'resolution': ('meeting_id','agenda_item_id'),
    'resolution_register_entry': ('property_id','resolution_id'),
}
TABLES = ('property_source_security','property','audit_event') + tuple(CONTEXT)


def upgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""
    CREATE FUNCTION property_meeting_graph_valid(org uuid,property uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT NOT EXISTS(SELECT 1 FROM public.owners_meeting m WHERE m.property_id=property AND m.organization_id<>org)
        AND NOT EXISTS(SELECT 1 FROM public.agenda_item a JOIN public.owners_meeting m ON m.id=a.meeting_id
          WHERE m.property_id=property AND (m.organization_id<>org OR a.organization_id<>org))
        AND NOT EXISTS(SELECT 1 FROM public.resolution r JOIN public.owners_meeting m ON m.id=r.meeting_id
          LEFT JOIN public.agenda_item a ON a.id=r.agenda_item_id
          WHERE m.property_id=property AND (r.organization_id<>org OR m.organization_id<>org
            OR (r.agenda_item_id IS NOT NULL AND (a.id IS NULL OR a.organization_id<>org OR a.meeting_id<>m.id))))
        AND NOT EXISTS(SELECT 1 FROM public.resolution_register_entry e
          LEFT JOIN public.resolution r ON r.id=e.resolution_id LEFT JOIN public.owners_meeting m ON m.id=r.meeting_id
          WHERE (e.property_id=property OR m.property_id=property) AND
            (e.organization_id<>org OR r.id IS NULL OR r.organization_id<>org OR m.id IS NULL
              OR m.organization_id<>org OR e.property_id<>m.property_id))
    $$;
    REVOKE ALL ON FUNCTION property_meeting_graph_valid(uuid,uuid) FROM PUBLIC,app_rw;

    CREATE FUNCTION property_meeting_property_access(org uuid,property uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      IF property IS NULL OR capability IS NULL OR capability NOT IN ('read','prepare','approve','export') THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      RETURN public.property_source_actor_permitted(org,actor,'property',property,capability)
        AND public.property_complete_graph_actor_access(org,actor,property,'read',false)
        AND (capability<>'export' OR public.property_complete_graph_actor_access(org,actor,property,'export',false))
        AND public.property_meeting_graph_valid(org,property);
    END $$;

    CREATE FUNCTION property_meeting_row_access(org uuid,kind text,context jsonb,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; property uuid; meeting uuid; resolution uuid; declared_property uuid;
    BEGIN
      IF context->>'organization_id' IS DISTINCT FROM org::text OR context->>'id' IS NULL
        OR kind IS NULL OR kind NOT IN ('owners_meeting','agenda_item','resolution','resolution_register_entry')
        OR capability IS NULL OR capability NOT IN ('read','prepare','approve','export') THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      IF kind='owners_meeting' THEN
        property:=(context->>'property_id')::uuid;
      ELSIF kind IN ('agenda_item','resolution') THEN
        meeting:=(context->>'meeting_id')::uuid;
        SELECT m.property_id INTO property FROM public.owners_meeting m WHERE m.organization_id=org AND m.id=meeting;
        IF kind='resolution' AND context->>'agenda_item_id' IS NOT NULL AND NOT EXISTS(
          SELECT 1 FROM public.agenda_item a WHERE a.organization_id=org AND a.id=(context->>'agenda_item_id')::uuid AND a.meeting_id=meeting)
          THEN RETURN false; END IF;
      ELSE
        declared_property:=(context->>'property_id')::uuid;
        resolution:=(context->>'resolution_id')::uuid;
        SELECT m.property_id INTO property FROM public.resolution r JOIN public.owners_meeting m
          ON m.id=r.meeting_id AND m.organization_id=r.organization_id WHERE r.organization_id=org AND r.id=resolution;
        IF property IS DISTINCT FROM declared_property THEN RETURN false; END IF;
      END IF;
      RETURN public.property_meeting_property_access(org,property,capability);
    END $$;

    CREATE FUNCTION property_meeting_access(org uuid,kind text,resource uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE context jsonb;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN false; END IF;
      IF kind='owners_meeting' THEN
        SELECT jsonb_build_object('id',r.id,'organization_id',r.organization_id,'property_id',r.property_id) INTO context
          FROM public.owners_meeting r WHERE r.organization_id=org AND r.id=resource;
      ELSIF kind='agenda_item' THEN
        SELECT jsonb_build_object('id',r.id,'organization_id',r.organization_id,'meeting_id',r.meeting_id) INTO context
          FROM public.agenda_item r WHERE r.organization_id=org AND r.id=resource;
      ELSIF kind='resolution' THEN
        SELECT jsonb_build_object('id',r.id,'organization_id',r.organization_id,'meeting_id',r.meeting_id,'agenda_item_id',r.agenda_item_id) INTO context
          FROM public.resolution r WHERE r.organization_id=org AND r.id=resource;
      ELSIF kind='resolution_register_entry' THEN
        SELECT jsonb_build_object('id',r.id,'organization_id',r.organization_id,'property_id',r.property_id,'resolution_id',r.resolution_id) INTO context
          FROM public.resolution_register_entry r WHERE r.organization_id=org AND r.id=resource;
      ELSE RETURN false; END IF;
      RETURN context IS NOT NULL AND public.property_meeting_row_access(org,kind,context,capability);
    END $$;
    REVOKE ALL ON FUNCTION property_meeting_property_access(uuid,uuid,text),property_meeting_row_access(uuid,text,jsonb,text),property_meeting_access(uuid,text,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_meeting_property_access(uuid,uuid,text),property_meeting_row_access(uuid,text,jsonb,text),property_meeting_access(uuid,text,uuid,text) TO app_rw;

    CREATE FUNCTION protect_meeting_source_context() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE before jsonb:=to_jsonb(OLD); after jsonb:=to_jsonb(NEW); field text;
    BEGIN
      FOREACH field IN ARRAY ARRAY['organization_id','id','property_id','meeting_id','resolution_id'] LOOP
        IF before->field IS DISTINCT FROM after->field THEN
          RAISE EXCEPTION 'Meeting source context is immutable' USING ERRCODE='23514';
        END IF;
      END LOOP;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION protect_meeting_source_context() FROM PUBLIC,app_rw;
    """)
    for table,fields in CONTEXT.items():
        context='jsonb_build_object('+','.join(f"'{field}',{field}" for field in ('organization_id','id')+fields)+')'
        write='approve' if table=='resolution_register_entry' else 'prepare'
        read=f"property_meeting_row_access(organization_id,'{table}',{context},'read')"
        effect=f"property_meeting_row_access(organization_id,'{table}',{context},'{write}')"
        op.execute(f'CREATE POLICY meeting_source_read ON {table} AS RESTRICTIVE FOR SELECT USING ({read})')
        op.execute(f'CREATE POLICY meeting_source_insert ON {table} AS RESTRICTIVE FOR INSERT WITH CHECK ({effect})')
        op.execute(f'CREATE POLICY meeting_source_update ON {table} AS RESTRICTIVE FOR UPDATE USING ({effect}) WITH CHECK ({effect})')
        op.execute(f'CREATE POLICY meeting_source_delete ON {table} AS RESTRICTIVE FOR DELETE USING ({effect})')
        op.execute(f'CREATE TRIGGER a_lock_meeting_source BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION lock_strict_property_source_binding()')
        op.execute(f'CREATE TRIGGER protect_meeting_source_context BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_meeting_source_context()')
    op.execute("""
    ALTER FUNCTION property_source_audit_access(uuid,text,uuid) RENAME TO property_audit_before_meetings;
    CREATE FUNCTION property_source_audit_access(org uuid,kind text,resource uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog,pg_temp SET row_security=on AS $$
    BEGIN
      IF public.property_source_security_mode(org)='PREPARATION' THEN
        RETURN public.property_audit_before_meetings(org,kind,resource);
      END IF;
      IF kind IN ('owners_meeting','agenda_item','resolution','resolution_register_entry') THEN
        RETURN public.property_meeting_access(org,kind,resource,'read');
      END IF;
      RETURN public.property_audit_before_meetings(org,kind,resource);
    END $$;
    REVOKE ALL ON FUNCTION property_source_audit_access(uuid,text,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_source_audit_access(uuid,text,uuid) TO app_rw;
    -- Policies keep their referenced function OID on rename; switch explicitly.
    ALTER POLICY source_audit_read ON audit_event USING(property_source_audit_access(organization_id,entity_type,entity_id));
    """)


def downgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain strict meeting source access'; END IF; END $$;
    ALTER POLICY source_audit_read ON audit_event USING(property_audit_before_meetings(organization_id,entity_type,entity_id));
    DROP FUNCTION property_source_audit_access(uuid,text,uuid);
    ALTER FUNCTION property_audit_before_meetings(uuid,text,uuid) RENAME TO property_source_audit_access;
    """)
    for table in CONTEXT:
        for action in ('read','insert','update','delete'):
            op.execute(f'DROP POLICY meeting_source_{action} ON {table}')
        op.execute(f'DROP TRIGGER a_lock_meeting_source ON {table}')
        op.execute(f'DROP TRIGGER protect_meeting_source_context ON {table}')
    op.execute('DROP FUNCTION property_meeting_access(uuid,text,uuid,text),property_meeting_row_access(uuid,text,jsonb,text),property_meeting_property_access(uuid,uuid,text),property_meeting_graph_valid(uuid,uuid),protect_meeting_source_context()')
