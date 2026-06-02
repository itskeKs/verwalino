"""M6: payment immutability trigger (ADR-0003 / ADR-0009).

Status-aware BEFORE UPDATE/DELETE trigger on payment -- the DB-level enforcement
of "a booked payment is a frozen booking record". Same mechanic as 0008/0011/0015
(cost_entry / settlement_run / economic_plan); NOTHING new is invented -- ADR-0009
re-uses the ADR-0003 pattern with freeze condition status='booked'.

payment (freeze condition: OLD.status = 'booked'):
  - UPDATE of any content-bearing column on a booked payment -> RAISE EXCEPTION.
  - The ONLY allowed change on a booked payment is setting reversed_by_id
    (the Storno chain, section 5 -- the forward pointer to the Storno row) +
    updated_at.
  - DELETE of a booked payment -> always RAISE EXCEPTION.
  - draft payments are freely editable/deletable (pure Erfassungs-Korrektur).

payment keeps a full CRUD grant (editable draft); the trigger is the sole
status-dependent barrier (decision E3-5, section 1.6).

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-02
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION trg_payment_immutability()
    RETURNS TRIGGER AS $$
    -- GoBD immutability for payment (ADR-0003 / ADR-0009).
    --
    -- FREEZE CONDITION: OLD.status = 'booked'
    --
    -- Once a payment is booked, its content-bearing columns are frozen. The
    -- ONLY allowed mutation is setting reversed_by_id (linking the Storno row,
    -- section 5). Correction = Storno (new booked row) + Neubuchung; the booked
    -- original is NEVER edited.
    --
    -- Content-bearing columns (frozen once booked):
    --   organization_id, bank_account_id, property_id, management_mandate_id,
    --   direction, amount, value_date, tenant_profile_id, owner_profile_id,
    --   payment_type, lease_agreement_id, period_year, period_month,
    --   settlement_run_id, status (cannot move away from booked --
    --   correction = new row), booked_at, booked_by, reverses_id,
    --   retention_until, note
    --
    -- Allowed changes on a booked payment:
    --   reversed_by_id (NULL -> a valid UUID, the Storno chain forward pointer)
    --   updated_at (technical timestamp)
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.status = 'booked' THEN
                RAISE EXCEPTION 'GoBD: DELETE of booked payment is forbidden (id=%)', OLD.id;
            END IF;
            -- drafts: DELETE allowed (Erfassungs-Korrektur / discard)
            RETURN OLD;
        END IF;

        -- UPDATE path
        IF OLD.status = 'booked' THEN
            IF NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change organization_id on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.bank_account_id IS DISTINCT FROM OLD.bank_account_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change bank_account_id on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.property_id IS DISTINCT FROM OLD.property_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change property_id on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.management_mandate_id IS DISTINCT FROM OLD.management_mandate_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change management_mandate_id on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.direction IS DISTINCT FROM OLD.direction THEN
                RAISE EXCEPTION 'GoBD: Cannot change direction on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.amount IS DISTINCT FROM OLD.amount THEN
                RAISE EXCEPTION 'GoBD: Cannot change amount on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.value_date IS DISTINCT FROM OLD.value_date THEN
                RAISE EXCEPTION 'GoBD: Cannot change value_date on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.tenant_profile_id IS DISTINCT FROM OLD.tenant_profile_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change tenant_profile_id on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.owner_profile_id IS DISTINCT FROM OLD.owner_profile_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change owner_profile_id on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.payment_type IS DISTINCT FROM OLD.payment_type THEN
                RAISE EXCEPTION 'GoBD: Cannot change payment_type on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.lease_agreement_id IS DISTINCT FROM OLD.lease_agreement_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change lease_agreement_id on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.period_year IS DISTINCT FROM OLD.period_year THEN
                RAISE EXCEPTION 'GoBD: Cannot change period_year on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.period_month IS DISTINCT FROM OLD.period_month THEN
                RAISE EXCEPTION 'GoBD: Cannot change period_month on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.settlement_run_id IS DISTINCT FROM OLD.settlement_run_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change settlement_run_id on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'GoBD: Cannot change status on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.booked_at IS DISTINCT FROM OLD.booked_at THEN
                RAISE EXCEPTION 'GoBD: Cannot change booked_at on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.booked_by IS DISTINCT FROM OLD.booked_by THEN
                RAISE EXCEPTION 'GoBD: Cannot change booked_by on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.reverses_id IS DISTINCT FROM OLD.reverses_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change reverses_id on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.retention_until IS DISTINCT FROM OLD.retention_until THEN
                RAISE EXCEPTION 'GoBD: Cannot change retention_until on booked payment (id=%)', OLD.id;
            END IF;
            IF NEW.note IS DISTINCT FROM OLD.note THEN
                RAISE EXCEPTION 'GoBD: Cannot change note on booked payment (id=%)', OLD.id;
            END IF;
            -- reversed_by_id: this IS the allowed change (Storno chain)
            -- updated_at: technical timestamp, allowed
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER trg_payment_immutability
        BEFORE UPDATE OR DELETE ON payment
        FOR EACH ROW
        EXECUTE FUNCTION trg_payment_immutability();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_payment_immutability ON payment")
    op.execute("DROP FUNCTION IF EXISTS trg_payment_immutability()")
