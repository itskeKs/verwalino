"""Strict, sanitized provider contract for bounded property knowledge answers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.declarations.knowledge_schemas import KnowledgeHit
from app.declarations.provider import ProviderReservationEstimate, ProviderUsage


class KnowledgeProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    normalized_question: str = Field(min_length=1, max_length=1_000)
    hits: list[KnowledgeHit] = Field(max_length=50)
    allowed_fact_ids: list[str] = Field(max_length=50)
    allowed_rule_ids: list[str] = Field(max_length=50)
    allowed_source_ids: list[str] = Field(max_length=100)
    locale: Literal["de-DE"] = "de-DE"
    schema_version: str = Field(min_length=1, max_length=64)
    prompt_version: str = Field(min_length=1, max_length=64)


class ProviderKnowledgeAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ANSWER", "NOT_DOCUMENTED", "NEEDS_CLARIFICATION"]
    answer: str = Field(min_length=1, max_length=4_000)
    fact_ids: list[str] = Field(default_factory=list, max_length=20)
    rule_ids: list[str] = Field(default_factory=list, max_length=20)
    source_ids: list[str] = Field(default_factory=list, max_length=30)


@dataclass(frozen=True)
class KnowledgeProviderResult:
    output: ProviderKnowledgeAnswer
    usage: ProviderUsage


class KnowledgeProviderError(RuntimeError):
    """Safe provider failure; never carry question, evidence, or model prose."""

    code = "PROVIDER_UNAVAILABLE"

    def __init__(self, message: str = "Knowledge provider is unavailable.", usage: ProviderUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


class ProviderUnavailable(KnowledgeProviderError):
    code = "PROVIDER_UNAVAILABLE"


class ProviderTimeout(KnowledgeProviderError):
    code = "PROVIDER_TIMEOUT"


class ProviderBudgetExceeded(KnowledgeProviderError):
    code = "PROVIDER_BUDGET_EXCEEDED"


class ProviderInvalidOutput(KnowledgeProviderError):
    code = "PROVIDER_INVALID_OUTPUT"


class ProviderInvalidCitation(KnowledgeProviderError):
    code = "INVALID_CITATION"


class KnowledgeAnswerProvider(Protocol):
    def estimate_reservation(
        self,
        request: KnowledgeProviderRequest,
        *,
        max_output_tokens: int,
    ) -> ProviderReservationEstimate: ...

    async def answer(
        self,
        request: KnowledgeProviderRequest,
        *,
        max_output_tokens: int,
    ) -> KnowledgeProviderResult: ...
