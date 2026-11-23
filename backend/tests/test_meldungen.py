"""Meldungen: the Teilungserklärung imports a user started and has not finished are that user's, and nobody else's (#871)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.declarations.progress import MELDUNG_STATES
from app.repositories.declaration_import_repo import DeclarationImportRepository
from app.services.meldungen_service import MELDUNGEN_LIMIT, MeldungenService
from tests.test_declaration_job_principal import new_draft
from tests.test_financial_scope_boundary import scope_seed as scope_seed, worker_tenant as worker_tenant
from tests.test_property_source_enforcement import synthetic_enforcement
from tests.test_scoped_account_statements import client
from tests.test_declaration_import_api import (  # noqa: F401  (the two autouse fixtures switch the import on)
    _import_with_one_document,
    _set_organization,
    enable_declaration_imports,
    isolated_document_store,
)

NOW = datetime(2026, 11, 23, 12, 0, 0, tzinfo=timezone.utc)


def _start(client, import_id):
    return client.post(f"/api/declaration-imports/{import_id}/start", headers={"Idempotency-Key": str(uuid.uuid4())})


async def _meldungen(client, *, only: set[str] | None = None) -> list[dict]:
    """What the user's frame is told; with `only`, just the Meldungen of those imports (the test database keeps the rows of other tests)."""
    answer = await client.get("/api/meldungen")
    assert answer.status_code == 200, answer.text
    items = answer.json()["items"]
    return items if only is None else [item for item in items if item["id"] in only]


async def _insert_imports(session, organization, user, statuses, *, property_id=None, mode="NEW_PROPERTY"):
    """One import of this user per status as (status, id), written the way the status tests of the import write them."""
    ids = []
    for status in statuses:
        import_id = uuid.uuid4()
        await _set_organization(session, organization)
        await session.execute(
            text("INSERT INTO declaration_import (id, organization_id, mode, target_property_id, initiated_by, max_cost_usd) "
                 "VALUES (:id, :org, :mode, :target, :user, 10)"),
            {"id": import_id, "org": organization, "mode": mode, "target": property_id if mode == "EXISTING_PROPERTY" else None,
             "user": user})
        if status in ("APPLYING", "APPLIED"):
            applied = status == "APPLIED"
            await session.execute(
                text("UPDATE declaration_import SET status = :status, approved_by = :user, approved_at = now()"
                     + (", applied_property_id = :property, approval_idempotency_key = :key, "
                        "approval_result_json = CAST('{}' AS jsonb)" if applied else "") + " WHERE id = :id"),
                {"id": import_id, "status": status, "user": user, **({"property": property_id, "key": str(import_id)} if applied else {})})
        elif status != "DRAFT":
            await session.execute(text("UPDATE declaration_import SET status = :status WHERE id = :id"), {"id": import_id, "status": status})
        ids.append((status, import_id))
    return ids


async def _insert_property(session, organization, name):
    property_id = uuid.uuid4()
    await _set_organization(session, organization)
    await session.execute(
        text("INSERT INTO property (id, organization_id, name, management_type) VALUES (:id, :org, :name, 'WEG')"),
        {"id": property_id, "org": organization, "name": name})
    return property_id


async def _remove(migrator_session, ids, property_id=None):
    """Rows written for a test leave with it: the test database keeps what its tests wrote, and a Meldung that stays is
    another test's count. The session commits once."""
    await migrator_session.execute(text("DELETE FROM declaration_import WHERE id = ANY(:ids)"), {"ids": list(ids)})
    if property_id is not None:
        await migrator_session.execute(text("DELETE FROM property WHERE id = :id"), {"id": property_id})
    await migrator_session.commit()


