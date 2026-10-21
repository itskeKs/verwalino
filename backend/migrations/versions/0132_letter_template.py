"""Saved plain-text letter templates and immutable personalized PDFs (T48)."""
from alembic import op

revision = "0132"
down_revision = "0131"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE letter_template (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        organization_id uuid NOT NULL REFERENCES organization(id) ON DELETE RESTRICT,
        name varchar(255) NOT NULL CHECK(length(btrim(name))>0),
        subject varchar(500) NOT NULL CHECK(length(btrim(subject))>0),
        body text NOT NULL CHECK(length(body) BETWEEN 1 AND 20000),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(organization_id,id)
    );
    CREATE TABLE letter_document (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        organization_id uuid NOT NULL REFERENCES organization(id) ON DELETE RESTRICT,
        template_id uuid NOT NULL,
        property_id uuid NOT NULL REFERENCES property(id) ON DELETE RESTRICT,
        person_id uuid NOT NULL REFERENCES person(id) ON DELETE RESTRICT,
        tenant_profile_id uuid NOT NULL REFERENCES tenant_profile(id) ON DELETE RESTRICT,
        lease_agreement_id uuid NOT NULL REFERENCES lease_agreement(id) ON DELETE RESTRICT,
        request_id uuid NOT NULL,
        as_of date NOT NULL,
        recipient_name text NOT NULL,
        subject text NOT NULL,
        body text NOT NULL,
        storage_uri text NOT NULL,
        content_sha256 varchar(64) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(organization_id,template_id) REFERENCES letter_template(organization_id,id) ON DELETE RESTRICT,
        UNIQUE(organization_id,request_id,person_id)
    );
    CREATE INDEX ix_letter_document_property ON letter_document(organization_id,property_id,created_at,id);
    CREATE FUNCTION letter_source_access(p_org uuid,p_property uuid,p_person uuid,p_tenant uuid,p_lease uuid)
      RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public,pg_temp AS $$
        SELECT coalesce(property_master_access(p_org,p_property,'export'),false)
           AND coalesce(person_source_access(p_org,p_person,'export'),false)
           AND coalesce(profile_source_access(p_org,'tenant_profile',p_tenant,'export'),false)
           AND EXISTS(SELECT 1 FROM lease_agreement l JOIN unit u ON u.id=l.unit_id AND u.organization_id=l.organization_id
             JOIN tenant_profile t ON t.id=l.tenant_profile_id AND t.organization_id=l.organization_id
             WHERE l.id=p_lease AND l.organization_id=p_org AND u.property_id=p_property
               AND t.id=p_tenant AND t.person_id=p_person
               AND property_history_row_access(p_org,'lease_agreement',to_jsonb(l),'export'))
    $$;
    REVOKE ALL ON FUNCTION letter_source_access(uuid,uuid,uuid,uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION letter_source_access(uuid,uuid,uuid,uuid,uuid) TO app_rw;
    CREATE FUNCTION letter_recipients_access(p_org uuid,p_property uuid,p_date date)
      RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE source record;
    BEGIN
      IF p_org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR public.scope_actor_role(p_org) NOT IN ('OWNER_ADMIN','CLERK')
        OR NOT coalesce(public.property_master_access(p_org,p_property,'prepare'),false)
        OR NOT coalesce(public.property_master_access(p_org,p_property,'export'),false) THEN RETURN false; END IF;
      FOR source IN SELECT l.*,t.person_id FROM public.lease_agreement l
        JOIN public.unit u ON u.id=l.unit_id AND u.organization_id=l.organization_id
        JOIN public.tenant_profile t ON t.id=l.tenant_profile_id AND t.organization_id=l.organization_id
        WHERE u.property_id=p_property AND l.organization_id=p_org
          AND l.valid_from<=p_date AND (l.valid_to IS NULL OR l.valid_to>p_date)
      LOOP
        IF NOT coalesce(public.letter_source_access(p_org,p_property,source.person_id,
            source.tenant_profile_id,source.id),false) THEN RETURN false; END IF;
      END LOOP;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION letter_recipients_access(uuid,uuid,date) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION letter_recipients_access(uuid,uuid,date) TO app_rw;
    CREATE FUNCTION letter_request_access(p_org uuid,p_request uuid) RETURNS boolean
      LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE source record;
    BEGIN
      IF p_org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR public.scope_actor_role(p_org) NOT IN ('OWNER_ADMIN','CLERK') THEN RETURN false; END IF;
      FOR source IN SELECT * FROM public.letter_document WHERE organization_id=p_org AND request_id=p_request LOOP
        IF NOT coalesce(public.letter_source_access(p_org,source.property_id,source.person_id,
             source.tenant_profile_id,source.lease_agreement_id),false) THEN RETURN false; END IF;
      END LOOP;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION letter_request_access(uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION letter_request_access(uuid,uuid) TO app_rw;
    CREATE FUNCTION letter_document_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'Issued letters are immutable' USING ERRCODE='23514'; END $$;
    CREATE TRIGGER letter_document_immutable BEFORE UPDATE OR DELETE ON letter_document
       FOR EACH ROW EXECUTE FUNCTION letter_document_immutable();
    CREATE TRIGGER a_letter_document_acl_lock BEFORE INSERT ON letter_document
       FOR EACH STATEMENT EXECUTE FUNCTION task_acl_lock();
    CREATE TRIGGER a_letter_template_acl_lock BEFORE INSERT OR UPDATE OR DELETE ON letter_template
       FOR EACH STATEMENT EXECUTE FUNCTION task_acl_lock();
    ALTER TABLE letter_template ENABLE ROW LEVEL SECURITY;
    ALTER TABLE letter_template FORCE ROW LEVEL SECURITY;
    ALTER TABLE letter_document ENABLE ROW LEVEL SECURITY;
    ALTER TABLE letter_document FORCE ROW LEVEL SECURITY;
    CREATE POLICY letter_template_read ON letter_template FOR SELECT USING (
        organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK','READ_ONLY'));
    CREATE POLICY letter_template_insert ON letter_template FOR INSERT WITH CHECK (
        organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK'));
    CREATE POLICY letter_template_update ON letter_template FOR UPDATE USING (
        organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK')) WITH CHECK (
        organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK'));
    CREATE POLICY letter_document_read ON letter_document FOR SELECT USING (
        organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK','READ_ONLY')
        AND letter_source_access(organization_id,property_id,person_id,tenant_profile_id,lease_agreement_id));
    CREATE POLICY letter_document_insert ON letter_document FOR INSERT WITH CHECK (
        organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK')
        AND letter_source_access(organization_id,property_id,person_id,tenant_profile_id,lease_agreement_id));
    GRANT SELECT,INSERT,UPDATE ON letter_template TO app_rw;
    GRANT SELECT,INSERT ON letter_document TO app_rw;
    """)


def downgrade():
    op.execute("""
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM letter_template) OR EXISTS(SELECT 1 FROM letter_document) THEN
        RAISE EXCEPTION 'Cannot remove populated letter tables';
      END IF;
    END $$;
    DROP TABLE letter_document;
    DROP TABLE letter_template;
    DROP FUNCTION letter_document_immutable();
    DROP FUNCTION IF EXISTS letter_request_access(uuid,uuid);
    DROP FUNCTION letter_recipients_access(uuid,uuid,date);
    DROP FUNCTION letter_source_access(uuid,uuid,uuid,uuid,uuid);
    """)
