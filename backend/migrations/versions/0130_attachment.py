"""Retained general attachments with source-bound access and one-way archives."""
from alembic import op

revision = "0130"
down_revision = "0129"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE attachment (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        organization_id uuid NOT NULL REFERENCES organization(id) ON DELETE RESTRICT,
        property_id uuid NOT NULL REFERENCES property(id) ON DELETE RESTRICT,
        unit_id uuid REFERENCES unit(id) ON DELETE RESTRICT,
        lease_agreement_id uuid REFERENCES lease_agreement(id) ON DELETE RESTRICT,
        person_id uuid REFERENCES person(id) ON DELETE RESTRICT,
        title varchar(255) NOT NULL DEFAULT '',
        original_filename varchar(255) NOT NULL,
        mime_type varchar(100) NOT NULL,
        content_byte_size bigint NOT NULL CHECK(content_byte_size BETWEEN 1 AND 26214400),
        content_sha256 varchar(64) NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
        storage_backend varchar(50) NOT NULL,
        storage_uri varchar(1000) NOT NULL,
        uploaded_by uuid NOT NULL REFERENCES "user"(id) ON DELETE RESTRICT,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        archived_at timestamptz,
        archived_by uuid REFERENCES "user"(id) ON DELETE RESTRICT,
        archive_reason text,
        CONSTRAINT ck_attachment_storage_key CHECK(storage_uri = organization_id::text || '/' ||
            left(content_sha256,2) || '/' || content_sha256 || '.bin'),
        CONSTRAINT ck_attachment_archive CHECK(
            (archived_at IS NULL AND archived_by IS NULL AND archive_reason IS NULL) OR
            (archived_at IS NOT NULL AND archived_by IS NOT NULL AND
             archive_reason IS NOT NULL AND length(btrim(archive_reason)) BETWEEN 1 AND 2000))
    );
    CREATE INDEX ix_attachment_org_property ON attachment(organization_id,property_id,created_at,id);
    CREATE INDEX ix_attachment_unit ON attachment(unit_id);
    CREATE INDEX ix_attachment_lease ON attachment(lease_agreement_id);
    CREATE INDEX ix_attachment_person ON attachment(person_id);

    CREATE FUNCTION attachment_parent_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NOT EXISTS(SELECT 1 FROM property p WHERE p.id=NEW.property_id AND p.organization_id=NEW.organization_id)
           OR NOT EXISTS(SELECT 1 FROM "user" u WHERE u.id=NEW.uploaded_by AND u.organization_id=NEW.organization_id)
           OR (NEW.unit_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM unit u WHERE u.id=NEW.unit_id
                 AND u.property_id=NEW.property_id AND u.organization_id=NEW.organization_id))
           OR (NEW.lease_agreement_id IS NOT NULL AND NOT EXISTS(
                 SELECT 1 FROM lease_agreement l JOIN unit u ON u.id=l.unit_id AND u.organization_id=l.organization_id
                 WHERE l.id=NEW.lease_agreement_id AND l.organization_id=NEW.organization_id
                 AND u.property_id=NEW.property_id AND (NEW.unit_id IS NULL OR l.unit_id=NEW.unit_id)))
           OR (NEW.person_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM person p WHERE p.id=NEW.person_id
                 AND p.organization_id=NEW.organization_id))
           OR (NEW.archived_by IS NOT NULL AND NOT EXISTS(SELECT 1 FROM "user" u WHERE u.id=NEW.archived_by
                 AND u.organization_id=NEW.organization_id)) THEN
            RAISE EXCEPTION 'Attachment parent coherence violation' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END $$;
    CREATE TRIGGER trg_attachment_parent BEFORE INSERT OR UPDATE ON attachment
        FOR EACH ROW EXECUTE FUNCTION attachment_parent_guard();

    CREATE FUNCTION attachment_original_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'Attachment originals cannot be deleted';
        END IF;
        IF ROW(NEW.id,NEW.organization_id,NEW.property_id,NEW.unit_id,NEW.lease_agreement_id,
               NEW.person_id,NEW.title,NEW.original_filename,NEW.mime_type,NEW.content_byte_size,
               NEW.content_sha256,NEW.storage_backend,NEW.storage_uri,NEW.uploaded_by,NEW.created_at)
           IS DISTINCT FROM
           ROW(OLD.id,OLD.organization_id,OLD.property_id,OLD.unit_id,OLD.lease_agreement_id,
               OLD.person_id,OLD.title,OLD.original_filename,OLD.mime_type,OLD.content_byte_size,
               OLD.content_sha256,OLD.storage_backend,OLD.storage_uri,OLD.uploaded_by,OLD.created_at) THEN
            RAISE EXCEPTION 'Attachment original is immutable';
        END IF;
        IF OLD.archived_at IS NOT NULL AND
           ROW(NEW.archived_at,NEW.archived_by,NEW.archive_reason) IS DISTINCT FROM
           ROW(OLD.archived_at,OLD.archived_by,OLD.archive_reason) THEN
            RAISE EXCEPTION 'Attachment archive is final';
        END IF;
        RETURN NEW;
    END $$;
    CREATE TRIGGER trg_attachment_original BEFORE UPDATE OR DELETE ON attachment
        FOR EACH ROW EXECUTE FUNCTION attachment_original_guard();

    -- A source-specific invoker adapter delegates to the existing authorities.
    -- Policy dependencies stay with the attachment lifecycle, not older schemas.
    CREATE FUNCTION attachment_source_access(p_org uuid,p_property uuid,p_unit uuid,
        p_lease uuid,p_person uuid,p_capability text) RETURNS boolean
        LANGUAGE plpgsql STABLE SECURITY INVOKER AS $$
    DECLARE permitted boolean;
    BEGIN
        IF NOT coalesce(property_master_access(p_org,p_property,p_capability),false) THEN
            RETURN false;
        END IF;
        IF p_unit IS NOT NULL AND NOT EXISTS(SELECT 1 FROM unit u
            WHERE u.id=p_unit AND u.organization_id=p_org AND u.property_id=p_property) THEN
            RETURN false;
        END IF;
        IF p_person IS NOT NULL AND NOT coalesce(person_source_access(p_org,p_person,p_capability),false) THEN
            RETURN false;
        END IF;
        IF p_lease IS NOT NULL THEN
            SELECT property_history_row_access(l.organization_id,'lease_agreement',to_jsonb(l),p_capability)
              INTO permitted FROM lease_agreement l JOIN unit u
                ON u.id=l.unit_id AND u.organization_id=l.organization_id
              WHERE l.id=p_lease AND l.organization_id=p_org AND u.property_id=p_property
                AND (p_unit IS NULL OR l.unit_id=p_unit);
            IF NOT coalesce(permitted,false) THEN RETURN false; END IF;
        END IF;
        RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION attachment_source_access(uuid,uuid,uuid,uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION attachment_source_access(uuid,uuid,uuid,uuid,uuid,text) TO app_rw;

    ALTER TABLE attachment ENABLE ROW LEVEL SECURITY;
    ALTER TABLE attachment FORCE ROW LEVEL SECURITY;
    CREATE POLICY attachment_read ON attachment FOR SELECT USING (
        organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND attachment_source_access(organization_id,property_id,unit_id,lease_agreement_id,person_id,'read'));
    CREATE POLICY attachment_insert ON attachment FOR INSERT WITH CHECK (
        organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND uploaded_by = NULLIF(current_setting('app.current_user_id',true),'')::uuid
        AND attachment_source_access(organization_id,property_id,unit_id,lease_agreement_id,person_id,'prepare'));
    CREATE POLICY attachment_archive ON attachment FOR UPDATE USING (
        organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND attachment_source_access(organization_id,property_id,unit_id,lease_agreement_id,person_id,'prepare'))
        WITH CHECK (
        organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND attachment_source_access(organization_id,property_id,unit_id,lease_agreement_id,person_id,'prepare')
        AND archived_by = NULLIF(current_setting('app.current_user_id',true),'')::uuid);
    GRANT SELECT,INSERT,UPDATE ON attachment TO app_rw;
    """)


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM attachment) THEN
            RAISE EXCEPTION 'Cannot downgrade while retained attachments exist';
        END IF;
    END $$;
    DROP TABLE attachment;
    DROP FUNCTION IF EXISTS attachment_source_access(uuid,uuid,uuid,uuid,uuid,text);
    DROP FUNCTION attachment_original_guard();
    DROP FUNCTION attachment_parent_guard();
    """)
