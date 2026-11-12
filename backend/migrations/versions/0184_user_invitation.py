"""Single-use user invitations with hashed tokens and tenant row security."""

from alembic import op
from sqlalchemy import text

revision = "0184"
down_revision = "0183"
branch_labels = None
depends_on = None


def patch_retention_catalog(reverse=False):
    """Classify the new table for erasure by patching the live catalog body."""
    definition = op.get_bind().scalar(
        text(
            "SELECT pg_get_functiondef('public.organization_retention_catalog()'::regprocedure)"
        )
    )
    before = "WHEN entity='password_reset_request' THEN 'delete'"
    after = "WHEN entity IN ('password_reset_request','user_invitation') THEN 'delete'"
    if reverse:
        before, after = after, before
        if before not in definition and after in definition:
            return
    assert definition.count(before) == 1
    op.execute(definition.replace(before, after))


def patch_import_admission(reverse=False):
    """Invitation rows are pending identity, not data: like
    password_reset_request they must not make an organization non-empty
    for archive import. The 0147-owned body is replaced here, not edited."""
    definition = op.get_bind().scalar(
        text(
            "SELECT pg_get_functiondef("
            "'public.organization_import_admission(uuid,jsonb)'::regprocedure)"
        )
    )
    before = "'organization_deletion','password_reset_request'"
    after = "'organization_deletion','password_reset_request','user_invitation'"
    if reverse:
        before, after = after, before
        if before not in definition and after in definition:
            return
    assert definition.count(before) == 1
    op.execute(definition.replace(before, after))


def patch_password_reset_cleanup(reverse=False):
    """Finished invitations share the password-reset cleanup: seven days
    after they finished (accepted, revoked or expired) they are deleted by
    the same bounded mechanism and schedule; the 0145-owned body is
    replaced here, not edited."""
    definition = op.get_bind().scalar(
        text(
            "SELECT pg_get_functiondef("
            "'public.cleanup_password_reset_requests()'::regprocedure)"
        )
    )
    before = "      GET DIAGNOSTICS removed = ROW_COUNT;\n      RETURN removed;"
    after = (
        "      GET DIAGNOSTICS removed = ROW_COUNT;\n"
        "      WITH finished AS MATERIALIZED (\n"
        "        SELECT id FROM public.user_invitation\n"
        "        WHERE accepted_at<clock_timestamp()-interval '7 days'\n"
        "          OR revoked_at<clock_timestamp()-interval '7 days'\n"
        "          OR (accepted_at IS NULL AND revoked_at IS NULL\n"
        "              AND expires_at<clock_timestamp()-interval '7 days')\n"
        "        ORDER BY COALESCE(accepted_at,revoked_at,expires_at),id\n"
        "        LIMIT 100 FOR UPDATE SKIP LOCKED)\n"
        "      DELETE FROM public.user_invitation AS invitations\n"
        "        USING finished WHERE invitations.id=finished.id;\n"
        "      GET DIAGNOSTICS invitation_removed = ROW_COUNT;\n"
        "      RETURN removed+invitation_removed;"
    )
    if reverse:
        before, after = after, before
        if before not in definition and after in definition:
            return
    assert definition.count(before) == 1
    definition = definition.replace(before, after)
    declaration = "DECLARE removed integer;"
    extended = "DECLARE removed integer; invitation_removed integer;"
    if not reverse:
        assert extended not in definition
        definition = definition.replace(declaration, extended)
    else:
        definition = definition.replace(extended, declaration)
    op.execute(definition)


