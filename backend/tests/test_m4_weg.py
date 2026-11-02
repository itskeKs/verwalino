"""M4 WEG tests -- persistence, RLS, immutability, atomic finalize, and the M4
exit (Wirtschaftsplan + WEG-Jahresabrechnung with hand-checked Abrechnungsspitze
+ Vermoegensbericht, all as PDF; amounts consistent).

Uses the same fixtures as M0-M3. The engine math itself is proven in
tests/engine/ (Golden + property-based); here we prove the DB invariants and the
full create->compute->finalize->PDF path through the real service + API.
"""

from __future__ import annotations

import os
import psycopg2
import uuid
from datetime import date
from decimal import Decimal

from tests.financial_coverage_helpers import attest_fixture, finalize_weg_fixture, weg_values, reserve_evidence
import pytest
from sqlalchemy import text

SYNC_URL = os.environ.get(
    "HV_DATABASE_URL_SYNC",
    "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
)


# ===========================================================================
# Seed: a WEG (org_a) -- 2 units MEA 600/400, two owners; cost entries from
# three spheres (umlagefaehig / nicht umlagefaehig / Erhaltungsruecklage).
# Mirrors engine Golden 2/3 so the Abrechnungsspitze is hand-checkable.
# ===========================================================================
def _seed_weg(seed_data):
    import psycopg2

    org = str(seed_data["org_a_id"])
    ids = {
        "property": uuid.uuid4(),
        "u1": uuid.uuid4(),
        "u2": uuid.uuid4(),
        "person_a": uuid.uuid4(),
        "person_b": uuid.uuid4(),
        "owner_a": uuid.uuid4(),
        "owner_b": uuid.uuid4(),
        "bank_giro": uuid.uuid4(),
        "bank_ruecklage": uuid.uuid4(),
    }

    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()

        def cat(code):
            cur.execute("SELECT id FROM cost_category WHERE code=%s AND plan_version=1", (code,))
            return cur.fetchone()[0]

        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
            (str(ids["property"]), org, "WEG Hauptstrasse 5", "WEG"),
        )
        # Units: U1 MEA 600, U2 MEA 400.
        for uid, num, mea in [(ids["u1"], "1", 600), (ids["u2"], "2", 400)]:
            cur.execute(
                "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, heated_area, mea) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (str(uid), org, str(ids["property"]), num, "WOHNUNG", mea, mea, mea),
            )
        # Two owners, one per unit, whole period.
        for pid, oid, first, uid in [
            (ids["person_a"], ids["owner_a"], "Anton", ids["u1"]),
            (ids["person_b"], ids["owner_b"], "Berta", ids["u2"]),
        ]:
            cur.execute(
                "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
                (str(pid), org, first, "Eigentuemer"),
            )
            cur.execute(
                "INSERT INTO owner_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
                (str(oid), org, str(pid)),
            )
            cur.execute(
                "INSERT INTO ownership_history (id, organization_id, unit_id, owner_profile_id, valid_from, mea_share) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (str(uuid.uuid4()), org, str(uid), str(oid), date(2000, 1, 1), None),
            )

        # Bank accounts (model anchor; manual balances captured at report time).
        for bid, atype, label in [
            (ids["bank_giro"], "LAUFEND", "Girokonto"),
            (ids["bank_ruecklage"], "RUECKLAGE", "Ruecklagenkonto"),
        ]:
            cur.execute(
                "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
                "VALUES (%s,%s,%s,%s,%s)",
                (str(bid), org, str(ids["property"]), atype, label),
            )

        # Cost entries (active chain) for 2025. Owner sphere reads
        # owner_distribution_key (here MEA for all). Three spheres:
        #   Gartenpflege 4000 UMLAGEFAEHIG ; Verwalter 3000 NICHT_UMLAGEFAEHIG ;
        #   Ruecklagen-Zufuehrung 6000 (ERHALTUNGSRUECKLAGE, reserve_relevant).
        def add_cost(code, gross, owner_key="MEA"):
            ce = uuid.uuid4()
            cur.execute(
                "INSERT INTO cost_entry (id, organization_id, property_id, cost_category_id, cost_category_plan_version, "
                "invoice_date, service_period_from, service_period_to, gross_amount, owner_distribution_key, "
                "tenant_distribution_key, tenant_apportionable, status, source, confirmed_at, confirmed_by) "
                "VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,'confirmed','MANUAL',now(),%s)",
                (
                    str(ce), org, str(ids["property"]), str(cat(code)),
                    date(2025, 6, 1), date(2025, 1, 1), date(2025, 12, 31),
                    gross, owner_key, "MEA", "NICHT_UMLAGEFAEHIG",
                    str(seed_data["user_a_admin_id"]),
                ),
            )
            return ce

        ids["ce_garten"] = add_cost("GARTENPFLEGE", Decimal("4000.00"))
        ids["ce_verwalter"] = add_cost("WEG_VERWALTERKOSTEN", Decimal("3000.00"))
        ids["ce_ruecklage"] = add_cost("ERHALTUNGSRUECKLAGE", Decimal("6000.00"))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {**seed_data, **{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in ids.items()}}


@pytest.fixture
def m4_seed(seed_data):
    return _seed_weg(seed_data)


@pytest.fixture
def m4_withdrawal_seed(m4_seed):
    # A reserve-classified receipt covers 8,000 without changing owner costs.
    receipt = previous_cost({**m4_seed, 'ce_garten': m4_seed['ce_ruecklage']}, '8000.00')
    return {**m4_seed, 'ce_garten': str(receipt)}


async def _create_and_finalize_plan(client, m4_seed, reserve="6000.00"):
    """Create + finalize a Wirtschaftsplan with the two owners' MEA shares."""
    resp = await client.post("/api/economic-plans", json={
        "property_id": m4_seed["property"],
        "plan_year": 2025,
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
    })
    assert resp.status_code == 201, resp.text
    plan_id = resp.json()["id"]
    # Planned: Gartenpflege 4000 + Verwalter 3000 = 7000 cost; reserve 6000.
    body = {
        "planned_costs": [
            {"category_code": "GARTENPFLEGE", "label": "Gartenpflege", "planned_amount": "4000.00", "cost_group": "UMLAGEFAEHIG"},
            {"category_code": "WEG_VERWALTERKOSTEN", "label": "Verwalter", "planned_amount": "3000.00", "cost_group": "NICHT_UMLAGEFAEHIG"},
        ],
        "reserve_contribution_total": reserve,
    }
    await attest_fixture(client, "economic_plan", plan_id, body)
    resp = await client.post(f"/api/economic-plans/{plan_id}/finalize", json=body)
    assert resp.status_code == 200, resp.text
    return plan_id


