"""Reject overlapping active settlement runs of one lease across accounting scopes.

RLS hides the runs of other accounting scopes from the acting user, so the
application check can only compare periods inside the visible scope. This
trigger compares a new or moved lease run with every active (non-superseded)
run of the same lease and type that belongs to a different accounting scope (a
legacy run without a scope counts as its own context), with row security off
and without returning row data. Rows of another organization are left to the
organization RLS policy, which rejects them without revealing anything about
that organization. Identical bounds stay valid (repeats and
corrections). Runs of one scope stay with the application checks, as before.

Also adds the narrow organization-bound reader of a property's settlement start
month for creation paths whose user cannot read the property itself.
"""
from alembic import op

revision = '0182'
down_revision = '0181'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('LOCK TABLE settlement_run IN SHARE ROW EXCLUSIVE MODE NOWAIT')
    op.execute("""
    CREATE FUNCTION guard_settlement_run_period_overlap() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE sphere uuid;
    BEGIN
      -- A row of another organization is left to the organization RLS policy,
      -- which rejects it without revealing anything about that organization.
      IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN NEW; END IF;
      IF NEW.lease_agreement_id IS NULL OR NEW.superseded_by_id IS NOT NULL THEN RETURN NEW; END IF;
      -- Concurrent creators of one lease and type serialize here; the check below then sees
      -- the committed run of the other scope.
      PERFORM pg_advisory_xact_lock(hashtextextended('settlement-run-period:'||NEW.organization_id::text
        ||':'||NEW.lease_agreement_id::text||':'||NEW.run_type,0));
      -- bind_new_financial_resource runs first, so the new run's scope is known.
      SELECT b.scope_id INTO sphere FROM public.accounting_scope_binding b
        WHERE b.organization_id=NEW.organization_id AND b.settlement_run_id=NEW.id;
      IF EXISTS(SELECT 1 FROM public.settlement_run r
        LEFT JOIN public.accounting_scope_binding b ON b.organization_id=r.organization_id AND b.settlement_run_id=r.id
        WHERE r.organization_id=NEW.organization_id AND r.lease_agreement_id=NEW.lease_agreement_id
          AND r.run_type=NEW.run_type AND r.id<>NEW.id AND r.superseded_by_id IS NULL
          AND r.period_start<=NEW.period_end AND r.period_end>=NEW.period_start
          AND (r.period_start<>NEW.period_start OR r.period_end<>NEW.period_end)
          AND b.scope_id IS DISTINCT FROM sphere) THEN
        RAISE EXCEPTION 'Settlement period overlaps an active run of this lease'
          USING ERRCODE='23P01',CONSTRAINT='settlement_run_active_period';
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION guard_settlement_run_period_overlap() FROM PUBLIC;
    CREATE TRIGGER guard_settlement_run_period_overlap
      BEFORE INSERT OR UPDATE OF organization_id,lease_agreement_id,run_type,period_start,period_end
      ON settlement_run FOR EACH ROW EXECUTE FUNCTION guard_settlement_run_period_overlap();

    CREATE FUNCTION financial_property_settlement_start_month(org uuid,property uuid) RETURNS integer
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR public.scope_actor_role(org) IS NULL THEN RETURN NULL; END IF;
      RETURN (SELECT p.fiscal_year_start_month::integer FROM public.property p
        WHERE p.organization_id=org AND p.id=property);
    END $$;
    REVOKE ALL ON FUNCTION financial_property_settlement_start_month(uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION financial_property_settlement_start_month(uuid,uuid) TO app_rw;
    """)


def downgrade():
    op.execute('LOCK TABLE settlement_run IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""
    DROP TRIGGER guard_settlement_run_period_overlap ON settlement_run;
    DROP FUNCTION guard_settlement_run_period_overlap();
    DROP FUNCTION financial_property_settlement_start_month(uuid,uuid);
    """)
