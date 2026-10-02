"""Prepare one source-audience resolver; do not activate source RLS.

Revision ID: 0049
Revises: 0048
"""
from alembic import op

revision='0049'
down_revision='0048'
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
    CREATE FUNCTION property_source_actor_role(org uuid, actor uuid) RETURNS text
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN NULL; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      RETURN (SELECT m.role::text FROM public.membership m JOIN public."user" u
        ON u.organization_id=m.organization_id AND u.id=m.user_id
        WHERE m.organization_id=org AND m.user_id=actor AND u.is_active);
    END $$;

    CREATE FUNCTION property_data_domain_actor_access(org uuid, actor uuid, domain uuid, capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE role text;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','prepare','approve','export') THEN RETURN false; END IF;
      role:=public.property_source_actor_role(org,actor);
      RETURN COALESCE(role IS NOT NULL AND (capability IN ('read','export') OR role IN ('OWNER_ADMIN','CLERK'))
        AND EXISTS(SELECT 1 FROM public.property_data_grant g WHERE g.organization_id=org AND g.domain_id=domain
          AND g.user_id=actor AND capability=ANY(g.capabilities) AND g.revoked_at IS NULL
          AND (g.expires_at IS NULL OR g.expires_at>clock_timestamp())),false);
    END $$;

    CREATE OR REPLACE FUNCTION has_property_data_capability(org uuid, domain uuid, capability text) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT public.property_data_domain_actor_access(org,
        NULLIF(current_setting('app.current_user_id',true),'')::uuid,domain,capability)
    $$;

    CREATE FUNCTION property_data_property_actor_access(org uuid, actor uuid, property uuid, capability text) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT COALESCE((SELECT public.property_data_domain_actor_access(org,actor,d.id,capability)
        FROM public.property_data_domain d WHERE d.organization_id=org AND d.property_id=property),false)
    $$;

    CREATE FUNCTION property_source_import_actor_access(org uuid, actor uuid, job uuid, capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE role text; destination uuid; creator uuid; mode text; allowed boolean;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','prepare','approve','export') THEN RETURN false; END IF;
      role:=public.property_source_actor_role(org,actor);
      IF role IS NULL THEN RETURN false; END IF;
      SELECT COALESCE(i.applied_property_id,i.target_property_id),i.initiated_by,i.mode
        INTO destination,creator,mode FROM public.declaration_import i WHERE i.organization_id=org AND i.id=job;
      IF NOT FOUND THEN RETURN false; END IF;
      IF destination IS NULL THEN
        allowed:=mode='NEW_PROPERTY' AND creator=actor AND capability IN ('read','prepare','export')
          AND (capability<>'prepare' OR role IN ('OWNER_ADMIN','CLERK'));
      ELSE
        allowed:=public.property_data_property_actor_access(org,actor,destination,capability);
      END IF;
      IF NOT COALESCE(allowed,false) THEN RETURN false; END IF;
      -- No recursive call to document access: inspect only immutable local
      -- audience metadata and exact link topology, including archived sources.
      RETURN NOT EXISTS(SELECT 1 FROM public.declaration_import_document l
        JOIN public.property_document d ON d.organization_id=l.organization_id AND d.id=l.document_id
        WHERE l.organization_id=org AND l.import_id=job AND (
          (SELECT count(*) FROM public.declaration_import_document other
            WHERE other.organization_id=org AND other.document_id=d.id)<>1
          OR (d.property_id IS NOT NULL AND d.property_id IS DISTINCT FROM destination)
          OR (d.property_id IS NULL AND (d.uploaded_by IS NULL
            OR (destination IS NULL AND d.uploaded_by IS DISTINCT FROM creator)
            OR (destination IS NOT NULL AND NOT public.property_data_property_actor_access(
              org,d.uploaded_by,destination,'prepare'))))));
    END $$;

    CREATE FUNCTION property_source_document_actor_access(org uuid, actor uuid, document uuid, capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE role text; property uuid; creator uuid; job uuid; links bigint;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','prepare','approve','export') THEN RETURN false; END IF;
      role:=public.property_source_actor_role(org,actor);
      IF role IS NULL THEN RETURN false; END IF;
      SELECT d.property_id,d.uploaded_by INTO property,creator FROM public.property_document d
        WHERE d.organization_id=org AND d.id=document;
      IF NOT FOUND THEN RETURN false; END IF;
      SELECT count(*) INTO links FROM public.declaration_import_document l WHERE l.organization_id=org AND l.document_id=document;
      IF links>1 THEN RETURN false; END IF;
      IF links=1 THEN
        SELECT l.import_id INTO job FROM public.declaration_import_document l WHERE l.organization_id=org AND l.document_id=document;
        RETURN public.property_source_import_actor_access(org,actor,job,capability);
      ELSIF property IS NOT NULL THEN
        RETURN public.property_data_property_actor_access(org,actor,property,capability);
      END IF;
      RETURN COALESCE(creator=actor AND capability IN ('read','prepare','export')
        AND (capability<>'prepare' OR role IN ('OWNER_ADMIN','CLERK')),false);
    END $$;

    CREATE FUNCTION property_source_actor_access(org uuid, actor uuid, kind text, resource uuid, capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE property uuid; job uuid; document uuid; rule uuid; other uuid; subject uuid; subject_kind text;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','prepare','approve','export')
        OR public.property_source_actor_role(org,actor) IS NULL THEN RETURN false; END IF;
      CASE kind
        WHEN 'property' THEN
          RETURN public.property_data_property_actor_access(org,actor,resource,capability);
        WHEN 'property_document' THEN
          RETURN public.property_source_document_actor_access(org,actor,resource,capability);
        WHEN 'declaration_import' THEN
          RETURN public.property_source_import_actor_access(org,actor,resource,capability);
        WHEN 'unit' THEN SELECT u.property_id INTO property FROM public.unit u WHERE u.organization_id=org AND u.id=resource;
        WHEN 'declaration_import_document' THEN SELECT l.import_id INTO job FROM public.declaration_import_document l WHERE l.organization_id=org AND l.id=resource;
        WHEN 'declaration_candidate' THEN SELECT c.import_id INTO job FROM public.declaration_candidate c WHERE c.organization_id=org AND c.id=resource;
        WHEN 'declaration_candidate_evidence' THEN SELECT e.import_id INTO job FROM public.declaration_candidate_evidence e WHERE e.organization_id=org AND e.id=resource;
        WHEN 'declaration_candidate_issue' THEN SELECT i.import_id INTO job FROM public.declaration_candidate_issue i WHERE i.organization_id=org AND i.id=resource;
        WHEN 'declaration_extraction_run' THEN SELECT r.import_id INTO job FROM public.declaration_extraction_run r WHERE r.organization_id=org AND r.id=resource;
        WHEN 'declaration_queue_request' THEN SELECT q.import_id INTO job FROM public.declaration_queue_request q WHERE q.organization_id=org AND q.id=resource;
        WHEN 'declaration_import_pass' THEN SELECT r.import_id INTO job FROM public.declaration_import_pass p
          JOIN public.declaration_extraction_run r ON r.organization_id=p.organization_id AND r.id=p.declaration_extraction_run_id
          WHERE p.organization_id=org AND p.id=resource;
        WHEN 'property_knowledge_query_run' THEN SELECT q.property_id INTO property FROM public.property_knowledge_query_run q WHERE q.organization_id=org AND q.id=resource;
        WHEN 'property_legal_reference' THEN SELECT l.property_id,l.source_import_id INTO property,job FROM public.property_legal_reference l WHERE l.organization_id=org AND l.id=resource;
        WHEN 'property_assignment' THEN SELECT a.property_id,a.source_import_id INTO property,job FROM public.property_assignment a WHERE a.organization_id=org AND a.id=resource;
        WHEN 'approved_fact_source' THEN
          SELECT f.property_id,f.source_import_id,f.property_document_id,f.subject_id,f.subject_kind
            INTO property,job,document,subject,subject_kind FROM public.approved_fact_source f WHERE f.organization_id=org AND f.id=resource;
          IF NOT FOUND OR NOT ((subject_kind='PROPERTY' AND subject=property) OR (subject_kind='UNIT' AND EXISTS(
            SELECT 1 FROM public.unit u WHERE u.organization_id=org AND u.property_id=property AND u.id=subject))) THEN RETURN false; END IF;
        WHEN 'property_rule' THEN
          SELECT r.property_id,r.source_import_id INTO property,job FROM public.property_rule r WHERE r.organization_id=org AND r.id=resource;
          IF EXISTS(SELECT 1 FROM public.property_rule_source s WHERE s.organization_id=org AND s.property_rule_id=resource
            AND NOT public.property_source_document_actor_access(org,actor,s.property_document_id,capability)) THEN RETURN false; END IF;
        WHEN 'property_rule_source' THEN
          SELECT s.property_id,s.property_document_id,s.property_rule_id INTO property,document,rule FROM public.property_rule_source s WHERE s.organization_id=org AND s.id=resource;
          IF NOT public.property_source_actor_access(org,actor,'property_rule',rule,capability) THEN RETURN false; END IF;
        WHEN 'property_rule_relation' THEN
          SELECT r.property_id,r.source_import_id,r.source_rule_id,r.target_rule_id INTO property,job,rule,other FROM public.property_rule_relation r WHERE r.organization_id=org AND r.id=resource;
          IF NOT public.property_source_actor_access(org,actor,'property_rule',rule,capability)
            OR NOT public.property_source_actor_access(org,actor,'property_rule',other,capability) THEN RETURN false; END IF;
        WHEN 'property_document_relation' THEN
          SELECT r.property_id,r.source_import_id,r.source_document_id,r.target_document_id INTO property,job,document,other FROM public.property_document_relation r WHERE r.organization_id=org AND r.id=resource;
          IF NOT public.property_source_document_actor_access(org,actor,other,capability) THEN RETURN false; END IF;
        ELSE RETURN false;
      END CASE;
      IF property IS NULL AND job IS NULL THEN RETURN false; END IF;
      IF property IS NOT NULL AND NOT public.property_data_property_actor_access(org,actor,property,capability) THEN RETURN false; END IF;
      IF job IS NOT NULL THEN
        -- Derived rows may never publish a different import audience, even when
        -- the current actor happens to hold both grants.
        IF property IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.declaration_import i WHERE i.organization_id=org AND i.id=job
          AND COALESCE(i.applied_property_id,i.target_property_id)=property) THEN RETURN false; END IF;
        IF NOT public.property_source_import_actor_access(org,actor,job,capability) THEN RETURN false; END IF;
      END IF;
      IF document IS NOT NULL AND NOT public.property_source_document_actor_access(org,actor,document,capability) THEN RETURN false; END IF;
      RETURN true;
    END $$;

    CREATE FUNCTION property_source_access(org uuid, kind text, resource uuid, capability text) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT public.property_source_actor_access(org,NULLIF(current_setting('app.current_user_id',true),'')::uuid,kind,resource,capability)
    $$;

    REVOKE ALL ON FUNCTION property_source_actor_role(uuid,uuid),property_data_domain_actor_access(uuid,uuid,uuid,text),
      property_data_property_actor_access(uuid,uuid,uuid,text),property_source_import_actor_access(uuid,uuid,uuid,text),
      property_source_document_actor_access(uuid,uuid,uuid,text),property_source_actor_access(uuid,uuid,text,uuid,text)
      FROM PUBLIC,app_rw;
    REVOKE ALL ON FUNCTION property_source_access(uuid,text,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_source_access(uuid,text,uuid,text) TO app_rw;
    """)


def downgrade():
    # No row, grant or policy is added by this preparation migration. Restore the
    # prior live-domain helper before removing only the unactivated resolvers.
    op.execute("""
    CREATE OR REPLACE FUNCTION has_property_data_capability(org uuid, domain uuid, capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE role text;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR capability NOT IN ('read','prepare','approve','export') THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      role:=public.scope_actor_role(org);
      RETURN COALESCE(role IS NOT NULL AND (capability IN ('read','export') OR role IN ('OWNER_ADMIN','CLERK'))
        AND EXISTS(SELECT 1 FROM public.property_data_grant g WHERE g.organization_id=org AND g.domain_id=domain
          AND g.user_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid AND capability=ANY(g.capabilities)
          AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at>clock_timestamp())),false);
    END $$;
    DROP FUNCTION property_source_access(uuid,text,uuid,text),property_source_actor_access(uuid,uuid,text,uuid,text),
      property_source_document_actor_access(uuid,uuid,uuid,text),property_source_import_actor_access(uuid,uuid,uuid,text),
      property_data_property_actor_access(uuid,uuid,uuid,text),property_data_domain_actor_access(uuid,uuid,uuid,text),
      property_source_actor_role(uuid,uuid);
    """)
