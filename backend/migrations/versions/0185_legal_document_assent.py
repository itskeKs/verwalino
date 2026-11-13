"""Versioned legal-document assent recorded at hosted sign-up (issue #633)."""

from alembic import op
from sqlalchemy import text

revision = "0185"
down_revision = "0184"
branch_labels = None
depends_on = None


def patch_retention_catalog(reverse=False):
    """Classify the assent record explicitly: it is contract-conclusion
    evidence (Art. 28 GDPR), retained like delete-protected records; the
    0147-owned body is replaced here, not edited."""
    definition = op.get_bind().scalar(
        text(
            "SELECT pg_get_functiondef('public.organization_retention_catalog()'::regprocedure)"
        )
    )
    before = "          WHEN protected THEN 'retain'"
    after = (
        "          -- Assent records prove contract conclusion and survive\n"
        "          -- organization erasure like delete-protected evidence.\n"
        "          WHEN entity='legal_document_assent' THEN 'retain'\n"
        "          WHEN protected THEN 'retain'"
    )
    if reverse:
        before, after = after, before
        if before not in definition and after in definition:
            return
    assert definition.count(before) == 1
    op.execute(definition.replace(before, after))


def patch_import_admission(reverse=False):
    """Assent rows exist in every hosted organization from confirmation on;
    like invitations they must not make an organization non-empty for
    archive import. The 0147-owned body is replaced here, not edited."""
    definition = op.get_bind().scalar(
        text(
            "SELECT pg_get_functiondef("
            "'public.organization_import_admission(uuid,jsonb)'::regprocedure)"
        )
    )
    before = "'organization_deletion','password_reset_request','user_invitation'"
    after = "'organization_deletion','password_reset_request','user_invitation','legal_document_assent'"
    if reverse:
        before, after = after, before
        if before not in definition and after in definition:
            return
    assert definition.count(before) == 1
    op.execute(definition.replace(before, after))


