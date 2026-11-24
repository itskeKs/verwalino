"""A booked Storno reverses an original that is no Storno itself (issue #912).

Revision 0208 (issue #721) made the database require of a booked payment that names an original (``reverses_id``) that
the original is another booked payment of the same organization, with the opposite direction and the same value in every
column ``PaymentService.reverse_payment`` copies. It did not ask that the original is not a Storno itself. As the runtime
role: book O, book a mirrored Storno S of O and link it (``O.reversed_by_id = S``), then book SS, which mirrors S (so it
has O's direction and O's amount), and set ``S.reversed_by_id = SS``; every statement is accepted. The books then show O
as reversed while the three rows sum to O's amount, so the reversal no longer neutralises: a wrong amount in the books.
The application cannot write it: ``reverse_payment`` answers 409 for a Storno, and the organization import requires an
original without ``reverses_id`` (``payment_reversal_pairs``); it takes raw SQL.

One rule, one revision: the original that a booked payment names through ``reverses_id`` has ``reverses_id`` NULL. It is
one more condition of the lookup the mirror rule of 0208 already makes inside ``trg_payment_immutability()``, so it holds
at the same two moments, the INSERT as booked and the UPDATE of a draft to booked, with the same refusal (SQLSTATE
P0001, "GoBD: a booked Storno must mirror the payment it reverses"). A draft SS may exist and is refused when it is
booked. The original is booked, so its ``reverses_id`` is frozen and the answer cannot change after the check: no second
statement and no index are needed. The text of the function is otherwise the text of 0208, so ``downgrade()`` restores
exactly what 0208 leaves, and the drift guard that reads the compared columns from the function source still holds.

Existing rows are not changed and not re-checked by the function (booked rows cannot be edited). The upgrade counts the
booked payments whose original is a booked payment that names an original itself, and refuses, naming only that number;
the error carries the query that lists them as its hint, not their ids or amounts. Such rows are booked evidence that
cannot be told apart from damage, so they are not repaired here: the application never writes them, so they come from
direct SQL, and the operator decides with the owner of the data what the books say before retrying the upgrade. Rows
that do not mirror their original are not looked for: that was 0208's rule, which does not re-check existing rows.

No trigger is created, dropped or changed, in particular no DELETE trigger, so the retention catalog of 0147 (which
recognises ``trg_payment_immutability()`` by name) sees the same table. The function keeps its owner, definer rights,
``search_path``, ``row_security = off`` and its ACL (executable by its owner only, 0192): ``CREATE OR REPLACE`` changes
none of them. No table, column or index changes.

``payment`` is locked ``ACCESS EXCLUSIVE ... NOWAIT`` first, as the revisions that change a table do, so contention
aborts the upgrade instead of queueing in front of the writers. No data can be lost: ``downgrade()`` restores the function
of 0208 exactly.

Revision ID: 0209
Revises: 0208
Create Date: 2026-11-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0209"
down_revision: Union[str, None] = "0208"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The function as 0208 created it: restored verbatim by downgrade().
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
    -- The same rule holds for every way a row becomes booked, not only for the
    -- later UPDATE of a booked row: a row that is booked after the statement
    -- carries reversed_by_id NULL or the id of a booked payment of the same
    -- organization whose reverses_id is this row's id. It is checked
    --   - on INSERT with status 'booked',
    --   - on UPDATE of a draft to 'booked' (also when the same UPDATE sets the link),
    --   - on UPDATE of a booked row from NULL to a link.
    -- A draft may carry any link its foreign keys allow until it is booked. Since
    -- the Storno rule below, a row cannot become booked with a link at all: its
    -- Storno would have to be booked before it, and a booked Storno needs a booked
    -- original. The link rule stays as the second guard.
    -- Each rule is written once, after the three entries have decided that it applies.
    --
    -- A booked payment that names an original (reverses_id) is that original's Storno, and it must mirror it
    -- (issue #721): the original is another booked payment of the same organization, the Storno has the opposite
    -- direction and the same value in every column PaymentService.reverse_payment copies
    -- (REVERSAL_COPIED_COLUMNS in backend/app/services/payment_service.py), compared with IS NOT DISTINCT FROM.
    -- The mandate is the one exception: the original has none, or the same one. The database derives the mandate
    -- from the account's scope when a payment is inserted, so a Storno booked after the account was classified
    -- carries a mandate that an older original lacks; a mandate the original has is never changed
    -- (the pairs the organization import accepts, payment_reversal_pairs). The original must be booked: a draft
    -- can still be edited, so a comparison with it would not hold. It is checked
    --   - on INSERT with status 'booked',
    --   - on UPDATE of a draft to 'booked'.
    -- A draft Storno is not checked until it is booked, and a booked row is not checked again: reverses_id and the
    -- columns it is compared in are frozen. Rows that were booked before this rule are not re-checked.
    -- That a booked payment is the only one that names an original is the unique index
    -- uq_payment_booked_storno, not this function.
    --
    -- SECURITY DEFINER with row_security off: the Storno lookup must not depend on
    -- what the acting role may read. It compares the organization of both rows
    -- itself, so a payment of another organization never qualifies.
    DECLARE
        check_link boolean := false;
        check_mirror boolean := false;
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.status = 'booked' THEN
                RAISE EXCEPTION 'GoBD: DELETE of booked payment is forbidden (id=%)', OLD.id;
            END IF;
            -- drafts: DELETE allowed (Erfassungs-Korrektur / discard)
            RETURN OLD;
        END IF;

        IF TG_OP = 'INSERT' THEN
            -- inserted as booked: the Storno and the link are checked below
            IF NEW.status = 'booked' THEN
                check_link := true;
                check_mirror := true;
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
            -- UPDATE of a draft to booked: the Storno and the link are checked below
            check_link := true;
            check_mirror := true;
        END IF;

        IF check_mirror AND NEW.reverses_id IS NOT NULL THEN
            IF NOT EXISTS (
                SELECT 1 FROM public.payment original
                WHERE original.organization_id = NEW.organization_id
                  AND original.id = NEW.reverses_id
                  AND original.id <> NEW.id
                  AND original.status = 'booked'
                  AND original.direction IS DISTINCT FROM NEW.direction
                  AND (original.management_mandate_id IS NULL
                       OR original.management_mandate_id = NEW.management_mandate_id)
                  AND (original.bank_account_id, original.property_id, original.amount, original.value_date,
                       original.payment_type, original.tenant_profile_id, original.owner_profile_id,
                       original.lease_agreement_id, original.period_year, original.period_month,
                       original.settlement_run_id, original.reserve_cost_entry_id,
                       original.reserve_register_entry_id)
                      IS NOT DISTINCT FROM
                      (NEW.bank_account_id, NEW.property_id, NEW.amount, NEW.value_date,
                       NEW.payment_type, NEW.tenant_profile_id, NEW.owner_profile_id,
                       NEW.lease_agreement_id, NEW.period_year, NEW.period_month,
                       NEW.settlement_run_id, NEW.reserve_cost_entry_id,
                       NEW.reserve_register_entry_id)
            ) THEN
                RAISE EXCEPTION 'GoBD: a booked Storno must mirror the payment it reverses (id=%)', NEW.id;
            END IF;
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
    -- A draft may carry any link its foreign keys allow until it is booked. Since
    -- the Storno rule below, a row cannot become booked with a link at all: its
    -- Storno would have to be booked before it, and a booked Storno needs a booked
    -- original. The link rule stays as the second guard.
    -- Each rule is written once, after the three entries have decided that it applies.
    --
    -- A booked payment that names an original (reverses_id) is that original's Storno, and it must mirror it
    -- (issue #721): the original is another booked payment of the same organization, the Storno has the opposite
    -- direction and the same value in every column PaymentService.reverse_payment copies
    -- (REVERSAL_COPIED_COLUMNS in backend/app/services/payment_service.py), compared with IS NOT DISTINCT FROM.
    -- The mandate is the one exception: the original has none, or the same one. The database derives the mandate
    -- from the account's scope when a payment is inserted, so a Storno booked after the account was classified
    -- carries a mandate that an older original lacks; a mandate the original has is never changed
    -- (the pairs the organization import accepts, payment_reversal_pairs). The original must be booked: a draft
    -- can still be edited, so a comparison with it would not hold. It is no Storno itself (its reverses_id is NULL,
    -- issue #912): a Storno of a Storno mirrors the Storno, so it has the direction of the first original, the books
    -- show that original as reversed while the three rows sum to its amount, and the application never writes one.
    -- It is checked
    --   - on INSERT with status 'booked',
    --   - on UPDATE of a draft to 'booked'.
    -- A draft Storno is not checked until it is booked, and a booked row is not checked again: reverses_id and the
    -- columns it is compared in are frozen. Rows that were booked before this rule are not re-checked.
    -- That a booked payment is the only one that names an original is the unique index
    -- uq_payment_booked_storno, not this function.
    --
    -- SECURITY DEFINER with row_security off: the Storno lookup must not depend on
    -- what the acting role may read. It compares the organization of both rows
    -- itself, so a payment of another organization never qualifies.
    DECLARE
        check_link boolean := false;
        check_mirror boolean := false;
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.status = 'booked' THEN
                RAISE EXCEPTION 'GoBD: DELETE of booked payment is forbidden (id=%)', OLD.id;
            END IF;
            -- drafts: DELETE allowed (Erfassungs-Korrektur / discard)
            RETURN OLD;
        END IF;

        IF TG_OP = 'INSERT' THEN
            -- inserted as booked: the Storno and the link are checked below
            IF NEW.status = 'booked' THEN
                check_link := true;
                check_mirror := true;
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
            -- UPDATE of a draft to booked: the Storno and the link are checked below
            check_link := true;
            check_mirror := true;
        END IF;

        IF check_mirror AND NEW.reverses_id IS NOT NULL THEN
            IF NOT EXISTS (
                SELECT 1 FROM public.payment original
                WHERE original.organization_id = NEW.organization_id
                  AND original.id = NEW.reverses_id
                  AND original.id <> NEW.id
                  AND original.status = 'booked'
                  AND original.reverses_id IS NULL
                  AND original.direction IS DISTINCT FROM NEW.direction
                  AND (original.management_mandate_id IS NULL
                       OR original.management_mandate_id = NEW.management_mandate_id)
                  AND (original.bank_account_id, original.property_id, original.amount, original.value_date,
                       original.payment_type, original.tenant_profile_id, original.owner_profile_id,
                       original.lease_agreement_id, original.period_year, original.period_month,
                       original.settlement_run_id, original.reserve_cost_entry_id,
                       original.reserve_register_entry_id)
                      IS NOT DISTINCT FROM
                      (NEW.bank_account_id, NEW.property_id, NEW.amount, NEW.value_date,
                       NEW.payment_type, NEW.tenant_profile_id, NEW.owner_profile_id,
                       NEW.lease_agreement_id, NEW.period_year, NEW.period_month,
                       NEW.settlement_run_id, NEW.reserve_cost_entry_id,
                       NEW.reserve_register_entry_id)
            ) THEN
                RAISE EXCEPTION 'GoBD: a booked Storno must mirror the payment it reverses (id=%)', NEW.id;
            END IF;
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

# Count only, no ids and no amounts: the hint names the query that lists the rows.
PREFLIGHT = """
    DO $$ DECLARE offending bigint; BEGIN
      SELECT count(*) INTO offending FROM public.payment p
        WHERE p.status = 'booked' AND p.reverses_id IS NOT NULL
          AND EXISTS (SELECT 1 FROM public.payment o
                      WHERE o.organization_id = p.organization_id AND o.id = p.reverses_id
                        AND o.status = 'booked' AND o.reverses_id IS NOT NULL);
      IF offending > 0 THEN
        RAISE EXCEPTION 'Cannot upgrade 0209: % booked payment(s) name an original that is itself a Storno; repair the existing data before retrying', offending
          USING HINT = 'List them with: SELECT p.organization_id, p.id, p.reverses_id FROM public.payment p WHERE p.status = ''booked'' AND p.reverses_id IS NOT NULL AND EXISTS (SELECT 1 FROM public.payment o WHERE o.organization_id = p.organization_id AND o.id = p.reverses_id AND o.status = ''booked'' AND o.reverses_id IS NOT NULL)';
      END IF;
    END $$
"""


def _lock_payment() -> None:
    op.execute("LOCK TABLE public.payment IN ACCESS EXCLUSIVE MODE NOWAIT")


def upgrade() -> None:
    # Fail rather than inspect an RLS-filtered subset of the existing rows.
    op.execute("SET LOCAL row_security = off")
    _lock_payment()
    op.execute(PREFLIGHT)
    op.execute(CURRENT_FUNCTION)


def downgrade() -> None:
    _lock_payment()
    op.execute(PREVIOUS_FUNCTION)
