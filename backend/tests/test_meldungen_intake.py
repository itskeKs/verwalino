"""Meldungen: a Posteingang batch is a Meldung of the user who uploaded it while it waits, runs or has failed (#872).

The state of a file is decided by `intake_batch_status` (what the batch page shows); the Meldungen read states the batches
of a user in one statement (`intake_batch_progress`). The walk below lets the real workers take a batch through every
stage and compares the Meldung with the batch page's own answer at each step, so that the two cannot drift apart.
"""
import hashlib
import importlib
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import get_worker_session_factory
from app.core.intake_extraction import extract_logical, outcome
from app.core.intake_inspection import result
from app.repositories.intake_batch_repo import BatchProgress, FileGroup, IntakeBatchRepository
from app.services.meldungen_service import (
    AWAITS_SETUP,
    INTAKE_FILE_STATES,
    INTAKE_STAGES,
    MELDUNGEN_LIMIT,
    MeldungenService,
    intake_batch_state,
    intake_file_state,
)
from app.workers.intake_extraction import process_once as extract_once
from app.workers.outbox import process_once as deliver_once
from app.workers.workflow import process_once as inspect_once
from tests.intake_model_support import Harness, enable_models
from tests.conftest import MIGRATOR_SYNC_URL, MIGRATOR_URL
from tests.test_financial_scope_boundary import scope_seed as scope_seed, worker_tenant as worker_tenant
from tests.test_intake_batches import create, manifest
from tests.test_invoice_extraction_worker import pdf_bytes
from tests.test_invoice_source_custody import fixture, store as store
from tests.test_scoped_account_statements import client

KIND = "INTAKE_BATCH"
ROOT = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def migrator():
    """A second migrator session: the fixture commits, and ``migrator_session`` can do that only once."""
    engine = create_async_engine(MIGRATOR_URL, pool_size=1)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def meldungen(api, batch=None):
    response = await api.get("/api/meldungen")
    assert response.status_code == 200, response.text
    items = [item for item in response.json()["items"] if item["kind"] == KIND]
    return items if batch is None else [item for item in items if item["id"] == batch]


async def upload(api, batch, data, key="document-1"):
    response = await api.post(f"/api/intake-batches/{batch['id']}/items", files={"file": ("invoice.pdf", data, "application/pdf")},
                              data={"client_item_key": key})
    assert response.status_code == 202, response.text


async def page_state(api, batch):
    """What the batch page shows for each file, folded the way the Meldung folds it: the batch page's own answer."""
    page = (await api.get(f"/api/intake-batches/{batch['id']}")).json()
    return intake_batch_state([FileGroup(item["status"], item["stage"], item["error_code"], 1) for item in page["items"]])


async def expect_meldung(api, batch, state, stage):
    """The Meldung says the state and stage, and the batch page's answer for the same moment says the same."""
    assert await page_state(api, batch) == ((state, stage) if state else None)
    found = await meldungen(api, batch["id"])
    if state is None:
        assert found == []
        return None
    [meldung] = found
    assert (meldung["state"], meldung["stage"]) == (state, stage)
    return meldung


