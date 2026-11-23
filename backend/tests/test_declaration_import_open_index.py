"""The Meldungen read finds a user's open imports through an index, however many imports an organization has (#871, 0204).

The frame asks for the caller's Meldungen on every navigation of every user, so the read must cost what the user's open
imports cost and not what every import the organization ever created costs.
"""
import importlib
import json
from contextlib import asynccontextmanager

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.declarations.progress import MELDUNG_STATES
from app.repositories.declaration_import_repo import DeclarationImportRepository
from tests.conftest import MIGRATOR_SYNC_URL, MIGRATOR_URL

INDEX = 'ix_declaration_import_org_initiator_status'
MARKER = 'open-index-plan-test'
# What an organization that has used the import for a long time holds: thousands of ended imports, half of them the
# caller's own, and a few open ones.
ENDED = 5000
OPEN = ['QUEUED', 'NEEDS_REVIEW', 'FAILED']


@asynccontextmanager
async def migrator():
    engine = create_async_engine(MIGRATOR_URL, pool_size=1)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


def nodes(plan):
    """Every node of an EXPLAIN (FORMAT JSON) plan, the root first."""
    yield plan
    for child in plan.get('Plans', []):
        yield from nodes(child)


@pytest.mark.asyncio
async def test_the_read_of_a_users_open_imports_uses_the_index_when_the_organization_has_thousands_of_imports(seed_data, app_rw_session):
    org, caller, colleague = seed_data['org_a_id'], seed_data['user_a_clerk_id'], seed_data['user_a_admin_id']
    statuses = [status for status, state in MELDUNG_STATES.items() if state is not None]
    async with migrator() as seed:
        try:
            for user in (caller, colleague):
                await seed.execute(text("""INSERT INTO declaration_import (id, organization_id, mode, initiated_by, max_cost_usd, current_stage)
                    SELECT gen_random_uuid(), :org, 'NEW_PROPERTY', :user, 10, :marker FROM generate_series(1, :count)"""),
                    {'org': org, 'user': user, 'marker': MARKER, 'count': ENDED // 2})
            await seed.execute(text("UPDATE declaration_import SET status = 'CANCELLED' WHERE current_stage = :marker"), {'marker': MARKER})
            for status in OPEN:
                await seed.execute(text("""INSERT INTO declaration_import (id, organization_id, mode, initiated_by, max_cost_usd, current_stage, status)
                    VALUES (gen_random_uuid(), :org, 'NEW_PROPERTY', :user, 10, :marker, :status)"""),
                    {'org': org, 'user': caller, 'marker': MARKER, 'status': status})
            own = set((await seed.execute(text("SELECT id FROM declaration_import WHERE current_stage = :marker AND status <> 'CANCELLED'"),
                                          {'marker': MARKER})).scalars())
            await seed.commit()
            # Statistics as a database that has held these rows for a while has them.
            await seed.execute(text('ANALYZE declaration_import'))
            await seed.commit()

            statements = []

            def capture(connection, cursor, statement, parameters, context, executemany):
                if 'declaration_import.initiated_by' in statement:
                    statements.append((statement, parameters))

            engine = app_rw_session.sync_session.get_bind()
            await app_rw_session.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {'org': str(org)})
            await app_rw_session.execute(text("SELECT set_config('app.current_user_id', :user, true)"), {'user': str(caller)})
            event.listen(engine, 'before_cursor_execute', capture)
            try:
                rows = await DeclarationImportRepository(app_rw_session, org).list_started_by(caller, statuses, 50)
            finally:
                event.remove(engine, 'before_cursor_execute', capture)
            # Other tests of the session may have left open imports of the same user: only this test's rows are compared.
            assert sorted(row.status for row in rows if row.id in own) == sorted(OPEN)

            # The plan of the statement the repository sent, for the values it sent.
            [(statement, parameters)] = statements
            connection = await app_rw_session.connection()
            plan = (await connection.exec_driver_sql('EXPLAIN (FORMAT JSON) ' + statement, parameters)).scalar_one()
            plan = (json.loads(plan) if isinstance(plan, str) else plan)[0]['Plan']
            scans = [(node['Node Type'], node.get('Relation Name'), node.get('Index Name'), node.get('Index Cond')) for node in nodes(plan)
                     if node['Node Type'].endswith('Scan')]
            on_index = [scan for scan in scans if scan[2] == INDEX]
            assert on_index and ('Seq Scan', 'declaration_import') not in [scan[:2] for scan in scans], scans
            # The statuses are checked inside the index, so the caller's thousands of ended imports are not read.
            assert all('status' in scan[3] for scan in on_index), scans
        finally:
            await seed.rollback()
            await seed.execute(text('DELETE FROM declaration_import WHERE current_stage = :marker'), {'marker': MARKER})
            await seed.commit()


def test_the_index_is_a_plain_index_on_organization_initiator_and_status():
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as db:
            definition = db.scalar(text("SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = :name"), {'name': INDEX})
    finally:
        engine.dispose()
    # No predicate: which statuses are open is decided in one place, `MELDUNG_STATES`, not restated in SQL.
    assert definition == f'CREATE INDEX {INDEX} ON public.declaration_import USING btree (organization_id, initiated_by, status)'


def test_0204_adds_and_drops_only_the_index():
    migration = importlib.import_module('migrations.versions.0204_declaration_import_open_index')
    indexes = text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1")
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                head = {row[0]: row[1] for row in db.execute(indexes)}
                assert INDEX in head
                migration.downgrade()
                assert {row[0]: row[1] for row in db.execute(indexes)} == {key: value for key, value in head.items() if key != INDEX}
                migration.upgrade()
                assert {row[0]: row[1] for row in db.execute(indexes)} == head
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()
