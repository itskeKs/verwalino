"""Enforce direct master-data authority with atomic explicit creation audiences."""
from alembic import op

revision = '0061'
down_revision = '0060'
branch_labels = None
depends_on = None

TABLES = ('property','unit','property_data_domain','property_data_grant','property_source_security')
DOMAIN_FK = 'property_data_domain_organization_id_property_id_fkey'


def upgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    # Publish the explicit domain/grants before the new property must satisfy
    # INSERT RETURNING read policies. The same transaction must supply the parent.
    op.execute(f'ALTER TABLE property_data_domain ALTER CONSTRAINT {DOMAIN_FK} DEFERRABLE INITIALLY DEFERRED')
    op.execute("""
    CREATE FUNCTION property_data_setup_target_exists(org uuid,property uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||org::text,0));
      IF public.scope_actor_role(org) IS DISTINCT FROM 'OWNER_ADMIN' THEN RETURN false; END IF;
      RETURN EXISTS(SELECT 1 FROM public.property p WHERE p.organization_id=org AND p.id=property);
    END $$;
    REVOKE ALL ON FUNCTION property_data_setup_target_exists(uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_data_setup_target_exists(uuid,uuid) TO app_rw;
    CREATE FUNCTION property_master_access(org uuid,property uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text;
    BEGIN
      IF property IS NULL OR capability IS NULL OR capability NOT IN ('read','prepare','approve','export') THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      RETURN public.property_source_actor_permitted(org,NULLIF(current_setting('app.current_user_id',true),'')::uuid,
        'property',property,capability)
        AND public.property_complete_graph_actor_access(org,
          NULLIF(current_setting('app.current_user_id',true),'')::uuid,property,'read',false);
    END $$;
    REVOKE ALL ON FUNCTION property_master_access(uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_master_access(uuid,uuid,text) TO app_rw;
    """)
    op.execute("""
    CREATE TABLE property_creation_receipt (
      organization_id uuid NOT NULL, actor_user_id uuid NOT NULL, request_key uuid NOT NULL,
      request_digest varchar(64) NOT NULL CHECK(request_digest ~ '^[0-9a-f]{64}$'), property_id uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(organization_id,actor_user_id,request_key),
      FOREIGN KEY(organization_id,actor_user_id) REFERENCES "user"(organization_id,id),
      FOREIGN KEY(organization_id,property_id) REFERENCES property(organization_id,id)
    );
    ALTER TABLE property_creation_receipt ENABLE ROW LEVEL SECURITY;
    ALTER TABLE property_creation_receipt FORCE ROW LEVEL SECURITY;
    CREATE POLICY property_creation_read ON property_creation_receipt FOR SELECT USING (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND actor_user_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid
      AND scope_actor_role(organization_id) IS NOT NULL);
    CREATE POLICY property_creation_insert ON property_creation_receipt FOR INSERT WITH CHECK (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND actor_user_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid
      AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK')
      AND property_master_access(organization_id,property_id,'prepare'));
    REVOKE ALL ON property_creation_receipt FROM PUBLIC,app_rw;
    GRANT SELECT,INSERT ON property_creation_receipt TO app_rw;
    CREATE FUNCTION protect_property_creation_receipt() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    BEGIN RAISE EXCEPTION 'Property creation receipt is immutable' USING ERRCODE='23514'; END $$;
    REVOKE ALL ON FUNCTION protect_property_creation_receipt() FROM PUBLIC,app_rw;
    CREATE TRIGGER protect_property_creation_receipt BEFORE UPDATE OR DELETE ON property_creation_receipt
      FOR EACH ROW EXECUTE FUNCTION protect_property_creation_receipt();
    """)
    for table, column in [('property','id'),('unit','property_id')]:
        read=f"property_master_access(organization_id,{column},'read')"
        write=f"property_master_access(organization_id,{column},'prepare')"
        op.execute(f'CREATE POLICY property_master_read ON {table} AS RESTRICTIVE FOR SELECT USING ({read})')
        op.execute(f'CREATE POLICY property_master_insert ON {table} AS RESTRICTIVE FOR INSERT WITH CHECK ({write})')
        op.execute(f'CREATE POLICY property_master_update ON {table} AS RESTRICTIVE FOR UPDATE USING ({write}) WITH CHECK ({write})')
        op.execute(f'CREATE POLICY property_master_delete ON {table} AS RESTRICTIVE FOR DELETE USING ({write})')
        op.execute(f'CREATE TRIGGER a_lock_property_master BEFORE INSERT OR UPDATE OR DELETE ON {table} '
                   'FOR EACH STATEMENT EXECUTE FUNCTION lock_strict_property_source_binding()')


def downgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+',property_creation_receipt IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain strict property master authority'; END IF; END $$""")
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_creation_receipt) THEN
      RAISE EXCEPTION 'Retain property creation receipts'; END IF; END $$;
      DROP TABLE property_creation_receipt;
      DROP FUNCTION protect_property_creation_receipt();
    """)
    for table in ('property','unit'):
        for action in ('read','insert','update','delete'):
            op.execute(f'DROP POLICY property_master_{action} ON {table}')
        op.execute(f'DROP TRIGGER a_lock_property_master ON {table}')
    op.execute('DROP FUNCTION property_master_access(uuid,uuid,text)')
    op.execute('DROP FUNCTION IF EXISTS property_data_setup_target_exists(uuid,uuid)')
    op.execute(f'ALTER TABLE property_data_domain ALTER CONSTRAINT {DOMAIN_FK} NOT DEFERRABLE INITIALLY IMMEDIATE')
