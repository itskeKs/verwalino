"""Hosted signup reservations and a narrow atomic provisioning boundary."""
from alembic import op

revision = "0143"
down_revision = "0142"
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    ALTER TABLE public.auth_throttle ADD COLUMN last_lockout_at timestamptz;
    UPDATE public.auth_throttle SET last_lockout_at=updated_at WHERE lockouts>0;

    -- Explicit reuse keeps the four-argument CLI contract while signup always
    -- creates a tenant. Neither bootstrap overload is callable by runtime roles.
    CREATE FUNCTION public.bootstrap_organization_admin(
      org_name text, admin_email text, admin_name text, password_hash text, reuse_existing boolean)
    RETURNS uuid LANGUAGE plpgsql
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE org uuid; actor uuid;
    BEGIN
      LOCK TABLE public.organization IN SHARE ROW EXCLUSIVE MODE;
      IF reuse_existing THEN
        SELECT id INTO org FROM public.organization WHERE name=org_name;
      END IF;
      IF org IS NULL THEN
        INSERT INTO public.organization(name) VALUES (org_name) RETURNING id INTO org;
      END IF;
      INSERT INTO public."user"(organization_id,email,display_name,password_hash)
        VALUES (org,lower(btrim(admin_email)),admin_name,password_hash) RETURNING id INTO actor;
      INSERT INTO public.membership(organization_id,user_id,role)
        VALUES (org,actor,'OWNER_ADMIN');
      UPDATE public.setup_token SET used_at=COALESCE(used_at,clock_timestamp());
      RETURN org;
    END $$;
    REVOKE ALL ON FUNCTION public.bootstrap_organization_admin(text,text,text,text,boolean) FROM PUBLIC;

    CREATE OR REPLACE FUNCTION public.bootstrap_organization_admin(
      org_name text, admin_email text, admin_name text, password_hash text)
    RETURNS uuid LANGUAGE plpgsql
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      RETURN public.bootstrap_organization_admin(org_name,admin_email,admin_name,password_hash,true);
    END $$;

    -- Only pending requests reserve an email; existing users are checked by the
    -- definer below without rewriting legacy organization accounts.
    CREATE TABLE public.signup_request (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      email_normalized text NOT NULL UNIQUE
        CHECK (email_normalized=lower(btrim(email_normalized)) AND length(email_normalized) BETWEEN 3 AND 254),
      organization_name text NOT NULL CHECK (length(btrim(organization_name)) BETWEEN 1 AND 255),
      admin_name text NOT NULL CHECK (length(btrim(admin_name)) BETWEEN 1 AND 255),
      password_hash text,
      token_hash bytea NOT NULL UNIQUE CHECK (octet_length(token_hash)=32),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '24 hours',
      confirmed_at timestamptz,
      resent_at timestamptz,
      CHECK ((confirmed_at IS NULL AND password_hash IS NOT NULL)
          OR (confirmed_at IS NOT NULL AND password_hash IS NULL))
    );
    REVOKE ALL ON public.signup_request FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT SELECT,INSERT,UPDATE,DELETE ON public.signup_request TO app_rw;

    -- Includes inactive, memberless and ambiguous legacy identities that the
    -- deliberately non-enumerating authentication lookup cannot expose.
    CREATE FUNCTION public.signup_email_registered(email text) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT EXISTS(SELECT 1 FROM public."user" u WHERE lower(btrim(u.email))=lower(btrim($1)))
    $$;
    REVOKE ALL ON FUNCTION public.signup_email_registered(text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.signup_email_registered(text) TO app_rw;

    CREATE FUNCTION public.provision_organization(token text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE pending public.signup_request%ROWTYPE; supplied bytea; expected bytea;
      difference integer := 0; byte_index integer; org uuid;
    BEGIN
      -- Same order as setup/bootstrap. Never let bootstrap reuse a tenant by name.
      LOCK TABLE public.organization IN SHARE ROW EXCLUSIVE MODE;
      supplied := sha256(convert_to(COALESCE(token,''),'UTF8'));
      SELECT * INTO pending FROM public.signup_request WHERE token_hash=supplied FOR UPDATE;
      expected := COALESCE(pending.token_hash,decode(repeat('00',32),'hex'));
      FOR byte_index IN 0..31 LOOP
        difference := difference | (get_byte(supplied,byte_index) # get_byte(expected,byte_index));
      END LOOP;
      IF difference<>0 OR pending.id IS NULL OR pending.confirmed_at IS NOT NULL
        OR pending.expires_at<=clock_timestamp() OR pending.password_hash IS NULL
        OR public.signup_email_registered(pending.email_normalized) THEN
        RAISE EXCEPTION 'Signup refused';
      END IF;
      org := public.bootstrap_organization_admin(pending.organization_name,pending.email_normalized,
                                                pending.admin_name,pending.password_hash,false);
      DELETE FROM public.signup_request WHERE id=pending.id;
      RETURN org;
    EXCEPTION WHEN OTHERS THEN
      RAISE EXCEPTION 'Signup refused' USING ERRCODE='P0001';
    END $$;
    REVOKE ALL ON FUNCTION public.provision_organization(text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.provision_organization(text) TO app_rw;
    ''')


def downgrade():
    op.execute('''
    LOCK TABLE public.signup_request IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.signup_request) THEN
        RAISE EXCEPTION 'Cannot discard retained signup state';
      END IF;
      IF EXISTS(SELECT 1 FROM public.auth_throttle WHERE last_lockout_at IS NOT NULL) THEN
        RAISE EXCEPTION 'Cannot discard retained auth throttle state';
      END IF;
    END $$;
    DROP FUNCTION public.provision_organization(text);
    DROP FUNCTION public.signup_email_registered(text);
    DROP TABLE public.signup_request;
    -- Restore revision 0141's exact implementation, settings and existing ACL.
    CREATE OR REPLACE FUNCTION public.bootstrap_organization_admin(
      org_name text, admin_email text, admin_name text, password_hash text)
    RETURNS uuid LANGUAGE plpgsql
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE org uuid; actor uuid;
    BEGIN
      LOCK TABLE public.organization IN SHARE ROW EXCLUSIVE MODE;
      SELECT id INTO org FROM public.organization WHERE name=org_name;
      IF org IS NULL THEN
        INSERT INTO public.organization(name) VALUES (org_name) RETURNING id INTO org;
      END IF;
      INSERT INTO public."user"(organization_id,email,display_name,password_hash)
        VALUES (org,lower(btrim(admin_email)),admin_name,password_hash) RETURNING id INTO actor;
      INSERT INTO public.membership(organization_id,user_id,role)
        VALUES (org,actor,'OWNER_ADMIN');
      UPDATE public.setup_token SET used_at=COALESCE(used_at,clock_timestamp());
      RETURN org;
    END $$;
    DROP FUNCTION public.bootstrap_organization_admin(text,text,text,text,boolean);
    ALTER TABLE public.auth_throttle DROP COLUMN last_lockout_at;
    ''')
