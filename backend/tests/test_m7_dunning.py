"""M7 tests -- dunning notice lifecycle, immutability, RLS, escalation,
overdue detection, fee calculation, PDF smoke, and the golden test.

Uses the same fixtures as M0-M6 (seed_data, app_rw_session, migrator_session,
client_a_admin/clerk/readonly, client_b_admin). The M6 seed (m6_seed) is
reused since dunning builds on the same tenant/owner/lease/payment setup.

The pure engine tests (overdue detection, escalation, fee) run without DB.
The integration tests prove the DB invariants (immutability, RLS, CHECK)
and the full create -> issue -> PDF path.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone as _tz
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text


SYNC_URL = os.environ.get(
    "HV_DATABASE_URL_SYNC",
    "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
)


# ===========================================================================
# Seed: reuse m6_seed but add payments that create arrears for dunning tests.
# ===========================================================================
@pytest.fixture(scope="session")
def m7_seed(seed_data):
    """Set up properties, accounts, persons, leases, and partial payments
    to create overdue open items for the M7 dunning tests."""
    import psycopg2

    org_a = str(seed_data["org_a_id"])
    org_b = str(seed_data["org_b_id"])
    ids = {
        "prop_m7": uuid.uuid4(),
        "acct_m7": uuid.uuid4(),
        "unit_m7": uuid.uuid4(),
        "person_m7": uuid.uuid4(),
        "tenant_m7": uuid.uuid4(),
        "lease_m7": uuid.uuid4(),
        # Owner for owner-dunning test
        "person_o_m7": uuid.uuid4(),
        "owner_m7": uuid.uuid4(),
        # org_b for cross-tenant
        "prop_b_m7": uuid.uuid4(),
        "acct_b_m7": uuid.uuid4(),
    }

    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()

        # Property + account
        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
            (str(ids["prop_m7"]), org_a, "Mahnhaus", "MIET"),
        )
        cur.execute(
            "INSERT INTO bank_account (id, organization_id, property_id, account_type, label, iban) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (str(ids["acct_m7"]), org_a, str(ids["prop_m7"]), "LAUFEND", "Konto M7",
             "DE02200505501015871393"),
        )

        # Unit
        cur.execute(
            "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, heated_area, mea) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["unit_m7"]), org_a, str(ids["prop_m7"]), "M7-1", "WOHNUNG", 80, 80, 500),
        )

        # Tenant person + profile
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name, street, zip_code, city) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["person_m7"]), org_a, "Max", "Mahner", "Schuldenstr. 1", "12345", "Mahnstadt"),
        )
        cur.execute(
            "INSERT INTO tenant_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["tenant_m7"]), org_a, str(ids["person_m7"])),
        )

        # Lease: base_rent 500, NK advance 100. Full year 2025, open-ended.
        cur.execute(
            "INSERT INTO lease_agreement (id, organization_id, unit_id, tenant_profile_id, "
            "valid_from, valid_to, base_rent, advance_payment) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["lease_m7"]), org_a, str(ids["unit_m7"]), str(ids["tenant_m7"]),
             date(2025, 1, 1), None, Decimal("500.00"), Decimal("100.00")),
        )

        # Payments: pay only Jan, Feb fully (600 each = 500 rent + 100 NK).
        # March to June NOT paid -> 4 months with 600 arrears each.
        for month_i in [1, 2]:
            for ptype, amt in [("KALTMIETE", "500.00"), ("NK_VORAUSZAHLUNG", "100.00")]:
                pid = uuid.uuid4()
                cur.execute(
                    "INSERT INTO payment (id, organization_id, bank_account_id, property_id, "
                    "direction, amount, value_date, payment_type, tenant_profile_id, "
                    "lease_agreement_id, period_year, period_month, status, booked_at, booked_by) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (str(pid), org_a, str(ids["acct_m7"]), str(ids["prop_m7"]),
                     "IN", amt, date(2025, month_i, 5), ptype, str(ids["tenant_m7"]),
                     str(ids["lease_m7"]), 2025, month_i, "booked",
                     datetime(2025, month_i, 5, 10, 0, tzinfo=_tz.utc),
                     str(seed_data["user_a_admin_id"])),
                )

        # Owner person + profile (for owner dunning test)
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
            (str(ids["person_o_m7"]), org_a, "Olaf", "Ownerzeiger"),
        )
        cur.execute(
            "INSERT INTO owner_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["owner_m7"]), org_a, str(ids["person_o_m7"])),
        )

        # org_b for isolation test
        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
            (str(ids["prop_b_m7"]), org_b, "Betahaus M7", "WEG"),
        )
        cur.execute(
            "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
            "VALUES (%s,%s,%s,%s,%s)",
            (str(ids["acct_b_m7"]), org_b, str(ids["prop_b_m7"]), "LAUFEND", "Konto B M7"),
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {**seed_data, **ids}


async def _set_org(session, org_id):
    await session.execute(
        text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(org_id)}
    )


_ISSUE_DATE = date(2025, 7, 1)
_DUE_DATE = date(2025, 7, 15)
_ISSUED_AT = datetime(2025, 7, 1, 10, 0, 0, tzinfo=_tz.utc)


def _insert_dunning_sql() -> str:
    return """
        INSERT INTO dunning_notice (
            id, organization_id, tenant_profile_id, owner_profile_id,
            level, issue_date, due_date,
            principal_amount, fee_amount, total_amount,
            included_items, status, issued_at, issued_by
        ) VALUES (
            :id, :org, :tenant, :owner,
            :level, :issue_date, :due_date,
            :principal, :fee, :total,
            CAST(:items AS jsonb), :status, :issued_at, :issued_by
        )
    """


# ===========================================================================
# T53 batch dunning
# ===========================================================================
@pytest_asyncio.fixture
async def isolated_batch_seed(rent_world, seed_data):
    # An organization-wide preview must not enumerate other modules' retained
    # scoped portfolios. Reuse the exact original EUR 2,400 seed in its own org.
    seed = {**seed_data, "org_a_id": rent_world["org"],
            "user_a_admin_id": rent_world["admin"],
            "org_b_id": rent_world["foreign_org"]}
    rows = m7_seed.__wrapped__(seed)
    async with api_client(rent_world) as client:
        yield client, rows


def batch_request(**changes):
    return {
        "period_start": "2025-01-01", "period_end": "2025-06-30",
        "reference_date": "2025-07-01", "threshold": "2399.99",
        "grace_days": 0, **changes,
    }


async def batch_preview(client, body=None):
    response = await client.post("/api/dunning/batch/preview", json=body or batch_request())
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_batch_preview_exact_threshold_and_no_writes(isolated_batch_seed):
    client_a_admin, m7_seed = isolated_batch_seed
    before = (await client_a_admin.get("/api/dunning")).json()
    result = await batch_preview(client_a_admin)
    row = next(r for r in result["candidates"] if r["lease_agreement_id"] == str(m7_seed["lease_m7"]))
    assert Decimal(row["principal_amount"]) == Decimal("2400.00")
    assert row["tenant_profile_id"] == str(m7_seed["tenant_m7"])
    assert len(result["source_digest"]) == 64
    equal = await batch_preview(client_a_admin, batch_request(threshold="2400.00"))
    assert str(m7_seed["lease_m7"]) not in [r["lease_agreement_id"] for r in equal["candidates"]]
    assert (await client_a_admin.get("/api/dunning")).json() == before


@pytest.mark.asyncio
async def test_batch_selected_draft_and_same_request_replay(isolated_batch_seed):
    client_a_admin, m7_seed = isolated_batch_seed
    body = batch_request()
    preview = await batch_preview(client_a_admin, body)
    request = {**body, "expected_source_digest": preview["source_digest"],
               "request_id": str(uuid.uuid4()), "lease_agreement_ids": [str(m7_seed["lease_m7"])]}
    before = {r["id"] for r in (await client_a_admin.get("/api/dunning")).json()}
    response = await client_a_admin.post("/api/dunning/batch", json=request)
    assert response.status_code == 201, response.text
    rows = response.json()
    assert len(rows) == 1 and rows[0]["status"] == "draft"
    assert Decimal(rows[0]["principal_amount"]) == Decimal("2400.00")
    assert rows[0]["issued_at"] is None and rows[0]["interest_amount"] in ("0.00", "0")
    replay = await client_a_admin.post("/api/dunning/batch", json=request)
    assert replay.status_code == 201, replay.text
    assert replay.json() == rows
    after = {r["id"] for r in (await client_a_admin.get("/api/dunning")).json()}
    assert after - before == {rows[0]["id"]}
    changed = await client_a_admin.post("/api/dunning/batch", json={**request, "threshold": "0.00"})
    assert changed.status_code == 409


@pytest.mark.asyncio
async def test_batch_stale_and_unknown_selection_are_atomic(isolated_batch_seed):
    client_a_admin, m7_seed = isolated_batch_seed
    preview = await batch_preview(client_a_admin)
    before = (await client_a_admin.get("/api/dunning")).json()
    body = {**batch_request(), "expected_source_digest": "0" * 64,
            "request_id": str(uuid.uuid4()), "lease_agreement_ids": [str(m7_seed["lease_m7"])]}
    response = await client_a_admin.post("/api/dunning/batch", json=body)
    assert response.status_code == 409, response.text
    body["expected_source_digest"] = preview["source_digest"]
    body["lease_agreement_ids"].append(str(uuid.uuid4()))
    response = await client_a_admin.post("/api/dunning/batch", json=body)
    assert response.status_code in (404, 422), response.text
    assert (await client_a_admin.get("/api/dunning")).json() == before


@pytest.mark.asyncio
async def test_batch_policy_change_requires_refresh_and_same_policy_is_saved(isolated_batch_seed):
    client, seed = isolated_batch_seed
    body = batch_request()
    old = await batch_preview(client, body)
    path = "/api/dunning/settings"
    settings = _mutable_settings((await client.get(path)).json())
    answer = await client.put(path, json={**settings, "fee_level_1": "3.00", "default_interest_enabled": True})
    assert answer.status_code == 200, answer.text

    request = selected_batch(body, old, [seed["lease_m7"]])
    before = (await client.get("/api/dunning")).json()
    stale = await client.post("/api/dunning/batch", json=request)
    assert stale.status_code == 409, stale.text
    assert (await client.get("/api/dunning")).json() == before

    preview = await batch_preview(client, body)
    candidate = next(row for row in preview["candidates"] if row["lease_agreement_id"] == str(seed["lease_m7"]))
    assert candidate["fee_amount"] == "3.00"
    assert Decimal(candidate["interest_amount"]) > 0
    created = await client.post("/api/dunning/batch", json={**request, "expected_source_digest": preview["source_digest"]})
    assert created.status_code == 201, created.text
    assert {key: created.json()[0][key] for key in ("fee_amount", "interest_amount", "total_amount")} == {
        key: candidate[key] for key in ("fee_amount", "interest_amount", "total_amount")
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("threshold", ["-1.00", "NaN", "Infinity", "1.001"])
async def test_batch_invalid_threshold(client_a_admin, m7_seed, threshold):
    response = await client_a_admin.post("/api/dunning/batch/preview", json=batch_request(threshold=threshold))
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_batch_readonly_cannot_prepare(client_a_readonly, m7_seed):
    response = await client_a_readonly.post("/api/dunning/batch/preview", json=batch_request())
    assert response.status_code == 403, response.text
    response = await client_a_readonly.post("/api/dunning/batch", json={**batch_request(),
        "expected_source_digest": "0" * 64, "request_id": str(uuid.uuid4()),
        "lease_agreement_ids": [str(m7_seed["lease_m7"])]})
    assert response.status_code == 403, response.text


# Fresh organizations isolate concurrency and history from session-shared M7 data.
from tests.test_rent_changes import rent_db as rent_db, rent_world as rent_world, api_client, post_change  # noqa: E402,F401


def fresh_batch_request(**changes):
    return batch_request(period_start="2026-01-01", period_end="2026-01-31",
                         reference_date="2026-02-15", threshold="100.00", **changes)


def selected_batch(body, preview, leases, request_id=None):
    return {**body, "expected_source_digest": preview["source_digest"],
            "lease_agreement_ids": [str(value) for value in leases],
            "request_id": str(request_id or uuid.uuid4())}


@pytest.mark.asyncio
async def test_batch_real_history_invalidates_preview_and_single_draft_agrees(rent_world):
    w = rent_world
    body = fresh_batch_request()
    async with api_client(w) as api:
        old = await batch_preview(api, body)
        await post_change(api, w, valid_from="2026-01-16", base_rent="760.00")
        response = await api.post("/api/dunning/batch", json=selected_batch(body, old, [w["lease"]]))
        assert response.status_code == 409, response.text
        assert (await api.get("/api/dunning")).json() == []
        current = await batch_preview(api, body)
        row = next(r for r in current["candidates"] if r["lease_agreement_id"] == str(w["lease"]))
        assert Decimal(row["principal_amount"]) == Decimal("880.97")
        single = await api.post("/api/dunning/tenant", json={
            "tenant_profile_id": str(w["tenant"]), "lease_agreement_id": str(w["lease"]),
            "period_start": body["period_start"], "period_end": body["period_end"],
            "reference_date": body["reference_date"]})
        assert single.status_code == 201, single.text
        assert Decimal(single.json()["principal_amount"]) == Decimal("880.97")


@pytest.mark.asyncio
async def test_batch_parallel_replay_and_replay_after_changed_rent(rent_world):
    import asyncio
    w = rent_world
    body = fresh_batch_request()
    async with api_client(w) as api:
        preview = await batch_preview(api, body)
        request = selected_batch(body, preview, [w["lease"], w["hidden_lease"]])
    async with api_client(w) as first, api_client(w) as second:
        a, b = await asyncio.gather(first.post("/api/dunning/batch", json=request),
                                    second.post("/api/dunning/batch", json=request))
        assert a.status_code == b.status_code == 201, (a.text, b.text)
        assert a.json() == b.json()
        assert len(a.json()) == 2
        assert [Decimal(row["principal_amount"]) for row in a.json()] == [Decimal("850.00")] * 2
        originals = a.json()
        await post_change(first, w, valid_from="2026-01-16", base_rent="760.00")
        replay = await second.post("/api/dunning/batch", json=request)
        assert replay.status_code == 201, replay.text
        assert replay.json() == originals
        assert {row["id"] for row in (await second.get("/api/dunning")).json()} == {row["id"] for row in originals}


@pytest.mark.asyncio
async def test_batch_second_draft_failure_rolls_back_every_result(rent_world, monkeypatch):
    from fastapi import HTTPException
    from app.repositories.dunning_notice_repo import DunningNoticeRepository
    original = DunningNoticeRepository.create
    calls = 0

    async def fail_second(repo, notice):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise HTTPException(422, "Synthetic late second-draft failure")
        return await original(repo, notice)

    w = rent_world
    body = fresh_batch_request()
    async with api_client(w) as api:
        preview = await batch_preview(api, body)
        request = selected_batch(body, preview, [w["lease"], w["hidden_lease"]])
        monkeypatch.setattr(DunningNoticeRepository, "create", fail_second)
        response = await api.post("/api/dunning/batch", json=request)
        assert response.status_code == 422, response.text
        assert calls == 2
        assert (await api.get("/api/dunning")).json() == []
        monkeypatch.setattr(DunningNoticeRepository, "create", original)
        retry = await api.post("/api/dunning/batch", json=request)
        assert retry.status_code == 201, retry.text
        assert len(retry.json()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", [[], ["duplicate", "duplicate"]])
async def test_batch_empty_or_duplicate_selection_rejected(rent_world, selection):
    w = rent_world
    body = fresh_batch_request()
    async with api_client(w) as api:
        preview = await batch_preview(api, body)
        ids = [] if not selection else [w["lease"], w["lease"]]
        response = await api.post("/api/dunning/batch", json=selected_batch(body, preview, ids))
        assert response.status_code == 422, response.text
        assert (await api.get("/api/dunning")).json() == []


@pytest.mark.asyncio
async def test_batch_foreign_selection_cannot_create_partial_result(rent_world):
    w = rent_world
    body = fresh_batch_request()
    async with api_client(w) as api:
        preview = await batch_preview(api, body)
        response = await api.post("/api/dunning/batch", json=selected_batch(body, preview, [w["lease"], w["foreign_lease"]]))
        assert response.status_code in (404, 422), response.text
        assert (await api.get("/api/dunning")).json() == []


from tests.test_financial_scope_boundary import worker_tenant, scope_seed as scope_seed  # noqa: E402,F401


@pytest.mark.asyncio
async def test_batch_scope_members_and_revoked_replay_fail_closed(migrator_session, scope_seed):
    from sqlalchemy import text
    from tests.test_scoped_account_statements import statement_fixture, client, mutate
    from tests.test_scoped_dunning import grant
    ids = await statement_fixture(migrator_session, scope_seed, "lease")
    await grant(ids, ["read", "prepare"])
    body = batch_request(period_end="2025-12-31", reference_date="2026-01-15",
                         threshold="7000.00", accounting_scope_id=str(ids["scope"]))
    async with client(ids) as api:
        preview = await batch_preview(api, body)
        assert [r["lease_agreement_id"] for r in preview["candidates"]] == [str(ids["lease"])]
        assert Decimal(preview["candidates"][0]["principal_amount"]) == Decimal("7100.00")
        request = selected_batch(body, preview, [ids["lease"]])
        created = await api.post("/api/dunning/batch", json=request)
        assert created.status_code == 201, created.text
        assert created.json()[0]["accounting_scope_id"] == str(ids["scope"])
    await grant(ids, ["read"])
    async with client(ids) as api:
        replay = await api.post("/api/dunning/batch", json=request)
        assert replay.status_code == 404, replay.text
        assert len((await api.get("/api/dunning")).json()) == 1
    await mutate(text("UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL"), ids)
    async with client(ids) as api:
        assert (await api.post("/api/dunning/batch/preview", json=body)).status_code == 404
        assert (await api.post("/api/dunning/batch", json=request)).status_code == 404


@pytest.mark.asyncio
async def test_batch_waits_for_existing_financial_input_lock(rent_world, rent_db):
    import asyncio
    from sqlalchemy import text
    w = rent_world
    await rent_db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                          {"key": f"financial-inputs:{w['org']}"})
    blocker = await rent_db.scalar(text("SELECT pg_backend_pid()"))
    async with api_client(w) as api:
        task = asyncio.create_task(api.post("/api/dunning/batch/preview", json=fresh_batch_request()))
        waiting = False
        try:
            for _ in range(200):
                waiting = await rent_db.scalar(text("""SELECT EXISTS(SELECT 1 FROM pg_locks l
                    WHERE l.locktype='advisory' AND NOT l.granted
                    AND :blocker=ANY(pg_blocking_pids(l.pid)))"""), {"blocker": blocker})
                if waiting or task.done():
                    break
                await asyncio.sleep(0.01)
        finally:
            await rent_db.rollback()
        response = await asyncio.wait_for(task, timeout=15)
        assert waiting, "Batch dunning must wait for the shared financial-input boundary"
        assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_batch_migration_preserves_data_security_and_issued_original(rent_world):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_letters import create_template, issue_body
    w = rent_world
    body = fresh_batch_request()
    async with api_client(w) as api:
        preview = await batch_preview(api, body)
        request = selected_batch(body, preview, [w["lease"]])
        created = await api.post("/api/dunning/batch", json=request)
        assert created.status_code == 201, created.text
        template = await create_template(api)
        issued = await api.post("/api/letters/issue", json=issue_body(w, template))
        assert issued.status_code == 201, issued.text
        pdf_url = issued.json()[0]["pdf_url"]
        original_pdf = (await api.get(pdf_url)).content
        assert original_pdf.startswith(b"%PDF")
    paths = list((Path(__file__).parents[1] / "migrations/versions").glob("0135_*.py"))
    assert len(paths) == 1
    spec = importlib.util.spec_from_file_location("dunning_batch_migration", paths[0])
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0135" and migration.down_revision == "0134"
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            def snapshot():
                tables = connection.execute(text("""SELECT c.relname FROM pg_class c
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relkind IN ('r','p')
                    AND c.relname <> 'dunning_batch'
                    AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass
                        AND d.objid=c.oid AND d.deptype='e') ORDER BY c.relname""")).scalars().all()
                rows = {name: connection.execute(text(
                    f'SELECT to_jsonb(t)::text FROM "{name}" t ORDER BY to_jsonb(t)::text'
                )).scalars().all() for name in tables}
                security = [connection.execute(text(query)).all() for query in (
                    "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text,pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname <> 'dunning_batch' ORDER BY c.relname",
                    "SELECT tablename,policyname,permissive,roles::text,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' AND tablename <> 'dunning_batch' ORDER BY tablename,policyname",
                    "SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal AND c.relname <> 'dunning_batch' ORDER BY pg_get_triggerdef(t.oid)",
                    "SELECT pg_get_functiondef(p.oid),p.proacl::text,pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f' AND p.proname <> 'protect_dunning_batch' ORDER BY p.proname,p.oid",
                )]
                return rows, security

            try:
                before = snapshot()
                assert before[0]["lease_agreement"] and before[0]["letter_document"] and before[0]["dunning_notice"]
                with Operations.context(MigrationContext.configure(connection)):
                    with pytest.raises(DBAPIError):
                        with connection.begin_nested():
                            migration.downgrade()
                    assert snapshot() == before
                    connection.execute(text("TRUNCATE dunning_batch"))
                    migration.downgrade()
                    assert connection.scalar(text("SELECT to_regclass('public.dunning_batch')")) is None
                    assert snapshot() == before
                    migration.upgrade()
                    assert snapshot() == before
                    assert connection.scalar(text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='dunning_batch'::regclass"))
                    assert connection.scalar(text("SELECT count(*) FROM pg_policies WHERE tablename='dunning_batch'")) >= 2
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    async with api_client(w) as api:
        replay = await api.post("/api/dunning/batch", json=request)
        assert replay.status_code == 201 and replay.json() == created.json()
        assert (await api.get(pdf_url)).content == original_pdf


@pytest.mark.asyncio
async def test_batch_receipt_is_actor_scoped_and_immutable(rent_world):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_tenant_session
    w = rent_world
    async with api_client(w) as api:
        body = fresh_batch_request()
        preview = await batch_preview(api, body)
        created = await api.post("/api/dunning/batch", json=selected_batch(body, preview, [w["lease"]]))
        assert created.status_code == 201, created.text
    async with get_tenant_session(w["org"]) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(w["admin"])})
        assert await db.scalar(text("SELECT count(*) FROM dunning_batch")) == 1
        for statement in ("UPDATE dunning_batch SET request_digest=request_digest", "DELETE FROM dunning_batch"):
            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(text(statement))
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(w["clerk"])})
        assert await db.scalar(text("SELECT count(*) FROM dunning_batch")) == 0


@pytest.mark.asyncio
async def test_batch_escalation_change_invalidates_basis_and_preserves_issued_notice(rent_world, rent_db):
    w = rent_world
    # Issuance freezes the payment destination: one eligible account.
    await _set_org(rent_db, w["org"])
    await rent_db.execute(
        text("INSERT INTO bank_account (id, organization_id, property_id, account_type, label, iban) "
             "VALUES (:id, :org, :property, 'LAUFEND', 'T23 Mietkonto', 'DE36302501010002042407')"),
        {"id": uuid.uuid4(), "org": w["org"], "property": w["property"]},
    )
    await rent_db.commit()
    body = fresh_batch_request()
    async with api_client(w) as api:
        old = await batch_preview(api, body)
        created = await api.post("/api/dunning/tenant", json={
            "tenant_profile_id": str(w["tenant"]), "lease_agreement_id": str(w["lease"]),
            "period_start": body["period_start"], "period_end": body["period_end"],
            "reference_date": body["reference_date"]})
        assert created.status_code == 201, created.text
        issued = await api.post(f"/api/dunning/{created.json()['id']}/issue")
        assert issued.status_code == 200, issued.text
        original = issued.json()
        original_pdf = await api.get(f"/api/dunning/{original['id']}/pdf")
        assert original_pdf.status_code == 200 and original_pdf.content.startswith(b"%PDF")
        response = await api.post("/api/dunning/batch", json=selected_batch(body, old, [w["lease"]]))
        assert response.status_code == 409, response.text
        current = await batch_preview(api, body)
        row = next(row for row in current["candidates"] if row["lease_agreement_id"] == str(w["lease"]))
        assert row["level"] == 2
        assert Decimal(row["principal_amount"]) == Decimal("850.00")
        assert Decimal(row["fee_amount"]) == Decimal("2.50")
        assert Decimal(row["total_amount"]) == Decimal("852.50")
        created = await api.post("/api/dunning/batch", json=selected_batch(body, current, [w["lease"]]))
        assert created.status_code == 201, created.text
        assert (await api.get(f"/api/dunning/{original['id']}")).json() == original
        assert (await api.get(f"/api/dunning/{original['id']}/pdf")).content == original_pdf.content


@pytest.mark.asyncio
async def test_batch_live_role_downgrade_blocks_existing_cookie_and_replay(rent_world, rent_db):
    from sqlalchemy import text
    w = rent_world
    async with api_client(w) as api:
        body = fresh_batch_request()
        preview = await batch_preview(api, body)
        request = selected_batch(body, preview, [w["lease"]])
        created = await api.post("/api/dunning/batch", json=request)
        assert created.status_code == 201, created.text
        await rent_db.execute(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:admin"), w)
        await rent_db.commit()
        assert (await api.post("/api/dunning/batch/preview", json=body)).status_code == 403
        assert (await api.post("/api/dunning/batch", json=request)).status_code == 403
        assert {row["id"] for row in (await api.get("/api/dunning")).json()} == {row["id"] for row in created.json()}


# ===========================================================================
# Pure engine tests (no DB)
# ===========================================================================
class TestDunningEngine:
    """Test the pure dunning engine functions."""

    def test_find_overdue_items_filters_arrears(self):
        from app.engine.mietkonto import OpenItem
        from app.engine.dunning import find_overdue_items

        items = [
            OpenItem(period_year=2025, period_month=3, payment_type="KALTMIETE",
                     soll=Decimal("500.00"), ist=Decimal("0.00"), open_amount=Decimal("500.00")),
            OpenItem(period_year=2025, period_month=3, payment_type="NK_VORAUSZAHLUNG",
                     soll=Decimal("100.00"), ist=Decimal("0.00"), open_amount=Decimal("100.00")),
            # Balanced -- should NOT be included
            OpenItem(period_year=2025, period_month=1, payment_type="KALTMIETE",
                     soll=Decimal("500.00"), ist=Decimal("500.00"), open_amount=Decimal("0.00")),
            # Credit -- should NOT be included
            OpenItem(period_year=2025, period_month=2, payment_type="KALTMIETE",
                     soll=Decimal("500.00"), ist=Decimal("600.00"), open_amount=Decimal("-100.00")),
        ]

        ref_date = date(2025, 7, 1)
        overdue = find_overdue_items(items, ref_date, grace_days=0, current_year=2026)
        assert len(overdue) == 2
        assert all(it.open_amount > 0 for it in overdue)
        assert overdue[0].payment_type == "KALTMIETE"
        assert overdue[1].payment_type == "NK_VORAUSZAHLUNG"

    def test_find_overdue_items_respects_grace_period(self):
        from app.engine.mietkonto import OpenItem
        from app.engine.dunning import find_overdue_items

        items = [
            OpenItem(period_year=2025, period_month=6, payment_type="KALTMIETE",
                     soll=Decimal("500.00"), ist=Decimal("0.00"), open_amount=Decimal("500.00")),
        ]
        # June 2025 due date is June 1; ref_date June 2 with 5 days grace
        # -> deadline June 6, ref June 2 is NOT past deadline.
        overdue = find_overdue_items(items, date(2025, 6, 2), grace_days=5, current_year=2026)
        assert len(overdue) == 0

        # But with ref_date July 1 (past June 6) it IS overdue.
        overdue = find_overdue_items(items, date(2025, 7, 1), grace_days=5, current_year=2026)
        assert len(overdue) == 1

    def test_escalation_levels(self):
        from app.engine.dunning import next_dunning_level, MAX_LEVEL

        assert next_dunning_level(0) == 1  # no prior notices -> level 1
        assert next_dunning_level(1) == 2  # 1 prior -> level 2
        assert next_dunning_level(2) == 3  # 2 prior -> level 3
        assert next_dunning_level(3) == MAX_LEVEL  # capped at max
        assert next_dunning_level(10) == MAX_LEVEL  # stays capped

    def test_dunning_fee_defaults(self):
        from app.engine.dunning import dunning_fee

        assert dunning_fee(1) == Decimal("0.00")
        assert dunning_fee(2) == Decimal("2.50")
        assert dunning_fee(3) == Decimal("2.50")

    def test_dunning_fee_custom(self):
        from app.engine.dunning import dunning_fee

        custom = {1: Decimal("2.50"), 2: Decimal("7.00"), 3: Decimal("15.00")}
        assert dunning_fee(1, custom) == Decimal("2.50")
        assert dunning_fee(2, custom) == Decimal("7.00")
        assert dunning_fee(3, custom) == Decimal("15.00")

    def test_assemble_dunning_golden(self):
        """Golden test: arrears -> dunning with correct principal + fee + total."""
        from app.engine.dunning import OverdueItem, assemble_dunning

        items = [
            OverdueItem(period_year=2025, period_month=3, payment_type="KALTMIETE",
                        soll=Decimal("500.00"), ist=Decimal("0.00"), open_amount=Decimal("500.00")),
            OverdueItem(period_year=2025, period_month=3, payment_type="NK_VORAUSZAHLUNG",
                        soll=Decimal("100.00"), ist=Decimal("0.00"), open_amount=Decimal("100.00")),
            OverdueItem(period_year=2025, period_month=4, payment_type="KALTMIETE",
                        soll=Decimal("500.00"), ist=Decimal("0.00"), open_amount=Decimal("500.00")),
            OverdueItem(period_year=2025, period_month=4, payment_type="NK_VORAUSZAHLUNG",
                        soll=Decimal("100.00"), ist=Decimal("0.00"), open_amount=Decimal("100.00")),
        ]

        # First dunning (0 prior issued notices) -> level 1, fee 0.00
        result = assemble_dunning(items, existing_notice_count=0,
                                   issue_date=date(2025, 7, 1))
        assert result is not None
        assert result.level == 1
        assert result.principal_amount == Decimal("1200.00")
        assert result.fee_amount == Decimal("0.00")
        assert result.total_amount == Decimal("1200.00")
        assert result.due_date == date(2025, 7, 15)
        assert len(result.overdue_items) == 4
        # Snapshot is serializable
        snapshot = result.items_snapshot()
        assert len(snapshot) == 4
        assert snapshot[0]["open_amount"] == "500.00"

    def test_assemble_dunning_level2_with_fee(self):
        from app.engine.dunning import OverdueItem, assemble_dunning

        items = [
            OverdueItem(period_year=2025, period_month=3, payment_type="KALTMIETE",
                        soll=Decimal("500.00"), ist=Decimal("0.00"), open_amount=Decimal("500.00")),
        ]
        # 1 prior issued -> level 2, fee 2.50
        result = assemble_dunning(items, existing_notice_count=1,
                                   issue_date=date(2025, 7, 15))
        assert result is not None
        assert result.level == 2
        assert result.fee_amount == Decimal("2.50")
        assert result.total_amount == Decimal("502.50")

    def test_assemble_dunning_level3_with_fee(self):
        from app.engine.dunning import OverdueItem, assemble_dunning

        items = [
            OverdueItem(period_year=2025, period_month=3, payment_type="KALTMIETE",
                        soll=Decimal("500.00"), ist=Decimal("0.00"), open_amount=Decimal("500.00")),
        ]
        # 2 prior issued -> level 3, fee 2.50
        result = assemble_dunning(items, existing_notice_count=2,
                                   issue_date=date(2025, 8, 1))
        assert result is not None
        assert result.level == 3
        assert result.fee_amount == Decimal("2.50")
        assert result.total_amount == Decimal("502.50")

    def test_assemble_dunning_no_overdue_returns_none(self):
        from app.engine.dunning import assemble_dunning

        result = assemble_dunning([], existing_notice_count=0,
                                   issue_date=date(2025, 7, 1))
        assert result is None

    def test_assemble_dunning_override_fee(self):
        from app.engine.dunning import OverdueItem, assemble_dunning

        items = [
            OverdueItem(period_year=2025, period_month=3, payment_type="KALTMIETE",
                        soll=Decimal("500.00"), ist=Decimal("0.00"), open_amount=Decimal("500.00")),
        ]
        result = assemble_dunning(items, existing_notice_count=0,
                                   issue_date=date(2025, 7, 1),
                                   override_fee=Decimal("3.00"))
        assert result is not None
        assert result.fee_amount == Decimal("3.00")
        assert result.total_amount == Decimal("503.00")


# ===========================================================================
# DB-level: Immutability trigger (issued dunning_notice is frozen)
# ===========================================================================
class TestDunningImmutability:
    async def _insert_issued(self, session, m7_seed) -> uuid.UUID:
        did = uuid.uuid4()
        import json
        items = json.dumps([{
            "period_year": 2025, "period_month": 3,
            "payment_type": "KALTMIETE",
            "soll": "500.00", "ist": "0.00", "open_amount": "500.00",
        }])
        await session.execute(
            text(_insert_dunning_sql()),
            {
                "id": str(did), "org": str(m7_seed["org_a_id"]),
                "tenant": str(m7_seed["tenant_m7"]), "owner": None,
                "level": 1, "issue_date": _ISSUE_DATE, "due_date": _DUE_DATE,
                "principal": "500.00", "fee": "0.00", "total": "500.00",
                "items": items, "status": "issued",
                "issued_at": _ISSUED_AT, "issued_by": str(m7_seed["user_a_admin_id"]),
            },
        )
        return did

    @pytest.mark.asyncio
    async def test_update_amount_on_issued_rejected(self, app_rw_session, m7_seed):
        session = app_rw_session
        await _set_org(session, m7_seed["org_a_id"])
        did = await self._insert_issued(session, m7_seed)
        with pytest.raises(Exception, match="Immutability.*principal_amount.*issued"):
            await session.execute(
                text("UPDATE dunning_notice SET principal_amount = '999.00' WHERE id = :id"),
                {"id": str(did)},
            )

    @pytest.mark.asyncio
    async def test_update_level_on_issued_rejected(self, app_rw_session, m7_seed):
        session = app_rw_session
        await _set_org(session, m7_seed["org_a_id"])
        did = await self._insert_issued(session, m7_seed)
        with pytest.raises(Exception, match="Immutability.*level.*issued"):
            await session.execute(
                text("UPDATE dunning_notice SET level = 2 WHERE id = :id"),
                {"id": str(did)},
            )

    @pytest.mark.asyncio
    async def test_update_status_on_issued_rejected(self, app_rw_session, m7_seed):
        session = app_rw_session
        await _set_org(session, m7_seed["org_a_id"])
        did = await self._insert_issued(session, m7_seed)
        with pytest.raises(Exception, match="Immutability.*status.*issued"):
            await session.execute(
                text("UPDATE dunning_notice SET status = 'draft' WHERE id = :id"),
                {"id": str(did)},
            )

    @pytest.mark.asyncio
    async def test_delete_issued_forbidden(self, app_rw_session, m7_seed):
        session = app_rw_session
        await _set_org(session, m7_seed["org_a_id"])
        did = await self._insert_issued(session, m7_seed)
        with pytest.raises(Exception, match="Immutability.*DELETE.*issued"):
            await session.execute(
                text("DELETE FROM dunning_notice WHERE id = :id"), {"id": str(did)}
            )

    @pytest.mark.asyncio
    async def test_delete_draft_allowed(self, app_rw_session, m7_seed):
        session = app_rw_session
        await _set_org(session, m7_seed["org_a_id"])
        did = uuid.uuid4()
        import json
        items = json.dumps([])
        await session.execute(
            text(_insert_dunning_sql()),
            {
                "id": str(did), "org": str(m7_seed["org_a_id"]),
                "tenant": str(m7_seed["tenant_m7"]), "owner": None,
                "level": 1, "issue_date": _ISSUE_DATE, "due_date": _DUE_DATE,
                "principal": "100.00", "fee": "0.00", "total": "100.00",
                "items": items, "status": "draft",
                "issued_at": None, "issued_by": None,
            },
        )
        await session.execute(
            text("DELETE FROM dunning_notice WHERE id = :id"), {"id": str(did)}
        )
        row = await session.execute(
            text("SELECT id FROM dunning_notice WHERE id = :id"), {"id": str(did)}
        )
        assert row.first() is None

    @pytest.mark.asyncio
    async def test_update_draft_allowed(self, app_rw_session, m7_seed):
        session = app_rw_session
        await _set_org(session, m7_seed["org_a_id"])
        did = uuid.uuid4()
        import json
        items = json.dumps([])
        await session.execute(
            text(_insert_dunning_sql()),
            {
                "id": str(did), "org": str(m7_seed["org_a_id"]),
                "tenant": str(m7_seed["tenant_m7"]), "owner": None,
                "level": 1, "issue_date": _ISSUE_DATE, "due_date": _DUE_DATE,
                "principal": "100.00", "fee": "0.00", "total": "100.00",
                "items": items, "status": "draft",
                "issued_at": None, "issued_by": None,
            },
        )
        # Should NOT raise
        await session.execute(
            text("UPDATE dunning_notice SET principal_amount = '200.00' WHERE id = :id"),
            {"id": str(did)},
        )
        row = await session.execute(
            text("SELECT principal_amount FROM dunning_notice WHERE id = :id"), {"id": str(did)}
        )
        assert row.scalar_one() == Decimal("200.00")


# ===========================================================================
# RLS: org-isolated, fail-closed
# ===========================================================================
class TestDunningRLS:
    @pytest.mark.asyncio
    async def test_org_b_cannot_see_org_a_dunning(self, app_rw_session, m7_seed):
        session = app_rw_session
        await _set_org(session, m7_seed["org_a_id"])
        did = uuid.uuid4()
        import json
        items = json.dumps([])
        await session.execute(
            text(_insert_dunning_sql()),
            {
                "id": str(did), "org": str(m7_seed["org_a_id"]),
                "tenant": str(m7_seed["tenant_m7"]), "owner": None,
                "level": 1, "issue_date": _ISSUE_DATE, "due_date": _DUE_DATE,
                "principal": "100.00", "fee": "0.00", "total": "100.00",
                "items": items, "status": "draft",
                "issued_at": None, "issued_by": None,
            },
        )
        # Switch to org_b
        await _set_org(session, m7_seed["org_b_id"])
        row = await session.execute(
            text("SELECT id FROM dunning_notice WHERE id = :id"), {"id": str(did)}
        )
        assert row.first() is None

    @pytest.mark.asyncio
    async def test_no_org_context_returns_nothing(self, app_rw_session, m7_seed):
        session = app_rw_session
        await session.execute(text("SELECT set_config('app.current_org_id', '', true)"))
        row = await session.execute(text("SELECT count(*) FROM dunning_notice"))
        assert row.scalar_one() == 0


# ===========================================================================
# CHECK constraints
# ===========================================================================
class TestDunningChecks:
    @pytest.mark.asyncio
    async def test_both_counterparties_rejected(self, app_rw_session, m7_seed):
        """Both tenant and owner set -> CHECK violation."""
        session = app_rw_session
        await _set_org(session, m7_seed["org_a_id"])
        import json
        with pytest.raises(Exception, match="ck_dunning_notice_one_counterparty|check constraint"):
            await session.execute(
                text(_insert_dunning_sql()),
                {
                    "id": str(uuid.uuid4()), "org": str(m7_seed["org_a_id"]),
                    "tenant": str(m7_seed["tenant_m7"]),
                    "owner": str(m7_seed["owner_m7"]),  # BOTH set
                    "level": 1, "issue_date": _ISSUE_DATE, "due_date": _DUE_DATE,
                    "principal": "100.00", "fee": "0.00", "total": "100.00",
                    "items": json.dumps([]), "status": "draft",
                    "issued_at": None, "issued_by": None,
                },
            )

    @pytest.mark.asyncio
    async def test_level_out_of_range_rejected(self, app_rw_session, m7_seed):
        session = app_rw_session
        await _set_org(session, m7_seed["org_a_id"])
        import json
        with pytest.raises(Exception, match="ck_dunning_notice_level|check constraint"):
            await session.execute(
                text(_insert_dunning_sql()),
                {
                    "id": str(uuid.uuid4()), "org": str(m7_seed["org_a_id"]),
                    "tenant": str(m7_seed["tenant_m7"]), "owner": None,
                    "level": 0,  # out of range
                    "issue_date": _ISSUE_DATE, "due_date": _DUE_DATE,
                    "principal": "100.00", "fee": "0.00", "total": "100.00",
                    "items": json.dumps([]), "status": "draft",
                    "issued_at": None, "issued_by": None,
                },
            )


# ===========================================================================
# API: create / issue / list / delete / PDF + RBAC
# ===========================================================================
class TestDunningAPI:
    @pytest.mark.parametrize('dated', [False, True])
    def test_owner_notice_names_snapshot_due_day_and_preserves_legacy_html(self, dated):
        from hashlib import sha256
        from types import SimpleNamespace
        from app.services.dunning_pdf import build_dunning_html

        monthly = {'period_year': 2026, 'period_month': 7, 'payment_type': 'HAUSGELD_VORSCHUSS',
            'soll': '600.00', 'ist': '0.00', 'open_amount': '600.00'}
        if dated:
            monthly['due_date'] = '2026-07-03'
        notice = SimpleNamespace(level=1, principal_amount=Decimal('650.00'), fee_amount=Decimal('0.00'),
            interest_amount=Decimal('0.00'), interest_snapshot=None, total_amount=Decimal('650.00'),
            issue_date=date(2026, 7, 4), due_date=date(2026, 7, 18), included_items=[monthly,
                {'period_year': 2026, 'period_month': 7, 'payment_type': 'SONDERZAHLUNG',
                 'soll': '50.00', 'ist': '0.00', 'open_amount': '50.00', 'due_date': '2026-07-01'}])
        html = build_dunning_html(notice)
        if dated:
            assert 'Fällig am 03.07.2026' in html
        else:
            # Fingerprint of the pre-date-rendering template, including an old dated levy.
            assert sha256(html.encode()).hexdigest() == 'dc4f5138fea695bd60344a59af98be9defbe8edcc32227a47e5591126975b112'

    def test_owner_notice_omits_the_fallback_date_for_a_payment_without_a_period(self):
        from types import SimpleNamespace
        from app.engine.dunning import assemble_dunning, find_overdue_items
        from app.engine.mietkonto import PaymentInput, account_statement
        from app.services.dunning_pdf import build_dunning_html

        issue_date = date(2026, 7, 4)
        payment = PaymentInput(uuid.uuid4(), 'HAUSGELD_VORSCHUSS', 'OUT', Decimal('600.00'),
            period_year=None, period_month=None)
        statement = account_statement([], [payment])
        overdue = find_overdue_items(list(statement.open_items), issue_date, current_year=2026)
        draft = assemble_dunning(overdue, 0, issue_date)
        items = draft.items_snapshot()
        assert items == [{'period_year': 0, 'period_month': None, 'payment_type': 'HAUSGELD_VORSCHUSS',
            'soll': '0.00', 'ist': '-600.00', 'open_amount': '600.00', 'due_date': '2026-01-01',
            '_dunning_format': 2}]

        issued = SimpleNamespace(**vars(draft), included_items=items, status='issued')
        assert 'Fällig am 01.01.2026' not in build_dunning_html(issued)

    @pytest.mark.parametrize("issued_at", [None, datetime(2026, 11, 1, 12, 26, 4, 611054, tzinfo=_tz.utc)])
    def test_issue_time_serializes_as_iso_with_timezone(self, issued_at):
        from app.api.dunning import _to_out
        from app.models.dunning_notice import DunningNotice

        notice = DunningNotice(
            id=uuid.uuid4(), organization_id=uuid.uuid4(), level=1,
            issue_date=date(2026, 11, 1), due_date=date(2026, 11, 15),
            principal_amount=Decimal("1234.56"), fee_amount=Decimal("0.00"),
            interest_amount=Decimal("15.22"), total_amount=Decimal("1249.78"),
            included_items=[], status="issued" if issued_at else "draft", issued_at=issued_at,
        )
        output = _to_out(notice)
        assert output.issued_at == ("2026-11-01T12:26:04.611054+00:00" if issued_at else None)

    @pytest.mark.asyncio
    async def test_create_issue_list_flow(self, client_a_admin, m7_seed):
        """Golden: tenant with arrears -> dunning level 1 with correct amounts."""
        # Create dunning for tenant
        r = await client_a_admin.post(
            "/api/dunning/tenant",
            json={
                "tenant_profile_id": str(m7_seed["tenant_m7"]),
                "lease_agreement_id": str(m7_seed["lease_m7"]),
                "period_start": "2025-01-01",
                "period_end": "2025-06-30",
                "reference_date": "2025-07-01",
                "grace_days": 0,
            },
        )
        assert r.status_code == 201, r.text
        data = r.json()
        dunning_id = data["id"]
        assert data["status"] == "draft"
        assert data["level"] == 1
        # 4 months unpaid (Mar-Jun): each 500 rent + 100 NK = 600/month = 2400 total
        assert data["principal_amount"] == "2400.00"
        assert data["fee_amount"] == "0.00"  # Level 1 default fee
        assert data["total_amount"] == "2400.00"
        assert data["tenant_profile_id"] == str(m7_seed["tenant_m7"])
        assert data["included_items"] is not None
        assert len(data["included_items"]) == 8  # 4 months * 2 types

        # Issue it
        r = await client_a_admin.post(f"/api/dunning/{dunning_id}/issue")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "issued"
        assert data["issued_at"] is not None

        # List all
        r = await client_a_admin.get("/api/dunning")
        assert r.status_code == 200
        notices = r.json()
        assert any(n["id"] == dunning_id for n in notices)

        # Detail
        r = await client_a_admin.get(f"/api/dunning/{dunning_id}")
        assert r.status_code == 200
        assert r.json()["id"] == dunning_id

    @pytest.mark.asyncio
    async def test_escalation_1_to_2(self, isolated_batch_seed):
        """After one issued notice, the next should be level 2."""
        client_a_admin, m7_seed = isolated_batch_seed
        first = await client_a_admin.post(
            "/api/dunning/tenant",
            json={
                "tenant_profile_id": str(m7_seed["tenant_m7"]),
                "lease_agreement_id": str(m7_seed["lease_m7"]),
                "period_start": "2025-01-01",
                "period_end": "2025-06-30",
                "reference_date": "2025-07-01",
            },
        )
        assert first.status_code == 201, first.text
        assert first.json()["level"] == 1
        issued = await client_a_admin.post(f"/api/dunning/{first.json()['id']}/issue")
        assert issued.status_code == 200, issued.text
        r = await client_a_admin.post(
            "/api/dunning/tenant",
            json={
                "tenant_profile_id": str(m7_seed["tenant_m7"]),
                "lease_agreement_id": str(m7_seed["lease_m7"]),
                "period_start": "2025-01-01",
                "period_end": "2025-06-30",
                "reference_date": "2025-07-15",
            },
        )
        assert r.status_code == 201
        data = r.json()
        assert data["level"] == 2

    @pytest.mark.asyncio
    async def test_pdf_only_for_issued(self, client_a_admin, m7_seed):
        """PDF endpoint returns 409 for draft, works for issued."""
        # Create a draft
        r = await client_a_admin.post(
            "/api/dunning/tenant",
            json={
                "tenant_profile_id": str(m7_seed["tenant_m7"]),
                "lease_agreement_id": str(m7_seed["lease_m7"]),
                "period_start": "2025-01-01",
                "period_end": "2025-06-30",
                "reference_date": "2025-07-01",
            },
        )
        assert r.status_code == 201
        dunning_id = r.json()["id"]

        # PDF for draft -> 409
        r = await client_a_admin.get(f"/api/dunning/{dunning_id}/pdf")
        assert r.status_code == 409

        # Issue it
        r = await client_a_admin.post(f"/api/dunning/{dunning_id}/issue")
        assert r.status_code == 200

        # PDF for issued -> 200 + %PDF
        r = await client_a_admin.get(f"/api/dunning/{dunning_id}/pdf")
        assert r.status_code == 200
        assert r.content[:5] == b"%PDF-"

    @pytest.mark.asyncio
    async def test_delete_draft_succeeds(self, client_a_admin, m7_seed):
        r = await client_a_admin.post(
            "/api/dunning/tenant",
            json={
                "tenant_profile_id": str(m7_seed["tenant_m7"]),
                "lease_agreement_id": str(m7_seed["lease_m7"]),
                "period_start": "2025-01-01",
                "period_end": "2025-06-30",
                "reference_date": "2025-07-01",
            },
        )
        assert r.status_code == 201
        dunning_id = r.json()["id"]

        r = await client_a_admin.delete(f"/api/dunning/{dunning_id}")
        assert r.status_code == 204

        # Verify gone
        r = await client_a_admin.get(f"/api/dunning/{dunning_id}")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_issued_rejected(self, client_a_admin, m7_seed):
        r = await client_a_admin.post(
            "/api/dunning/tenant",
            json={
                "tenant_profile_id": str(m7_seed["tenant_m7"]),
                "lease_agreement_id": str(m7_seed["lease_m7"]),
                "period_start": "2025-01-01",
                "period_end": "2025-06-30",
                "reference_date": "2025-07-01",
            },
        )
        assert r.status_code == 201
        dunning_id = r.json()["id"]

        # Issue
        r = await client_a_admin.post(f"/api/dunning/{dunning_id}/issue")
        assert r.status_code == 200

        # Delete -> 409
        r = await client_a_admin.delete(f"/api/dunning/{dunning_id}")
        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_readonly_cannot_create(self, client_a_readonly, m7_seed):
        r = await client_a_readonly.post(
            "/api/dunning/tenant",
            json={
                "tenant_profile_id": str(m7_seed["tenant_m7"]),
                "lease_agreement_id": str(m7_seed["lease_m7"]),
                "period_start": "2025-01-01",
                "period_end": "2025-06-30",
                "reference_date": "2025-07-01",
            },
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_readonly_can_list(self, client_a_readonly, m7_seed):
        r = await client_a_readonly.get("/api/dunning")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_foreign_id_returns_404(self, client_a_admin, m7_seed):
        r = await client_a_admin.get(f"/api/dunning/{uuid.uuid4()}")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_cross_org_returns_empty(self, client_b_admin, m7_seed):
        """Org B should not see org A's dunning notices."""
        r = await client_b_admin.get("/api/dunning")
        assert r.status_code == 200
        # Should have no notices (org B has none)
        assert len(r.json()) == 0

    @pytest.mark.asyncio
    async def test_filter_by_status(self, client_a_admin, m7_seed):
        r = await client_a_admin.get("/api/dunning", params={"status": "issued"})
        assert r.status_code == 200
        for n in r.json():
            assert n["status"] == "issued"

    @pytest.mark.asyncio
    async def test_filter_by_tenant(self, client_a_admin, m7_seed):
        r = await client_a_admin.get(
            "/api/dunning",
            params={"tenant_profile_id": str(m7_seed["tenant_m7"])},
        )
        assert r.status_code == 200
        for n in r.json():
            assert n["tenant_profile_id"] == str(m7_seed["tenant_m7"])


