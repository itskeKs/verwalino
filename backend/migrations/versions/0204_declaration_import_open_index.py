"""An index for the read of a user's open imports, the Meldungen of the application frame (issue #871).

``GET /api/meldungen`` runs on every navigation of every user. It reads the imports of one organization that one user
started and that have one of the statuses of an open import. ``declaration_import`` had indexes on its primary key,
``(organization_id, id)``, ``organization_id``, ``target_property_id`` and ``applied_property_id`` only, so the read took
every import of the organization and dropped the ended ones: its cost grew with every import an organization ever created.

``ix_declaration_import_org_initiator_status (organization_id, initiated_by, status)`` serves the equality filters on the
organization and the user and checks the ``status IN (...)`` list inside the index, so the caller's own ended imports are not
read either. It is not partial on purpose: which statuses are open is decided in one place (``MELDUNG_STATES`` in
``declarations/progress.py``), and a predicate would restate that list in SQL and fall behind it with the next status; a
partial index is also not usable by the generic plan that PostgreSQL may choose for a prepared statement. The read's order (the
start time of the import's queue request) comes from another table, which no index on this one can serve; what is left to
sort after the filter is the user's open imports, at most one answer's worth.

``CREATE INDEX`` takes a ``SHARE`` lock: writers of the table wait, readers do not, for the build of a table with one row per
import. The lock is requested with ``NOWAIT`` first, as the revisions that change a table do, so contention aborts the upgrade
instead of queueing in front of the workers. No revision here builds an index ``CONCURRENTLY``, which cannot run inside the
migration transaction. No row is read or changed and no data can be lost: ``downgrade()`` drops the index.

Revision ID: 0204
Revises: 0203
Create Date: 2026-11-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0204"
down_revision: Union[str, None] = "0203"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("LOCK TABLE public.declaration_import IN SHARE MODE NOWAIT")
    op.execute("CREATE INDEX ix_declaration_import_org_initiator_status ON public.declaration_import (organization_id, initiated_by, status)")


def downgrade() -> None:
    op.execute("DROP INDEX public.ix_declaration_import_org_initiator_status")
