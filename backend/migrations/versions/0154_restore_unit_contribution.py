"""Allow reasoned restoration within the original and booked contribution caps."""
from alembic import op
from sqlalchemy import text

revision = '0154'
down_revision = '0153'
branch_labels = None
depends_on = None


def replace_guard(reverse=False):
    definition = op.get_bind().scalar(text(
        "SELECT pg_get_functiondef('public.guard_reserve_ledger()'::regprocedure)"))
    changes = [
        ('allowed:=LEAST(remaining,', 'allowed:=LEAST(original.amount,'),
        ('IF NEW.amount>0 OR (target>0 AND target>allowed) THEN', 'IF target>0 AND target>allowed THEN'),
        ('IF NEW.amount=0 OR remaining<=0 OR target<0 THEN', """IF remaining=0 AND target=0 THEN
          RAISE EXCEPTION 'Reserve correction has no remaining amount'
            USING ERRCODE='23514',CONSTRAINT='reserve_correction_already_zero'; END IF;
        IF NEW.amount=0 OR target<0 THEN"""),
        ('Reserve reduction must lower the remaining amount to at least zero',
         'Reserve correction must change the remaining amount and keep it nonnegative'),
    ]
    for before, after in changes:
        if reverse:
            before, after = after, before
        assert definition.count(before) == 1
        definition = definition.replace(before, after)
    op.execute(definition)


def upgrade():
    replace_guard()


def downgrade():
    if op.get_bind().scalar(text("""SELECT EXISTS(SELECT 1 FROM reserve_ledger_entry
        WHERE reverses_entry_id IS NOT NULL AND amount>0)""")):
        raise RuntimeError('Cannot downgrade while restored reserve contribution counter-entries exist')
    replace_guard(reverse=True)
