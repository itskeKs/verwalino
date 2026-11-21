"""Model-assisted extraction of an unstructured invoice (issue #500, ADR 0017).

The pages of an unstructured invoice PDF are read by the organization's document reading model (a page with a text
layer is used as it is), then the organization's extraction model fills the nine fields of the invoice proposal and
proposes one of the seeded cost categories. The models read and interpret: there is no OCR step and no pattern
matching on what they read. The result is a proposal that a person reviews before anything is posted.

Every request is reserved before it leaves, under the organization's budget lock, its opt-in and destination (read again
for each request: a run stops before the next request when the organization withdraws the opt-in or replaces the provider,
endpoint, key or a model the run started with) and its monthly limit, and settled
with the usage it consumed (``invoice_model_call``, migration 0197), in short transactions of its own. The
requests run outside any transaction. The calls of one extraction attempt are bounded by the constants below.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Sequence

from fastapi import HTTPException

from app.core.ai_models import endpoint_request_timeout
from app.core.config import settings
from app.core.db import set_tenant_context
from app.core.invoice_pdf import INVOICE_MAX_PAGES, INVOICE_MAX_TEXT_CHARS, MODEL_PROCESSOR_VERSION, outcome
from app.core.locking import retry_worker_completion
from app.core.worker_process import run_bounded_processor
from app.declarations.pipeline import PipelineFailure, _Ledger, _Reservation, _unwrap, _validate_usage
from app.declarations.preflight import IMAGE_PAGE_SOURCE, DocumentIntegrityError, PageArtifact, PdfPreflight
from app.declarations.provider import (
    READING_IMAGE_MEDIA_TYPE,
    DocumentInput,
    DocumentReadingProvider,
    InvoiceCategory,
    InvoiceExtractionProvider,
    PageImage,
    PageInput,
    ProviderFailure,
    ProviderReservationEstimate,
    ProviderUsage,
)
from app.declarations.schemas import InvoiceExtractionOutput, PageTranscript, transcript_text
from app.repositories.invoice_model_call_repo import InvoiceModelCallRepository
from app.repositories.organization_ai_settings_repo import OrganizationAiSettingsRepository
from app.services.ai_settings_service import (
    INVOICE_AI_DOCUMENT_MODEL_MISSING,
    INVOICE_AI_KEY_UNREADABLE,
    INVOICE_AI_SETTINGS_CHANGED,
    AiKeyUnreadableError,
    effective_ai_settings,
    invoice_import_blocker,
    require_organization_ai_budget,
)
from app.services.invoice_processing_service import InvoiceProcessingService

logger = logging.getLogger(__name__)

# ---- bounds of one extraction attempt -------------------------------------------------------------------------
# An unstructured invoice has at most INVOICE_MAX_PAGES pages and INVOICE_MAX_TEXT_CHARS characters of text
# (core/invoice_pdf.py). A real invoice is one to three pages, a dense page reads to under 3,000 tokens, so a 10-page
# invoice reads to about 30,000 output tokens. The token bounds hold the worst reservation of the last request on top
# of what the earlier requests consumed: an extraction request over the largest text reserves about 130,000 input
# tokens where a transport counts UTF-8 bytes. The cost bound is far above what the priced models cost for that
# (a few cents) and below a figure that would surprise an organization on one invoice; it is the money guard of one
# attempt, and the organization's monthly limit is the second one.
INVOICE_MAX_INPUT_TOKENS = 300_000
INVOICE_MAX_OUTPUT_TOKENS = 64_000
INVOICE_MAX_COST_USD = Decimal("0.25")
# One request's output allowance per stage.
INVOICE_READING_MAX_OUTPUT_TOKENS = 6_000
INVOICE_EXTRACTION_MAX_OUTPUT_TOKENS = 3_000
# A page whose text layer has fewer non-whitespace characters than this is read by the reading model.
INVOICE_TEXT_MIN_CHARS = 40
# Rendering a page for the reading model: poppler at this resolution, at most this many pixels per page (an A4 page
# at 180 dpi is 3.1 million), within a local deadline that excludes the time spent waiting for the models.
INVOICE_RENDER_DPI = 180
INVOICE_RENDER_MAX_PIXELS = 20_000_000
INVOICE_RENDER_SECONDS = 30
INVOICE_LOCAL_DEADLINE_SECONDS = 120
INVOICE_MAX_TEMP_BYTES = 64 * 1024 * 1024
# The identity of the one document inside the requests; it carries no meaning beyond the pages' order.
DOCUMENT_KEY = "invoice"

# The reasons an attempt ends without a proposal on this path (lower-case like every attempt code) and their status.
INVOICE_PAGE_LIMIT = "invoice_page_limit"
INVOICE_TEXT_LIMIT = "invoice_text_limit"
INVOICE_AI_LIMIT = "invoice_ai_limit"
ORGANIZATION_BUDGET_EXCEEDED = "organization_ai_budget_exceeded"
MODEL_READING_FAILED = "model_reading_failed"
MODEL_EXTRACTION_FAILED = "model_extraction_failed"
MODEL_RESULT_INVALID = "model_result_invalid"
LEASE_LOST = "lease_lost"

# The weight of each field in the overall confidence that the review panel shows next to the proposal.
_FIELD_WEIGHTS = {
    "invoice_date": Decimal("0.20"), "gross_amount": Decimal("0.30"), "document_number": Decimal("0.10"),
    "vat_rate": Decimal("0.05"), "vat_amount": Decimal("0.05"), "service_period_from": Decimal("0.10"),
    "service_period_to": Decimal("0.05"), "vendor_name": Decimal("0.05"), "suggested_category_code": Decimal("0.10"),
}


class InvoiceModelFailure(Exception):
    """An attempt that ends with this status and reason; never carries document text or a provider message."""

    def __init__(self, status: str, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class InvoicePage:
    """One page of an unstructured invoice as the local inspection found it: its text layer and its size in points."""

    number: int
    text: str
    width: float
    height: float


@dataclass(frozen=True)
class InvoiceProviders:
    """The two stages' transports: the reading model's (None without a document reading model) and the extraction model's."""

    reader: DocumentReadingProvider | None
    extractor: InvoiceExtractionProvider


