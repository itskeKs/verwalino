"""Test fixtures for M0 tests.

Provides:
  - A real PostgreSQL test database (via docker-compose).
  - Two Organizations (org_a, org_b) with users and memberships.
  - Authenticated HTTPX test clients for each org.
  - Direct DB sessions (app_rw role) for RLS-level tests.
  - A migrator session for setup (bypasses RLS).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date
from typing import AsyncGenerator

import pytest
import pytest_asyncio
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.security import hash_password, create_session_token
from app.core.config import settings
from app.core.db import override_engine
from app.main import app

# ---------------------------------------------------------------------------
# Database URLs (from env or defaults matching docker-compose)
# ---------------------------------------------------------------------------
MIGRATOR_URL = os.environ.get(
    "HV_DATABASE_URL_MIGRATOR",
    "postgresql+asyncpg://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
)
APP_RW_URL = os.environ.get(
    "HV_DATABASE_URL",
    "postgresql+asyncpg://app_rw:app_secret@localhost:5433/hausverwaltung",
)

# Sync URL for running Alembic migrations
MIGRATOR_SYNC_URL = os.environ.get(
    "HV_DATABASE_URL_SYNC",
    "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
)


# ---------------------------------------------------------------------------
# Run Alembic migrations before tests
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def run_migrations():
    """Run Alembic migrations using the migrator role."""
    from alembic.config import Config
    from alembic import command

    alembic_cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", MIGRATOR_SYNC_URL)
    alembic_cfg.set_main_option(
        "script_location",
        os.path.join(os.path.dirname(__file__), "..", "migrations"),
    )

    # Downgrade to clean state, then upgrade to head
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")
    yield


# ---------------------------------------------------------------------------
# Inject test engine into the app's DB module (once, at session start)
# ---------------------------------------------------------------------------
_test_app_rw_engine = None


@pytest_asyncio.fixture(autouse=True)
async def inject_test_engine(run_migrations):
    """Replace the app's DB engine with a fresh one per test function.

    This avoids asyncpg event-loop mismatch: each test function gets
    its own event loop (pytest-asyncio default), so the engine must be
    recreated to use the current loop.
    """
    global _test_app_rw_engine
    _test_app_rw_engine = create_async_engine(
        APP_RW_URL, pool_pre_ping=True, pool_size=10, max_overflow=5
    )
    override_engine(_test_app_rw_engine)
    yield
    await _test_app_rw_engine.dispose()


# ---------------------------------------------------------------------------
# Seed data: two organizations with users and memberships
# ---------------------------------------------------------------------------
_seed_cache: dict | None = None


@pytest.fixture(scope="session")
def seed_data(run_migrations):
    """Create two orgs, users, memberships, and sample mandates using the migrator role."""
    global _seed_cache
    if _seed_cache is not None:
        return _seed_cache

    org_a_id = uuid.uuid4()
    org_b_id = uuid.uuid4()
    user_a_admin_id = uuid.uuid4()
    user_a_clerk_id = uuid.uuid4()
    user_a_readonly_id = uuid.uuid4()
    user_b_admin_id = uuid.uuid4()
    mandate_a_id = uuid.uuid4()
    mandate_b_id = uuid.uuid4()

    pw_hash = hash_password("testpass123")

    # Use synchronous psycopg2 for seeding (avoids async event loop issues in session fixture)
    import psycopg2
    sync_url = MIGRATOR_SYNC_URL
    # Parse: postgresql://user:pass@host:port/db
    conn = psycopg2.connect(sync_url)
    conn.autocommit = False
    try:
        cur = conn.cursor()

        # Organizations
        cur.execute(
            "INSERT INTO organization (id, name) VALUES (%s, %s)",
            (str(org_a_id), "Verwaltung Alpha"),
        )
        cur.execute(
            "INSERT INTO organization (id, name) VALUES (%s, %s)",
            (str(org_b_id), "Verwaltung Beta"),
        )

        # Users for Org A
        for uid, email, name in [
            (user_a_admin_id, "admin@alpha.de", "Admin Alpha"),
            (user_a_clerk_id, "clerk@alpha.de", "Clerk Alpha"),
            (user_a_readonly_id, "reader@alpha.de", "Reader Alpha"),
        ]:
            cur.execute(
                'INSERT INTO "user" (id, organization_id, email, password_hash, display_name) '
                "VALUES (%s, %s, %s, %s, %s)",
                (str(uid), str(org_a_id), email, pw_hash, name),
            )

        # User for Org B
        cur.execute(
            'INSERT INTO "user" (id, organization_id, email, password_hash, display_name) '
            "VALUES (%s, %s, %s, %s, %s)",
            (str(user_b_admin_id), str(org_b_id), "admin@beta.de", pw_hash, "Admin Beta"),
        )

        # Memberships
        for uid, org, role in [
            (user_a_admin_id, org_a_id, "OWNER_ADMIN"),
            (user_a_clerk_id, org_a_id, "CLERK"),
            (user_a_readonly_id, org_a_id, "READ_ONLY"),
            (user_b_admin_id, org_b_id, "OWNER_ADMIN"),
        ]:
            cur.execute(
                "INSERT INTO membership (id, organization_id, user_id, role) "
                "VALUES (%s, %s, %s, %s)",
                (str(uuid.uuid4()), str(org), str(uid), role),
            )

        # Mandates
        cur.execute(
            "INSERT INTO management_mandate (id, organization_id, mandate_type, label, start_date) "
            "VALUES (%s, %s, %s, %s, %s)",
            (str(mandate_a_id), str(org_a_id), "WEG", "WEG Hauptstr 5", date(2025, 1, 1)),
        )
        cur.execute(
            "INSERT INTO management_mandate (id, organization_id, mandate_type, label, start_date) "
            "VALUES (%s, %s, %s, %s, %s)",
            (str(mandate_b_id), str(org_b_id), "MIET", "Miet Bahnhofstr 12", date(2025, 6, 1)),
        )

        # Audit events
        cur.execute(
            "INSERT INTO audit_event (id, organization_id, actor_user_id, entity_type, entity_id, action) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), str(org_a_id), str(user_a_admin_id), "organization", str(org_a_id), "org.created"),
        )
        cur.execute(
            "INSERT INTO audit_event (id, organization_id, actor_user_id, entity_type, entity_id, action) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), str(org_b_id), str(user_b_admin_id), "organization", str(org_b_id), "org.created"),
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    _seed_cache = {
        "org_a_id": org_a_id,
        "org_b_id": org_b_id,
        "user_a_admin_id": user_a_admin_id,
        "user_a_clerk_id": user_a_clerk_id,
        "user_a_readonly_id": user_a_readonly_id,
        "user_b_admin_id": user_b_admin_id,
        "mandate_a_id": mandate_a_id,
        "mandate_b_id": mandate_b_id,
    }
    return _seed_cache


# ---------------------------------------------------------------------------
# Authenticated test clients
# ---------------------------------------------------------------------------
def _make_session_cookie(user_id: uuid.UUID, org_id: uuid.UUID, role: str) -> str:
    return create_session_token(user_id, org_id, role)


@pytest_asyncio.fixture
async def client_a_admin(seed_data) -> AsyncGenerator[httpx.AsyncClient, None]:
    token = _make_session_cookie(
        seed_data["user_a_admin_id"], seed_data["org_a_id"], "OWNER_ADMIN"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies={settings.session_cookie_name: token},
    ) as client:
        yield client


@pytest_asyncio.fixture
async def client_a_clerk(seed_data) -> AsyncGenerator[httpx.AsyncClient, None]:
    token = _make_session_cookie(
        seed_data["user_a_clerk_id"], seed_data["org_a_id"], "CLERK"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies={settings.session_cookie_name: token},
    ) as client:
        yield client


@pytest_asyncio.fixture
async def client_a_readonly(seed_data) -> AsyncGenerator[httpx.AsyncClient, None]:
    token = _make_session_cookie(
        seed_data["user_a_readonly_id"], seed_data["org_a_id"], "READ_ONLY"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies={settings.session_cookie_name: token},
    ) as client:
        yield client


@pytest_asyncio.fixture
async def client_b_admin(seed_data) -> AsyncGenerator[httpx.AsyncClient, None]:
    token = _make_session_cookie(
        seed_data["user_b_admin_id"], seed_data["org_b_id"], "OWNER_ADMIN"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies={settings.session_cookie_name: token},
    ) as client:
        yield client


@pytest_asyncio.fixture
async def client_unauth() -> AsyncGenerator[httpx.AsyncClient, None]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Direct DB sessions for RLS-level tests
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def app_rw_session(seed_data) -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(APP_RW_URL, pool_size=2)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                yield session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def migrator_session(seed_data) -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(MIGRATOR_URL, pool_size=2)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                yield session
    finally:
        await engine.dispose()
