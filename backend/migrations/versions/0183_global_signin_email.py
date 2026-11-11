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
    # decision on itskeKs/verwalino#134). Cross-organization duplicates are
    # resolved by hand before the upgrade (procedure: DEPLOY.md, "Migration
    # 0183: duplicate sign-in addresses"); this revision never rewrites rows.
    # The refusal names a bounded sample of canonical addresses.
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
                    'Resolve them by hand as the migrator role before the upgrade, see DEPLOY.md '
                    '(Migration 0183: duplicate sign-in addresses): an inactive imported placeholder '
                    'without credentials gets the address <user id>-<organization id>@imported.archive.invalid, '
                    'two real accounts need a decision of the owner which organization keeps the address. '
                    'Deactivating an account does not help, the index covers inactive rows. '
                    'Then re-run the upgrade.', conflicts;
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
    # address that belongs to a user of any organization. The lookup is the
    # one signup already uses (0144); only the application role needs to
    # execute it.
    op.execute("GRANT EXECUTE ON FUNCTION public.signup_email_registered(text) TO app_rw")


def downgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION public.signup_email_registered(text) FROM app_rw")
    op.execute("DROP INDEX IF EXISTS public.uq_user_signin_email")
    # Restore the 0027 per-organization canonical index verbatim.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_user_org_canonical_email
        ON public."user" (organization_id, pg_catalog.lower(pg_catalog.btrim(email)))
        """
    )