@pytest.mark.asyncio
async def test_t35_migration_roundtrip_preserves_old_letters(migrator_session, m7_seed):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from tests.conftest import MIGRATOR_SYNC_URL
    await _set_org(migrator_session, m7_seed["org_a_id"])
    legacy_id = await TestDunningImmutability()._insert_issued(migrator_session, m7_seed)
    await migrator_session.commit()
    migration = importlib.import_module("migrations.versions.0129_dunning_interest")
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            point = connection.begin_nested()
            try:
                # Rehearse 0129 with a pre-interest notice. Other tests may
                # have issued interest-bearing notices in this disposable DB.
                # The savepoint restores them and their dependencies.
                connection.execute(text("""
                    CREATE TEMP TABLE dunning_0129_fixture AS
                    SELECT * FROM dunning_notice WHERE id=:legacy
                """), {"legacy": legacy_id})
                connection.execute(text("TRUNCATE public.dunning_notice CASCADE"))
                connection.execute(text("INSERT INTO dunning_notice SELECT * FROM dunning_0129_fixture"))
                connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                before = connection.execute(text("SELECT to_jsonb(n) - 'interest_amount' - 'interest_snapshot' FROM dunning_notice n WHERE status='issued' ORDER BY id")).scalars().all()
                assert before, "Roundtrip must cover existing issued letters"
                function = connection.scalar(text("SELECT pg_get_functiondef('trg_dunning_notice_immutability()'::regprocedure)"))
                migration.downgrade()
                assert not connection.scalar(text("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='dunning_notice' AND column_name='interest_amount')"))
                assert connection.execute(text("SELECT to_jsonb(n) FROM dunning_notice n WHERE status='issued' ORDER BY id")).scalars().all() == before
                migration.upgrade()
                assert connection.scalar(text("SELECT pg_get_functiondef('trg_dunning_notice_immutability()'::regprocedure)")) == function
                assert connection.execute(text("SELECT to_jsonb(n) - 'interest_amount' - 'interest_snapshot' FROM dunning_notice n WHERE status='issued' ORDER BY id")).scalars().all() == before
                assert connection.scalar(text("SELECT count(*) FROM dunning_notice WHERE interest_snapshot IS NOT NULL OR interest_amount <> 0")) == 0
            finally:
                point.rollback()
    finally:
        engine.dispose()


