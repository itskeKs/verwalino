"""Remove the unclassified money compatibility fallback in strict source mode."""
from alembic import op
from sqlalchemy import text

revision = '0081'
down_revision = '0080'
branch_labels = None
depends_on = None
TABLES = ('bank_account', 'payment', 'cost_entry', 'settlement_run', 'economic_plan')
BEFORE = 'IF sphere IS NULL THEN RETURN selected IS NULL; END IF; -- legacy compatibility only'
AFTER = """IF public.property_source_security_mode(org)='PAUSED' AND capability NOT IN ('read','export') THEN RETURN false; END IF;
      IF sphere IS NULL THEN
        IF public.property_source_security_mode(org) IS DISTINCT FROM 'PREPARATION' THEN
          IF kind IN ('bank_account','payment','cost_entry','settlement_run','economic_plan','coverage_declaration') AND resource IS NOT NULL THEN RETURN false; END IF;
          IF kind='invoice_document' AND EXISTS(SELECT 1 FROM public.invoice_document d
            WHERE d.organization_id=org AND d.id=resource AND d.cost_entry_id IS NOT NULL) THEN RETURN false; END IF;
        END IF;
        -- Other audit kinds and standalone invoice intake have separate boundaries.
        RETURN selected IS NULL;
      END IF;"""


INVOICE_BEFORE = "IF (replacement_scope IS NOT NULL OR NULLIF(current_setting('app.current_scope_id',true),'') IS NOT NULL)"
INVOICE_AFTER = "IF ((NEW.cost_entry_id IS NOT NULL AND public.property_source_security_mode(NEW.organization_id) IN ('ENFORCED','PAUSED')) OR replacement_scope IS NOT NULL OR NULLIF(current_setting('app.current_scope_id',true),'') IS NOT NULL)"


def _replace(before, after, signature='public.financial_scope_access(uuid,text,uuid,text)'):
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"), {'signature': signature})
    if definition.count(before) != 1:
        raise RuntimeError('Unexpected financial scope helper; refusing partial enforcement')
    op.execute(definition.replace(before, after))