def create_invoice_providers(effective) -> InvoiceProviders:
    """The transports of the organization's provider, endpoint and key, one per model role; nothing is requested yet."""
    from app.declarations.provider_runtime import create_extraction_provider, create_reading_provider

    reader = create_reading_provider(effective) if effective.document_model else None
    return InvoiceProviders(reader=reader, extractor=create_extraction_provider(effective))


async def close_invoice_providers(providers: InvoiceProviders) -> None:
    for owned in (providers.reader, providers.extractor):
        client = getattr(owned, "_client", None)
        if client is not None:
            await client.close()


def non_whitespace_length(text: str) -> int:
    return len("".join(text.split()))


def pages_need_reading(pages: Sequence[InvoicePage]) -> bool:
    """Whether the reading model must read at least one page: one without a text layer of its own."""
    return any(non_whitespace_length(page.text) < INVOICE_TEXT_MIN_CHARS for page in pages)


def _render_failure(code: str) -> InvoiceModelFailure:
    if code == "PAGE_RENDER_TIMEOUT":
        return InvoiceModelFailure("timeout", "render_timeout")
    if code in {"PAGE_RENDER_UNAVAILABLE", "PDF_UNREADABLE"}:
        return InvoiceModelFailure("unavailable", "render_unavailable")
    return InvoiceModelFailure("unsupported", "image_pixel_limit")