@pytest.mark.asyncio
@pytest.mark.parametrize("route, table", [
    ("economic-plans", "economic_plan"),
    ("weg-settlements/asset-reports", "settlement_run"),
    ("weg-settlements", "settlement_run"),
])
@pytest.mark.parametrize("field", ["property_id", "management_mandate_id"])
async def test_foreign_and_unknown_parents_create_no_financial_draft(
    client_a_admin, client_b_admin, m4_seed, migrator_session, field, route, table,
):
    body = {"property_id": m4_seed["property"], "plan_year": 2025,
            "period_start": "2025-01-01", "period_end": "2025-12-31"}
    if route != "economic-plans":
        body.pop("plan_year")
    client = client_b_admin if field == "property_id" else client_a_admin
    foreign = m4_seed["property"] if field == "property_id" else m4_seed["mandate_b_id"]
    if route == "weg-settlements":
        plan = await client_a_admin.post("/api/economic-plans", json={
            "property_id": m4_seed["property"], "plan_year": 2025,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert plan.status_code == 201, plan.text
        body["economic_plan_id"] = plan.json()["id"]
        client = client_a_admin
        if field == "property_id":
            property_response = await client_b_admin.post("/api/properties", json={
                "name": "Foreign WEG parent", "management_type": "WEG",
            })
            assert property_response.status_code == 201, property_response.text
            foreign = property_response.json()["id"]
    before = await migrator_session.scalar(text(f"SELECT count(*) FROM {table}"))
    responses = []
    expected_status = 422 if route == "weg-settlements" and field == "property_id" else 404
    for parent in (foreign, uuid.uuid4()):
        response = await client.post(f"/api/{route}", json=body | {field: str(parent)})
        assert response.status_code == expected_status, response.text
        responses.append(response.json())
    assert responses[0] == responses[1]
    assert await migrator_session.scalar(text(f"SELECT count(*) FROM {table}")) == before


# T54: prior-year actuals are a proposal, never a computation or finalization.
@pytest.fixture
def previous_world(seed_data):
    return _seed_weg(seed_data)


def previous_request(world, **changes):
    return {"property_id": world["property"], "plan_year": 2026, **changes}


async def previous_preview(api, world, **changes):
    response = await api.post("/api/economic-plans/previous/preview", json=previous_request(world, **changes))
    assert response.status_code == 200, response.text
    return response.json()


def previous_adoption(world, preview, **changes):
    return {**previous_request(world), "expected_source_digest": preview["source_digest"],
            "planned_costs": preview["planned_costs"],
            "reserve_contribution_total": preview["reserve_contribution_total"], **changes}


@pytest.mark.asyncio
async def test_previous_preview_exact_categories_reserve_and_no_writes(client_a_admin, previous_world):
    api, w = client_a_admin, previous_world
    before = (await api.get("/api/economic-plans")).json()
    sources = (await api.get("/api/cost-entries")).json()
    preview = await previous_preview(api, w)
    assert preview["source_year"] == 2025 and preview["plan_year"] == 2026
    assert preview["period_start"] == "2025-01-01" and preview["period_end"] == "2025-12-31"
    rows = {r["category_code"]: r for r in preview["planned_costs"]}
    assert set(rows) == {"GARTENPFLEGE", "WEG_VERWALTERKOSTEN"}
    assert Decimal(rows["GARTENPFLEGE"]["planned_amount"]) == Decimal("4000.00")
    assert rows["GARTENPFLEGE"]["cost_group"] == "UMLAGEFAEHIG"
    assert Decimal(rows["WEG_VERWALTERKOSTEN"]["planned_amount"]) == Decimal("3000.00")
    assert rows["WEG_VERWALTERKOSTEN"]["cost_group"] == "NICHT_UMLAGEFAEHIG"
    assert all(r["category_id"] and r["distribution_key"] == "MEA" for r in rows.values())
    assert Decimal(preview["reserve_contribution_total"]) == Decimal("6000.00")
    assert len(preview["source_digest"]) == 64
    assert (await api.get("/api/economic-plans")).json() == before
    assert (await api.get("/api/cost-entries")).json() == sources


@pytest.mark.asyncio
async def test_previous_create_retains_editable_draft_inputs_and_reload(client_a_admin, previous_world):
    api, w = client_a_admin, previous_world
    preview = await previous_preview(api, w)
    body = previous_adoption(w, preview)
    body["planned_costs"][0]["planned_amount"] = "4123.45"
    body["reserve_contribution_total"] = "6500.00"
    created = await api.post("/api/economic-plans/previous", json=body)
    assert created.status_code == 201, created.text
    draft = created.json()
    assert draft["status"] == "draft" and draft["snapshot_sha256"] is None
    assert draft["plan_year"] == 2026 and draft["period_start"] == "2026-01-01"
    assert draft["period_end"] == "2026-12-31" and not draft["lines"]
    assert draft["draft_inputs"]["planned_costs"] == body["planned_costs"]
    assert Decimal(draft["draft_inputs"]["reserve_contribution_total"]) == Decimal("6500.00")
    assert (await api.get(f"/api/economic-plans/{draft['id']}")).json() == draft
    assert (await api.get(f"/api/economic-plans/{draft['id']}/pdf")).status_code == 409


@pytest.mark.asyncio
async def test_previous_stale_basis_creates_no_draft(client_a_admin, previous_world):
    api, w = client_a_admin, previous_world
    preview = await previous_preview(api, w)
    before = (await api.get("/api/economic-plans")).json()
    response = await api.post("/api/economic-plans/previous", json=previous_adoption(
        w, preview, expected_source_digest="0" * 64))
    assert response.status_code == 409, response.text
    assert (await api.get("/api/economic-plans")).json() == before


@pytest.mark.asyncio
async def test_previous_foreign_property_is_not_disclosed(client_b_admin, previous_world):
    response = await client_b_admin.post("/api/economic-plans/previous/preview", json=previous_request(previous_world))
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_previous_readonly_cannot_prepare_or_adopt(client_a_readonly, previous_world):
    body = previous_request(previous_world)
    assert (await client_a_readonly.post("/api/economic-plans/previous/preview", json=body)).status_code == 403
    body.update(expected_source_digest="0" * 64, planned_costs=[], reserve_contribution_total="0.00")
    assert (await client_a_readonly.post("/api/economic-plans/previous", json=body)).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("year", [0, 1, 10000, 2026.5, "2026"])
async def test_previous_invalid_target_year_rejected(client_a_admin, previous_world, year):
    response = await client_a_admin.post("/api/economic-plans/previous/preview", json=previous_request(previous_world, plan_year=year))
    assert response.status_code == 422, response.text


def previous_cost(world, amount, *, status="confirmed", start="2025-01-01", end="2025-12-31", invoice="2025-06-01", category=None):
    import psycopg2
    key = uuid.uuid4()
    with psycopg2.connect(SYNC_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""INSERT INTO cost_entry(id,organization_id,property_id,cost_category_id,
                cost_category_plan_version,invoice_date,service_period_from,service_period_to,gross_amount,
                owner_distribution_key,tenant_distribution_key,tenant_apportionable,status,source,confirmed_at,confirmed_by)
                SELECT %s,organization_id,property_id,COALESCE(%s::uuid,cost_category_id),cost_category_plan_version,%s,%s,%s,%s,
                'MEA','MEA','NICHT_UMLAGEFAEHIG',%s,'MANUAL',
                CASE WHEN %s='confirmed' THEN now() END,CASE WHEN %s='confirmed' THEN confirmed_by END
                FROM cost_entry WHERE id=%s""", (str(key), str(category) if category else None, invoice, start, end, Decimal(amount), status, status, status, world["ce_garten"]))
    return key


@pytest.mark.asyncio
async def test_previous_current_signed_and_period_prorated_sources(client_a_admin, previous_world):
    import psycopg2
    w = previous_world
    previous_cost(w, "-20.05")
    previous_cost(w, "365.00", start="2024-07-01", end="2025-06-30", invoice="2026-06-01")
    old = previous_cost(w, "111.11")
    replacement = previous_cost(w, "222.22")
    previous_cost(w, "999.00", status="needs_review")
    previous_cost(w, "777.00", start="2024-01-01", end="2024-12-31")
    previous_cost(w, "888.00", start="2026-01-01", end="2026-12-31")
    with psycopg2.connect(SYNC_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE cost_entry SET superseded_by_id=%s WHERE id=%s", (str(replacement), str(old)))
    preview = await previous_preview(client_a_admin, w)
    rows = {r["category_code"]: r for r in preview["planned_costs"]}
    assert Decimal(rows["GARTENPFLEGE"]["planned_amount"]) == Decimal("4383.17")
    assert Decimal(rows["WEG_VERWALTERKOSTEN"]["planned_amount"]) == Decimal("3000.00")
    assert Decimal(preview["reserve_contribution_total"]) == Decimal("6000.00")


@pytest.mark.asyncio
async def test_previous_actual_source_change_invalidates_digest(client_a_admin, previous_world):
    api, w = client_a_admin, previous_world
    old = await previous_preview(api, w)
    before = (await api.get("/api/economic-plans")).json()
    previous_cost(w, "1.00")
    response = await api.post("/api/economic-plans/previous", json=previous_adoption(w, old))
    assert response.status_code == 409, response.text
    assert (await api.get("/api/economic-plans")).json() == before
    fresh = await previous_preview(api, w)
    assert fresh["source_digest"] != old["source_digest"]
    assert Decimal(next(r for r in fresh["planned_costs"] if r["category_code"] == "GARTENPFLEGE")["planned_amount"]) == Decimal("4001.00")


@pytest.mark.asyncio
@pytest.mark.parametrize("amount,expected", [("-4000.00", 200), ("-5000.00", 422)])
async def test_previous_zero_is_preserved_negative_net_is_not_clipped(client_a_admin, previous_world, amount, expected):
    previous_cost(previous_world, amount)
    response = await client_a_admin.post("/api/economic-plans/previous/preview", json=previous_request(previous_world))
    assert response.status_code == expected, response.text
    if expected == 200:
        row = next(row for row in response.json()["planned_costs"] if row["category_code"] == "GARTENPFLEGE")
        assert Decimal(row["planned_amount"]) == Decimal("0.00")


@pytest.mark.asyncio
async def test_previous_empty_source_does_not_manufacture_zero_plan(client_a_admin, previous_world):
    before = (await client_a_admin.get("/api/economic-plans")).json()
    response = await client_a_admin.post("/api/economic-plans/previous/preview", json=previous_request(previous_world, plan_year=2028))
    assert response.status_code == 422, response.text
    assert (await client_a_admin.get("/api/economic-plans")).json() == before


@pytest.mark.asyncio
async def test_previous_late_failure_rolls_back_new_draft(client_a_admin, previous_world, monkeypatch):
    from fastapi import HTTPException
    from app.repositories.audit_repo import AuditRepository
    api, w = client_a_admin, previous_world
    preview = await previous_preview(api, w)
    before = (await api.get("/api/economic-plans")).json()
    original = AuditRepository.create
    calls = []

    async def fail_after_insert(self, event):
        if event.entity_type == "economic_plan":
            calls.append(event.entity_id)
            raise HTTPException(422, "Synthetic late plan audit failure")
        return await original(self, event)

    monkeypatch.setattr(AuditRepository, "create", fail_after_insert)
    response = await api.post("/api/economic-plans/previous", json=previous_adoption(w, preview))
    assert response.status_code == 422 and len(calls) == 1, response.text
    assert (await api.get("/api/economic-plans")).json() == before


@pytest.mark.asyncio
async def test_previous_edited_values_compute_without_changing_final_original(client_a_admin, previous_world):
    api, w = client_a_admin, previous_world
    old_id = await _create_and_finalize_plan(api, w)
    original = (await api.get(f"/api/economic-plans/{old_id}")).json()
    original_pdf = await api.get(f"/api/economic-plans/{old_id}/pdf")
    assert original_pdf.status_code == 200 and original_pdf.content.startswith(b"%PDF")
    preview = await previous_preview(api, w)
    adopted = previous_adoption(w, preview)
    next(row for row in adopted["planned_costs"] if row["category_code"] == "GARTENPFLEGE")["planned_amount"] = "4100.25"
    adopted["reserve_contribution_total"] = "6500.00"
    response = await api.post("/api/economic-plans/previous", json=adopted)
    assert response.status_code == 201, response.text
    draft = response.json()
    inputs = draft["draft_inputs"]
    assert (await api.post(f"/api/economic-plans/{draft['id']}/compute", json=inputs)).status_code == 422
    await attest_fixture(api, "economic_plan", draft["id"], inputs)
    computed = await api.post(f"/api/economic-plans/{draft['id']}/compute", json=inputs)
    assert computed.status_code == 200, computed.text
    owner_lines = [line for line in computed.json()["lines"] if line["line_type"] == "OWNER_ADVANCE"]
    assert sorted(Decimal(line["planned_amount"]) for line in owner_lines) == [Decimal("5440.10"), Decimal("8160.15")]
    assert (await api.get(f"/api/economic-plans/{old_id}")).json() == original
    assert (await api.get(f"/api/economic-plans/{old_id}/pdf")).content == original_pdf.content


from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: E402,F401


def previous_plan_count(ids):
    import psycopg2
    with psycopg2.connect(SYNC_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM economic_plan WHERE organization_id=%s AND property_id=%s",
                           (str(ids["org"]), str(ids["property"])))
            return cursor.fetchone()[0]


async def previous_scoped_fixture(session, seed):
    from sqlalchemy import text
    from tests.test_property_year_report import fixture, cost, ready
    from tests.test_scoped_account_statements import mutate
    ids = await fixture(session, seed, management_type="WEG", scope_type="WEG",
                        start="2025-01-01", end="2025-12-31")
    await cost(session, ids, "100.00")
    await cost(session, ids, "25.00")
    await ready(session, ids)
    ids["target"] = uuid.uuid4()
    await mutate(text("""INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,
        label,valid_from,valid_to,whole_property,origin,created_by)
        VALUES (:target,:org,:property,'WEG','Next year plan','2026-01-01','2026-12-31',true,'REVIEWED',:actor)"""), ids)
    await mutate(text("""INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason)
        VALUES (:org,:target,:clerk,ARRAY['read','prepare'],:actor,'Explicit next-year plan preparation')"""), ids)
    return ids


def previous_scoped_request(ids):
    return {"property_id": str(ids["property"]), "plan_year": 2026,
            "source_accounting_scope_id": str(ids["scope"]), "accounting_scope_id": str(ids["target"])}


@pytest.mark.asyncio
async def test_previous_independent_read_and_prepare_year_scopes(migrator_session, scope_seed):
    from tests.test_property_year_report import client
    ids = await previous_scoped_fixture(migrator_session, scope_seed)
    body = previous_scoped_request(ids)
    async with client(ids) as api:
        response = await api.post("/api/economic-plans/previous/preview", json=body)
        assert response.status_code == 200, response.text
        preview = response.json()
        assert len(preview["planned_costs"]) == 1
        assert Decimal(preview["planned_costs"][0]["planned_amount"]) == Decimal("125.00")
        response = await api.post("/api/economic-plans/previous", json={**body,
            "expected_source_digest": preview["source_digest"], "planned_costs": preview["planned_costs"],
            "reserve_contribution_total": "0.00"})
        assert response.status_code == 201, response.text
        assert response.json()["accounting_scope_id"] == str(ids["target"])
        assert (await api.get(f"/api/economic-plans/{response.json()['id']}")).json() == response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked", ["scope", "target"])
async def test_previous_revoked_year_authority_creates_nothing(migrator_session, scope_seed, revoked):
    from sqlalchemy import text
    from tests.test_property_year_report import client
    from tests.test_scoped_account_statements import mutate
    ids = await previous_scoped_fixture(migrator_session, scope_seed)
    body = previous_scoped_request(ids)
    async with client(ids) as api:
        projected = await api.post("/api/economic-plans/previous/preview", json=body)
        assert projected.status_code == 200, projected.text
        preview = projected.json()
    await mutate(text("""UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor
        WHERE organization_id=:org AND scope_id=:revoked AND user_id=:clerk AND revoked_at IS NULL"""),
        {**ids, "revoked": ids[revoked]})
    async with client(ids) as api:
        response = await api.post("/api/economic-plans/previous", json={**body,
            "expected_source_digest": preview["source_digest"], "planned_costs": preview["planned_costs"],
            "reserve_contribution_total": "0.00"})
        assert response.status_code == 404, response.text
    async with client(ids, "actor") as api:
        # Admin has no implicit source grant either; use the actual table only in the test fixture below.
        assert (await api.post("/api/economic-plans/previous/preview", json=body)).status_code == 404
    assert previous_plan_count(ids) == 0


@pytest.mark.asyncio
async def test_previous_waits_for_financial_lock(client_a_admin, previous_world, migrator_session):
    import asyncio
    from sqlalchemy import text
    session, w = migrator_session, previous_world
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                          {"key": f"financial-inputs:{w['org_a_id']}"})
    blocker = await session.scalar(text("SELECT pg_backend_pid()"))
    task = asyncio.create_task(client_a_admin.post("/api/economic-plans/previous/preview", json=previous_request(w)))
    waiting = False
    try:
        for _ in range(200):
            waiting = await session.scalar(text("""SELECT EXISTS(SELECT 1 FROM pg_locks l
                WHERE l.locktype='advisory' AND NOT l.granted
                AND :blocker=ANY(pg_blocking_pids(l.pid)))"""), {"blocker": blocker})
            if waiting or task.done():
                break
            await asyncio.sleep(0.01)
    finally:
        await session.rollback()
    response = await asyncio.wait_for(task, timeout=15)
    assert waiting, "Prior-year proposal must wait for the existing financial-input boundary"
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_previous_parallel_previews_are_equal_and_nonwriting(client_a_admin, previous_world):
    import asyncio
    before = (await client_a_admin.get("/api/economic-plans")).json()
    responses = await asyncio.gather(*(client_a_admin.post("/api/economic-plans/previous/preview",
        json=previous_request(previous_world)) for _ in range(2)))
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert responses[0].json() == responses[1].json()
    assert (await client_a_admin.get("/api/economic-plans")).json() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", ["-1.00", "NaN", "1.001", "1000000000000.00"])
async def test_previous_invalid_edited_amount_rolls_back(client_a_admin, previous_world, amount):
    api, w = client_a_admin, previous_world
    preview = await previous_preview(api, w)
    body = previous_adoption(w, preview)
    body["planned_costs"][0]["planned_amount"] = amount
    before = (await api.get("/api/economic-plans")).json()
    response = await api.post("/api/economic-plans/previous", json=body)
    assert response.status_code == 422, response.text
    assert (await api.get("/api/economic-plans")).json() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("category_id", "00000000-0000-0000-0000-000000000001"),
    ("category_code", "GRUNDSTEUER"), ("cost_group", "NICHT_UMLAGEFAEHIG"), ("distribution_key", "AREA")])
async def test_previous_forged_category_metadata_cannot_be_adopted(client_a_admin, previous_world, field, value):
    api, w = client_a_admin, previous_world
    projected = await previous_preview(api, w)
    body = previous_adoption(w, projected)
    garden = next(row for row in body["planned_costs"] if row["category_code"] == "GARTENPFLEGE")
    garden[field] = value
    before = (await api.get("/api/economic-plans")).json()
    response = await api.post("/api/economic-plans/previous", json=body)
    assert response.status_code == 422, response.text
    assert (await api.get("/api/economic-plans")).json() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["drop", "duplicate"])
async def test_previous_incomplete_or_duplicate_categories_not_silently_changed(client_a_admin, previous_world, change):
    api, w = client_a_admin, previous_world
    projected = await previous_preview(api, w)
    body = previous_adoption(w, projected)
    if change == "drop":
        body["planned_costs"].pop()
    else:
        body["planned_costs"].append(dict(body["planned_costs"][0]))
    before = (await api.get("/api/economic-plans")).json()
    response = await api.post("/api/economic-plans/previous", json=body)
    assert response.status_code == 422, response.text
    assert (await api.get("/api/economic-plans")).json() == before


@pytest.mark.asyncio
async def test_previous_live_role_downgrade_blocks_stored_preview(migrator_session, scope_seed):
    from sqlalchemy import text
    from tests.test_property_year_report import client
    from tests.test_scoped_account_statements import mutate
    ids = await previous_scoped_fixture(migrator_session, scope_seed)
    body = previous_scoped_request(ids)
    async with client(ids) as api:
        response = await api.post("/api/economic-plans/previous/preview", json=body)
        assert response.status_code == 200, response.text
        projected = response.json()
        await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"), ids)
        assert (await api.post("/api/economic-plans/previous/preview", json=body)).status_code == 403
        response = await api.post("/api/economic-plans/previous", json={**body,
            "expected_source_digest": projected["source_digest"], "planned_costs": projected["planned_costs"],
            "reserve_contribution_total": "0.00"})
        assert response.status_code == 403, response.text
    assert previous_plan_count(ids) == 0


@pytest.mark.asyncio
async def test_previous_inactive_category_fails_whole_proposal(client_a_admin, previous_world):
    import psycopg2
    category = uuid.uuid4()
    with psycopg2.connect(SYNC_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""INSERT INTO cost_category(id,plan_version,code,label,cost_group,
                tenant_apportionability_default,owner_billing_relevant_default,
                owner_distribution_key_default,tenant_distribution_key_default,reserve_relevant,is_active)
                VALUES (%s,1,%s,'T54 inactive category','UMLAGEFAEHIG','UMLAGEFAEHIG',true,'MEA','MEA',false,false)""",
                (str(category), "T54_INACTIVE_" + uuid.uuid4().hex[:12]))
    previous_cost(previous_world, "100.00", category=category)
    before = (await client_a_admin.get("/api/economic-plans")).json()
    response = await client_a_admin.post("/api/economic-plans/previous/preview", json=previous_request(previous_world))
    assert response.status_code == 422, response.text
    assert (await client_a_admin.get("/api/economic-plans")).json() == before


