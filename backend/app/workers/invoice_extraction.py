"""Sequential local invoice worker. Database transactions never span extraction."""
import argparse
import asyncio
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import uuid

from app.core.request_id import correlation_context

from sqlalchemy import select

from app.core.config import settings
from app.core.db import get_worker_session_factory, get_worker_unscoped_session, set_tenant_context
from app.models.invoice_processing_attempt import InvoiceProcessingAttempt
from app.models.organization import Organization
from app.core.invoice_ocr import outcome, run_isolated_extraction
from app.core.storage import compute_sha256, get_store
from app.core.worker_process import run_bounded_processor
from app.services.invoice_processing_service import InvoiceProcessingService


async def process_once(factory, org_id, *, processor=run_isolated_extraction):
    if not settings.invoice_extraction_enabled:
        return False
    async with factory() as session, session.begin():
        await set_tenant_context(session, org_id)
        claim = await InvoiceProcessingService(session, org_id).claim()
    if claim is None:
        return False
    with correlation_context(claim["attempt_id"]):
        try:
            data = get_store().get(claim["storage_uri"])
            if compute_sha256(data) != claim["sha256"]:
                result = outcome("failed", "source_integrity_failed")
            else:
                result = await run_bounded_processor(processor, data, claim["mime_type"])
        except Exception:
            result = outcome("failed", "processor_failed")
        async with factory() as session, session.begin():
            await set_tenant_context(session, org_id)
            await InvoiceProcessingService(session, org_id).finish(claim, result)
        logger.info("Invoice worker job completed")
        return True


logger = logging.getLogger(__name__)


async def organization_ids():
    """Discover scheduling IDs only; source access remains tenant/principal scoped."""
    async with get_worker_unscoped_session() as session:
        return list(await session.scalars(select(Organization.id).order_by(Organization.id)))


async def check_readiness():
    """Check local infrastructure without claiming jobs or invoking a provider."""
    root = Path(settings.document_storage_root)
    if not root.is_dir() or not os.access(root, os.R_OK | os.X_OK):
        raise RuntimeError("document_storage_unavailable")
    with tempfile.TemporaryFile() as probe:
        probe.write(b"invoice-worker")
    if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
        raise RuntimeError("ocr_unavailable")
    languages = subprocess.run(["tesseract", "--list-langs"], capture_output=True,
                               text=True, check=True, timeout=10)
    if "deu" not in languages.stdout.splitlines():
        raise RuntimeError("ocr_german_unavailable")
    async with get_worker_unscoped_session() as session:
        await session.execute(select(InvoiceProcessingAttempt.id).limit(0))


async def main():
    from app.core.logging import configure_logging
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization", action="append", type=uuid.UUID,
                        help="Restrict processing to this organization (repeatable; default: all).")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()
    try:
        await check_readiness()
    except Exception:
        logger.error("Invoice worker result=readiness_failed (check worker DB role, document storage and German OCR)")
        return 1
    if args.healthcheck:
        return 0
    # Refresh scheduling IDs each round so new organizations need no restart.
    while True:
        failed = False
        try:
            organizations = args.organization or await organization_ids()
            for org_id in dict.fromkeys(organizations):
                try:
                    await process_once(get_worker_session_factory(), org_id)
                except Exception:
                    failed = True
                    logger.error("Invoice worker organization=%s result=iteration_failed", org_id)
        except Exception:
            failed = True
            logger.error("Invoice worker result=discovery_failed")
        if args.once:
            return 1 if failed else 0
        await asyncio.sleep(2)


async def run():
    task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        return await main()
    except asyncio.CancelledError:
        # Bounded extraction drains before cancellation; its lease can be reclaimed.
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
