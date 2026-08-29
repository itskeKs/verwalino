"""Typed, provider-agnostic contract for declaration extraction."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Generic, Protocol, TypeVar

from app.declarations.observability import ProviderPassMetadata
from app.declarations.schemas import (
    CoreExtractionOutput,
    DocumentInventoryOutput,
    ReconciliationOutput,
    RuleExtractionOutput,
    TargetedRecheckOutput,
)


@dataclass(frozen=True)
class PageInput:
    document_key: str
    pdf_page: int
    text: str
    quality: float
    image_data_url: str | None = None


@dataclass(frozen=True)
class DocumentInput:
    document_key: str
    pages: Sequence[PageInput]


T = TypeVar("T")

PROVIDER_REFUSAL = "PROVIDER_REFUSAL"
PROVIDER_INCOMPLETE = "PROVIDER_INCOMPLETE"
PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
PROVIDER_SCHEMA_FAILURE = "PROVIDER_SCHEMA_FAILURE"
PROVIDER_CONNECTION_ERROR = "PROVIDER_CONNECTION_ERROR"


@dataclass(frozen=True)
class ProviderUsage:
    """Sanitized metering data suitable for the import budget ledger."""

    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    attempt_count: int
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0


@dataclass(frozen=True)
class ProviderReservationEstimate:
    """No-network worst-case request input reservation.

    Providers which build non-trivial request wrappers (structured schemas,
    repair prompts or vision blocks) expose this small contract so the import
    ledger reserves the same upper bound as the live adapter.  ``input_tokens``
    is aggregate across the bounded attempts; output is deliberately owned by
    the pipeline because one logical call has one aggregate output ceiling.
    """

    input_tokens: int
    attempt_count: int
    attempt_input_tokens: tuple[int, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.input_tokens, bool)
            or not isinstance(self.input_tokens, int)
            or self.input_tokens <= 0
            or isinstance(self.attempt_count, bool)
            or not isinstance(self.attempt_count, int)
            or self.attempt_count < 1
            or len(self.attempt_input_tokens) != self.attempt_count
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
                for value in self.attempt_input_tokens
            )
            or sum(self.attempt_input_tokens) != self.input_tokens
        ):
            raise ValueError("invalid provider reservation estimate")

    @property
    def max_attempt_input_tokens(self) -> int:
        return max(self.attempt_input_tokens)


@dataclass(frozen=True)
class ProviderCallResult(Generic[T]):
    output: T
    usage: ProviderUsage
    metadata: ProviderPassMetadata | None = None
    # ``metadata`` remains the current/final attempt for existing callers.
    # The tuple makes bounded schema repair auditable without retaining data.
    attempt_metadata: tuple[ProviderPassMetadata, ...] = ()
    # False means at least one SDK leg ran without trustworthy usage; callers
    # must retain their conservative reservation rather than settle it down.
    usage_complete: bool = True


class ProviderFailure(RuntimeError):
    """A stable, safe provider error.

    Never put document content, a response body, or a credential in ``message``.
    ``usage`` is optional because a failed request might not have reached the
    provider; when it is present it is already aggregated and sanitized.
    """

    def __init__(
        self,
        code: str,
        retryable: bool,
        message: str,
        usage: ProviderUsage | None = None,
        attempt_metadata: tuple[ProviderPassMetadata, ...] = (),
        metadata: ProviderPassMetadata | None = None,
        usage_complete: bool | None = None,
        attempt_count: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.usage = usage
        self.attempt_metadata = attempt_metadata
        self.metadata = metadata or (attempt_metadata[-1] if attempt_metadata else None)
        inferred_attempt_count = (
            usage.attempt_count
            if isinstance(usage, ProviderUsage)
            else len(attempt_metadata)
        )
        self.attempt_count = (
            inferred_attempt_count if attempt_count is None else attempt_count
        )
        self.usage_complete = (
            usage is not None or inferred_attempt_count == 0
            if usage_complete is None
            else usage_complete
        )

    def set_attempt_metadata(
        self, attempt_metadata: tuple[ProviderPassMetadata, ...]
    ) -> None:
        self.attempt_metadata = attempt_metadata
        self.metadata = attempt_metadata[-1] if attempt_metadata else None


class DeclarationExtractionProvider(Protocol):
    def estimate_reservation(
        self, method: str, *, max_output_tokens: int, **request: object
    ) -> ProviderReservationEstimate: ...

    async def inventory(
        self,
        documents: Sequence[DocumentInput],
        *,
        max_output_tokens: int,
    ) -> ProviderCallResult[DocumentInventoryOutput]: ...

    async def extract_core(
        self,
        documents: Sequence[DocumentInput],
        *,
        max_output_tokens: int,
    ) -> ProviderCallResult[CoreExtractionOutput]: ...

    async def extract_rules(
        self,
        documents: Sequence[DocumentInput],
        *,
        max_output_tokens: int,
    ) -> ProviderCallResult[RuleExtractionOutput]: ...

    async def reconcile(
        self,
        inventory: DocumentInventoryOutput,
        core: CoreExtractionOutput,
        rules: RuleExtractionOutput,
        *,
        max_output_tokens: int,
    ) -> ProviderCallResult[ReconciliationOutput]: ...

    async def recheck(
        self,
        documents: Sequence[DocumentInput],
        issue_codes: Sequence[str],
        subject_keys: Sequence[str],
        *,
        max_output_tokens: int,
    ) -> ProviderCallResult[TargetedRecheckOutput]: ...
