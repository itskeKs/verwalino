"""Keep financial row visibility from weakening privacy retention decisions."""
from alembic import op

revision = '0037'
down_revision = '0036'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE FUNCTION privacy_financial_context_complete(org uuid, person uuid, capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR capability NOT IN ('read','export') OR NULLIF(current_setting('app.current_scope_id',true),'') IS NOT NULL THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      RETURN NOT EXISTS(
        SELECT 1 FROM public.cost_entry c JOIN public.vendor v ON v.organization_id=c.organization_id AND v.id=c.vendor_id
          WHERE c.organization_id=org AND v.person_id=person AND c.status='confirmed'
            AND NOT public.financial_scope_access(org,'cost_entry',c.id,capability)
        UNION ALL
        SELECT 1 FROM public.settlement_run r WHERE r.organization_id=org AND r.status='finalized'
          AND (EXISTS(SELECT 1 FROM public.tenant_profile t WHERE t.organization_id=org AND t.id=r.tenant_profile_id AND t.person_id=person)
            OR EXISTS(SELECT 1 FROM public.settlement_line ln JOIN public.owner_profile o ON o.organization_id=ln.organization_id AND o.id=ln.addressee_owner_profile_id
              WHERE ln.organization_id=org AND ln.settlement_run_id=r.id AND o.person_id=person))
          AND NOT public.financial_scope_access(org,'settlement_run',r.id,capability)
        UNION ALL
        SELECT 1 FROM public.economic_plan p WHERE p.organization_id=org AND p.status='finalized'
          AND EXISTS(SELECT 1 FROM public.economic_plan_line ln JOIN public.owner_profile o ON o.organization_id=ln.organization_id AND o.id=ln.owner_profile_id
            WHERE ln.organization_id=org AND ln.economic_plan_id=p.id AND o.person_id=person)
          AND NOT public.financial_scope_access(org,'economic_plan',p.id,capability));
    END $$;
    REVOKE ALL ON FUNCTION privacy_financial_context_complete(uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION privacy_financial_context_complete(uuid,uuid,text) TO app_rw;
    ''')


def downgrade():
    op.execute('DROP FUNCTION privacy_financial_context_complete(uuid,uuid,text)')
