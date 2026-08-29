"""Bounded OpenAI Responses adapter for declaration extraction.

This module intentionally contains no logging of request/response data.  The
pipeline receives only typed output and sanitized aggregate usage.
"""

from __future__ import annotations

import base64
from hashlib import sha256
import io
import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from json import JSONDecodeError
from typing import Any, Callable, TypeVar

import tiktoken
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    ContentFilterFinishReasonError,
    LengthFinishReasonError,
    RateLimitError,
)
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.declarations.prompts import (
    CORE_EXTRACTION_PROMPT,
    INVENTORY_PROMPT,
    RECHECK_PROMPT,
    RECONCILIATION_PROMPT,
    RULE_EXTRACTION_PROMPT,
)
from app.declarations.provider import (
    DocumentInput,
    PageInput,
    PROVIDER_CONNECTION_ERROR,
    PROVIDER_INCOMPLETE,
    PROVIDER_REFUSAL,
    PROVIDER_SCHEMA_FAILURE,
    PROVIDER_TIMEOUT,
    ProviderCallResult,
    ProviderFailure,
    ProviderReservationEstimate,
    ProviderUsage,
)
from app.declarations.observability import (
    ProviderPassMetadata,
    StaticContractFingerprints,
    fingerprint_static_contract,
)
from app.declarations.schemas import (
    CoreExtractionOutput,
    DocumentInventoryOutput,
    ReconciliationOutput,
    RuleExtractionOutput,
    StrictModel,
    TargetedRecheckOutput,
)


T = TypeVar("T", bound=StrictModel)
_MILLION = Decimal("1000000")
_LUNA_SHORT_CONTEXT_MAX_INPUT_TOKENS = 272_000
# The documented 2,500-patch cap times the 1.2 multiplier is 3,000.
# OpenAI notes that final billing may differ by one token due to rounding, so
# reserve the upper boundary instead of the nominal estimate.
_GPT_54_MINI_HIGH_IMAGE_INPUT_TOKENS = 3_001
_PNG_DATA_URL = re.compile(r"^data:image/png;base64,([A-Za-z0-9+/]*={0,2})$")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_STATIC_HARNESS = (
    "declaration-responses-v1|structured-output|store=false|"
    "no-previous-response-id|fixed-pass-manager"
)
_SAFE_RESPONSE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _safe_response_identifier(value: object) -> str | None:
    if isinstance(value, str) and _SAFE_RESPONSE_IDENTIFIER.fullmatch(value):
        return value
    return None


def _metadata_tuple(
    *values: ProviderPassMetadata | None,
) -> tuple[ProviderPassMetadata, ...]:
    return tuple(value for value in values if isinstance(value, ProviderPassMetadata))


def _response_has_refusal(response: object) -> bool:
    """Inspect only typed item discriminators, never refusal text or payloads."""
    output = response.get("output") if isinstance(response, Mapping) else getattr(response, "output", None)
    if not isinstance(output, Sequence) or isinstance(output, (str, bytes, bytearray)):
        return False
    for item in output:
        content = item.get("content") if isinstance(item, Mapping) else getattr(item, "content", None)
        if not isinstance(content, Sequence) or isinstance(content, (str, bytes, bytearray)):
            continue
        for block in content:
            block_type = block.get("type") if isinstance(block, Mapping) else getattr(block, "type", None)
            if block_type == "refusal":
                return True
    return False


@dataclass(frozen=True)
class _Rates:
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    cached_input_usd_per_million: Decimal
    cache_write_multiplier: Decimal | None


class _SchemaFailure(Exception):
    def __init__(
        self,
        usage: ProviderUsage | None = None,
        metadata: ProviderPassMetadata | None = None,
    ) -> None:
        self.usage = usage
        self.metadata = metadata
        self.usage_complete = usage is not None


@dataclass(frozen=True)
class _AttemptSuccess:
    output: StrictModel
    usage: ProviderUsage
    metadata: ProviderPassMetadata


