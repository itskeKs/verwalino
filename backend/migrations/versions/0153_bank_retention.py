"""Classify imported bank lines as retained dependencies, never voucher roots."""
from alembic import op
from sqlalchemy import text

revision = '0153'
down_revision = '0152'
branch_labels = None
depends_on = None


def catalog(reverse=False):
    definition = op.get_bind().scalar(text(
        "SELECT pg_get_functiondef('public.organization_retention_catalog()'::regprocedure)"))
    before = "WHEN entity IN ('cost_entry','payment','settlement_run','settlement_line',"
    after = "WHEN entity IN ('bank_transaction','cost_entry','payment','settlement_run','settlement_line',"
    # The existing FK closure follows every retained payment, including drafts
    # bound by immutable scope evidence, and propagates its actual deadline.
    # No bank line seeds an independent retention or person deadline.
    old_predicate = "WHEN disposition<>'conditional' THEN 'false'"
    new_predicate = old_predicate + "\n        WHEN entity='bank_transaction' THEN 'false'"
    if reverse:
        before, after = after, before
        old_predicate, new_predicate = new_predicate, old_predicate
    assert definition.count(before) == definition.count(old_predicate) == 1
    op.execute(definition.replace(before, after).replace(old_predicate, new_predicate))


def upgrade():
    catalog()


def downgrade():
    catalog(reverse=True)
