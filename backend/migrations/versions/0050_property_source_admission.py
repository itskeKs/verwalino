"""Capture durable authorization when an original enters a property audience.

Revision ID: 0050
Revises: 0049
"""
from alembic import op

revision='0050'
down_revision='0049'
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
            OR (destination IS NOT NULL AND NOT public.property_data_property_actor_access(
              org,d.uploaded_by,destination,'prepare'))))));
    END $$;
"""

IMPORT_ACCESS = """
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


def upgrade():
    op.execute("""
    LOCK TABLE property_data_domain,property_data_grant,declaration_import_document,property_document
      IN ACCESS EXCLUSIVE MODE NOWAIT;
    ALTER TABLE property_data_domain ADD CONSTRAINT uq_property_data_domain_context UNIQUE(organization_id,property_id,id);
    ALTER TABLE property_data_grant ADD CONSTRAINT uq_property_data_grant_actor_context UNIQUE(organization_id,domain_id,user_id,id);
    CREATE TABLE property_source_admission (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL,
      import_id uuid NOT NULL, document_id uuid NOT NULL, property_id uuid NOT NULL,
      domain_id uuid NOT NULL, grant_id uuid NOT NULL, admitted_by uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CONSTRAINT uq_property_source_admission_document UNIQUE(organization_id,document_id),
      CONSTRAINT fk_property_source_admission_link FOREIGN KEY(organization_id,import_id,document_id)
        REFERENCES declaration_import_document(organization_id,import_id,document_id),
      CONSTRAINT fk_property_source_admission_domain FOREIGN KEY(organization_id,property_id,domain_id)
        REFERENCES property_data_domain(organization_id,property_id,id),
      CONSTRAINT fk_property_source_admission_grant FOREIGN KEY(organization_id,domain_id,admitted_by,grant_id)
        REFERENCES property_data_grant(organization_id,domain_id,user_id,id)
    );
    CREATE INDEX ix_property_source_admission_import ON property_source_admission(organization_id,import_id);
    ALTER TABLE property_source_admission ENABLE ROW LEVEL SECURITY;
    ALTER TABLE property_source_admission FORCE ROW LEVEL SECURITY;
    CREATE POLICY property_source_admission_read ON property_source_admission FOR SELECT USING (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND property_source_access(organization_id,'property_document',document_id,'read'));
    CREATE POLICY property_source_admission_insert ON property_source_admission FOR INSERT WITH CHECK(false);
    REVOKE ALL ON property_source_admission FROM PUBLIC,app_rw;
    GRANT SELECT ON property_source_admission TO app_rw;

    CREATE FUNCTION lock_property_source_admission() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$ BEGIN
      IF NEW.organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER a_lock_property_source_admission BEFORE INSERT ON declaration_import_document
      FOR EACH ROW EXECUTE FUNCTION lock_property_source_admission();

    CREATE FUNCTION capture_property_source_admission() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      target uuid; domain uuid; permitted_grant uuid;
    BEGIN
      IF NOT COALESCE(public.property_source_actor_role(NEW.organization_id,actor) IN ('OWNER_ADMIN','CLERK'),false) THEN RETURN NEW; END IF;
      SELECT i.target_property_id INTO target FROM public.declaration_import i
        JOIN public.property_document d ON d.organization_id=i.organization_id AND d.id=NEW.document_id
        WHERE i.organization_id=NEW.organization_id AND i.id=NEW.import_id
          AND i.mode='EXISTING_PROPERTY' AND i.status='DRAFT' AND i.applied_property_id IS NULL
          AND d.property_id IS NULL AND d.archived_at IS NULL AND d.uploaded_by=actor;
      IF NOT FOUND OR target IS NULL THEN RETURN NEW; END IF;
      IF (SELECT count(*) FROM public.declaration_import_document l
          WHERE l.organization_id=NEW.organization_id AND l.document_id=NEW.document_id)<>1 THEN RETURN NEW; END IF;
      SELECT d.id,g.id INTO domain,permitted_grant FROM public.property_data_domain d
        JOIN public.property_data_grant g ON g.organization_id=d.organization_id AND g.domain_id=d.id
        WHERE d.organization_id=NEW.organization_id AND d.property_id=target AND g.user_id=actor
          AND 'prepare'=ANY(g.capabilities) AND g.revoked_at IS NULL
          AND (g.expires_at IS NULL OR g.expires_at>clock_timestamp());
      IF NOT FOUND THEN RETURN NEW; END IF;
      -- A preparation-mode legacy upload without a grant retains compatibility
      -- but receives no admission proof. Never backfill one from later grants.
      INSERT INTO public.property_source_admission(organization_id,import_id,document_id,property_id,domain_id,grant_id,admitted_by)
        VALUES(NEW.organization_id,NEW.import_id,NEW.document_id,target,domain,permitted_grant,actor);
      RETURN NEW;
    END $$;
    CREATE TRIGGER capture_property_source_admission AFTER INSERT ON declaration_import_document
      FOR EACH ROW EXECUTE FUNCTION capture_property_source_admission();

    CREATE FUNCTION protect_property_source_admission() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$ BEGIN
      IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Source admission evidence is immutable'; END IF;
      -- Only the attachment AFTER trigger may capture fresh admission evidence.
      -- A later grant cannot turn a direct receipt insert into historical proof.
      IF pg_trigger_depth()<>2
        OR NEW.admitted_by IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid
        OR NOT COALESCE(public.property_source_actor_role(NEW.organization_id,NEW.admitted_by) IN ('OWNER_ADMIN','CLERK'),false)
        OR NOT EXISTS(SELECT 1 FROM public.property_data_grant g WHERE g.organization_id=NEW.organization_id
          AND g.domain_id=NEW.domain_id AND g.id=NEW.grant_id AND g.user_id=NEW.admitted_by
          AND 'prepare'=ANY(g.capabilities) AND g.revoked_at IS NULL
          AND (g.expires_at IS NULL OR g.expires_at>clock_timestamp()))
        OR NOT EXISTS(SELECT 1 FROM public.declaration_import i JOIN public.property_document d
          ON d.organization_id=i.organization_id AND d.id=NEW.document_id
          WHERE i.organization_id=NEW.organization_id AND i.id=NEW.import_id
            AND i.mode='EXISTING_PROPERTY' AND i.status='DRAFT' AND i.target_property_id=NEW.property_id
            AND i.applied_property_id IS NULL AND d.uploaded_by=NEW.admitted_by
            AND d.property_id IS NULL AND d.archived_at IS NULL)
        OR (SELECT count(*) FROM public.declaration_import_document l
          WHERE l.organization_id=NEW.organization_id AND l.document_id=NEW.document_id)<>1 THEN
        RAISE EXCEPTION 'Source admission requires current authorized attachment' USING ERRCODE='42501';
      END IF;
      NEW.created_at:=clock_timestamp();
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(NEW.organization_id,NEW.admitted_by,'property_document',NEW.document_id,'property_document.admitted',
          jsonb_build_object('import_id',NEW.import_id,'property_id',NEW.property_id,'domain_id',NEW.domain_id,'grant_id',NEW.grant_id));
      RETURN NEW;
    END $$;
    CREATE TRIGGER protect_property_source_admission BEFORE INSERT OR UPDATE OR DELETE ON property_source_admission
      FOR EACH ROW EXECUTE FUNCTION protect_property_source_admission();

    CREATE FUNCTION guard_property_source_admission_binding() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$ BEGIN
      IF NEW.property_id IS NOT NULL AND EXISTS(SELECT 1 FROM public.property_source_admission a
        WHERE a.organization_id=NEW.organization_id AND a.document_id=NEW.id AND a.property_id<>NEW.property_id) THEN
        RAISE EXCEPTION 'Original binding differs from its admitted property'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER guard_property_source_admission_binding BEFORE UPDATE OF property_id ON property_document
      FOR EACH ROW EXECUTE FUNCTION guard_property_source_admission_binding();
    REVOKE ALL ON FUNCTION lock_property_source_admission(),capture_property_source_admission(),
      protect_property_source_admission(),guard_property_source_admission_binding() FROM PUBLIC,app_rw;
    """)
    op.execute(IMPORT_ACCESS)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_admission) THEN
      RAISE EXCEPTION 'Retain populated source admission evidence'; END IF; END $$""")
    op.execute(PREVIOUS_IMPORT_ACCESS)
    op.execute("""
      DROP TRIGGER guard_property_source_admission_binding ON property_document;
      DROP TRIGGER capture_property_source_admission ON declaration_import_document;
      DROP TRIGGER a_lock_property_source_admission ON declaration_import_document;
      DROP TABLE property_source_admission;
      DROP FUNCTION lock_property_source_admission(),capture_property_source_admission(),
        protect_property_source_admission(),guard_property_source_admission_binding();
      ALTER TABLE property_data_grant DROP CONSTRAINT uq_property_data_grant_actor_context;
      ALTER TABLE property_data_domain DROP CONSTRAINT uq_property_data_domain_context;
    """)
