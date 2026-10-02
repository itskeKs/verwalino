"""Record explicit new-property publication authority without activating source RLS.

Revision ID: 0051
Revises: 0050
"""
from alembic import op

revision='0051'
down_revision='0050'
branch_labels=None
depends_on=None

PREVIOUS_IMPORT_ACCESS = """
CREATE OR REPLACE FUNCTION property_source_import_actor_access(org uuid, actor uuid, job uuid, capability text) RETURNS boolean
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
            OR (destination IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.property_source_admission a
              WHERE a.organization_id=org AND a.import_id=job AND a.document_id=d.id AND a.property_id=destination))))));
    END $$;
"""

IMPORT_ACCESS = """
CREATE OR REPLACE FUNCTION property_source_import_actor_access(org uuid, actor uuid, job uuid, capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE role text; destination uuid; creator uuid; mode text; publication uuid; allowed boolean;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','prepare','approve','export') THEN RETURN false; END IF;
      role:=public.property_source_actor_role(org,actor);
      IF role IS NULL THEN RETURN false; END IF;
      SELECT COALESCE(i.applied_property_id,i.target_property_id),i.initiated_by,i.mode,i.publication_grant_id
        INTO destination,creator,mode,publication FROM public.declaration_import i WHERE i.organization_id=org AND i.id=job;
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
            OR (destination IS NOT NULL AND NOT (
              (mode='NEW_PROPERTY' AND publication IS NOT NULL AND d.uploaded_by=creator)
              OR EXISTS(SELECT 1 FROM public.property_source_admission a
                WHERE a.organization_id=org AND a.import_id=job AND a.document_id=d.id AND a.property_id=destination)))))));
    END $$;
"""