@pytest.mark.asyncio
async def test_a_started_import_is_a_meldung_of_its_starter_until_it_ends(client_a_admin) -> None:
    import_id, _document = await _import_with_one_document(client_a_admin, "teilung.pdf")
    path = f"/api/declaration-imports/{import_id}"
    title = (await client_a_admin.get(f"{path}/documents")).json()[0]["title"]
    # A draft that was never started is no Meldung.
    assert await _meldungen(client_a_admin, only={import_id}) == []

    started = await _start(client_a_admin, import_id)
    assert started.status_code == 200
    try:
        [meldung] = await _meldungen(client_a_admin, only={import_id})
        assert {key: meldung[key] for key in meldung if key != "at"} == {
            "kind": "DECLARATION_IMPORT", "id": import_id, "title": "Teilungserklärung einlesen",
            "subject": f"Neues Objekt: {title}", "state": "WAITING", "state_label": "Wartet auf Verarbeitung",
            "target": f"/teilungserklaerung/import/{import_id}", "stage": None}
        # The time is the one the status page names as the start.
        assert datetime.fromisoformat(meldung["at"]) == datetime.fromisoformat(started.json()["started_at"])
    finally:
        assert (await client_a_admin.post(f"{path}/cancel")).status_code == 200
    # A cancelled import leaves the list.
    assert await _meldungen(client_a_admin, only={import_id}) == []


@pytest.mark.asyncio
async def test_the_newest_start_comes_first(client_a_admin) -> None:
    first, _ = await _import_with_one_document(client_a_admin, "erste.pdf")
    second, _ = await _import_with_one_document(client_a_admin, "zweite.pdf")
    try:
        for import_id in (first, second):
            assert (await _start(client_a_admin, import_id)).status_code == 200
        listed = [item["id"] for item in await _meldungen(client_a_admin, only={first, second})]
        assert listed == [second, first]
    finally:
        for import_id in (first, second):
            await client_a_admin.post(f"/api/declaration-imports/{import_id}/cancel")


@pytest.mark.asyncio
async def test_a_meldung_belongs_to_its_starter_alone(client_a_admin, client_a_clerk, client_b_admin) -> None:
    by_clerk, _ = await _import_with_one_document(client_a_clerk, "klerk.pdf")
    by_admin, _ = await _import_with_one_document(client_a_admin, "verwaltung.pdf")
    both = {by_clerk, by_admin}
    try:
        for client, import_id in ((client_a_clerk, by_clerk), (client_a_admin, by_admin)):
            assert (await _start(client, import_id)).status_code == 200
        # Each of the two users of the organization sees only the import they started, the administrator included,
        # although each of them can open the other's import page.
        assert [item["id"] for item in await _meldungen(client_a_clerk, only=both)] == [by_clerk]
        assert [item["id"] for item in await _meldungen(client_a_admin, only=both)] == [by_admin]
        assert (await client_a_admin.get(f"/api/declaration-imports/{by_clerk}")).status_code == 200
        # Another organization is told nothing about either.
        assert await _meldungen(client_b_admin, only=both) == []
        body = (await client_b_admin.get("/api/meldungen")).text
        assert by_clerk not in body and by_admin not in body
    finally:
        await client_a_clerk.post(f"/api/declaration-imports/{by_clerk}/cancel")
        await client_a_admin.post(f"/api/declaration-imports/{by_admin}/cancel")


@pytest.mark.asyncio
async def test_the_query_names_the_organization_itself_not_only_row_level_security(migrator_session, seed_data) -> None:
    """Row-level security alone also hides another organization's imports, so the route tests cannot tell whether the
    query is bounded by the organization too. The migrator's session is one the policies do not apply to (as in the pass
    ledger test): only the repository's own filter is left to keep an import of organization B out of A's answer. The
    session says it acts for the organization that asks, as a request does, because the access function the query calls
    admits an import only inside the session's own organization."""
    org_a, org_b, user_b = seed_data["org_a_id"], seed_data["org_b_id"], seed_data["user_b_admin_id"]
    statuses = [status for status, state in MELDUNG_STATES.items() if state is not None]
    [(_status, import_id)] = await _insert_imports(migrator_session, org_b, user_b, ["FAILED"])
    try:
        await _set_organization(migrator_session, org_b)
        assert [row.id for row in await DeclarationImportRepository(migrator_session, org_b).list_started_by(user_b, statuses, 50)] == [import_id]
        # The same user id and status, asked as organization A: nothing, though no policy hides the row here.
        await _set_organization(migrator_session, org_a)
        assert await DeclarationImportRepository(migrator_session, org_a).list_started_by(user_b, statuses, 50) == []
    finally:
        await migrator_session.rollback()