async def test_a_batch_is_a_meldung_of_its_uploader_from_the_receipt_until_its_documents_wait_for_review(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids["org"])
    data = pdf_bytes()
    async with client(ids) as api:
        batch = await create(api, data)
        # The manifest alone: no file arrived, nothing is processed.
        await expect_meldung(api, batch, None, None)
        await upload(api, batch, data)
        meldung = await expect_meldung(api, batch, "WAITING", "inspection")
        assert (meldung["kind"], meldung["title"], meldung["subject"], meldung["state_label"]) == (
            KIND, "Dokumentenstapel", "1 Datei", "Wartet auf Verarbeitung")
        assert meldung["target"] == f"/posteingang/{batch['id']}"
        assert datetime.fromisoformat(meldung["at"]) == datetime.fromisoformat(batch["created_at"])

        assert await inspect_once(get_worker_session_factory(), ids["org"], inspector=lambda d, mime: result(d, "clean", mime=mime, pages=1))
        meldung = await expect_meldung(api, batch, "RUNNING", "preparation")
        assert meldung["state_label"] == "Läuft"
        assert await deliver_once(get_worker_session_factory(), ids["org"])
        await expect_meldung(api, batch, "RUNNING", "extraction")

        def unavailable(d, mime, start, end, count):
            return outcome(d, mime, start, end, count, "unavailable", "ai_import_disabled")

        # The organization's setup is missing: nothing failed and nothing runs, the file waits and can be requested again.
        assert await extract_once(get_worker_session_factory(), ids["org"], processor=unavailable)
        meldung = await expect_meldung(api, batch, "WAITING", "extraction")
        assert meldung["state_label"] == "Wartet auf Verarbeitung"
        page = (await api.get(f"/api/intake-batches/{batch['id']}")).json()
        child = (await api.get("/api/logical-documents", params={"document_ref_id": page["items"][0]["document_ref_id"]})).json()["items"][0]
        status = (await api.get(f"/api/logical-documents/{child['id']}/processing")).json()
        retry = await api.post(f"/api/logical-documents/{child['id']}/retry-extraction", json={"expected_fence": status["fence"], "reason": "Models switched on"},
                               headers={"Idempotency-Key": str(uuid.uuid4())})
        assert retry.status_code == 202
        await expect_meldung(api, batch, "RUNNING", "extraction")
        # Processing finished: the proposals wait for review in the Posteingang, which is no Meldung of the batch.
        assert await extract_once(get_worker_session_factory(), ids["org"], processor=extract_logical, providers=Harness())
        await expect_meldung(api, batch, None, None)
        assert (await api.get(f"/api/intake-batches/{batch['id']}")).json()["counts"]["review"] == 1


