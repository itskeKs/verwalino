"""Persist invoice job principals and cancel stale permissions without reading bytes."""
from alembic import op

revision = '0035'
down_revision = '0034'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    ALTER TABLE invoice_processing_attempt ADD COLUMN initiated_by uuid,
      ADD COLUMN requested_scope_id uuid,
      ADD CONSTRAINT fk_invoice_attempt_initiator FOREIGN KEY(organization_id,initiated_by) REFERENCES "user"(organization_id,id),
      ADD CONSTRAINT fk_invoice_attempt_scope FOREIGN KEY(organization_id,requested_scope_id) REFERENCES accounting_scope(organization_id,id);
    CREATE FUNCTION protect_invoice_job_principal() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF (NEW.organization_id,NEW.document_id,NEW.initiated_by,NEW.requested_scope_id,NEW.attempt_no)
        IS DISTINCT FROM (OLD.organization_id,OLD.document_id,OLD.initiated_by,OLD.requested_scope_id,OLD.attempt_no) THEN
        RAISE EXCEPTION 'Invoice job principal and scope are immutable' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER protect_invoice_job_principal BEFORE UPDATE ON invoice_processing_attempt
      FOR EACH ROW EXECUTE FUNCTION protect_invoice_job_principal();

    CREATE FUNCTION invoice_attempt_authorized(org uuid, attempt uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE job public.invoice_processing_attempt;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN false; END IF;
      SELECT * INTO job FROM public.invoice_processing_attempt WHERE organization_id=org AND id=attempt;
      IF NOT FOUND OR job.initiated_by IS NULL OR job.initiated_by IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      RETURN COALESCE(public.scope_actor_role(org) IN ('OWNER_ADMIN','CLERK')
        AND public.financial_scope_access(org,'invoice_document',job.document_id,'prepare')
        AND job.requested_scope_id IS NOT DISTINCT FROM public.financial_scope_id(org,'invoice_document',job.document_id),false);
    END $$;
    CREATE FUNCTION cancel_unauthorized_invoice_attempt(org uuid, attempt uuid, fence uuid) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE job public.invoice_processing_attempt;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN false; END IF;
      SELECT * INTO job FROM public.invoice_processing_attempt WHERE organization_id=org AND id=attempt;
      IF NOT FOUND OR job.initiated_by IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid THEN RETURN false; END IF;
      -- Keep the same document-before-attempt lock order as normal claims/finish.
      PERFORM 1 FROM public.invoice_document WHERE organization_id=org AND id=job.document_id FOR UPDATE;
      SELECT * INTO job FROM public.invoice_processing_attempt WHERE organization_id=org AND id=attempt FOR UPDATE;
      IF job.status NOT IN ('queued','running') OR job.token IS DISTINCT FROM fence
        OR public.invoice_attempt_authorized(org,attempt) THEN RETURN false; END IF;
      UPDATE public.invoice_processing_attempt SET status='canceled',error_code='scope_permission_changed',completed_at=clock_timestamp()
        WHERE organization_id=org AND id=attempt;
      UPDATE public.invoice_document SET processing_status='canceled',processing_error_code='scope_permission_changed'
        WHERE organization_id=org AND id=job.document_id
          AND NOT EXISTS(SELECT 1 FROM public.invoice_processing_attempt a WHERE a.organization_id=org AND a.document_id=job.document_id AND a.status IN ('queued','running'));
      INSERT INTO public.audit_event(organization_id,entity_type,entity_id,action,payload_diff)
        VALUES(org,'invoice_document',job.document_id,'invoice.processing_canceled',jsonb_build_object('attempt_id',job.id,'reason','scope_permission_changed'));
      RETURN true;
    END $$;
    ALTER FUNCTION authorize_scoped_invoice_write() RENAME TO authorize_scoped_invoice_write_baseline;
    CREATE FUNCTION authorize_scoped_invoice_write() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE original_scope uuid; replacement_scope uuid; capability text;
    BEGIN
      -- An unauthorized job may only cancel processing metadata. It never reads
      -- bytes or alters an original, a financial link or extraction proposals.
      IF TG_OP='UPDATE' AND NEW.processing_status='canceled' AND NEW.processing_error_code='scope_permission_changed'
        AND (to_jsonb(NEW)-ARRAY['processing_status','processing_error_code','updated_at'])=(to_jsonb(OLD)-ARRAY['processing_status','processing_error_code','updated_at'])
        AND EXISTS(SELECT 1 FROM public.invoice_processing_attempt a WHERE a.organization_id=NEW.organization_id AND a.document_id=NEW.id
          AND a.status='canceled' AND a.error_code='scope_permission_changed'
          AND NOT EXISTS(SELECT 1 FROM public.invoice_processing_attempt newer WHERE newer.organization_id=a.organization_id AND newer.document_id=a.document_id AND newer.attempt_no>a.attempt_no)
          AND a.initiated_by IS NOT DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid)
        AND NOT EXISTS(SELECT 1 FROM public.invoice_processing_attempt a WHERE a.organization_id=NEW.organization_id AND a.document_id=NEW.id AND a.status IN ('queued','running')) THEN RETURN NEW; END IF;
      capability:=CASE WHEN NEW.released_at IS NOT NULL THEN 'confirm' ELSE 'prepare' END;
      replacement_scope:=public.financial_scope_id(NEW.organization_id,'cost_entry',NEW.cost_entry_id);
      IF (replacement_scope IS NOT NULL OR NULLIF(current_setting('app.current_scope_id',true),'') IS NOT NULL)
        AND NOT public.financial_scope_access(NEW.organization_id,'cost_entry',NEW.cost_entry_id,capability) THEN
        RAISE EXCEPTION 'Source scope capability required' USING ERRCODE='42501';
      END IF;
      IF TG_OP='UPDATE' THEN
        original_scope:=public.financial_scope_id(OLD.organization_id,'cost_entry',OLD.cost_entry_id);
        IF original_scope IS NOT NULL AND original_scope IS DISTINCT FROM replacement_scope THEN
          RAISE EXCEPTION 'A classified source cannot leave its accounting scope' USING ERRCODE='23514';
        END IF;
        IF original_scope IS NOT NULL AND NOT public.financial_scope_access(OLD.organization_id,'cost_entry',OLD.cost_entry_id,capability) THEN
          RAISE EXCEPTION 'Source scope capability required' USING ERRCODE='42501';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    DROP TRIGGER authorize_scoped_invoice_write ON invoice_document;
    CREATE TRIGGER authorize_scoped_invoice_write BEFORE INSERT OR UPDATE ON invoice_document FOR EACH ROW EXECUTE FUNCTION authorize_scoped_invoice_write();
    CREATE POLICY classified_financial_update ON invoice_document AS RESTRICTIVE FOR UPDATE
      USING (financial_scope_access(organization_id,'invoice_document',id,'read'))
      WITH CHECK (financial_scope_access(organization_id,'cost_entry',cost_entry_id,'read'));
    REVOKE ALL ON FUNCTION protect_invoice_job_principal(),invoice_attempt_authorized(uuid,uuid),cancel_unauthorized_invoice_attempt(uuid,uuid,uuid),authorize_scoped_invoice_write() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION invoice_attempt_authorized(uuid,uuid),cancel_unauthorized_invoice_attempt(uuid,uuid,uuid) TO app_rw;
    ''')


def downgrade():
    op.execute('''
    DROP POLICY classified_financial_update ON invoice_document;
    DROP TRIGGER authorize_scoped_invoice_write ON invoice_document;
    DROP FUNCTION authorize_scoped_invoice_write();
    ALTER FUNCTION authorize_scoped_invoice_write_baseline() RENAME TO authorize_scoped_invoice_write;
    CREATE TRIGGER authorize_scoped_invoice_write BEFORE INSERT OR UPDATE ON invoice_document FOR EACH ROW EXECUTE FUNCTION authorize_scoped_invoice_write();
    DROP TRIGGER protect_invoice_job_principal ON invoice_processing_attempt;
    DROP FUNCTION cancel_unauthorized_invoice_attempt(uuid,uuid,uuid),invoice_attempt_authorized(uuid,uuid),protect_invoice_job_principal();
    ALTER TABLE invoice_processing_attempt DROP COLUMN initiated_by, DROP COLUMN requested_scope_id;
    ''')
