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
      user_id uuid NOT NULL,
      secret_encrypted text NOT NULL,
      confirmed boolean NOT NULL DEFAULT false,
      confirmed_at timestamptz,
      last_totp_step integer,
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      CONSTRAINT uq_user_second_factor_org_user UNIQUE (organization_id,user_id),
      FOREIGN KEY (organization_id,user_id) REFERENCES public."user"(organization_id,id) ON DELETE CASCADE
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
      user_id uuid NOT NULL,
      position integer NOT NULL CHECK (position >= 0),
      code_hash text NOT NULL CHECK (code_hash ~ '^[0-9a-f]{64}$'),
      used_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      CONSTRAINT uq_user_recovery_code_org_user_position UNIQUE (organization_id,user_id,position),
      FOREIGN KEY (organization_id,user_id) REFERENCES public."user"(organization_id,id) ON DELETE CASCADE
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
    REVOKE ALL ON public.user_recovery_code FROM PUBLIC,app_worker,app_review;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.user_recovery_code TO app_rw;
    CREATE TRIGGER a_organization_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON public.user_recovery_code
      FOR EACH ROW EXECUTE FUNCTION public.organization_lifecycle_guard();

    -- The audited admin reset. RLS deliberately gives an OWNER_ADMIN no read
    -- access and no DELETE policy on another user's rows, so this function
    -- re-verifies actor, active role and target in one transaction and
    -- removes the factor and the recovery codes together.
    CREATE FUNCTION public.reset_second_factor(org uuid, target uuid)
    RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE
      actor uuid;
      removed integer;
    BEGIN
      actor := NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      IF org IS NULL OR target IS NULL OR actor IS NULL
        OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR NOT EXISTS(SELECT 1 FROM public.membership m JOIN public."user" u
          ON u.id=m.user_id AND u.organization_id=m.organization_id
          WHERE m.organization_id=org AND m.user_id=actor
            AND m.role='OWNER_ADMIN' AND u.is_active)
        OR NOT EXISTS(SELECT 1 FROM public."user" u
          WHERE u.organization_id=org AND u.id=target AND u.is_active) THEN
        RAISE EXCEPTION 'Second-factor reset refused' USING ERRCODE='42501';
      END IF;
      DELETE FROM public.user_second_factor f
        WHERE f.organization_id=org AND f.user_id=target;
      GET DIAGNOSTICS removed = ROW_COUNT;
      DELETE FROM public.user_recovery_code c
        WHERE c.organization_id=org AND c.user_id=target;
      RETURN removed;
    END $$;
    REVOKE ALL ON FUNCTION public.reset_second_factor(uuid,uuid) FROM PUBLIC,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.reset_second_factor(uuid,uuid) TO app_rw;
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
    DROP FUNCTION public.reset_second_factor(uuid,uuid);
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
