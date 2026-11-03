"""Permit empty WEG draft deletion while retaining financial dependencies."""
from alembic import op
from sqlalchemy import text

revision = '0151'
down_revision = '0150'
branch_labels = None
depends_on = None


def retention_catalog(reverse=False):
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('organization_retention_catalog()'::regprocedure)"))
    before = "'guard_reserve_payment_evidence'"
    after = before + ",'lock_weg_draft_delete','guard_weg_draft_delete'"
    old_predicate = "WHEN entity IN ('settlement_run','economic_plan') THEN $sql$t.status='finalized'$sql$"
    new_predicate = """WHEN entity='settlement_run' THEN $sql$t.status='finalized' OR
          (t.run_type IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT') AND public.weg_draft_delete_reason(t) IS NOT NULL)$sql$
        WHEN entity='economic_plan' THEN $sql$t.status='finalized'$sql$"""
    if reverse:
        before, after = after, before
        old_predicate, new_predicate = new_predicate, old_predicate
    assert definition.count(before) == definition.count(old_predicate) == 1
    op.execute(definition.replace(before, after).replace(old_predicate, new_predicate))


def upgrade():
    op.execute('LOCK TABLE settlement_run,payment,reserve_ledger_entry,accounting_scope_binding IN SHARE ROW EXCLUSIVE MODE NOWAIT')
    op.execute("""
    CREATE FUNCTION weg_draft_delete_reason(run public.settlement_run) RETURNS text
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF run.status<>'draft' OR run.superseded_by_id IS NOT NULL
        OR EXISTS(SELECT 1 FROM public.settlement_run r WHERE r.organization_id=run.organization_id AND r.superseded_by_id=run.id) THEN
        RETURN 'SETTLEMENT_DELETE_NOT_DRAFT';
      END IF;
      -- A period's booked cash is evidence even without an explicit run link.
      -- Reversed/cancelled bookings remain audited records too.
      IF EXISTS(SELECT 1 FROM public.payment p WHERE p.organization_id=run.organization_id
        AND (p.settlement_run_id=run.id OR (p.status='booked' AND p.property_id=run.property_id
          AND p.value_date BETWEEN run.period_start AND run.period_end))) THEN
        RETURN 'SETTLEMENT_DELETE_HAS_BOOKINGS';
      END IF;
      IF EXISTS(SELECT 1 FROM public.reserve_ledger_entry e
        WHERE e.organization_id=run.organization_id AND e.settlement_run_id=run.id) THEN
        RETURN 'SETTLEMENT_DELETE_HAS_LEDGER';
      END IF;
      -- Scope bindings are immutable evidence; their deferred FK must survive.
      IF EXISTS(SELECT 1 FROM public.accounting_scope_binding b
        WHERE b.organization_id=run.organization_id AND b.settlement_run_id=run.id) THEN
        RETURN 'SETTLEMENT_DELETE_SCOPE_DEPENDENCY';
      END IF;
      IF EXISTS(SELECT 1 FROM public.coverage_declaration d
          WHERE d.organization_id=run.organization_id AND d.settlement_run_id=run.id)
        OR EXISTS(SELECT 1 FROM public.financial_computation_receipt r
          WHERE r.organization_id=run.organization_id AND r.settlement_run_id=run.id)
        OR EXISTS(SELECT 1 FROM public.allocation_correction_impact i
          WHERE i.organization_id=run.organization_id AND i.settlement_run_id=run.id) THEN
        RETURN 'SETTLEMENT_DELETE_HAS_EVIDENCE';
      END IF;
      RETURN NULL;
    END $$;
    REVOKE ALL ON FUNCTION weg_draft_delete_reason(public.settlement_run) FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION weg_draft_delete_reason(org uuid,target uuid) RETURNS text
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE run public.settlement_run;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR public.scope_actor_role(org) IS NULL THEN RETURN 'NOT_FOUND'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      SELECT * INTO run FROM public.settlement_run r WHERE r.organization_id=org AND r.id=target
        AND r.run_type IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT');
      IF run.id IS NULL OR NOT public.financial_scope_access(org,'settlement_run',target,'read')
        OR NOT public.financial_parent_source_access(org,'settlement_run',target,run.property_id) THEN
        RETURN 'NOT_FOUND';
      END IF;
      RETURN public.weg_draft_delete_reason(run);
    END $$;
    REVOKE ALL ON FUNCTION weg_draft_delete_reason(uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION weg_draft_delete_reason(uuid,uuid) TO app_rw;

    CREATE FUNCTION lock_weg_draft_delete() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE org uuid; selected uuid:=NULLIF(current_setting('app.current_org_id',true),'')::uuid;
    BEGIN
      -- Statement-level acquisition precedes tuple/FK locks. Trusted maintenance
      -- without tenant context locks organizations in a stable order too.
      FOR org IN SELECT DISTINCT r.organization_id FROM public.settlement_run r
        WHERE r.run_type IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT')
          AND (selected IS NULL OR r.organization_id=selected) ORDER BY r.organization_id LOOP
        PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
        PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      END LOOP;
      RETURN NULL;
    END $$;
    REVOKE ALL ON FUNCTION lock_weg_draft_delete() FROM PUBLIC;
    CREATE TRIGGER a_lock_weg_draft_delete BEFORE DELETE ON settlement_run
      FOR EACH STATEMENT EXECUTE FUNCTION lock_weg_draft_delete();

    CREATE FUNCTION guard_weg_draft_delete() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE reason text;
    BEGIN
      IF OLD.run_type NOT IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT') THEN RETURN OLD; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||OLD.organization_id::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||OLD.organization_id::text,0));
      reason:=public.weg_draft_delete_reason(OLD);
      IF reason IS NOT NULL THEN
        RAISE EXCEPTION 'WEG draft deletion refused' USING ERRCODE='23514',CONSTRAINT=lower(reason);
      END IF;
      -- Computed lines are replaceable, but retained computation evidence above
      -- must never be removed merely to satisfy the line FK.
      DELETE FROM public.settlement_line WHERE organization_id=OLD.organization_id AND settlement_run_id=OLD.id;
      RETURN OLD;
    END $$;
    REVOKE ALL ON FUNCTION guard_weg_draft_delete() FROM PUBLIC;
    CREATE TRIGGER guard_weg_draft_delete BEFORE DELETE ON settlement_run
      FOR EACH ROW EXECUTE FUNCTION guard_weg_draft_delete();
    """)
    retention_catalog()


def downgrade():
    op.execute('LOCK TABLE settlement_run IN ACCESS EXCLUSIVE MODE NOWAIT')
    retention_catalog(reverse=True)
    op.execute("""DROP TRIGGER guard_weg_draft_delete ON settlement_run;
      DROP TRIGGER a_lock_weg_draft_delete ON settlement_run;
      DROP FUNCTION guard_weg_draft_delete(),lock_weg_draft_delete(),
        weg_draft_delete_reason(uuid,uuid),weg_draft_delete_reason(public.settlement_run);""")
