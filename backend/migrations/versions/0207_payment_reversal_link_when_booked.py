"""Payment reversal link: checked on every way a payment becomes booked, not only on a booked row's UPDATE.

Issue #720. 0192 (#717) made the `reversed_by_id` of a booked payment change only from NULL to its real
Storno, but `trg_payment_immutability()` fired on UPDATE and DELETE only and applied the link rule only when
the old row was already booked. A booked row could still carry a forged link when the link was set before or
while the row became booked, and a booked row that looks reversed drops out of the DATEV export and the dunning
and answers 409 to a later reversal. As the runtime role under an organization these were accepted: inserting
a payment as booked with `reversed_by_id` naming an unrelated booked payment; setting the link on a draft, then
booking it; booking a draft and setting the link in the same UPDATE.

The rule is now one for every way a row becomes booked: a row that is booked after the statement carries
`reversed_by_id` NULL or the id of a booked payment of the same organization whose `reverses_id` is the row's
id. The function checks it at one place, after the three entries (INSERT as booked, UPDATE of a draft to booked,
UPDATE of a booked row from NULL to a link) have decided that it applies, with the message of 0192 (SQLSTATE
P0001, "GoBD: reversed_by_id of booked payment must name its booked Storno"). Re-pointing or clearing a set link
on a booked row keeps its own refusal; a draft may carry any link its keys allow until it is booked.

Because the keys of 0206 are not deferrable, the only non-NULL link an INSERT can legitimately carry is the one
of a statement that inserts the Storno ahead of its original: the Storno needs the original to exist (its
`reverses_id`), so in any other order the Storno is not there when the original's row is checked and the insert is
refused. The writers are unaffected: `PaymentService.reverse_payment` and the organization import insert the
original without a link and set it after the Storno exists, which is the NULL -> link change of a booked row.

The function needs an INSERT trigger to be called on INSERT. This revision adds a second trigger,
`trg_payment_immutability_insert` (BEFORE INSERT, same function), and leaves `trg_payment_immutability` (BEFORE
UPDATE OR DELETE) untouched: no DELETE trigger is created, dropped or changed, so the retention catalog of 0147
(which recognises the function by name) sees the same table. The function keeps its owner, definer rights,
`search_path`, `row_security = off` and its ACL (executable by its owner only, 0192): `CREATE OR REPLACE` changes
none of them.

`payment` is locked `ACCESS EXCLUSIVE ... NOWAIT` first, as the revisions that change a table do, so contention
aborts the upgrade instead of queueing in front of the writers; the deferred checks that earlier revisions of the
same upgrade queued on `payment` are run first (they would block the trigger statement, as in 0206). No row is
read or changed, existing rows are not re-checked (a booked row with a link made before this revision stays as
it is) and no data can be lost: `downgrade()` drops the INSERT trigger first, then restores the function of 0192
exactly.

Revision ID: 0207
Revises: 0206
Create Date: 2026-11-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0207"
down_revision: Union[str, None] = "0206"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The function as 0192 created it: restored verbatim by downgrade().
PREVIOUS_FUNCTION = """
    CREATE OR REPLACE FUNCTION trg_payment_immutability()
    RETURNS TRIGGER AS $$
    -- GoBD immutability for payment (ADR-0003 / ADR-0009).
    --
    -- FREEZE CONDITION: OLD.status = 'booked'
    --
    -- Once a payment is booked, its content-bearing columns are frozen. The
    -- ONLY allowed mutation is setting reversed_by_id (linking the Storno row,
    -- section 5) once. Correction = Storno (new booked row) + Neubuchung; the
    -- booked original is NEVER edited.
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
    --   reversed_by_id (NULL -> the id of a booked payment of the same
    --   organization whose reverses_id is this row's id: the Storno chain forward
    --   pointer. Once set it is never re-pointed or cleared.)
    --   updated_at (technical timestamp)
    --
    -- SECURITY DEFINER with row_security off: the Storno lookup must not depend on
    -- what the acting role may read. It compares the organization of both rows
    -- itself, so a payment of another organization never qualifies.
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
            -- reversed_by_id: the allowed change (Storno chain), only NULL -> the
            -- booked Storno of this payment in the same organization
            IF NEW.reversed_by_id IS DISTINCT FROM OLD.reversed_by_id THEN
                IF OLD.reversed_by_id IS NOT NULL THEN
                    RAISE EXCEPTION 'GoBD: Cannot change reversed_by_id on booked payment (id=%)', OLD.id;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM public.payment storno
                    WHERE storno.id = NEW.reversed_by_id
                      AND storno.id <> OLD.id
                      AND storno.organization_id = OLD.organization_id
                      AND storno.status = 'booked'
                      AND storno.reverses_id = OLD.id
                ) THEN
                    RAISE EXCEPTION 'GoBD: reversed_by_id of booked payment must name its booked Storno (id=%)', OLD.id;
                END IF;
            END IF;
            -- updated_at: technical timestamp, allowed
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp SET row_security = off;
"""

CURRENT_FUNCTION = """
    CREATE OR REPLACE FUNCTION trg_payment_immutability()
    RETURNS TRIGGER AS $$
    -- GoBD immutability for payment (ADR-0003 / ADR-0009).
    --
    -- FREEZE CONDITION: OLD.status = 'booked'
    --
    -- Once a payment is booked, its content-bearing columns are frozen. The
    -- ONLY allowed mutation is setting reversed_by_id (linking the Storno row,
    -- section 5) once. Correction = Storno (new booked row) + Neubuchung; the
    -- booked original is NEVER edited.
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
    --   reversed_by_id (NULL -> the id of a booked payment of the same
    --   organization whose reverses_id is this row's id: the Storno chain forward
    --   pointer. Once set it is never re-pointed or cleared.)
    --   updated_at (technical timestamp)
    --
    -- The same rule holds for every way a row becomes booked, not only for the
    -- later UPDATE of a booked row: a row that is booked after the statement
    -- carries reversed_by_id NULL or the id of a booked payment of the same
    -- organization whose reverses_id is this row's id. It is checked
    --   - on INSERT with status 'booked',
    --   - on UPDATE of a draft to 'booked' (also when the same UPDATE sets the link),
    --   - on UPDATE of a booked row from NULL to a link.
    -- A draft may carry any link its foreign keys allow until it is booked. An
    -- INSERT can name a Storno only when the same statement inserted that Storno
    -- ahead of this row, because the Storno needs the original to exist first.
    -- The rule is written once, at the end of the function.
    --
    -- SECURITY DEFINER with row_security off: the Storno lookup must not depend on
    -- what the acting role may read. It compares the organization of both rows
    -- itself, so a payment of another organization never qualifies.
    DECLARE
        check_link boolean := false;
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.status = 'booked' THEN
                RAISE EXCEPTION 'GoBD: DELETE of booked payment is forbidden (id=%)', OLD.id;
            END IF;
            -- drafts: DELETE allowed (Erfassungs-Korrektur / discard)
            RETURN OLD;
        END IF;

        IF TG_OP = 'INSERT' THEN
            -- inserted as booked: the link is checked below
            IF NEW.status = 'booked' THEN
                check_link := true;
            END IF;
        ELSIF OLD.status = 'booked' THEN
            -- UPDATE path of a booked row
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
            -- reversed_by_id: the allowed change (Storno chain), only NULL -> a link
            -- (checked below); once set it is never re-pointed or cleared
            IF NEW.reversed_by_id IS DISTINCT FROM OLD.reversed_by_id THEN
                IF OLD.reversed_by_id IS NOT NULL THEN
                    RAISE EXCEPTION 'GoBD: Cannot change reversed_by_id on booked payment (id=%)', OLD.id;
                END IF;
                check_link := true;
            END IF;
            -- updated_at: technical timestamp, allowed
        ELSIF NEW.status = 'booked' THEN
            -- UPDATE of a draft to booked: the link is checked below
            check_link := true;
        END IF;

        IF check_link AND NEW.reversed_by_id IS NOT NULL THEN
            IF NOT EXISTS (
                SELECT 1 FROM public.payment storno
                WHERE storno.id = NEW.reversed_by_id
                  AND storno.id <> NEW.id
                  AND storno.organization_id = NEW.organization_id
                  AND storno.status = 'booked'
                  AND storno.reverses_id = NEW.id
            ) THEN
                RAISE EXCEPTION 'GoBD: reversed_by_id of booked payment must name its booked Storno (id=%)', NEW.id;
            END IF;
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp SET row_security = off;
"""

CREATE_INSERT_TRIGGER = """
    CREATE TRIGGER trg_payment_immutability_insert
        BEFORE INSERT ON public.payment
        FOR EACH ROW
        EXECUTE FUNCTION public.trg_payment_immutability();
"""


def _lock_payment() -> None:
    # Deferred checks queued on payment by earlier revisions of the same upgrade would block the trigger statements.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute("LOCK TABLE public.payment IN ACCESS EXCLUSIVE MODE NOWAIT")


def upgrade() -> None:
    _lock_payment()
    # The function first: it handles INSERT before any trigger calls it so.
    op.execute(CURRENT_FUNCTION)
    op.execute(CREATE_INSERT_TRIGGER)


def downgrade() -> None:
    _lock_payment()
    # The trigger first: the function of 0192 reads OLD, which an INSERT does not have.
    op.execute("DROP TRIGGER trg_payment_immutability_insert ON public.payment")
    op.execute(PREVIOUS_FUNCTION)
