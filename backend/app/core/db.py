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


_worker_engine: AsyncEngine | None = None
_worker_session_factory: async_sessionmaker[AsyncSession] | None = None


def override_worker_engine(engine: AsyncEngine | None) -> None:
    """Inject a separate worker engine in disposable integration tests."""
    global _worker_engine, _worker_session_factory
    _worker_engine = engine
    _worker_session_factory = async_sessionmaker(engine, expire_on_commit=False) if engine else None


def get_worker_session_factory() -> async_sessionmaker[AsyncSession]:
    global _worker_engine, _worker_session_factory
    if _worker_session_factory is None:
        if settings.database_url_worker is None:
            raise RuntimeError("HV_DATABASE_URL_WORKER is required for declaration workers")
        _worker_engine = create_async_engine(settings.database_url_worker.get_secret_value(), pool_pre_ping=True,
            pool_size=5, max_overflow=2)
        _worker_session_factory = async_sessionmaker(_worker_engine, expire_on_commit=False)
    return _worker_session_factory


@asynccontextmanager
async def get_worker_tenant_session(org_id: uuid.UUID) -> AsyncGenerator[AsyncSession, None]:
    async with get_worker_session_factory()() as session, session.begin():
        valid = await session.scalar(text("""WITH RECURSIVE inherited(roleid) AS (
            SELECT roleid FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user)
            UNION SELECT m.roleid FROM pg_auth_members m JOIN inherited p ON m.member=p.roleid)
            SELECT current_user='app_worker' AND r.rolinherit AND NOT r.rolsuper
            AND NOT r.rolbypassrls AND NOT r.rolcreaterole AND NOT r.rolcreatedb AND NOT r.rolreplication
            AND NOT EXISTS(SELECT 1 FROM pg_auth_members WHERE roleid=r.oid)
            AND pg_has_role(current_user,'app_rw','USAGE')
            AND EXISTS(SELECT 1 FROM inherited WHERE roleid=(SELECT oid FROM pg_roles WHERE rolname='app_rw'))
            AND NOT EXISTS(SELECT 1 FROM inherited WHERE roleid<>(SELECT oid FROM pg_roles WHERE rolname='app_rw'))
            AND EXISTS(SELECT 1 FROM pg_roles w WHERE w.rolname='app_rw' AND NOT w.rolsuper
              AND NOT w.rolbypassrls AND NOT w.rolcreaterole AND NOT w.rolcreatedb AND NOT w.rolreplication)
            FROM pg_roles r WHERE r.rolname=current_user"""))
        if not valid:
            raise RuntimeError("Declaration workers require the isolated app_worker database role")
        await set_tenant_context(session, org_id)
        yield session


@asynccontextmanager
async def get_worker_unscoped_session() -> AsyncGenerator[AsyncSession, None]:
    async with get_worker_tenant_session(uuid.UUID(_NULL_ORG)) as session:
        yield session


_review_engine: AsyncEngine | None = None
_review_session_factory: async_sessionmaker[AsyncSession] | None = None


def override_review_engine(engine: AsyncEngine | None) -> None:
    global _review_engine, _review_session_factory
    _review_engine = engine
    _review_session_factory = async_sessionmaker(engine, expire_on_commit=False) if engine else None


def get_review_session_factory() -> async_sessionmaker[AsyncSession]:
    global _review_engine, _review_session_factory
    if _review_session_factory is None:
        if settings.database_url_review is None:
            raise RuntimeError('HV_DATABASE_URL_REVIEW is required for human review validation')
        _review_engine = create_async_engine(settings.database_url_review.get_secret_value(), pool_pre_ping=True,
                                            pool_size=5, max_overflow=2)
        _review_session_factory = async_sessionmaker(_review_engine, expire_on_commit=False)
    return _review_session_factory


REVIEW_ROLE_CHECK = """SELECT current_user='app_review' AND session_user=current_user AND r.rolcanlogin
    AND NOT r.rolinherit AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication
    AND NOT EXISTS(SELECT 1 FROM pg_auth_members WHERE roleid=r.oid OR member=r.oid)
    AND NOT EXISTS(SELECT 1 FROM pg_class WHERE relowner=r.oid)
    AND NOT EXISTS(SELECT 1 FROM pg_proc WHERE proowner=r.oid)
    AND NOT EXISTS(SELECT 1 FROM pg_namespace WHERE nspowner=r.oid)
    AND NOT EXISTS(SELECT 1 FROM pg_database WHERE datdba=r.oid)
    AND NOT has_schema_privilege(r.oid,'public','CREATE')
    AND NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') AND (
        has_table_privilege(r.oid,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
        OR has_any_column_privilege(r.oid,c.oid,'INSERT,UPDATE,REFERENCES')
        OR (c.relname NOT IN ('declaration_import','declaration_candidate','declaration_candidate_evidence','declaration_candidate_issue')
          AND has_any_column_privilege(r.oid,c.oid,'SELECT'))))
    FROM pg_roles r WHERE r.rolname=current_user"""


@asynccontextmanager
async def get_review_tenant_session(org_id: uuid.UUID) -> AsyncGenerator[AsyncSession, None]:
    async with get_review_session_factory()() as session, session.begin():
        if not await session.scalar(text(REVIEW_ROLE_CHECK)):
            raise RuntimeError('Human reviews require the isolated app_review database role')
        await set_tenant_context(session, org_id)
        yield session
