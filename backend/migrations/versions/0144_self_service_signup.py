"""Hosted signup reservations and a narrow atomic provisioning boundary."""
from alembic import op

revision = "0144"
down_revision = "0143"
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
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
      password_hash text NOT NULL,
      token_hash bytea NOT NULL UNIQUE CHECK (octet_length(token_hash)=32),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '24 hours',
      resent_at timestamptz
    );
    REVOKE ALL ON public.signup_request FROM PUBLIC,app_rw,app_worker,app_review;

    -- Includes inactive, memberless and ambiguous legacy identities that the
    -- deliberately non-enumerating authentication lookup cannot expose.
    CREATE FUNCTION public.signup_email_registered(email text) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT EXISTS(SELECT 1 FROM public."user" u WHERE lower(btrim(u.email))=lower(btrim($1)))
    $$;
    REVOKE ALL ON FUNCTION public.signup_email_registered(text) FROM PUBLIC;


    -- No pgcrypto extension is installed. Two PostgreSQL UUIDs provide 244
    -- random bits; only their SHA-256 digest is stored, never a caller's token.
    CREATE FUNCTION public.request_signup(email text, org_name text, admin_name text, password_hash text)
    RETURNS text LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE pending public.signup_request%ROWTYPE; token text; now_at timestamptz;
    BEGIN
      IF COALESCE(current_setting('app.current_org_id',true),'')<>'' THEN RETURN NULL; END IF;
      -- Match provisioning's organization -> request lock order, including races
      -- with existing identities becoming registered during a form submission.
      LOCK TABLE public.organization IN SHARE ROW EXCLUSIVE MODE;
      IF public.signup_email_registered(email) THEN RETURN NULL; END IF;
      IF password_hash IS NULL OR password_hash NOT LIKE '$argon2id$%' THEN RETURN NULL; END IF;
      token := replace(gen_random_uuid()::text||gen_random_uuid()::text,'-','');
      now_at := clock_timestamp();
      INSERT INTO public.signup_request(email_normalized,organization_name,admin_name,password_hash,
                                        token_hash,created_at,expires_at)
        VALUES (lower(btrim(email)),org_name,admin_name,password_hash,
                sha256(convert_to(token,'UTF8')),now_at,now_at+interval '24 hours')
        ON CONFLICT (email_normalized) DO NOTHING RETURNING * INTO pending;
      IF pending.id IS NOT NULL THEN RETURN token; END IF;
      SELECT * INTO pending FROM public.signup_request
        WHERE email_normalized=lower(btrim(email)) FOR UPDATE;
      -- A live request retains its original credentials and names until its
      -- fixed expiry. An expired request accepts replacement details.
      IF pending.expires_at<=now_at THEN
        UPDATE public.signup_request SET organization_name=org_name,admin_name=$3,password_hash=$4,
          token_hash=sha256(convert_to(token,'UTF8')),created_at=now_at,
          expires_at=now_at+interval '24 hours',resent_at=NULL WHERE id=pending.id;
        RETURN token;
      END IF;
      RETURN public.resend_signup(email);
    END $$;
    REVOKE ALL ON FUNCTION public.request_signup(text,text,text,text) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.request_signup(text,text,text,text) TO app_rw;

    CREATE FUNCTION public.resend_signup(email text) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE pending public.signup_request%ROWTYPE; token text; now_at timestamptz;
    BEGIN
      IF COALESCE(current_setting('app.current_org_id',true),'')<>'' THEN RETURN NULL; END IF;
      LOCK TABLE public.organization IN SHARE ROW EXCLUSIVE MODE;
      IF public.signup_email_registered(email) THEN RETURN NULL; END IF;
      SELECT * INTO pending FROM public.signup_request
        WHERE email_normalized=lower(btrim(email)) FOR UPDATE;
      now_at := clock_timestamp();
      IF pending.id IS NULL OR COALESCE(pending.resent_at,pending.created_at)+interval '10 minutes'>now_at
        THEN RETURN NULL; END IF;
      token := replace(gen_random_uuid()::text||gen_random_uuid()::text,'-','');
      UPDATE public.signup_request SET token_hash=sha256(convert_to(token,'UTF8')),
        resent_at=now_at WHERE id=pending.id;
      RETURN token;
    END $$;
    REVOKE ALL ON FUNCTION public.resend_signup(text) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.resend_signup(text) TO app_rw;

    -- Token-bound password snapshot for Argon2 verification outside a transaction.
    CREATE FUNCTION public.signup_confirmation(token text)
    RETURNS TABLE(email_normalized text,password_hash text)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT r.email_normalized,r.password_hash FROM public.signup_request r
      WHERE COALESCE(current_setting('app.current_org_id',true),'')=''
        AND r.token_hash=sha256(convert_to(COALESCE(token,''),'UTF8'))
        AND r.expires_at>statement_timestamp()
    $$;
    REVOKE ALL ON FUNCTION public.signup_confirmation(text) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.signup_confirmation(text) TO app_rw;

    CREATE FUNCTION public.cleanup_signup_requests() RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE removed integer;
    BEGIN
      IF COALESCE(current_setting('app.current_org_id',true),'')<>'' THEN RETURN 0; END IF;
      DELETE FROM public.signup_request WHERE id IN (
        SELECT id FROM public.signup_request WHERE expires_at<clock_timestamp()-interval '7 days'
        ORDER BY expires_at,id LIMIT 100 FOR UPDATE SKIP LOCKED);
      GET DIAGNOSTICS removed = ROW_COUNT;
      RETURN removed;
    END $$;
    REVOKE ALL ON FUNCTION public.cleanup_signup_requests() FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.cleanup_signup_requests() TO app_rw;

    CREATE FUNCTION public.provision_organization(token text, password_hash text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE pending public.signup_request%ROWTYPE; supplied bytea; expected bytea;
      difference integer := 0; byte_index integer; org uuid;
    BEGIN
      IF COALESCE(current_setting('app.current_org_id',true),'')<>'' THEN
        RAISE EXCEPTION 'Signup refused';
      END IF;
      -- Same order as setup/bootstrap. Never let bootstrap reuse a tenant by name.
      LOCK TABLE public.organization IN SHARE ROW EXCLUSIVE MODE;
      supplied := sha256(convert_to(COALESCE(token,''),'UTF8'));
      SELECT * INTO pending FROM public.signup_request WHERE token_hash=supplied FOR UPDATE;
      expected := COALESCE(pending.token_hash,decode(repeat('00',32),'hex'));
      FOR byte_index IN 0..31 LOOP
        difference := difference | (get_byte(supplied,byte_index) # get_byte(expected,byte_index));
      END LOOP;
      IF difference<>0 OR pending.id IS NULL
        OR pending.expires_at<=clock_timestamp() OR password_hash IS NULL
        OR pending.password_hash IS DISTINCT FROM password_hash
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
    REVOKE ALL ON FUNCTION public.provision_organization(text,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.provision_organization(text,text) TO app_rw;
    ''')


def downgrade():
    op.execute('''
    LOCK TABLE public.signup_request IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.signup_request) THEN
        RAISE EXCEPTION 'Cannot discard retained signup state';
      END IF;
    END $$;
    DROP FUNCTION public.provision_organization(text,text);
    DROP FUNCTION public.request_signup(text,text,text,text);
    DROP FUNCTION public.resend_signup(text);
    DROP FUNCTION public.signup_confirmation(text);
    DROP FUNCTION public.cleanup_signup_requests();
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
    ''')