class InvoiceModelPipeline:
    """Read the pages of one claimed invoice attempt, extract its fields and meter every request."""

    def __init__(
        self,
        *,
        session_factory,
        org_id: uuid.UUID,
        claim: dict[str, Any],
        data: bytes,
        effective,
        providers: InvoiceProviders,
        categories: Sequence[InvoiceCategory],
        preflight: PdfPreflight | None = None,
    ) -> None:
        self._factory = session_factory
        self._org_id = org_id
        self._claim = claim
        self._data = data
        self._effective = effective
        self._providers = providers
        self._categories = tuple(categories)
        # Until the pages are known the stricter answer holds; ``run`` sets it from them.
        self._needs_reading = True
        self._preflight = preflight or PdfPreflight(
            max_pdf_bytes=len(data) + 1,
            max_pages=INVOICE_MAX_PAGES,
            ocr_dpi=INVOICE_RENDER_DPI,
            ocr_timeout_seconds=INVOICE_RENDER_SECONDS,
            max_pixels_per_page=INVOICE_RENDER_MAX_PIXELS,
            max_temp_bytes=INVOICE_MAX_TEMP_BYTES,
            max_ocr_pages_per_import=INVOICE_MAX_PAGES,
            max_pixels_per_import=INVOICE_RENDER_MAX_PIXELS * INVOICE_MAX_PAGES,
            deadline_seconds=INVOICE_LOCAL_DEADLINE_SECONDS,
        )
        timeout = endpoint_request_timeout(effective.provider, effective.request_timeout_seconds) or settings.declaration_provider_timeout_seconds
        # A request may take two timeouts (the one bounded repair of an invalid answer) before its result is settled.
        self._lease_seconds = min(2 * timeout + 60, 3600)

    async def run(self, pages: Sequence[InvoicePage]) -> dict:
        """The outcome of the attempt: a proposal, or the structured reason there is none."""
        ledger = _Ledger(
            input_tokens=0, output_tokens=0, cost_usd=Decimal("0"), attempts=0,
            max_input_tokens=INVOICE_MAX_INPUT_TOKENS, max_output_tokens=INVOICE_MAX_OUTPUT_TOKENS,
            max_cost_usd=INVOICE_MAX_COST_USD, configured=_allowance(INVOICE_READING_MAX_OUTPUT_TOKENS),
            model=self._effective.model, price_provider=self._effective.provider,
        )
        self._needs_reading = pages_need_reading(pages)
        try:
            texts = await self._read_pages(ledger, pages)
            return await self._extract(ledger, pages, texts)
        except InvoiceModelFailure as failure:
            return outcome(failure.status, failure.code, processor_version=MODEL_PROCESSOR_VERSION)

    # ---- stage 1: reading ----------------------------------------------------------------------------------

    async def _read_pages(self, ledger: _Ledger, pages: Sequence[InvoicePage]) -> list[str]:
        budget = self._preflight.new_import_budget()
        texts: list[str] = []
        for page in pages:
            if non_whitespace_length(page.text) >= INVOICE_TEXT_MIN_CHARS:
                texts.append(page.text)
            else:
                texts.append(await self._read_page(ledger, budget, page))
            # After every page, so that no page is read (and paid for) once the text has passed the bound.
            if len("\n".join(texts)) > INVOICE_MAX_TEXT_CHARS:
                raise InvoiceModelFailure("needs_review", INVOICE_TEXT_LIMIT)
        return texts

    async def _read_page(self, ledger: _Ledger, budget, page: InvoicePage) -> str:
        reader, model = self._providers.reader, self._effective.document_model
        if reader is None or not model:
            raise InvoiceModelFailure("unavailable", INVOICE_AI_DOCUMENT_MODEL_MISSING)
        pixels = math.ceil(page.width * INVOICE_RENDER_DPI / 72) * math.ceil(page.height * INVOICE_RENDER_DPI / 72)
        if pixels > INVOICE_RENDER_MAX_PIXELS:
            raise InvoiceModelFailure("unsupported", "image_pixel_limit")
        artifact = PageArtifact(page_number=page.number, text="", source=IMAGE_PAGE_SOURCE, rotation=0, dpi=INVOICE_RENDER_DPI)
        try:
            png = await run_bounded_processor(
                partial(self._preflight.render_page_for_reading, self._data, artifact, budget=budget))
        except DocumentIntegrityError as error:
            raise _render_failure(error.code) from None
        image = PageImage(document_key=DOCUMENT_KEY, pdf_page=page.number, media_type=READING_IMAGE_MEDIA_TYPE, data=png)
        ledger.configured = _allowance(INVOICE_READING_MAX_OUTPUT_TOKENS)

        def call(max_output_tokens: int):
            return reader.read_page(image, model=model, max_output_tokens=max_output_tokens)

        transcript = await self._metered(
            ledger, budget, provider=reader, model=model, stage="read_page", pdf_page=page.number,
            method="read_page", request={"page": image}, call=call, expected=PageTranscript,
            failure_code=MODEL_READING_FAILED,
        )
        if (transcript.document_key, transcript.pdf_page) != (DOCUMENT_KEY, page.number):
            raise InvoiceModelFailure("failed", MODEL_RESULT_INVALID)
        return transcript_text(transcript.blocks)

    # ---- stage 2: extraction -------------------------------------------------------------------------------

    async def _extract(self, ledger: _Ledger, pages: Sequence[InvoicePage], texts: Sequence[str]) -> dict:
        extractor, model = self._providers.extractor, self._effective.model
        documents = (DocumentInput(DOCUMENT_KEY, tuple(
            PageInput(document_key=DOCUMENT_KEY, pdf_page=page.number, text=text, quality=1.0)
            for page, text in zip(pages, texts, strict=True))),)
        ledger.configured = _allowance(INVOICE_EXTRACTION_MAX_OUTPUT_TOKENS)

        def call(max_output_tokens: int):
            return extractor.extract_invoice(documents, categories=self._categories, model=model,
                                             max_output_tokens=max_output_tokens)

        extracted = await self._metered(
            ledger, None, provider=extractor, model=model, stage="extract_invoice", pdf_page=None,
            method="extract_invoice", request={"documents": documents, "categories": self._categories}, call=call,
            expected=InvoiceExtractionOutput, failure_code=MODEL_EXTRACTION_FAILED,
        )
        return _proposal(extracted, {category.code for category in self._categories}, "\n".join(texts),
                         [page.number for page in pages])

    # ---- one metered request -------------------------------------------------------------------------------

    async def _metered(self, ledger: _Ledger, budget, *, provider, model: str, stage: str, pdf_page: int | None,
                       method: str, request: dict[str, Any], call: Callable[[int], Awaitable[Any]],
                       expected: type, failure_code: str):
        """Reserve the request, make it, settle it with what it consumed: the one path of every provider request."""
        allowance = ledger.output_allowance()
        if allowance <= 0:
            raise InvoiceModelFailure("needs_review", INVOICE_AI_LIMIT)
        try:
            estimate = provider.estimate_reservation(method, max_output_tokens=allowance, **request)
        except Exception:
            # Nothing was requested and nothing is reserved: the request could not even be built.
            raise InvoiceModelFailure("failed", failure_code) from None
        if not isinstance(estimate, ProviderReservationEstimate):
            raise InvoiceModelFailure("failed", failure_code)
        try:
            reservation = ledger.reserve(
                "", reserved_input_tokens=estimate.input_tokens, model=model,
                price_provider=getattr(provider, "_price_provider", "openai"))
        except PipelineFailure:
            raise InvoiceModelFailure("needs_review", INVOICE_AI_LIMIT) from None
        call_id = await self._reserve_durably(stage, pdf_page, model, reservation)
        ledger.charge_reservation(reservation)
        started = time.monotonic()
        try:
            raw = await call(reservation.output_tokens)
        except ProviderFailure as exc:
            self._exclude_wait(budget, started)
            await self._settle(call_id, *_failed_amounts(exc, reservation), error_code=exc.code)
            raise InvoiceModelFailure("failed", failure_code) from None
        except asyncio.CancelledError:
            # A worker shutdown while the request was out: what it consumed is unknown, so it stays booked at its
            # reservation, as for any failure without reported usage, and the shutdown goes on. A settlement that
            # fails here leaves the row reserved, which counts at the same amount; it never replaces the shutdown.
            try:
                await self._settle(call_id, reservation.input_tokens, reservation.output_tokens, reservation.cost_usd,
                                   complete=False, error_code="WORKER_CANCELLED")
            except Exception:
                logger.error("Invoice model call result=cancelled_settlement_failed")
            raise
        except Exception:
            self._exclude_wait(budget, started)
            await self._settle(call_id, reservation.input_tokens, reservation.output_tokens, reservation.cost_usd,
                               complete=False, error_code="PROVIDER_UNEXPECTED_FAILURE")
            raise InvoiceModelFailure("failed", failure_code) from None
        self._exclude_wait(budget, started)
        try:
            output = _unwrap(raw, expected, ledger, reservation, reservation_already_charged=True)
        except PipelineFailure as exc:
            # The usage a provider reported is recorded when it is valid; otherwise the reservation stands.
            await self._settle(call_id, *_reported_amounts(raw, reservation), error_code=exc.code)
            raise InvoiceModelFailure("failed", MODEL_RESULT_INVALID) from None
        usage = raw.usage
        if raw.usage_complete:
            await self._settle(call_id, usage.input_tokens, usage.output_tokens, usage.cost_usd, complete=True, error_code=None)
        else:
            await self._settle(call_id, reservation.input_tokens, reservation.output_tokens, reservation.cost_usd,
                               complete=False, error_code=None)
        return output

    @staticmethod
    def _exclude_wait(budget, started: float) -> None:
        if budget is not None:
            budget.exclude_wait(time.monotonic() - started)

    async def _reserve_durably(self, stage: str, pdf_page: int | None, model: str, reservation: _Reservation) -> uuid.UUID:
        """In one transaction: extend the claim's lease, check the opt-in and the monthly limit and hold the worst case
        of the request."""
        provider = self._providers.reader if stage == "read_page" else self._providers.extractor
        refusal = None
        call_id = None
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, self._org_id)
            if not await InvoiceProcessingService(session, self._org_id).renew(self._claim, self._lease_seconds):
                # Committed on purpose: an initiator who lost permission has cancelled the attempt in this transaction.
                refusal = InvoiceModelFailure("failed", LEASE_LOST)
            elif (blocker := await self._opt_in_blocker(session)) is not None:
                refusal = InvoiceModelFailure("unavailable", blocker)
            else:
                try:
                    await require_organization_ai_budget(session, self._org_id, reservation.cost_usd, model=model,
                                                         provider=getattr(provider, "_price_provider", "openai"))
                except HTTPException:
                    refusal = InvoiceModelFailure("unavailable", ORGANIZATION_BUDGET_EXCEEDED)
                else:
                    call_id = await InvoiceModelCallRepository(session, self._org_id).reserve(
                        attempt_id=self._claim["attempt_id"], stage=stage, pdf_page=pdf_page, model_name=model,
                        input_tokens=reservation.input_tokens, output_tokens=reservation.output_tokens,
                        cost_usd=reservation.cost_usd)
        if refusal is not None:
            raise refusal
        return call_id

    async def _opt_in_blocker(self, session) -> str | None:
        """The reason the organization no longer allows this request, read again now: None, or the worker's code.

        The same decision the worker takes before the run (``invoice_import_blocker``), under the budget lock that
        every settings save holds, so a switch turned off or a confirmation lost stops the run before its next request.
        So does a destination or model that is no longer the one the run was built from: the clients keep the provider,
        endpoint, key and models of the start, and a replacement is the organization's word for the next request, which
        a new start picks up.
        """
        await OrganizationAiSettingsRepository(session, self._org_id).lock_budget()
        try:
            effective = await effective_ai_settings(session, self._org_id)
        except AiKeyUnreadableError:
            return INVOICE_AI_KEY_UNREADABLE
        if (blocker := invoice_import_blocker(effective, needs_reading=self._needs_reading)) is not None:
            return blocker
        if _destination(effective) != _destination(self._effective):
            return INVOICE_AI_SETTINGS_CHANGED
        return None

    async def _settle(self, call_id: uuid.UUID, input_tokens: int, output_tokens: int, cost_usd: Decimal,
                      complete: bool, *, error_code: str | None) -> None:
        async def settle() -> None:
            async with self._factory() as session, session.begin():
                await set_tenant_context(session, self._org_id)
                await InvoiceModelCallRepository(session, self._org_id).settle(
                    call_id, input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost_usd,
                    usage_complete=complete, error_code=error_code)

        await retry_worker_completion(settle)



