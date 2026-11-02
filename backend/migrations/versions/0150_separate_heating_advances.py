"""Separate tenant operating-cost and heating advances without rewriting history."""
from alembic import op
from sqlalchemy import text

revision = '0150'
down_revision = '0149'
branch_labels = None
depends_on = None

PAYMENT_TYPES = "'KALTMIETE','NK_VORAUSZAHLUNG','HAUSGELD_VORSCHUSS','RUECKLAGENANTEIL','SONDERZAHLUNG','NACHZAHLUNG','KAUTION','RUECKLAGENENTNAHME'"


def patch_function(signature, before, after, reverse=False):
    if reverse:
        before, after = after, before
    definition = op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),
        {'signature': signature})
    assert definition.count(before) == 1, signature
    op.execute(definition.replace(before, after))


def patch_guards(reverse=False):
    patch_function('validate_accounting_binding_baseline(public.accounting_scope_binding)',
        "'KALTMIETE','NK_VORAUSZAHLUNG','KAUTION'", "'KALTMIETE','NK_VORAUSZAHLUNG','HK_VORAUSZAHLUNG','KAUTION'", reverse)
    patch_function('computation_input_basis(uuid,text,uuid,boolean)',
        "'advance_payments_total','advance_payments_source'",
        "'advance_payments_total','advance_payments_source','heating_advance_payments_total','heating_advance_payments_source'", reverse)
    # Old frozen parents were hashed before these columns existed. Read their
    # retained payload as-is; only new split snapshots include the new columns.
    patch_function('financial_computation_state(uuid,text,uuid)',
        "public.bounded_computation_digest(parent-'updated_at')",
        "public.bounded_computation_digest((parent-'updated_at') - CASE WHEN payload#>>'{inputs,heating_advance_payments_total}' IS NULL THEN ARRAY['heating_advance_payments_total','heating_advance_payments_source'] ELSE ARRAY[]::text[] END)", reverse)
    original = "    IF OLD.status = 'finalized' THEN\n        IF NEW.organization_id"
    guarded = "    IF OLD.status = 'finalized' THEN" + """
        IF NEW.heating_advance_payments_total IS DISTINCT FROM OLD.heating_advance_payments_total
          OR NEW.heating_advance_payments_source IS DISTINCT FROM OLD.heating_advance_payments_source THEN
            RAISE EXCEPTION 'GoBD: Cannot change heating advances on finalized settlement_run';
        END IF;
        IF NEW.organization_id"""
    patch_function('trg_settlement_run_immutability()', original, guarded, reverse)


def upgrade():
    for table in ('lease_agreement', 'lease_rent_change'):
        op.execute(f"""ALTER TABLE {table} ADD COLUMN heating_advance_payment NUMERIC(14,2) NOT NULL DEFAULT 0;
            ALTER TABLE {table} ADD CONSTRAINT ck_{table}_heating_advance_finite_nonnegative
            CHECK(heating_advance_payment >= 0 AND heating_advance_payment < 'Infinity'::numeric)""")
    op.execute(f"""ALTER TABLE payment DROP CONSTRAINT ck_payment_ck_payment_type;
        ALTER TABLE payment ADD CONSTRAINT ck_payment_ck_payment_type
          CHECK(payment_type IN ({PAYMENT_TYPES},'HK_VORAUSZAHLUNG'));
        ALTER TABLE payment ADD CONSTRAINT ck_payment_heating_counterparty
          CHECK(payment_type <> 'HK_VORAUSZAHLUNG' OR (tenant_profile_id IS NOT NULL AND owner_profile_id IS NULL));
        ALTER TABLE settlement_run ADD COLUMN heating_advance_payments_total NUMERIC(14,2) DEFAULT 0;
        ALTER TABLE settlement_run ALTER COLUMN heating_advance_payments_total DROP DEFAULT;
        ALTER TABLE settlement_run ADD COLUMN heating_advance_payments_source VARCHAR(12) NOT NULL DEFAULT 'MANUAL';
        ALTER TABLE settlement_run ADD CONSTRAINT ck_settlement_run_heating_source CHECK(heating_advance_payments_source IN ('MANUAL','DERIVED'));
        ALTER TABLE settlement_run ADD CONSTRAINT ck_settlement_run_heating_total CHECK(heating_advance_payments_total > '-Infinity'::numeric AND heating_advance_payments_total < 'Infinity'::numeric)""")
    patch_guards()


def downgrade():
    op.execute("""LOCK TABLE lease_agreement,lease_rent_change,payment,settlement_run IN ACCESS EXCLUSIVE MODE NOWAIT;
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM settlement_run
            WHERE status='finalized' AND snapshot_payload->>'engine_version'='m3.2.0') THEN
            RAISE EXCEPTION 'Retain finalized split-engine snapshots; repair forward' USING ERRCODE='23514';
          END IF;
          IF EXISTS(SELECT 1 FROM lease_agreement WHERE heating_advance_payment > 0)
            OR EXISTS(SELECT 1 FROM lease_rent_change WHERE heating_advance_payment > 0)
            OR EXISTS(SELECT 1 FROM payment WHERE payment_type='HK_VORAUSZAHLUNG')
            OR EXISTS(SELECT 1 FROM settlement_run WHERE heating_advance_payments_total <> 0) THEN
            RAISE EXCEPTION 'Retain heating advances and payments; repair forward' USING ERRCODE='23514';
          END IF;
        END $$""")
    patch_guards(reverse=True)
    op.execute(f"""ALTER TABLE payment DROP CONSTRAINT ck_payment_heating_counterparty;
        ALTER TABLE payment DROP CONSTRAINT ck_payment_ck_payment_type;
        ALTER TABLE payment ADD CONSTRAINT ck_payment_ck_payment_type CHECK(payment_type IN ({PAYMENT_TYPES}));
        ALTER TABLE settlement_run DROP COLUMN heating_advance_payments_total;
        ALTER TABLE settlement_run DROP COLUMN heating_advance_payments_source;
        ALTER TABLE lease_rent_change DROP COLUMN heating_advance_payment;
        ALTER TABLE lease_agreement DROP COLUMN heating_advance_payment""")