class TestT35Interest:
    @staticmethod
    def tenant_body(seed, **extra):
        return {
            "tenant_profile_id": str(seed["tenant_m7"]),
            "lease_agreement_id": str(seed["lease_m7"]),
            "period_start": "2025-03-01", "period_end": "2025-03-31",
            "reference_date": "2025-04-30", "override_fee": "0.00", **extra,
        }

    @pytest.mark.asyncio
    async def test_removed_manual_rate_is_rejected(self, client_a_admin, m7_seed):
        response = await client_a_admin.post("/api/dunning/tenant", json=self.tenant_body(
            m7_seed, base_interest_rate="2.50"))
        assert response.status_code == 422, response.text

    @pytest.mark.asyncio
    async def test_t35_api_and_pdf_snapshot(self, client_a_admin, m7_seed, enabled_dunning_interest):
        import io
        import pdfplumber
        response = await client_a_admin.post("/api/dunning/tenant", json=self.tenant_body(
            m7_seed))
        assert response.status_code == 201, response.text
        notice = response.json()
        assert notice["principal_amount"] == "600.00"
        assert notice["interest_amount"] == "6.70"
        assert notice["total_amount"] == "606.70"
        assert [item["days"] for item in notice["interest_snapshot"]["items"]] == [56, 56]
        response = await client_a_admin.post(f'/api/dunning/{notice["id"]}/issue')
        assert response.status_code == 200, response.text
        response = await client_a_admin.get(f'/api/dunning/{notice["id"]}/pdf')
        assert response.status_code == 200, response.text
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            text = "\n".join(page.extract_text() for page in pdf.pages)
        for value in ("Basiszinssatz", "2,27", "7,27", "30.04.2025", "56", "6,70", "606,70"):
            assert value in text

    @pytest.mark.asyncio
    async def test_missing_rate_returns_conflict_without_creating_a_draft(
        self, client_a_admin, m7_seed, enabled_dunning_interest,
    ):
        before = (await client_a_admin.get("/api/dunning")).json()
        response = await client_a_admin.post("/api/dunning/tenant", json=self.tenant_body(
            m7_seed, reference_date="2027-01-01"))
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "base_interest_rate_missing"
        assert "01.01.2027" in response.json()["detail"]["message"]
        assert (await client_a_admin.get("/api/dunning")).json() == before

    @pytest.mark.asyncio
    @pytest.mark.parametrize("extra", [
        {"override_fee": "1000000000000"}, {"override_fee": "NaN"},
    ])
    async def test_t35_overflow_returns_422(self, client_a_admin, m7_seed, extra):
        response = await client_a_admin.post("/api/dunning/tenant", json=self.tenant_body(m7_seed, **extra))
        assert response.status_code == 422, response.text

    @pytest.mark.asyncio
    async def test_t35_settings_authenticated(self, client_a_admin, client_a_readonly):
        from httpx import ASGITransport, AsyncClient
        from app.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anonymous:
            assert (await anonymous.get("/api/dunning/settings")).status_code == 401
        assert (await client_a_readonly.get("/api/dunning/settings")).status_code == 200

    @pytest.mark.asyncio
    async def test_t35_issued_interest_sql_immutable_and_downgrade_guard(self, client_a_admin, m7_seed, app_rw_session, enabled_dunning_interest):
        import importlib
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import create_engine
        from sqlalchemy.exc import DBAPIError
        from tests.conftest import MIGRATOR_SYNC_URL
        response = await client_a_admin.post("/api/dunning/tenant", json=self.tenant_body(m7_seed))
        assert response.status_code == 201, response.text
        notice = response.json()
        assert (await client_a_admin.post(f'/api/dunning/{notice["id"]}/issue')).status_code == 200
        await _set_org(app_rw_session, m7_seed["org_a_id"])
        for sql in ["UPDATE dunning_notice SET interest_amount=0 WHERE id=:id",
                    "UPDATE dunning_notice SET interest_snapshot=NULL WHERE id=:id",
                    "UPDATE dunning_notice SET interest_snapshot='{}' WHERE id=:id"]:
            with pytest.raises(DBAPIError, match="Immutability.*interest"):
                async with app_rw_session.begin_nested():
                    await app_rw_session.execute(text(sql), {"id": uuid.UUID(notice["id"])})
        await app_rw_session.rollback()
        engine = create_engine(MIGRATOR_SYNC_URL)
        migration = importlib.import_module("migrations.versions.0129_dunning_interest")
        try:
            with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(DBAPIError, match="Cannot downgrade while interest-backed"):
                    with connection.begin_nested():
                        migration.downgrade()
                assert connection.scalar(text("SELECT interest_amount FROM dunning_notice WHERE id=:id"), {"id": notice["id"]}) == Decimal("6.70")
        finally:
            engine.dispose()


