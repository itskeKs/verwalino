"""Single-use mailbox password recovery with atomic T57 session revocation."""
from alembic import op

revision = '0144'
down_revision = '0143'
branch_labels = None
depends_on = None


def upgrade():
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
    GRANT SELECT,INSERT,UPDATE,DELETE ON public.password_reset_request TO app_rw;

    CREATE FUNCTION public.reset_password_with_token(token text, password_hash text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE pending public.password_reset_request%ROWTYPE; supplied bytea; expected bytea;
      difference integer := 0; byte_index integer; saved_org text; changed uuid;
    BEGIN
      supplied := sha256(convert_to(COALESCE(token,''),'UTF8'));
      -- Read the tenant before locking, then use the same ACL -> row order as T57.
      SELECT * INTO pending FROM public.password_reset_request WHERE token_hash=supplied;
      saved_org := current_setting('app.current_org_id',true);
      IF pending.id IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||pending.organization_id::text,0));
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
      -- The existing T57 trigger increments session_version when the hash changes.
      UPDATE public."user" u SET password_hash=$2
        WHERE u.id=pending.user_id AND u.organization_id=pending.organization_id
          AND u.is_active AND u.password_hash IS DISTINCT FROM $2
          AND EXISTS(SELECT 1 FROM public.membership m WHERE m.user_id=u.id
            AND m.organization_id=u.organization_id AND m.role IN ('OWNER_ADMIN','CLERK','READ_ONLY'))
        RETURNING u.id INTO changed;
      IF changed IS NULL THEN RAISE EXCEPTION 'Password reset refused'; END IF;
      UPDATE public.password_reset_request SET used_at=clock_timestamp()
        WHERE user_id=changed AND used_at IS NULL;
      PERFORM set_config('app.current_org_id',COALESCE(saved_org,''),true);
      RETURN changed;
    EXCEPTION WHEN OTHERS THEN
      RAISE EXCEPTION 'Password reset refused' USING ERRCODE='P0001';
    END $$;
    REVOKE ALL ON FUNCTION public.reset_password_with_token(text,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.reset_password_with_token(text,text) TO app_rw;
    ''')


def downgrade():
    op.execute('''
    LOCK TABLE public.password_reset_request IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.password_reset_request) THEN
        RAISE EXCEPTION 'Cannot discard retained password reset state';
      END IF;
    END $$;
    DROP FUNCTION public.reset_password_with_token(text,text);
    DROP TABLE public.password_reset_request;
    ''')
