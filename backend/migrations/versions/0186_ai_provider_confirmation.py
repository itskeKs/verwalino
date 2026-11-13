"""Recorded provider confirmation required before an organization AI key is stored (issue #617)."""

from alembic import op
from sqlalchemy import text

revision = "0186"
down_revision = "0185"
branch_labels = None
depends_on = None


def patch_import_admission(reverse=False):
    """Confirmation rows are evidence, not data: like assents and invitations
    they must not make an organization non-empty for archive import. The
    0147-owned body is replaced here, not edited."""
    definition = op.get_bind().scalar(
        text(
            "SELECT pg_get_functiondef("
            "'public.organization_import_admission(uuid,jsonb)'::regprocedure)"
        )
    )
    before = "'organization_deletion','password_reset_request','user_invitation','legal_document_assent'"
    after = "'organization_deletion','password_reset_request','user_invitation','legal_document_assent','organization_ai_confirmation'"
    if reverse:
        before, after = after, before
        if before not in definition and after in definition:
            return
    assert definition.count(before) == 1
    op.execute(definition.replace(before, after))


def upgrade():
    op.execute("""
    CREATE TABLE public.organization_ai_confirmation (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      organization_id uuid NOT NULL REFERENCES public.organization(id) ON DELETE RESTRICT,
      confirmed_by_user_id uuid NOT NULL,
      provider varchar(32) NOT NULL,
      base_url varchar(2048),
      text_version varchar(32) NOT NULL,
      confirmed_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      FOREIGN KEY (organization_id,confirmed_by_user_id) REFERENCES public."user"(organization_id,id) ON DELETE RESTRICT
    );
    CREATE INDEX ix_organization_ai_confirmation_org ON public.organization_ai_confirmation(organization_id,confirmed_at);
    ALTER TABLE public.organization_ai_confirmation ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.organization_ai_confirmation FORCE ROW LEVEL SECURITY;
    CREATE POLICY organization_ai_confirmation_read ON public.organization_ai_confirmation FOR SELECT TO app_rw
      USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid);
    CREATE POLICY organization_ai_confirmation_admin ON public.organization_ai_confirmation FOR ALL TO app_rw
      USING (organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND public.scope_actor_role(organization_id)='OWNER_ADMIN')
      WITH CHECK (organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND public.scope_actor_role(organization_id)='OWNER_ADMIN');
    REVOKE ALL ON public.organization_ai_confirmation FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT SELECT,INSERT,UPDATE,DELETE ON public.organization_ai_confirmation TO app_rw;
    CREATE TRIGGER a_organization_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON public.organization_ai_confirmation
      FOR EACH ROW EXECUTE FUNCTION public.organization_lifecycle_guard();
    """)
    patch_import_admission()


def downgrade():
    op.execute("""
    LOCK TABLE public.organization_ai_confirmation IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.organization_ai_confirmation) THEN
        RAISE EXCEPTION 'Cannot discard recorded AI provider confirmations';
      END IF;
    END $$;
    DROP TABLE public.organization_ai_confirmation;
    """)
    patch_import_admission(reverse=True)
