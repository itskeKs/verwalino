"""Deterministic, no-network provider used by Q&A tests and local demos."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Literal

from app.declarations.knowledge_provider import (
    KnowledgeProviderRequest,
    KnowledgeProviderResult,
    ProviderBudgetExceeded,
    ProviderInvalidCitation,
    ProviderKnowledgeAnswer,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.declarations.provider import ProviderReservationEstimate, ProviderUsage
from app.core.config import Settings, settings


class FakeKnowledgeProvider:
    """Selects the first bounded record and can deterministically fail."""

    def __init__(
        self,
        *,
        mode: Literal["success", "timeout", "error", "invalid_citation"] = "success",
        usage: ProviderUsage | None = None,
        max_output_tokens: int | None = None,
        configured_settings: Settings | None = None,
    ) -> None:
        configured = configured_settings or settings
        self.mode = mode
        self._max_output_tokens = (
            max_output_tokens
            if max_output_tokens is not None
            else configured.declaration_qa_max_output_tokens
        )
        self._usage = usage or ProviderUsage(
            input_tokens=17,
            output_tokens=11,
            cost_usd=Decimal("0.0000166"),
            attempt_count=1,
        )
        self.calls: list[KnowledgeProviderRequest] = []

    def estimate_reservation(
        self,
        request: KnowledgeProviderRequest,
        *,
        max_output_tokens: int,
    ) -> ProviderReservationEstimate:
        self._validate_output_limit(max_output_tokens)
        if self._usage.input_tokens <= 0:
            raise ProviderUnavailable("Fake knowledge provider usage is invalid.")
        return ProviderReservationEstimate(
            input_tokens=self._usage.input_tokens,
            attempt_count=1,
            attempt_input_tokens=(self._usage.input_tokens,),
        )

    async def answer(
        self, request: KnowledgeProviderRequest, *, max_output_tokens: int
    ) -> KnowledgeProviderResult:
        self.estimate_reservation(request, max_output_tokens=max_output_tokens)
        self.calls.append(request.model_copy(deep=True))
        if self.mode == "timeout":
            raise ProviderTimeout(usage=deepcopy(self._usage))
        if self.mode == "error":
            raise ProviderUnavailable(usage=deepcopy(self._usage))
        if self.mode == "invalid_citation":
            raise ProviderInvalidCitation(usage=deepcopy(self._usage))
        if self._usage.output_tokens > max_output_tokens:
            raise ProviderBudgetExceeded(
                "Fake knowledge provider exceeded its output allowance.",
                usage=deepcopy(self._usage),
            )
        if not request.hits:
            output = ProviderKnowledgeAnswer(
                status="NOT_DOCUMENTED",
                answer="Keine belastbare Information vorhanden.",
            )
        else:
            hit = request.hits[0]
            output = ProviderKnowledgeAnswer(
                status="NEEDS_CLARIFICATION" if hit.status == "NEEDS_CLARIFICATION" else "ANSWER",
                answer=hit.summary,
                fact_ids=[hit.id] if hit.kind == "FACT" else [],
                rule_ids=[hit.id] if hit.kind == "RULE" else [],
                source_ids=[hit.sources[0].id] if hit.sources else [],
            )
        return KnowledgeProviderResult(output=output.model_copy(deep=True), usage=deepcopy(self._usage))

    def _validate_output_limit(self, max_output_tokens: int) -> None:
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or max_output_tokens <= 0
            or max_output_tokens > self._max_output_tokens
        ):
            raise ProviderBudgetExceeded("Output token limit exceeds the configured provider cap.")