async def test_a_file_that_stopped_with_a_failure_keeps_its_batch_in_the_list_until_it_is_asked_for_again(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    data = pdf_bytes()
    async with client(ids) as api:
        batch = await create(api, data)
        await upload(api, batch, data)
        assert await inspect_once(get_worker_session_factory(), ids["org"], inspector=lambda d, mime: result(d, "rejected", "invalid_document", mime=mime))
        meldung = await expect_meldung(api, batch, "FAILED", "inspection")
        assert meldung["state_label"] == "Verarbeitung fehlgeschlagen"
        # No error text, no file name and no code reaches the Meldung.
        assert "invoice.pdf" not in str(meldung) and "invalid_document" not in str(meldung)
        assert (await meldungen(api, batch["id"])) == [meldung]


async def test_a_batch_with_a_missing_file_is_a_meldung_while_another_file_of_it_is_processed(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    data = pdf_bytes()
    second = {"client_item_key": "document-2", "filename": "second.pdf", "mime_type": "application/pdf",
              "content_sha256": hashlib.sha256(data + b"2").hexdigest(), "content_byte_size": len(data) + 1}
    async with client(ids) as api:
        batch = await create(api, data, items=[manifest(data)["items"][0], second])
        await upload(api, batch, data)
        meldung = await expect_meldung(api, batch, "WAITING", "inspection")
        assert meldung["subject"] == "2 Dateien"


async def test_nobody_but_the_uploader_is_told_about_a_batch(migrator_session, scope_seed, store, seed_data):
    ids = await fixture(migrator_session, scope_seed)
    data = pdf_bytes()
    async with client(ids) as api:
        batch = await create(api, data)
        await upload(api, batch, data)
        assert len(await meldungen(api, batch["id"])) == 1
    # Another user of the same organization, an administrator included, sees nothing of it, however it asks.
    async with client(ids, "actor") as administrator:
        assert await meldungen(administrator, batch["id"]) == []
    # Another organization sees nothing of it.
    foreign = {"org": seed_data["org_b_id"], "user": seed_data["user_b_admin_id"]}
    async with client(foreign, "user") as other:
        assert await meldungen(other, batch["id"]) == []


async def test_a_batch_of_a_scope_its_uploader_may_no_longer_read_is_no_meldung(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    data = pdf_bytes()
    async with client(ids) as api:
        batch = await create(api, data, scope_hint_id=str(ids["scope"]))
        await upload(api, batch, data)
        assert len(await meldungen(api, batch["id"])) == 1
        # The Posteingang's own list applies the same rule: the batch is hidden from a user whose grant ended.
        async with migrator() as admin:
            await admin.execute(text("UPDATE scope_grant SET revoked_at = clock_timestamp(), revoked_by = :actor WHERE id = :grant"), {"grant": ids["grant"], "actor": ids["actor"]})
            await admin.commit()
        assert await meldungen(api, batch["id"]) == []
        assert batch["id"] not in [header["id"] for header in (await api.get("/api/intake-batches")).json()["items"]]


async def test_the_read_names_the_organization_of_the_session_and_cannot_be_pointed_at_another(migrator_session, scope_seed, store, app_rw_session, seed_data):
    ids = await fixture(migrator_session, scope_seed)
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(ids["org"])})
    await app_rw_session.execute(text("SELECT set_config('app.current_user_id', :user, true)"), {"user": str(ids["clerk"])})
    assert await IntakeBatchRepository(app_rw_session, ids["org"]).progress(MELDUNGEN_LIMIT) is not None
    with pytest.raises(DBAPIError, match="Intake organization required"):
        await IntakeBatchRepository(app_rw_session, seed_data["org_b_id"]).progress(MELDUNGEN_LIMIT)


async def test_the_read_costs_what_the_users_own_batches_cost_not_what_the_organization_holds(migrator_session, scope_seed, store, app_rw_session):
    """The frame asks on every navigation: no sequential scan of the batch or item table, however many an organization holds."""
    ids = await fixture(migrator_session, scope_seed)
    org, caller, colleague = ids["org"], ids["clerk"], ids["actor"]
    batches = text("""INSERT INTO intake_batch (id, organization_id, initiated_by, request_key, request_digest, manifest)
        SELECT gen_random_uuid(), :org, :user, gen_random_uuid(), 'plan-test', '[{"k": 1}]'::jsonb FROM generate_series(1, :count)""")
    items = text("""INSERT INTO intake_item (organization_id, batch_id, position, client_item_key, filename, mime_type, content_sha256, content_byte_size)
        SELECT organization_id, id, 1, 'k', 'f.pdf', 'application/pdf', repeat('0', 64), 1 FROM intake_batch WHERE initiated_by = :user""")
    # What an organization that has used the Posteingang for a long time holds: thousands of batches of a colleague, and the
    # caller's own few.
    async with migrator() as seed:
        try:
            for user, count in ((colleague, 4000), (caller, 3)):
                await seed.execute(batches, {"org": org, "user": user, "count": count})
                await seed.execute(items, {"user": user})
            await seed.commit()
            for table in ("intake_batch", "intake_item"):
                await seed.execute(text(f"ANALYZE {table}"))
            await seed.commit()
            await app_rw_session.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org)})
            await app_rw_session.execute(text("SELECT set_config('app.current_user_id', :user, true)"), {"user": str(caller)})
            scans = text("SELECT relname, seq_scan, idx_scan FROM pg_stat_xact_user_tables WHERE relname IN ('intake_batch', 'intake_item')")
            before = {name: (seq, index) for name, seq, index in (await app_rw_session.execute(scans)).all()}
            rows = await IntakeBatchRepository(app_rw_session, org).progress(MELDUNGEN_LIMIT)
            after = {name: (seq, index) for name, seq, index in (await app_rw_session.execute(scans)).all()}
            assert len(rows) == 3
            # The caller's three batches come from the owner index and their files from the batch's key: nothing is scanned
            # (the counters of this transaction do count the statements the function ran).
            for table in ("intake_batch", "intake_item"):
                assert after[table][0] == before[table][0] and after[table][1] > before[table][1], (table, before, after)
        finally:
            await app_rw_session.rollback()
            await seed.rollback()
            # The intake rows are immutable (a trigger refuses DELETE); the fixtures of this suite clear them the same way.
            await seed.execute(text("TRUNCATE public.intake_batch, public.intake_upload_admission CASCADE"))
            await seed.commit()


