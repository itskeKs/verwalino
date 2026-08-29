"""Bounded OpenAI Responses adapter for declaration extraction.

This module intentionally contains no logging of request/response data.  The
pipeline receives only typed output and sanitized aggregate usage.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from json import JSONDecodeError
from typing import Any, TypeVar

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
    ProviderCallResult,
    ProviderFailure,
    ProviderReservationEstimate,
    ProviderUsage,
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


@dataclass(frozen=True)
class _Rates:
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal


class _SchemaFailure(Exception):
    def __init__(self, usage: ProviderUsage | None = None) -> None:
        self.usage = usage


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
        # Tests inject a local encoder so mocked SDK verification never needs a
        # tokenizer download. Production uses the official tokenizer package.
        self._token_encoder = token_encoder
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
            )
        if self._escalation_model == "gpt-5.4-mini":
            rates[self._escalation_model] = _Rates(
                configured.declaration_escalation_input_usd_per_million,
                configured.declaration_escalation_output_usd_per_million,
            )
        return rates

    def _validate_configured_rates(self) -> None:
        for rates in self._rates.values():
            if (
                not rates.input_usd_per_million.is_finite()
                or rates.input_usd_per_million <= 0
                or not rates.output_usd_per_million.is_finite()
                or rates.output_usd_per_million <= 0
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
        pages = self._sorted_pages(documents)[: self._max_recheck_pages]
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
            return self._request_reservation(
                self._primary_model,
                INVENTORY_PROMPT,
                self._page_content_blocks(self._sorted_pages(documents), include_images=False),
                DocumentInventoryOutput,
                max_output_tokens,
            )
        if method == "extract_core":
            return self._request_reservation(
                self._primary_model,
                CORE_EXTRACTION_PROMPT,
                self._page_content_blocks(self._sorted_pages(documents), include_images=False),
                CoreExtractionOutput,
                max_output_tokens,
            )
        if method == "extract_rules":
            return self._request_reservation(
                self._primary_model,
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
                self._primary_model,
                RECONCILIATION_PROMPT,
                [{"type": "input_text", "text": payload}],
                ReconciliationOutput,
                max_output_tokens,
            )
        if method == "recheck":
            pages = self._sorted_pages(documents)[: self._max_recheck_pages]
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
                self._escalation_model,
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
        model: str,
        prompt: str,
        documents: Sequence[DocumentInput],
        output_type: type[T],
        max_output_tokens: int,
        include_images: bool,
    ) -> ProviderCallResult[T]:
        return await self._run(
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
        model: str,
        prompt: str,
        content_blocks: list[dict[str, str]],
        output_type: type[T],
        max_output_tokens: int,
    ) -> ProviderCallResult[T]:
        self._validate_output_allowance(max_output_tokens)
        self._require_model_rate(model)
        self._require_client()
        primary_input = self._request_input(prompt, content_blocks)
        repair_prompt = (
            f"{prompt}\n\nSchema-only repair: return a fresh response "
            "matching the supplied schema."
        )
        repair_input = self._request_input(repair_prompt, content_blocks)
        reservation = self._request_reservation(
            model, prompt, content_blocks, output_type, max_output_tokens
        )
        self._validate_reservation_budget(model, reservation, max_output_tokens)
        try:
            output, first_usage = await self._attempt(
                model=model,
                input_payload=primary_input,
                output_type=output_type,
                max_output_tokens=max_output_tokens,
            )
            self._ensure_actual_usage_within_budget(first_usage)
            return ProviderCallResult(output=output, usage=first_usage)
        except _SchemaFailure as first_failure:
            first_usage = first_failure.usage
            if first_usage is not None:
                self._ensure_actual_usage_within_budget(first_usage)
            repair_allowance = max_output_tokens
            if first_usage is not None:
                repair_allowance -= first_usage.output_tokens
            if repair_allowance <= 0:
                raise ProviderFailure(
                    "OPENAI_SCHEMA_INVALID",
                    False,
                    "Structured output was invalid.",
                    usage=first_usage,
                ) from None

            try:
                output, second_usage = await self._attempt(
                    model=model,
                    input_payload=repair_input,
                    output_type=output_type,
                    max_output_tokens=repair_allowance,
                )
            except _SchemaFailure as second_failure:
                aggregate = self._aggregate_usage(
                    first_usage, second_failure.usage, attempt_count=2
                )
                if aggregate is not None:
                    self._ensure_actual_usage_within_budget(aggregate)
                raise ProviderFailure(
                    "OPENAI_SCHEMA_INVALID",
                    False,
                    "Structured output was invalid.",
                    usage=aggregate,
                ) from None
            except ProviderFailure as error:
                aggregate = self._aggregate_usage(
                    first_usage, error.usage, attempt_count=2
                )
                if aggregate is not None:
                    self._ensure_actual_usage_within_budget(aggregate)
                error.usage = aggregate
                raise
            return ProviderCallResult(
                output=output,
                usage=self._checked_aggregate_usage(
                    first_usage, second_usage, attempt_count=2
                ),
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

    @staticmethod
    def _request_input(
        prompt: str, content_blocks: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        return [
            {"role": "developer", "content": [{"type": "input_text", "text": prompt}]},
            {"role": "user", "content": content_blocks},
        ]

    def _request_reservation(
        self,
        model: str,
        prompt: str,
        content_blocks: list[dict[str, str]],
        output_type: type[StrictModel],
        max_output_tokens: int,
    ) -> ProviderReservationEstimate:
        """Build both possible requests once and estimate their true envelope."""
        primary = self._request_input(prompt, content_blocks)
        repair = self._request_input(
            f"{prompt}\n\nSchema-only repair: return a fresh response matching the supplied schema.",
            content_blocks,
        )
        first = self._estimate_input_tokens(model, primary, output_type, max_output_tokens)
        repair_estimate = self._estimate_input_tokens(
            model, repair, output_type, max_output_tokens
        )
        return ProviderReservationEstimate(
            input_tokens=first + repair_estimate,
            attempt_count=2,
            attempt_input_tokens=(first, repair_estimate),
        )

    def _estimate_input_tokens(
        self,
        model: str,
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
                    {
                        "model": model,
                        "input": self._input_for_text_estimation(input_payload),
                        "text": {"format": self._sdk_text_format(output_type)},
                        "store": False,
                        "max_output_tokens": max_output_tokens,
                    },
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
        cost = (
            Decimal(reservation.input_tokens) * rates.input_usd_per_million
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
        model: str,
        input_payload: list[dict[str, Any]],
        output_type: type[T],
        max_output_tokens: int,
    ) -> tuple[T, ProviderUsage]:
        try:
            response = await self._client.responses.parse(
                model=model,
                input=input_payload,
                text_format=output_type,
                store=False,
                timeout=self._timeout_seconds,
                max_output_tokens=max_output_tokens,
            )
        except Exception as error:
            if self._is_schema_exception(error):
                raise _SchemaFailure() from None
            raise ProviderFailure(
                "OPENAI_REQUEST_FAILED",
                self._is_retryable_exception(error),
                "OpenAI request failed.",
            ) from None

        usage = self._validated_usage(getattr(response, "usage", None), model, max_output_tokens)
        if (
            getattr(response, "status", "completed") != "completed"
            or getattr(response, "refusal", None) is not None
        ):
            raise _SchemaFailure(usage)
        try:
            parsed = response.output_parsed
        except Exception:
            raise _SchemaFailure(usage) from None
        if parsed is None or not isinstance(parsed, output_type):
            raise _SchemaFailure(usage)
        return parsed, usage

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
        if (
            isinstance(input_tokens, bool)
            or isinstance(output_tokens, bool)
            or not isinstance(input_tokens, int)
            or not isinstance(output_tokens, int)
            or input_tokens < 0
            or output_tokens < 0
        ):
            raise ProviderFailure("OPENAI_USAGE_INVALID", False, "OpenAI response usage is invalid.")
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
                    input_multiplier=Decimal("2"),
                    output_multiplier=Decimal("1.5"),
                ),
                1,
            )
            raise ProviderFailure(
                "OPENAI_LONG_CONTEXT_RATE_UNCONFIGURED",
                False,
                "Long-context Luna pricing is not configured.",
                usage,
            )
        cost = self._cost_usd(input_tokens, output_tokens, rates)
        usage = ProviderUsage(input_tokens, output_tokens, cost, 1)
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
        input_multiplier: Decimal = Decimal("1"),
        output_multiplier: Decimal = Decimal("1"),
    ) -> Decimal:
        return (
            Decimal(input_tokens)
            * rates.input_usd_per_million
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
        )

    @staticmethod
    def _is_schema_exception(error: Exception) -> bool:
        # The SDK may surface a local Pydantic parse exception rather than a
        # response object. No body is retained or echoed for repair.
        return isinstance(
            error,
            (
                APIResponseValidationError,
                ContentFilterFinishReasonError,
                JSONDecodeError,
                LengthFinishReasonError,
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