@pytest.mark.asyncio
async def test_a_member_who_may_not_open_the_import_pages_has_no_meldung(
    client_a_readonly, client_unauth, app_rw_session, migrator_session, seed_data
) -> None:
    organization, user = seed_data["org_a_id"], seed_data["user_a_readonly_id"]
    [(_status, import_id)] = rows = await _insert_imports(app_rw_session, organization, user, ["FAILED"])
    await app_rw_session.commit()
    try:
        # The import pages refuse this role (403 on the import routes), so no Meldung leads to one: an empty list, not a refusal.
        assert (await client_a_readonly.get(f"/api/declaration-imports/{import_id}")).status_code == 403
        assert (await client_a_readonly.get("/api/meldungen")).json() == {"items": []}
    finally:
        await _remove(migrator_session, [i for _status, i in rows])
    assert (await client_unauth.get("/api/meldungen")).status_code == 401


@pytest.mark.asyncio
async def test_every_status_is_a_meldung_or_not_as_decided(client_a_clerk, app_rw_session, migrator_session, seed_data) -> None:
    organization, user = seed_data["org_a_id"], seed_data["user_a_clerk_id"]
    property_id = await _insert_property(app_rw_session, organization, "Lindenallee 12")
    ids = dict(await _insert_imports(app_rw_session, organization, user, list(MELDUNG_STATES), property_id=property_id,
                                     mode="EXISTING_PROPERTY"))
    await app_rw_session.commit()
    try:
        listed = {item["id"]: item for item in await _meldungen(client_a_clerk, only={str(i) for i in ids.values()})}
    finally:
        await _remove(migrator_session, ids.values(), property_id)
    expected = {
        "QUEUED": ("WAITING", "Wartet auf Verarbeitung"),
        "RETRY_WAIT": ("WAITING", "Neuer Versuch ist eingeplant"),
        "PREFLIGHT": ("RUNNING", "Läuft: Dateien prüfen"),
        "EXTRACTING": ("RUNNING", "Läuft: Angaben auslesen"),
        "RECONCILING": ("RUNNING", "Läuft: Angaben abgleichen"),
        "APPLYING": ("RUNNING", "Wird übernommen"),
        "NEEDS_REVIEW": ("REVIEW", "Wartet auf Ihre Prüfung"),
        "FAILED": ("FAILED", "Verarbeitung fehlgeschlagen"),
    }
    assert {status for status, import_id in ids.items() if str(import_id) in listed} == set(expected)
    for status, (state, label) in expected.items():
        item = listed[str(ids[status])]
        assert (item["state"], item["state_label"]) == (state, label), status
        # An import waiting for the review leads to the review, every other to its status page.
        suffix = "/review" if status == "NEEDS_REVIEW" else ""
        assert item["target"] == f"/teilungserklaerung/import/{ids[status]}{suffix}", status
        # An existing property is named; no start was recorded for these rows.
        assert (item["subject"], item["at"]) == ("Lindenallee 12", None), status


