"""Reject partial payment aggregates when a financial context is missing."""
from alembic import op

revision = '0036'
down_revision = '0035'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE FUNCTION payment_computation_context_valid(org uuid, lease uuid, owner uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE selected uuid;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR (lease IS NULL)=(owner IS NULL) THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      selected:=NULLIF(current_setting('app.current_scope_id',true),'')::uuid;
      IF selected IS NOT NULL THEN
        RETURN public.scope_financial_inputs_classified(org,selected)
          AND (lease IS NULL OR EXISTS(SELECT 1 FROM public.lease_agreement l
            WHERE l.organization_id=org AND l.id=lease AND public.unit_in_financial_context(org,l.unit_id)))
          AND (owner IS NULL
            OR EXISTS(SELECT 1 FROM public.ownership_history h JOIN public.accounting_scope s ON s.organization_id=h.organization_id AND s.id=selected
              WHERE h.organization_id=org AND h.owner_profile_id=owner AND public.unit_in_financial_context(org,h.unit_id)
                AND (h.valid_to IS NULL OR h.valid_to>s.valid_from) AND (s.valid_to IS NULL OR h.valid_from<=s.valid_to))
            OR EXISTS(SELECT 1 FROM public.payment p JOIN public.accounting_scope_binding b ON b.organization_id=p.organization_id AND b.payment_id=p.id
              WHERE p.organization_id=org AND p.owner_profile_id=owner AND b.scope_id=selected)
            OR EXISTS(SELECT 1 FROM public.economic_plan_line ln JOIN public.accounting_scope_binding b ON b.organization_id=ln.organization_id AND b.economic_plan_id=ln.economic_plan_id
              WHERE ln.organization_id=org AND ln.owner_profile_id=owner AND b.scope_id=selected));
      END IF;
      -- Definer visibility is essential: an invisible payment must not become a
      -- zero in a statement, even for an administrator without financial grants.
      RETURN NOT EXISTS(SELECT 1 FROM public.accounting_scope_binding b WHERE b.organization_id=org AND (
        EXISTS(SELECT 1 FROM public.lease_agreement l JOIN public.unit u ON u.organization_id=l.organization_id AND u.id=l.unit_id
          WHERE l.organization_id=org AND l.id=lease AND u.property_id=b.property_id)
        OR EXISTS(SELECT 1 FROM public.ownership_history h JOIN public.unit u ON u.organization_id=h.organization_id AND u.id=h.unit_id
          WHERE h.organization_id=org AND h.owner_profile_id=owner AND u.property_id=b.property_id)
        OR EXISTS(SELECT 1 FROM public.payment p WHERE p.organization_id=org AND p.owner_profile_id=owner AND p.property_id=b.property_id)
        OR EXISTS(SELECT 1 FROM public.economic_plan_line ln JOIN public.economic_plan p ON p.organization_id=ln.organization_id AND p.id=ln.economic_plan_id
          WHERE ln.organization_id=org AND ln.owner_profile_id=owner AND p.property_id=b.property_id)));
    END $$;
    REVOKE ALL ON FUNCTION payment_computation_context_valid(uuid,uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION payment_computation_context_valid(uuid,uuid,uuid) TO app_rw;
    ''')


def downgrade():
    op.execute('DROP FUNCTION payment_computation_context_valid(uuid,uuid,uuid)')