async def test_every_stage_and_status_the_database_names_has_a_decision_and_a_label(migrator_session):
    """A stage or status added to the batch page's function without a decision here (and a word on the batch page) fails."""
    emitted_stages, emitted_statuses = set(), set()
    for function in ("intake_batch_status(uuid,uuid,integer,integer)", "intake_batch_status_pre_extraction(uuid,uuid,integer,integer)"):
        source = await migrator_session.scalar(text("SELECT pg_get_functiondef(CAST(:function AS regprocedure))"), {"function": f"public.{function}"})
        emitted_stages.update(re.findall(r"stage:='(\w+)'", source))
        emitted_statuses.update(re.findall(r"state:='(\w+)'", source) + re.findall(r"THEN '(\w+)'", source))
    assert emitted_stages == set(INTAKE_STAGES) and emitted_statuses, (emitted_stages, emitted_statuses)
    decided_statuses = {status for status, _ in INTAKE_FILE_STATES}
    assert emitted_statuses == decided_statuses, (emitted_statuses, decided_statuses)
    assert {stage for _, stage in INTAKE_FILE_STATES} == set(INTAKE_STAGES)
    # The words come from one table the batch page also reads; a stage without a word would show the user nothing.
    source = (ROOT / "frontend" / "src" / "lib" / "intakeStages.ts").read_text()
    words = source[source.index("INTAKE_STAGE_LABELS"):]
    assert set(re.findall(r"^\s+(\w+): '", words.split("}")[0], re.MULTILINE)) == set(INTAKE_STAGES)
    statuses = source[source.index("INTAKE_STATUS_LABELS"):source.index("INTAKE_STAGE_LABELS")]
    assert set(re.findall(r"^\s+(\w+): '", statuses, re.MULTILINE)) == decided_statuses | {"confirmed"}


def test_the_organization_setups_the_batch_page_calls_a_file_that_did_not_fail_are_the_ones_decided_here():
    source = (ROOT / "frontend" / "src" / "lib" / "invoiceProcessing.ts").read_text()
    block = source[source.index("export const AWAITS_SETUP"):].split(";")[0]
    assert set(re.findall(r'"(\w+)"', block)) == set(AWAITS_SETUP)


@pytest.mark.no_db
@pytest.mark.parametrize("status, stage, error, state", [
    ("waiting", "inspection", None, None),
    ("received", "inspection", None, "WAITING"),
    ("processing", "inspection", None, "RUNNING"),
    ("failed", "inspection", "scanner_detected", "FAILED"),
    ("processing", "preparation", None, "RUNNING"),
    ("failed", "preparation", "blocked", "FAILED"),
    ("processing", "extraction", None, "RUNNING"),
    ("failed", "extraction", "model_extraction_failed", "FAILED"),
    ("failed", "extraction", "ai_import_disabled", "WAITING"),
    ("review", "split", "awaiting_split", None),
    ("review", "review", None, None),
])
def test_each_state_of_a_file_is_decided_once(status, stage, error, state):
    assert intake_file_state(status, stage, error) == state


@pytest.mark.no_db
def test_a_batch_is_in_the_state_of_its_most_urgent_file_and_names_the_earliest_stage_of_that_state():
    def group(status, stage, error=None):
        return FileGroup(status, stage, error, 1)

    assert intake_batch_state([]) is None
    assert intake_batch_state([group("review", "review"), group("waiting", "inspection")]) is None
    assert intake_batch_state([group("received", "inspection"), group("processing", "extraction")]) == ("RUNNING", "extraction")
    assert intake_batch_state([group("processing", "extraction"), group("processing", "preparation")]) == ("RUNNING", "preparation")
    assert intake_batch_state([group("failed", "extraction", "ai_key_unreadable"), group("received", "inspection")]) == ("WAITING", "inspection")
    assert intake_batch_state([group("failed", "extraction", "ai_key_unreadable")]) == ("WAITING", "extraction")
    assert intake_batch_state([group("processing", "extraction"), group("failed", "inspection"), group("failed", "extraction")]) == ("FAILED", "inspection")


