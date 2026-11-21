"""Tests that run the model-assisted invoice path leave no 0197 data behind.

The 0197 downgrade refuses to discard metering records and model-read proposals, and the migration-chain tests of the
suite downgrade through it on a database that earlier tests have used. The tests that make such rows remove them
before and after, with the owner's rights: the extraction records are immutable for the runtime, not for the owner.
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
    "ALTER TABLE public.extraction_revision ENABLE TRIGGER USER",
    "ALTER TABLE public.field_evidence ENABLE TRIGGER USER",
    "UPDATE public.invoice_processing_attempt SET processor_version='invoice-local-v1' WHERE processor_version='invoice-model-v1'",
)


def discard_invoice_model_data(execute) -> None:
    """Run the statements through ``execute`` (a cursor's or a connection's), in the caller's transaction."""
    for statement in DISCARD:
        execute(statement)


@pytest.fixture
def no_invoice_model_data():
    def discard():
        with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection:
            with connection, connection.cursor() as cursor:
                discard_invoice_model_data(cursor.execute)

    discard()
    yield
    discard()
