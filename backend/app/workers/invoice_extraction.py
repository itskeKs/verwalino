"""Sequential local invoice worker. Database transactions never span extraction."""
import argparse
import asyncio
import uuid
from app.core.config import settings
from app.core.db import get_session_factory, set_tenant_context
from app.core.invoice_ocr import outcome, run_isolated_extraction
from app.core.storage import compute_sha256, get_store
from app.services.invoice_processing_service import InvoiceProcessingService


async def process_once(factory, org_id, *, processor=run_isolated_extraction):
    if not settings.invoice_extraction_enabled:
        return False
    async with factory() as session, session.begin():
        await set_tenant_context(session, org_id)
        claim = await InvoiceProcessingService(session, org_id).claim()
    if claim is None:
        return False
    try:
        data = get_store().get(claim["storage_uri"])
        if compute_sha256(data) != claim["sha256"]:
            result = outcome("failed", "source_integrity_failed")
        else:
            result = await asyncio.to_thread(processor, data, claim["mime_type"])
    except Exception:
        result = outcome("failed", "processor_failed")
    async with factory() as session, session.begin():
        await set_tenant_context(session, org_id)
        await InvoiceProcessingService(session, org_id).finish(claim, result)
    return True


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization", action="append", required=True, type=uuid.UUID)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    # One process at a time, round robin across the explicitly configured orgs.
    while True:
        for org_id in args.organization:
            await process_once(get_session_factory(), org_id)
        if args.once:
            return
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
