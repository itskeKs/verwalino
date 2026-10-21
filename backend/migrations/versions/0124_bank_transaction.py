"""T15: bank_transaction table -- imported bank statement lines.

T15: Bankumsätze Tabelle.
Columns from F2 CSV/CAMT formats: account (FK to bank_account),
booking_date, value_date, amount, counterparty_name, counterparty_iban,
purpose, import_hash (unique per account), status (NEU/ZUGEORDNET/IGNORIERT).
Tenant-behaftet (organization_id, RLS).

Optional link to payment via payment.bank_transaction_id (set when transaction is assigned).

Revision ID: 0124
Revises: 0123
Create Date: 2026-10-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0124"
down_revision: Union[str, None] = "0123"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Controlled status vocabulary for bank transactions.
BANK_TRANSACTION_STATUSES = ("NEU", "ZUGEORDNET", "IGNORIERT")


def _in_check(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # ======================================================================
    # bank_transaction (tenant-behaftet)
    # ======================================================================
    op.create_table(
        "bank_transaction",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("bank_account_id", UUID(as_uuid=True),
                  sa.ForeignKey("bank_account.id", ondelete="RESTRICT"), nullable=False),
        # Booking and value dates.
        sa.Column("booking_date", sa.Date(), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=False),
        # Amount (always positive; sign from purpose/counterparty context).
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        # Counterparty information.
        sa.Column("counterparty_name", sa.String(255), nullable=True),
        sa.Column("counterparty_iban", sa.String(34), nullable=True),
        # Purpose/reference text.
        sa.Column("purpose", sa.String(500), nullable=True),
        # Import hash for idempotent imports (unique per account).
        sa.Column("import_hash", sa.String(64), nullable=False),
        # Status: NEU (new), ZUGEORDNET (assigned to payment), IGNORIERT (ignored).
        sa.Column("status", sa.String(12), nullable=False, server_default=sa.text("'NEU'")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        # --- CHECK constraints ---
        sa.CheckConstraint(f"status IN ({_in_check(BANK_TRANSACTION_STATUSES)})", name="ck_bank_transaction_status"),
    )

    # --- Indices ---
    op.create_index("ix_bank_transaction_organization_id", "bank_transaction", ["organization_id"])
    op.create_index("ix_bank_transaction_bank_account_id", "bank_transaction", ["bank_account_id"])
    op.create_index("ix_bank_transaction_import_hash", "bank_transaction", ["import_hash"])
    op.create_index("ix_bank_transaction_value_date", "bank_transaction", ["value_date"])

    # --- Unique constraint: import_hash per account prevents duplicate imports. ---
    op.create_unique_constraint(
        "uq_bank_transaction_account_import",
        "bank_transaction",
        ["bank_account_id", "import_hash"],
    )

    # --- Grants (full CRUD for app_rw) ---
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON bank_transaction TO app_rw")

    # ======================================================================
    # payment: add bank_transaction_id (T15)
    # ======================================================================
    op.add_column("payment", sa.Column("bank_transaction_id", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_payment_bank_transaction_id", "payment", ["bank_transaction_id"])
    op.create_foreign_key(
        "fk_payment_bank_transaction_id",
        "payment",
        "bank_transaction",
        ["bank_transaction_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- Row Level Security (T15 requirement) ---
    op.execute("ALTER TABLE bank_transaction ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE bank_transaction FORCE ROW LEVEL SECURITY")

    # Policy: SELECT only rows belonging to the user's organization.
    op.execute("""
        CREATE POLICY bank_transaction_select_policy ON bank_transaction
        FOR SELECT
        USING (organization_id = current_setting('app.current_org_id')::uuid)
    """)

    # Policy: INSERT only rows belonging to the user's organization.
    op.execute("""
        CREATE POLICY bank_transaction_insert_policy ON bank_transaction
        FOR INSERT
        WITH CHECK (organization_id = current_setting('app.current_org_id')::uuid)
    """)

    # Policy: UPDATE only rows belonging to the user's organization.
    op.execute("""
        CREATE POLICY bank_transaction_update_policy ON bank_transaction
        FOR UPDATE
        USING (organization_id = current_setting('app.current_org_id')::uuid)
        WITH CHECK (organization_id = current_setting('app.current_org_id')::uuid)
    """)

    # Policy: DELETE only rows belonging to the user's organization.
    op.execute("""
        CREATE POLICY bank_transaction_delete_policy ON bank_transaction
        FOR DELETE
        USING (organization_id = current_setting('app.current_org_id')::uuid)
    """)


def downgrade() -> None:
    # ======================================================================
    # payment: remove bank_transaction_id (T15 downgrade) — must happen BEFORE
    # dropping bank_transaction table because of the FK constraint.
    # ======================================================================
    op.execute("ALTER TABLE payment DROP CONSTRAINT IF EXISTS fk_payment_bank_transaction_id")
    op.execute("DROP INDEX IF EXISTS ix_payment_bank_transaction_id")
    # Use raw SQL for IF EXISTS on drop_column (alembic.op.drop_column has no IF EXISTS).
    op.execute("ALTER TABLE payment DROP COLUMN IF EXISTS bank_transaction_id")

    # Drop policies via raw SQL (alembic.op has no drop_policy).
    op.execute("DROP POLICY IF EXISTS bank_transaction_org_policy ON bank_transaction")
    op.execute("DROP POLICY IF EXISTS bank_transaction_insert_policy ON bank_transaction")
    op.execute("DROP POLICY IF EXISTS bank_transaction_update_policy ON bank_transaction")
    op.execute("DROP POLICY IF EXISTS bank_transaction_delete_policy ON bank_transaction")
    # Disable RLS and drop table.
    op.execute("ALTER TABLE bank_transaction DISABLE ROW LEVEL SECURITY")
    op.drop_table("bank_transaction")