def upgrade():
    op.execute("""
    CREATE TABLE public.user_invitation (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      organization_id uuid NOT NULL REFERENCES public.organization(id) ON DELETE RESTRICT,
      email text NOT NULL,
      role text NOT NULL CHECK (role IN ('OWNER_ADMIN','CLERK','READ_ONLY')),
      invited_by uuid NOT NULL,
      token_hash bytea NOT NULL UNIQUE CHECK (octet_length(token_hash)=32),
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      expires_at timestamptz NOT NULL,
      last_sent_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      accepted_at timestamptz,
      revoked_at timestamptz,
      FOREIGN KEY (organization_id,invited_by) REFERENCES public."user"(organization_id,id) ON DELETE RESTRICT
    );
    CREATE INDEX ix_user_invitation_org ON public.user_invitation(organization_id);
    ALTER TABLE public.user_invitation ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.user_invitation FORCE ROW LEVEL SECURITY;
    CREATE POLICY user_invitation_read ON public.user_invitation FOR SELECT TO app_rw
      USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid);
    CREATE POLICY user_invitation_admin ON public.user_invitation FOR ALL TO app_rw
      USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND public.scope_actor_role(organization_id) = 'OWNER_ADMIN')
      WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND public.scope_actor_role(organization_id) = 'OWNER_ADMIN');
    REVOKE ALL ON public.user_invitation FROM PUBLIC,app_worker,app_review;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.user_invitation TO app_rw;
    CREATE TRIGGER a_organization_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON public.user_invitation
      FOR EACH ROW EXECUTE FUNCTION public.organization_lifecycle_guard();

    -- Acceptance precedes tenant selection, so it runs through one definer
    -- function instead of runtime table DML; tokens are digests, never stored.
    CREATE FUNCTION public.accept_user_invitation(token text, password_hash text)
    RETURNS TABLE(organization_id uuid, email text, role text)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE pending public.user_invitation%ROWTYPE; inviter public."user"%ROWTYPE;
      supplied bytea; expected bytea;
      difference integer := 0; byte_index integer; created uuid;
    BEGIN
      IF COALESCE(current_setting('app.current_org_id',true),'')<>'' THEN
        RAISE EXCEPTION 'Invitation acceptance refused';
      END IF;
      IF password_hash IS NULL OR password_hash NOT LIKE '$argon2id$%' THEN
        RAISE EXCEPTION 'Invitation acceptance refused';
      END IF;
      supplied := sha256(convert_to(COALESCE(token,''),'UTF8'));
      SELECT * INTO pending FROM public.user_invitation WHERE token_hash=supplied;
      IF pending.id IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||pending.organization_id::text,0));
        SELECT * INTO inviter FROM public."user" u WHERE u.id=pending.invited_by
          AND u.organization_id=pending.organization_id FOR UPDATE;
      END IF;
      SELECT * INTO pending FROM public.user_invitation WHERE token_hash=supplied FOR UPDATE;
      expected := COALESCE(pending.token_hash,decode(repeat('00',32),'hex'));
      FOR byte_index IN 0..31 LOOP
        difference := difference | (get_byte(supplied,byte_index) # get_byte(expected,byte_index));
      END LOOP;
      IF difference<>0 OR pending.id IS NULL OR pending.accepted_at IS NOT NULL
        OR pending.revoked_at IS NOT NULL OR pending.expires_at<=clock_timestamp()
        OR inviter.id IS NULL OR NOT inviter.is_active
        OR NOT EXISTS(SELECT 1 FROM public.membership m WHERE m.organization_id=pending.organization_id
          AND m.user_id=pending.invited_by AND m.role='OWNER_ADMIN') THEN
        RAISE EXCEPTION 'Invitation acceptance refused';
      END IF;
      IF public.signup_email_registered(pending.email) THEN
        RAISE EXCEPTION 'Invitation acceptance refused';
      END IF;
      PERFORM set_config('app.current_org_id',pending.organization_id::text,true);
      INSERT INTO public."user"(id,organization_id,email,password_hash,display_name,is_active)
        VALUES (gen_random_uuid(),pending.organization_id,pending.email,password_hash,'',true)
        RETURNING id INTO created;
      INSERT INTO public.membership(id,organization_id,user_id,role)
        VALUES (gen_random_uuid(),pending.organization_id,created,pending.role);
      UPDATE public.user_invitation SET accepted_at=clock_timestamp() WHERE id=pending.id;
      PERFORM set_config('app.current_org_id','',true);
      RETURN QUERY SELECT pending.organization_id, pending.email, pending.role;
    END $$;
    REVOKE ALL ON FUNCTION public.accept_user_invitation(text,text) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.accept_user_invitation(text,text) TO app_rw;
    """)
    patch_retention_catalog()
    patch_import_admission()
    patch_password_reset_cleanup()


def downgrade():
    op.execute("""
    LOCK TABLE public.user_invitation IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.user_invitation) THEN
        RAISE EXCEPTION 'Cannot discard retained invitation state';
      END IF;
    END $$;
    DROP FUNCTION public.accept_user_invitation(text,text);
    DROP TABLE public.user_invitation;
    """)
    patch_retention_catalog(reverse=True)
    patch_import_admission(reverse=True)
    patch_password_reset_cleanup(reverse=True)
