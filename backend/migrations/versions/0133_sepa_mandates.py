"""Explicit creditor configuration and tenant direct-debit consent (T50)."""
from alembic import op

revision = '0133'
down_revision = '0132'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE sepa_creditor (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      organization_id uuid NOT NULL REFERENCES organization(id) ON DELETE RESTRICT,
      bank_account_id uuid NOT NULL,
      creditor_id varchar(35) NOT NULL,
      name varchar(70) NOT NULL CHECK(length(btrim(name))>0),
      country varchar(2) NOT NULL CHECK(country ~ '^[A-Z]{2}$'),
      town varchar(35) NOT NULL CHECK(length(btrim(town))>0),
      bank_fingerprint varchar(64) NOT NULL,
      bank_authorized boolean NOT NULL CHECK(bank_authorized),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY(organization_id,bank_account_id) REFERENCES bank_account(organization_id,id) ON DELETE RESTRICT,
      UNIQUE(organization_id,bank_account_id),
      UNIQUE(organization_id,bank_account_id,creditor_id)
    );
    CREATE TABLE sepa_mandate (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      organization_id uuid NOT NULL REFERENCES organization(id) ON DELETE RESTRICT,
      bank_account_id uuid NOT NULL,
      tenant_profile_id uuid NOT NULL REFERENCES tenant_profile(id) ON DELETE RESTRICT,
      creditor_id varchar(35) NOT NULL,
      reference varchar(35) NOT NULL CHECK(length(btrim(reference))>0 AND reference=upper(reference)),
      signed_on date NOT NULL,
      sequence_type varchar(4) NOT NULL CHECK(sequence_type IN ('FRST','RCUR')),
      last_used_on date,
      debtor_country varchar(2) NOT NULL CHECK(debtor_country ~ '^[A-Z]{2}$'),
      debtor_bic varchar(11),
      source_fingerprint varchar(64) NOT NULL,
      original_reference varchar(255) NOT NULL CHECK(length(btrim(original_reference))>0),
      revoked_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY(organization_id,bank_account_id,creditor_id)
        REFERENCES sepa_creditor(organization_id,bank_account_id,creditor_id) ON DELETE RESTRICT,
      UNIQUE(organization_id,creditor_id,reference),
      CHECK((sequence_type='FRST' AND last_used_on IS NULL)
         OR (sequence_type='RCUR' AND last_used_on IS NOT NULL AND last_used_on>=signed_on))
    );
    CREATE UNIQUE INDEX uq_sepa_active_tenant_bank ON sepa_mandate(organization_id,bank_account_id,tenant_profile_id)
      WHERE revoked_at IS NULL;
    CREATE FUNCTION sepa_bank_access(p_org uuid,p_bank uuid,p_cap text) RETURNS boolean
      LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public,pg_temp AS $$
      SELECT coalesce(financial_scope_access(p_org,'bank_account',p_bank,p_cap),false)
        AND EXISTS(SELECT 1 FROM bank_account b WHERE b.organization_id=p_org AND b.id=p_bank
          AND b.account_type='LAUFEND' AND property_master_access(p_org,b.property_id,p_cap))
    $$;
    CREATE FUNCTION sepa_mandate_access(p_org uuid,p_bank uuid,p_tenant uuid,p_cap text) RETURNS boolean
      LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public,pg_temp AS $$
      SELECT coalesce(sepa_bank_access(p_org,p_bank,p_cap),false)
        AND coalesce(profile_source_access(p_org,'tenant_profile',p_tenant,p_cap),false)
        AND EXISTS(SELECT 1 FROM tenant_profile t JOIN lease_agreement l
          ON l.tenant_profile_id=t.id AND l.organization_id=t.organization_id
          JOIN unit u ON u.id=l.unit_id AND u.organization_id=l.organization_id
          JOIN bank_account b ON b.property_id=u.property_id AND b.organization_id=u.organization_id
          WHERE t.id=p_tenant AND t.organization_id=p_org AND b.id=p_bank)
    $$;
    CREATE FUNCTION sepa_input_lock() RETURNS trigger LANGUAGE plpgsql
      SET search_path=pg_catalog,public,pg_temp AS $$
    DECLARE org uuid:=nullif(current_setting('app.current_org_id',true),'')::uuid;
    BEGIN
      IF org IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org,0));
        PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org,0));
      END IF;
      RETURN NULL;
    END $$;
    CREATE FUNCTION sepa_identity_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF TG_TABLE_NAME='sepa_creditor' THEN
        IF TG_OP='UPDATE' AND (to_jsonb(NEW)-'updated_at') IS DISTINCT FROM (to_jsonb(OLD)-'updated_at')
          AND EXISTS(SELECT 1 FROM public.sepa_mandate m WHERE m.organization_id=OLD.organization_id
            AND m.bank_account_id=OLD.bank_account_id AND m.revoked_at IS NULL) THEN
          RAISE EXCEPTION 'Revoke active mandates before changing creditor identity' USING ERRCODE='23514';
        END IF;
      ELSE
        IF NOT EXISTS(SELECT 1 FROM public.tenant_profile t JOIN public.lease_agreement l
          ON l.tenant_profile_id=t.id AND l.organization_id=t.organization_id
          JOIN public.unit u ON u.id=l.unit_id AND u.organization_id=l.organization_id
          JOIN public.bank_account b ON b.property_id=u.property_id AND b.organization_id=u.organization_id
          WHERE t.id=NEW.tenant_profile_id AND t.organization_id=NEW.organization_id
            AND b.id=NEW.bank_account_id) THEN
          RAISE EXCEPTION 'Mandate tenant and bank must share organization and property' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND (OLD.revoked_at IS NOT NULL OR
          (to_jsonb(NEW)-ARRAY['sequence_type','last_used_on','revoked_at','updated_at']) IS DISTINCT FROM
          (to_jsonb(OLD)-ARRAY['sequence_type','last_used_on','revoked_at','updated_at']) OR
          (OLD.last_used_on IS NOT NULL AND (NEW.last_used_on IS NULL OR NEW.last_used_on<OLD.last_used_on))) THEN
          RAISE EXCEPTION 'Mandate identity and recorded usage cannot be rewritten' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION sepa_bank_access(uuid,uuid,text),sepa_mandate_access(uuid,uuid,uuid,text),
      sepa_input_lock(),sepa_identity_guard() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION sepa_bank_access(uuid,uuid,text),sepa_mandate_access(uuid,uuid,uuid,text),
      sepa_input_lock(),sepa_identity_guard() TO app_rw;
    """)
    for table in ('sepa_creditor', 'sepa_mandate'):
        access = "sepa_bank_access(organization_id,bank_account_id,{cap})" if table == 'sepa_creditor' else "sepa_mandate_access(organization_id,bank_account_id,tenant_profile_id,{cap})"
        tenant = "organization_id=nullif(current_setting('app.current_org_id',true),'')::uuid"
        read = f"{tenant} AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK','READ_ONLY') AND {access.format(cap=chr(39)+'read'+chr(39))}"
        write = f"{tenant} AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK') AND {access.format(cap=chr(39)+'prepare'+chr(39))}"
        op.execute(f"""
        ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
        CREATE POLICY {table}_read ON {table} FOR SELECT USING ({read});
        CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK ({write});
        CREATE POLICY {table}_update ON {table} FOR UPDATE USING ({write}) WITH CHECK ({write});
        GRANT SELECT,INSERT,UPDATE ON {table} TO app_rw;
        CREATE TRIGGER a_{table}_input_lock BEFORE INSERT OR UPDATE OR DELETE ON {table}
          FOR EACH STATEMENT EXECUTE FUNCTION sepa_input_lock();
        CREATE TRIGGER {table}_identity BEFORE INSERT OR UPDATE ON {table}
          FOR EACH ROW EXECUTE FUNCTION sepa_identity_guard();
        """)


def downgrade():
    op.execute("""
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM sepa_creditor) OR EXISTS(SELECT 1 FROM sepa_mandate) THEN
        RAISE EXCEPTION 'Cannot remove populated SEPA configuration or mandates';
      END IF;
    END $$;
    DROP TABLE sepa_mandate;
    DROP TABLE sepa_creditor;
    DROP FUNCTION sepa_identity_guard();
    DROP FUNCTION sepa_input_lock();
    DROP FUNCTION sepa_mandate_access(uuid,uuid,uuid,text);
    DROP FUNCTION sepa_bank_access(uuid,uuid,text);
    """)
