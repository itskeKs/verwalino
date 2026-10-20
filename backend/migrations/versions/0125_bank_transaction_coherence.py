"""Bind imported transactions and payment links to their organization/account.

Revision ID: 0125
Revises: 0124
"""
from alembic import op

revision = "0125"
down_revision = "0124"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_bank_account_org_id", "bank_account", ["organization_id", "id"])
    op.create_foreign_key(
        "fk_bank_transaction_account_org", "bank_transaction", "bank_account",
        ["organization_id", "bank_account_id"], ["organization_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_bank_transaction_org_account_id", "bank_transaction",
        ["organization_id", "bank_account_id", "id"],
    )
    op.drop_constraint("fk_payment_bank_transaction_id", "payment", type_="foreignkey")
    op.create_foreign_key(
        "fk_payment_bank_transaction_coherence", "payment", "bank_transaction",
        ["organization_id", "bank_account_id", "bank_transaction_id"],
        ["organization_id", "bank_account_id", "id"], ondelete="RESTRICT",
    )
    op.execute("""
        CREATE FUNCTION trg_payment_bank_transaction_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status = 'booked'
               AND NEW.bank_transaction_id IS DISTINCT FROM OLD.bank_transaction_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change bank_transaction_id on booked payment (id=%)', OLD.id;
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_payment_bank_transaction_immutable
        BEFORE UPDATE OF bank_transaction_id ON payment
        FOR EACH ROW EXECUTE FUNCTION trg_payment_bank_transaction_immutable();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_payment_bank_transaction_immutable ON payment")
    op.execute("DROP FUNCTION IF EXISTS trg_payment_bank_transaction_immutable()")
    op.drop_constraint("fk_payment_bank_transaction_coherence", "payment", type_="foreignkey")
    op.create_foreign_key(
        "fk_payment_bank_transaction_id", "payment", "bank_transaction",
        ["bank_transaction_id"], ["id"], ondelete="SET NULL",
    )
    op.drop_constraint("uq_bank_transaction_org_account_id", "bank_transaction", type_="unique")
    op.drop_constraint("fk_bank_transaction_account_org", "bank_transaction", type_="foreignkey")
    op.drop_constraint("uq_bank_account_org_id", "bank_account", type_="unique")
