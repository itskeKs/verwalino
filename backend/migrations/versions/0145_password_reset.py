"""Single-use mailbox password recovery with atomic T57 session revocation."""
from alembic import op
from sqlalchemy import text

revision = '0145'
down_revision = '0144'
branch_labels = None
depends_on = None


# Restore the preceding migration's lookup body verbatim on downgrade.
_PREVIOUS_AUTH_LOOKUP = """
        CREATE OR REPLACE FUNCTION public.app_auth_lookup(login_email text)
        RETURNS TABLE (
            user_id uuid,
            organization_id uuid,
            password_hash text,
            is_active boolean,
            role text
        )
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
            WITH candidates AS (
                SELECT u.id AS user_id,
                       u.organization_id,
                       u.password_hash,
                       u.is_active,
                       m.role
                FROM public."user" AS u
                JOIN public.membership AS m
                  ON m.user_id = u.id
                 AND m.organization_id = u.organization_id
                WHERE pg_catalog.lower(pg_catalog.btrim(u.email)) =
                      pg_catalog.lower(pg_catalog.btrim(login_email))
            )
            SELECT user_id, organization_id, password_hash, is_active, role
            FROM candidates
            WHERE (SELECT pg_catalog.count(*) FROM candidates) = 1
        $$;
        """


def upgrade():
    op.execute(_PREVIOUS_AUTH_LOOKUP.replace(
        'pg_catalog.lower(pg_catalog.btrim(login_email))',
        "pg_catalog.lower(pg_catalog.btrim(login_email))\n"
        "                  AND u.is_active\n"
        "                  AND m.role IN ('OWNER_ADMIN','CLERK','READ_ONLY')"))
    connection = op.get_bind()
    crypto_schema = connection.execute(text("""SELECT n.nspname FROM pg_extension e
        JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pgcrypto'""")).scalar()
    # PostgreSQL UUIDs carry 122 random bits each; the fallback needs no extension.
    token_expression = "replace(gen_random_uuid()::text||gen_random_uuid()::text,'-','')"
    if crypto_schema is not None:
        schema = connection.dialect.identifier_preparer.quote_schema(crypto_schema)
        token_expression = f"encode({schema}.gen_random_bytes(32),'hex')"
    op.execute('''
    CREATE TABLE public.password_reset_request (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id uuid NOT NULL,
      organization_id uuid NOT NULL,
      token_hash bytea NOT NULL UNIQUE CHECK (octet_length(token_hash)=32),
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      expires_at timestamptz NOT NULL DEFAULT statement_timestamp()+interval '1 hour',
      used_at timestamptz,
      FOREIGN KEY (organization_id,user_id) REFERENCES public."user"(organization_id,id) ON DELETE CASCADE
    );
    CREATE INDEX ix_password_reset_user ON public.password_reset_request(user_id);
    REVOKE ALL ON public.password_reset_request FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION public.issue_password_reset(email text) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor public."user"%ROWTYPE; token text;
    BEGIN
      IF COALESCE(current_setting('app.current_org_id',true),'')<>'' THEN RETURN NULL; END IF;
      -- Generate equal random work even when no active, unique recipient exists.
      token := TOKEN_EXPRESSION;
      SELECT u.* INTO actor FROM public.app_auth_lookup($1) p
        JOIN public."user" u ON u.id=p.user_id AND u.organization_id=p.organization_id;
      IF actor.id IS NULL THEN RETURN NULL; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||actor.organization_id::text,0));
      SELECT u.* INTO actor FROM public.app_auth_lookup($1) p
        JOIN public."user" u ON u.id=p.user_id AND u.organization_id=p.organization_id
        WHERE u.id=actor.id FOR UPDATE OF u;
      IF actor.id IS NULL THEN RETURN NULL; END IF;
      UPDATE public.password_reset_request SET used_at=clock_timestamp()
        WHERE user_id=actor.id AND used_at IS NULL;
      INSERT INTO public.password_reset_request(user_id,organization_id,token_hash)
        VALUES (actor.id,actor.organization_id,sha256(convert_to(token,'UTF8')));
      RETURN token;
    END $$;
    REVOKE ALL ON FUNCTION public.issue_password_reset(text) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.issue_password_reset(text) TO app_rw;

    -- Keep T57's invoker generation guard unchanged. This associated AFTER
    -- trigger can consume global reset rows without granting runtime table DML.
    CREATE FUNCTION public.t57_consume_password_reset() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      UPDATE public.password_reset_request SET used_at=clock_timestamp()
        WHERE user_id=NEW.id AND organization_id=NEW.organization_id AND used_at IS NULL;
      RETURN NULL;
    END $$;
    REVOKE ALL ON FUNCTION public.t57_consume_password_reset() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER t57_consume_password_reset AFTER UPDATE ON public."user"
      FOR EACH ROW WHEN (OLD.password_hash IS DISTINCT FROM NEW.password_hash
        OR (OLD.is_active AND NOT NEW.is_active))
      EXECUTE FUNCTION public.t57_consume_password_reset();

    CREATE FUNCTION public.reset_password_with_token(token text, password_hash text) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE pending public.password_reset_request%ROWTYPE; supplied bytea; expected bytea;
      difference integer := 0; byte_index integer; recipient text;
    BEGIN
      IF COALESCE(current_setting('app.current_org_id',true),'')<>'' THEN
        RAISE EXCEPTION 'Password reset refused';
      END IF;
      supplied := sha256(convert_to(COALESCE(token,''),'UTF8'));
      SELECT * INTO pending FROM public.password_reset_request WHERE token_hash=supplied;
      IF pending.id IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||pending.organization_id::text,0));
        -- Match password-change lock order: ACL, user, then reset rows.
        PERFORM 1 FROM public."user" WHERE id=pending.user_id
          AND organization_id=pending.organization_id FOR UPDATE;
      END IF;
      SELECT * INTO pending FROM public.password_reset_request WHERE token_hash=supplied FOR UPDATE;
      expected := COALESCE(pending.token_hash,decode(repeat('00',32),'hex'));
      FOR byte_index IN 0..31 LOOP
        difference := difference | (get_byte(supplied,byte_index) # get_byte(expected,byte_index));
      END LOOP;
      IF difference<>0 OR pending.id IS NULL OR pending.used_at IS NOT NULL
        OR pending.expires_at<=clock_timestamp() OR password_hash IS NULL
        OR password_hash NOT LIKE '$argon2id$%' THEN
        RAISE EXCEPTION 'Password reset refused';
      END IF;
      PERFORM set_config('app.current_org_id',pending.organization_id::text,true);
      UPDATE public."user" u SET password_hash=$2
        WHERE u.id=pending.user_id AND u.organization_id=pending.organization_id
          AND u.password_hash IS DISTINCT FROM $2
          AND EXISTS(SELECT 1 FROM public.app_auth_lookup(u.email) p
            WHERE p.user_id=u.id AND p.organization_id=u.organization_id)
        RETURNING u.email INTO recipient;
      IF recipient IS NULL THEN RAISE EXCEPTION 'Password reset refused'; END IF;
      PERFORM set_config('app.current_org_id','',true);
      -- Return only the confirmed recipient needed for the security notification.
      RETURN recipient;
    END $$;
    REVOKE ALL ON FUNCTION public.reset_password_with_token(text,text) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.reset_password_with_token(text,text) TO app_rw;

    CREATE FUNCTION public.cleanup_password_reset_requests() RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE removed integer;
    BEGIN
      IF COALESCE(current_setting('app.current_org_id',true),'')<>'' THEN RETURN 0; END IF;
      -- Materialize the locked batch once; a semi-join may rescan an IN subquery.
      WITH expired AS MATERIALIZED (
        SELECT id FROM public.password_reset_request
        WHERE expires_at<clock_timestamp()-interval '7 days'
          OR used_at<clock_timestamp()-interval '7 days'
        ORDER BY expires_at,id LIMIT 100 FOR UPDATE SKIP LOCKED)
      DELETE FROM public.password_reset_request AS requests
        USING expired WHERE requests.id=expired.id;
      GET DIAGNOSTICS removed = ROW_COUNT;
      RETURN removed;
    END $$;
    REVOKE ALL ON FUNCTION public.cleanup_password_reset_requests() FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.cleanup_password_reset_requests() TO app_rw;
    '''.replace('TOKEN_EXPRESSION', token_expression))


def downgrade():
    op.execute('''
    LOCK TABLE public.password_reset_request IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.password_reset_request) THEN
        RAISE EXCEPTION 'Cannot discard retained password reset state';
      END IF;
    END $$;
    DROP TRIGGER t57_consume_password_reset ON public."user";
    DROP FUNCTION public.t57_consume_password_reset();
    DROP FUNCTION public.cleanup_password_reset_requests();
    DROP FUNCTION public.reset_password_with_token(text,text);
    DROP FUNCTION public.issue_password_reset(text);
    DROP TABLE public.password_reset_request;
    ''')
    op.execute(_PREVIOUS_AUTH_LOOKUP)
