"""Recorded provider confirmation required before an organization AI key is stored (issue #617)."""

from alembic import op
from sqlalchemy import text

revision = "0186"
down_revision = "0185"
branch_labels = None
depends_on = None


def patch_retention_catalog(reverse=False):
    """The runtime role holds no UPDATE/DELETE on the evidence, so the catalog
    would file it as protected and retain it; confirmations are erased with
    the organization instead. The 0147-owned body is replaced here, not
    edited."""
    definition = op.get_bind().scalar(
        text(
            "SELECT pg_get_functiondef('public.organization_retention_catalog()'::regprocedure)"
        )
    )
    before = "          WHEN protected THEN 'retain'"
    after = (
        "          WHEN entity='organization_ai_confirmation' THEN 'delete'\n"
        "          WHEN protected THEN 'retain'"
    )
    if reverse:
        before, after = after, before
        if before not in definition and after in definition:
            return
    assert definition.count(before) == 1
    op.execute(definition.replace(before, after))


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
    GRANT SELECT,INSERT ON public.organization_ai_confirmation TO app_rw;
    CREATE TRIGGER a_organization_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON public.organization_ai_confirmation
      FOR EACH ROW EXECUTE FUNCTION public.organization_lifecycle_guard();

    -- Evidence rows are insert-only for the runtime (like audit_event): the
    -- database binds the confirmer to the acting session user and the time to
    -- database time, and refuses UPDATE/DELETE. The migrator role stays
    -- exempt so erasure and synthetic test seeds can delete and insert.
    CREATE FUNCTION public.guard_organization_ai_confirmation() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF current_user='app_migrator' AND session_user='app_migrator' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
      END IF;
      IF TG_OP='INSERT' THEN
        IF NULLIF(current_setting('app.current_user_id',true),'')::uuid IS DISTINCT FROM NEW.confirmed_by_user_id THEN
          RAISE EXCEPTION 'AI provider confirmation must name the acting user' USING ERRCODE='23514';
        END IF;
        NEW.confirmed_at := statement_timestamp();
        RETURN NEW;
      END IF;
      RAISE EXCEPTION 'AI provider confirmations are immutable' USING ERRCODE='23514';
    END $$;
    REVOKE ALL ON FUNCTION public.guard_organization_ai_confirmation() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER organization_ai_confirmation_guard BEFORE INSERT OR UPDATE OR DELETE ON public.organization_ai_confirmation
      FOR EACH ROW EXECUTE FUNCTION public.guard_organization_ai_confirmation();
    """)
    patch_retention_catalog()
    patch_import_admission()


def downgrade():
    op.execute("""
    DROP TRIGGER organization_ai_confirmation_guard ON public.organization_ai_confirmation;
    DROP FUNCTION public.guard_organization_ai_confirmation();
    LOCK TABLE public.organization_ai_confirmation IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.organization_ai_confirmation) THEN
        RAISE EXCEPTION 'Cannot discard recorded AI provider confirmations';
      END IF;
    END $$;
    DROP TABLE public.organization_ai_confirmation;
    """)
    patch_retention_catalog(reverse=True)
    patch_import_admission(reverse=True)
