"""Editable master metadata and explicit bank/vendor lifecycle (T55)."""
from alembic import op
import sqlalchemy as sa

revision = '0136'
down_revision = '0135'
branch_labels = None
depends_on = None


REFERENCE_TABLES = ('payment', 'bank_transaction', 'sepa_creditor', 'sepa_mandate', 'cost_entry')
LOCK_TABLES = ('bank_account', 'vendor') + REFERENCE_TABLES


def upgrade():
    op.add_column('bank_account', sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('vendor', sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('property', sa.Column('construction_year', sa.Integer(), nullable=True))
    op.add_column('property', sa.Column('total_area', sa.Numeric(14, 2), nullable=True))
    op.create_check_constraint(op.f('ck_property_ck_property_construction_year'), 'property', 'construction_year BETWEEN 1 AND 9999')
    op.create_check_constraint(op.f('ck_property_ck_property_total_area'), 'property', 'total_area >= 0 AND total_area < 1000000000000')
    op.execute("""
    CREATE FUNCTION t55_input_lock() RETURNS trigger LANGUAGE plpgsql
      SET search_path=pg_catalog,pg_temp AS $$
    DECLARE org uuid:=nullif(current_setting('app.current_org_id',true),'')::uuid;
    BEGIN
      IF org IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
        PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      END IF;
      RETURN NULL;
    END $$;
    CREATE FUNCTION t55_bank_details_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF (NEW.iban,NEW.bic) IS DISTINCT FROM (OLD.iban,OLD.bic) AND (
        EXISTS(SELECT 1 FROM public.payment p WHERE p.organization_id=OLD.organization_id AND p.bank_account_id=OLD.id)
        OR EXISTS(SELECT 1 FROM public.bank_transaction t WHERE t.organization_id=OLD.organization_id AND t.bank_account_id=OLD.id)
        OR EXISTS(SELECT 1 FROM public.sepa_creditor c WHERE c.organization_id=OLD.organization_id AND c.bank_account_id=OLD.id)
        OR EXISTS(SELECT 1 FROM public.sepa_mandate m WHERE m.organization_id=OLD.organization_id AND m.bank_account_id=OLD.id)
      ) THEN
        RAISE EXCEPTION 'Used bank account details cannot be rewritten; create a new account instead' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION t55_active_reference_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE target uuid;
    BEGIN
      IF TG_TABLE_NAME='cost_entry' THEN
        target:=NEW.vendor_id;
        IF target IS NULL THEN RETURN NEW; END IF;
        IF TG_OP='UPDATE' THEN
          IF (NEW.organization_id,NEW.vendor_id) IS NOT DISTINCT FROM (OLD.organization_id,OLD.vendor_id) THEN RETURN NEW; END IF;
        END IF;
        IF EXISTS(SELECT 1 FROM public.vendor v WHERE v.organization_id=NEW.organization_id AND v.id=target AND NOT v.is_active) THEN
          RAISE EXCEPTION 'Vendor is inactive; select an active vendor for new costs' USING ERRCODE='23514';
        END IF;
      ELSE
        target:=NEW.bank_account_id;
        IF TG_OP='UPDATE' THEN
          IF (NEW.organization_id,NEW.bank_account_id) IS NOT DISTINCT FROM (OLD.organization_id,OLD.bank_account_id) THEN RETURN NEW; END IF;
        END IF;
        IF TG_TABLE_NAME='payment' THEN
          IF NEW.reverses_id IS NOT NULL AND NEW.status='booked' AND EXISTS(
            SELECT 1 FROM public.payment p WHERE p.organization_id=NEW.organization_id AND p.id=NEW.reverses_id
              AND p.status='booked' AND p.reverses_id IS NULL AND p.reversed_by_id IS NULL
              AND (p.bank_account_id,p.property_id,p.management_mandate_id,p.amount,p.value_date,
                   p.payment_type,p.tenant_profile_id,p.owner_profile_id,p.lease_agreement_id,
                   p.period_year,p.period_month,p.settlement_run_id)
                IS NOT DISTINCT FROM
                  (NEW.bank_account_id,NEW.property_id,NEW.management_mandate_id,NEW.amount,NEW.value_date,
                   NEW.payment_type,NEW.tenant_profile_id,NEW.owner_profile_id,NEW.lease_agreement_id,
                   NEW.period_year,NEW.period_month,NEW.settlement_run_id)
              AND p.direction<>NEW.direction
          ) THEN RETURN NEW; END IF;
        END IF;
        IF EXISTS(SELECT 1 FROM public.bank_account b WHERE b.organization_id=NEW.organization_id AND b.id=target AND NOT b.is_active) THEN
          RAISE EXCEPTION 'Bank account is inactive; select an active account for new references' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION t55_input_lock(),t55_bank_details_guard(),t55_active_reference_guard() FROM PUBLIC,app_rw;
    CREATE TRIGGER t55_bank_details_guard BEFORE UPDATE OF iban,bic ON bank_account
      FOR EACH ROW EXECUTE FUNCTION t55_bank_details_guard();
    """)
    for table in LOCK_TABLES:
        op.execute(f"CREATE TRIGGER a_t55_input_lock BEFORE INSERT OR UPDATE OR DELETE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION t55_input_lock()")
    for table in REFERENCE_TABLES:
        op.execute(f"CREATE TRIGGER t55_active_reference_guard BEFORE INSERT OR UPDATE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION t55_active_reference_guard()")


def downgrade():
    op.execute("""
    LOCK TABLE bank_account,vendor,property IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM bank_account WHERE NOT is_active)
        OR EXISTS(SELECT 1 FROM vendor WHERE NOT is_active)
        OR EXISTS(SELECT 1 FROM property WHERE construction_year IS NOT NULL OR total_area IS NOT NULL) THEN
        RAISE EXCEPTION 'Cannot remove populated T55 lifecycle or property facts';
      END IF;
    END $$;
    """)
    for table in REFERENCE_TABLES:
        op.execute(f'DROP TRIGGER t55_active_reference_guard ON {table}')
    for table in LOCK_TABLES:
        op.execute(f'DROP TRIGGER a_t55_input_lock ON {table}')
    op.execute("""
    DROP TRIGGER t55_bank_details_guard ON bank_account;
    DROP FUNCTION t55_active_reference_guard();
    DROP FUNCTION t55_bank_details_guard();
    DROP FUNCTION t55_input_lock();
    """)
    op.drop_constraint(op.f('ck_property_ck_property_total_area'), 'property', type_='check')
    op.drop_constraint(op.f('ck_property_ck_property_construction_year'), 'property', type_='check')
    op.drop_column('property', 'total_area')
    op.drop_column('property', 'construction_year')
    op.drop_column('vendor', 'is_active')
    op.drop_column('bank_account', 'is_active')
