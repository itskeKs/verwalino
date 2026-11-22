"""Erasing an organization whose inbox documents were read by the models (issue #501, review of PR #832).

The metering rows of the inbox (``invoice_model_call.intake_attempt_id``, migration 0198) reference the attempts of the
extraction queue with ON DELETE RESTRICT, so the erasure has to remove them before the attempts; its order follows the
foreign keys.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.intake_extraction import extract_logical
from app.declarations.fake_provider import FakeInvoiceExtractionProvider, synthetic_invoice_output
from app.declarations.schemas import ReadingBlock
from app.services.organization_deletion_service import run_deletion
from tests.intake_model_support import Harness, document_pdf, enable_models, extract_all, ledger, tesseract_unusable as tesseract_unusable
from tests.test_financial_scope_boundary import scope_seed as scope_seed, worker_tenant as worker_tenant
from tests.test_intake_model_extraction import FIRST, SECOND, Reader, receive_original, split
from tests.test_invoice_source_custody import fixture, store as store
from tests.test_rent_changes import rent_db as rent_db
from tests.test_scope_payment_reconciliation import read_value
from tests.test_scoped_account_statements import client

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("tesseract_unusable")]

ORGANIZATION = "Isolated invoice queue"


async def test_an_organization_with_inbox_ledger_rows_is_erased_in_foreign_key_order(migrator_session, scope_seed, store, rent_db):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    org = ids["org"]
    try:
        async with client(ids, "actor") as api:
            ref = await receive_original(api, ids, document_pdf([FIRST, SECOND, None, None]), pages=4)
            await split(api, ref, 1, 2)
            second = await split(api, ref, 3, 4)
            reader = Reader(transcripts={(second, 3): [ReadingBlock(kind="TEXT", text="Stadtwerke Test GmbH, Abrechnung Wasser, Seite 3")],
                                         (second, 4): [ReadingBlock(kind="TEXT", text="Gesamtbetrag 99,00 EUR, Seite 4")]})
            harness = Harness(reader=reader, extractor=FakeInvoiceExtractionProvider(synthetic_invoice_output(gross_amount=("99.00", 0.9))))
            assert await extract_all(org, harness, processor=extract_logical) == 3
            rows = await ledger(org)
            assert rows and all(row.intake_attempt_id is not None for row in rows), "the inbox's own metering rows"
            attempts = await read_value("SELECT count(*) FROM intake_extraction_attempt WHERE organization_id=:org", ids)
            assert attempts == 3
            assert (await api.post("/api/organizations/deletion")).status_code == 200
            assert (await api.post("/api/organizations/deletion/confirm", json={"organization_name": ORGANIZATION})).status_code == 200

        async with rent_db.begin():
            await rent_db.execute(text("SELECT set_config('app.current_user_id','',true)"))
            assert await run_deletion(rent_db, org, now=datetime.now(timezone.utc) + timedelta(days=8))

        assert await read_value("SELECT count(*) FROM invoice_model_call WHERE organization_id=:org", ids) == 0, "the ledger rows went first"
        assert await read_value("SELECT status FROM organization_deletion WHERE organization_id=:org", ids) == "deleted_pending_retention"
    finally:
        await rent_db.rollback()
        async with rent_db.begin():
            await rent_db.execute(text("DELETE FROM organization_deletion WHERE organization_id=:org"), {"org": org})
