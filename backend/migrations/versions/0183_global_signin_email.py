"""Globally unique sign-in address; one address belongs to one organization.

Revision ID: 0183
Revises: 0182
Create Date: 2026-11-11
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0183"
down_revision: Union[str, None] = "0182"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Hosted mode: an address belongs to exactly one organization (owner
    # decision on itskeKs/verwalino#134). Cross-organization duplicates must
    # be resolved by hand; the refusal names a bounded sample of canonical
    # addresses and the operator re-runs the upgrade after resolving them.
    op.execute(
        """
        DO $$
        DECLARE conflicts text;
        BEGIN
            SELECT string_agg(canonical, ', ' ORDER BY canonical)
            INTO conflicts
            FROM (
                SELECT pg_catalog.lower(pg_catalog.btrim(email)) AS canonical
                FROM public."user"
                GROUP BY 1
                HAVING count(*) > 1
                ORDER BY 1
                LIMIT 5
            ) duplicates;
            IF conflicts IS NOT NULL THEN
                RAISE EXCEPTION 'Duplicate sign-in addresses across organizations: %. '
                    'Resolve by hand: change or deactivate one address per duplicate, '
                    'then re-run the upgrade.', conflicts;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_user_signin_email
        ON public."user" (pg_catalog.lower(pg_catalog.btrim(email)))
        """
    )
    # The global canonical index implies per-organization canonical
    # uniqueness; keep exactly one enforcement instead of two.
    op.execute("DROP INDEX IF EXISTS public.uq_user_org_canonical_email")
    # Runtime import check: an archive placeholder may not occupy a sign-in
    # address that belongs to a user of any organization.
    op.execute(
        """
        CREATE FUNCTION public.signin_email_taken(email text) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = off
        AS $$
            SELECT EXISTS(
                SELECT 1 FROM public."user" AS u
                WHERE pg_catalog.lower(pg_catalog.btrim(u.email)) =
                      pg_catalog.lower(pg_catalog.btrim(email))
            )
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.signin_email_taken(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.signin_email_taken(text) TO app_rw")


def downgrade() -> None:
    op.execute("DROP FUNCTION public.signin_email_taken(text)")
    op.execute("DROP INDEX IF EXISTS public.uq_user_signin_email")
    # Restore the 0027 per-organization canonical index verbatim.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_user_org_canonical_email
        ON public."user" (organization_id, pg_catalog.lower(pg_catalog.btrim(email)))
        """
    )
