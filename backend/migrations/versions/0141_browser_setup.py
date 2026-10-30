"""Token-gated first organization with no direct runtime organization creation."""
from alembic import op

revision = "0141"
down_revision = "0140"
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    CREATE TABLE public.setup_token (
      singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
      token_hash bytea NOT NULL CHECK (octet_length(token_hash)=32),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      used_at timestamptz
    );
    REVOKE ALL ON public.setup_token FROM PUBLIC,app_rw,app_worker,app_review;
    REVOKE INSERT ON public.organization FROM app_rw;

    -- Shared with the migrator CLI. Invokers need table privileges; app_rw has
    -- neither EXECUTE nor organization INSERT. Only the gated definer calls it.
    CREATE FUNCTION public.bootstrap_organization_admin(
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
    REVOKE ALL ON FUNCTION public.bootstrap_organization_admin(text,text,text,text) FROM PUBLIC;

    CREATE FUNCTION public.setup_first_organization(
      token text, org_name text, admin_email text, admin_name text, password_hash text)
    RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE pending public.setup_token%ROWTYPE; supplied bytea; expected bytea;
      difference integer := 0; byte_index integer; org uuid;
    BEGIN
      -- Conflicts with every organization INSERT and with another setup/CLI.
      LOCK TABLE public.organization IN SHARE ROW EXCLUSIVE MODE;
      IF EXISTS(SELECT 1 FROM public.organization) THEN
        RAISE EXCEPTION 'Setup refused';
      END IF;
      SELECT * INTO pending FROM public.setup_token WHERE singleton FOR UPDATE;
      supplied := sha256(convert_to(COALESCE(token,''),'UTF8'));
      expected := COALESCE(pending.token_hash,decode(repeat('00',32),'hex'));
      -- Always compare all 32 hash bytes. No prefix/length-dependent exit.
      FOR byte_index IN 0..31 LOOP
        difference := difference | (get_byte(supplied,byte_index) # get_byte(expected,byte_index));
      END LOOP;
      IF difference <> 0 OR pending.token_hash IS NULL OR pending.used_at IS NOT NULL
        OR pending.created_at <= clock_timestamp()-interval '24 hours' THEN
        RAISE EXCEPTION 'Setup refused';
      END IF;
      org := public.bootstrap_organization_admin(org_name,admin_email,admin_name,password_hash);
      RETURN org;
    EXCEPTION WHEN OTHERS THEN
      -- Neither constraint diagnostics nor supplied values cross this boundary.
      RAISE EXCEPTION 'Setup refused' USING ERRCODE='P0001';
    END $$;
    REVOKE ALL ON FUNCTION public.setup_first_organization(text,text,text,text,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.setup_first_organization(text,text,text,text,text) TO app_rw;
    ''')


def downgrade():
    op.execute('''
    LOCK TABLE public.organization IN SHARE ROW EXCLUSIVE MODE;
    LOCK TABLE public.setup_token IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.setup_token) THEN
        RAISE EXCEPTION 'Cannot discard retained setup token state';
      END IF;
    END $$;
    DROP FUNCTION public.setup_first_organization(text,text,text,text,text);
    DROP FUNCTION public.bootstrap_organization_admin(text,text,text,text);
    DROP TABLE public.setup_token;
    GRANT INSERT ON public.organization TO app_rw;
    ''')