M7_IBAN = "DE02200505501015871393"


def _pdf_text(content: bytes) -> str:
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _m7_draft_body(seed, **extra):
    return {
        "tenant_profile_id": str(seed["tenant_m7"]),
        "lease_agreement_id": str(seed["lease_m7"]),
        "period_start": "2025-03-01", "period_end": "2025-03-31",
        "reference_date": "2025-04-30", "override_fee": "0.00", **extra,
    }


@pytest.mark.asyncio
async def test_issue_autoselects_single_eligible_account_and_freezes_destination(
    client_a_admin, m7_seed, rent_db,
):
    created = await client_a_admin.post("/api/dunning/tenant", json=_m7_draft_body(m7_seed))
    assert created.status_code == 201, created.text
    notice_id = created.json()["id"]

    issued = await client_a_admin.post(f"/api/dunning/{notice_id}/issue")
    assert issued.status_code == 200, issued.text

    await _set_org(rent_db, m7_seed["org_a_id"])
    values = await rent_db.scalar(
        text("SELECT frozen_display_names->'values' FROM dunning_notice WHERE id=:id"),
        {"id": uuid.UUID(notice_id)},
    )
    assert values["bank_iban"] == M7_IBAN
    assert values["property_name"] == "Mahnhaus"
    assert values["unit_label"] == "M7-1"

    pdf = await client_a_admin.get(f"/api/dunning/{notice_id}/pdf")
    assert pdf.status_code == 200
    page_text = _pdf_text(pdf.content)
    assert "Mahnhaus" in page_text and "M7-1" in page_text and M7_IBAN in page_text


