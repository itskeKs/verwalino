"""Deterministic provider for tests and local, no-network flows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal

from app.declarations.prompts import INVOICE_PROMPT_VERSION, READING_PROMPT_VERSION
from app.declarations.provider import (
    INVOICE_PASS_ID,
    READING_PASS_ID,
    DocumentInput,
    InvoiceCategory,
    PageImage,
    ProviderCallResult,
    ProviderFailure,
    ProviderReservationEstimate,
    ProviderUsage,
)
from app.declarations.observability import ProviderPassMetadata
from app.declarations.schemas import (
    INVOICE_SCHEMA_VERSION,
    READING_SCHEMA_VERSION,
    CoreExtractionOutput,
    DocumentInventoryOutput,
    InvoiceAmountField,
    InvoiceDateField,
    InvoiceExtractionOutput,
    InvoiceIbanField,
    InvoiceTextField,
    PageTranscript,
    ReadingBlock,
    ReconciliationOutput,
    RuleExtractionOutput,
    TargetedRecheckOutput,
)


class FakeDeclarationProvider:
    """Strict outputs with isolated copies on every successful call."""

    def __init__(
        self,
        inventory_output: DocumentInventoryOutput,
        core_output: CoreExtractionOutput,
        rules_output: RuleExtractionOutput,
        reconciliation_output: ReconciliationOutput,
        recheck_output: TargetedRecheckOutput,
        usage: ProviderUsage | None = None,
    ) -> None:
        self._inventory_output = inventory_output
        self._core_output = core_output
        self._rules_output = rules_output
        self._reconciliation_output = reconciliation_output
        self._recheck_output = recheck_output
        self._usage = usage or ProviderUsage(
            input_tokens=17,
            output_tokens=11,
            cost_usd=Decimal("0.000001"),
            attempt_count=1,
        )

    def _result(self, pass_id: str, output):
        usage = deepcopy(self._usage)
        if pass_id == "recheck":
            configured_model = "gpt-5.4-mini"
            reasoning_effort = "none"
        else:
            configured_model = "gpt-5.6-luna"
            reasoning_effort = "medium"
        metadata = ProviderPassMetadata.from_values(
            pass_id=pass_id,
            attempt_no=1,
            stage="provider",
            configured_model=configured_model,
            returned_model_id=configured_model,
            reasoning_effort=reasoning_effort,
            request_id=None,
            prompt_version="fake-v1",
            prompt_sha256="0" * 64,
            schema_version="1",
            schema_sha256="1" * 64,
            harness_version="1",
            harness_sha256="2" * 64,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            cache_write_input_tokens=usage.cache_write_input_tokens,
            output_tokens=usage.output_tokens,
            estimated_cost_usd=usage.cost_usd,
            latency_ms=0,
            retry_count=max(usage.attempt_count - 1, 0),
            route_action="NONE",
            route_reason_codes=(),
            failure_class=None,
        )
        return ProviderCallResult(
            output=output.model_copy(deep=True),
            usage=usage,
            metadata=metadata,
            attempt_metadata=(metadata,),
        )

    def estimate_reservation(
        self, method: str, *, max_output_tokens: int, **request: object
    ) -> ProviderReservationEstimate:
        """The in-process fake has no hidden prompt or retry request."""
        return ProviderReservationEstimate(
            input_tokens=self._usage.input_tokens,
            attempt_count=1,
            attempt_input_tokens=(self._usage.input_tokens,),
        )

    async def inventory(
        self, documents: Sequence[DocumentInput], *, max_output_tokens: int
    ) -> ProviderCallResult[DocumentInventoryOutput]:
        return self._result("inventory", self._inventory_output)

    async def extract_core(
        self, documents: Sequence[DocumentInput], *, max_output_tokens: int
    ) -> ProviderCallResult[CoreExtractionOutput]:
        return self._result("extract_core", self._core_output)

    async def extract_rules(
        self, documents: Sequence[DocumentInput], *, max_output_tokens: int
    ) -> ProviderCallResult[RuleExtractionOutput]:
        return self._result("extract_rules", self._rules_output)

    async def reconcile(
        self,
        inventory: DocumentInventoryOutput,
        core: CoreExtractionOutput,
        rules: RuleExtractionOutput,
        *,
        max_output_tokens: int,
    ) -> ProviderCallResult[ReconciliationOutput]:
        return self._result("reconcile", self._reconciliation_output)

    async def recheck(
        self,
        documents: Sequence[DocumentInput],
        issue_codes: Sequence[str],
        subject_keys: Sequence[str],
        *,
        max_output_tokens: int,
    ) -> ProviderCallResult[TargetedRecheckOutput]:
        return self._result("recheck", self._recheck_output)


@dataclass(frozen=True)
class ReadingRecord:
    """One request the fake reading stage received: which model was asked for which page."""

    model: str
    document_key: str
    pdf_page: int


class FakeDocumentReadingProvider:
    """Deterministic reading stage for tests and local, no-network flows.

    ``reads`` lists every request in order, also the ones scripted to fail. A page without a scripted transcript
    gets one text block naming it. ``failures`` maps (document_key, pdf_page) to the ``ProviderFailure`` raised.
    """

    def __init__(
        self,
        transcripts: Mapping[tuple[str, int], Sequence[ReadingBlock]] | None = None,
        failures: Mapping[tuple[str, int], ProviderFailure] | None = None,
        usage: ProviderUsage | None = None,
    ) -> None:
        self._transcripts = {key: list(blocks) for key, blocks in (transcripts or {}).items()}
        self._failures = dict(failures or {})
        self._usage = usage or ProviderUsage(
            input_tokens=17, output_tokens=11, cost_usd=Decimal("0.000001"), attempt_count=1
        )
        self.reads: list[ReadingRecord] = []

    def estimate_reservation(
        self, method: str, *, max_output_tokens: int, **request: object
    ) -> ProviderReservationEstimate:
        """The in-process fake has no hidden prompt, image or retry request."""
        return ProviderReservationEstimate(
            input_tokens=self._usage.input_tokens,
            attempt_count=1,
            attempt_input_tokens=(self._usage.input_tokens,),
        )

    async def read_page(
        self, page: PageImage, *, model: str, max_output_tokens: int
    ) -> ProviderCallResult[PageTranscript]:
        key = (page.document_key, page.pdf_page)
        self.reads.append(ReadingRecord(model, page.document_key, page.pdf_page))
        if key in self._failures:
            raise self._failures[key]
        blocks = self._transcripts.get(key) or [
            ReadingBlock(kind="TEXT", text=f"Synthetic page {page.pdf_page} of {page.document_key}")
        ]
        usage = deepcopy(self._usage)
        metadata = ProviderPassMetadata.from_values(
            pass_id=READING_PASS_ID,
            attempt_no=1,
            stage="provider",
            configured_model=model,
            returned_model_id=model,
            reasoning_effort="default",
            request_id=None,
            prompt_version=READING_PROMPT_VERSION,
            prompt_sha256="0" * 64,
            schema_version=READING_SCHEMA_VERSION,
            schema_sha256="1" * 64,
            harness_version="1",
            harness_sha256="2" * 64,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            cache_write_input_tokens=usage.cache_write_input_tokens,
            output_tokens=usage.output_tokens,
            estimated_cost_usd=usage.cost_usd,
            latency_ms=0,
            retry_count=0,
            route_action="NONE",
            route_reason_codes=(),
            failure_class=None,
        )
        transcript = PageTranscript(
            document_key=page.document_key,
            pdf_page=page.pdf_page,
            blocks=[block.model_copy(deep=True) for block in blocks],
        )
        return ProviderCallResult(
            output=transcript, usage=usage, metadata=metadata, attempt_metadata=(metadata,)
        )


def synthetic_invoice_output(**values: object) -> InvoiceExtractionOutput:
    """A complete synthetic invoice proposal: a building insurance premium of 312,00 EUR; ``values`` replace fields
    given as ``field=(value, confidence)`` or ``field=(value, confidence, source_page, source_quote)``. The default
    fields state no source."""
    def stated(kind: type, value: object, confidence: float, source_page: int | None = None, source_quote: str | None = None):
        return kind(value=value, confidence=confidence, source_page=source_page, source_quote=source_quote)

    fields: dict[str, tuple] = {
        "vendor_name": (InvoiceTextField, "Mustermann Versicherungen AG", 0.95),
        "invoice_date": (InvoiceDateField, "2026-01-15", 0.97),
        "document_number": (InvoiceTextField, "RE-2026-0042", 0.9),
        "gross_amount": (InvoiceAmountField, "312.00", 0.98),
        "vat_amount": (InvoiceAmountField, None, 0),
        "vat_rate": (InvoiceAmountField, None, 0),
        "service_period_from": (InvoiceDateField, "2026-01-01", 0.9),
        "service_period_to": (InvoiceDateField, "2026-12-31", 0.9),
        "suggested_category_code": (InvoiceTextField, "GEBAEUDEVERSICHERUNG", 0.8),
        "vendor_iban": (InvoiceIbanField, "DE89 3704 0044 0532 0130 00", 0.9),
    }
    for name, replacement in values.items():
        fields[name] = (fields[name][0], *replacement)
    return InvoiceExtractionOutput(**{name: stated(*parts) for name, parts in fields.items()})


@dataclass(frozen=True)
class InvoiceExtractionRecord:
    """One request the fake extraction stage received: which model was asked about which pages, with what text."""

    model: str
    document_key: str
    pages: tuple[int, ...]
    text: str
    categories: tuple[str, ...]


class FakeInvoiceExtractionProvider:
    """Deterministic invoice extraction stage for tests and local, no-network flows.

    ``calls`` lists every request in order, also the one scripted to fail. ``failure`` is the ``ProviderFailure``
    raised instead of an answer.
    """

    def __init__(
        self,
        output: InvoiceExtractionOutput | None = None,
        failure: ProviderFailure | None = None,
        usage: ProviderUsage | None = None,
    ) -> None:
        self._output = output or synthetic_invoice_output()
        self._failure = failure
        self._usage = usage or ProviderUsage(
            input_tokens=17, output_tokens=11, cost_usd=Decimal("0.000001"), attempt_count=1
        )
        self.calls: list[InvoiceExtractionRecord] = []

    def estimate_reservation(
        self, method: str, *, max_output_tokens: int, **request: object
    ) -> ProviderReservationEstimate:
        """The in-process fake has no hidden prompt or retry request."""
        return ProviderReservationEstimate(
            input_tokens=self._usage.input_tokens,
            attempt_count=1,
            attempt_input_tokens=(self._usage.input_tokens,),
        )

    async def extract_invoice(
        self,
        documents: Sequence[DocumentInput],
        *,
        categories: Sequence[InvoiceCategory],
        model: str,
        max_output_tokens: int,
    ) -> ProviderCallResult[InvoiceExtractionOutput]:
        pages = sorted((page for document in documents for page in document.pages), key=lambda page: page.pdf_page)
        self.calls.append(
            InvoiceExtractionRecord(
                model=model,
                document_key=documents[0].document_key if documents else "",
                pages=tuple(page.pdf_page for page in pages),
                text="\n".join(page.text for page in pages),
                categories=tuple(category.code for category in categories),
            )
        )
        if self._failure is not None:
            raise self._failure
        usage = deepcopy(self._usage)
        metadata = ProviderPassMetadata.from_values(
            pass_id=INVOICE_PASS_ID,
            attempt_no=1,
            stage="provider",
            configured_model=model,
            returned_model_id=model,
            reasoning_effort="default",
            request_id=None,
            prompt_version=INVOICE_PROMPT_VERSION,
            prompt_sha256="0" * 64,
            schema_version=INVOICE_SCHEMA_VERSION,
            schema_sha256="1" * 64,
            harness_version="1",
            harness_sha256="2" * 64,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            cache_write_input_tokens=usage.cache_write_input_tokens,
            output_tokens=usage.output_tokens,
            estimated_cost_usd=usage.cost_usd,
            latency_ms=0,
            retry_count=0,
            route_action="NONE",
            route_reason_codes=(),
            failure_class=None,
        )
        return ProviderCallResult(
            output=self._output.model_copy(deep=True), usage=usage, metadata=metadata, attempt_metadata=(metadata,)
        )
