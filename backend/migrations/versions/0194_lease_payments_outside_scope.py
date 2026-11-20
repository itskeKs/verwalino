"""The debt of a lease counts every booked payment of that lease, whichever scope's account received it (issue #803).

A rent statement selects one accounting scope. Row security (0034) then hides every payment whose account belongs to
another scope, so a lease that two rental scopes cover owed its full rent in each of them while only the payments of
that one scope reduced it. By the owner's decision the debt of a lease counts every booked payment of the lease for the
period; the selected scope still decides which leases and periods the caller may open.

``lease_payments_outside_scope(org, lease, listed)`` reaches the payments of the one lease the request names that the
caller did not read through row security (``listed`` holds the ids it did read, so nothing is counted twice). It
answers only where the statement itself is admitted: for the caller's organization, and where
``payment_computation_context_valid`` (0036) accepts the lease, which for a selected scope also asks for the caller's
live ``read`` grant, a scope whose financial inputs are all classified and a lease whose unit the scope covers. An
unknown id, a lease of another organization, a lease outside the selected scope and a caller without a grant get no
rows. What it returns is what the calculation reads and no more: type, direction, amount, assigned period, value date
and booking time, of booked payments that are neither reversed nor a reversal (the set a statement counts). It returns
no payment id, account, counterparty, text or scope, so the caller can sum and allocate the money but cannot name or
open the payment.

The function has the attributes of the other reach functions (definer rights, fixed search path, row security off,
EXECUTE for the runtime role only). No row is read or rewritten by the migration and no data can be lost:
``downgrade()`` drops the function.

Revision ID: 0194
Revises: 0193
Create Date: 2026-11-20
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0194"
down_revision: Union[str, None] = "0193"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE FUNCTION lease_payments_outside_scope(org uuid,lease uuid,listed uuid[])
      RETURNS TABLE(payment_type varchar,direction varchar,amount numeric,period_year smallint,period_month smallint,
                    value_date date,booked_at timestamptz)
      LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR NOT public.payment_computation_context_valid(org,lease,NULL) THEN
        RETURN;
      END IF;
      RETURN QUERY SELECT p.payment_type,p.direction,p.amount,p.period_year,p.period_month,p.value_date,p.booked_at
        FROM public.payment p
        WHERE p.organization_id=org AND p.lease_agreement_id=lease AND p.status='booked'
          AND p.reversed_by_id IS NULL AND p.reverses_id IS NULL
          AND p.id<>ALL(COALESCE(listed,ARRAY[]::uuid[]))
        ORDER BY p.value_date,p.created_at,p.id;
    END $$;
    REVOKE ALL ON FUNCTION lease_payments_outside_scope(uuid,uuid,uuid[]) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION lease_payments_outside_scope(uuid,uuid,uuid[]) TO app_rw;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION lease_payments_outside_scope(uuid,uuid,uuid[])")