@pytest.mark.asyncio
async def test_issue_requires_explicit_selection_with_several_eligible_accounts(
    client_a_admin, m7_seed, rent_db,
):
    second = uuid.uuid4()
    await _set_org(rent_db, m7_seed["org_a_id"])
    await rent_db.execute(
        text("INSERT INTO bank_account (id, organization_id, property_id, account_type, label, iban) "
             "VALUES (:id, :org, :property, 'LAUFEND', 'Zweites Mietkonto', 'DE44120300000000202052')"),
        {"id": second, "org": m7_seed["org_a_id"], "property": m7_seed["prop_m7"]},
    )
    # A foreign-organization account with an IBAN must never be offered.
    await rent_db.execute(
        text("UPDATE bank_account SET iban='DE89370400440532013000' WHERE id=:id"),
        {"id": m7_seed["acct_b_m7"]},
    )
    await rent_db.commit()

    # The seed organization is session-scoped; remove the extra account again
    # so tests after this one keep exactly one eligible account.
    try:
        created = await client_a_admin.post("/api/dunning/tenant", json=_m7_draft_body(m7_seed))
        assert created.status_code == 201, created.text
        notice_id = created.json()["id"]

        refused = await client_a_admin.post(f"/api/dunning/{notice_id}/issue")
        assert refused.status_code == 409, refused.text
        detail = refused.json()["detail"]
        assert detail["code"] == "dunning_bank_selection_required"
        offered = {row["id"]: row for row in detail["bank_accounts"]}
        assert set(offered) == {str(m7_seed["acct_m7"]), str(second)}
        assert offered[str(second)]["iban"] == "DE44120300000000202052"

        chosen = await client_a_admin.post(
            f"/api/dunning/{notice_id}/issue", json={"bank_account_id": str(second)},
        )
        assert chosen.status_code == 200, chosen.text
        page_text = _pdf_text((await client_a_admin.get(f"/api/dunning/{notice_id}/pdf")).content)
        assert "DE44120300000000202052" in page_text and M7_IBAN not in page_text
    finally:
        await _set_org(rent_db, m7_seed["org_a_id"])
        await rent_db.execute(
            text("DELETE FROM bank_account WHERE id=:id"), {"id": second},
        )
        await rent_db.execute(
            text("UPDATE bank_account SET iban=NULL WHERE id=:id"),
            {"id": m7_seed["acct_b_m7"]},
        )
        await rent_db.commit()


