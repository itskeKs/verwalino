"""Read-only API routes for approved property knowledge."""

from __future__ import annotations

import uuid
import inspect
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.live_principal import get_live_member_db
from app.core.tenant import AuthContext, get_current_user
from app.declarations.knowledge_schemas import (
    KnowledgeAnswerOut,
    KnowledgeQuestionRequest,
    KnowledgeSearchResponse,
    PropertyKnowledgeOverview,
    RuleOut,
)
from app.services.property_knowledge_service import (
    KnowledgeQueryBudgetExceeded,
    KnowledgeQueryUnavailable,
    PropertyKnowledgeNotFound,
    PropertyKnowledgeService,
    query_property_knowledge,
)

RuleStatus = Literal[
    "EFFECTIVE", "SUPERSEDED", "NEEDS_CLARIFICATION", "RETRACTED",
]
_HISTORY_STATUSES = frozenset(("SUPERSEDED", "RETRACTED"))
_PROPERTY_NOT_FOUND_DETAIL = "Property not found"
_knowledge_provider_cache = None


@asynccontextmanager
async def _knowledge_router_lifespan(_app):
    try:
        yield
    finally:
        await close_knowledge_provider()


router = APIRouter(
    prefix="/api/properties",
    tags=["property-knowledge"],
    lifespan=_knowledge_router_lifespan,
)


def get_knowledge_provider():
    """Lazily construct the optional adapter only when this endpoint needs it."""
    global _knowledge_provider_cache
    if not settings.declaration_qa_enabled:
        return None
    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    if not key:
        return None
    if _knowledge_provider_cache is None:
        from openai import AsyncOpenAI

        from app.declarations.openai_knowledge_provider import OpenAIKnowledgeProvider

        _knowledge_provider_cache = OpenAIKnowledgeProvider(client=AsyncOpenAI(api_key=key))
    return _knowledge_provider_cache


def get_knowledge_provider_source(request: Request):
    """Return, but do not invoke, the override-aware provider constructor."""
    return request.app.dependency_overrides.get(
        get_knowledge_provider, get_knowledge_provider,
    )


async def close_knowledge_provider() -> None:
    """Close the lazily cached SDK client during application shutdown."""
    global _knowledge_provider_cache
    provider, _knowledge_provider_cache = _knowledge_provider_cache, None
    close = getattr(getattr(provider, "_client", None), "close", None)
    if close is not None:
        result = close()
        if inspect.isawaitable(result):
            await result


@router.get("/{property_id}/rules", response_model=list[RuleOut])
async def list_property_rules(
    property_id: uuid.UUID,
    status: list[RuleStatus] | None = Query(default=None),
    include_history: bool = False,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
) -> list[RuleOut]:
    history_requested = include_history or bool(
        status and any(item in _HISTORY_STATUSES for item in status)
    )
    try:
        overview = await PropertyKnowledgeService(db, auth.organization_id).overview(
            property_id,
            include_history=history_requested,
        )
    except PropertyKnowledgeNotFound as exc:
        raise HTTPException(status_code=404, detail=_PROPERTY_NOT_FOUND_DETAIL) from exc

    rules = [rule for topic_rules in overview.rules_by_topic.values() for rule in topic_rules]
    if status is not None:
        rules = [rule for rule in rules if rule.status in status]
    return sorted(rules, key=lambda rule: (rule.topic, rule.title, rule.id))


@router.get("/{property_id}/knowledge", response_model=PropertyKnowledgeOverview)
async def get_property_knowledge(
    property_id: uuid.UUID,
    include_history: bool = False,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
) -> PropertyKnowledgeOverview:
    try:
        return await PropertyKnowledgeService(db, auth.organization_id).overview(
            property_id,
            include_history=include_history,
        )
    except PropertyKnowledgeNotFound as exc:
        raise HTTPException(status_code=404, detail=_PROPERTY_NOT_FOUND_DETAIL) from exc


@router.get("/{property_id}/knowledge/search", response_model=KnowledgeSearchResponse)
async def search_property_knowledge(
    property_id: uuid.UUID,
    q: str = Query(...),
    limit: int = Query(default=20, ge=1, le=50),
    include_history: bool = False,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
) -> KnowledgeSearchResponse:
    try:
        return await PropertyKnowledgeService(db, auth.organization_id).search(
            property_id,
            q,
            limit=limit,
            include_history=include_history,
        )
    except PropertyKnowledgeNotFound as exc:
        raise HTTPException(status_code=404, detail=_PROPERTY_NOT_FOUND_DETAIL) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Query must not be blank") from exc


@router.post("/{property_id}/knowledge/query", response_model=KnowledgeAnswerOut)
async def query_property_knowledge_endpoint(
    property_id: uuid.UUID,
    body: KnowledgeQuestionRequest,
    auth: AuthContext = Depends(get_current_user),
    provider_source=Depends(get_knowledge_provider_source),
) -> KnowledgeAnswerOut:
    """Bounded Q&A; no request-scoped database session crosses the provider call."""
    try:
        return await query_property_knowledge(
            organization_id=auth.organization_id,
            actor_user_id=auth.user_id,
            session_version=auth.session_version,
            property_id=property_id,
            question=body,
            provider_factory=provider_source,
        )
    except PropertyKnowledgeNotFound as exc:
        raise HTTPException(status_code=404, detail=_PROPERTY_NOT_FOUND_DETAIL) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid knowledge question") from exc
    except KnowledgeQueryBudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except KnowledgeQueryUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=exc.code,
            headers={"Retry-After": "30"} if exc.code == "QA_TEMPORARILY_UNAVAILABLE" else None,
        ) from exc
