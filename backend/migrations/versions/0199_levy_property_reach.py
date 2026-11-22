"""A special levy of another property no longer refuses the unscoped read of a property without a scope (issue #816).

0127 wraps ``report_scope_source_access(org, sphere, capability)`` so that, as soon as the organization
holds any special levy, an unscoped read (``sphere IS NULL``) is refused for every property of the
organization. 0193 added the overload that takes the one lease or the one owner such a read concerns
and answers as before the levy when none of the properties it touches has classified finance. The
annual overview of a property (``GET /api/reports/property-year`` and the readers that share it) and
the matching of the bank transactions of an unscoped bank account concern one property, not a lease or
an owner, so a levy still refused them although the property has no scope the user could choose.

This revision replaces the five-argument function by one with a sixth argument, ``property``, the one
property an unscoped read concerns. For such a read it answers as the function did before 0127
(``report_sources_before_special_levy``) when the property has no classified finance
(``property_has_classified_finance``, which counts the property of a levy) and no accounting scope of
its own, because only for a property without a scope is there nothing to ask the user to choose. In
every other case it is what the five-argument function was: a lease or an owner as 0193 resolves it,
and otherwise the three-argument function. That keeps the organization-wide overviews, exports and
SEPA, an unscoped read of the levy's own property, of a property that has classified finance or a scope,
a read that names two subjects, and every read of a scope as they are. A property id that names no
property of the caller's organization (unknown, or of another organization) answers as before the levy,
like an unknown lease or owner does in 0193.

The sixth argument has a default, so a caller that names no property (``DunningScopeService``) is
unchanged. The three-argument function, its callers and the policies that call it stay as they are.
The new function has the same attributes as the one it replaces (definer rights, fixed search path,
row security off, EXECUTE for the runtime role only). No row is read or rewritten and no data can be
lost: ``downgrade()`` drops the six-argument function and creates the five-argument one of 0193 again.

Revision ID: 0199
Revises: 0198
Create Date: 2026-11-22
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0199"
down_revision: Union[str, None] = "0198"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    DROP FUNCTION report_scope_source_access(uuid,uuid,text,uuid,uuid);
    CREATE FUNCTION report_scope_source_access(org uuid,sphere uuid,capability text,lease uuid,owner uuid,property uuid DEFAULT NULL) RETURNS boolean
      LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF sphere IS NULL AND org IS NOT DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid AND (
        (property IS NOT NULL AND lease IS NULL AND owner IS NULL
          AND NOT public.property_has_classified_finance(org,property)
          AND NOT EXISTS(SELECT 1 FROM public.accounting_scope s WHERE s.organization_id=org AND s.property_id=property))
        OR (property IS NULL AND (lease IS NULL)<>(owner IS NULL)
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
          ) concerned WHERE public.property_has_classified_finance(org,concerned.property_id)))) THEN
        RETURN public.report_sources_before_special_levy(org,NULL,capability);
      END IF;
      RETURN public.report_scope_source_access(org,sphere,capability);
    END $$;
    REVOKE ALL ON FUNCTION report_scope_source_access(uuid,uuid,text,uuid,uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION report_scope_source_access(uuid,uuid,text,uuid,uuid,uuid) TO app_rw;
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION report_scope_source_access(uuid,uuid,text,uuid,uuid,uuid);
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