def _destination(effective) -> tuple:
    """Where a request goes and with what: provider, endpoint, key, reading model and extraction model.

    The key is the decrypted one the settings read already carries; a secret compares by its value, and saving the same
    key again (a new ciphertext) is no change.
    """
    return (effective.provider, effective.base_url, effective.api_key, effective.document_model, effective.model)


def _allowance(tokens: int) -> SimpleNamespace:
    """What the shared ledger reads of its settings: the output allowance of one request of the current stage."""
    return SimpleNamespace(declaration_provider_max_output_tokens_per_call=tokens)


def _failed_amounts(exc: ProviderFailure, reservation: _Reservation) -> tuple[int, int, Decimal, bool]:
    """What a failed request consumed: its reported usage; nothing only when it provably never left; else the reservation.

    Unknown is not zero. A request that reached the provider leaves an attempt on the failure (its metadata, counted
    in ``attempt_count``); a failure without any attempt, usage or metadata was raised before anything was sent.
    """
    usage = exc.usage if isinstance(exc.usage, ProviderUsage) else None
    if exc.usage_complete is True and usage is not None:
        try:
            _validate_usage(usage)
        except PipelineFailure:
            return reservation.input_tokens, reservation.output_tokens, reservation.cost_usd, False
        return usage.input_tokens, usage.output_tokens, usage.cost_usd, True
    if (exc.usage_complete is True and exc.usage is None and exc.attempt_count == 0
            and not exc.attempt_metadata and exc.metadata is None):
        return 0, 0, Decimal("0"), True
    return reservation.input_tokens, reservation.output_tokens, reservation.cost_usd, False


