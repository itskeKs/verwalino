"""TOTP second factor: factor state and one-time recovery codes (issue #208).

The authenticator secret is stored encrypted (Fernet via the app), recovery
codes only as keyed hashes. Both tables are tenant data with row-level
security; the retention catalog classifies them as 'delete' (erased with
the organization, like password_reset_request).
"""

from alembic import op
from sqlalchemy import text

revision = "0188"
down_revision = "0187"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE public.user_second_factor (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      organization_id uuid NOT NULL REFERENCES public.organization(id) ON DELETE RESTRICT,
      user_id uuid NOT NULL REFERENCES public."user"(id) ON DELETE CASCADE,
      secret_encrypted text NOT NULL,
      confirmed boolean NOT NULL DEFAULT false,
      confirmed_at timestamptz,
      last_totp_step integer,
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      CONSTRAINT uq_user_second_factor_org_user UNIQUE (organization_id,user_id)
    );
    CREATE INDEX ix_user_second_factor_org ON public.user_second_factor(organization_id);
    ALTER TABLE public.user_second_factor ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.user_second_factor FORCE ROW LEVEL SECURITY;
    -- A factor belongs to its user; an OWNER_ADMIN manages (resets) it.
    CREATE POLICY user_second_factor_self_read ON public.user_second_factor FOR SELECT TO app_rw
      USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND user_id = NULLIF(current_setting('app.current_user_id',true),'')::uuid);
    CREATE POLICY user_second_factor_self_insert ON public.user_second_factor FOR INSERT TO app_rw
      WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND user_id = NULLIF(current_setting('app.current_user_id',true),'')::uuid);
    CREATE POLICY user_second_factor_self_update ON public.user_second_factor FOR UPDATE TO app_rw
      USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND user_id = NULLIF(current_setting('app.current_user_id',true),'')::uuid)
      WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND user_id = NULLIF(current_setting('app.current_user_id',true),'')::uuid);
    CREATE POLICY user_second_factor_self_delete ON public.user_second_factor FOR DELETE TO app_rw
      USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND user_id = NULLIF(current_setting('app.current_user_id',true),'')::uuid);
    CREATE POLICY user_second_factor_admin ON public.user_second_factor FOR ALL TO app_rw
      USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND public.scope_actor_role(organization_id) = 'OWNER_ADMIN')
      WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND public.scope_actor_role(organization_id) = 'OWNER_ADMIN');
    REVOKE ALL ON public.user_second_factor FROM PUBLIC,app_worker,app_review;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.user_second_factor TO app_rw;
    CREATE TRIGGER a_organization_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON public.user_second_factor
      FOR EACH ROW EXECUTE FUNCTION public.organization_lifecycle_guard();
    """)

    # The hosted second-factor reset ends the target's sessions: the trigger
    # that owns session_version advances it when the app names the reset
    # account in this transaction-local GUC (issue #208). The invoker-scoped
    # check cannot be spoofed into a wider bump: it names exactly one user id
    # and only this app code path sets it.
    definition = op.get_bind().scalar(text(
        "SELECT pg_get_functiondef('public.t57_advance_user_session_version()'::regprocedure)"
    ))
    before = """      IF NEW.password_hash IS DISTINCT FROM OLD.password_hash
        OR (OLD.is_active AND NOT NEW.is_active) THEN
        NEW.session_version := OLD.session_version + 1;
      END IF;
      RETURN NEW;"""
    after = """      IF NEW.password_hash IS DISTINCT FROM OLD.password_hash
        OR (OLD.is_active AND NOT NEW.is_active) THEN
        NEW.session_version := OLD.session_version + 1;
      END IF;
      IF NULLIF(current_setting('app.second_factor_reset_user_id',true),'')::uuid IS NOT NULL
        AND NULLIF(current_setting('app.second_factor_reset_user_id',true),'')::uuid = NEW.id THEN
        NEW.session_version := OLD.session_version + 1;
      END IF;
      RETURN NEW;"""
    assert definition.count(before) == 1
    op.execute(definition.replace(before, after))

    op.execute("""
    CREATE TABLE public.user_recovery_code (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      organization_id uuid NOT NULL REFERENCES public.organization(id) ON DELETE RESTRICT,
      user_id uuid NOT NULL REFERENCES public."user"(id) ON DELETE CASCADE,
      position integer NOT NULL CHECK (position >= 0),
      code_hash text NOT NULL CHECK (code_hash ~ '^[0-9a-f]{64}$'),
      used_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      CONSTRAINT uq_user_recovery_code_org_user_position UNIQUE (organization_id,user_id,position)
    );
    CREATE INDEX ix_user_recovery_code_org ON public.user_recovery_code(organization_id);
    ALTER TABLE public.user_recovery_code ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.user_recovery_code FORCE ROW LEVEL SECURITY;
    -- Recovery codes are read and spent by their user only; an OWNER_ADMIN
    -- resets (deletes) the whole set.
    CREATE POLICY user_recovery_code_self_read ON public.user_recovery_code FOR SELECT TO app_rw
      USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND user_id = NULLIF(current_setting('app.current_user_id',true),'')::uuid);
    CREATE POLICY user_recovery_code_self_insert ON public.user_recovery_code FOR INSERT TO app_rw
      WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND user_id = NULLIF(current_setting('app.current_user_id',true),'')::uuid);
    CREATE POLICY user_recovery_code_self_update ON public.user_recovery_code FOR UPDATE TO app_rw
      USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND user_id = NULLIF(current_setting('app.current_user_id',true),'')::uuid)
      WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND user_id = NULLIF(current_setting('app.current_user_id',true),'')::uuid);
    CREATE POLICY user_recovery_code_self_delete ON public.user_recovery_code FOR DELETE TO app_rw
      USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND user_id = NULLIF(current_setting('app.current_user_id',true),'')::uuid);
    CREATE POLICY user_recovery_code_admin_reset ON public.user_recovery_code FOR DELETE TO app_rw
      USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND public.scope_actor_role(organization_id) = 'OWNER_ADMIN');
    REVOKE ALL ON public.user_recovery_code FROM PUBLIC,app_worker,app_review;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.user_recovery_code TO app_rw;
    CREATE TRIGGER a_organization_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON public.user_recovery_code
      FOR EACH ROW EXECUTE FUNCTION public.organization_lifecycle_guard();
    """)


def downgrade():
    op.execute("""
    LOCK TABLE public.user_second_factor IN ACCESS EXCLUSIVE MODE;
    LOCK TABLE public.user_recovery_code IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.user_second_factor) OR EXISTS(SELECT 1 FROM public.user_recovery_code) THEN
        RAISE EXCEPTION 'Cannot discard retained second-factor state';
      END IF;
    END $$;
    DROP TABLE public.user_recovery_code;
    DROP TABLE public.user_second_factor;
    """)
    definition = op.get_bind().scalar(text(
        "SELECT pg_get_functiondef('public.t57_advance_user_session_version()'::regprocedure)"
    ))
    before = """      IF NEW.password_hash IS DISTINCT FROM OLD.password_hash
        OR (OLD.is_active AND NOT NEW.is_active) THEN
        NEW.session_version := OLD.session_version + 1;
      END IF;
      IF NULLIF(current_setting('app.second_factor_reset_user_id',true),'')::uuid IS NOT NULL
        AND NULLIF(current_setting('app.second_factor_reset_user_id',true),'')::uuid = NEW.id THEN
        NEW.session_version := OLD.session_version + 1;
      END IF;
      RETURN NEW;"""
    after = """      IF NEW.password_hash IS DISTINCT FROM OLD.password_hash
        OR (OLD.is_active AND NOT NEW.is_active) THEN
        NEW.session_version := OLD.session_version + 1;
      END IF;
      RETURN NEW;"""
    assert definition.count(before) == 1
    op.execute(definition.replace(before, after))
