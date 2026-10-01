"""Bounded keyset pages with signed, actor/query-bound continuation cursors."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Generic, TypeVar

from fastapi import HTTPException, Query
from itsdangerous import BadData, URLSafeTimedSerializer
from pydantic import BaseModel
from sqlalchemy import func, or_, select, tuple_

from app.core.config import settings

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None


@dataclass(frozen=True)
class PageQuery:
    limit: int
    q: str
    cursor: str | None


def page_query(limit: int = Query(50, ge=1, le=200), q: str = Query("", max_length=100),
               cursor: str | None = Query(None, max_length=4096)) -> PageQuery:
    return PageQuery(limit, q.strip(), cursor)


async def page_rows(session, auth, model, sort_column, search_columns, query: PageQuery,
                    *, filters=(), context: str = ""):
    signer = URLSafeTimedSerializer(settings.session_secret, salt="hv-selector-page-v1")
    scope = [str(auth.organization_id), str(auth.user_id), model.__tablename__, query.q, context]
    order = func.lower(func.coalesce(sort_column, ""))
    statement = select(model, order.label("page_order")).where(
        model.organization_id == auth.organization_id, *filters)
    if query.q:
        # Escape SQL wildcards: a user's % or _ is literal search input.
        escaped = query.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(or_(*(column.ilike(f"%{escaped}%", escape="\\") for column in search_columns)))
    if query.cursor:
        try:
            payload = signer.loads(query.cursor, max_age=86400)
            if not isinstance(payload, dict) or payload.get("scope") != scope or not isinstance(payload.get("order"), str):
                raise ValueError("Cursor context mismatch")
            last_id = uuid.UUID(payload["id"])
        except (BadData, ValueError, TypeError, KeyError):
            raise HTTPException(422, "Invalid or expired page cursor") from None
        statement = statement.where(tuple_(order, model.id) > tuple_(payload["order"], last_id))
    rows = (await session.execute(statement.order_by(order, model.id).limit(query.limit + 1))).all()
    visible = rows[:query.limit]
    cursor = None
    if len(rows) > query.limit:
        last, key = visible[-1]
        cursor = signer.dumps({"scope": scope, "order": key, "id": str(last.id)})
    return [row[0] for row in visible], cursor