def upgrade():
    op.execute("""
    ALTER TABLE public.signup_request
      ADD COLUMN terms_version text,
      ADD COLUMN av_version text;

    CREATE TABLE public.legal_document_assent (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      organization_id uuid NOT NULL REFERENCES public.organization(id) ON DELETE RESTRICT,
      user_id uuid NOT NULL,
      document text NOT NULL CHECK (document IN ('TERMS','AV')),
      version text NOT NULL CHECK (version ~ '^\\d{4}-\\d{2}-\\d{2}$'),
      assented_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      FOREIGN KEY (organization_id,user_id) REFERENCES public."user"(organization_id,id) ON DELETE RESTRICT
    );
    CREATE INDEX ix_legal_document_assent_org ON public.legal_document_assent(organization_id,assented_at);
    ALTER TABLE public.legal_document_assent ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.legal_document_assent FORCE ROW LEVEL SECURITY;
    CREATE POLICY legal_document_assent_read ON public.legal_document_assent FOR SELECT TO app_rw
      USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid);
    REVOKE ALL ON public.legal_document_assent FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT SELECT ON public.legal_document_assent TO app_rw;
    CREATE TRIGGER a_organization_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON public.legal_document_assent
      FOR EACH ROW EXECUTE FUNCTION public.organization_lifecycle_guard();

    -- Like 0144's request_signup, but the request stores the assented document
    -- versions; the hosted API validates them against the current constants
    -- before this function is reached. 0144's function stays byte-identical
    -- because release rehearsals compare the accepted source exactly.
    CREATE FUNCTION public.request_signup_assented(email text, org_name text, admin_name text, password_hash text,
                                                   terms_version text, av_version text)
    RETURNS text LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE pending public.signup_request%ROWTYPE; token text; now_at timestamptz;
    BEGIN
      IF COALESCE(current_setting('app.current_org_id',true),'')<>'' THEN RETURN NULL; END IF;
      IF terms_version IS NULL OR av_version IS NULL
        OR terms_version !~ '^\\d{4}-\\d{2}-\\d{2}$' OR av_version !~ '^\\d{4}-\\d{2}-\\d{2}$' THEN
        RETURN NULL;
      END IF;
      LOCK TABLE public.organization IN SHARE ROW EXCLUSIVE MODE;
      IF public.signup_email_registered(email) THEN RETURN NULL; END IF;
      IF password_hash IS NULL OR password_hash NOT LIKE '$argon2id$%' THEN RETURN NULL; END IF;
      token := replace(gen_random_uuid()::text||gen_random_uuid()::text,'-','');
      now_at := clock_timestamp();
      INSERT INTO public.signup_request(email_normalized,organization_name,admin_name,password_hash,
                                        token_hash,created_at,expires_at,terms_version,av_version)
        VALUES (lower(btrim(email)),org_name,admin_name,password_hash,
                sha256(convert_to(token,'UTF8')),now_at,now_at+interval '24 hours',terms_version,av_version)
        ON CONFLICT (email_normalized) DO NOTHING RETURNING * INTO pending;
      IF pending.id IS NOT NULL THEN RETURN token; END IF;
      SELECT * INTO pending FROM public.signup_request
        WHERE email_normalized=lower(btrim(email)) FOR UPDATE;
      -- A live request retains its original credentials and names until its
      -- fixed expiry. An expired request accepts replacement details.
      IF pending.expires_at<=now_at THEN
        UPDATE public.signup_request SET organization_name=org_name,admin_name=$3,password_hash=$4,
          token_hash=sha256(convert_to(token,'UTF8')),created_at=now_at,
          expires_at=now_at+interval '24 hours',resent_at=NULL,
          terms_version=$5,av_version=$6 WHERE id=pending.id;
        RETURN token;
      END IF;
      RETURN public.resend_signup(email);
    END $$;
    REVOKE ALL ON FUNCTION public.request_signup_assented(text,text,text,text,text,text) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.request_signup_assented(text,text,text,text,text,text) TO app_rw;

    -- Provisioning deletes the signup_request row in the same transaction that
    -- creates organization, administrator and membership (0144); this trigger
    -- records both assents for exactly that delete. A user with this address
    -- created in the same transaction (created_at = now() = the transaction
    -- start) identifies the provisioning delete, so expiry cleanup and
    -- rehearsal deletes of old requests pass through. xmin cannot identify
    -- the row here: definer exception blocks run the inserts in a
    -- subtransaction with its own xid.
    CREATE FUNCTION public.record_signup_assent() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE admin_user public."user"%ROWTYPE;
    BEGIN
      SELECT * INTO admin_user FROM public."user" u
        WHERE u.email=OLD.email_normalized AND u.created_at>=transaction_timestamp()
        ORDER BY u.created_at DESC LIMIT 1;
      IF admin_user.id IS NULL THEN RETURN OLD; END IF;
      IF OLD.terms_version IS NULL OR OLD.av_version IS NULL
        OR OLD.terms_version !~ '^\\d{4}-\\d{2}-\\d{2}$'
        OR OLD.av_version !~ '^\\d{4}-\\d{2}-\\d{2}$' THEN
        RAISE EXCEPTION 'Signup refused';
      END IF;
      INSERT INTO public.legal_document_assent(organization_id,user_id,document,version,assented_at)
        VALUES (admin_user.organization_id,admin_user.id,'TERMS',OLD.terms_version,clock_timestamp()),
               (admin_user.organization_id,admin_user.id,'AV',OLD.av_version,clock_timestamp());
      RETURN OLD;
    END $$;
    REVOKE ALL ON FUNCTION public.record_signup_assent() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER t_record_signup_assent BEFORE DELETE ON public.signup_request
      FOR EACH ROW EXECUTE FUNCTION public.record_signup_assent();

    -- Portable-archive restore (issue #633 round 1): the table stays read-only
    -- for the runtime role; the import replays archived assent records through
    -- this narrow definer, which admits only the organization's administrator
    -- during an import into that same organization.
    CREATE FUNCTION public.restore_legal_document_assent(id uuid, organization_id uuid, user_id uuid,
                                                         document text, version text, assented_at timestamptz)
    RETURNS void LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF session_user<>'app_rw'
        OR COALESCE(NULLIF(current_setting('app.current_org_id',true),''),'00000000-0000-0000-0000-000000000000')::uuid
           IS DISTINCT FROM organization_id
        OR public.scope_actor_role(organization_id) IS DISTINCT FROM 'OWNER_ADMIN' THEN
        RAISE EXCEPTION 'Current administrator required' USING ERRCODE='42501';
      END IF;
      INSERT INTO public.legal_document_assent(id,organization_id,user_id,document,version,assented_at)
        VALUES (id,organization_id,user_id,document,version,assented_at);
    END $$;
    REVOKE ALL ON FUNCTION public.restore_legal_document_assent(uuid,uuid,uuid,text,text,timestamptz) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.restore_legal_document_assent(uuid,uuid,uuid,text,text,timestamptz) TO app_rw;

    """)
    patch_retention_catalog()
    patch_import_admission()


def downgrade():
    op.execute("""
    DROP FUNCTION public.restore_legal_document_assent(uuid,uuid,uuid,text,text,timestamptz);
    LOCK TABLE public.legal_document_assent IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.legal_document_assent) THEN
        RAISE EXCEPTION 'Cannot discard retained assent state';
      END IF;
    END $$;
    DROP TRIGGER t_record_signup_assent ON public.signup_request;
    DROP FUNCTION public.record_signup_assent();
    DROP FUNCTION public.request_signup_assented(text,text,text,text,text,text);
    DROP TABLE public.legal_document_assent;
    ALTER TABLE public.signup_request DROP COLUMN terms_version, DROP COLUMN av_version;
    """)
    patch_retention_catalog(reverse=True)
    patch_import_admission(reverse=True)
