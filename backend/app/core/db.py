"""Database engine, session factory, and tenant-context injection.

Key design (architecture.md section 2.3 / 7.2):
  - The app connects as `app_rw` (NO BYPASSRLS).
  - Before any tenant query, `SET LOCAL app.current_org_id = '<uuid>'` is
    executed inside the transaction.  SET LOCAL is transaction-scoped and
    resets on commit/rollback -- safe for connection pools.
  - If no tenant context is set, the GUC defaults to an impossible UUID
    (00000000-...) so RLS returns zero rows (fail-closed).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import text

from app.core.config import settings

# ---------------------------------------------------------------------------
# Engine (app_rw -- NO BYPASSRLS)
# ---------------------------------------------------------------------------
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Lazy-create the engine on first use (avoids event-loop-at-import issues)."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=5,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


def override_engine(engine: AsyncEngine) -> None:
    """Replace the engine (used in tests to inject a test-scoped engine)."""
    global _engine, _session_factory
    _engine = engine
    _session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


# The "impossible" UUID that the GUC falls back to when unset.
# Every `organization_id` is a real v4 UUID, so this will never match.
_NULL_ORG = "00000000-0000-0000-0000-000000000000"


async def set_tenant_context(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Set the tenant context for the current transaction (SET LOCAL)."""
    await session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"),
        {"org": str(org_id)},
    )


@asynccontextmanager
async def get_tenant_session(
    org_id: uuid.UUID,
) -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional session with tenant context already set."""
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            await set_tenant_context(session, org_id)
            yield session


@asynccontextmanager
async def get_unscoped_session() -> AsyncGenerator[AsyncSession, None]:
    """Session WITHOUT tenant context -- returns zero rows on tenant tables (fail-closed).

    Useful only for auth lookups on the `organization` table (global).
    """
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            # Explicitly set the impossible UUID so that any accidental
            # tenant query returns nothing.
            await session.execute(
                text("SELECT set_config('app.current_org_id', :org, true)"),
                {"org": _NULL_ORG},
            )
            yield session