class _Batches:
    def __init__(self, batches):
        self.batches, self.calls = batches, []

    async def progress(self, limit):
        self.calls.append(limit)
        return self.batches


class _NoImports:
    async def list_started_by(self, user_id, statuses, limit):
        return []


@pytest.mark.no_db
async def test_the_answer_holds_the_newest_meldungen_of_all_kinds_at_most_the_limit():
    group = (FileGroup("received", "inspection", None, 1),)
    batches = [BatchProgress(uuid.uuid4(), datetime(2026, 11, 23, 12, minute, tzinfo=timezone.utc), 1, group)
               for minute in range(MELDUNGEN_LIMIT + 5)]
    service = MeldungenService(None, uuid.uuid4(), uuid.uuid4(), "CLERK")  # type: ignore[arg-type]
    service._imports, service._batches = _NoImports(), _Batches(list(reversed(batches)))  # type: ignore[assignment]
    found = await service.list()
    assert len(found) == MELDUNGEN_LIMIT
    assert [meldung.id for meldung in found] == [batch.id for batch in reversed(batches)][:MELDUNGEN_LIMIT]
    assert service._batches.calls == [MELDUNGEN_LIMIT]


@pytest.mark.no_db
async def test_a_user_who_may_not_open_the_import_pages_still_finds_the_batches_the_posteingang_lists_for_them():
    batch = BatchProgress(uuid.uuid4(), datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc), 2, (FileGroup("failed", "inspection", None, 2),))
    service = MeldungenService(None, uuid.uuid4(), uuid.uuid4(), "READ_ONLY")  # type: ignore[arg-type]
    service._imports, service._batches = SimpleNamespace(), _Batches([batch])  # type: ignore[assignment]
    [meldung] = await service.list()
    assert (meldung.kind, meldung.state, meldung.stage, meldung.subject) == (KIND, "FAILED", "inspection", "2 Dateien")


def test_0205_adds_and_drops_only_the_function_and_grants_it_as_the_list_function_of_the_posteingang_is():
    migration = importlib.import_module("migrations.versions.0205_intake_batch_progress")
    functions = text("SELECT p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' ORDER BY 1")
    privileges = text("""SELECT has_function_privilege(role, CAST(:function AS regprocedure), 'EXECUTE')
        FROM unnest(ARRAY['app_rw', 'app_worker', 'app_review']) AS role ORDER BY role""")
    public = text("SELECT count(*) FROM pg_proc p, aclexplode(p.proacl) a WHERE p.oid = CAST(:function AS regprocedure) AND a.grantee = 0")
    ours, sibling = {"function": "public.intake_batch_progress(uuid,integer)"}, {"function": "public.intake_batch_headers(uuid,timestamptz,uuid,integer)"}
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                head = set(db.scalars(functions))
                assert "intake_batch_progress(uuid,integer)" in head
                # The roles that may call it are the ones that may call the Posteingang's own list function: the application's, never PUBLIC.
                callers = [row[0] for row in db.execute(privileges, sibling)]
                assert callers[1] and db.scalar(public, sibling) == 0 and db.scalar(public, ours) == 0
                assert [row[0] for row in db.execute(privileges, ours)] == callers
                migration.downgrade()
                assert set(db.scalars(functions)) == head - {"intake_batch_progress(uuid,integer)"}
                migration.upgrade()
                assert set(db.scalars(functions)) == head
                assert [row[0] for row in db.execute(privileges, ours)] == callers
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()