# ===========================================================================
# RLS fail-closed + cross-org isolation for economic_plan / economic_plan_line
# ===========================================================================
class TestM4RLSFailClosed:
    @pytest.mark.asyncio
    async def test_no_context_zero_plans(self, app_rw_session):
        await app_rw_session.execute(text("SELECT set_config('app.current_org_id', '', true)"))
        result = await app_rw_session.execute(text("SELECT * FROM economic_plan"))
        assert len(result.fetchall()) == 0

    @pytest.mark.asyncio
    async def test_no_context_zero_plan_lines(self, app_rw_session):
        await app_rw_session.execute(text("SELECT set_config('app.current_org_id', '', true)"))
        result = await app_rw_session.execute(text("SELECT * FROM economic_plan_line"))
        assert len(result.fetchall()) == 0

    @pytest.mark.asyncio
    async def test_insert_foreign_org_plan_rejected(self, app_rw_session, m4_seed):
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :oid, true)"),
            {"oid": str(m4_seed["org_b_id"])},
        )
        with pytest.raises(Exception):
            await app_rw_session.execute(
                text(
                    "INSERT INTO economic_plan (id, organization_id, property_id, plan_year, "
                    "period_start, period_end, engine_version) "
                    "VALUES (:id, :org, :prop, 2025, '2025-01-01', '2025-12-31', 'm4.1.0')"
                ),
                {"id": str(uuid.uuid4()), "org": str(m4_seed["org_a_id"]), "prop": m4_seed["property"]},
            )

    @pytest.mark.asyncio
    async def test_cross_org_plan_invisible(self, client_a_admin, client_b_admin, m4_seed):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        # org_b cannot see org_a's plan.
        resp = await client_b_admin.get(f"/api/economic-plans/{plan_id}")
        assert resp.status_code == 404


