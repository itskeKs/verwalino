"""Tests that run the model-assisted invoice or inbox path leave no 0197 or 0198 data behind.

The 0197 and 0198 downgrades refuse to discard metering records and model-read proposals, and the migration-chain tests of
the suite downgrade through them on a database that earlier tests have used. The tests that make such rows remove them
before and after, with the owner's rights: the extraction records are immutable for the runtime, not for the owner. A
model-read inbox proposal is not deleted (assignment proposals and reviews may rest on it) but made a locally read one.
"""
from contextlib import closing

import psycopg2
import pytest

from tests.conftest import MIGRATOR_SYNC_URL

DISCARD = (
    "DELETE FROM public.invoice_model_call",
    "ALTER TABLE public.field_evidence DISABLE TRIGGER USER",
    "ALTER TABLE public.extraction_revision DISABLE TRIGGER USER",
    "DELETE FROM public.field_evidence WHERE extraction_revision_id IN"
    " (SELECT id FROM public.extraction_revision WHERE processor_version='invoice-model-v1')",
    "DELETE FROM public.extraction_revision WHERE processor_version='invoice-model-v1'",
    "UPDATE public.extraction_revision SET processor_version='intake-local-v1' WHERE processor_version='intake-model-v1'",
    # The location a model stated on a page it read is no location of a locally read proposal.
    "UPDATE public.field_evidence SET locator_status='unlocated', page_number=NULL, quotation=NULL, xml_path=NULL, method='unlocated' WHERE method='reading_model'",
    "ALTER TABLE public.extraction_revision ENABLE TRIGGER USER",
    "ALTER TABLE public.field_evidence ENABLE TRIGGER USER",
    "UPDATE public.invoice_processing_attempt SET processor_version='invoice-local-v1' WHERE processor_version='invoice-model-v1'",
)


def discard_invoice_model_data(execute) -> None:
    """Run the statements through ``execute`` (a cursor's or a connection's), in the caller's transaction."""
    for statement in DISCARD:
        execute(statement)


def discard_now() -> None:
    """Discard the data in its own transaction (a module-level fixture's use of the function above)."""
    with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection:
        with connection, connection.cursor() as cursor:
            discard_invoice_model_data(cursor.execute)


@pytest.fixture
def no_invoice_model_data():
    discard_now()
    yield
    discard_now()
