"""Prepare explicit shared property-data domains without activating source policies."""
from alembic import op

revision = '0045'
down_revision = '0044'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    CREATE TABLE property_data_domain (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL,
      property_id uuid NOT NULL, label varchar(255) NOT NULL CHECK(length(trim(label))>0),
      reason text NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 2000),
      created_by uuid NOT NULL, request_key uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(organization_id,id), UNIQUE(organization_id,property_id), UNIQUE(organization_id,created_by,request_key),
      FOREIGN KEY(organization_id,property_id) REFERENCES property(organization_id,id),
      FOREIGN KEY(organization_id,created_by) REFERENCES "user"(organization_id,id)
    );
    CREATE TABLE property_data_grant (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL, domain_id uuid NOT NULL,
      user_id uuid NOT NULL, capabilities varchar(16)[] NOT NULL,
      reason text NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 2000),
      granted_by uuid NOT NULL, request_key uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      expires_at timestamptz, revoked_at timestamptz, revoked_by uuid,
      UNIQUE(organization_id,granted_by,request_key),
      FOREIGN KEY(organization_id,domain_id) REFERENCES property_data_domain(organization_id,id),
      FOREIGN KEY(organization_id,user_id) REFERENCES "user"(organization_id,id),
      FOREIGN KEY(organization_id,granted_by) REFERENCES "user"(organization_id,id),
      FOREIGN KEY(organization_id,revoked_by) REFERENCES "user"(organization_id,id),
      CHECK(capabilities @> ARRAY['read']::varchar[] AND array_position(capabilities,NULL) IS NULL
        AND capabilities <@ ARRAY['read','prepare','approve','export']::varchar[]),
      CHECK((revoked_at IS NULL)=(revoked_by IS NULL)), CHECK(expires_at IS NULL OR expires_at>created_at)
    );
    CREATE UNIQUE INDEX uq_property_data_grant_live ON property_data_grant(organization_id,domain_id,user_id) WHERE revoked_at IS NULL;
    CREATE INDEX ix_property_data_grant_actor ON property_data_grant(organization_id,user_id,domain_id) WHERE revoked_at IS NULL;

    CREATE FUNCTION has_property_data_capability(org uuid, domain uuid, capability text) RETURNS boolean
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

    CREATE FUNCTION protect_property_data_setup() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; action text;
    BEGIN
      IF TG_OP='DELETE' OR (TG_TABLE_NAME='property_data_domain' AND TG_OP='UPDATE') THEN
        RAISE EXCEPTION 'Property data evidence is immutable' USING ERRCODE='23514'; END IF;
      IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Property data organization mismatch' USING ERRCODE='42501'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
      IF public.scope_actor_role(NEW.organization_id) IS DISTINCT FROM 'OWNER_ADMIN' THEN
        RAISE EXCEPTION 'Active administrator required' USING ERRCODE='42501'; END IF;
      IF TG_OP='INSERT' THEN
        NEW.created_at:=clock_timestamp();
        IF TG_TABLE_NAME='property_data_domain' THEN
          IF NEW.created_by IS DISTINCT FROM actor THEN RAISE EXCEPTION 'Domain actor mismatch' USING ERRCODE='42501'; END IF;
          action:='property_data.domain_prepared';
        ELSE
          IF NEW.granted_by IS DISTINCT FROM actor OR NEW.revoked_at IS NOT NULL OR NEW.revoked_by IS NOT NULL
            OR NOT EXISTS(SELECT 1 FROM public.membership m JOIN public."user" u ON u.organization_id=m.organization_id AND u.id=m.user_id
              WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.user_id AND u.is_active) THEN
            RAISE EXCEPTION 'Grant requires matching actor and active member' USING ERRCODE='42501'; END IF;
          action:='property_data.grant_prepared';
        END IF;
      ELSE
        IF OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL OR NEW.revoked_by IS DISTINCT FROM actor
          OR (to_jsonb(NEW)-'revoked_at'-'revoked_by') IS DISTINCT FROM (to_jsonb(OLD)-'revoked_at'-'revoked_by') THEN
          RAISE EXCEPTION 'Only one-way property data grant revocation is allowed' USING ERRCODE='23514'; END IF;
        NEW.revoked_at:=clock_timestamp(); action:='property_data.grant_revoked';
      END IF;
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(NEW.organization_id,actor,TG_TABLE_NAME,NEW.id,action,jsonb_build_object('reason',NEW.reason));
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION has_property_data_capability(uuid,uuid,text),protect_property_data_setup() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION has_property_data_capability(uuid,uuid,text) TO app_rw;
    ''')
    tenant = "organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid"
    actor = "NULLIF(current_setting('app.current_user_id',true),'')::uuid"
    admin = "scope_actor_role(organization_id)='OWNER_ADMIN'"
    for table in ('property_data_domain', 'property_data_grant'):
        read = f"{admin} OR has_property_data_capability(organization_id,id,'read')" if table.endswith('domain') else f"{admin} OR (user_id={actor} AND scope_actor_role(organization_id) IS NOT NULL)"
        op.execute(f'''
          ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          CREATE TRIGGER protect_property_data_setup BEFORE INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION protect_property_data_setup();
          CREATE POLICY property_data_read ON {table} FOR SELECT USING ({tenant} AND ({read}));
          CREATE POLICY property_data_create ON {table} FOR INSERT WITH CHECK ({tenant} AND {admin});
          REVOKE ALL ON {table} FROM PUBLIC,app_rw;
          GRANT SELECT,INSERT ON {table} TO app_rw;
        ''')
    op.execute(f'''
      CREATE POLICY property_data_revoke ON property_data_grant FOR UPDATE USING ({tenant} AND {admin}) WITH CHECK ({tenant} AND {admin});
      GRANT UPDATE(revoked_at,revoked_by) ON property_data_grant TO app_rw;
    ''')


def downgrade():
    op.execute('''
      DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_data_domain) THEN
        RAISE EXCEPTION 'Retain populated property data configuration'; END IF; END $$;
      DROP TABLE property_data_grant,property_data_domain;
      DROP FUNCTION has_property_data_capability(uuid,uuid,text),protect_property_data_setup();
    ''')
