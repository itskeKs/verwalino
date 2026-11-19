"""A special levy refuses an unscoped read only where classified data refuses it (issue #739).

0127 wraps ``report_scope_source_access(org, sphere, capability)`` so that, as soon as the
organization holds any special levy, an unscoped read (``sphere IS NULL``) is refused for every
property of the organization, also for a property that has no accounting scope at all: the
function does not know which lease or owner the read concerns. The classified check of 0036,
``payment_computation_context_valid(org, lease, owner)``, is narrower: it refuses an unscoped
read of one lease or one owner only when that lease or owner sits on a property with classified
finance. A manager of an organization with a levy could therefore not open the rent account of a
tenant of a property without a scope, nor create a notice for that tenant.

This revision adds an overload that takes the one lease or the one owner an unscoped read
concerns. For such a read it answers as the function did before 0127
(``report_sources_before_special_levy``) when none of the properties the lease or owner touches
has classified finance (``property_has_classified_finance``, which counts the property of a
levy). In every other case it is the three-argument function: an unscoped read that names no
lease and no owner (the organization-wide overviews, exports, SEPA), one that names both, one
on a property with classified finance (the levy's own property included), and every read of a
scope. The properties are resolved inside the function from the row ids, the way 0036 does it
(lease: its unit; owner: the units of its ownership entries that were not corrected (0190), its
payments, the lines of its plans, its levy allocations, which also find a levy whose share a corrected
entry still carries), and only for rows of the caller's organization, so the caller hands over only
the id the request already names and an id of another organization or an unknown one concerns no
property and answers as before the levy.

The three-argument function, its callers and the policies that call it stay as they are. The new
function has the same attributes (definer rights, fixed search path, row security off, EXECUTE for
the runtime role only). No row is read or rewritten and no data can be lost: ``downgrade()`` drops
the overload.

Revision ID: 0193
Revises: 0192
Create Date: 2026-11-19
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0193"
down_revision: Union[str, None] = "0192"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE FUNCTION report_scope_source_access(org uuid,sphere uuid,capability text,lease uuid,owner uuid) RETURNS boolean
      LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF sphere IS NULL AND (lease IS NULL)<>(owner IS NULL)
        AND org IS NOT DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND NOT EXISTS(SELECT 1 FROM (
          SELECT u.property_id FROM public.lease_agreement l
            JOIN public.unit u ON u.organization_id=l.organization_id AND u.id=l.unit_id
            WHERE l.organization_id=org AND l.id=lease
          UNION SELECT u.property_id FROM public.ownership_history h
            JOIN public.unit u ON u.organization_id=h.organization_id AND u.id=h.unit_id
            WHERE h.organization_id=org AND h.owner_profile_id=owner AND h.corrected_at IS NULL
          UNION SELECT p.property_id FROM public.payment p
            WHERE p.organization_id=org AND p.owner_profile_id=owner
          UNION SELECT e.property_id FROM public.economic_plan_line ln
            JOIN public.economic_plan e ON e.organization_id=ln.organization_id AND e.id=ln.economic_plan_id
            WHERE ln.organization_id=org AND ln.owner_profile_id=owner
          UNION SELECT v.property_id FROM public.special_levy_allocation a
            JOIN public.special_levy v ON v.organization_id=a.organization_id AND v.id=a.special_levy_id
            WHERE a.organization_id=org AND a.owner_profile_id=owner
        ) concerned WHERE public.property_has_classified_finance(org,concerned.property_id)) THEN
        RETURN public.report_sources_before_special_levy(org,NULL,capability);
      END IF;
      RETURN public.report_scope_source_access(org,sphere,capability);
    END $$;
    REVOKE ALL ON FUNCTION report_scope_source_access(uuid,uuid,text,uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION report_scope_source_access(uuid,uuid,text,uuid,uuid) TO app_rw;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION report_scope_source_access(uuid,uuid,text,uuid,uuid)")
