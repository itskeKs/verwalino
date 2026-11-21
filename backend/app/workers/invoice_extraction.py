"""Sequential invoice worker. Database transactions never span extraction or a model request.

A structured invoice (XRechnung, or a PDF with embedded ZUGFeRD/Factur-X data) is read by the deterministic parser.
An unstructured PDF goes to the organization's models only when the organization has opted in: its pages are read by
the document reading model and its fields extracted by the extraction model (``declarations/invoice_pipeline.py``,
issue #500). Without that, the original stays and the attempt ends with the reason. Every outcome is a proposal.
"""
import argparse
import asyncio
import logging
import os
from pathlib import Path
import shutil
import signal
import tempfile
import uuid

from app.workers.error_tracking import configure_worker_error_tracking, report_recorded_failure, tracked_job

from sqlalchemy import select

from app.core.config import settings
from app.core.locking import retry_worker_completion
from app.core.db import get_worker_session_factory, get_worker_unscoped_session, set_tenant_context
from app.declarations.invoice_pipeline import (
    INVOICE_MAX_PAGES,
    INVOICE_PAGE_LIMIT,
    InvoiceModelPipeline,
    InvoicePage,
    close_invoice_providers,
    create_invoice_providers,
    pages_need_reading,
)
from app.declarations.provider import InvoiceCategory
from app.models.invoice_processing_attempt import InvoiceProcessingAttempt
from app.models.organization import Organization
from app.core.invoice_pdf import UNSTRUCTURED, outcome, run_isolated_inspection
from app.core.storage import compute_sha256, get_store
from app.core.worker_process import run_bounded_processor
from app.repositories.cost_category_repo import CostCategoryRepository
from app.services.ai_settings_service import (
    INVOICE_AI_KEY_UNREADABLE,
    AiKeyUnreadableError,
    effective_ai_settings,
    invoice_import_blocker,
)
from app.services.invoice_processing_service import InvoiceProcessingService


def inspected_pages(inspected):
    """The pages of an unstructured inspection, or None when the private IPC result is malformed."""
    try:
        pages = [InvoicePage(number=page["number"], text=page["text"], width=float(page["width"]), height=float(page["height"]))
                 for page in inspected["pages"]]
        count = inspected["page_count"]
    except (KeyError, TypeError, ValueError):
        return None
    if type(count) is not int or not 1 <= count <= 200 or len(pages) not in {0, count}:
        return None
    if any(type(page.number) is not int or not isinstance(page.text, str) or page.width <= 0 or page.height <= 0 for page in pages):
        return None
    return pages


async def model_outcome(factory, org_id, claim, data, inspected, providers):
    """The outcome for an unstructured PDF: through the organization's two models, or the reason it cannot go there.

    No provider is constructed and nothing leaves the host unless the organization's AI import switch, confirmed key
    and models allow it; a refusal keeps the original and ends the attempt without a proposal. Such a refusal is a
    local decision and carries the local processor version; only a run of the models carries ``invoice-model-v1``.
    """
    pages = inspected_pages(inspected)
    if pages is None:
        return outcome("failed", "processor_failed")
    async with factory() as session, session.begin():
        await set_tenant_context(session, org_id)
        try:
            effective = await effective_ai_settings(session, org_id)
        except AiKeyUnreadableError:
            return outcome("unavailable", INVOICE_AI_KEY_UNREADABLE)
        categories = [InvoiceCategory(item.code, item.label) for item in await CostCategoryRepository(session).list_active()]
    blocker = invoice_import_blocker(effective, needs_reading=pages_need_reading(pages))
    if blocker is not None:
        return outcome("unavailable", blocker)
    if inspected["page_count"] > INVOICE_MAX_PAGES:
        return outcome("needs_review", INVOICE_PAGE_LIMIT)
    owned = providers(effective)
    try:
        return await InvoiceModelPipeline(session_factory=factory, org_id=org_id, claim=claim, data=data, effective=effective,
                                          providers=owned, categories=categories).run(pages)
    finally:
        await close_invoice_providers(owned)


async def process_once(factory, org_id, *, processor=run_isolated_inspection, providers=create_invoice_providers):
    if not settings.invoice_extraction_enabled:
        return False
    async with factory() as session, session.begin():
        await set_tenant_context(session, org_id)
        claim = await InvoiceProcessingService(session, org_id).claim()
    if claim is None:
        return False
    with tracked_job("invoice_extraction", claim["attempt_id"]):
        failure = None
        try:
            data = get_store().get(claim["storage_uri"])
            if compute_sha256(data) != claim["sha256"]:
                result = outcome("failed", "source_integrity_failed")
            else:
                result = await run_bounded_processor(processor, data, claim["mime_type"])
                if isinstance(result, dict) and result.get("status") == UNSTRUCTURED:
                    result = await model_outcome(factory, org_id, claim, data, result, providers)
        except Exception as error:
            failure = error
            result = outcome("failed", "processor_failed")
        report_recorded_failure(result.get("error_code"), failure)
        async def finish():
            async with factory() as session, session.begin():
                await set_tenant_context(session, org_id)
                return await InvoiceProcessingService(session, org_id).finish(claim, result)
        # On exhaustion, main/WorkflowRunner catches the error; claim() retries the expired lease, then fails at its cap.
        await retry_worker_completion(finish)
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
    # Pages without a text layer are rendered for the reading model.
    if not shutil.which("pdftoppm"):
        raise RuntimeError("pdf_renderer_unavailable")
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
        logger.error("Invoice worker result=readiness_failed (check worker DB role, document storage and the PDF renderer)")
        return 1
    if args.healthcheck:
        return 0
    configure_worker_error_tracking()
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
