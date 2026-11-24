"""A booked Storno mirrors the payment it reverses, and only one booked payment reverses a payment (issue #721).

``PaymentService.reverse_payment`` always writes a Storno that equals its original in the columns it copies
(``REVERSAL_COPIED_COLUMNS``) with the opposite direction, and the organization import accepts an archived pair only
when it is such a pair (``payment_reversal_pairs``, issue #687). The database checked neither: as the runtime role a
booked payment in the direction of its original, for another amount, was accepted as the Storno of a booked payment
and linked through ``reversed_by_id``, and a second booked payment naming the same original stayed booked. A reversal
that does not neutralise its original is a wrong amount in the books.

Two rules, one revision:

* The Storno rule is part of ``trg_payment_immutability()``, which 0207 already fires on INSERT and on UPDATE. A row
  that is booked after the statement and names an original (``reverses_id``) needs a row of the same organization that
  is booked, is another row, has the opposite direction and equals it in every copied column (``IS NOT DISTINCT FROM``,
  so NULL equals NULL and a NULL against a value is a difference). It is checked at the two moments 0207 checks the
  link: an INSERT with status ``booked`` and the UPDATE of a draft to ``booked``. A draft Storno is not checked until
  it is booked. A booked row is not checked again, since ``reverses_id`` and every compared column are frozen on it.
  The refusal is SQLSTATE P0001, "GoBD: a booked Storno must mirror the payment it reverses".
* ``uq_payment_booked_storno`` is a unique index on ``(organization_id, reverses_id)`` for booked rows that name an
  original. An index, not a check in the trigger, because two transactions that book a Storno of the same original at
  once cannot see each other's rows. A draft is outside the index, so a draft Storno may exist beside a booked one and
  is refused (SQLSTATE 23505) only when it is booked.

The mandate is the one column compared with an exception, as in the import: the original has no mandate, or the same
one. The database derives the mandate of a payment from the scope of its account when the payment is inserted
(``bind_new_payment_to_account``), so a Storno booked after the account was classified carries a mandate that an older
original lacks, and the same trigger refuses a Storno whose mandate differs from the account's, so a mandate the
original has is copied unchanged. Both writers produce nothing else, and the rule accepts nothing else: an original
with a mandate and a Storno with another one or none is refused. Where an account has no scope binding yet (legacy,
until activation) nothing derives a mandate, so a Storno could gain one there that no scope gave it; the import accepts
the same pair, and so does this rule.

The original must be booked. A booked payment is frozen, so the comparison holds for good; a draft original could be
edited after the Storno was booked and break the mirror again. ``reverse_payment`` and the import book the original
first, so the rule costs them nothing. What follows from it: the INSERT of a Storno ahead of its original in one
statement, which 0207 let through in order to keep a link rule reachable, is refused now, and with it every way in which
a payment could become booked while its Storno is already booked. The link rule of 0192 and 0207 stays as it is.

Existing rows are not changed and not re-checked by the function (booked rows cannot be edited). The index cannot be
built over booked payments that name one original more than once, so the upgrade counts them first and refuses, naming
only the number of rows. The error carries the query that lists the originals as its hint, not the ids. Such rows are
booked evidence that cannot be told apart from damage, so they are not repaired here: the application never writes
them (a second reversal answers 409, the import validates its pairs), and the operator decides with the owner of the
data what the books say before retrying the upgrade.

No trigger is created, dropped or changed, in particular no DELETE trigger, so the retention catalog of 0147 (which
recognises ``trg_payment_immutability()`` by name) sees the same table. The function keeps its owner, definer rights,
``search_path``, ``row_security = off`` and its ACL (executable by its owner only, 0192): ``CREATE OR REPLACE`` changes
none of them.

``payment`` is locked ``ACCESS EXCLUSIVE ... NOWAIT`` first, as the revisions that change a table do, so contention
aborts the upgrade instead of queueing in front of the writers; the deferred checks that earlier revisions of the same
upgrade queued on ``payment`` are run first (they would block the index build, as in 0206 and 0207). No data can be lost:
``downgrade()`` drops the index and restores the function of 0207 exactly.

Revision ID: 0208
Revises: 0207
Create Date: 2026-11-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0208"
down_revision: Union[str, None] = "0207"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The function as 0207 created it: restored verbatim by downgrade().
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

CREATE_INDEX = """
    CREATE UNIQUE INDEX uq_payment_booked_storno
        ON public.payment (organization_id, reverses_id)
        WHERE status = 'booked' AND reverses_id IS NOT NULL
"""

# Count only, no ids and no amounts: the hint names the query that lists the originals.
PREFLIGHT = """
    DO $$ DECLARE sharing bigint; BEGIN
      SELECT count(*) INTO sharing FROM public.payment p
        WHERE p.status = 'booked' AND p.reverses_id IS NOT NULL
          AND EXISTS (SELECT 1 FROM public.payment q
                      WHERE q.organization_id = p.organization_id AND q.reverses_id = p.reverses_id
                        AND q.status = 'booked' AND q.id <> p.id);
      IF sharing > 0 THEN
        RAISE EXCEPTION 'Cannot upgrade 0208: % booked payment(s) name an original that another booked payment also names; repair the existing data before retrying', sharing
          USING HINT = 'List the originals with: SELECT p.organization_id, p.reverses_id, count(*) FROM public.payment p WHERE p.status = ''booked'' AND p.reverses_id IS NOT NULL GROUP BY p.organization_id, p.reverses_id HAVING count(*) > 1';
      END IF;
    END $$
"""


def _lock_payment() -> None:
    # Deferred checks queued on payment by earlier revisions of the same upgrade would block the index statements.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute("LOCK TABLE public.payment IN ACCESS EXCLUSIVE MODE NOWAIT")


def upgrade() -> None:
    # Fail rather than inspect an RLS-filtered subset of the existing rows.
    op.execute("SET LOCAL row_security = off")
    _lock_payment()
    op.execute(PREFLIGHT)
    op.execute(CURRENT_FUNCTION)
    op.execute(CREATE_INDEX)


def downgrade() -> None:
    _lock_payment()
    op.execute("DROP INDEX public.uq_payment_booked_storno")
    op.execute(PREVIOUS_FUNCTION)
