"""The level of a tenant notice counts the demands of a lease across its accounting scopes (issue #818).

Since #803 the open months of a lease do not depend on the selected rental scope, so two scopes that cover one lease show
the same arrears. The level of a notice still counted only the notices of the selected scope, and row security (0039)
hides the notices of every other scope from that session, so each scope could send a first-level reminder (no fee) for
months the other scope had already demanded. By the owner's decision the dunning level counts the demands of a lease
across all its scopes; the amounts stay with the scope.

``lease_item_demand_count(org, lease, item_vars)`` is that count. ``item_vars`` is an array of the items the new notice
would demand, each as the variables of the matching rule (``payment_type``, ``period_year``, ``period_month``,
``due_date``, ``joint_advance``); the function returns how often the most often demanded of them was already demanded in
an issued, not cancelled notice that belongs to the lease through its scope context, whichever scope that is. It matches
an item as ``DunningNoticeRepository`` does for notices without a scope (``_DEMANDED_ITEM``) and counts every notice once.
It returns one number: no notice id, scope, date or text of the notices it read, so a caller without a grant for those
scopes can neither name nor open them.

It answers only where the statement itself is admitted: for the caller's organization and where
``payment_computation_context_valid`` (0036) accepts the lease, which for a selected scope also asks for the caller's
live ``read`` grant, a scope whose financial inputs are all classified and a lease whose unit the scope covers. An unknown
id, a lease of another organization, a lease outside the selected scope and a caller without a grant get 0.

The function has the attributes of the other reach functions (definer rights, fixed search path, row security off,
EXECUTE for the runtime role only). No row is read or rewritten by the migration and no data can be lost:
``downgrade()`` drops the function.

Revision ID: 0203
Revises: 0202
Create Date: 2026-11-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0203"
down_revision: Union[str, None] = "0202"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE FUNCTION lease_item_demand_count(org uuid,lease uuid,item_vars jsonb) RETURNS integer
      LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE highest integer;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR jsonb_typeof(item_vars) IS DISTINCT FROM 'array'
        OR NOT public.payment_computation_context_valid(org,lease,NULL) THEN
        RETURN 0;
      END IF;
      WITH notices AS MATERIALIZED (
        SELECT n.included_items FROM public.dunning_scope_context c
          JOIN public.dunning_notice n ON n.organization_id=c.organization_id AND n.id=c.dunning_notice_id
          WHERE c.organization_id=org AND c.lease_agreement_id=lease AND n.status='issued' AND n.cancelled_at IS NULL)
      SELECT COALESCE(max(per_item.demanded),0)::integer INTO highest FROM (
        SELECT count(*) FILTER (WHERE jsonb_path_exists(notices.included_items,
            '$[*] ? (@.period_year == $period_year && @.period_month == $period_month && (@.payment_type == $payment_type || (@.payment_type == $joint_advance && @.combined_advance == true)) && ($due_date == null || !exists(@.due_date) || @.due_date == $due_date))'::jsonpath,
            item.vars)) AS demanded
        FROM jsonb_array_elements(item_vars) WITH ORDINALITY AS item(vars,ordinal) CROSS JOIN notices
        GROUP BY item.ordinal) per_item;
      RETURN highest;
    END $$;
    REVOKE ALL ON FUNCTION lease_item_demand_count(uuid,uuid,jsonb) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION lease_item_demand_count(uuid,uuid,jsonb) TO app_rw;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION lease_item_demand_count(uuid,uuid,jsonb)")