# ===========================================================================
# RBAC -- READ_ONLY cannot create/finalize; can read.
# ===========================================================================
class TestM4Rbac:
    @pytest.mark.asyncio
    async def test_readonly_cannot_create_plan(self, client_a_readonly, m4_seed):
        resp = await client_a_readonly.post("/api/economic-plans", json={
            "property_id": m4_seed["property"], "plan_year": 2025,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_readonly_cannot_create_weg_run(self, client_a_admin, client_a_readonly, m4_seed):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        resp = await client_a_readonly.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"], "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert resp.status_code == 403


# ===========================================================================
# Immutability of economic_plan (ADR-0003 trigger)
# ===========================================================================
class TestM4PlanImmutability:
    @pytest.mark.asyncio
    async def test_update_finalized_plan_rejected(self, app_rw_session, m4_seed, client_a_admin):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :oid, true)"),
            {"oid": str(m4_seed["org_a_id"])},
        )
        with pytest.raises(Exception, match="GoBD|finalized"):
            await app_rw_session.execute(
                text("UPDATE economic_plan SET reserve_contribution_total = 999 WHERE id = :id"),
                {"id": plan_id},
            )

    @pytest.mark.asyncio
    async def test_delete_finalized_plan_rejected(self, app_rw_session, m4_seed, client_a_admin):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :oid, true)"),
            {"oid": str(m4_seed["org_a_id"])},
        )
        with pytest.raises(Exception, match="GoBD|finalized"):
            await app_rw_session.execute(
                text("DELETE FROM economic_plan WHERE id = :id"), {"id": plan_id}
            )

    @pytest.mark.asyncio
    async def test_finalized_plan_line_frozen(self, app_rw_session, m4_seed, client_a_admin):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :oid, true)"),
            {"oid": str(m4_seed["org_a_id"])},
        )
        with pytest.raises(Exception, match="GoBD|finalized"):
            await app_rw_session.execute(
                text("UPDATE economic_plan_line SET planned_amount = 1 WHERE economic_plan_id = :id"),
                {"id": plan_id},
            )

    @pytest.mark.asyncio
    async def test_superseded_by_id_allowed_on_finalized_plan(self, app_rw_session, m4_seed, client_a_admin):
        # The ONLY allowed mutation: setting superseded_by_id (correction chain).
        resp = await client_a_admin.post(f"/api/economic-plans/{await _create_and_finalize_plan(client_a_admin, m4_seed)}/correction")
        assert resp.status_code == 201
        # The correction creates a new draft plan and links the original.
        assert resp.json()["status"] == "draft"


