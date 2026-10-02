"""Require proof and current approval for strict original publication.

Revision ID: 0055
Revises: 0054
"""
from alembic import op

revision='0055'
down_revision='0054'
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
    LOCK TABLE property_document,declaration_import_document IN ACCESS EXCLUSIVE MODE NOWAIT;
    CREATE FUNCTION lock_strict_property_source_binding() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE org uuid:=NULLIF(current_setting('app.current_org_id',true),'')::uuid;
    BEGIN
      IF org IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      END IF;
      RETURN NULL;
    END $$;
    CREATE TRIGGER a_lock_strict_property_source_binding BEFORE INSERT OR UPDATE OF property_id ON property_document
      FOR EACH STATEMENT EXECUTE FUNCTION lock_strict_property_source_binding();
    CREATE TRIGGER a_lock_strict_property_source_link BEFORE INSERT ON declaration_import_document
      FOR EACH STATEMENT EXECUTE FUNCTION lock_strict_property_source_binding();

    CREATE FUNCTION guard_strict_property_source_binding() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; job uuid;
    BEGIN
      -- Read actual row organization state even for trusted legacy maintenance
      -- without GUCs. row_security=off prevents an accidental compatibility fallback.
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
      SELECT s.mode INTO mode FROM public.property_source_security s WHERE s.organization_id=NEW.organization_id;
      IF COALESCE(mode,'PREPARATION')='PREPARATION' THEN RETURN NEW; END IF;
      IF NEW.property_id IS NULL OR (TG_OP='UPDATE' AND OLD.property_id IS NOT DISTINCT FROM NEW.property_id) THEN RETURN NEW; END IF;
      IF TG_OP='INSERT' OR mode<>'ENFORCED'
        OR NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR NOT public.property_source_actor_permitted(NEW.organization_id,actor,'property',NEW.property_id,'approve')
        OR NOT public.property_source_binding_proven(NEW.organization_id,NEW.id,NEW.property_id) THEN
        RAISE EXCEPTION 'Original binding requires proven audience and current approval' USING ERRCODE='42501'; END IF;
      SELECT i.id INTO job FROM public.declaration_import i JOIN public.declaration_import_document l
        ON l.organization_id=i.organization_id AND l.import_id=i.id
        WHERE l.organization_id=NEW.organization_id AND l.document_id=NEW.id AND i.status='APPLIED'
          AND i.applied_property_id=NEW.property_id AND i.approved_by=actor AND i.approved_at IS NOT NULL;
      IF NOT FOUND THEN RAISE EXCEPTION 'Original binding requires applied approval receipt' USING ERRCODE='42501'; END IF;
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(NEW.organization_id,actor,'property_document',NEW.id,'property_document.binding_approved',
          jsonb_build_object('property_id',NEW.property_id,'import_id',job));
      RETURN NEW;
    END $$;
    CREATE TRIGGER guard_strict_property_source_binding BEFORE INSERT OR UPDATE OF property_id ON property_document
      FOR EACH ROW EXECUTE FUNCTION guard_strict_property_source_binding();

    CREATE FUNCTION guard_strict_property_source_link() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; document public.property_document;
    BEGIN
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
      SELECT s.mode INTO mode FROM public.property_source_security s WHERE s.organization_id=NEW.organization_id;
      IF COALESCE(mode,'PREPARATION')='PREPARATION' THEN RETURN NEW; END IF;
      SELECT * INTO document FROM public.property_document d WHERE d.organization_id=NEW.organization_id AND d.id=NEW.document_id FOR UPDATE;
      IF NOT FOUND OR document.property_id IS NOT NULL OR EXISTS(SELECT 1 FROM public.declaration_import_document l
          WHERE l.organization_id=NEW.organization_id AND l.document_id=NEW.document_id) THEN
        RAISE EXCEPTION 'Original requires a single unbound import link' USING ERRCODE='42501'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER guard_strict_property_source_link BEFORE INSERT ON declaration_import_document
      FOR EACH ROW EXECUTE FUNCTION guard_strict_property_source_link();
    REVOKE ALL ON FUNCTION lock_strict_property_source_binding(),guard_strict_property_source_binding(),
      guard_strict_property_source_link() FROM PUBLIC,app_rw;
    """)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain enforced original binding boundary'; END IF; END $$;
      DROP TRIGGER guard_strict_property_source_link ON declaration_import_document;
      DROP TRIGGER guard_strict_property_source_binding ON property_document;
      DROP TRIGGER a_lock_strict_property_source_link ON declaration_import_document;
      DROP TRIGGER a_lock_strict_property_source_binding ON property_document;
      DROP FUNCTION guard_strict_property_source_link(),guard_strict_property_source_binding(),lock_strict_property_source_binding();
    """)