@pytest.mark.asyncio
async def test_issue_refused_without_eligible_account_and_recovers(
    client_a_admin, m7_seed, rent_db,
):
    await _set_org(rent_db, m7_seed["org_a_id"])
    await rent_db.execute(
        text("UPDATE bank_account SET is_active=false WHERE id=:id"),
        {"id": m7_seed["acct_m7"]},
    )
    await rent_db.commit()

    created = await client_a_admin.post("/api/dunning/tenant", json=_m7_draft_body(m7_seed))
    assert created.status_code == 201, created.text
    notice_id = created.json()["id"]

    refused = await client_a_admin.post(f"/api/dunning/{notice_id}/issue")
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "dunning_bank_account_missing"
    assert "Konto" in detail["message"]
    current = await client_a_admin.get(f"/api/dunning/{notice_id}")
    assert current.json()["status"] == "draft"

    await _set_org(rent_db, m7_seed["org_a_id"])
    await rent_db.execute(
        text("UPDATE bank_account SET is_active=true WHERE id=:id"),
        {"id": m7_seed["acct_m7"]},
    )
    await rent_db.commit()
    assert (await client_a_admin.post(f"/api/dunning/{notice_id}/issue")).status_code == 200


@pytest.mark.asyncio
async def test_issue_rejects_unknown_or_ineligible_selected_account(
    client_a_admin, m7_seed, rent_db,
):
    reserve = uuid.uuid4()
    await _set_org(rent_db, m7_seed["org_a_id"])
    await rent_db.execute(
        text("INSERT INTO bank_account (id, organization_id, property_id, account_type, label, iban) "
             "VALUES (:id, :org, :property, 'RUECKLAGE', 'Ruecklage', 'DE89370400440532013000')"),
        {"id": reserve, "org": m7_seed["org_a_id"], "property": m7_seed["prop_m7"]},
    )
    await rent_db.commit()

    created = await client_a_admin.post("/api/dunning/tenant", json=_m7_draft_body(m7_seed))
    assert created.status_code == 201, created.text
    notice_id = created.json()["id"]

    ineligible = await client_a_admin.post(
        f"/api/dunning/{notice_id}/issue", json={"bank_account_id": str(reserve)},
    )
    assert ineligible.status_code == 409, ineligible.text
    assert ineligible.json()["detail"]["code"] == "dunning_bank_account_ineligible"
    assert (await client_a_admin.post(
        f"/api/dunning/{notice_id}/issue", json={"bank_account_id": str(m7_seed["acct_b_m7"])},
    )).status_code == 404
    assert (await client_a_admin.post(
        f"/api/dunning/{notice_id}/issue", json={"bank_account_id": str(uuid.uuid4())},
    )).status_code == 404
    # The refused notice is untouched and issues with the auto-selected account.
    assert (await client_a_admin.post(f"/api/dunning/{notice_id}/issue")).status_code == 200


@pytest.mark.asyncio
async def test_issue_treats_whitespace_iban_as_no_eligible_account(
    client_a_admin, m7_seed, rent_db,
):
    """A whitespace-only IBAN is not "an IBAN": the account is not eligible,
    and with no other eligible account issuance stops with guidance."""
    blank = uuid.uuid4()
    await _set_org(rent_db, m7_seed["org_a_id"])
    # The used seed account cannot be rewritten (trigger), so it is deactivated
    # (allowed) and a fresh account with a blank IBAN takes its place.
    await rent_db.execute(
        text("UPDATE bank_account SET is_active=false WHERE id=:id"),
        {"id": m7_seed["acct_m7"]},
    )
    await rent_db.execute(
        text("INSERT INTO bank_account (id, organization_id, property_id, account_type, label, iban) "
             "VALUES (:id, :org, :property, 'LAUFEND', 'Blankes Konto', '   ')"),
        {"id": blank, "org": m7_seed["org_a_id"], "property": m7_seed["prop_m7"]},
    )
    await rent_db.commit()
    try:
        created = await client_a_admin.post("/api/dunning/tenant", json=_m7_draft_body(m7_seed))
        assert created.status_code == 201, created.text
        notice_id = created.json()["id"]

        refused = await client_a_admin.post(f"/api/dunning/{notice_id}/issue")
        assert refused.status_code == 409, refused.text
        detail = refused.json()["detail"]
        assert detail["code"] == "dunning_bank_account_missing"
        assert (await client_a_admin.get(f"/api/dunning/{notice_id}")).json()["status"] == "draft"
    finally:
        await _set_org(rent_db, m7_seed["org_a_id"])
        await rent_db.execute(text("DELETE FROM bank_account WHERE id=:id"), {"id": blank})
        await rent_db.execute(
            text("UPDATE bank_account SET is_active=true WHERE id=:id"),
            {"id": m7_seed["acct_m7"]},
        )
        await rent_db.commit()


@pytest.mark.asyncio
async def test_issued_notice_keeps_frozen_destination_after_master_data_changes(
    client_a_admin, m7_seed, rent_db,
):
    """The frozen notice is self-supporting: later Stammdaten changes and even
    deactivating or re-IBANing the destination account must not change the
    issued PDF. The destination is a fresh unused account, so its IBAN may be
    rewritten (the used-account trigger does not apply)."""
    chosen = uuid.uuid4()
    await _set_org(rent_db, m7_seed["org_a_id"])
    await rent_db.execute(
        text("INSERT INTO bank_account (id, organization_id, property_id, account_type, label, iban) "
             "VALUES (:id, :org, :property, 'LAUFEND', 'Zweites Konto', 'DE44120300000000202052')"),
        {"id": chosen, "org": m7_seed["org_a_id"], "property": m7_seed["prop_m7"]},
    )
    await rent_db.commit()

    created = await client_a_admin.post("/api/dunning/tenant", json=_m7_draft_body(m7_seed))
    assert created.status_code == 201, created.text
    notice_id = created.json()["id"]
    # Two eligible accounts: pick the fresh one explicitly.
    issued = await client_a_admin.post(
        f"/api/dunning/{notice_id}/issue", json={"bank_account_id": str(chosen)},
    )
    assert issued.status_code == 200, issued.text
    original = (await client_a_admin.get(f"/api/dunning/{notice_id}/pdf")).content
    assert "DE44120300000000202052" in _pdf_text(original)

    await _set_org(rent_db, m7_seed["org_a_id"])
    await rent_db.execute(
        text("UPDATE bank_account SET iban='DE89370400440532013000', is_active=false "
             "WHERE id=:id"),
        {"id": chosen},
    )
    await rent_db.execute(
        text("UPDATE property SET name='Umbenannt' WHERE id=:id"),
        {"id": m7_seed["prop_m7"]},
    )
    await rent_db.execute(
        text("UPDATE unit SET number='M7-99' WHERE id=:id"),
        {"id": m7_seed["unit_m7"]},
    )
    await rent_db.execute(
        text("UPDATE person SET last_name='Ungenannt' WHERE id=:id"),
        {"id": m7_seed["person_m7"]},
    )
    await rent_db.commit()
    try:
        current = await client_a_admin.get(f"/api/dunning/{notice_id}/pdf")
        assert current.status_code == 200
        assert current.content == original
        text_after = _pdf_text(current.content)
        assert "Mahnhaus" in text_after and "M7-1" in text_after
        assert "DE44120300000000202052" in text_after
        assert "Max Mahner" in text_after
        assert "Umbenannt" not in text_after
        assert "M7-99" not in text_after
        assert "Ungenannt" not in text_after
    finally:
        await _set_org(rent_db, m7_seed["org_a_id"])
        await rent_db.execute(
            text("DELETE FROM bank_account WHERE id=:id"), {"id": chosen},
        )
        await rent_db.execute(
            text("UPDATE property SET name='Mahnhaus' WHERE id=:id"),
            {"id": m7_seed["prop_m7"]},
        )
        await rent_db.execute(
            text("UPDATE unit SET number='M7-1' WHERE id=:id"),
            {"id": m7_seed["unit_m7"]},
        )
        await rent_db.execute(
            text("UPDATE person SET last_name='Mahner' WHERE id=:id"),
            {"id": m7_seed["person_m7"]},
        )
        await rent_db.commit()


