"""Preserve covered corrections and released reserve-draft history."""
from alembic import op
from sqlalchemy import text

revision = '0173'
down_revision = '0172'
branch_labels = None
depends_on = None


REJECT_COVERED = """          RAISE EXCEPTION 'A covered cost correction cannot be rejected'
            USING ERRCODE='23514',CONSTRAINT='bank_cost_rejection'; END IF;"""

PATCHES = (
    ('guard_bank_cost_assignment()', (
        ('AND p.bank_transaction_id=NEW.id) THEN', 'AND p.bank_transaction_id=NEW.id AND p.cancelled_at IS NULL) THEN'),
    )),
    ('guard_payment_cost_bank()', (
        ('IF NEW.bank_transaction_id IS NOT NULL AND EXISTS',
         'IF NEW.cancelled_at IS NULL AND NEW.bank_transaction_id IS NOT NULL AND EXISTS'),
    )),
    ('bind_new_payment_to_account()', (
        ('      INSERT INTO public.accounting_scope_binding(', """      -- Restore the original binding of a released reserve draft when the
      -- same bank transaction has since been matched to a cost.
      IF NEW.cancelled_at IS NOT NULL AND NEW.payment_type='RUECKLAGENENTNAHME'
        AND EXISTS(SELECT 1 FROM public.bank_transaction b WHERE b.organization_id=NEW.organization_id
          AND b.id=NEW.bank_transaction_id AND b.bank_account_id=NEW.bank_account_id AND b.cost_entry_id IS NOT NULL)
        AND EXISTS(SELECT 1 FROM public.accounting_scope_binding b WHERE b.organization_id=NEW.organization_id
          AND b.property_id=NEW.property_id AND b.scope_id=sphere.id AND b.payment_id=NEW.id
          AND b.payment_bank_account_id=NEW.bank_account_id) THEN RETURN NEW; END IF;
      INSERT INTO public.accounting_scope_binding("""),
    )),
    ('guard_covered_cost_correction()', (
        ("AND NOT (NEW.status='confirmed' AND OLD.status<>'confirmed') THEN RETURN NEW; END IF;",
         """AND NOT (NEW.status IN ('confirmed','rejected') AND NEW.status<>OLD.status) THEN RETURN NEW; END IF;"""),
        ('        IF covered=0 THEN RETURN NEW; END IF;', """        IF covered=0 THEN RETURN NEW; END IF;
        IF NEW.status='rejected' THEN
""" + REJECT_COVERED),
    )),
    ('check_bank_cost_successors(uuid,uuid,uuid,uuid,numeric,uuid)', (
        ('          IF successor.gross_amount<public.bank_cost_covered(org,successor.id)+additional THEN',
         """          IF successor.status='rejected' THEN
""" + REJECT_COVERED + """
          IF successor.gross_amount<public.bank_cost_covered(org,successor.id)+additional THEN"""),
    )),
)


def _patch_functions(reverse=False):
    for signature, replacements in PATCHES:
        source = op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:sig AS regprocedure))'), {'sig': signature})
        for before, after in replacements:
            old, new = (after, before) if reverse else (before, after)
            if source.count(old) != 1:
                raise RuntimeError(f'Unexpected definition of {signature}; repair forward')
            source = source.replace(old, new, 1)
        op.execute(source)


def _payment_trigger(columns):
    op.execute(f"""DROP TRIGGER guard_payment_cost_bank ON payment;
      CREATE TRIGGER guard_payment_cost_bank BEFORE INSERT OR UPDATE OF {columns} ON payment
        FOR EACH ROW EXECUTE FUNCTION guard_payment_cost_bank();""")


def upgrade():
    op.execute('LOCK TABLE cost_entry,bank_transaction,payment IN ACCESS EXCLUSIVE MODE NOWAIT')
    if op.get_bind().scalar(text("""SELECT EXISTS(
        SELECT 1 FROM (SELECT DISTINCT organization_id,cost_entry_id FROM bank_transaction
            WHERE cost_entry_id IS NOT NULL) b
        CROSS JOIN LATERAL public.bank_cost_versions(b.organization_id,b.cost_entry_id,false) v
        JOIN cost_entry c ON c.organization_id=b.organization_id AND c.id=v.id
        WHERE c.status='rejected')""")):
        raise RuntimeError('Retain rejected bank-covered corrections; repair forward')
    _patch_functions()
    _payment_trigger('bank_transaction_id,cancelled_at')


def downgrade():
    op.execute('LOCK TABLE cost_entry,bank_transaction,payment IN ACCESS EXCLUSIVE MODE NOWAIT')
    _payment_trigger('bank_transaction_id')
    _patch_functions(reverse=True)
