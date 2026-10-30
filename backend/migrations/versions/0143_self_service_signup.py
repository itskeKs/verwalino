"""Hosted signup reservations and a narrow atomic provisioning boundary."""
from alembic import op

revision = "0143"
down_revision = "0142"
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    -- The unique reservation survives confirmation: pending and active signup
    -- identities share one index without rewriting legacy organization accounts.
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
        OR public.signup_email_registered(pending.email_normalized)
        OR EXISTS(SELECT 1 FROM public.organization WHERE name=pending.organization_name) THEN
        RAISE EXCEPTION 'Signup refused';
      END IF;
      org := public.bootstrap_organization_admin(pending.organization_name,pending.email_normalized,
                                                pending.admin_name,pending.password_hash);
      UPDATE public.signup_request SET confirmed_at=clock_timestamp(),password_hash=NULL WHERE id=pending.id;
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
    END $$;
    DROP FUNCTION public.provision_organization(text);
    DROP FUNCTION public.signup_email_registered(text);
    DROP TABLE public.signup_request;
    ''')
