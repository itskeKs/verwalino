"""Scope of production wait budgets and real test-database diagnostics."""
import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from tests.test_rent_changes import rent_db  # noqa: F401


@pytest.mark.parametrize('path', ['deletion', 'quota'])
async def test_only_admitted_lifecycle_and_hosted_quota_writes_cap_waits_at_thirty_seconds(rent_db, monkeypatch, path):
    from app.services.organization_deletion_service import lifecycle_lock
    from app.services.quota_service import lock_quota
    monkeypatch.setattr(settings, 'deployment_mode', 'hosted')
    await rent_db.execute(text("SET LOCAL lock_timeout='0'"))
    await (lifecycle_lock if path == 'deletion' else lock_quota)(rent_db, uuid.uuid4())
    assert await rent_db.scalar(text('SHOW lock_timeout')) == '30s'
    await rent_db.rollback()


async def test_lock_timeout_dump_identifies_waiter_holder_and_transaction_age(rent_db, capsys):
    key = f'diagnostic-test:{uuid.uuid4()}'
    holder = await rent_db.scalar(text('SELECT pg_backend_pid()'))
    await rent_db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'), {'key': key})
    try:
        async with AsyncSession(rent_db.bind) as waiter:
            await waiter.execute(text("SET LOCAL lock_timeout='100ms'"))
            pid = await waiter.scalar(text('SELECT pg_backend_pid()'))
            with pytest.raises(DBAPIError) as error:
                await waiter.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'), {'key': key})
            assert error.value.orig.sqlstate == '55P03'
            await waiter.rollback()
        diagnostic = capsys.readouterr().err
        assert f'pid={pid} waiting_statement=' in diagnostic
        assert 'pg_stat_activity=' in diagnostic and 'pg_locks=' in diagnostic
        assert f'"pid": {holder}' in diagnostic
        assert 'blocking_pids' in diagnostic and 'xact_age' in diagnostic
        assert 'pg_advisory_xact_lock' in diagnostic
    finally:
        await rent_db.rollback()


async def test_completion_retry_does_not_swallow_unrelated_errors_or_cancellation():
    from app.core.locking import retry_worker_completion
    for error in (ValueError('not contention'), asyncio.CancelledError()):
        async def failed():
            raise error
        with pytest.raises(type(error)):
            await retry_worker_completion(failed)