def upgrade():
    op.execute('LOCK TABLE property_source_security,accounting_scope_binding,invoice_document,invoice_processing_attempt,' + ','.join(TABLES) + ' IN ACCESS EXCLUSIVE MODE NOWAIT')
    _replace(BEFORE, AFTER)
    _replace(INVOICE_BEFORE, INVOICE_AFTER, 'public.authorize_scoped_invoice_write()')
    op.execute("""
    CREATE FUNCTION guard_strict_financial_resource() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE row_data jsonb; org uuid; capability text:='prepare';
    BEGIN
      row_data:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
      org:=(row_data->>'organization_id')::uuid;
      -- Trusted migration fixtures may retain unknown historical rows. Runtime
      -- inserts remain tenant constrained by their existing RLS policies.
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
      END IF;
      IF public.property_source_security_mode(org) IS DISTINCT FROM 'PREPARATION' THEN
        IF TG_TABLE_NAME='cost_entry' AND row_data->>'status'='confirmed' THEN capability:='confirm';
        ELSIF TG_TABLE_NAME='payment' AND row_data->>'status'='booked' THEN capability:='confirm';
        ELSIF TG_TABLE_NAME IN ('settlement_run','economic_plan') AND row_data->>'status'='finalized' THEN capability:='approve'; END IF;
        IF NOT public.financial_scope_access(org,TG_TABLE_NAME,(row_data->>'id')::uuid,capability) THEN
          RAISE EXCEPTION 'Strict finance requires a current exact accounting scope and capability' USING ERRCODE='42501'; END IF;
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION guard_strict_financial_resource() FROM PUBLIC,app_rw,app_worker,app_review;
    """)
    op.execute("""
    CREATE FUNCTION invoice_queue_metadata(org uuid,attempt uuid DEFAULT NULL) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE result jsonb;
    BEGIN
      IF session_user<>'app_worker' OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Isolated invoice worker and tenant required' USING ERRCODE='42501'; END IF;
      SELECT jsonb_build_object('id',a.id,'document_id',a.document_id,'initiated_by',a.initiated_by,'token',a.token)
        INTO result FROM public.invoice_processing_attempt a WHERE a.organization_id=org
        AND (CASE WHEN attempt IS NOT NULL THEN a.id=attempt
          ELSE a.status='queued' OR (a.status='running' AND a.lease_until<clock_timestamp()) END)
        ORDER BY a.created_at,a.id LIMIT 1;
      RETURN result;
    END $$;
    CREATE FUNCTION invoice_queue_count(org uuid) RETURNS bigint
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF session_user<>'app_rw' OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN NULL; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('invoice-queue:'||org::text,0));
      RETURN (SELECT count(*) FROM public.invoice_processing_attempt WHERE organization_id=org AND status IN ('queued','running'));
    END $$;
    REVOKE ALL ON FUNCTION invoice_queue_metadata(uuid,uuid) FROM PUBLIC,app_rw,app_review;
    GRANT EXECUTE ON FUNCTION invoice_queue_metadata(uuid,uuid) TO app_worker;
    REVOKE ALL ON FUNCTION invoice_queue_count(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION invoice_queue_count(uuid) TO app_rw;
    CREATE POLICY classified_attempt_read ON invoice_processing_attempt AS RESTRICTIVE FOR SELECT
      USING(financial_scope_access(organization_id,'invoice_document',document_id,'read'));
    CREATE POLICY classified_attempt_insert ON invoice_processing_attempt AS RESTRICTIVE FOR INSERT
      WITH CHECK(financial_scope_access(organization_id,'invoice_document',document_id,'prepare'));
    CREATE POLICY classified_attempt_update ON invoice_processing_attempt AS RESTRICTIVE FOR UPDATE
      USING(financial_scope_access(organization_id,'invoice_document',document_id,'prepare'))
      WITH CHECK(financial_scope_access(organization_id,'invoice_document',document_id,'prepare'));
    CREATE TRIGGER a_lock_invoice_attempt_acl BEFORE INSERT OR UPDATE OR DELETE ON invoice_processing_attempt
      FOR EACH STATEMENT EXECUTE FUNCTION lock_strict_property_source_binding();
    """)
    for table in TABLES:
        op.execute(f'CREATE TRIGGER a_lock_strict_financial_acl BEFORE INSERT OR UPDATE OR DELETE ON {table} '
                   'FOR EACH STATEMENT EXECUTE FUNCTION lock_strict_property_source_binding()')
        # PostgreSQL orders same-event triggers by name: the atomic creation
        # binding must exist before this check, while still before row insertion.
        op.execute(f'CREATE TRIGGER zz_guard_strict_financial_resource BEFORE INSERT OR UPDATE OR DELETE ON {table} '
                   'FOR EACH ROW EXECUTE FUNCTION guard_strict_financial_resource()')


def downgrade():
    op.execute('LOCK TABLE property_source_security,accounting_scope_binding,invoice_document,invoice_processing_attempt,' + ','.join(TABLES) + ' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain strict financial scope access; pause and repair forward'; END IF; END $$""")
    for table in TABLES:
        op.execute(f'DROP TRIGGER zz_guard_strict_financial_resource ON {table}')
        op.execute(f'DROP TRIGGER a_lock_strict_financial_acl ON {table}')
    op.execute('DROP FUNCTION guard_strict_financial_resource()')
    op.execute('''DROP POLICY classified_attempt_read ON invoice_processing_attempt;
      DROP POLICY classified_attempt_insert ON invoice_processing_attempt;
      DROP POLICY classified_attempt_update ON invoice_processing_attempt;
      DROP TRIGGER a_lock_invoice_attempt_acl ON invoice_processing_attempt;
      DROP FUNCTION invoice_queue_metadata(uuid,uuid),invoice_queue_count(uuid);''')
    _replace(AFTER, BEFORE)
    _replace(INVOICE_AFTER, INVOICE_BEFORE, 'public.authorize_scoped_invoice_write()')
