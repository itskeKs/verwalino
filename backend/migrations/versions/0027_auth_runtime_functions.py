"""Security-definer functions for runtime authentication.

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-27
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
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
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.app_validate_session(session_user_id uuid, session_organization_id uuid)
        RETURNS TABLE (role text)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT m.role
            FROM public."user" AS u
            JOIN public.membership AS m
              ON m.user_id = u.id
             AND m.organization_id = u.organization_id
            WHERE u.id = session_user_id
              AND u.organization_id = session_organization_id
              AND u.is_active
        $$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.app_auth_lookup(text) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.app_validate_session(uuid, uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.app_auth_lookup(text) TO app_rw")
    op.execute("GRANT EXECUTE ON FUNCTION public.app_validate_session(uuid, uuid) TO app_rw")
    # A prior production rollout installed the 0027 runtime objects before its
    # Alembic revision was recorded.  Reconcile an exact pre-existing index,
    # but do not treat an arbitrary object with this name as equivalent: a
    # different expression would silently weaken canonical-email uniqueness.
    op.execute(
        """
        DO $$
        DECLARE
            existing_index oid;
            index_matches boolean;
        BEGIN
            existing_index := to_regclass('public.uq_user_org_canonical_email')::oid;
            IF existing_index IS NULL THEN
                CREATE UNIQUE INDEX uq_user_org_canonical_email
                ON public."user"
                (organization_id, pg_catalog.lower(pg_catalog.btrim(email)));
                RETURN;
            END IF;

            SELECT i.indisunique
                   AND i.indrelid = 'public."user"'::regclass
                   AND i.indpred IS NULL
                   AND i.indisvalid
                   AND i.indisready
                   AND i.indislive
                   AND index_access_method.amname = 'btree'
                   AND i.indnkeyatts = 2
                   AND i.indnatts = 2
                   AND pg_get_indexdef(i.indexrelid, 1, true) = 'organization_id'
                   AND pg_get_indexdef(i.indexrelid, 2, true) = 'lower(btrim(email::text))'
            INTO index_matches
            FROM pg_index AS i
            JOIN pg_class AS index_relation ON index_relation.oid = i.indexrelid
            JOIN pg_am AS index_access_method ON index_access_method.oid = index_relation.relam
            WHERE i.indexrelid = existing_index;

            IF COALESCE(index_matches, false) IS NOT TRUE THEN
                RAISE EXCEPTION
                    '0027 found conflicting canonical-email index uq_user_org_canonical_email';
            END IF;
        END;
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.uq_user_org_canonical_email")
    op.execute("DROP FUNCTION public.app_validate_session(uuid, uuid)")
    op.execute("DROP FUNCTION public.app_auth_lookup(text)")
