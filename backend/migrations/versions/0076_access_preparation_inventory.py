"""Expose bounded administrative preparation counts without source content."""
from alembic import op

revision = '0076'
down_revision = '0075'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION access_preparation_inventory(org uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE instant timestamptz; result jsonb;
    BEGIN
      IF session_user<>'app_rw' OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Current administrator required' USING ERRCODE='42501'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      IF public.scope_actor_role(org) IS DISTINCT FROM 'OWNER_ADMIN' THEN
        RAISE EXCEPTION 'Current administrator required' USING ERRCODE='42501'; END IF;
      instant:=clock_timestamp();
      WITH members AS MATERIALIZED (
        SELECT u.id FROM public."user" u JOIN public.membership m ON m.organization_id=u.organization_id AND m.user_id=u.id
        WHERE u.organization_id=org AND u.is_active AND m.role IN ('OWNER_ADMIN','CLERK','READ_ONLY')
      ), properties AS MATERIALIZED (
        SELECT p.id,d.id domain_id FROM public.property p LEFT JOIN public.property_data_domain d
          ON d.organization_id=p.organization_id AND d.property_id=p.id WHERE p.organization_id=org
      ), contacts AS MATERIALIZED (
        SELECT p.id,p.created_by,d.id domain_id FROM public.person p LEFT JOIN public.person_data_domain d
          ON d.organization_id=p.organization_id AND d.person_id=p.id WHERE p.organization_id=org
      ), originals AS MATERIALIZED (
        SELECT d.id,d.property_id,d.uploaded_by,
          EXISTS(SELECT 1 FROM public.property_source_classification c WHERE c.organization_id=org AND c.document_id=d.id) OR
          EXISTS(SELECT 1 FROM public.property_source_admission a WHERE a.organization_id=org AND a.document_id=d.id) OR
          EXISTS(SELECT 1 FROM public.property_source_admission_review r WHERE r.organization_id=org AND r.document_id=d.id) AS has_record,
          EXISTS(SELECT 1 FROM public.property_source_classification c WHERE c.organization_id=org AND c.document_id=d.id AND c.decision='QUARANTINED') AS quarantined
        FROM public.property_document d WHERE d.organization_id=org
      ) SELECT jsonb_build_object(
        'generated_at',instant,'mode',public.property_source_security_mode(org),'activation_available',false,
        'counts',jsonb_build_object(
          'properties_total',(SELECT count(*) FROM properties),
          'properties_without_domain',(SELECT count(*) FROM properties WHERE domain_id IS NULL),
          'properties_without_current_reader',(SELECT count(*) FROM properties p WHERE NOT EXISTS(
            SELECT 1 FROM public.property_data_grant g JOIN members m ON m.id=g.user_id WHERE g.organization_id=org AND g.domain_id=p.domain_id
              AND 'read'=ANY(g.capabilities) AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at>instant))),
          'contacts_total',(SELECT count(*) FROM contacts),
          'contacts_without_domain',(SELECT count(*) FROM contacts WHERE domain_id IS NULL),
          'contacts_unknown_creator',(SELECT count(*) FROM contacts WHERE created_by IS NULL),
          'contacts_without_current_reader',(SELECT count(*) FROM contacts p WHERE NOT (
            (p.domain_id IS NULL AND EXISTS(SELECT 1 FROM members m WHERE m.id=p.created_by)) OR EXISTS(
              SELECT 1 FROM public.person_data_grant g JOIN members m ON m.id=g.user_id WHERE g.organization_id=org AND g.domain_id=p.domain_id
                AND 'read'=ANY(g.capabilities) AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at>instant)))),
          'originals_total',(SELECT count(*) FROM originals),
          'bound_originals_without_review_record',(SELECT count(*) FROM originals WHERE property_id IS NOT NULL AND NOT has_record),
          'originals_quarantined',(SELECT count(*) FROM originals WHERE quarantined),
          'unbound_originals_unknown_uploader',(SELECT count(*) FROM originals WHERE property_id IS NULL AND uploaded_by IS NULL),
          'bank_accounts_without_scope',(SELECT count(*) FROM public.bank_account r WHERE r.organization_id=org AND NOT EXISTS(
            SELECT 1 FROM public.accounting_scope_binding b WHERE b.organization_id=org AND b.bank_account_id=r.id)),
          'payments_without_scope',(SELECT count(*) FROM public.payment r WHERE r.organization_id=org AND NOT EXISTS(
            SELECT 1 FROM public.accounting_scope_binding b WHERE b.organization_id=org AND b.payment_id=r.id)),
          'cost_entries_without_scope',(SELECT count(*) FROM public.cost_entry r WHERE r.organization_id=org AND NOT EXISTS(
            SELECT 1 FROM public.accounting_scope_binding b WHERE b.organization_id=org AND b.cost_entry_id=r.id)),
          'settlements_without_scope',(SELECT count(*) FROM public.settlement_run r WHERE r.organization_id=org AND NOT EXISTS(
            SELECT 1 FROM public.accounting_scope_binding b WHERE b.organization_id=org AND b.settlement_run_id=r.id)),
          'economic_plans_without_scope',(SELECT count(*) FROM public.economic_plan r WHERE r.organization_id=org AND NOT EXISTS(
            SELECT 1 FROM public.accounting_scope_binding b WHERE b.organization_id=org AND b.economic_plan_id=r.id)),
          'dunning_without_context',(SELECT count(*) FROM public.dunning_notice r WHERE r.organization_id=org AND NOT EXISTS(
            SELECT 1 FROM public.dunning_scope_context c WHERE c.organization_id=org AND c.dunning_notice_id=r.id)),
          'mandate_originals_without_scope',(SELECT count(*) FROM public.mandate_document d WHERE d.organization_id=org AND d.accounting_scope_id IS NULL)
        )) INTO result;
      RETURN result;
    END $$;
    REVOKE ALL ON FUNCTION access_preparation_inventory(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION access_preparation_inventory(uuid) TO app_rw;
    """)


def downgrade():
    op.execute('DROP FUNCTION access_preparation_inventory(uuid)')