def upgrade():
    op.execute("""
    LOCK TABLE declaration_import, declaration_import_document, property_data_domain, property_data_grant
      IN ACCESS EXCLUSIVE MODE NOWAIT;
    ALTER TABLE declaration_import ADD COLUMN approval_request_digest varchar(64),
      ADD COLUMN publication_domain_id uuid, ADD COLUMN publication_grant_id uuid,
      ADD CONSTRAINT fk_declaration_publication_domain FOREIGN KEY(organization_id,applied_property_id,publication_domain_id)
        REFERENCES property_data_domain(organization_id,property_id,id),
      ADD CONSTRAINT fk_declaration_publication_grant FOREIGN KEY(organization_id,publication_domain_id,approved_by,publication_grant_id)
        REFERENCES property_data_grant(organization_id,domain_id,user_id,id),
      ADD CONSTRAINT ck_declaration_publication_digest CHECK(approval_request_digest IS NULL OR
        (status='APPLIED' AND approval_request_digest ~ '^[0-9a-f]{64}$')),
      ADD CONSTRAINT ck_declaration_publication_context CHECK(
        (publication_domain_id IS NULL AND publication_grant_id IS NULL) OR
        (publication_domain_id IS NOT NULL AND publication_grant_id IS NOT NULL AND mode='NEW_PROPERTY'
          AND status='APPLIED' AND applied_property_id IS NOT NULL AND approved_by IS NOT NULL
          AND approved_at IS NOT NULL AND approval_request_digest IS NOT NULL));

    CREATE FUNCTION lock_declaration_publication_audience() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE org uuid:=NULLIF(current_setting('app.current_org_id',true),'')::uuid;
    BEGIN
      IF org IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||org::text,0));
      END IF;
      RETURN NULL;
    END $$;
    CREATE TRIGGER a_lock_declaration_publication_audience BEFORE UPDATE OF publication_domain_id,publication_grant_id
      ON declaration_import FOR EACH STATEMENT EXECUTE FUNCTION lock_declaration_publication_audience();

    CREATE FUNCTION guard_declaration_publication_audience() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      IF TG_OP='UPDATE' AND OLD.status='APPLIED' AND
        ROW(NEW.approval_request_digest,NEW.publication_domain_id,NEW.publication_grant_id) IS DISTINCT FROM
        ROW(OLD.approval_request_digest,OLD.publication_domain_id,OLD.publication_grant_id) THEN
        RAISE EXCEPTION 'Publication audience evidence is immutable';
      END IF;
      IF NEW.publication_domain_id IS NULL THEN RETURN NEW; END IF;
      IF TG_OP='UPDATE' AND OLD.status='APPLIED' THEN RETURN NEW; END IF;
      IF TG_OP<>'UPDATE' THEN RAISE EXCEPTION 'Publication audience requires a reviewed transition'; END IF;
      IF OLD.status<>'NEEDS_REVIEW' OR NEW.mode<>'NEW_PROPERTY' OR NEW.status<>'APPLIED'
        OR OLD.initiated_by IS DISTINCT FROM actor OR NEW.approved_by IS DISTINCT FROM actor
        OR public.property_source_actor_role(NEW.organization_id,actor) IS DISTINCT FROM 'OWNER_ADMIN'
        OR NOT EXISTS(SELECT 1 FROM public.property_data_domain d JOIN public.property_data_grant g
          ON g.organization_id=d.organization_id AND g.domain_id=d.id
          WHERE d.organization_id=NEW.organization_id AND d.id=NEW.publication_domain_id
            AND d.property_id=NEW.applied_property_id AND g.id=NEW.publication_grant_id AND g.user_id=actor
            AND ARRAY['read','prepare','approve']::varchar[] <@ g.capabilities AND g.revoked_at IS NULL
            AND (g.expires_at IS NULL OR g.expires_at>clock_timestamp()))
        OR NOT EXISTS(SELECT 1 FROM public.declaration_import_document l WHERE l.organization_id=NEW.organization_id AND l.import_id=NEW.id)
        OR EXISTS(SELECT 1 FROM public.declaration_import_document l JOIN public.property_document d
          ON d.organization_id=l.organization_id AND d.id=l.document_id
          WHERE l.organization_id=NEW.organization_id AND l.import_id=NEW.id AND
            (d.uploaded_by IS DISTINCT FROM actor OR d.property_id IS NOT NULL OR d.archived_at IS NOT NULL
             OR (SELECT count(*) FROM public.declaration_import_document other
                 WHERE other.organization_id=NEW.organization_id AND other.document_id=d.id)<>1)) THEN
        RAISE EXCEPTION 'Publication audience requires current creator and destination authority' USING ERRCODE='42501';
      END IF;
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(NEW.organization_id,actor,'declaration_import',NEW.id,'declaration_import.audience_published',
          jsonb_build_object('property_id',NEW.applied_property_id,'domain_id',NEW.publication_domain_id,
            'grant_id',NEW.publication_grant_id,'request_digest',NEW.approval_request_digest));
      RETURN NEW;
    END $$;
    CREATE TRIGGER guard_declaration_publication_audience BEFORE INSERT OR UPDATE ON declaration_import
      FOR EACH ROW EXECUTE FUNCTION guard_declaration_publication_audience();

    CREATE FUNCTION guard_published_source_links() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE context record;
    BEGIN
      IF TG_OP='DELETE' THEN context:=OLD; ELSE context:=NEW; END IF;
      IF EXISTS(SELECT 1 FROM public.declaration_import i WHERE i.organization_id=context.organization_id
          AND i.publication_domain_id IS NOT NULL AND (i.id=context.import_id
            OR EXISTS(SELECT 1 FROM public.declaration_import_document l
              WHERE l.organization_id=i.organization_id AND l.import_id=i.id AND l.document_id=context.document_id))) THEN
        RAISE EXCEPTION 'Published source links are immutable';
      END IF;
      RETURN context;
    END $$;
    CREATE TRIGGER guard_published_source_links BEFORE INSERT OR UPDATE OR DELETE ON declaration_import_document
      FOR EACH ROW EXECUTE FUNCTION guard_published_source_links();
    REVOKE ALL ON FUNCTION lock_declaration_publication_audience(),guard_declaration_publication_audience(),guard_published_source_links() FROM PUBLIC,app_rw;
    """)
    op.execute(IMPORT_ACCESS)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM declaration_import WHERE approval_request_digest IS NOT NULL
      OR publication_domain_id IS NOT NULL OR publication_grant_id IS NOT NULL) THEN
      RAISE EXCEPTION 'Retain populated publication audience evidence'; END IF; END $$""")
    op.execute(PREVIOUS_IMPORT_ACCESS)
    op.execute("""
      DROP TRIGGER guard_published_source_links ON declaration_import_document;
      DROP TRIGGER guard_declaration_publication_audience ON declaration_import;
      DROP TRIGGER a_lock_declaration_publication_audience ON declaration_import;
      DROP FUNCTION guard_published_source_links(),guard_declaration_publication_audience(),lock_declaration_publication_audience();
      ALTER TABLE declaration_import DROP CONSTRAINT fk_declaration_publication_domain,
        DROP CONSTRAINT fk_declaration_publication_grant, DROP CONSTRAINT ck_declaration_publication_digest,
        DROP CONSTRAINT ck_declaration_publication_context, DROP COLUMN approval_request_digest,
        DROP COLUMN publication_domain_id, DROP COLUMN publication_grant_id;
    """)
