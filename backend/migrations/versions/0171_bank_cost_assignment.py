"""Match outgoing bank lines to immutable existing costs with bounded coverage."""
from alembic import op
from sqlalchemy import text

revision = '0171'
down_revision = '0170'
branch_labels = None
depends_on = None


def retention(reverse=False):
    catalog = op.get_bind().scalar(text("SELECT pg_get_functiondef('organization_retention_catalog()'::regprocedure)"))
    before = "'trg_cost_entry_immutability','protect_adopted_cost'"
    after = "'guard_bank_cost_assignment','trg_cost_entry_immutability','protect_adopted_cost'"
    source = op.get_bind().scalar(text("SELECT pg_get_functiondef('organization_retention_plan(uuid)'::regprocedure)"))
    marker = '      -- Legacy WEG/plan rendering resolves every historical owner of the'
    addition = '''      -- Bank evidence inherits the cost deadline without becoming a personal root.
      INSERT INTO pg_temp.organization_retention_work AS existing
        SELECT 'bank_transaction',jsonb_build_object('id',b.id),w.retention_until,
          'ADR-0012: bank evidence of retained cost',NULL::date
        FROM public.bank_transaction b JOIN pg_temp.organization_retention_work w
          ON w.entity='cost_entry' AND w.identity=jsonb_build_object('id',b.cost_entry_id)
        WHERE b.organization_id=org
        ON CONFLICT(entity,identity) DO UPDATE SET
          retention_until=GREATEST(existing.retention_until,EXCLUDED.retention_until);
'''
    old, new = (after, before) if reverse else (before, after)
    if catalog.count(old) != 1 or source.count(addition if reverse else marker) != 1:
        raise RuntimeError('Unexpected organization retention definitions; repair forward')
    op.execute(catalog.replace(old, new, 1))
    op.execute(source.replace(addition, '', 1) if reverse else source.replace(marker, addition + marker, 1))


