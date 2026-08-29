"""Strict, provider-independent DTOs for approved property knowledge."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    document_id: str
    document_title: str
    pdf_page: int = Field(ge=1)
    printed_page: str | None = None
    section: str | None = None
    quote: str


class KnowledgeHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["FACT", "RULE"]
    topic: str
    title: str
    summary: str
    status: Literal["EFFECTIVE", "SUPERSEDED", "NEEDS_CLARIFICATION", "RETRACTED"]
    scope: dict[str, Any]
    effect: dict[str, Any]
    sources: list[KnowledgeSource]
    score: float = Field(ge=0)


class PropertyDocumentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    doc_type: str
    title: str
    document_date: date | None = None
    deed_reference: str | None = None
    page_count: int = Field(ge=0)
    archived_at: datetime | None = None


class DocumentRelationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_document_id: str
    target_document_id: str
    relation_type: str
    status: str


class RuleRelationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_rule_id: str
    target_rule_id: str
    relation_type: str


class RuleOut(KnowledgeHit):
    model_config = ConfigDict(extra="forbid")

    relations: list[RuleRelationOut] = Field(default_factory=list)


class PropertyKnowledgeOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_id: str
    documents: list[PropertyDocumentOut]
    document_relations: list[DocumentRelationOut]
    rules_by_topic: dict[str, list[RuleOut]]
    open_rule_topics: list[str]


class KnowledgeSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_id: str
    normalized_query: str
    limit: int = Field(ge=1, le=50)
    include_history: bool
    hits: list[KnowledgeHit]


class KnowledgeQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2_000)


class KnowledgeAnswerOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ANSWER", "NOT_DOCUMENTED", "NEEDS_CLARIFICATION"]
    answer: str = Field(min_length=1, max_length=4_000)
    fact_ids: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    sources: list[KnowledgeSource] = Field(default_factory=list)