def _reported_amounts(raw: object, reservation: _Reservation) -> tuple[int, int, Decimal, bool]:
    usage = getattr(raw, "usage", None)
    if isinstance(usage, ProviderUsage) and getattr(raw, "usage_complete", False) is True:
        try:
            _validate_usage(usage)
        except PipelineFailure:
            pass
        else:
            return usage.input_tokens, usage.output_tokens, usage.cost_usd, True
    return reservation.input_tokens, reservation.output_tokens, reservation.cost_usd, False


def _proposal(output: InvoiceExtractionOutput, category_codes: set[str], raw_text: str, source_pages: list[int]) -> dict:
    """The invoice proposal of the extraction model's typed fields; a value that is not stated has confidence 0."""
    values: dict[str, str | None] = {}
    confidences: dict[str, float] = {}
    for name in _FIELD_WEIGHTS:
        field = getattr(output, name)
        value = field.value
        if name == "suggested_category_code" and value not in category_codes:
            value = None
        values[name] = value
        confidences[name] = float(field.confidence) if value is not None else 0.0
    overall = sum(_FIELD_WEIGHTS[name] * Decimal(str(confidences[name])) for name in _FIELD_WEIGHTS)
    payload = {
        **values,
        "confidence": float(round(min(overall, Decimal("1")), 4)),
        "field_confidences": confidences,
        "raw_text": raw_text,
        "is_einvoice": False,
        "source_format": None,
        "source_pages": source_pages,
    }
    complete = values["gross_amount"] is not None and values["invoice_date"] is not None
    return outcome("succeeded" if complete else "needs_review", None if complete else "missing_fields", payload,
                   processor_version=MODEL_PROCESSOR_VERSION)