def upgrade():
    op.execute('''ALTER TABLE bank_transaction ADD COLUMN cost_entry_id uuid;
      ALTER TABLE bank_transaction ADD CONSTRAINT fk_bank_transaction_cost_org
        FOREIGN KEY(organization_id,cost_entry_id) REFERENCES cost_entry(organization_id,id) ON DELETE RESTRICT;
      CREATE INDEX ix_bank_transaction_cost ON bank_transaction(organization_id,cost_entry_id) WHERE cost_entry_id IS NOT NULL;
      ALTER TABLE bank_transaction ADD CONSTRAINT ck_bank_transaction_cost_assignment
        CHECK(cost_entry_id IS NULL OR (amount<0 AND status='ZUGEORDNET'));
      CREATE FUNCTION bank_cost_open_amount(org uuid,cost uuid) RETURNS numeric
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
        SELECT c.gross_amount+coalesce((SELECT sum(b.amount) FROM public.bank_transaction b
          WHERE b.organization_id=org AND b.cost_entry_id=cost),0)
        FROM public.cost_entry c WHERE c.organization_id=org AND c.id=cost
          AND public.financial_scope_access(org,'cost_entry',cost,'read')
          AND NOT EXISTS(SELECT 1 FROM public.bank_transaction b WHERE b.organization_id=org AND b.cost_entry_id=cost
            AND NOT public.financial_scope_access(org,'bank_account',b.bank_account_id,'read'))
      $$;
      REVOKE ALL ON FUNCTION bank_cost_open_amount(uuid,uuid) FROM PUBLIC,app_worker,app_review;
      GRANT EXECUTE ON FUNCTION bank_cost_open_amount(uuid,uuid) TO app_rw;
      CREATE FUNCTION guard_bank_cost_assignment() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      DECLARE source public.cost_entry; account public.bank_account; covered numeric;
        cost_scope uuid; bank_scope uuid;
      BEGIN
        IF TG_OP IN ('UPDATE','DELETE') AND OLD.cost_entry_id IS NOT NULL THEN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'Bank evidence of confirmed cost cannot be deleted' USING ERRCODE='23514'; END IF;
          IF (NEW.id,NEW.organization_id,NEW.bank_account_id,NEW.cost_entry_id,NEW.amount,
              NEW.status,NEW.booking_date,NEW.value_date,NEW.import_hash,NEW.created_at)
            IS DISTINCT FROM (OLD.id,OLD.organization_id,OLD.bank_account_id,OLD.cost_entry_id,OLD.amount,
              OLD.status,OLD.booking_date,OLD.value_date,OLD.import_hash,OLD.created_at) THEN
            RAISE EXCEPTION 'Bank evidence of confirmed cost is immutable' USING ERRCODE='23514'; END IF;
          RETURN NEW;
        END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF NEW.cost_entry_id IS NULL THEN RETURN NEW; END IF;
        PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
        PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||NEW.organization_id::text,0));
        SELECT * INTO source FROM public.cost_entry WHERE organization_id=NEW.organization_id AND id=NEW.cost_entry_id FOR UPDATE;
        SELECT * INTO account FROM public.bank_account WHERE organization_id=NEW.organization_id AND id=NEW.bank_account_id;
        IF source.id IS NULL OR account.id IS NULL OR source.property_id<>account.property_id
          OR source.status<>'confirmed' OR source.superseded_by_id IS NOT NULL OR NEW.amount>=0 OR NEW.status<>'ZUGEORDNET' THEN
          RAISE EXCEPTION 'Active confirmed same-property cost and outgoing bank line required' USING ERRCODE='23514'; END IF;
        IF TG_OP='UPDATE' AND OLD.status<>'NEU' THEN
          RAISE EXCEPTION 'Bank line already processed' USING ERRCODE='23514'; END IF;
        IF EXISTS(SELECT 1 FROM public.payment p WHERE p.organization_id=NEW.organization_id AND p.bank_transaction_id=NEW.id) THEN
          RAISE EXCEPTION 'Bank line already linked to a payment' USING ERRCODE='23514'; END IF;
        SELECT scope_id INTO cost_scope FROM public.accounting_scope_binding WHERE organization_id=NEW.organization_id AND cost_entry_id=source.id;
        SELECT scope_id INTO bank_scope FROM public.accounting_scope_binding WHERE organization_id=NEW.organization_id AND bank_account_id=account.id;
        IF cost_scope IS DISTINCT FROM bank_scope THEN
          RAISE EXCEPTION 'Bank and cost accounting scopes differ' USING ERRCODE='23514'; END IF;
        IF session_user<>'app_migrator' AND (
          NOT public.financial_scope_access(NEW.organization_id,'cost_entry',source.id,'prepare')
          OR NOT public.financial_scope_access(NEW.organization_id,'cost_entry',source.id,'confirm')
          OR NOT public.financial_scope_access(NEW.organization_id,'bank_account',account.id,'confirm')) THEN
          RAISE EXCEPTION 'Financial source authority required' USING ERRCODE='42501'; END IF;
        SELECT coalesce(-sum(amount),0) INTO covered FROM public.bank_transaction
          WHERE organization_id=NEW.organization_id AND cost_entry_id=source.id AND id<>NEW.id;
        IF covered-NEW.amount>source.gross_amount THEN
          RAISE EXCEPTION 'Bank debits exceed cost total' USING ERRCODE='23514'; END IF;
        RETURN NEW;
      END $$;
      REVOKE ALL ON FUNCTION guard_bank_cost_assignment() FROM PUBLIC,app_rw,app_worker,app_review;
      CREATE TRIGGER guard_bank_cost_assignment BEFORE INSERT OR UPDATE OR DELETE ON bank_transaction
        FOR EACH ROW EXECUTE FUNCTION guard_bank_cost_assignment();
      ALTER TABLE bank_transaction ENABLE ALWAYS TRIGGER guard_bank_cost_assignment;
      CREATE TRIGGER a_lock_bank_cost_inputs BEFORE INSERT OR UPDATE OR DELETE ON bank_transaction
        FOR EACH STATEMENT EXECUTE FUNCTION lock_strict_property_source_binding();
      CREATE POLICY bank_transaction_cost_read ON bank_transaction AS RESTRICTIVE FOR SELECT
        USING(cost_entry_id IS NULL OR financial_scope_access(organization_id,'cost_entry',cost_entry_id,'read'));
      CREATE FUNCTION guard_payment_cost_bank() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      BEGIN
        IF NEW.bank_transaction_id IS NOT NULL AND EXISTS(SELECT 1 FROM public.bank_transaction b
          WHERE b.organization_id=NEW.organization_id AND b.id=NEW.bank_transaction_id AND b.cost_entry_id IS NOT NULL) THEN
          RAISE EXCEPTION 'Bank line already covers a cost' USING ERRCODE='23514'; END IF;
        RETURN NEW;
      END $$;
      REVOKE ALL ON FUNCTION guard_payment_cost_bank() FROM PUBLIC,app_rw,app_worker,app_review;
      CREATE TRIGGER guard_payment_cost_bank BEFORE INSERT OR UPDATE OF bank_transaction_id ON payment
        FOR EACH ROW EXECUTE FUNCTION guard_payment_cost_bank();
      ALTER FUNCTION validate_accounting_binding(public.accounting_scope_binding) RENAME TO validate_accounting_binding_before_bank_cost;
      CREATE FUNCTION validate_accounting_binding(binding public.accounting_scope_binding) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      BEGIN
        PERFORM public.validate_accounting_binding_before_bank_cost(binding);
        IF binding.cost_entry_id IS NULL AND binding.bank_account_id IS NULL THEN RETURN; END IF;
        IF EXISTS(SELECT 1 FROM public.bank_transaction b JOIN public.accounting_scope_binding other
          ON other.organization_id=b.organization_id AND (
            (binding.cost_entry_id=b.cost_entry_id AND other.bank_account_id=b.bank_account_id)
            OR (binding.bank_account_id=b.bank_account_id AND other.cost_entry_id=b.cost_entry_id))
          WHERE b.organization_id=binding.organization_id AND b.cost_entry_id IS NOT NULL
            AND other.scope_id<>binding.scope_id) THEN
          RAISE EXCEPTION 'Bank and matched cost must retain the same accounting scope' USING ERRCODE='23514'; END IF;
      END $$;
      REVOKE ALL ON FUNCTION validate_accounting_binding(public.accounting_scope_binding) FROM PUBLIC,app_rw,app_worker,app_review;
    ''')
    retention()


def downgrade():
    op.execute('LOCK TABLE bank_transaction IN ACCESS EXCLUSIVE MODE NOWAIT')
    if op.get_bind().scalar(text('SELECT EXISTS(SELECT 1 FROM bank_transaction WHERE cost_entry_id IS NOT NULL)')):
        raise RuntimeError('Retain bank cost assignments; repair forward')
    retention(reverse=True)
    op.execute('''DROP POLICY bank_transaction_cost_read ON bank_transaction;
      DROP FUNCTION validate_accounting_binding(public.accounting_scope_binding);
      ALTER FUNCTION validate_accounting_binding_before_bank_cost(public.accounting_scope_binding) RENAME TO validate_accounting_binding;
      DROP TRIGGER a_lock_bank_cost_inputs ON bank_transaction;
      DROP TRIGGER guard_bank_cost_assignment ON bank_transaction;
      DROP TRIGGER guard_payment_cost_bank ON payment;
      DROP FUNCTION guard_payment_cost_bank();
      DROP FUNCTION guard_bank_cost_assignment(),bank_cost_open_amount(uuid,uuid);
      ALTER TABLE bank_transaction DROP COLUMN cost_entry_id;
    ''')