@pytest.mark.asyncio
async def test_draft_without_interest_stays_interest_free_after_enabling(client_a_admin, m7_seed):
    import io
    import pdfplumber
    response = await client_a_admin.post('/api/dunning/tenant', json=TestT35Interest.tenant_body(m7_seed))
    assert response.status_code == 201, response.text
    notice = response.json()
    assert notice['interest_amount'] == '0.00'
    assert notice['interest_snapshot'] is None
    path = "/api/dunning/settings"
    assert (await client_a_admin.put(path, json={**DUNNING_DEFAULT_SETTINGS, "default_interest_enabled": True})).status_code == 200
    try:
        assert (await client_a_admin.post(f'/api/dunning/{notice["id"]}/issue')).status_code == 200
        response = await client_a_admin.get(f'/api/dunning/{notice["id"]}/pdf')
        assert response.status_code == 200
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            text = '\n'.join(page.extract_text() for page in pdf.pages)
        assert 'Basiszinssatz' not in text
        assert 'Mahngebühr (Stufe' in text and '0,00 EUR' in text
        assert 'vertraglich vereinbart' not in text
        assert '600,00' in text
        assert (await client_a_admin.get(f'/api/dunning/{notice["id"]}')).json()['interest_snapshot'] is None
    finally:
        assert (await client_a_admin.put(path, json=DUNNING_DEFAULT_SETTINGS)).status_code == 200


@pytest.mark.asyncio
async def test_t35_raw_sql_draft_total_and_provenance_consistency(client_a_admin, m7_seed, app_rw_session, enabled_dunning_interest):
    from sqlalchemy.exc import DBAPIError
    response = await client_a_admin.post('/api/dunning/tenant', json=TestT35Interest.tenant_body(m7_seed))
    assert response.status_code == 201, response.text
    notice = response.json()
    await _set_org(app_rw_session, m7_seed['org_a_id'])
    for sql in ["UPDATE dunning_notice SET total_amount=600 WHERE id=:id",
                "UPDATE dunning_notice SET interest_snapshot=NULL WHERE id=:id"]:
        with pytest.raises(DBAPIError, match='ck_dunning_notice_(total_sum|interest_provenance)'):
            async with app_rw_session.begin_nested():
                await app_rw_session.execute(text(sql), {'id': uuid.UUID(notice['id'])})
    assert await app_rw_session.scalar(text('SELECT interest_amount FROM dunning_notice WHERE id=:id'), {'id': uuid.UUID(notice['id'])}) == Decimal('6.70')


