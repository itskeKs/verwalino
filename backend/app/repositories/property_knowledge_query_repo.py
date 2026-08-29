"""Tenant- and property-scoped SQL primitives for approved knowledge retrieval."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from sqlalchemy import String, and_, case, cast, exists, func, literal, null, or_, select, union_all, update, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.approved_fact_source import ApprovedFactSource
from app.models.property import Property
from app.models.property_document import PropertyDocument
from app.models.property_document_relation import PropertyDocumentRelation
from app.models.property_rule import PropertyRule, PropertyRuleRelation, PropertyRuleSource
from app.models.property_knowledge_query_run import PropertyKnowledgeQueryRun


@dataclass(frozen=True)
class KnowledgeQueryReservation:
    """Detached, non-sensitive reservation data retained across the provider call."""

    run_id: uuid.UUID
    budget_window_start: datetime
    reserved_input_tokens: int
    max_output_tokens: int
    reserved_cost_usd: Decimal


class PropertyKnowledgeQueryRepository:
    """All statements are explicitly constrained by organization and property."""

    def __init__(self, session: AsyncSession, organization_id: uuid.UUID):
        self._session = session
        self._organization_id = organization_id

    async def get_property(self, property_id: uuid.UUID) -> Property | None:
        return await self._session.scalar(
            select(Property).where(
                Property.organization_id == self._organization_id,
                Property.id == property_id,
            )
        )

    async def reserve_query(
        self,
        *,
        property_id: uuid.UUID,
        budget_window_start: datetime,
        now: datetime,
        reservation_expires_at: datetime,
        provider: str,
        model: str,
        reserved_input_tokens: int,
        max_output_tokens: int,
        reserved_cost_usd: Decimal,
        budget_cap_usd: Decimal,
    ) -> KnowledgeQueryReservation | None:
        """Atomically reclaim expired rows and reserve one bounded Q&A attempt."""
        lock_key = f"{self._organization_id}:{budget_window_start.isoformat()}"
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:key AS text), 0))"),
            {"key": lock_key},
        )
        await self._session.execute(
            update(PropertyKnowledgeQueryRun).where(
                PropertyKnowledgeQueryRun.organization_id == self._organization_id,
                PropertyKnowledgeQueryRun.budget_window_start == budget_window_start,
                PropertyKnowledgeQueryRun.status == "RESERVED",
                PropertyKnowledgeQueryRun.reservation_expires_at <= now,
            ).values(
                status="EXPIRED",
                finalized_at=now,
                outcome="RESERVATION_EXPIRED",
                error_code="RESERVATION_EXPIRED",
            )
        )
        spent = await self._session.scalar(
            select(func.coalesce(func.sum(
                case(
                    (PropertyKnowledgeQueryRun.status == "FINALIZED", PropertyKnowledgeQueryRun.estimated_cost_usd),
                    else_=PropertyKnowledgeQueryRun.reserved_cost_usd,
                )
            ), Decimal("0"))).where(
                PropertyKnowledgeQueryRun.organization_id == self._organization_id,
                PropertyKnowledgeQueryRun.budget_window_start == budget_window_start,
                PropertyKnowledgeQueryRun.status.in_(("FINALIZED", "RESERVED")),
            )
        )
        current = Decimal(str(spent or "0"))
        if current + reserved_cost_usd > budget_cap_usd:
            return None
        run = PropertyKnowledgeQueryRun(
            organization_id=self._organization_id,
            property_id=property_id,
            budget_window_start=budget_window_start,
            provider=provider[:120],
            model=model[:120],
            reserved_cost_usd=reserved_cost_usd,
            reservation_expires_at=reservation_expires_at,
        )
        self._session.add(run)
        await self._session.flush()
        return KnowledgeQueryReservation(
            run_id=run.id,
            budget_window_start=budget_window_start,
            reserved_input_tokens=reserved_input_tokens,
            max_output_tokens=max_output_tokens,
            reserved_cost_usd=reserved_cost_usd,
        )

    async def finalize_query(
        self,
        reservation: KnowledgeQueryReservation,
        *,
        now: datetime,
        estimated_cost_usd: Decimal,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        outcome: str,
        error_code: str | None,
    ) -> bool:
        """Finalize only this reservation, under the same budget-window lock."""
        lock_key = f"{self._organization_id}:{reservation.budget_window_start.isoformat()}"
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:key AS text), 0))"),
            {"key": lock_key},
        )
        result = await self._session.execute(
            update(PropertyKnowledgeQueryRun).where(
                PropertyKnowledgeQueryRun.organization_id == self._organization_id,
                PropertyKnowledgeQueryRun.id == reservation.run_id,
                PropertyKnowledgeQueryRun.status == "RESERVED",
            ).values(
                status="FINALIZED",
                estimated_cost_usd=estimated_cost_usd,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                outcome=outcome[:64],
                error_code=error_code[:100] if error_code else None,
                finalized_at=now,
            )
        )
        return result.rowcount == 1

    async def list_documents(self, property_id: uuid.UUID) -> tuple[PropertyDocument, ...]:
        rows = await self._session.scalars(
            select(PropertyDocument).where(
                PropertyDocument.organization_id == self._organization_id,
                PropertyDocument.property_id == property_id,
                PropertyDocument.archived_at.is_(None),
            ).order_by(PropertyDocument.document_date, PropertyDocument.title, PropertyDocument.id)
        )
        return tuple(rows)

    async def list_document_relations(
        self, property_id: uuid.UUID, statuses: Iterable[str]
    ) -> tuple[PropertyDocumentRelation, ...]:
        source_document = aliased(PropertyDocument)
        target_document = aliased(PropertyDocument)
        rows = await self._session.scalars(
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
                PropertyDocumentRelation.status.in_(tuple(statuses)),
                source_document.archived_at.is_(None),
                target_document.archived_at.is_(None),
            ).order_by(
                PropertyDocumentRelation.source_document_id,
                PropertyDocumentRelation.target_document_id,
                PropertyDocumentRelation.relation_type,
                PropertyDocumentRelation.id,
            )
        )
        return tuple(rows)

    async def list_rules(self, property_id: uuid.UUID, statuses: Iterable[str]) -> tuple[PropertyRule, ...]:
        rows = await self._session.scalars(
            select(PropertyRule).where(
                PropertyRule.organization_id == self._organization_id,
                PropertyRule.property_id == property_id,
                PropertyRule.status.in_(tuple(statuses)),
                self._has_active_rule_source(PropertyRule.id, property_id),
            ).order_by(PropertyRule.topic, PropertyRule.title, PropertyRule.id)
        )
        return tuple(rows)

    async def list_rule_relations(self, property_id: uuid.UUID) -> tuple[PropertyRuleRelation, ...]:
        rows = await self._session.scalars(
            select(PropertyRuleRelation).where(
                PropertyRuleRelation.organization_id == self._organization_id,
                PropertyRuleRelation.property_id == property_id,
            ).order_by(
                PropertyRuleRelation.source_rule_id,
                PropertyRuleRelation.target_rule_id,
                PropertyRuleRelation.relation_type,
                PropertyRuleRelation.id,
            )
        )
        return tuple(rows)

    async def list_rule_sources(self, property_id: uuid.UUID, rule_id: uuid.UUID) -> tuple[tuple[PropertyRuleSource, str], ...]:
        rows = await self._session.execute(
            select(PropertyRuleSource, PropertyDocument.title).join(
                PropertyDocument,
                and_(
                    PropertyDocument.organization_id == PropertyRuleSource.organization_id,
                    PropertyDocument.property_id == PropertyRuleSource.property_id,
                    PropertyDocument.id == PropertyRuleSource.property_document_id,
                ),
            ).where(
                PropertyRuleSource.organization_id == self._organization_id,
                PropertyRuleSource.property_id == property_id,
                PropertyRuleSource.property_rule_id == rule_id,
                PropertyDocument.archived_at.is_(None),
            ).order_by(PropertyRuleSource.property_document_id, PropertyRuleSource.pdf_page, PropertyRuleSource.id)
        )
        return tuple(rows.tuples())

    async def list_fact_sources(
        self,
        property_id: uuid.UUID,
        *,
        subject_kind: str,
        subject_id: uuid.UUID,
        field_key: str,
        value_fingerprint: str,
        status: str,
    ) -> tuple[tuple[ApprovedFactSource, str], ...]:
        rows = await self._session.execute(
            select(ApprovedFactSource, PropertyDocument.title).join(
                PropertyDocument,
                and_(
                    PropertyDocument.organization_id == ApprovedFactSource.organization_id,
                    PropertyDocument.property_id == ApprovedFactSource.property_id,
                    PropertyDocument.id == ApprovedFactSource.property_document_id,
                ),
            ).where(
                ApprovedFactSource.organization_id == self._organization_id,
                ApprovedFactSource.property_id == property_id,
                ApprovedFactSource.subject_kind == subject_kind,
                ApprovedFactSource.subject_id == subject_id,
                ApprovedFactSource.field_key == field_key,
                ApprovedFactSource.value_fingerprint == value_fingerprint,
                ApprovedFactSource.status == status,
                PropertyDocument.archived_at.is_(None),
            ).order_by(
                ApprovedFactSource.property_document_id,
                ApprovedFactSource.pdf_page,
                ApprovedFactSource.id,
            )
        )
        return tuple(rows.tuples())

    async def search_candidates(
        self,
        property_id: uuid.UUID,
        normalized_query: str,
        *,
        statuses: Iterable[str],
        trigram_threshold: Decimal,
        limit: int,
    ) -> tuple[dict, ...]:
        """Return capped hit identities using German FTS plus a trigram fallback."""
        accepted = tuple(statuses)
        tsquery = func.websearch_to_tsquery("german", normalized_query)
        threshold = float(trigram_threshold)

        fact_similarity = func.similarity(func.lower(ApprovedFactSource.search_text), normalized_query.lower())
        fact_score = (
            literal(0.70) * func.ts_rank_cd(ApprovedFactSource.search_vector, tsquery)
            + literal(0.30) * fact_similarity
        )
        fact_match = or_(ApprovedFactSource.search_vector.op("@@")(tsquery), fact_similarity >= threshold)
        facts = select(
            literal("FACT").label("kind"),
            cast(null(), String).label("item_id"),
            ApprovedFactSource.subject_kind.label("subject_kind"),
            cast(ApprovedFactSource.subject_id, String).label("subject_id"),
            ApprovedFactSource.field_key.label("field_key"),
            ApprovedFactSource.value_fingerprint.label("value_fingerprint"),
            ApprovedFactSource.status.label("status"),
            ApprovedFactSource.field_key.label("title"),
            func.max(fact_score).label("score"),
        ).join(
            PropertyDocument,
            and_(
                PropertyDocument.organization_id == ApprovedFactSource.organization_id,
                PropertyDocument.property_id == ApprovedFactSource.property_id,
                PropertyDocument.id == ApprovedFactSource.property_document_id,
            ),
        ).where(
            ApprovedFactSource.organization_id == self._organization_id,
            ApprovedFactSource.property_id == property_id,
            ApprovedFactSource.subject_kind != "RULE",
            ApprovedFactSource.status.in_(accepted),
            PropertyDocument.archived_at.is_(None),
            fact_match,
        ).group_by(
            ApprovedFactSource.subject_kind,
            ApprovedFactSource.subject_id,
            ApprovedFactSource.field_key,
            ApprovedFactSource.value_fingerprint,
            ApprovedFactSource.status,
        )

        rule_similarity = func.similarity(func.lower(PropertyRule.search_text), normalized_query.lower())
        rule_score = (
            literal(0.70) * func.ts_rank_cd(PropertyRule.search_vector, tsquery)
            + literal(0.30) * rule_similarity
        )
        rule_match = or_(PropertyRule.search_vector.op("@@")(tsquery), rule_similarity >= threshold)
        rules = select(
            literal("RULE").label("kind"),
            cast(PropertyRule.id, String).label("item_id"),
            cast(null(), String).label("subject_kind"),
            cast(null(), String).label("subject_id"),
            cast(null(), String).label("field_key"),
            cast(null(), String).label("value_fingerprint"),
            PropertyRule.status.label("status"),
            PropertyRule.title.label("title"),
            rule_score.label("score"),
        ).where(
            PropertyRule.organization_id == self._organization_id,
            PropertyRule.property_id == property_id,
            PropertyRule.status.in_(accepted),
            self._has_active_rule_source(PropertyRule.id, property_id),
            rule_match,
        )

        candidates = union_all(facts, rules).subquery()
        rows = await self._session.execute(
            select(candidates).order_by(
                candidates.c.score.desc(),
                candidates.c.kind,
                candidates.c.title,
                candidates.c.item_id,
                candidates.c.subject_id,
                candidates.c.subject_kind,
                candidates.c.field_key,
                candidates.c.value_fingerprint,
                candidates.c.status,
            ).limit(limit)
        )
        return tuple(dict(row) for row in rows.mappings())

    async def get_rule(self, property_id: uuid.UUID, rule_id: uuid.UUID) -> PropertyRule | None:
        return await self._session.scalar(
            select(PropertyRule).where(
                PropertyRule.organization_id == self._organization_id,
                PropertyRule.property_id == property_id,
                PropertyRule.id == rule_id,
                self._has_active_rule_source(PropertyRule.id, property_id),
            )
        )

    def _has_active_rule_source(self, rule_id, property_id: uuid.UUID):
        return exists(
            select(1).select_from(PropertyRuleSource).join(
                PropertyDocument,
                and_(
                    PropertyDocument.organization_id == PropertyRuleSource.organization_id,
                    PropertyDocument.property_id == PropertyRuleSource.property_id,
                    PropertyDocument.id == PropertyRuleSource.property_document_id,
                ),
            ).where(
                PropertyRuleSource.organization_id == self._organization_id,
                PropertyRuleSource.property_id == property_id,
                PropertyRuleSource.property_rule_id == rule_id,
                PropertyDocument.archived_at.is_(None),
            )
        )
