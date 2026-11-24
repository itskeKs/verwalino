"""The reversal links of a payment carry the organization in their key (issue #718).

``payment.reverses_id`` (on a Storno, the original it reverses) and ``payment.reversed_by_id`` (on the original, its Storno)
were plain self references to ``payment(id)`` since 0016. Foreign key checks run without row-level security, so a statement of
``app_rw`` under organization B could insert a payment whose ``reverses_id`` is the id of a payment of organization A: the
insert succeeded when the id existed and failed with a foreign key violation when it did not, an existence probe for foreign
payment ids. The application never writes such a row (``reverse_payment`` copies from an original it loaded under row-level
security), but the database accepted it, while the other references of a payment carry the organization in their key
(``fk_payment_tenant_profile_id_tenant`` and the like, 0140, ADR-0010).

This revision replaces both keys with composite keys ``(organization_id, reverses_id)`` and ``(organization_id,
reversed_by_id)`` to ``payment(organization_id, id)``, the unique key ``uq_reserve_payment_org`` of 0149. The new keys keep
every option the simple keys have: ``ON DELETE RESTRICT`` and not deferrable. They are ``MATCH SIMPLE``, the default: a key
with a NULL column is not checked, and ``organization_id`` is NOT NULL, so a payment without a reversal link (the NULL
``reverses_id`` of every original, the NULL ``reversed_by_id`` of every payment nobody reversed) stays valid exactly as before.
``MATCH FULL`` would refuse it. A booked payment and its Storno name each other, so neither can be inserted with both links;
the order the writers already use stays the one that works without a deferred key: the original is inserted without
``reversed_by_id``, the Storno with ``reverses_id`` after it, then ``reversed_by_id`` is set on the original (the one change
the immutability trigger allows on a booked payment).

The upgrade refuses, naming the number of rows, when an existing row references a payment of another organization: such a row
is evidence that cannot be told apart from damage, so it is not repaired here. The error carries the query that lists the rows
(their own ids and organizations) as its hint; the message itself names no id.

``payment`` is locked ``ACCESS EXCLUSIVE ... NOWAIT`` first, as the revisions that change a table do, so contention aborts the
upgrade instead of queueing in front of the writers. The keys are added validated, not ``NOT VALID`` followed by ``VALIDATE
CONSTRAINT``: the whole upgrade is one transaction, so the table lock lasts until it commits either way, and the preflight
and the validation read the table once each. No row is changed and no data can be lost: ``downgrade()`` restores the two
simple keys (their names and definition from 0016) and needs no data check, since every row that satisfies the composite
keys satisfies the simple ones.

Revision ID: 0206
Revises: 0205
Create Date: 2026-11-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0206"
down_revision: Union[str, None] = "0205"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (column, simple key of 0016, composite key of this revision); the composite name follows 0140 (``fk_<table>_<column>_tenant``).
LINKS = (
    ("reverses_id", "fk_payment_reverses_id_payment", "fk_payment_reverses_id_tenant"),
    ("reversed_by_id", "fk_payment_reversed_by_id_payment", "fk_payment_reversed_by_id_tenant"),
)


def upgrade() -> None:
    # Fail rather than inspect an RLS-filtered subset of the existing rows.
    op.execute("SET LOCAL row_security = off")
    # Deferred checks that earlier revisions of the same upgrade queued on payment would block the ALTER TABLE (as in 0140).
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute("LOCK TABLE public.payment IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute("""DO $$ DECLARE foreign_links bigint; BEGIN
      SELECT count(*) INTO foreign_links FROM public.payment p
        WHERE (p.reverses_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.payment q
                 WHERE q.organization_id = p.organization_id AND q.id = p.reverses_id))
           OR (p.reversed_by_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.payment q
                 WHERE q.organization_id = p.organization_id AND q.id = p.reversed_by_id));
      IF foreign_links > 0 THEN
        RAISE EXCEPTION 'Cannot upgrade 0206: % payment row(s) name a payment of another organization in reverses_id or reversed_by_id; repair the existing data before retrying', foreign_links
          USING HINT = 'List the rows with: SELECT p.id, p.organization_id FROM public.payment p WHERE (p.reverses_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.payment q WHERE q.organization_id = p.organization_id AND q.id = p.reverses_id)) OR (p.reversed_by_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.payment q WHERE q.organization_id = p.organization_id AND q.id = p.reversed_by_id))';
      END IF;
    END $$""")
    for column, simple, composite in LINKS:
        op.execute(f"ALTER TABLE public.payment DROP CONSTRAINT {simple}")
        op.execute(f"""ALTER TABLE public.payment ADD CONSTRAINT {composite}
          FOREIGN KEY (organization_id, {column}) REFERENCES public.payment (organization_id, id) ON DELETE RESTRICT""")


def downgrade() -> None:
    op.execute("LOCK TABLE public.payment IN ACCESS EXCLUSIVE MODE NOWAIT")
    for column, simple, composite in LINKS:
        op.execute(f"ALTER TABLE public.payment DROP CONSTRAINT {composite}")
        op.execute(f"""ALTER TABLE public.payment ADD CONSTRAINT {simple}
          FOREIGN KEY ({column}) REFERENCES public.payment (id) ON DELETE RESTRICT""")