# ===========================================================================
# Wirtschaftsplan end-to-end (monthly rates)
# ===========================================================================
class TestM4WirtschaftsplanE2E:
    @pytest.mark.asyncio
    async def test_plan_monthly_rates(self, client_a_admin, m4_seed):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        resp = await client_a_admin.get(f"/api/economic-plans/{plan_id}")
        data = resp.json()
        # Total volume = 4000 + 3000 + 6000 = 13000. Owner A 600/1000 = 7800;
        # B 400/1000 = 5200. Monthly A 650.00 ; B 433.33 (Σ rates == annual).
        advances = {ln["owner_profile_id"]: ln for ln in data["lines"] if ln["line_type"] == "OWNER_ADVANCE"}
        a = advances[m4_seed["owner_a"]]
        b = advances[m4_seed["owner_b"]]
        assert a["planned_amount"] == "7800.00"
        # 7800/12 = 650.00 exactly.
        assert a["monthly_amount"] == "650.00"
        assert b["planned_amount"] == "5200.00"
        # 5200/12 = 433.3333... ; floor 433.33 * 12 = 5199.96 -> 4 extra cents go
        # to the first 4 rates (Largest-Remainder, ADR-0005): 4x 433.34 + 8x 433.33.
        # monthly_amount is the representative first rate (433.34); the exact 12
        # rates sum to 5200.00. Per-rate detail lives in the plan snapshot.
        assert b["monthly_amount"] == "433.34"

    @pytest.mark.asyncio
    async def test_plan_pdf_only_finalized(self, client_a_admin, m4_seed):
        resp = await client_a_admin.post("/api/economic-plans", json={
            "property_id": m4_seed["property"], "plan_year": 2026,
            "period_start": "2026-01-01", "period_end": "2026-12-31",
        })
        plan_id = resp.json()["id"]
        # Draft -> no PDF.
        resp = await client_a_admin.get(f"/api/economic-plans/{plan_id}/pdf")
        assert resp.status_code == 409


