"""Durable session invalidation for password changes and deactivation (T57)."""
from alembic import op

revision = '0138'
down_revision = '0137'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    ALTER TABLE public."user" ADD COLUMN session_version integer NOT NULL DEFAULT 0
      CONSTRAINT ck_user_session_version_nonnegative CHECK (session_version >= 0);

    CREATE FUNCTION public.t57_lock_user_session_change() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE org uuid;
    BEGIN
      org := NULLIF(current_setting('app.current_org_id',true),'')::uuid;
      IF org IS NOT NULL AND org <> '00000000-0000-0000-0000-000000000000'::uuid THEN
        PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||org::text,0));
      ELSE
        -- BEFORE STATEMENT has no affected-row transition table. Unscoped
        -- maintenance takes all tenant ACLs in order, before any user row lock.
        FOR org IN SELECT id FROM public.organization ORDER BY id LOOP
          PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||org::text,0));
        END LOOP;
      END IF;
      RETURN NULL;
    END $$;
    CREATE TRIGGER t57_lock_user_session_change BEFORE UPDATE ON public."user"
      FOR EACH STATEMENT EXECUTE FUNCTION public.t57_lock_user_session_change();

    CREATE FUNCTION public.t57_advance_user_session_version() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF TG_OP='INSERT' THEN
        IF NEW.session_version IS DISTINCT FROM 0 THEN
          RAISE EXCEPTION 'Session generation is trigger-managed' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END IF;
      IF NEW.session_version IS DISTINCT FROM OLD.session_version THEN
        -- The DDL owner may prepare a rollback-only migration rehearsal.
        -- This is an invoker function: runtime app_rw cannot assume this role.
        IF current_user = 'app_migrator' AND NEW.session_version = 0
          AND NEW.password_hash IS NOT DISTINCT FROM OLD.password_hash
          AND NEW.is_active IS NOT DISTINCT FROM OLD.is_active THEN
          RETURN NEW;
        END IF;
        RAISE EXCEPTION 'Session generation is trigger-managed' USING ERRCODE='23514';
      END IF;
      IF NEW.password_hash IS DISTINCT FROM OLD.password_hash
        OR (OLD.is_active AND NOT NEW.is_active) THEN
        NEW.session_version := OLD.session_version + 1;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER t57_advance_user_session_version BEFORE INSERT OR UPDATE ON public."user"
      FOR EACH ROW EXECUTE FUNCTION public.t57_advance_user_session_version();
    REVOKE ALL ON FUNCTION public.t57_lock_user_session_change(),
      public.t57_advance_user_session_version() FROM PUBLIC,app_rw;

    CREATE FUNCTION public.app_auth_lookup_versioned(login_email text)
    RETURNS TABLE (user_id uuid, organization_id uuid, password_hash text,
      is_active boolean, role text, session_version integer)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT p.user_id,p.organization_id,p.password_hash,p.is_active,p.role,u.session_version
      FROM public.app_auth_lookup(login_email) p
      JOIN public."user" u ON u.id=p.user_id AND u.organization_id=p.organization_id
    $$;
    CREATE FUNCTION public.app_validate_session_versioned(
      session_user_id uuid, session_organization_id uuid, session_generation integer)
    RETURNS TABLE (role text)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT p.role
      FROM public.app_validate_session(session_user_id,session_organization_id) p
      JOIN public."user" u ON u.id=session_user_id AND u.organization_id=session_organization_id
      WHERE session_generation >= 0 AND u.session_version=session_generation
    $$;
    REVOKE ALL ON FUNCTION public.app_auth_lookup_versioned(text),
      public.app_validate_session_versioned(uuid,uuid,integer) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.app_auth_lookup_versioned(text),
      public.app_validate_session_versioned(uuid,uuid,integer) TO app_rw;
    ''')


def downgrade():
    op.execute('''
    LOCK TABLE public."user" IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public."user" WHERE session_version > 0) THEN
        RAISE EXCEPTION '0138 downgrade would resurrect invalidated sessions';
      END IF;
    END $$;
    DROP FUNCTION public.app_validate_session_versioned(uuid,uuid,integer);
    DROP FUNCTION public.app_auth_lookup_versioned(text);
    DROP TRIGGER t57_advance_user_session_version ON public."user";
    DROP TRIGGER t57_lock_user_session_change ON public."user";
    DROP FUNCTION public.t57_advance_user_session_version();
    DROP FUNCTION public.t57_lock_user_session_change();
    ALTER TABLE public."user" DROP COLUMN session_version;
    ''')
