"""Tool-free OpenAI Responses adapter for bounded property knowledge Q&A."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from json import JSONDecodeError
from typing import Any

from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APITimeoutError,
    ContentFilterFinishReasonError,
    LengthFinishReasonError,
    RateLimitError,
)
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.declarations.knowledge_prompts import KNOWLEDGE_QA_DEVELOPER_PROMPT, knowledge_qa_user_payload
from app.declarations.knowledge_provider import (
    KnowledgeProviderRequest,
    KnowledgeProviderResult,
    ProviderBudgetExceeded,
    ProviderInvalidCitation,
    ProviderInvalidOutput,
    ProviderKnowledgeAnswer,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.declarations.provider import ProviderReservationEstimate, ProviderUsage


_MILLION = Decimal("1000000")


class _ByteTokenEncoder:
    """Local conservative fallback; it never downloads tokenizer data in tests or startup."""

    @staticmethod
    def encode(value: str) -> list[int]:
        return list(value.encode("utf-8"))


class OpenAIKnowledgeProvider:
    """One injected SDK client, no retries, no tools, and no retained responses."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
        max_cost_usd: Decimal | None = None,
        input_usd_per_million: Decimal | None = None,
        output_usd_per_million: Decimal | None = None,
        token_encoder: Any | None = None,
        configured_settings: Settings | None = None,
    ) -> None:
        configured = configured_settings or settings
        self._model = model or configured.declaration_qa_model
        self._timeout_seconds = timeout_seconds if timeout_seconds is not None else configured.declaration_qa_timeout_seconds
        self._max_input_tokens = max_input_tokens if max_input_tokens is not None else configured.declaration_qa_max_input_tokens
        self._max_output_tokens = max_output_tokens if max_output_tokens is not None else configured.declaration_qa_max_output_tokens
        self._max_cost_usd = max_cost_usd if max_cost_usd is not None else configured.declaration_qa_max_cost_usd
        self._input_rate = input_usd_per_million if input_usd_per_million is not None else configured.declaration_qa_input_usd_per_million
        self._output_rate = output_usd_per_million if output_usd_per_million is not None else configured.declaration_qa_output_usd_per_million
        self._client = client
        self._token_encoder = token_encoder or _ByteTokenEncoder()
        self._validate_configuration()

    def _validate_configuration(self) -> None:
        if self._model != "gpt-5.6-luna":
            raise ProviderUnavailable("The configured knowledge model rate is not verified.")
        if (
            self._timeout_seconds <= 0
            or self._max_input_tokens <= 0
            or self._max_output_tokens <= 0
            or self._max_cost_usd <= 0
        ):
            raise ProviderUnavailable("Knowledge provider configuration is invalid.")
        for rate in (self._input_rate, self._output_rate):
            if not rate.is_finite() or rate <= 0:
                raise ProviderUnavailable("The configured knowledge model rate is invalid.")

    async def answer(
        self, request: KnowledgeProviderRequest, *, max_output_tokens: int
    ) -> KnowledgeProviderResult:
        reservation = self.estimate_reservation(request, max_output_tokens=max_output_tokens)
        if self._client is None:
            raise ProviderUnavailable()
        user_payload = knowledge_qa_user_payload(request)
        input_payload = [
            {"role": "developer", "content": [{"type": "input_text", "text": KNOWLEDGE_QA_DEVELOPER_PROMPT}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_payload}]},
        ]
        try:
            response = await self._client.responses.parse(
                model=self._model,
                input=input_payload,
                text_format=ProviderKnowledgeAnswer,
                store=False,
                tools=[],
                timeout=self._timeout_seconds,
                max_output_tokens=max_output_tokens,
            )
        except APITimeoutError:
            raise ProviderTimeout() from None
        except (APIConnectionError, RateLimitError):
            raise ProviderUnavailable() from None
        except (
            APIResponseValidationError,
            ContentFilterFinishReasonError,
            JSONDecodeError,
            LengthFinishReasonError,
            ValidationError,
        ):
            raise ProviderInvalidOutput() from None
        except Exception:
            raise ProviderUnavailable() from None
        usage = self._validated_usage(getattr(response, "usage", None), max_output_tokens)
        if getattr(response, "status", "completed") != "completed" or getattr(response, "refusal", None) is not None:
            raise ProviderInvalidOutput(usage=usage)
        parsed = getattr(response, "output_parsed", None)
        if not isinstance(parsed, ProviderKnowledgeAnswer):
            raise ProviderInvalidOutput(usage=usage)
        try:
            self._validate_allowlisted_citations(parsed, request)
        except ProviderInvalidCitation as error:
            error.usage = usage
            raise
        return KnowledgeProviderResult(output=parsed, usage=usage)

    def estimate_reservation(
        self,
        request: KnowledgeProviderRequest,
        *,
        max_output_tokens: int,
    ) -> ProviderReservationEstimate:
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or max_output_tokens <= 0
            or max_output_tokens > self._max_output_tokens
        ):
            raise ProviderBudgetExceeded("Output token limit exceeds the configured provider cap.")
        reserved_input_tokens = self._reserved_input_tokens(knowledge_qa_user_payload(request))
        if reserved_input_tokens > self._max_input_tokens:
            raise ProviderBudgetExceeded("Input token budget would be exceeded.")
        if self._cost(reserved_input_tokens, max_output_tokens) > self._max_cost_usd:
            raise ProviderBudgetExceeded("Cost budget would be exceeded.")
        return ProviderReservationEstimate(
            input_tokens=reserved_input_tokens,
            attempt_count=1,
            attempt_input_tokens=(reserved_input_tokens,),
        )

    def _validated_usage(self, raw_usage: object, max_output_tokens: int) -> ProviderUsage:
        input_tokens = getattr(raw_usage, "input_tokens", None)
        output_tokens = getattr(raw_usage, "output_tokens", None)
        if (
            raw_usage is None
            or isinstance(input_tokens, bool)
            or isinstance(output_tokens, bool)
            or not isinstance(input_tokens, int)
            or not isinstance(output_tokens, int)
            or input_tokens < 0
            or output_tokens < 0
        ):
            raise ProviderInvalidOutput("Knowledge provider usage is missing or invalid.")
        usage = ProviderUsage(input_tokens, output_tokens, self._cost(input_tokens, output_tokens), 1)
        if output_tokens > max_output_tokens:
            raise ProviderBudgetExceeded("Knowledge provider exceeded its output allowance.", usage=usage)
        if input_tokens > self._max_input_tokens or usage.cost_usd > self._max_cost_usd:
            raise ProviderBudgetExceeded("Knowledge provider exceeded its budget.", usage=usage)
        return usage

    def _cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return (Decimal(input_tokens) * self._input_rate + Decimal(output_tokens) * self._output_rate) / _MILLION

    def _reserved_input_tokens(self, user_payload: str) -> int:
        """Account for every transmitted text element before a provider call."""
        envelope = json.dumps(
            {
                "developer": KNOWLEDGE_QA_DEVELOPER_PROMPT,
                "user": user_payload,
                "response_schema": ProviderKnowledgeAnswer.model_json_schema(),
                "tools": [],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        tokens = self._token_encoder.encode(envelope)
        if not isinstance(tokens, list) or len(tokens) <= 0:
            raise ProviderUnavailable("Knowledge provider token accounting is unavailable.")
        return len(tokens)

    @staticmethod
    def _validate_allowlisted_citations(output: ProviderKnowledgeAnswer, request: KnowledgeProviderRequest) -> None:
        groups = (
            (output.fact_ids, request.allowed_fact_ids, None),
            (output.rule_ids, request.allowed_rule_ids, None),
            (output.source_ids, request.allowed_source_ids, "source"),
        )
        for ids, allowed, kind in groups:
            if len(ids) != len(set(ids)) or not set(ids) <= set(allowed):
                raise ProviderInvalidCitation("Knowledge provider returned an invalid citation.")
            for value in ids:
                try:
                    if kind == "source":
                        prefix, raw = value.split(":", 1)
                        if prefix not in {"fact-source", "rule-source"}:
                            raise ValueError
                    else:
                        raw = value
                    uuid.UUID(raw)
                except (ValueError, IndexError, AttributeError):
                    raise ProviderInvalidCitation("Knowledge provider returned a malformed citation.") from None