# ===========================================================================
# WEG-Jahresabrechnung end-to-end -- the hand-checked Abrechnungsspitze.
# ===========================================================================
class TestM4WegSettlementE2E:
    @pytest.mark.asyncio
    async def test_full_weg_settlement(self, client_a_admin, m4_withdrawal_seed):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_withdrawal_seed)
        # Create the WEG run linked to the plan.
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_withdrawal_seed["property"], "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["id"]
        # Compute + finalize with reserve opening 20000, withdrawal 8000.
        resp = await finalize_weg_fixture(client_a_admin, run_id, m4_withdrawal_seed, "20000.00", "8000.00")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "finalized"
        assert data["snapshot_sha256"] is not None

        # Ist costs (excl. reserve) = Gartenpflege 4000 + Verwalter 3000 = 7000.
        assert data["total_cost"] == "7000.00"

        lines = data["lines"]
        # Per-owner OWNER_COST_TOTAL: A 7000*600/1000 = 4200 ; B = 2800.
        cost_totals = {ln["addressee_owner_profile_id"]: ln["amount"]
                       for ln in lines if ln["line_type"] == "OWNER_COST_TOTAL"}
        assert cost_totals[m4_withdrawal_seed["owner_a"]] == "4200.00"
        assert cost_totals[m4_withdrawal_seed["owner_b"]] == "2800.00"

        # Plan Soll: the plan advance per owner is cost+reserve share:
        #   A 13000*600/1000 = 7800 ; B = 5200.
        plan_adv = {ln["addressee_owner_profile_id"]: ln["amount"]
                    for ln in lines if ln["line_type"] == "PLAN_ADVANCE"}
        assert plan_adv[m4_withdrawal_seed["owner_a"]] == "7800.00"
        assert plan_adv[m4_withdrawal_seed["owner_b"]] == "5200.00"

        # Abrechnungsspitze = Ist - Soll (NOT incl. reserve in the Ist):
        #   A 4200 - 7800 = -3600.00 (Guthaben) ; B 2800 - 5200 = -2400.00.
        spitze = {ln["addressee_owner_profile_id"]: ln["amount"]
                  for ln in lines if ln["line_type"] == "ABRECHNUNGSSPITZE"}
        assert spitze[m4_withdrawal_seed["owner_a"]] == "-3600.00"
        assert spitze[m4_withdrawal_seed["owner_b"]] == "-2400.00"

        # A reserve cost position is not cash: no owner has paid a contribution.
        res_owner = {ln["addressee_owner_profile_id"]: ln["amount"]
                     for ln in lines if ln["line_type"] == "RESERVE_CONTRIBUTION"
                     and ln["addressee_owner_profile_id"] is not None}
        assert res_owner == {}

        # Cash development: 20000 opening + 0 paid - 8000 override = 12000.
        closing = next(ln for ln in lines if ln["line_type"] == "RESERVE_CLOSING")
        assert closing["amount"] == "12000.00"
        opening = next(ln for ln in lines if ln["line_type"] == "RESERVE_OPENING")
        assert opening["amount"] == "20000.00"

    @pytest.mark.asyncio
    async def test_sum_owner_cost_equals_total(self, client_a_admin, m4_seed):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"], "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        run_id = resp.json()["id"]
        resp = await finalize_weg_fixture(client_a_admin, run_id, m4_seed)
        lines = resp.json()["lines"]
        s = sum(Decimal(ln["amount"]) for ln in lines if ln["line_type"] == "OWNER_COST_TOTAL")
        assert s == Decimal("7000.00")

    @pytest.mark.asyncio
    async def test_weg_requires_plan(self, client_a_admin, m4_seed):
        # No economic_plan_id -> the create endpoint requires it (422 on missing).
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"], "economic_plan_id": str(uuid.uuid4()),
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        # Unknown plan id -> 404.
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize("plan_id", [None, "None", "", "not-a-uuid"])
    async def test_weg_refuses_invalid_plan_with_structured_german_message(self, client_a_admin, m4_seed, plan_id):
        response = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"], "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == {
            "code": "WEG_PLAN_REQUIRED", "message": "Bitte wählen Sie einen gültigen Wirtschaftsplan.",
        }

    @pytest.mark.asyncio
    async def test_weg_pdf_renders_for_finalized(self, client_a_admin, m4_seed):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"], "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        run_id = resp.json()["id"]
        await finalize_weg_fixture(client_a_admin, run_id, m4_seed)
        resp = await client_a_admin.get(f"/api/weg-settlements/{run_id}/pdf")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content[:4] == b"%PDF"

    @pytest.mark.asyncio
    async def test_finalized_weg_run_cannot_recompute(self, client_a_admin, m4_seed):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"], "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        run_id = resp.json()["id"]
        await finalize_weg_fixture(client_a_admin, run_id, m4_seed)
        resp = await client_a_admin.post(f"/api/weg-settlements/{run_id}/compute", json=weg_values(m4_seed))
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_payload_consistent_with_lines(self, client_a_admin, m4_seed, app_rw_session):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"], "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        run_id = resp.json()["id"]
        await finalize_weg_fixture(client_a_admin, run_id, m4_seed)

        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :oid, true)"),
            {"oid": str(m4_seed["org_a_id"])},
        )
        row = (await app_rw_session.execute(
            text("SELECT snapshot_payload FROM settlement_run WHERE id = :id"), {"id": run_id}
        )).first()
        payload = row[0]
        # Σ owner_cost_total in the payload result == total_cost.
        owners = payload["result"]["owners"]
        s = sum(Decimal(o["owner_cost_total"]) for o in owners)
        assert s == Decimal(payload["result"]["cost_total"])
        # Σ spitze == cost_total - plan_advances_total (no drift).
        s_sp = sum(Decimal(o["abrechnungsspitze"]) for o in owners)
        assert s_sp == Decimal(payload["result"]["abrechnungsspitze_total"])