@pytest.mark.asyncio
async def test_t35_aggregated_open_item_overflow_returns_422(client_a_admin, m7_seed, monkeypatch):
    from types import SimpleNamespace
    from app.engine.mietkonto import OpenItem
    from app.services import payment_service
    item = OpenItem(2025, 3, 'KALTMIETE', Decimal('1000000000000.00'), Decimal('0'), Decimal('1000000000000.00'))
    calls = []

    def overflowing_statement(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(open_items=[item])

    monkeypatch.setattr(payment_service, 'account_statement', overflowing_statement)
    response = await client_a_admin.post('/api/dunning/tenant', json=TestT35Interest.tenant_body(m7_seed))
    assert len(calls) == 1, 'Overflow must be injected into the canonical tenant statement'
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_property_state_reaches_normal_tenant_dunning(client_a_admin, m7_seed, enabled_dunning_interest):
    body = TestT35Interest.tenant_body(m7_seed, period_start='2026-01-01', period_end='2026-01-31',
        reference_date='2026-01-07')
    # Nationwide third working day is 6 January; Bavaria excludes Epiphany.
    response = await client_a_admin.post('/api/dunning/tenant', json=body)
    assert response.status_code == 201, response.text
    assert {item['due_date'] for item in response.json()['interest_snapshot']['items']} == {'2026-01-06'}
    original = response.json()
    issued = await client_a_admin.post(f"/api/dunning/{original['id']}/issue")
    assert issued.status_code == 200, issued.text
    frozen_pdf = (await client_a_admin.get(f"/api/dunning/{original['id']}/pdf")).content
    changed = await client_a_admin.put(f"/api/properties/{m7_seed['prop_m7']}", json={'federal_state': 'BY'})
    assert changed.status_code == 200, changed.text
    response = await client_a_admin.post('/api/dunning/tenant', json=body)
    assert response.status_code == 422, response.text
    response = await client_a_admin.post('/api/dunning/tenant', json={**body, 'reference_date':'2026-01-08'})
    assert response.status_code == 201, response.text
    assert {item['due_date'] for item in response.json()['interest_snapshot']['items']} == {'2026-01-07'}
    reloaded = (await client_a_admin.get(f"/api/dunning/{original['id']}")).json()
    assert reloaded['interest_snapshot'] == original['interest_snapshot']
    assert (await client_a_admin.get(f"/api/dunning/{original['id']}/pdf")).content == frozen_pdf


DUNNING_DEFAULT_SETTINGS = {
    "fee_level_1": "0.00", "fee_level_2": "2.50", "fee_level_3": "2.50",
    "default_interest_enabled": False,
}

# Read-only on the GET response; the PUT body forbids it (extra=forbid).
BASE_RATE_COVERED_UNTIL = "base_rate_covered_until"


def _mutable_settings(response_json):
    return {key: value for key, value in response_json.items()
            if key != BASE_RATE_COVERED_UNTIL}


@pytest.mark.asyncio
async def test_dunning_settings_report_base_rate_coverage(client_a_admin):
    response = await client_a_admin.get("/api/dunning/settings")
    assert response.status_code == 200, response.text
    assert response.json()[BASE_RATE_COVERED_UNTIL] == "2026-12-31"


@pytest.mark.asyncio
async def test_organization_dunning_settings_require_a_live_admin(
    client_a_admin, client_a_clerk, client_a_readonly, client_b_admin, seed_data,
):
    path = "/api/dunning/settings"
    assert _mutable_settings((await client_a_readonly.get(path)).json()) == DUNNING_DEFAULT_SETTINGS
    updated = {**DUNNING_DEFAULT_SETTINGS, "fee_level_2": "3.00",
               "fee_level_3": "4.00", "default_interest_enabled": True}
    for client in (client_a_clerk, client_a_readonly):
        assert (await client.put(path, json=updated)).status_code == 403
    assert (await client_a_admin.put(path, json={**updated, "fee_level_1": "-0.01"})).status_code == 422
    assert (await client_a_admin.put(path, json={**updated, "organization_id": str(seed_data["org_b_id"])})).status_code == 422
    try:
        response = await client_a_admin.put(path, json=updated)
        assert response.status_code == 200, response.text
        assert _mutable_settings((await client_a_readonly.get(path)).json()) == updated
        assert _mutable_settings((await client_b_admin.get(path)).json()) == DUNNING_DEFAULT_SETTINGS
    finally:
        await client_a_admin.put(path, json=DUNNING_DEFAULT_SETTINGS)



@pytest_asyncio.fixture
async def enabled_dunning_interest(client_a_admin):
    path = "/api/dunning/settings"
    original = _mutable_settings((await client_a_admin.get(path)).json())
    response = await client_a_admin.put(path, json={**original, "default_interest_enabled": True})
    assert response.status_code == 200, response.text
    try:
        yield
    finally:
        response = await client_a_admin.put(path, json=original)
        assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_0174_downgrade_refuses_default_policy_notice_without_interest(isolated_batch_seed, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    import sqlalchemy
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.exc import DBAPIError
    from tests.migration_rehearsal import project_legacy_advance_modes

    client, seed = isolated_batch_seed
    payload = TestT35Interest.tenant_body(seed)
    del payload["override_fee"]
    draft = await client.post("/api/dunning/tenant", json=payload)
    assert draft.status_code == 201, draft.text
    notice = draft.json()
    assert notice["fee_amount"] == "0.00"
    assert notice["interest_snapshot"] is None
    issued = await client.post(f"/api/dunning/{notice['id']}/issue")
    assert issued.status_code == 200, issued.text

    cfg = Config("alembic.ini")
    cfg.attributes["hv_declaration_test_database"] = True
    engine = sqlalchemy.create_engine(SYNC_URL)
    try:
        with engine.connect() as connection:
            params = {"id": notice["id"], "org": notice["organization_id"]}
            assert connection.scalar(text("SELECT count(*) FROM dunning_settings WHERE organization_id=:org"), params) == 0
            assert connection.scalar(text("""
                SELECT status='issued' AND interest_snapshot IS NULL AND included_items->0->>'_dunning_format'='2'
                FROM dunning_notice WHERE id=:id
            """), params)
            with monkeypatch.context() as patch:
                patch.setattr(sqlalchemy, "engine_from_config",
                              lambda *args, **kwargs: SimpleNamespace(connect=lambda: nullcontext(connection)))
                with connection.begin_nested() as historical:
                    connection.execute(text("LOCK TABLE public.lease_agreement, public.lease_rent_change, "
                                            "public.audit_event IN ACCESS EXCLUSIVE MODE"))
                    # Only this rollback-only historical schema projection omits the new line type.
                    connection.execute(text('ALTER TABLE public.settlement_line DISABLE TRIGGER USER'))
                    connection.execute(text("DELETE FROM public.settlement_line WHERE line_type='RESERVE_SOLL'"))
                    connection.execute(text('ALTER TABLE public.settlement_line ENABLE TRIGGER USER'))
                    command.downgrade(cfg, "0176")
                    connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                    project_legacy_advance_modes(connection)
                    # Historical dunning schemas cannot retain appearance preferences.
                    connection.execute(text("""UPDATE public."user"
                        SET design_theme='klar' WHERE design_theme<>'klar'"""))
                    # Isolate the one default-policy notice from other test evidence.
                    connection.execute(text("CREATE TEMP TABLE default_policy_notice AS SELECT * FROM dunning_notice WHERE id=:id"), params)
                    connection.execute(text("TRUNCATE public.dunning_settings, public.dunning_notice CASCADE"))
                    connection.execute(text("INSERT INTO dunning_notice SELECT * FROM default_policy_notice"))
                    with pytest.raises(DBAPIError, match="Previous releases cannot render retained organization-policy notices"):
                        with connection.begin_nested():
                            command.downgrade(cfg, "0173")
                    historical.rollback()
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0186"
            assert connection.scalar(text("SELECT to_regclass('public.dunning_settings')")) is not None
            assert connection.scalar(text("SELECT count(*) FROM dunning_notice WHERE id=:id"), params) == 1
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_yearless_owner_payout_does_not_create_historical_arrears(
    isolated_batch_seed, rent_db, monkeypatch,
):
    from app.services import dunning_service

    class CurrentDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 5)

    monkeypatch.setattr(dunning_service, "date", CurrentDate)
    client, seed = isolated_batch_seed
    await rent_db.execute(text("""
        INSERT INTO payment (organization_id, bank_account_id, property_id,
            direction, amount, value_date, payment_type, owner_profile_id,
            status, booked_at, booked_by)
        VALUES (:org, :account, :property, 'OUT', 100, '2025-05-15', 'SONDERZAHLUNG',
            :owner, 'booked', '2025-05-15 10:00+00', :actor)
    """), {"org": seed["org_a_id"], "account": seed["acct_m7"],
           "property": seed["prop_m7"], "owner": seed["owner_m7"], "actor": seed["user_a_admin_id"]})
    await rent_db.commit()

    response = await client.post("/api/dunning/owner", json={
        "owner_profile_id": str(seed["owner_m7"]), "period_start": "2025-05-01",
        "period_end": "2025-05-31", "reference_date": "2025-05-31",
    })
    assert response.status_code == 422, response.text
    assert (await client.get("/api/dunning")).json() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("sent, rate", [(False, None), (True, None), (True, "-0.8800")])
async def test_exact_legacy_batch_receipt_replay(isolated_batch_seed, rent_db, rent_world, sent, rate):
    import hashlib
    import json

    from app.models.dunning_batch import DunningBatch

    client, seed = isolated_batch_seed
    created = await client.post("/api/dunning/tenant", json=TestT35Interest.tenant_body(seed))
    assert created.status_code == 201, created.text
    notice = created.json()

    # The pre-upgrade request digest included the raw rate text and whether it
    # was present. Seed the receipt shape independently of the new serializer.
    body = batch_request(
        lease_agreement_ids=[str(seed["lease_m7"])],
        expected_source_digest="a" * 64, request_id=str(uuid.uuid4()),
    )
    old = dict(body, accounting_scope_id=None, override_fee=None,
               base_interest_rate=rate, base_interest_rate_supplied=sent)
    digest = hashlib.sha256(json.dumps(old, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    rent_db.add(DunningBatch(
        organization_id=seed["org_a_id"], actor_user_id=seed["user_a_admin_id"],
        request_id=uuid.UUID(body["request_id"]), request_digest=digest,
        result=[{"lease_agreement_id": str(seed["lease_m7"]), "notice_id": notice["id"]}],
    ))
    await rent_db.commit()

    exact = {**body, "base_interest_rate": rate} if sent else body
    replay = await client.post("/api/dunning/batch", json=exact)
    assert replay.status_code == 201, replay.text
    assert replay.json() == [notice]
    changed = {**body, "base_interest_rate": "0.00"} if not sent else body
    assert (await client.post("/api/dunning/batch", json=changed)).status_code == 409
    if sent and rate is not None:
        assert (await client.post("/api/dunning/batch", json={**body, "base_interest_rate": "-0.88"})).status_code == 409
    for actor in (api_client(rent_world, "clerk"), api_client(rent_world, foreign=True)):
        async with actor:
            assert (await actor.post("/api/dunning/batch", json={**body, "base_interest_rate": rate})).status_code == 422
    fresh = {**body, "request_id": str(uuid.uuid4()), "base_interest_rate": rate}
    assert (await client.post("/api/dunning/batch", json=fresh)).status_code == 422
    assert (await client.post("/api/dunning/batch/preview", json=batch_request(base_interest_rate=rate))).status_code == 422
    assert (await client.get("/api/dunning")).json() == [notice]


@pytest.mark.asyncio
async def test_unscoped_ledger_requires_exact_lease_provenance(isolated_batch_seed, rent_db):
    client, seed = isolated_batch_seed
    unit = await client.post("/api/units", json={
        "property_id": str(seed["prop_m7"]), "number": "M7-2", "usage_type": "WOHNUNG",
    })
    assert unit.status_code == 201, unit.text
    lease = await client.post("/api/leases", json={
        "unit_id": unit.json()["id"], "tenant_profile_id": str(seed["tenant_m7"]),
        "valid_from": "2025-01-01", "base_rent": "550.00", "advance_payment": "50.00",
    })
    assert lease.status_code == 201, lease.text
    other_lease = lease.json()["id"]
    await _set_org(rent_db, seed["org_a_id"])
    legacy_id = await TestDunningImmutability()._insert_issued(rent_db, seed)
    await rent_db.commit()
    legacy_path = f"/api/dunning/{legacy_id}"
    legacy = await client.get(legacy_path)
    assert legacy.status_code == 200, legacy.text
    legacy_pdf = await client.get(f"{legacy_path}/pdf")
    assert legacy_pdf.status_code == 200
    draft = await client.post("/api/dunning/tenant", json=TestT35Interest.tenant_body(seed))
    assert draft.status_code == 201, draft.text
    notice_id = draft.json()["id"]
    issued = await client.post(f"/api/dunning/{notice_id}/issue")
    assert issued.status_code == 200, issued.text

    for lease_id, expected_ids in ((seed["lease_m7"], [notice_id]), (other_lease, [])):
        ledger = await client.get(f"/api/payments/mietkonto/lease/{lease_id}", params={
            "period_start": "2025-03-01", "period_end": "2025-03-31",
        })
        assert ledger.status_code == 200, ledger.text
        assert [row["id"] for row in ledger.json()["dunning_notices"]] == expected_ids
    history = await client.get("/api/dunning", params={"tenant_profile_id": str(seed["tenant_m7"])})
    assert history.status_code == 200, history.text
    assert {row["id"] for row in history.json()} == {notice_id, str(legacy_id)}
    assert (await client.get(legacy_path)).json() == legacy.json()
    assert (await client.get(f"{legacy_path}/pdf")).content == legacy_pdf.content

# ===========================================================================
# Escalation anchors on the currently overdue items (#407)
# ===========================================================================
from tests.test_special_levy import levy_db as levy_db, levy_fixture as levy_fixture  # noqa: E402,F401


async def _escalation_lease(api, w, property_key, number):
    unit = await api.post("/api/units", json={
        "property_id": str(w[property_key]), "number": number, "usage_type": "WOHNUNG"})
    assert unit.status_code == 201, unit.text
    response = await api.post("/api/leases", json={
        "unit_id": unit.json()["id"], "tenant_profile_id": str(w["tenant"]),
        "valid_from": "2026-01-01", "base_rent": "700.00", "advance_payment": "0.00"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _escalation_account(api, w, property_key):
    response = await api.post("/api/bank-accounts", json={
        "property_id": str(w[property_key]), "account_type": "LAUFEND",
        "label": "Mahnkonto", "iban": "DE36302501010002042407"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _tenant_notice(api, w, lease_id, period_start, period_end, reference_date):
    response = await api.post("/api/dunning/tenant", json={
        "tenant_profile_id": str(w["tenant"]), "lease_agreement_id": str(lease_id),
        "period_start": period_start, "period_end": period_end, "reference_date": reference_date})
    assert response.status_code == 201, response.text
    return response.json()


async def _issue_notice(api, notice_id):
    response = await api.post(f"/api/dunning/{notice_id}/issue")
    assert response.status_code == 200, response.text


async def _pay_rent(api, w, lease_id, account_id, period_month, value_date):
    response = await api.post("/api/payments", json={
        "bank_account_id": str(account_id), "direction": "IN", "amount": "700.00",
        "value_date": value_date, "payment_type": "KALTMIETE",
        "tenant_profile_id": str(w["tenant"]), "lease_agreement_id": str(lease_id),
        "period_year": 2026, "period_month": period_month})
    assert response.status_code == 201, response.text
    response = await api.post(f"/api/payments/{response.json()['id']}/book")
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_paid_earlier_arrears_do_not_raise_new_notice_level(rent_world):
    w = rent_world
    async with api_client(w) as api:
        lease = await _escalation_lease(api, w, "hidden_property", "Esc 1")
        account = await _escalation_account(api, w, "hidden_property")
        first = await _tenant_notice(api, w, lease, "2026-01-01", "2026-01-31", "2026-02-20")
        assert (first["level"], first["fee_amount"], first["total_amount"]) == (1, "0.00", "700.00")
        await _issue_notice(api, first["id"])
        await _pay_rent(api, w, lease, account, 1, "2026-02-21")
        second = await _tenant_notice(api, w, lease, "2026-03-01", "2026-03-31", "2026-04-20")
        assert (second["level"], second["fee_amount"], second["total_amount"]) == (1, "0.00", "700.00")


@pytest.mark.asyncio
async def test_notice_for_second_lease_starts_at_level_one(rent_world):
    w = rent_world
    async with api_client(w) as api:
        first_lease = await _escalation_lease(api, w, "property", "Esc 1")
        second_lease = await _escalation_lease(api, w, "property", "Esc 2")
        await _escalation_account(api, w, "property")
        first = await _tenant_notice(api, w, first_lease, "2026-01-01", "2026-01-31", "2026-02-20")
        assert first["level"] == 1
        await _issue_notice(api, first["id"])
        second = await _tenant_notice(api, w, second_lease, "2026-01-01", "2026-01-31", "2026-02-20")
        assert (second["level"], second["fee_amount"], second["total_amount"]) == (1, "0.00", "700.00")


@pytest.mark.asyncio
async def test_same_arrears_escalate_levels_one_two_three(rent_world):
    w = rent_world
    async with api_client(w) as api:
        lease = await _escalation_lease(api, w, "hidden_property", "Esc 1")
        account = await _escalation_account(api, w, "hidden_property")
        first = await _tenant_notice(api, w, lease, "2026-03-01", "2026-03-31", "2026-04-20")
        assert (first["level"], first["fee_amount"]) == (1, "0.00")
        await _issue_notice(api, first["id"])
        second = await _tenant_notice(api, w, lease, "2026-03-01", "2026-03-31", "2026-05-05")
        assert (second["level"], second["fee_amount"], second["total_amount"]) == (2, "2.50", "702.50")
        await _issue_notice(api, second["id"])
        third = await _tenant_notice(api, w, lease, "2026-03-01", "2026-03-31", "2026-05-20")
        assert (third["level"], third["fee_amount"], third["total_amount"]) == (3, "2.50", "702.50")


@pytest.mark.asyncio
async def test_paid_levy_notice_does_not_raise_later_levy_notice_level(levy_db, levy_fixture):
    from tests.test_special_levy import client, create
    ids = levy_fixture
    await levy_db.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) "
        "VALUES (:org,:scope,:actor,ARRAY['read','prepare','approve','confirm','export'],:actor,'Levy escalation lifecycle test')"), ids)
    await levy_db.commit()
    async with client(ids, "actor") as api:
        await create(api, ids, due_date="2026-06-15")

        def body(period_start, period_end, reference_date):
            return {"owner_profile_id": str(ids["owners"][0]), "period_start": period_start,
                    "period_end": period_end, "reference_date": reference_date,
                    "accounting_scope_id": str(ids["scope"])}

        first = await api.post("/api/dunning/owner", json=body("2026-06-01", "2026-06-30", "2026-06-16"))
        assert first.status_code == 201, first.text
        first = first.json()
        assert (first["level"], first["fee_amount"], first["total_amount"]) == (1, "0.00", "3000.00")
        issued = await api.post(f"/api/dunning/{first['id']}/issue")
        assert issued.status_code == 200, issued.text
        payment = await api.post("/api/payments", json={
            "bank_account_id": str(ids["bank"]), "direction": "IN", "amount": "3000.00",
            "value_date": "2026-06-20", "payment_type": "SONDERZAHLUNG",
            "owner_profile_id": str(ids["owners"][0]), "period_year": 2026, "period_month": 6})
        assert payment.status_code == 201, payment.text
        assert (await api.post(f"/api/payments/{payment.json()['id']}/book")).status_code == 200
        await create(api, ids, due_date="2026-09-15")
        second = await api.post("/api/dunning/owner", json=body("2026-09-01", "2026-09-30", "2026-09-16"))
        assert second.status_code == 201, second.text
        second = second.json()
        assert (second["level"], second["fee_amount"], second["total_amount"]) == (1, "0.00", "3000.00")


@pytest.mark.asyncio
async def test_notice_without_lease_link_still_escalates_the_same_arrears(isolated_batch_seed, rent_db):
    client, seed = isolated_batch_seed
    await _set_org(rent_db, seed["org_a_id"])
    await TestDunningImmutability()._insert_issued(rent_db, seed)
    await rent_db.commit()
    body = TestT35Interest.tenant_body(seed)
    del body["override_fee"]
    response = await client.post("/api/dunning/tenant", json=body)
    assert response.status_code == 201, response.text
    notice = response.json()
    assert (notice["level"], notice["fee_amount"]) == (2, "2.50")


@pytest.mark.asyncio
async def test_notice_without_lease_link_does_not_escalate_other_months_of_another_lease(
        isolated_batch_seed, rent_db):
    client, seed = isolated_batch_seed
    unit = await client.post("/api/units", json={
        "property_id": str(seed["prop_m7"]), "number": "M7-3", "usage_type": "WOHNUNG"})
    assert unit.status_code == 201, unit.text
    lease = await client.post("/api/leases", json={
        "unit_id": unit.json()["id"], "tenant_profile_id": str(seed["tenant_m7"]),
        "valid_from": "2025-01-01", "base_rent": "550.00", "advance_payment": "50.00"})
    assert lease.status_code == 201, lease.text
    await _set_org(rent_db, seed["org_a_id"])
    await TestDunningImmutability()._insert_issued(rent_db, seed)
    await rent_db.commit()
    body = TestT35Interest.tenant_body(
        seed, lease_agreement_id=lease.json()["id"],
        period_start="2025-04-01", period_end="2025-04-30", reference_date="2025-05-31")
    del body["override_fee"]
    response = await client.post("/api/dunning/tenant", json=body)
    assert response.status_code == 201, response.text
    notice = response.json()
    assert (notice["level"], notice["fee_amount"]) == (1, "0.00")