class OpenAiDeclarationProvider:
    """Uses Structured Outputs while failing closed on metering uncertainty."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        primary_model: str | None = None,
        escalation_model: str | None = None,
        timeout_seconds: int | None = None,
        max_cost_usd: Decimal | None = None,
        max_input_tokens: int | None = None,
        max_output_tokens_per_call: int | None = None,
        max_recheck_pages: int | None = None,
        token_encoder: Any | None = None,
        configured_settings: Settings | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        configured = configured_settings or settings
        self._primary_model = primary_model or configured.declaration_primary_model
        self._escalation_model = escalation_model or configured.declaration_escalation_model
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else configured.declaration_provider_timeout_seconds
        )
        self._max_cost_usd = (
            max_cost_usd
            if max_cost_usd is not None
            else configured.declaration_max_cost_usd
        )
        self._max_input_tokens = (
            max_input_tokens
            if max_input_tokens is not None
            else configured.declaration_max_input_tokens
        )
        self._max_output_tokens_per_call = (
            max_output_tokens_per_call
            if max_output_tokens_per_call is not None
            else configured.declaration_provider_max_output_tokens_per_call
        )
        self._max_recheck_pages = (
            max_recheck_pages
            if max_recheck_pages is not None
            else configured.declaration_max_recheck_pages
        )
        self._max_image_encoded_bytes = configured.declaration_ocr_max_temp_bytes * 2
        self._max_image_decoded_bytes = configured.declaration_ocr_max_temp_bytes
        self._max_image_pixels = configured.declaration_ocr_max_pixels_per_page
        # Tests inject a local encoder so mocked SDK verification never needs a
        # tokenizer download. Production uses the official tokenizer package.
        self._token_encoder = token_encoder
        self._monotonic_clock = monotonic_clock
        self._primary_reasoning_effort = configured.declaration_primary_reasoning_effort
        self._escalation_reasoning_effort = configured.declaration_escalation_reasoning_effort
        self._prompt_version = configured.declaration_prompt_version
        self._schema_version = configured.declaration_schema_version
        self._harness_version = configured.declaration_harness_version
        self._rates = self._configured_rates(configured)
        self._validate_configured_rates()
        if client is not None:
            self._client = client
        else:
            api_key = (
                configured.openai_api_key.get_secret_value().strip()
                if configured.openai_api_key is not None
                else ""
            )
            self._client = AsyncOpenAI(api_key=api_key) if api_key else None

    def _configured_rates(self, configured: Settings) -> dict[str, _Rates]:
        rates: dict[str, _Rates] = {}
        # Rates are tied to the verified model IDs. A renamed model must be
        # explicitly reverified instead of inheriting a stale price.
        if self._primary_model == "gpt-5.6-luna":
            rates[self._primary_model] = _Rates(
                configured.declaration_primary_input_usd_per_million,
                configured.declaration_primary_output_usd_per_million,
                configured.declaration_primary_cached_input_usd_per_million,
                configured.declaration_primary_cache_write_multiplier,
            )
        if self._escalation_model == "gpt-5.4-mini":
            rates[self._escalation_model] = _Rates(
                configured.declaration_escalation_input_usd_per_million,
                configured.declaration_escalation_output_usd_per_million,
                configured.declaration_escalation_cached_input_usd_per_million,
                None,
            )
        return rates

    def _validate_configured_rates(self) -> None:
        for rates in self._rates.values():
            if (
                not rates.input_usd_per_million.is_finite()
                or rates.input_usd_per_million <= 0
                or not rates.output_usd_per_million.is_finite()
                or rates.output_usd_per_million <= 0
                or not rates.cached_input_usd_per_million.is_finite()
                or rates.cached_input_usd_per_million <= 0
                or (
                    rates.cache_write_multiplier is not None
                    and rates.cache_write_multiplier != Decimal("1.25")
                )
            ):
                raise ProviderFailure(
                    "MODEL_RATE_INVALID",
                    False,
                    "The configured model rate must be positive and finite.",
                )

    async def inventory(
        self, documents: Sequence[DocumentInput], *, max_output_tokens: int
    ) -> ProviderCallResult[DocumentInventoryOutput]:
        return await self._run_documents(
            pass_id="inventory",
            model=self._primary_model,
            prompt=INVENTORY_PROMPT,
            documents=documents,
            output_type=DocumentInventoryOutput,
            max_output_tokens=max_output_tokens,
            include_images=False,
        )

    async def extract_core(
        self, documents: Sequence[DocumentInput], *, max_output_tokens: int
    ) -> ProviderCallResult[CoreExtractionOutput]:
        return await self._run_documents(
            pass_id="extract_core",
            model=self._primary_model,
            prompt=CORE_EXTRACTION_PROMPT,
            documents=documents,
            output_type=CoreExtractionOutput,
            max_output_tokens=max_output_tokens,
            include_images=False,
        )

    async def extract_rules(
        self, documents: Sequence[DocumentInput], *, max_output_tokens: int
    ) -> ProviderCallResult[RuleExtractionOutput]:
        return await self._run_documents(
            pass_id="extract_rules",
            model=self._primary_model,
            prompt=RULE_EXTRACTION_PROMPT,
            documents=documents,
            output_type=RuleExtractionOutput,
            max_output_tokens=max_output_tokens,
            include_images=False,
        )

    async def reconcile(
        self,
        inventory: DocumentInventoryOutput,
        core: CoreExtractionOutput,
        rules: RuleExtractionOutput,
        *,
        max_output_tokens: int,
    ) -> ProviderCallResult[ReconciliationOutput]:
        payload = json.dumps(
            {
                "inventory": inventory.model_dump(mode="json"),
                "core": core.model_dump(mode="json"),
                "rules": rules.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return await self._run(
            pass_id="reconcile",
            model=self._primary_model,
            prompt=RECONCILIATION_PROMPT,
            content_blocks=[{"type": "input_text", "text": payload}],
            output_type=ReconciliationOutput,
            max_output_tokens=max_output_tokens,
        )

    async def recheck(
        self,
        documents: Sequence[DocumentInput],
        issue_codes: Sequence[str],
        subject_keys: Sequence[str],
        *,
        max_output_tokens: int,
    ) -> ProviderCallResult[TargetedRecheckOutput]:
        if self._max_recheck_pages <= 0:
            raise ProviderFailure(
                "OPENAI_RECHECK_PAGE_LIMIT_INVALID",
                False,
                "Recheck page limit must be positive.",
            )
        pages = self._validated_recheck_pages(documents)
        content_blocks: list[dict[str, str]] = [
            {
                "type": "input_text",
                "text": json.dumps(
                    {
                        "issue_codes": sorted(set(issue_codes)),
                        "subject_keys": sorted(set(subject_keys)),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        ]
        content_blocks.extend(self._page_content_blocks(pages, include_images=True))
        return await self._run(
            pass_id="recheck",
            model=self._escalation_model,
            prompt=RECHECK_PROMPT,
            content_blocks=content_blocks,
            output_type=TargetedRecheckOutput,
            max_output_tokens=max_output_tokens,
        )

    def estimate_reservation(
        self,
        method: str,
        *,
        max_output_tokens: int,
        documents: Sequence[DocumentInput] = (),
        inventory: DocumentInventoryOutput | None = None,
        core: CoreExtractionOutput | None = None,
        rules: RuleExtractionOutput | None = None,
        issue_codes: Sequence[str] = (),
        subject_keys: Sequence[str] = (),
    ) -> ProviderReservationEstimate:
        """Return the exact local worst-case input for one logical request.

        The pipeline calls this before its durable reservation.  It is kept in
        this adapter so the user-visible prompt, Structured Outputs schema,
        repair prompt and vision policy cannot drift from live preflight.
        """
        self._validate_output_allowance(max_output_tokens)
        if method == "inventory":
            self._reject_unexpected_images(documents)
            return self._request_reservation(
                "inventory", self._primary_model,
                INVENTORY_PROMPT,
                self._page_content_blocks(self._sorted_pages(documents), include_images=False),
                DocumentInventoryOutput,
                max_output_tokens,
            )
        if method == "extract_core":
            self._reject_unexpected_images(documents)
            return self._request_reservation(
                "extract_core", self._primary_model,
                CORE_EXTRACTION_PROMPT,
                self._page_content_blocks(self._sorted_pages(documents), include_images=False),
                CoreExtractionOutput,
                max_output_tokens,
            )
        if method == "extract_rules":
            self._reject_unexpected_images(documents)
            return self._request_reservation(
                "extract_rules", self._primary_model,
                RULE_EXTRACTION_PROMPT,
                self._page_content_blocks(self._sorted_pages(documents), include_images=False),
                RuleExtractionOutput,
                max_output_tokens,
            )
        if method == "reconcile" and inventory is not None and core is not None and rules is not None:
            payload = json.dumps(
                {
                    "inventory": inventory.model_dump(mode="json"),
                    "core": core.model_dump(mode="json"),
                    "rules": rules.model_dump(mode="json"),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            return self._request_reservation(
                "reconcile", self._primary_model,
                RECONCILIATION_PROMPT,
                [{"type": "input_text", "text": payload}],
                ReconciliationOutput,
                max_output_tokens,
            )
        if method == "recheck":
            pages = self._validated_recheck_pages(documents)
            blocks: list[dict[str, str]] = [
                {
                    "type": "input_text",
                    "text": json.dumps(
                        {
                            "issue_codes": sorted(set(issue_codes)),
                            "subject_keys": sorted(set(subject_keys)),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            ]
            blocks.extend(self._page_content_blocks(pages, include_images=True))
            return self._request_reservation(
                "recheck", self._escalation_model,
                RECHECK_PROMPT,
                blocks,
                TargetedRecheckOutput,
                max_output_tokens,
            )
        raise ProviderFailure(
            "OPENAI_RESERVATION_ESTIMATE_INVALID",
            False,
            "The provider request cannot be estimated safely.",
        )

    async def _run_documents(
        self,
        *,
        pass_id: str,
        model: str,
        prompt: str,
        documents: Sequence[DocumentInput],
        output_type: type[T],
        max_output_tokens: int,
        include_images: bool,
    ) -> ProviderCallResult[T]:
        if not include_images:
            self._reject_unexpected_images(documents)
        return await self._run(
            pass_id=pass_id,
            model=model,
            prompt=prompt,
            content_blocks=self._page_content_blocks(
                self._sorted_pages(documents), include_images=include_images
            ),
            output_type=output_type,
            max_output_tokens=max_output_tokens,
        )

    @staticmethod
    def _sorted_pages(documents: Sequence[DocumentInput]):
        return sorted(
            (page for document in documents for page in document.pages),
            key=lambda page: (page.document_key, page.pdf_page),
        )

    @staticmethod
    def _reject_unexpected_images(documents: Sequence[DocumentInput]) -> None:
        if any(page.image_data_url is not None for page in OpenAiDeclarationProvider._sorted_pages(documents)):
            raise ProviderFailure(
                "OPENAI_UNEXPECTED_IMAGE_INPUT",
                False,
                "Regular declaration passes must not include image input.",
            )

    def _validated_recheck_pages(
        self, documents: Sequence[DocumentInput]
    ) -> list[PageInput]:
        if self._max_recheck_pages <= 0:
            raise ProviderFailure(
                "OPENAI_RECHECK_PAGE_LIMIT_INVALID",
                False,
                "Recheck page limit must be positive.",
            )
        pages = self._sorted_pages(documents)
        grouped: dict[tuple[str, int], list[PageInput]] = {}
        for page in pages:
            grouped.setdefault((page.document_key, page.pdf_page), []).append(page)
        if not pages or len(grouped) > self._max_recheck_pages:
            raise ProviderFailure(
                "OPENAI_RECHECK_PAGE_LIMIT_EXCEEDED",
                False,
                "Recheck pages exceed the configured limit.",
            )
        if any(
            len([page for page in segments if page.image_data_url is not None]) != 1
            or not self._is_valid_png_data_url(
                next(page.image_data_url for page in segments if page.image_data_url is not None)
            )
            for segments in grouped.values()
        ):
            raise ProviderFailure(
                "OPENAI_RECHECK_IMAGE_REQUIRED",
                False,
                "Every selected recheck page requires a valid PNG image.",
            )
        return pages

    def _is_valid_png_data_url(self, value: object) -> bool:
        if not isinstance(value, str):
            return False
        match = _PNG_DATA_URL.fullmatch(value)
        if match is None:
            return False
        encoded = match.group(1)
        if not encoded or len(encoded) > self._max_image_encoded_bytes:
            return False
        # Base64 expands by at most 4/3. Reject before decoding rather than
        # letting an attacker force an unbounded temporary allocation.
        decoded_upper_bound = (len(encoded) // 4) * 3 + 3
        if decoded_upper_bound > self._max_image_decoded_bytes:
            return False
        try:
            decoded = base64.b64decode(encoded, validate=True)
            if len(decoded) > self._max_image_decoded_bytes:
                return False
            from PIL import Image

            with Image.open(io.BytesIO(decoded)) as image:
                if (
                    image.format != "PNG"
                    or image.width < 1
                    or image.height < 1
                    or image.width * image.height > self._max_image_pixels
                ):
                    return False
                image.verify()
        except Exception:
            # This predicate is the final image-input boundary. Pillow can
            # raise dedicated validation exceptions (including
            # DecompressionBombError) that do not inherit from OSError; every
            # such failure must become a stable invalid-image result.
            return False
        return decoded.startswith(_PNG_SIGNATURE) and decoded.endswith(b"IEND\xaeB`\x82")

    @staticmethod
    def _page_content_blocks(pages, *, include_images: bool) -> list[dict[str, str]]:
        blocks: list[dict[str, str]] = []
        for page in pages:
            blocks.append(
                {
                    "type": "input_text",
                    "text": (
                        f"document_key={page.document_key}; pdf_page={page.pdf_page}\n"
                        f"{page.text}"
                    ),
                }
            )
            if include_images and page.image_data_url is not None:
                blocks.append(
                    {
                        "type": "input_image",
                        "image_url": page.image_data_url,
                        "detail": "high",
                    }
                )
        return blocks

    async def _run(
        self,
        *,
        pass_id: str,
        model: str,
        prompt: str,
        content_blocks: list[dict[str, str]],
        output_type: type[T],
        max_output_tokens: int,
    ) -> ProviderCallResult[T]:
        self._validate_output_allowance(max_output_tokens)
        self._require_model_rate(model)
        self._require_client()
        self._validate_input_image_blocks(content_blocks)
        primary_input = self._request_input(prompt, content_blocks, model=model)
        repair_prompt = (
            f"{prompt}\n\nSchema-only repair: return a fresh response "
            "matching the supplied schema."
        )
        repair_input = self._request_input(repair_prompt, content_blocks, model=model)
        reservation = self._request_reservation(
            pass_id, model, prompt, content_blocks, output_type, max_output_tokens
        )
        self._validate_reservation_budget(model, reservation, max_output_tokens)
        try:
            first = await self._attempt(
                pass_id=pass_id,
                model=model,
                input_payload=primary_input,
                developer_prompt=prompt,
                output_type=output_type,
                max_output_tokens=max_output_tokens,
                attempt_no=1,
                stage="provider",
            )
            try:
                self._ensure_actual_usage_within_budget(first.usage)
            except ProviderFailure as error:
                error.set_attempt_metadata((first.metadata,))
                error.usage_complete = True
                error.attempt_count = 1
                raise
            return ProviderCallResult(
                output=first.output,
                usage=first.usage,
                metadata=first.metadata,
                attempt_metadata=(first.metadata,),
            )
        except _SchemaFailure as first_failure:
            first_usage = first_failure.usage
            first_metadata = first_failure.metadata
            first_usage_complete = first_failure.usage_complete
            if first_usage is None:
                # The logical call has one aggregate output ceiling.  Without
                # trustworthy first-leg usage, a repair could silently spend a
                # second full output envelope, so fail closed and retain the
                # pipeline's conservative reservation.
                raise ProviderFailure(
                    PROVIDER_SCHEMA_FAILURE,
                    False,
                    "Structured output was invalid.",
                    usage=None,
                    attempt_metadata=_metadata_tuple(first_metadata),
                    usage_complete=False,
                    attempt_count=1,
                ) from None
            if first_usage is not None:
                try:
                    self._ensure_actual_usage_within_budget(first_usage)
                except ProviderFailure as error:
                    error.set_attempt_metadata(_metadata_tuple(first_metadata))
                    error.usage_complete = first_usage_complete
                    error.attempt_count = 1
                    raise
            repair_allowance = max_output_tokens
            if first_usage is not None:
                repair_allowance -= first_usage.output_tokens
            if repair_allowance <= 0:
                raise ProviderFailure(
                    PROVIDER_SCHEMA_FAILURE,
                    False,
                    "Structured output was invalid.",
                    usage=first_usage,
                    attempt_metadata=_metadata_tuple(first_metadata),
                    usage_complete=first_usage_complete,
                    attempt_count=1,
                ) from None

            try:
                second = await self._attempt(
                    pass_id=pass_id,
                    model=model,
                    input_payload=repair_input,
                    developer_prompt=repair_prompt,
                    output_type=output_type,
                    max_output_tokens=repair_allowance,
                    attempt_no=2,
                    stage="schema_repair",
                )
            except _SchemaFailure as second_failure:
                aggregate = self._aggregate_usage(
                    first_usage, second_failure.usage, attempt_count=2
                )
                if aggregate is not None:
                    try:
                        self._ensure_actual_usage_within_budget(aggregate)
                    except ProviderFailure as error:
                        error.set_attempt_metadata(
                            _metadata_tuple(first_metadata, second_failure.metadata)
                        )
                        error.usage_complete = (
                            first_usage_complete and second_failure.usage_complete
                        )
                        error.attempt_count = 2
                        raise
                raise ProviderFailure(
                    PROVIDER_SCHEMA_FAILURE,
                    False,
                    "Structured output was invalid.",
                    usage=aggregate,
                    attempt_metadata=_metadata_tuple(first_metadata, second_failure.metadata),
                    usage_complete=first_usage_complete and second_failure.usage_complete,
                    attempt_count=2,
                ) from None
            except ProviderFailure as error:
                aggregate = self._aggregate_usage(
                    first_usage, error.usage, attempt_count=2
                )
                if aggregate is not None:
                    try:
                        self._ensure_actual_usage_within_budget(aggregate)
                    except ProviderFailure as budget_error:
                        budget_error.set_attempt_metadata(
                            _metadata_tuple(first_metadata, *error.attempt_metadata)
                        )
                        budget_error.usage_complete = (
                            first_usage_complete and error.usage_complete
                        )
                        budget_error.attempt_count = 2
                        raise
                error.usage = aggregate
                error.set_attempt_metadata(_metadata_tuple(first_metadata, *error.attempt_metadata))
                error.usage_complete = first_usage_complete and error.usage_complete
                error.attempt_count = 2
                raise
            try:
                aggregate = self._checked_aggregate_usage(
                    first_usage, second.usage, attempt_count=2
                )
            except ProviderFailure as error:
                error.set_attempt_metadata(_metadata_tuple(first_metadata, second.metadata))
                error.usage_complete = first_usage_complete
                error.attempt_count = 2
                raise
            return ProviderCallResult(
                output=second.output,
                usage=aggregate,
                metadata=second.metadata,
                attempt_metadata=_metadata_tuple(first_metadata, second.metadata),
                usage_complete=first_usage_complete,
            )

    def _validate_output_allowance(self, max_output_tokens: int) -> None:
        if isinstance(max_output_tokens, bool) or max_output_tokens <= 0:
            raise ProviderFailure(
                "OPENAI_OUTPUT_ALLOWANCE_INVALID", False, "Output allowance must be positive."
            )
        if max_output_tokens > self._max_output_tokens_per_call:
            raise ProviderFailure(
                "OPENAI_OUTPUT_ALLOWANCE_EXCEEDED",
                False,
                "Output allowance exceeds the configured per-call limit.",
            )

    def _require_client(self) -> None:
        if self._client is None:
            raise ProviderFailure(
                "OPENAI_API_KEY_MISSING", False, "OpenAI provider credentials are not configured."
            )

    def _require_model_rate(self, model: str) -> None:
        if model not in self._rates:
            raise ProviderFailure(
                "MODEL_RATE_NOT_CONFIGURED", False, "The model rate is not configured."
            )

    def _request_input(
        self,
        prompt: str,
        content_blocks: list[dict[str, str]],
        *,
        model: str,
    ) -> list[dict[str, Any]]:
        developer_block: dict[str, Any] = {"type": "input_text", "text": prompt}
        if self._uses_explicit_cache(model):
            # The SDK has no typed field yet; the compatibility test verifies
            # this raw block survives its request transformation.
            developer_block["prompt_cache_breakpoint"] = {"mode": "explicit"}
        return [
            {"role": "developer", "content": [developer_block]},
            {"role": "user", "content": content_blocks},
        ]

    def _request_reservation(
        self,
        pass_id: str,
        model: str,
        prompt: str,
        content_blocks: list[dict[str, str]],
        output_type: type[StrictModel],
        max_output_tokens: int,
    ) -> ProviderReservationEstimate:
        """Build both possible requests once and estimate their true envelope."""
        primary = self._request_input(prompt, content_blocks, model=model)
        repair = self._request_input(
            f"{prompt}\n\nSchema-only repair: return a fresh response matching the supplied schema.", content_blocks, model=model
        )
        first = self._estimate_input_tokens(
            pass_id, model, prompt, primary, output_type, max_output_tokens
        )
        repair_estimate = self._estimate_input_tokens(
            pass_id,
            model,
            f"{prompt}\n\nSchema-only repair: return a fresh response matching the supplied schema.",
            repair,
            output_type,
            max_output_tokens,
        )
        return ProviderReservationEstimate(
            input_tokens=first + repair_estimate,
            attempt_count=2,
            attempt_input_tokens=(first, repair_estimate),
        )

    def _estimate_input_tokens(
        self,
        pass_id: str,
        model: str,
        developer_prompt: str,
        input_payload: list[dict[str, Any]],
        output_type: type[StrictModel],
        max_output_tokens: int,
    ) -> int:
        rates = self._rates.get(model)
        if rates is None:
            raise ProviderFailure(
                "MODEL_RATE_NOT_CONFIGURED", False, "The model rate is not configured."
            )
        try:
            token_encoder = self._token_encoder or tiktoken.get_encoding("o200k_base")
            encoded = token_encoder.encode(
                json.dumps(
                    self._request_envelope(
                        pass_id=pass_id,
                        model=model,
                        developer_prompt=developer_prompt,
                        input_payload=self._input_for_text_estimation(input_payload),
                        output_type=output_type,
                        max_output_tokens=max_output_tokens,
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        except Exception as error:
            raise ProviderFailure(
                "OPENAI_INPUT_ESTIMATE_FAILED", False, "Input token estimation failed."
            ) from error
        estimated_input = len(encoded) + self._reserved_image_input_tokens(
            model, input_payload
        )
        if model == "gpt-5.6-luna" and estimated_input > _LUNA_SHORT_CONTEXT_MAX_INPUT_TOKENS:
            raise ProviderFailure(
                "OPENAI_LONG_CONTEXT_RATE_UNCONFIGURED",
                False,
                "Long-context Luna pricing is not configured.",
            )
        return estimated_input

    def _request_envelope(
        self,
        *,
        pass_id: str,
        model: str,
        developer_prompt: str,
        input_payload: list[dict[str, Any]],
        output_type: type[StrictModel],
        max_output_tokens: int,
    ) -> dict[str, Any]:
        """The complete, content-free request shape used by live and reserve paths."""
        envelope: dict[str, Any] = {
            "model": model,
            "input": input_payload,
            "text": {"format": self._sdk_text_format(output_type)},
            "store": False,
            "max_output_tokens": max_output_tokens,
            "reasoning": {"effort": self._reasoning_effort(model)},
        }
        if self._uses_explicit_cache(model):
            fingerprints = self._fingerprints(developer_prompt, output_type)
            envelope["prompt_cache_key"] = self._prompt_cache_key(
                model, pass_id, fingerprints
            )
            envelope["prompt_cache_options"] = {"mode": "explicit", "ttl": "30m"}
        return envelope

    def _fingerprints(
        self, developer_prompt: str, output_type: type[StrictModel]
    ) -> StaticContractFingerprints:
        return fingerprint_static_contract(
            developer_prompt=developer_prompt,
            schema=self._sdk_text_format(output_type),
            harness=_STATIC_HARNESS,
        )

    def _prompt_cache_key(
        self, model: str, pass_id: str, fingerprints: StaticContractFingerprints
    ) -> str:
        material = "|".join(
            (
                model,
                pass_id,
                self._prompt_version,
                fingerprints.prompt_sha256,
                self._schema_version,
                fingerprints.schema_sha256,
                self._harness_version,
                fingerprints.harness_sha256,
            )
        )
        return "decl-" + sha256(material.encode("ascii")).hexdigest()[:59]

    def _uses_explicit_cache(self, model: str) -> bool:
        return model == self._primary_model == "gpt-5.6-luna"

    def _reasoning_effort(self, model: str) -> str:
        if model == self._primary_model:
            return self._primary_reasoning_effort
        if model == self._escalation_model:
            return self._escalation_reasoning_effort
        raise ProviderFailure(
            "MODEL_RATE_NOT_CONFIGURED", False, "The model rate is not configured."
        )

    @staticmethod
    def _sdk_text_format(output_type: type[StrictModel]) -> dict[str, Any]:
        """Use the installed SDK's exact Structured Outputs transformation."""
        try:
            from openai.lib._parsing._responses import type_to_text_format_param

            transformed = type_to_text_format_param(output_type)
        except Exception as error:
            raise ProviderFailure(
                "OPENAI_RESERVATION_ESTIMATE_INVALID",
                False,
                "The SDK structured-output format cannot be estimated safely.",
            ) from error
        if (
            not isinstance(transformed, dict)
            or transformed.get("type") != "json_schema"
            or transformed.get("strict") is not True
            or not isinstance(transformed.get("name"), str)
            or not transformed["name"]
            or not isinstance(transformed.get("schema"), dict)
        ):
            raise ProviderFailure(
                "OPENAI_RESERVATION_ESTIMATE_INVALID",
                False,
                "The SDK structured-output format cannot be estimated safely.",
            )
        return transformed

    def _validate_reservation_budget(
        self,
        model: str,
        reservation: ProviderReservationEstimate,
        max_output_tokens: int,
    ) -> None:
        if (
            isinstance(reservation.input_tokens, bool)
            or not isinstance(reservation.input_tokens, int)
            or reservation.input_tokens < 0
            or reservation.attempt_count != 2
        ):
            raise ProviderFailure(
                "OPENAI_RESERVATION_ESTIMATE_INVALID",
                False,
                "The provider request cannot be estimated safely.",
            )
        if reservation.input_tokens > self._max_input_tokens:
            raise ProviderFailure("OPENAI_INPUT_BUDGET_EXCEEDED", False, "Input token budget would be exceeded.")
        rates = self._rates.get(model)
        assert rates is not None
        input_rate = max(
            rates.input_usd_per_million,
            rates.cached_input_usd_per_million,
            (
                rates.input_usd_per_million * rates.cache_write_multiplier
                if rates.cache_write_multiplier is not None
                else rates.input_usd_per_million
            ),
        )
        cost = (
            Decimal(reservation.input_tokens) * input_rate
            + Decimal(max_output_tokens) * rates.output_usd_per_million
        ) / _MILLION
        if cost > self._max_cost_usd:
            raise ProviderFailure("OPENAI_COST_BUDGET_EXCEEDED", False, "Cost budget would be exceeded.")

    @staticmethod
    def _input_for_text_estimation(
        input_payload: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Exclude image base64 from BPE counting; reserve vision tokens instead."""
        normalized = json.loads(json.dumps(input_payload))
        for message in normalized:
            for block in message.get("content", []):
                if block.get("type") == "input_image":
                    block["image_url"] = "<image-data-url-omitted>"
        return normalized

    @staticmethod
    def _reserved_image_input_tokens(
        model: str, input_payload: list[dict[str, Any]]
    ) -> int:
        images = [
            block
            for message in input_payload
            for block in message.get("content", [])
            if block.get("type") == "input_image"
        ]
        if not images:
            return 0
        if model != "gpt-5.4-mini" or any(
            image.get("detail") != "high" for image in images
        ):
            raise ProviderFailure(
                "OPENAI_IMAGE_RATE_NOT_CONFIGURED",
                False,
                "Image token reservation is not configured for this model or detail.",
            )
        return len(images) * _GPT_54_MINI_HIGH_IMAGE_INPUT_TOKENS

    async def _attempt(
        self,
        *,
        pass_id: str,
        model: str,
        input_payload: list[dict[str, Any]],
        developer_prompt: str,
        output_type: type[T],
        max_output_tokens: int,
        attempt_no: int,
        stage: str,
    ) -> _AttemptSuccess:
        started = self._monotonic_clock()
        fingerprints = self._fingerprints(developer_prompt, output_type)
        request_kwargs = self._request_envelope(
            pass_id=pass_id,
            model=model,
            developer_prompt=developer_prompt,
            input_payload=input_payload,
            output_type=output_type,
            max_output_tokens=max_output_tokens,
        )
        try:
            response = await self._client.responses.parse(
                model=request_kwargs["model"],
                input=request_kwargs["input"],
                text_format=output_type,
                store=request_kwargs["store"],
                timeout=self._timeout_seconds,
                max_output_tokens=request_kwargs["max_output_tokens"],
                reasoning=request_kwargs["reasoning"],
                **{
                    key: value
                    for key, value in request_kwargs.items()
                    if key in {"prompt_cache_key", "prompt_cache_options"}
                },
            )
        except Exception as error:
            elapsed = self._latency_ms(started)
            if isinstance(error, ContentFilterFinishReasonError):
                metadata = self._metadata(
                    pass_id=pass_id,
                    attempt_no=attempt_no,
                    stage=stage,
                    model=model,
                    fingerprints=fingerprints,
                    usage=None,
                    latency_ms=elapsed,
                    failure_class=PROVIDER_REFUSAL,
                )
                raise ProviderFailure(
                    PROVIDER_REFUSAL,
                    False,
                    "The provider refused the request.",
                    attempt_metadata=(metadata,),
                ) from None
            if isinstance(error, LengthFinishReasonError):
                metadata = self._metadata(
                    pass_id=pass_id,
                    attempt_no=attempt_no,
                    stage=stage,
                    model=model,
                    fingerprints=fingerprints,
                    usage=None,
                    latency_ms=elapsed,
                    failure_class=PROVIDER_INCOMPLETE,
                )
                raise ProviderFailure(
                    PROVIDER_INCOMPLETE,
                    True,
                    "The provider response was incomplete.",
                    attempt_metadata=(metadata,),
                ) from None
            if isinstance(error, APITimeoutError):
                metadata = self._metadata(
                    pass_id=pass_id,
                    attempt_no=attempt_no,
                    stage=stage,
                    model=model,
                    fingerprints=fingerprints,
                    usage=None,
                    latency_ms=elapsed,
                    failure_class=PROVIDER_TIMEOUT,
                )
                raise ProviderFailure(
                    PROVIDER_TIMEOUT,
                    True,
                    "OpenAI request timed out.",
                    attempt_metadata=(metadata,),
                ) from None
            if isinstance(error, APIConnectionError):
                metadata = self._metadata(
                    pass_id=pass_id,
                    attempt_no=attempt_no,
                    stage=stage,
                    model=model,
                    fingerprints=fingerprints,
                    usage=None,
                    latency_ms=elapsed,
                    failure_class=PROVIDER_CONNECTION_ERROR,
                )
                raise ProviderFailure(
                    PROVIDER_CONNECTION_ERROR,
                    True,
                    "OpenAI connection failed.",
                    attempt_metadata=(metadata,),
                ) from None
            if isinstance(error, RateLimitError):
                code = "PROVIDER_RATE_LIMIT"
                metadata = self._metadata(
                    pass_id=pass_id,
                    attempt_no=attempt_no,
                    stage=stage,
                    model=model,
                    fingerprints=fingerprints,
                    usage=None,
                    latency_ms=elapsed,
                    failure_class=code,
                )
                raise ProviderFailure(code, True, "OpenAI rate limit exceeded.", attempt_metadata=(metadata,)) from None
            status_code = getattr(error, "status_code", None)
            if (
                isinstance(status_code, int)
                and not isinstance(status_code, bool)
                and 500 <= status_code <= 599
            ):
                code = "PROVIDER_SERVER_ERROR"
                metadata = self._metadata(
                    pass_id=pass_id,
                    attempt_no=attempt_no,
                    stage=stage,
                    model=model,
                    fingerprints=fingerprints,
                    usage=None,
                    latency_ms=elapsed,
                    failure_class=code,
                )
                raise ProviderFailure(code, True, "OpenAI server request failed.", attempt_metadata=(metadata,)) from None
            if self._is_schema_exception(error):
                metadata = self._metadata(
                    pass_id=pass_id,
                    attempt_no=attempt_no,
                    stage=stage,
                    model=model,
                    fingerprints=fingerprints,
                    usage=None,
                    latency_ms=elapsed,
                    failure_class=PROVIDER_SCHEMA_FAILURE,
                )
                raise _SchemaFailure(metadata=metadata) from None
            code = "OPENAI_REQUEST_FAILED"
            metadata = self._metadata(
                pass_id=pass_id,
                attempt_no=attempt_no,
                stage=stage,
                model=model,
                fingerprints=fingerprints,
                usage=None,
                latency_ms=elapsed,
                failure_class=code,
            )
            raise ProviderFailure(
                code,
                self._is_retryable_exception(error),
                "OpenAI request failed.",
                attempt_metadata=(metadata,),
            ) from None

        elapsed = self._latency_ms(started)
        returned_model_id = _safe_response_identifier(getattr(response, "model", None))
        request_id = _safe_response_identifier(getattr(response, "_request_id", None))
        try:
            usage = self._validated_usage(
                getattr(response, "usage", None), model, max_output_tokens
            )
        except ProviderFailure as error:
            if error.code == "OPENAI_USAGE_MISSING":
                usage = None
            else:
                metadata = self._metadata(
                    pass_id=pass_id,
                    attempt_no=attempt_no,
                    stage=stage,
                    model=model,
                    fingerprints=fingerprints,
                    usage=error.usage,
                    latency_ms=elapsed,
                    failure_class=error.code,
                    returned_model_id=returned_model_id,
                    request_id=request_id,
                )
                error.set_attempt_metadata((metadata,))
                error.usage_complete = error.usage is not None
                error.attempt_count = attempt_no
                raise
        if getattr(response, "refusal", None) is not None or _response_has_refusal(response):
            metadata = self._metadata(
                pass_id=pass_id,
                attempt_no=attempt_no,
                stage=stage,
                model=model,
                fingerprints=fingerprints,
                usage=usage,
                latency_ms=elapsed,
                failure_class=PROVIDER_REFUSAL,
                returned_model_id=returned_model_id,
                request_id=request_id,
            )
            raise ProviderFailure(
                PROVIDER_REFUSAL,
                False,
                "The provider refused the request.",
                usage=usage,
                attempt_metadata=(metadata,),
            )
        if getattr(response, "status", "completed") != "completed":
            metadata = self._metadata(
                pass_id=pass_id,
                attempt_no=attempt_no,
                stage=stage,
                model=model,
                fingerprints=fingerprints,
                usage=usage,
                latency_ms=elapsed,
                failure_class=PROVIDER_INCOMPLETE,
                returned_model_id=returned_model_id,
                request_id=request_id,
            )
            raise ProviderFailure(
                PROVIDER_INCOMPLETE,
                True,
                "The provider response was incomplete.",
                usage=usage,
                attempt_metadata=(metadata,),
            )
        try:
            parsed = response.output_parsed
        except Exception:
            metadata = self._metadata(
                pass_id=pass_id,
                attempt_no=attempt_no,
                stage=stage,
                model=model,
                fingerprints=fingerprints,
                usage=usage,
                latency_ms=elapsed,
                failure_class=PROVIDER_SCHEMA_FAILURE,
                returned_model_id=returned_model_id,
                request_id=request_id,
            )
            raise _SchemaFailure(usage, metadata) from None
        if parsed is None or not isinstance(parsed, output_type):
            metadata = self._metadata(
                pass_id=pass_id,
                attempt_no=attempt_no,
                stage=stage,
                model=model,
                fingerprints=fingerprints,
                usage=usage,
                latency_ms=elapsed,
                failure_class=PROVIDER_SCHEMA_FAILURE,
                returned_model_id=returned_model_id,
                request_id=request_id,
            )
            raise _SchemaFailure(usage, metadata)
        if usage is None:
            metadata = self._metadata(
                pass_id=pass_id,
                attempt_no=attempt_no,
                stage=stage,
                model=model,
                fingerprints=fingerprints,
                usage=None,
                latency_ms=elapsed,
                failure_class="OPENAI_USAGE_MISSING",
                returned_model_id=returned_model_id,
                request_id=request_id,
            )
            raise ProviderFailure(
                "OPENAI_USAGE_MISSING",
                False,
                "OpenAI response usage is missing.",
                attempt_metadata=(metadata,),
                usage_complete=False,
                attempt_count=attempt_no,
            )
        metadata = self._metadata(
            pass_id=pass_id,
            attempt_no=attempt_no,
            stage=stage,
            model=model,
            fingerprints=fingerprints,
            usage=usage,
            latency_ms=elapsed,
            failure_class=None,
            returned_model_id=returned_model_id,
            request_id=request_id,
        )
        return _AttemptSuccess(output=parsed, usage=usage, metadata=metadata)

    def _latency_ms(self, started: float) -> int:
        return max(0, int((self._monotonic_clock() - started) * 1000))

    def _metadata(
        self,
        *,
        pass_id: str,
        attempt_no: int,
        stage: str,
        model: str,
        fingerprints: StaticContractFingerprints,
        usage: ProviderUsage | None,
        latency_ms: int,
        failure_class: str | None,
        returned_model_id: str | None = None,
        request_id: str | None = None,
    ) -> ProviderPassMetadata:
        observed = usage or ProviderUsage(0, 0, Decimal("0"), 1)
        reason_codes = (
            (failure_class,)
            if failure_class in {
                PROVIDER_REFUSAL,
                PROVIDER_INCOMPLETE,
                PROVIDER_TIMEOUT,
                PROVIDER_SCHEMA_FAILURE,
            }
            else ()
        )
        return ProviderPassMetadata.from_values(
            pass_id=pass_id,
            attempt_no=attempt_no,
            stage=stage,
            configured_model=model,
            returned_model_id=returned_model_id,
            reasoning_effort=self._reasoning_effort(model),
            request_id=request_id,
            prompt_version=self._prompt_version,
            prompt_sha256=fingerprints.prompt_sha256,
            schema_version=self._schema_version,
            schema_sha256=fingerprints.schema_sha256,
            harness_version=self._harness_version,
            harness_sha256=fingerprints.harness_sha256,
            input_tokens=observed.input_tokens,
            cached_input_tokens=observed.cached_input_tokens,
            cache_write_input_tokens=observed.cache_write_input_tokens,
            output_tokens=observed.output_tokens,
            estimated_cost_usd=observed.cost_usd,
            latency_ms=latency_ms,
            retry_count=attempt_no - 1,
            route_action="HUMAN_REVIEW" if failure_class else "NONE",
            route_reason_codes=reason_codes,
            failure_class=failure_class,
        )

    def _validate_input_image_blocks(self, content_blocks: Sequence[dict[str, object]]) -> None:
        for block in content_blocks:
            if block.get("type") != "input_image":
                continue
            if not self._is_valid_png_data_url(block.get("image_url")):
                raise ProviderFailure(
                    "OPENAI_IMAGE_INPUT_INVALID",
                    False,
                    "Image input must be a valid PNG data URL.",
                )

    def _validated_usage(
        self, raw_usage: object, model: str, max_output_tokens: int
    ) -> ProviderUsage:
        rates = self._rates.get(model)
        if rates is None:
            raise ProviderFailure(
                "MODEL_RATE_NOT_CONFIGURED", False, "The model rate is not configured."
            )
        if raw_usage is None:
            raise ProviderFailure("OPENAI_USAGE_MISSING", False, "OpenAI response usage is missing.")
        input_tokens = getattr(raw_usage, "input_tokens", None)
        output_tokens = getattr(raw_usage, "output_tokens", None)
        input_details = getattr(raw_usage, "input_tokens_details", None)
        cached_input_tokens = getattr(input_details, "cached_tokens", None)
        cache_write_input_tokens = getattr(input_details, "cache_write_tokens", None)
        if (
            isinstance(input_tokens, bool)
            or isinstance(output_tokens, bool)
            or isinstance(cached_input_tokens, bool)
            or isinstance(cache_write_input_tokens, bool)
            or not isinstance(input_tokens, int)
            or not isinstance(output_tokens, int)
            or not isinstance(cached_input_tokens, int)
            or not isinstance(cache_write_input_tokens, int)
            or input_tokens < 0
            or output_tokens < 0
            or cached_input_tokens < 0
            or cache_write_input_tokens < 0
            or cached_input_tokens + cache_write_input_tokens > input_tokens
        ):
            raise ProviderFailure("OPENAI_USAGE_INVALID", False, "OpenAI response usage is invalid.")
        if cache_write_input_tokens and rates.cache_write_multiplier is None:
            raise ProviderFailure(
                "OPENAI_CACHE_WRITE_UNSUPPORTED",
                False,
                "Explicit cache writes are not supported for this model.",
            )
        if (
            model == "gpt-5.6-luna"
            and input_tokens > _LUNA_SHORT_CONTEXT_MAX_INPUT_TOKENS
        ):
            usage = ProviderUsage(
                input_tokens,
                output_tokens,
                self._cost_usd(
                    input_tokens,
                    output_tokens,
                    rates,
                    cached_input_tokens=cached_input_tokens,
                    cache_write_input_tokens=cache_write_input_tokens,
                    input_multiplier=Decimal("2"),
                    output_multiplier=Decimal("1.5"),
                ),
                1,
                cached_input_tokens,
                cache_write_input_tokens,
            )
            raise ProviderFailure(
                "OPENAI_LONG_CONTEXT_RATE_UNCONFIGURED",
                False,
                "Long-context Luna pricing is not configured.",
                usage,
            )
        cost = self._cost_usd(
            input_tokens,
            output_tokens,
            rates,
            cached_input_tokens=cached_input_tokens,
            cache_write_input_tokens=cache_write_input_tokens,
        )
        usage = ProviderUsage(
            input_tokens,
            output_tokens,
            cost,
            1,
            cached_input_tokens,
            cache_write_input_tokens,
        )
        if output_tokens > max_output_tokens:
            raise ProviderFailure(
                "OPENAI_OUTPUT_BUDGET_MISMATCH",
                False,
                "OpenAI response exceeded its output allowance.",
                usage,
            )
        return usage

    @staticmethod
    def _cost_usd(
        input_tokens: int,
        output_tokens: int,
        rates: _Rates,
        *,
        cached_input_tokens: int = 0,
        cache_write_input_tokens: int = 0,
        input_multiplier: Decimal = Decimal("1"),
        output_multiplier: Decimal = Decimal("1"),
    ) -> Decimal:
        return (
            Decimal(input_tokens - cached_input_tokens - cache_write_input_tokens)
            * rates.input_usd_per_million
            * input_multiplier
            + Decimal(cached_input_tokens)
            * rates.cached_input_usd_per_million
            * input_multiplier
            + Decimal(cache_write_input_tokens)
            * rates.input_usd_per_million
            * (rates.cache_write_multiplier or Decimal("0"))
            * input_multiplier
            + Decimal(output_tokens)
            * rates.output_usd_per_million
            * output_multiplier
        ) / _MILLION

    def _ensure_actual_usage_within_budget(self, usage: ProviderUsage) -> None:
        if usage.input_tokens > self._max_input_tokens:
            raise ProviderFailure(
                "OPENAI_INPUT_BUDGET_EXCEEDED",
                False,
                "Input token budget was exceeded.",
                usage,
            )
        if usage.cost_usd > self._max_cost_usd:
            raise ProviderFailure(
                "OPENAI_COST_BUDGET_EXCEEDED",
                False,
                "Cost budget was exceeded.",
                usage,
            )

    def _checked_aggregate_usage(
        self, *usages: ProviderUsage | None, attempt_count: int | None = None
    ) -> ProviderUsage:
        aggregate = self._aggregate_usage(*usages, attempt_count=attempt_count)
        assert aggregate is not None
        self._ensure_actual_usage_within_budget(aggregate)
        return aggregate

    @staticmethod
    def _aggregate_usage(
        *usages: ProviderUsage | None, attempt_count: int | None = None
    ) -> ProviderUsage | None:
        present = [usage for usage in usages if usage is not None]
        if not present:
            return None
        return ProviderUsage(
            input_tokens=sum(usage.input_tokens for usage in present),
            output_tokens=sum(usage.output_tokens for usage in present),
            cost_usd=sum((usage.cost_usd for usage in present), Decimal("0")),
            attempt_count=(
                attempt_count
                if attempt_count is not None
                else sum(usage.attempt_count for usage in present)
            ),
            cached_input_tokens=sum(usage.cached_input_tokens for usage in present),
            cache_write_input_tokens=sum(
                usage.cache_write_input_tokens for usage in present
            ),
        )

    @staticmethod
    def _is_schema_exception(error: Exception) -> bool:
        # The SDK may surface a local Pydantic parse exception rather than a
        # response object. No body is retained or echoed for repair.
        return isinstance(
            error,
            (
                APIResponseValidationError,
                JSONDecodeError,
                ValidationError,
            ),
        )

    @staticmethod
    def _is_retryable_exception(error: Exception) -> bool:
        status = getattr(error, "status_code", None)
        return (
            isinstance(error, (APITimeoutError, APIConnectionError, RateLimitError))
            or (
                isinstance(error, APIStatusError)
                and isinstance(status, int)
                and 500 <= status <= 599
            )
            # Mocks or an SDK compatibility layer may only provide status.
            or (isinstance(status, int) and 500 <= status <= 599)
        )