# ===========================================================================
# Vermoegensbericht end-to-end.
# ===========================================================================
class TestM4VermoegensberichtE2E:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ["compute", "finalize"])
    @pytest.mark.parametrize("invalid_inputs", [False, True])
    async def test_out_of_scope_and_unknown_report_bank_accounts_precede_validation(
        self, client_a_admin, client_b_admin, seed_data, migrator_session, action, invalid_inputs,
    ):
        banks = []
        for api, label in ((client_a_admin, "Report"), (client_a_admin, "Other property"), (client_b_admin, "Foreign")):
            property_response = await api.post("/api/properties", json={
                "name": label + " report bank", "management_type": "WEG",
            })
            assert property_response.status_code == 201, property_response.text
            bank = await api.post("/api/bank-accounts", json={
                "property_id": property_response.json()["id"], "account_type": "LAUFEND", "label": label,
            })
            assert bank.status_code == 201, bank.text
            banks.append(bank.json())
        visible_bank, other_property_bank, foreign_bank = banks
        scope_id = uuid.uuid4()
        params = {"scope": str(scope_id), "org": str(seed_data["org_a_id"]),
                  "property": visible_bank["property_id"], "bank": visible_bank["id"],
                  "actor": str(seed_data["user_a_admin_id"])}
        with psycopg2.connect(SYNC_URL) as setup, setup.cursor() as cursor:
            cursor.execute("""INSERT INTO accounting_scope(
                id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by)
                VALUES(%(scope)s,%(org)s,%(property)s,'WEG','Report scope','2025-01-01',true,'REVIEWED',%(actor)s)""", params)
            cursor.execute("""INSERT INTO accounting_scope_binding(
                organization_id,property_id,scope_id,bank_account_id,origin,reason,reviewed_by)
                VALUES(%(org)s,%(property)s,%(scope)s,%(bank)s,'REVIEWED','Report bank',%(actor)s)""", params)
            cursor.execute("""INSERT INTO scope_grant(
                organization_id,scope_id,user_id,capabilities,granted_by,reason)
                VALUES(%(org)s,%(scope)s,%(actor)s,ARRAY['read','prepare'],%(actor)s,'Report preparation')""", params)
        report = await client_a_admin.post("/api/weg-settlements/asset-reports", json={
            "property_id": visible_bank["property_id"], "accounting_scope_id": str(scope_id),
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert report.status_code == 201, report.text
        run_id = report.json()["id"]
        before = await migrator_session.scalar(text("SELECT to_jsonb(r)::text FROM settlement_run r WHERE id=:id"), {"id": run_id})
        body = {"reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "0.00", "receivables": [], "payables": [], "bank_balances": [
                    {"bank_account_id": visible_bank["id"], "label": "Visible", "account_type": "LAUFEND", "balance": "0.00"},
                ]}
        if invalid_inputs:
            body["reserve_withdrawals_total"] = "1.00"
        responses = []
        for parent in (foreign_bank["id"], str(uuid.uuid4()), other_property_bank["id"]):
            balance = {"bank_account_id": parent, "label": "Unresolved", "account_type": "LAUFEND", "balance": "0.00"}
            response = await client_a_admin.post(f"/api/weg-settlements/asset-reports/{run_id}/{action}",
                json=body | {"bank_balances": body["bank_balances"] + [balance]})
            assert response.status_code == 404, response.text
            responses.append(response.json())
        assert responses == [{"detail": "Bank account not found"}] * 3
        assert await migrator_session.scalar(text("SELECT to_jsonb(r)::text FROM settlement_run r WHERE id=:id"), {"id": run_id}) == before
        assert await migrator_session.scalar(text("SELECT count(*) FROM settlement_line WHERE settlement_run_id=:id"), {"id": run_id}) == 0

    @pytest.mark.asyncio
    async def test_full_asset_report(self, client_a_admin, m4_withdrawal_seed):
        resp = await client_a_admin.post("/api/weg-settlements/asset-reports", json={
            "property_id": m4_withdrawal_seed["property"],
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["id"]
        body = {
            "reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "20000.00",
            "bank_balances": [
                {"bank_account_id": m4_withdrawal_seed["bank_giro"], "label": "Girokonto", "account_type": "LAUFEND", "balance": "15000.00"},
                {"bank_account_id": m4_withdrawal_seed["bank_ruecklage"], "label": "Ruecklagenkonto", "account_type": "RUECKLAGE", "balance": "18000.00"},
            ],
            "receivables": [{"label": "Hausgeldrueckstand", "amount": "500.00"}],
            "payables": [{"label": "offene Rechnung", "amount": "1200.00"}],
        }
        evidence = {key.removeprefix('reserve_'): value for key, value in (await reserve_evidence(client_a_admin, m4_withdrawal_seed)).items()}
        for component, amount in [('WITHDRAWAL', '8000.00')]:
            correction = await client_a_admin.post(f"/api/weg-settlements/{run_id}/reserve-ledger/overrides", json={
                'component': component, 'amount': amount, 'reason': 'Documented synthetic correction', **evidence})
            assert correction.status_code == 201, correction.text
        from tests.test_reserve_ledger import payment
        await payment(client_a_admin, m4_withdrawal_seed, '6000.00')
        await attest_fixture(client_a_admin, "settlement", run_id, body)
        resp = await client_a_admin.post(f"/api/weg-settlements/asset-reports/{run_id}/finalize", json=body)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        lines = data["lines"]
        # Reserve closing 18000 (automatic).
        closing = next(ln for ln in lines if ln["line_type"] == "RESERVE_CLOSING")
        assert closing["amount"] == "18000.00"
        # Bank balances captured.
        bank = [ln for ln in lines if ln["line_type"] == "BANK_BALANCE"]
        assert len(bank) == 2
        recv = next(ln for ln in lines if ln["line_type"] == "RECEIVABLE")
        assert recv["amount"] == "500.00"

    @pytest.mark.asyncio
    async def test_asset_report_pdf(self, client_a_admin, m4_seed):
        resp = await client_a_admin.post("/api/weg-settlements/asset-reports", json={
            "property_id": m4_seed["property"],
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        run_id = resp.json()["id"]
        body = {
            "reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "1000.00", "receivables": [], "payables": [],
            "bank_balances": [
                {"bank_account_id": m4_seed["bank_giro"], "label": "Giro", "account_type": "LAUFEND", "balance": "500.00"},
                {"bank_account_id": m4_seed["bank_ruecklage"], "label": "Reserve", "account_type": "RUECKLAGE", "balance": "0.00"}],
        }
        await attest_fixture(client_a_admin, "settlement", run_id, body)
        finalized = await client_a_admin.post(f"/api/weg-settlements/asset-reports/{run_id}/finalize", json=body)
        assert finalized.status_code == 200, finalized.text
        resp = await client_a_admin.get(f"/api/weg-settlements/asset-reports/{run_id}/pdf")
        assert resp.status_code == 200
        assert resp.content[:4] == b"%PDF"


# ===========================================================================
# Snapshot/hash determinism through the service (m4 section 9.1 #9).
# ===========================================================================
class TestM4Determinism:
    @pytest.mark.asyncio
    async def test_weg_results_stable_with_distinct_attestations(self, client_a_admin, m4_withdrawal_seed):
        # Identical arithmetic retains separate evidence for each attestation.
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_withdrawal_seed)

        async def finalize_once():
            resp = await client_a_admin.post("/api/weg-settlements", json={
                "property_id": m4_withdrawal_seed["property"], "economic_plan_id": plan_id,
                "period_start": "2025-01-01", "period_end": "2025-12-31",
            })
            run_id = resp.json()["id"]
            ledger = (await client_a_admin.get(f"/api/weg-settlements/{run_id}/reserve-ledger")).json()
            if ledger['frozen']:
                # Reuse the issued same-period opening; do not attest another import.
                values = {'paid_advances': weg_values(m4_withdrawal_seed)['paid_advances']}
                await attest_fixture(client_a_admin, 'settlement', run_id, values)
                resp = await client_a_admin.post(f"/api/weg-settlements/{run_id}/finalize", json=values)
                assert resp.status_code == 200, resp.text
            else:
                resp = await finalize_weg_fixture(client_a_admin, run_id, m4_withdrawal_seed, "20000.00", "8000.00")
            return resp.json()

        first = await finalize_once()
        second = await finalize_once()
        assert first["total_cost"] == second["total_cost"]
        assert [(l["line_type"], l["amount"]) for l in first["lines"]] == [(l["line_type"], l["amount"]) for l in second["lines"]]
        assert first["snapshot_sha256"] != second["snapshot_sha256"]
        reloaded = await client_a_admin.get(f"/api/weg-settlements/{first['id']}")
        assert reloaded.json()["snapshot_sha256"] == first["snapshot_sha256"]


# ===========================================================================

# ===========================================================================
# T09: derive_paid_advances – test paid-advances-derived route
# ===========================================================================
from datetime import datetime, timezone as _tz

_TZ_UTC = _tz.utc
_BOOKED_AT = datetime(2025, 6, 2, 10, 0, 0, tzinfo=_TZ_UTC)


def _insert_payment_sql() -> str:
    return """
        INSERT INTO payment (
            id, organization_id, bank_account_id, property_id,
            direction, amount, value_date, payment_type,
            tenant_profile_id, owner_profile_id, lease_agreement_id,
            period_year, period_month, status, booked_at, booked_by
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s, %s
        )
    """


@pytest.fixture(scope="session")
def m4_t09_seed(seed_data):
    """Keep booked payments separate from the no-payments fixture."""
    m4_seed = _seed_weg(seed_data)
    import psycopg2

    org = str(m4_seed["org_a_id"])
    prop = str(m4_seed["property"])
    owner_a = str(m4_seed["owner_a"])
    owner_b = str(m4_seed["owner_b"])
    bank_giro = str(m4_seed["bank_giro"])

    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()
        # 12 monthly payments per owner: 600.00 for owner A, 500.00 for owner B
        for month in range(1, 13):
            # Owner A: 12 x 600 = 7200
            cur.execute(
                _insert_payment_sql(),
                (
                    str(uuid.uuid4()),
                    org,
                    bank_giro,
                    prop,
                    "IN",
                    "600.00",
                    date(2025, month, 1),
                    "HAUSGELD_VORSCHUSS",
                    None,
                    owner_a,
                    None,
                    2025,
                    month,
                    "booked",
                    _BOOKED_AT,
                    str(m4_seed["user_a_admin_id"]),
                ),
            )
            # Owner B: 12 x 500 = 6000
            cur.execute(
                _insert_payment_sql(),
                (
                    str(uuid.uuid4()),
                    org,
                    bank_giro,
                    prop,
                    "IN",
                    "500.00",
                    date(2025, month, 1),
                    "HAUSGELD_VORSCHUSS",
                    None,
                    owner_b,
                    None,
                    2025,
                    month,
                    "booked",
                    _BOOKED_AT,
                    str(m4_seed["user_a_admin_id"]),
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return m4_seed


class TestM4PaidAdvancesDerived:
    @pytest.mark.asyncio
    async def test_derive_paid_advances_returns_correct_totals(self, client_a_admin, m4_t09_seed):
        """12 monthly payments per owner → correct paid totals."""
        # Create and finalize economic plan
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_t09_seed)

        # Create settlement run
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_t09_seed["property"],
            "economic_plan_id": plan_id,
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
        })
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["id"]

        # Derive paid advances
        resp = await client_a_admin.post(f"/api/weg-settlements/{run_id}/paid-advances-derived")
        assert resp.status_code == 200, resp.text
        result = resp.json()

        # Should return 2 owners with correct totals
        assert len(result) == 2
        totals = {item["owner_profile_id"]: item["paid_total"] for item in result}
        # Owner A: 12 x 600 = 7200
        assert totals[str(m4_t09_seed["owner_a"])] == "7200.00"
        # Owner B: 12 x 500 = 6000
        assert totals[str(m4_t09_seed["owner_b"])] == "6000.00"

    @pytest.mark.asyncio
    async def test_derive_paid_advances_returns_zero_totals_when_no_payments(self, client_a_admin, m4_seed):
        """No payments → 2 owners with paid_total='0.00'."""
        # Create and finalize economic plan (no payments added)
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)

        # Create settlement run
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"],
            "economic_plan_id": plan_id,
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
        })
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["id"]

        # Derive paid advances → 2 owners with 0.00
        resp = await client_a_admin.post(f"/api/weg-settlements/{run_id}/paid-advances-derived")
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert len(result) == 2
        totals = {item["owner_profile_id"]: item["paid_total"] for item in result}
        assert totals[str(m4_seed["owner_a"])] == "0.00"
        assert totals[str(m4_seed["owner_b"])] == "0.00"