@pytest.mark.asyncio
async def test_a_new_property_is_named_with_its_documents_in_their_order(client_a_clerk) -> None:
    import_id, _ = await _import_with_one_document(client_a_clerk, "teilung.pdf")
    try:
        uploaded = await client_a_clerk.post(
            f"/api/declaration-imports/{import_id}/documents",
            files={"file": ("nachtrag.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
            data={"document_role": "AMENDMENT", "sequence_no": "2"})
        assert uploaded.status_code == 201
        titles = [document["title"] for document in (await client_a_clerk.get(f"/api/declaration-imports/{import_id}/documents")).json()]
        assert len(titles) == 2
        assert (await _start(client_a_clerk, import_id)).status_code == 200
        [meldung] = await _meldungen(client_a_clerk, only={import_id})
        assert meldung["subject"] == f"Neues Objekt: {titles[0]}, {titles[1]}"
    finally:
        await client_a_clerk.post(f"/api/declaration-imports/{import_id}/cancel")


@pytest.mark.asyncio
async def test_under_enforced_source_security_the_starter_finds_the_import_and_the_colleague_does_not(scope_seed, migrator_session) -> None:
    """The read check of the import page (`property_import_runtime_access`) is part of the query, and it is the starter's."""
    await migrator_session.commit()
    ids = {"org": scope_seed["org_a_id"], "actor": scope_seed["user_a_admin_id"], "clerk": scope_seed["user_a_clerk_id"]}
    job = await new_draft(ids)
    async with synthetic_enforcement(scope_seed):
        async with client(ids, "actor") as starter, client(ids, "clerk") as colleague:
            started = await starter.post(f"/api/declaration-imports/{job}/start", headers={"Idempotency-Key": str(uuid.uuid4())})
            assert started.status_code == 200, started.text
            try:
                assert [item["target"] for item in await _meldungen(starter, only={job})] == [f"/teilungserklaerung/import/{job}"]
                assert await _meldungen(colleague, only={job}) == []
            finally:
                await starter.post(f"/api/declaration-imports/{job}/cancel")


@pytest.mark.asyncio
async def test_an_answer_holds_at_most_the_limit_of_meldungen(client_a_clerk, app_rw_session, migrator_session, seed_data) -> None:
    organization, user = seed_data["org_a_id"], seed_data["user_a_clerk_id"]
    rows = await _insert_imports(app_rw_session, organization, user, ["FAILED"] * (MELDUNGEN_LIMIT + 2))
    await app_rw_session.commit()
    try:
        assert len((await client_a_clerk.get("/api/meldungen")).json()["items"]) == MELDUNGEN_LIMIT
    finally:
        await _remove(migrator_session, [i for _status, i in rows])


# ---- the running step is the one the status page names, read once for all running imports ------------------------

class _NoBatches:
    """The Posteingang's source: this test is about imports."""

    async def progress(self, limit):
        return []


class _Repository:
    """The reads the service makes; one import that runs, read like its status page reads it."""

    def __init__(self, *, pages_read: bool) -> None:
        self.import_id = uuid.uuid4()
        self.run = SimpleNamespace(id=uuid.uuid4(), import_id=self.import_id, started_at=NOW - timedelta(minutes=5), completed_at=None)
        self.read = SimpleNamespace(declaration_extraction_run_id=self.run.id, created_at=NOW - timedelta(minutes=1),
                                    safe_metadata={"pass_id": "read_page", "failure_class": None})
        self.pages_read, self.calls = pages_read, []

    async def list_started_by(self, user_id, statuses, limit):
        self.calls.append(("list_started_by", limit))
        return [SimpleNamespace(id=self.import_id, mode="EXISTING_PROPERTY", status="PREFLIGHT", property_name="Lindenallee 12",
                                started_at=NOW)]

    async def document_titles(self, import_ids):
        return {}

    async def runs_of(self, import_ids):
        assert import_ids == [self.import_id]
        return [self.run]

    async def passes_of(self, import_ids):
        return [self.read] if self.pages_read else []

    async def page_reading_counts(self, import_ids):
        return {self.import_id: 1} if self.pages_read else {}


@pytest.mark.asyncio
@pytest.mark.parametrize("pages_read, label", [(False, "Läuft: Dateien prüfen"), (True, "Läuft: Seiten lesen")])
async def test_a_running_import_says_the_step_its_status_page_says(pages_read, label) -> None:
    service = MeldungenService(None, uuid.uuid4(), uuid.uuid4(), "CLERK")  # type: ignore[arg-type]
    service._imports, service._batches = _Repository(pages_read=pages_read), _NoBatches()  # type: ignore[assignment]
    [meldung] = await service.list()
    assert (meldung.state, meldung.state_label) == ("RUNNING", label)
    assert service._imports.calls == [("list_started_by", MELDUNGEN_LIMIT)]
