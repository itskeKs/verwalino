"""Deterministic browse and lexical retrieval over approved tenant-local knowledge."""

from __future__ import annotations

import re
import threading
import time
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from decimal import InvalidOperation, ROUND_UP
from typing import Any, Callable, Mapping

from sqlalchemy import and_, select, text
from fastapi import HTTPException
from app.core.live_principal import require_live_principal
from app.core.tenant import AuthContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import settings
from app.declarations.approval import value_fingerprint
from app.declarations.knowledge_schemas import (
    DocumentRelationOut,
    KnowledgeAnswerOut,
    KnowledgeHit,
    KnowledgeQuestionRequest,
    KnowledgeSearchResponse,
    KnowledgeSource,
    PropertyDocumentOut,
    PropertyKnowledgeOverview,
    RuleOut,
    RuleRelationOut,
)
from app.declarations.knowledge_prompts import KNOWLEDGE_QA_PROMPT_VERSION
from app.declarations.knowledge_provider import (
    KnowledgeAnswerProvider,
    KnowledgeProviderRequest,
    KnowledgeProviderResult,
    ProviderInvalidCitation,
    ProviderInvalidOutput,
    ProviderKnowledgeAnswer,
    ProviderBudgetExceeded,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.declarations.provider import ProviderReservationEstimate, ProviderUsage
from app.core.db import get_tenant_session
from app.models.property import Property
from app.models.property_assignment import PropertyAssignment
from app.models.property_document import PropertyDocument
from app.models.property_document_relation import PropertyDocumentRelation
from app.models.property_legal_reference import PropertyLegalReference
from app.models.unit import Unit
from app.repositories.property_knowledge_query_repo import PropertyKnowledgeQueryRepository
from app.repositories.property_knowledge_query_repo import KnowledgeQueryReservation
from app.repositories.property_knowledge_repo import approved_value_display


DEFAULT_STATUSES = ("EFFECTIVE", "NEEDS_CLARIFICATION")
HISTORY_STATUSES = ("SUPERSEDED", "RETRACTED")
_FACT_NAMESPACE = uuid.UUID("9d31bf6e-2df0-58ed-a9ec-30ecf7164431")
_PROPERTY_TEXT_FIELDS = {"name", "street", "zip_code", "city", "management_type"}
_UNIT_TEXT_FIELDS = {"number", "location", "building_part", "usage_type"}
_LEGAL_REFERENCE_TEXT_FIELDS = {
    "reference_type", "district", "cadastral_district", "field_number", "parcel", "sheet",
}
_ASSIGNMENT_TEXT_FIELDS = {"assignment_type", "designation", "location", "description"}
_OPAQUE_ASSIGNMENT_FIELDS = {"unit_subject_key", "details"}
_OPAQUE_LEGAL_REFERENCE_FIELDS = {"details"}
SAFE_NOT_DOCUMENTED_TEXT = "In den freigegebenen Unterlagen ist dazu keine belastbare Information dokumentiert."
UNVERIFIED_ANSWER_TEXT = "Die automatisierte Antwort konnte nicht verifiziert werden."
KNOWLEDGE_ANSWER_SCHEMA_VERSION = "knowledge-answer-v1"
_MICRO_USD = Decimal("0.000001")


class KnowledgeQueryBudgetExceeded(RuntimeError):
    """A safe, stable organization-budget/concurrency rejection."""


class KnowledgeQueryUnavailable(RuntimeError):
    """A safe, stable transport/provider availability failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _KnowledgeConcurrencyGuard:
    """Process-local nonblocking backpressure, independent of DB budget locks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._capacity = 0
        self._semaphore: threading.BoundedSemaphore | None = None

    def acquire(self, capacity: int) -> bool:
        with self._lock:
            if self._semaphore is None or self._capacity != capacity:
                self._capacity = capacity
                self._semaphore = threading.BoundedSemaphore(capacity)
            return self._semaphore.acquire(blocking=False)

    def release(self) -> None:
        with self._lock:
            if self._semaphore is not None:
                self._semaphore.release()


_qa_concurrency_guard = _KnowledgeConcurrencyGuard()


@dataclass(frozen=True)
class PreparedKnowledgeQuestion:
    """Detached retrieval DTO for Task 5's short-transaction provider flow."""

    property_id: uuid.UUID
    normalized_question: str
    hits: tuple[KnowledgeHit, ...]
    request: KnowledgeProviderRequest


class PropertyKnowledgeNotFound(LookupError):
    """Unknown and foreign properties deliberately share the same result."""


def normalize_knowledge_query(query: str) -> str:
    normalized = unicodedata.normalize("NFKC", query)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        raise ValueError("blank knowledge query")
    return normalized


def clamp_retrieval_limit(limit: int) -> int:
    return max(1, min(50, int(limit)))


class PropertyKnowledgeService:
    """Tenant-local browse, retrieval, and detached Q&A preparation.

    ``prepare_question`` and ``finalize_provider_result`` are deliberately
    separate: a future endpoint opens a short session for each and calls the
    provider between them, with no database transaction held over the network.
    ``answer`` is only a convenience wrapper for unit-level callers.
    """

    def __init__(
        self,
        session: AsyncSession,
        organization_id: uuid.UUID,
        provider: KnowledgeAnswerProvider | None = None,
    ):
        self._session = session
        self._organization_id = organization_id
        self._repo = PropertyKnowledgeQueryRepository(session, organization_id)
        self._provider = provider

    async def overview(
        self,
        property_id: uuid.UUID,
        *,
        include_history: bool = False,
    ) -> PropertyKnowledgeOverview:
        await self._require_property(property_id)
        statuses = self._statuses(include_history)
        documents = await self._repo.list_documents(property_id)
        document_relations = await self._repo.list_document_relations(property_id, statuses)
        rules = await self._repo.list_rules(property_id, statuses)
        relations = await self._repo.list_rule_relations(property_id)
        relation_by_rule: dict[uuid.UUID, list[RuleRelationOut]] = defaultdict(list)
        for relation in relations:
            out = RuleRelationOut(
                id=str(relation.id), source_rule_id=str(relation.source_rule_id),
                target_rule_id=str(relation.target_rule_id), relation_type=relation.relation_type,
            )
            relation_by_rule[relation.source_rule_id].append(out)
            relation_by_rule[relation.target_rule_id].append(out)
        grouped: dict[str, list[RuleOut]] = defaultdict(list)
        for rule in rules:
            hit = await self._rule_hit(property_id, rule, score=0.0)
            grouped[rule.topic].append(RuleOut(**hit.model_dump(), relations=relation_by_rule[rule.id]))
        return PropertyKnowledgeOverview(
            property_id=str(property_id),
            documents=[
                PropertyDocumentOut(
                    id=str(document.id), doc_type=document.doc_type, title=document.title,
                    document_date=document.document_date, deed_reference=document.deed_reference,
                    page_count=document.page_count, archived_at=document.archived_at,
                )
                for document in documents
            ],
            document_relations=[
                DocumentRelationOut(
                    id=str(relation.id), source_document_id=str(relation.source_document_id),
                    target_document_id=str(relation.target_document_id), relation_type=relation.relation_type,
                    status=relation.status,
                )
                for relation in document_relations
            ],
            rules_by_topic=dict(grouped),
            open_rule_topics=sorted({rule.topic for rule in rules if rule.status == "NEEDS_CLARIFICATION"}),
        )

    async def search(
        self,
        property_id: uuid.UUID,
        query: str,
        *,
        limit: int = 20,
        include_history: bool = False,
    ) -> KnowledgeSearchResponse:
        normalized = normalize_knowledge_query(query)
        bounded_limit = clamp_retrieval_limit(limit)
        # This must remain before every retrieval statement: no anonymous property probing.
        await self._require_property(property_id)
        candidates = await self._repo.search_candidates(
            property_id,
            normalized,
            statuses=self._statuses(include_history),
            trigram_threshold=settings.declaration_search_trigram_threshold,
            limit=bounded_limit,
        )
        hits: list[KnowledgeHit] = []
        for candidate in candidates:
            hit = await self._candidate_to_hit(property_id, candidate)
            if hit is not None:
                hits.append(hit)
        return KnowledgeSearchResponse(
            property_id=str(property_id), normalized_query=normalized, limit=bounded_limit,
            include_history=include_history, hits=hits,
        )

    async def prepare_question(
        self, property_id: uuid.UUID, question: str | KnowledgeQuestionRequest
    ) -> PreparedKnowledgeQuestion:
        """Retrieve only bounded, current DTOs; this method performs no network call."""
        raw_question = question.question if isinstance(question, KnowledgeQuestionRequest) else question
        normalized = normalize_knowledge_query(raw_question)
        if len(normalized) > 1_000:
            raise ValueError("normalized knowledge question exceeds provider limit")
        retrieval = await self.search(
            property_id,
            normalized,
            limit=settings.declaration_qa_max_hits,
        )
        hits = tuple(retrieval.hits)
        fact_ids = [hit.id for hit in hits if hit.kind == "FACT"]
        rule_ids = [hit.id for hit in hits if hit.kind == "RULE"]
        source_ids = [source.id for hit in hits for source in hit.sources]
        request = KnowledgeProviderRequest(
            normalized_question=normalized,
            hits=list(hits),
            allowed_fact_ids=fact_ids,
            allowed_rule_ids=rule_ids,
            allowed_source_ids=source_ids,
            schema_version=KNOWLEDGE_ANSWER_SCHEMA_VERSION,
            prompt_version=KNOWLEDGE_QA_PROMPT_VERSION,
        )
        return PreparedKnowledgeQuestion(property_id, normalized, hits, request)

    async def finalize_provider_result(
        self,
        prepared: PreparedKnowledgeQuestion,
        result: KnowledgeProviderResult,
    ) -> KnowledgeAnswerOut:
        """Refresh canonical DTOs before accepting any provider prose or citations."""
        final_hits = tuple(
            (await self.search(
                prepared.property_id,
                prepared.normalized_question,
                limit=settings.declaration_qa_max_hits,
            )).hits
        )
        try:
            answer = validate_knowledge_answer(
                result.output,
                final_hits,
                supplied_hits=prepared.hits,
            )
            if not await self._cited_facts_are_current(
                prepared.property_id,
                prepared.normalized_question,
                result.output.fact_ids,
            ):
                raise ProviderInvalidCitation("A cited fact is no longer canonical.")
            return answer
        except (ProviderInvalidCitation, ProviderInvalidOutput):
            return self._unverified_answer()

    async def answer(
        self, property_id: uuid.UUID, question: str | KnowledgeQuestionRequest
    ) -> KnowledgeAnswerOut:
        """Convenience wrapper; endpoint code should compose the two short phases."""
        prepared = await self.prepare_question(property_id, question)
        if not prepared.hits:
            return self._not_documented_answer()
        if self._provider is None:
            from app.declarations.knowledge_provider import ProviderUnavailable
            raise ProviderUnavailable()
        try:
            result = await self._provider.answer(
                prepared.request,
                max_output_tokens=settings.declaration_qa_max_output_tokens,
            )
        except (ProviderInvalidCitation, ProviderInvalidOutput):
            return self._unverified_answer()
        return await self.finalize_provider_result(prepared, result)

    @staticmethod
    def _not_documented_answer() -> KnowledgeAnswerOut:
        return KnowledgeAnswerOut(status="NOT_DOCUMENTED", answer=SAFE_NOT_DOCUMENTED_TEXT)

    @staticmethod
    def _unverified_answer() -> KnowledgeAnswerOut:
        return KnowledgeAnswerOut(status="NOT_DOCUMENTED", answer=UNVERIFIED_ANSWER_TEXT)

    async def _require_property(self, property_id: uuid.UUID) -> Property:
        property_row = await self._repo.get_property(property_id)
        if property_row is None:
            raise PropertyKnowledgeNotFound("property not found")
        return property_row

    @staticmethod
    def _statuses(include_history: bool) -> tuple[str, ...]:
        return DEFAULT_STATUSES + HISTORY_STATUSES if include_history else DEFAULT_STATUSES

    async def _candidate_to_hit(self, property_id: uuid.UUID, candidate: Mapping[str, Any]) -> KnowledgeHit | None:
        score = max(0.0, float(candidate["score"] or 0.0))
        if candidate["kind"] == "RULE":
            rule = await self._repo.get_rule(property_id, uuid.UUID(str(candidate["item_id"])))
            return None if rule is None else await self._rule_hit(property_id, rule, score)
        subject_id = uuid.UUID(str(candidate["subject_id"]))
        sources = await self._repo.list_fact_sources(
            property_id, subject_kind=str(candidate["subject_kind"]), subject_id=subject_id,
            field_key=str(candidate["field_key"]), value_fingerprint=str(candidate["value_fingerprint"]),
            status=str(candidate["status"]),
        )
        if not sources:
            return None
        source_rows = [source for source, _ in sources]
        status = str(candidate["status"])
        if any(value_fingerprint(source.approved_value_json) != source.value_fingerprint for source in source_rows):
            return None
        if status == "EFFECTIVE" and not await self._current_fact_is_valid(property_id, source_rows):
            return None
        value = source_rows[0].approved_value_json
        fact_id = _fact_hit_id(property_id, candidate)
        return KnowledgeHit(
            id=fact_id, kind="FACT", topic=str(candidate["subject_kind"]).lower(),
            title=f"{candidate['subject_kind'].replace('_', ' ').title()}: {candidate['field_key']}",
            summary=approved_value_display(value), status=status, scope={},
            effect={"field_key": str(candidate["field_key"])}, score=score,
            sources=[self._fact_source_out(source, title) for source, title in sources],
        )

    async def _rule_hit(self, property_id: uuid.UUID, rule, score: float) -> KnowledgeHit:
        sources = await self._repo.list_rule_sources(property_id, rule.id)
        return KnowledgeHit(
            id=str(rule.id), kind="RULE", topic=rule.topic, title=rule.title,
            summary=rule.summary, status=rule.status, scope=rule.scope_json,
            effect=rule.effect_json, score=max(0.0, score),
            sources=[self._rule_source_out(source, title) for source, title in sources],
        )

    async def _current_fact_is_valid(self, property_id: uuid.UUID, sources: list) -> bool:
        """Revalidate reversible canonical fields; opaque snapshots require a live subject only.

        Assignment ``unit_subject_key`` and ``details``, plus legal-reference
        ``details``, cannot be reconstructed from canonical storage. Those
        immutable typed snapshots are accepted only after exact subject and
        property validation; no synthetic fingerprint is ever invented.
        """
        source = sources[0]
        if any(row.value_fingerprint != source.value_fingerprint for row in sources):
            return False
        expected, subject_status, subject_exists = await self._canonical_fact_value(
            property_id, source.subject_kind, source.subject_id, source.field_key,
        )
        if not subject_exists:
            return False
        if source.subject_kind in {"LEGAL_REFERENCE", "ASSIGNMENT", "DOCUMENT_RELATION"} and subject_status != source.status:
            return False
        if source.subject_kind == "ASSIGNMENT" and source.field_key in _OPAQUE_ASSIGNMENT_FIELDS:
            return value_fingerprint(source.approved_value_json) == source.value_fingerprint
        if source.subject_kind == "LEGAL_REFERENCE" and source.field_key in _OPAQUE_LEGAL_REFERENCE_FIELDS:
            return value_fingerprint(source.approved_value_json) == source.value_fingerprint
        if expected is None:
            return False
        if source.subject_kind == "UNIT" and source.field_key in {"area", "heated_area"}:
            return self._unit_decimal_snapshot_matches(source.approved_value_json, expected)
        return value_fingerprint(expected) == source.value_fingerprint

    async def _cited_facts_are_current(
        self,
        property_id: uuid.UUID,
        normalized_question: str,
        fact_ids: list[str],
    ) -> bool:
        """Recheck every cited fact, including NEEDS_CLARIFICATION facts, at acceptance time."""
        if not fact_ids:
            return True
        expected_ids = set(fact_ids)
        candidates = await self._repo.search_candidates(
            property_id,
            normalized_question,
            statuses=DEFAULT_STATUSES,
            trigram_threshold=settings.declaration_search_trigram_threshold,
            limit=settings.declaration_qa_max_hits,
        )
        current_ids: set[str] = set()
        for candidate in candidates:
            if candidate["kind"] != "FACT":
                continue
            fact_id = _fact_hit_id(property_id, candidate)
            if fact_id not in expected_ids:
                continue
            subject_id = uuid.UUID(str(candidate["subject_id"]))
            source_rows = await self._repo.list_fact_sources(
                property_id,
                subject_kind=str(candidate["subject_kind"]),
                subject_id=subject_id,
                field_key=str(candidate["field_key"]),
                value_fingerprint=str(candidate["value_fingerprint"]),
                status=str(candidate["status"]),
            )
            sources = [source for source, _ in source_rows]
            if not sources or any(
                value_fingerprint(source.approved_value_json) != source.value_fingerprint
                for source in sources
            ):
                return False
            # Deliberately do not gate this on EFFECTIVE: conflicts must still
            # have current, active evidence before they can be cited.
            if not await self._current_fact_is_valid(property_id, sources):
                return False
            current_ids.add(fact_id)
        return current_ids == expected_ids

    @staticmethod
    def _unit_decimal_snapshot_matches(
        snapshot: Mapping[str, Any], current: Mapping[str, Any],
    ) -> bool:
        if (
            snapshot.get("kind") != "DECIMAL"
            or current.get("kind") != "DECIMAL"
            or snapshot.get("unit") != "m²"
            or current.get("unit") != "m²"
        ):
            return False
        try:
            snapshot_value = Decimal(str(snapshot.get("value")))
            current_value = Decimal(str(current.get("value")))
        except (InvalidOperation, TypeError, ValueError):
            return False
        return snapshot_value.is_finite() and current_value.is_finite() and snapshot_value == current_value

    async def _canonical_fact_value(
        self, property_id: uuid.UUID, subject_kind: str, subject_id: uuid.UUID, field_key: str,
    ) -> tuple[dict[str, Any] | None, str | None, bool]:
        if subject_kind == "PROPERTY":
            row = await self._session.scalar(select(Property).where(Property.organization_id == self._organization_id, Property.id == property_id, Property.id == subject_id))
            if row is None:
                return None, None, False
            if field_key == "mea_denominator":
                return ({"kind": "INTEGER", "value": row.mea_denominator} if row.mea_denominator is not None else None), None, True
            if field_key not in _PROPERTY_TEXT_FIELDS:
                return None, None, True
            value = getattr(row, field_key)
            return ({"kind": "TEXT", "value": value} if isinstance(value, str) else None), None, True
        if subject_kind == "UNIT":
            row = await self._session.scalar(select(Unit).where(Unit.organization_id == self._organization_id, Unit.property_id == property_id, Unit.id == subject_id))
            if row is None:
                return None, None, False
            if field_key == "mea" and row.mea is not None:
                property_row = await self._repo.get_property(property_id)
                if property_row is None or property_row.mea_denominator is None:
                    return None, None, True
                try:
                    if Decimal(row.mea) != Decimal(row.mea).to_integral_value():
                        return None, None, True
                except (InvalidOperation, TypeError):
                    return None, None, True
                return {"kind": "MEA", "numerator": int(row.mea), "denominator": property_row.mea_denominator}, None, True
            if field_key in {"area", "heated_area"}:
                value = getattr(row, field_key)
                return ({"kind": "DECIMAL", "value": str(value), "unit": "m²"} if value is not None else None), None, True
            if field_key not in _UNIT_TEXT_FIELDS:
                return None, None, True
            value = getattr(row, field_key)
            return ({"kind": "TEXT", "value": value} if isinstance(value, str) else None), None, True
        if subject_kind == "LEGAL_REFERENCE":
            row = await self._session.scalar(select(PropertyLegalReference).where(PropertyLegalReference.organization_id == self._organization_id, PropertyLegalReference.property_id == property_id, PropertyLegalReference.id == subject_id))
            if row is None:
                return None, None, False
            if field_key not in _LEGAL_REFERENCE_TEXT_FIELDS:
                return None, row.status, True
            value = getattr(row, field_key)
            return ({"kind": "TEXT", "value": value} if isinstance(value, str) else None), row.status, True
        if subject_kind == "ASSIGNMENT":
            row = await self._session.scalar(select(PropertyAssignment).where(PropertyAssignment.organization_id == self._organization_id, PropertyAssignment.property_id == property_id, PropertyAssignment.id == subject_id))
            if row is None:
                return None, None, False
            if field_key in _OPAQUE_ASSIGNMENT_FIELDS:
                return None, row.status, True
            if field_key not in _ASSIGNMENT_TEXT_FIELDS:
                return None, row.status, True
            value = getattr(row, field_key)
            return ({"kind": "TEXT", "value": value} if isinstance(value, str) else None), row.status, True
        if subject_kind == "DOCUMENT_RELATION" and field_key == "relation":
            source_document = aliased(PropertyDocument)
            target_document = aliased(PropertyDocument)
            row = await self._session.scalar(
                select(PropertyDocumentRelation).join(
                    source_document,
                    and_(
                        source_document.organization_id == PropertyDocumentRelation.organization_id,
                        source_document.property_id == PropertyDocumentRelation.property_id,
                        source_document.id == PropertyDocumentRelation.source_document_id,
                    ),
                ).join(
                    target_document,
                    and_(
                        target_document.organization_id == PropertyDocumentRelation.organization_id,
                        target_document.property_id == PropertyDocumentRelation.property_id,
                        target_document.id == PropertyDocumentRelation.target_document_id,
                    ),
                ).where(
                    PropertyDocumentRelation.organization_id == self._organization_id,
                    PropertyDocumentRelation.property_id == property_id,
                    PropertyDocumentRelation.id == subject_id,
                    source_document.archived_at.is_(None),
                    target_document.archived_at.is_(None),
                )
            )
            if row is None:
                return None, None, False
            return {
                "relation_type": row.relation_type,
                "target_document_key": str(row.target_document_id),
            }, row.status, True
        return None, None, False

    @staticmethod
    def _fact_source_out(source, document_title: str) -> KnowledgeSource:
        return KnowledgeSource(id=f"fact-source:{source.id}", document_id=str(source.property_document_id), document_title=document_title, pdf_page=source.pdf_page, printed_page=source.printed_page, section=source.section, quote=source.quote)

    @staticmethod
    def _rule_source_out(source, document_title: str) -> KnowledgeSource:
        return KnowledgeSource(id=f"rule-source:{source.id}", document_id=str(source.property_document_id), document_title=document_title, pdf_page=source.pdf_page, printed_page=source.printed_page, section=source.section, quote=source.quote)


def _require_uuid(value: str, *, source: bool = False) -> None:
    """Provider identifiers are opaque UUIDs, never semantic model-generated strings."""
    try:
        if source:
            prefix, raw = value.split(":", 1)
            if prefix not in {"fact-source", "rule-source"}:
                raise ValueError
            uuid.UUID(raw)
        else:
            uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ProviderInvalidCitation("Knowledge provider returned a malformed citation.") from None


def _fact_hit_id(property_id: uuid.UUID, candidate: Mapping[str, Any]) -> str:
    return str(uuid.uuid5(
        _FACT_NAMESPACE,
        ":".join((
            str(property_id),
            str(candidate["subject_kind"]),
            str(candidate["subject_id"]),
            str(candidate["field_key"]),
            str(candidate["value_fingerprint"]),
        )),
    ))


def validate_knowledge_answer(
    output: ProviderKnowledgeAnswer,
    hits: list[KnowledgeHit] | tuple[KnowledgeHit, ...],
    *,
    supplied_hits: list[KnowledgeHit] | tuple[KnowledgeHit, ...] | None = None,
) -> KnowledgeAnswerOut:
    """Fail closed on citations before exposing any model-provided prose."""
    hit_list = list(hits)
    facts = {hit.id: hit for hit in hit_list if hit.kind == "FACT"}
    rules = {hit.id: hit for hit in hit_list if hit.kind == "RULE"}
    sources = {source.id: (hit, source) for hit in hit_list for source in hit.sources}
    supplied_list = list(supplied_hits) if supplied_hits is not None else hit_list
    supplied_facts = {hit.id: hit for hit in supplied_list if hit.kind == "FACT"}
    supplied_rules = {hit.id: hit for hit in supplied_list if hit.kind == "RULE"}
    supplied_sources = {source.id: (hit, source) for hit in supplied_list for source in hit.sources}
    groups = (
        (output.fact_ids, facts, supplied_facts, False),
        (output.rule_ids, rules, supplied_rules, False),
        (output.source_ids, sources, supplied_sources, True),
    )
    for ids, final_allowed, supplied_allowed, is_source in groups:
        if (
            len(ids) != len(set(ids))
            or not set(ids) <= set(final_allowed)
            or not set(ids) <= set(supplied_allowed)
        ):
            raise ProviderInvalidCitation("Knowledge provider returned an invalid citation.")
        for value in ids:
            _require_uuid(value, source=is_source)
    cited_hit_ids = set(output.fact_ids) | set(output.rule_ids)
    for source_id in output.source_ids:
        owner, _ = sources[source_id]
        supplied_owner, _ = supplied_sources[source_id]
        if owner.id not in cited_hit_ids or supplied_owner.id not in cited_hit_ids:
            raise ProviderInvalidCitation("A cited source is not attached to a cited record.")
    for hit_id in cited_hit_ids:
        final_hit = facts.get(hit_id) or rules.get(hit_id)
        supplied_hit = supplied_facts.get(hit_id) or supplied_rules.get(hit_id)
        assert final_hit is not None and supplied_hit is not None
        if final_hit.model_dump(exclude={"score"}) != supplied_hit.model_dump(exclude={"score"}):
            raise ProviderInvalidCitation("A cited record changed during provider processing.")
    for source_id in output.source_ids:
        _, final_source = sources[source_id]
        _, supplied_source = supplied_sources[source_id]
        if final_source.model_dump() != supplied_source.model_dump():
            raise ProviderInvalidCitation("A cited source changed during provider processing.")
    if output.status == "NOT_DOCUMENTED":
        return KnowledgeAnswerOut(status="NOT_DOCUMENTED", answer=SAFE_NOT_DOCUMENTED_TEXT)
    if not output.source_ids:
        raise ProviderInvalidCitation("A substantive answer needs source citations.")
    if output.status == "ANSWER":
        cited_rules = [rules[rule_id] for rule_id in output.rule_ids]
        if cited_rules and all(rule.status == "SUPERSEDED" for rule in cited_rules):
            raise ProviderInvalidCitation("A superseded rule cannot be the only support for an answer.")
        cited_topics = {facts[fact_id].topic for fact_id in output.fact_ids} | {rule.topic for rule in cited_rules}
        if any(
            hit.status == "NEEDS_CLARIFICATION" and hit.topic in cited_topics
            for hit in (*hit_list, *supplied_list)
        ):
            raise ProviderInvalidCitation("An unresolved conflict cannot be concealed by an answer.")
    cited_sources = [sources[source_id][1] for source_id in output.source_ids]
    return KnowledgeAnswerOut(
        status=output.status,
        answer=output.answer,
        fact_ids=list(output.fact_ids),
        rule_ids=list(output.rule_ids),
        source_ids=list(output.source_ids),
        sources=cited_sources,
    )


def _utc_window_start(now: datetime, window_minutes: int) -> datetime:
    """Floor a UTC instant into the configured, positive budget window."""
    epoch_minutes = int(now.timestamp()) // 60
    floored = epoch_minutes - (epoch_minutes % window_minutes)
    return datetime.fromtimestamp(floored * 60, tz=timezone.utc)


def _stored_cost(value: Decimal) -> Decimal:
    """NUMERIC(12, 6) cannot round a reservation down below its worst case."""
    return value.quantize(_MICRO_USD, rounding=ROUND_UP)


def _configured_cost(input_tokens: int, output_tokens: int) -> Decimal:
    return (
        Decimal(input_tokens) * settings.declaration_qa_input_usd_per_million
        + Decimal(output_tokens) * settings.declaration_qa_output_usd_per_million
    ) / Decimal("1000000")


def _provider_identity(provider: KnowledgeAnswerProvider) -> tuple[str, str]:
    """Ledger metadata is deliberately limited to provider class and configured model."""
    return type(provider).__name__, str(getattr(provider, "_model", settings.declaration_qa_model))


def _usage_for_reservation(
    usage: object,
    reservation: KnowledgeQueryReservation,
) -> tuple[ProviderUsage | None, Decimal, str | None]:
    """Validate opaque SDK metering before accepting either cost or model prose."""
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    attempt_count = getattr(usage, "attempt_count", None)
    cost_usd = getattr(usage, "cost_usd", None)
    valid_numbers = (
        not isinstance(input_tokens, bool)
        and not isinstance(output_tokens, bool)
        and isinstance(input_tokens, int)
        and isinstance(output_tokens, int)
        and input_tokens >= 0
        and output_tokens >= 0
        and isinstance(cost_usd, Decimal)
        and cost_usd.is_finite()
        and cost_usd >= 0
    )
    if not valid_numbers:
        return None, reservation.reserved_cost_usd, "PROVIDER_USAGE_EXCEEDED_RESERVATION"
    sanitized_attempt_count = (
        attempt_count
        if isinstance(attempt_count, int) and not isinstance(attempt_count, bool) and attempt_count >= 0
        else 0
    )
    sanitized = ProviderUsage(input_tokens, output_tokens, cost_usd, sanitized_attempt_count)
    expected_cost = _configured_cost(input_tokens, output_tokens)
    if (
        input_tokens > reservation.reserved_input_tokens
        or output_tokens > reservation.max_output_tokens
        or attempt_count != 1
        or cost_usd != expected_cost
        or _stored_cost(expected_cost) > reservation.reserved_cost_usd
    ):
        conservative_cost = max(
            reservation.reserved_cost_usd,
            _stored_cost(expected_cost),
            _stored_cost(cost_usd),
        )
        return sanitized, conservative_cost, "PROVIDER_USAGE_EXCEEDED_RESERVATION"
    return sanitized, _stored_cost(expected_cost), None


async def _knowledge_actor(session: AsyncSession, organization_id: uuid.UUID, actor_user_id: uuid.UUID, property_id: uuid.UUID) -> None:
    await session.execute(text("SELECT set_config('app.current_user_id', :actor, true)"), {"actor": str(actor_user_id)})
    await require_live_principal(session, AuthContext(actor_user_id, organization_id, ""), writer=False)
    if not await session.scalar(text("SELECT knowledge_query_source_access(:org,:property)"),
        {"org":organization_id,"property":property_id}):
        raise HTTPException(status_code=404,detail="Property not found")


async def query_property_knowledge(
    *,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    property_id: uuid.UUID,
    question: KnowledgeQuestionRequest,
    provider: KnowledgeAnswerProvider | None = None,
    provider_factory: Callable[[], KnowledgeAnswerProvider | None] | None = None,
    concurrency_guard: _KnowledgeConcurrencyGuard | None = None,
) -> KnowledgeAnswerOut:
    """Run Q&A in three short phases; a DB transaction never crosses the network."""
    guard = concurrency_guard or _qa_concurrency_guard
    # Resolve the tenant-local property before process-local backpressure so an
    # unknown/foreign identifier remains a stable 404 even under load.
    async with get_tenant_session(organization_id) as session:
        await _knowledge_actor(session, organization_id, actor_user_id, property_id)
        service = PropertyKnowledgeService(session, organization_id)
        prepared = await service.prepare_question(property_id, question)
        if not prepared.hits:
            return service._not_documented_answer()
    if provider is None and provider_factory is not None:
        try:
            provider = provider_factory()
        except Exception:
            raise KnowledgeQueryUnavailable("QA_TEMPORARILY_UNAVAILABLE") from None
    if provider is None:
        raise KnowledgeQueryUnavailable(
            "QA_DISABLED" if not settings.declaration_qa_enabled else "QA_TEMPORARILY_UNAVAILABLE"
        )
    if not guard.acquire(settings.declaration_qa_max_concurrency):
        raise KnowledgeQueryBudgetExceeded("QA_CONCURRENCY_EXCEEDED")
    try:
        async with get_tenant_session(organization_id) as session:
            await _knowledge_actor(session, organization_id, actor_user_id, property_id)
            try:
                estimate = provider.estimate_reservation(
                    prepared.request,
                    max_output_tokens=settings.declaration_qa_max_output_tokens,
                )
            except ProviderBudgetExceeded as error:
                raise KnowledgeQueryBudgetExceeded("QA_BUDGET_EXCEEDED") from error
            except (ProviderTimeout, ProviderUnavailable, ProviderInvalidCitation, ProviderInvalidOutput):
                raise KnowledgeQueryUnavailable("QA_TEMPORARILY_UNAVAILABLE") from None
            except Exception:
                raise KnowledgeQueryUnavailable("QA_TEMPORARILY_UNAVAILABLE") from None
            if not isinstance(estimate, ProviderReservationEstimate) or (
                estimate.attempt_count != 1
                or estimate.attempt_input_tokens != (estimate.input_tokens,)
                or isinstance(estimate.input_tokens, bool)
                or estimate.input_tokens <= 0
            ):
                raise KnowledgeQueryUnavailable("QA_TEMPORARILY_UNAVAILABLE")
            now = datetime.now(timezone.utc)
            window_start = _utc_window_start(now, settings.declaration_qa_budget_window_minutes)
            reserved_cost = _stored_cost(_configured_cost(
                estimate.input_tokens, settings.declaration_qa_max_output_tokens,
            ))
            repository = PropertyKnowledgeQueryRepository(session, organization_id)
            provider_name, model = _provider_identity(provider)
            reservation = await repository.reserve_query(
                property_id=property_id,
                actor_user_id=actor_user_id,
                budget_window_start=window_start,
                now=now,
                reservation_expires_at=now + timedelta(seconds=settings.declaration_qa_reservation_seconds),
                provider=provider_name,
                model=model,
                reserved_input_tokens=estimate.input_tokens,
                max_output_tokens=settings.declaration_qa_max_output_tokens,
                reserved_cost_usd=reserved_cost,
                budget_cap_usd=settings.declaration_qa_max_cost_usd,
            )
            budget_denied = reservation is None

        if budget_denied:
            raise KnowledgeQueryBudgetExceeded("QA_BUDGET_EXCEEDED")

        started = time.monotonic()
        provider_result: KnowledgeProviderResult | None = None
        provider_error: Exception | None = None
        raw_usage: object = None
        try:
            provider_result = await provider.answer(
                prepared.request,
                max_output_tokens=reservation.max_output_tokens,
            )
            raw_usage = getattr(provider_result, "usage", None)
            if not isinstance(provider_result, KnowledgeProviderResult):
                provider_error = ProviderInvalidOutput("Knowledge provider result is invalid.", usage=raw_usage)
                provider_result = None
        except (ProviderTimeout, ProviderUnavailable, ProviderInvalidCitation, ProviderInvalidOutput, ProviderBudgetExceeded) as error:
            provider_error = error
            raw_usage = error.usage
        except Exception:
            # Do not surface arbitrary adapter errors or retain their message.
            provider_error = ProviderUnavailable()
        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        usage, finalized_cost, mismatch = _usage_for_reservation(raw_usage, reservation)
        if provider_error is not None and raw_usage is None:
            finalized_cost = reservation.reserved_cost_usd
        outcome = mismatch or (
            getattr(provider_error, "code", None) if provider_error is not None else "SUCCESS"
        )
        error_code = mismatch or (getattr(provider_error, "code", None) if provider_error is not None else None)
        answer: KnowledgeAnswerOut | None = None
        finalized = False
        try:
            async with get_tenant_session(organization_id) as session:
                # Resolve the immutable stored actor before reading any answer sources.
                await session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor":str(actor_user_id)})
                persisted_actor = await session.scalar(text("SELECT knowledge_query_actor(:org,:run)"), {"org": organization_id, "run": reservation.run_id})
                if persisted_actor != actor_user_id:
                    raise HTTPException(status_code=401, detail="Not authenticated")
                await _knowledge_actor(session, organization_id, persisted_actor, property_id)
                if provider_result is not None and mismatch is None:
                    answer = await PropertyKnowledgeService(session, organization_id).finalize_provider_result(
                        prepared, provider_result,
                    )
                    if answer.answer == UNVERIFIED_ANSWER_TEXT:
                        outcome = "UNVERIFIED_OUTPUT"
                        error_code = "UNVERIFIED_OUTPUT"
                finalized = await PropertyKnowledgeQueryRepository(session, organization_id).finalize_query(
                    reservation,
                    now=datetime.now(timezone.utc),
                    estimated_cost_usd=finalized_cost,
                    input_tokens=usage.input_tokens if usage is not None else 0,
                    output_tokens=usage.output_tokens if usage is not None else 0,
                    latency_ms=latency_ms,
                    outcome=outcome,
                    error_code=error_code,
                )
        except HTTPException:
            # Commit safe cancellation separately after the effect transaction rolls back.
            async with get_tenant_session(organization_id) as session:
                await session.execute(text("SELECT set_config('app.current_user_id', :actor, true)"), {"actor": str(actor_user_id)})
                await session.execute(text("SELECT cancel_unauthorized_knowledge(:org, :run)"), {"org": organization_id, "run": reservation.run_id})
            raise

        if not finalized:
            raise KnowledgeQueryUnavailable("QA_TEMPORARILY_UNAVAILABLE")
        if mismatch is not None:
            raise KnowledgeQueryBudgetExceeded("QA_BUDGET_EXCEEDED")
        if isinstance(provider_error, ProviderBudgetExceeded):
            raise KnowledgeQueryBudgetExceeded("QA_BUDGET_EXCEEDED")
        if isinstance(provider_error, (ProviderInvalidCitation, ProviderInvalidOutput)):
            return PropertyKnowledgeService._unverified_answer()
        if isinstance(provider_error, (ProviderTimeout, ProviderUnavailable)):
            raise KnowledgeQueryUnavailable("QA_TEMPORARILY_UNAVAILABLE")
        if provider_error is not None:
            raise KnowledgeQueryUnavailable("QA_TEMPORARILY_UNAVAILABLE")
        if answer is None:
            return PropertyKnowledgeService._unverified_answer()
        return answer
    finally:
        guard.release()
