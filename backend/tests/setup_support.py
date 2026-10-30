"""Rollback-only fresh-installation fixture shared by setup API and SQL tests."""
import hashlib
import httpx
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.core import db
from app.main import app
from tests.conftest import MIGRATOR_URL

TOKEN = "12" * 32
BODY = {"token": TOKEN, "organization_name": "Hausverwaltung mit langem Namen GmbH",
        "admin_email": " Setup@Example.test ", "admin_name": "Erika Mustermann",
        "password": "A-long-setup-password-123"}


@pytest_asyncio.fixture
async def fresh_setup(monkeypatch):
    """Keep shared seeds intact; every API statement still runs with app_rw ACL/RLS."""
    engine = create_async_engine(MIGRATOR_URL)
    async with engine.connect() as connection, connection.begin() as transaction:
        await connection.execute(text("TRUNCATE public.organization CASCADE"))
        if await connection.scalar(text("SELECT to_regclass('public.setup_token')")):
            await connection.execute(text("TRUNCATE public.setup_token"))
            await connection.execute(text("INSERT INTO public.setup_token(token_hash) VALUES (:hash)"),
                                     {"hash": hashlib.sha256(TOKEN.encode()).digest()})
        await connection.execute(text("SET LOCAL ROLE app_rw"))
        monkeypatch.setattr(db, "_session_factory", async_sessionmaker(
            connection, expire_on_commit=False, join_transaction_mode="create_savepoint"))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client, connection
        await transaction.rollback()
    await engine.dispose()
