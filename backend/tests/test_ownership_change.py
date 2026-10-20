"""T26: end ownership through the API and settle both owners by calendar days."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date
from decimal import Decimal

import httpx
import psycopg2
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.config import settings
from app.core.db import get_tenant_session
from app.core.security import create_session_token
from app.main import app
from app.models.ownership_history import OwnershipHistory
from app.repositories.ownership_history_repo import OwnershipHistoryRepository
from tests.conftest import MIGRATOR_SYNC_URL
from tests.financial_coverage_helpers import attest_fixture

pytestmark = pytest.mark.asyncio


@pytest.fixture
def ownership_world(seed_data):
    ids = {key: str(uuid.uuid4()) for key in (
        "property", "unit", "seller_person", "buyer_person", "seller", "buyer", "bank", "cost",
    )}
    actors = {key: uuid.uuid4() for key in (
        "org_a_id", "user_a_admin_id", "user_a_clerk_id", "user_a_readonly_id",
    )}
    params = {**ids, "org": str(actors["org_a_id"]), "actor": str(actors["user_a_admin_id"])}
    with psycopg2.connect(MIGRATOR_SYNC_URL) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO organization(id,name) VALUES (%s,'T26 synthetic')", (params["org"],))
        for key, role in (("user_a_admin_id", "OWNER_ADMIN"), ("user_a_clerk_id", "CLERK"),
                          ("user_a_readonly_id", "READ_ONLY")):
            actor = str(actors[key])
            cur.execute('''INSERT INTO "user"(id,organization_id,email,password_hash,display_name)
                VALUES (%s,%s,%s,'synthetic-unused-password','T26')''',
                        (actor, params["org"], f"{actor}@ownership-change.invalid"))
            cur.execute("INSERT INTO membership(organization_id,user_id,role) VALUES (%s,%s,%s)",
                        (params["org"], actor, role))
        cur.execute("""INSERT INTO property(id,organization_id,name,management_type)
            VALUES (%(property)s,%(org)s,'T26 synthetic WEG','WEG')""", params)
        cur.execute("""INSERT INTO unit(id,organization_id,property_id,number,usage_type,area,heated_area,mea)
            VALUES (%(unit)s,%(org)s,%(property)s,'1','WOHNUNG',100,100,1000)""", params)
        for owner in ("seller", "buyer"):
            cur.execute("""INSERT INTO person(id,organization_id,first_name,last_name)
                VALUES (%s,%s,%s,'T26 synthetic')""", (ids[owner + "_person"], params["org"], owner))
            cur.execute("INSERT INTO owner_profile(id,organization_id,person_id) VALUES (%s,%s,%s)",
                        (ids[owner], params["org"], ids[owner + "_person"]))
    return {**seed_data, **actors, **ids}


def seed_financial_inputs(world):
    params = {**world, "org": str(world["org_a_id"]), "actor": str(world["user_a_admin_id"])}
    with psycopg2.connect(MIGRATOR_SYNC_URL) as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO bank_account(id,organization_id,property_id,account_type,label)
            VALUES (%(bank)s,%(org)s,%(property)s,'LAUFEND','T26 synthetic')""", params)
        cur.execute("""INSERT INTO cost_entry(id,organization_id,property_id,cost_category_id,
            cost_category_plan_version,invoice_date,service_period_from,service_period_to,
            gross_amount,owner_distribution_key,tenant_distribution_key,tenant_apportionable,
            status,source,confirmed_at,confirmed_by)
            SELECT %(cost)s,%(org)s,%(property)s,id,1,'2026-06-01','2026-01-01','2026-12-31',
                365,'MEA','MEA','NICHT_UMLAGEFAEHIG','confirmed','MANUAL',now(),%(actor)s
            FROM cost_category WHERE code='WEG_VERWALTERKOSTEN' AND plan_version=1""", params)
        assert cur.rowcount == 1


def ownership_client(world, actor="user_a_admin_id"):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        cookies={settings.session_cookie_name: create_session_token(world[actor], world["org_a_id"])},
    )


@pytest_asyncio.fixture
async def client_a_admin(ownership_world):
    async with ownership_client(ownership_world) as api:
        yield api


async def create_owner(api, world, owner="seller", **changes):
    body = {"unit_id": world["unit"], "owner_profile_id": world[owner],
            "valid_from": "2026-01-01", "mea_share": "1"}
    body.update(changes)
    response = await api.post("/api/ownership", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def url(row):
    return f"/api/ownership/{row['id']}"


async def end_owner(api, row, end="2026-06-30"):
    response = await api.patch(url(row), json={"valid_to": end})
    assert response.status_code == 200, response.text
    actual = response.json()
    assert Decimal(actual["mea_share"]) == Decimal(row["mea_share"])
    assert actual == {**row, "valid_to": end, "mea_share": actual["mea_share"]}
    return actual


async def test_patch_end_persists_exclusive_bound_and_successor(client_a_admin, ownership_world):
    w = ownership_world
    seller = await create_owner(client_a_admin, w)
    seller = await end_owner(client_a_admin, seller)
    await end_owner(client_a_admin, seller)  # same request is idempotent
    buyer = await create_owner(client_a_admin, w, "buyer", valid_from="2026-07-01", mea_share="1.000000")
    async with get_tenant_session(w["org_a_id"]) as db:
        stored = await db.scalar(text("SELECT valid_to FROM ownership_history WHERE id=:id"),
                                 {"id": uuid.UUID(seller["id"])})
        assert stored == date(2026, 7, 1)
        repo = OwnershipHistoryRepository(db, w["org_a_id"])
        assert [str(o.id) for o in await repo.owners_at_date(uuid.UUID(w["unit"]), date(2026, 6, 30))] == [seller["id"]]
        assert [str(o.id) for o in await repo.owners_at_date(uuid.UUID(w["unit"]), date(2026, 7, 1))] == [buyer["id"]]
    response = await client_a_admin.get("/api/ownership", params={"unit_id": w["unit"]})
    assert response.status_code == 200, response.text
    assert response.json() == [{**seller, "valid_to": "2026-06-30"}, buyer]


async def finalize_plan(api, w):
    response = await api.post("/api/economic-plans", json={
        "property_id": w["property"], "plan_year": 2026,
        "period_start": "2026-01-01", "period_end": "2026-12-31",
    })
    assert response.status_code == 201, response.text
    plan = response.json()["id"]
    values = {"planned_costs": [{"category_code": "WEG_VERWALTERKOSTEN", "label": "Annual cost",
               "planned_amount": "365.00", "cost_group": "NICHT_UMLAGEFAEHIG"}],
              "reserve_contribution_total": "0.00"}
    await attest_fixture(api, "economic_plan", plan, values)
    response = await api.post(f"/api/economic-plans/{plan}/finalize", json=values)
    assert response.status_code == 200, response.text
    return plan


async def test_real_annual_weg_settlement_is_181_and_184_days(client_a_admin, ownership_world):
    w, api = ownership_world, client_a_admin
    seed_financial_inputs(w)
    seller = await create_owner(api, w)
    await end_owner(api, seller)
    await create_owner(api, w, "buyer", valid_from="2026-07-01")
    plan = await finalize_plan(api, w)
    response = await api.post("/api/weg-settlements", json={
        "property_id": w["property"], "economic_plan_id": plan,
        "period_start": "2026-01-01", "period_end": "2026-12-31",
    })
    assert response.status_code == 201, response.text
    run = response.json()["id"]
    values = {"reserve_opening_balance": "0.00", "reserve_withdrawals_total": "0.00",
              "paid_advances": [{"owner_profile_id": w[owner], "paid_total": "0.00"}
                                for owner in ("seller", "buyer")]}
    await attest_fixture(api, "settlement", run, values)
    response = await api.post(f"/api/weg-settlements/{run}/compute", json=values)
    assert response.status_code == 200, response.text
    data = response.json()
    totals = {line["addressee_owner_profile_id"]: Decimal(line["amount"])
              for line in data["lines"] if line["line_type"] == "OWNER_COST_TOTAL"}
    assert totals == {w["seller"]: Decimal("181.00"), w["buyer"]: Decimal("184.00")}
    cost_lines = {line["addressee_owner_profile_id"]: line for line in data["lines"]
                  if line["cost_category_code"] == "WEG_VERWALTERKOSTEN"}
    for owner, days in (("seller", 181), ("buyer", 184)):
        assert cost_lines[w[owner]]["time_share_days"] == days
        assert cost_lines[w[owner]]["time_share_total_days"] == 365
    response = await api.post(f"/api/weg-settlements/{run}/finalize", json=values)
    assert response.status_code == 200, response.text
    frozen = response.json()
    assert frozen["status"] == "finalized" and frozen["snapshot_sha256"]
    pdf = await api.get(f"/api/weg-settlements/{run}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    # Ending history later must not rewrite an already released statement/document.
    await end_owner(api, seller, "2026-06-29")
    assert (await api.get(f"/api/weg-settlements/{run}")).json() == frozen
    assert (await api.get(f"/api/weg-settlements/{run}/pdf")).content == pdf.content


@pytest.mark.parametrize("body", [
    {}, {"valid_to": None}, {"valid_to": "not-a-date"},
    {"valid_to": "2025-12-31"}, {"valid_to": "9999-12-31"},
    *[{"valid_to": "2026-06-30", field: value} for field, value in (
        ("unit_id", str(uuid.uuid4())), ("owner_profile_id", str(uuid.uuid4())),
        ("organization_id", str(uuid.uuid4())), ("valid_from", "2025-01-01"),
        ("mea_share", "0.5"), ("notes", "not editable"), ("id", str(uuid.uuid4())),
    )],
])
async def test_only_required_representable_end_is_accepted(client_a_admin, ownership_world, body):
    row = await create_owner(client_a_admin, ownership_world)
    response = await client_a_admin.patch(url(row), json=body)
    assert response.status_code == 422, response.text
    response = await client_a_admin.get("/api/ownership", params={"unit_id": ownership_world["unit"]})
    assert len(response.json()) == 1 and response.json()[0]["valid_to"] is None


@pytest.mark.parametrize("start,end", [
    ("1990-01-01", "1990-01-01"), ("2090-01-01", "2090-12-31"),
    ("2024-02-28", "2024-02-29"), ("9999-12-30", "9999-12-30"),
])
async def test_calendar_end_has_no_today_only_policy(client_a_admin, ownership_world, start, end):
    row = await create_owner(client_a_admin, ownership_world, valid_from=start)
    await end_owner(client_a_admin, row, end)


async def test_authentication_roles_and_tenant_isolation(client_a_admin, client_b_admin, ownership_world):
    w = ownership_world
    row = await create_owner(client_a_admin, w)
    async with ownership_client(w, "user_a_readonly_id") as reader:
        response = await reader.patch(url(row), json={"valid_to": "2026-06-30"})
        assert response.status_code == 403, response.text
    response = await client_b_admin.patch(url(row), json={"valid_to": "2026-06-30"})
    assert response.status_code == 404, response.text
    async with get_tenant_session(w["org_b_id"]) as db:
        assert await db.scalar(text("SELECT count(*) FROM ownership_history WHERE id=:id"),
                               {"id": uuid.UUID(row["id"])}) == 0
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        response = await anon.patch(url(row), json={"valid_to": "2026-06-30"})
        assert response.status_code == 401, response.text
    assert (await client_a_admin.patch(f"/api/ownership/{uuid.uuid4()}", json={"valid_to": "2026-06-30"})).status_code == 404
    assert (await client_a_admin.patch("/api/ownership/not-a-uuid", json={"valid_to": "2026-06-30"})).status_code == 422
    async with ownership_client(w, "user_a_clerk_id") as clerk:
        await end_owner(clerk, row)


async def test_same_owner_overlap_rolls_back_but_coowners_are_legal(client_a_admin, ownership_world):
    w, api = ownership_world, client_a_admin
    seller = await create_owner(api, w, valid_to="2026-06-30")
    later = await create_owner(api, w, valid_from="2026-07-01")
    # Different profiles may own concurrently; do not invent a one-owner rule.
    buyer = await create_owner(api, w, "buyer", valid_from="2026-01-01", mea_share="0.5")
    await end_owner(api, buyer)
    conflict = await api.patch(url(seller), json={"valid_to": "2026-07-01"})
    assert conflict.status_code == 409, conflict.text
    listing = (await api.get("/api/ownership", params={"unit_id": w["unit"]})).json()
    by_id = {row["id"]: row for row in listing}
    assert len(by_id) == 3
    assert by_id[seller["id"]]["valid_to"] == "2026-06-30"
    assert by_id[later["id"]]["valid_to"] is None
    await end_owner(api, seller, "2026-06-29")  # healthy next transaction after conflict


async def test_concurrent_identical_ends_are_idempotent(client_a_admin, ownership_world):
    row = await create_owner(client_a_admin, ownership_world)
    async with ownership_client(ownership_world) as second:
        responses = await asyncio.wait_for(asyncio.gather(
            client_a_admin.patch(url(row), json={"valid_to": "2026-06-30"}),
            second.patch(url(row), json={"valid_to": "2026-06-30"}),
        ), timeout=15)
    assert [response.status_code for response in responses] == [200, 200], [r.text for r in responses]
    assert responses[0].json() == responses[1].json()
    async with get_tenant_session(ownership_world["org_a_id"]) as db:
        assert await db.scalar(text("SELECT count(*) FROM ownership_history WHERE unit_id=:unit"),
                               {"unit": uuid.UUID(ownership_world["unit"])}) == 1


async def test_patch_uses_financial_lock_before_parent_lock_and_ambient_rollback(
    client_a_admin, ownership_world, monkeypatch,
):
    from app.repositories.ownership_history_repo import OwnershipHistoryRepository
    from app.services.financial_scope_service import FinancialScopeService
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession

    row = await create_owner(client_a_admin, ownership_world)
    calls = []
    original_inputs = FinancialScopeService.lock_inputs
    original_parent = OwnershipHistoryRepository.get_by_id_for_update
    original_flush = AsyncSession.flush

    async def inputs(service):
        calls.append("financial-inputs")
        return await original_inputs(service)

    async def parent(repo, entity_id):
        assert calls == ["financial-inputs"]
        calls.append("parent")
        return await original_parent(repo, entity_id)

    async def fail_after_flush(db, *args, **kwargs):
        await original_flush(db, *args, **kwargs)
        if any(isinstance(entity, OwnershipHistory)
               for entity in db.identity_map.values()):
            raise HTTPException(409, "Synthetic failure after flush")

    with monkeypatch.context() as patch:
        patch.setattr(FinancialScopeService, "lock_inputs", inputs)
        patch.setattr(OwnershipHistoryRepository, "get_by_id_for_update", parent)
        patch.setattr(AsyncSession, "flush", fail_after_flush)
        response = await client_a_admin.patch(url(row), json={"valid_to": "2026-06-30"})
        assert response.status_code == 409, response.text
    assert calls == ["financial-inputs", "parent"]
    async with get_tenant_session(ownership_world["org_a_id"]) as db:
        assert await db.scalar(text("SELECT valid_to FROM ownership_history WHERE id=:id"),
                               {"id": uuid.UUID(row["id"])}) is None
    await end_owner(client_a_admin, row)


async def test_live_source_prepare_contact_revocation_and_pause_without_bypasses(
    client_a_admin, ownership_world,
):
    from tests.contact_source_fixtures import grant_contact
    from tests.test_property_source_access import grant
    from tests.test_source_security_lifecycle import accepted, status, transition, transition_body

    w, api = ownership_world, client_a_admin
    row = await create_owner(api, w)
    domain, initial_receipt = await grant(api, w, w["property"], caps=["read", "prepare", "export"])
    person_domain, contact_receipt = await grant_contact(api, w, w["seller_person"], caps=["read"])
    await grant_contact(api, w, w["buyer_person"], caps=["read"])
    decision = await accepted(api)
    response = await transition(api, transition_body(await status(api), "ENFORCED", decision))
    assert response.status_code == 201, response.text
    response = await api.post(f"/api/property-data-domains/{domain}/grants/{initial_receipt}/revoke")
    assert response.status_code == 204, response.text
    response = await api.post(f"/api/property-data-domains/{domain}/grants", json={
        "user_id": str(w["user_a_admin_id"]), "capabilities": ["read", "export"],
        "reason": "T26 read without preparation",
    }, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 201, response.text
    read_receipt = response.json()["id"]
    visible = await api.get("/api/ownership", params={"unit_id": w["unit"]})
    assert visible.status_code == 200 and len(visible.json()) == 1, visible.text
    response = await api.patch(url(row), json={"valid_to": "2026-06-30"})
    assert response.status_code == 404, response.text  # read is not prepare

    async def prepare():
        response = await api.post(f"/api/property-data-domains/{domain}/grants", json={
            "user_id": str(w["user_a_admin_id"]), "capabilities": ["read", "prepare"],
            "reason": "T26 explicit preparation",
        }, headers={"Idempotency-Key": str(uuid.uuid4())})
        assert response.status_code == 201, response.text
        return response.json()["id"]

    response = await api.post(f"/api/property-data-domains/{domain}/grants/{read_receipt}/revoke")
    assert response.status_code == 204, response.text
    receipt = await prepare()
    row = await end_owner(api, row)
    response = await api.post(f"/api/property-data-domains/{domain}/grants/{receipt}/revoke")
    assert response.status_code == 204, response.text
    response = await api.patch(url(row), json={"valid_to": "2026-06-29"})
    assert response.status_code == 404, response.text
    # Even an idempotent request must still authorize preparation.
    assert (await api.patch(url(row), json={"valid_to": "2026-06-30"})).status_code == 404
    await prepare()
    response = await api.post(f"/api/person-data-domains/{person_domain}/grants/{contact_receipt}/revoke")
    assert response.status_code == 204, response.text
    response = await api.patch(url(row), json={"valid_to": "2026-06-29"})
    assert response.status_code == 404, response.text
    async with get_tenant_session(w["org_a_id"]) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),
                         {"actor": str(w["user_a_admin_id"])})
        # Retained history can remain readable; revoked contact effect authority
        # must nevertheless prevent any end-date mutation.
        assert await db.scalar(text("SELECT valid_to FROM ownership_history WHERE id=:id"),
                               {"id": uuid.UUID(row["id"])}) == date(2026, 7, 1)
    response = await api.post(f"/api/person-data-domains/{person_domain}/grants", json={
        "user_id": str(w["user_a_admin_id"]), "capabilities": ["read"], "reason_code": "TEAM_ACCESS",
    }, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 201, response.text
    response = await transition(api, transition_body(await status(api), "PAUSED"))
    assert response.status_code == 201, response.text
    response = await api.patch(url(row), json={"valid_to": "2026-06-29"})
    assert response.status_code == 404, response.text
    # Inspect retained originals as the fixture migrator, not by relaxing source RLS.
    with psycopg2.connect(MIGRATOR_SYNC_URL) as conn, conn.cursor() as cur:
        cur.execute("SELECT valid_to FROM ownership_history WHERE id=%s", (row["id"],))
        assert cur.fetchone() == (date(2026, 7, 1),)


async def test_booked_hausgeld_and_old_history_are_retained(client_a_admin, ownership_world):
    w, api = ownership_world, client_a_admin
    seed_financial_inputs(w)
    row = await create_owner(api, w)
    response = await api.post("/api/payments", json={
        "bank_account_id": w["bank"], "property_id": w["property"], "direction": "IN",
        "amount": "30.00", "value_date": "2026-01-05", "payment_type": "HAUSGELD_VORSCHUSS",
        "owner_profile_id": w["seller"], "period_year": 2026, "period_month": 1,
    })
    assert response.status_code == 201, response.text
    payment = response.json()["id"]
    response = await api.post(f"/api/payments/{payment}/book")
    assert response.status_code == 200, response.text
    async with get_tenant_session(w["org_a_id"]) as db:
        before = await db.scalar(text("SELECT to_jsonb(p) FROM payment p WHERE id=:id"),
                                 {"id": uuid.UUID(payment)})
        old = await db.scalar(text("SELECT to_jsonb(h) FROM ownership_history h WHERE id=:id"),
                              {"id": uuid.UUID(row["id"])})
    await end_owner(api, row)
    async with get_tenant_session(w["org_a_id"]) as db:
        assert await db.scalar(text("SELECT to_jsonb(p) FROM payment p WHERE id=:id"),
                               {"id": uuid.UUID(payment)}) == before
        after = await db.scalar(text("SELECT to_jsonb(h) FROM ownership_history h WHERE id=:id"),
                                {"id": uuid.UUID(row["id"])})
    assert before["status"] == "booked"
    assert {key: value for key, value in after.items() if key not in {"valid_to", "updated_at"}} == {
        key: value for key, value in old.items() if key not in {"valid_to", "updated_at"}}
    assert after["valid_to"] == "2026-07-01"


async def input_owner_draft(api, world):
    seed_financial_inputs(world)
    seller = await create_owner(api, world)
    plan = await finalize_plan(api, world)
    response = await api.get(f"/api/economic-plans/{plan}")
    assert response.status_code == 200, response.text
    assert {line["owner_profile_id"] for line in response.json()["lines"]
            if line["owner_profile_id"]} == {world["seller"]}
    await end_owner(api, seller)
    await create_owner(api, world, "buyer", valid_from="2026-07-01", valid_to="2026-12-31")
    response = await api.post("/api/weg-settlements", json={
        "property_id": world["property"], "economic_plan_id": plan,
        "period_start": "2026-01-01", "period_end": "2026-12-31",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_input_owners_include_buyer_missing_from_frozen_plan(client_a_admin, ownership_world):
    w, api = ownership_world, client_a_admin
    run = await input_owner_draft(api, w)
    response = await api.get(f"/api/weg-settlements/{run}/input-owners")
    assert response.status_code == 200, response.text
    assert response.json() == sorted([w["seller"], w["buyer"]])
    values = {"reserve_opening_balance": "0.00", "reserve_withdrawals_total": "0.00",
              "paid_advances": [{"owner_profile_id": w["seller"], "paid_total": "0.00"}]}
    incomplete = await api.post("/api/financial-input-coverage", json={
        "subject_kind": "settlement", "subject_id": run, "values": values,
        "confirmed_complete": True, "reason": "T26 incomplete owner coverage must stay rejected",
        "source_document_ids": [],
    })
    assert incomplete.status_code == 422, incomplete.text
    assert incomplete.json()["detail"]["blockers"][0]["code"] == "owner_payment_coverage_missing"
    values["paid_advances"] = [{"owner_profile_id": owner, "paid_total": "0.00"}
                               for owner in response.json()]
    await attest_fixture(api, "settlement", run, values)
    finalized = await api.post(f"/api/weg-settlements/{run}/finalize", json=values)
    assert finalized.status_code == 200, finalized.text
    totals = {line["addressee_owner_profile_id"]: Decimal(line["amount"])
              for line in finalized.json()["lines"] if line["line_type"] == "OWNER_COST_TOTAL"}
    assert totals == {w["seller"]: Decimal("181.00"), w["buyer"]: Decimal("184.00")}
    before = finalized.json()
    assert (await api.get(f"/api/weg-settlements/{run}/input-owners")).json() == response.json()
    assert (await api.get(f"/api/weg-settlements/{run}")).json() == before


def input_owner_state(world):
    with psycopg2.connect(MIGRATOR_SYNC_URL) as conn, conn.cursor() as cur:
        result = {}
        for table in ("settlement_run", "settlement_line", "coverage_declaration", "audit_event",
                      "economic_plan", "economic_plan_line", "ownership_history"):
            cur.execute(f"SELECT to_jsonb(t) FROM {table} t WHERE organization_id=%s ORDER BY id",
                        (str(world["org_a_id"]),))
            result[table] = cur.fetchall()
        return result


async def test_input_owners_read_auth_type_isolation_and_no_effects(
    client_a_admin, client_b_admin, ownership_world,
):
    w, api = ownership_world, client_a_admin
    run = await input_owner_draft(api, w)
    path = f"/api/weg-settlements/{run}/input-owners"
    asset = await api.post("/api/weg-settlements/asset-reports", json={
        "property_id": w["property"], "period_start": "2026-01-01", "period_end": "2026-12-31",
    })
    assert asset.status_code == 201, asset.text
    before = input_owner_state(w)
    async with ownership_client(w, "user_a_readonly_id") as reader:
        response = await reader.get(path)
        assert response.status_code == 200, response.text
        assert response.json() == sorted([w["seller"], w["buyer"]])
    assert (await client_b_admin.get(path)).status_code == 404
    assert (await api.get(f"/api/weg-settlements/{uuid.uuid4()}/input-owners")).status_code == 404
    assert (await api.get(f"/api/weg-settlements/{asset.json()['id']}/input-owners")).status_code == 404
    assert (await api.get("/api/weg-settlements/not-a-uuid/input-owners")).status_code == 422
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get(path)).status_code == 401
    assert input_owner_state(w) == before


def bind_input_owner_scope(world, run, *, whole_property):
    scope, grant = str(uuid.uuid4()), str(uuid.uuid4())
    params = {**world, "scope": scope, "grant": grant, "run": run,
              "org": str(world["org_a_id"]), "actor": str(world["user_a_admin_id"]),
              "reader": str(world["user_a_readonly_id"]), "whole": whole_property}
    with psycopg2.connect(MIGRATOR_SYNC_URL) as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO accounting_scope
            (id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by)
            VALUES (%(scope)s,%(org)s,%(property)s,'WEG','T26 input owner scope','2026-01-01',
                    %(whole)s,'REVIEWED',%(actor)s)""", params)
        if not whole_property:
            cur.execute("""INSERT INTO accounting_scope_unit(organization_id,property_id,scope_id,unit_id)
                VALUES (%(org)s,%(property)s,%(scope)s,%(unit)s)""", params)
        # Bind every existing financial source, as the normal reviewed-scope setup does.
        for table, key in (("bank_account", "bank"), ("cost_entry", "cost"),
                           ("settlement_run", "run"), ("economic_plan", "plan")):
            if key == "plan":
                cur.execute("SELECT economic_plan_id FROM settlement_run WHERE id=%(run)s", params)
                params["plan"] = str(cur.fetchone()[0])
            cur.execute(f"""INSERT INTO accounting_scope_binding
                (organization_id,property_id,scope_id,{table}_id,origin,reason,reviewed_by)
                VALUES (%(org)s,%(property)s,%(scope)s,%({key})s,'REVIEWED',
                        'T26 complete synthetic inputs',%(actor)s)""", params)
        cur.execute("""INSERT INTO scope_grant
            (id,organization_id,scope_id,user_id,capabilities,granted_by,reason)
            VALUES (%(grant)s,%(org)s,%(scope)s,%(reader)s,ARRAY['read'],%(actor)s,'T26 explicit read')""", params)
    return scope, grant


async def test_input_owners_exact_partial_scope_period_and_live_grant(client_a_admin, ownership_world):
    w, api = ownership_world, client_a_admin
    run = await input_owner_draft(api, w)
    extra = {key: str(uuid.uuid4()) for key in ("old_person", "old", "outside_person", "outside", "outside_unit")}
    with psycopg2.connect(MIGRATOR_SYNC_URL) as conn, conn.cursor() as cur:
        for owner in ("old", "outside"):
            cur.execute("INSERT INTO person(id,organization_id,first_name,last_name) VALUES (%s,%s,'T26','Excluded')",
                        (extra[owner + "_person"], str(w["org_a_id"])))
            cur.execute("INSERT INTO owner_profile(id,organization_id,person_id) VALUES (%s,%s,%s)",
                        (extra[owner], str(w["org_a_id"]), extra[owner + "_person"]))
        cur.execute("""INSERT INTO unit(id,organization_id,property_id,number,usage_type,area,mea)
            VALUES (%s,%s,%s,'outside','WOHNUNG',100,1000)""",
                    (extra["outside_unit"], str(w["org_a_id"]), w["property"]))
    w.update(extra)
    await create_owner(api, w, "old", valid_from="2025-01-01", valid_to="2025-12-31")
    await create_owner(api, w, "old", valid_from="2027-01-01", valid_to="2027-12-31")
    await create_owner(api, w, "outside", unit_id=w["outside_unit"])
    _, grant = bind_input_owner_scope(w, run, whole_property=False)
    path = f"/api/weg-settlements/{run}/input-owners"
    # Organization admin is not an implicit financial-scope reader.
    assert (await api.get(path)).status_code == 404
    async with ownership_client(w, "user_a_readonly_id") as reader:
        response = await reader.get(path)
        assert response.status_code == 200, response.text
        assert response.json() == sorted([w["seller"], w["buyer"]])
        with psycopg2.connect(MIGRATOR_SYNC_URL) as conn, conn.cursor() as cur:
            cur.execute("UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=%s WHERE id=%s",
                        (str(w["user_a_admin_id"]), grant))
        assert (await reader.get(path)).status_code == 404


async def test_input_owners_requires_complete_live_property_and_contact_sources(client_a_admin, ownership_world):
    from tests.contact_source_fixtures import grant_contact
    from tests.test_property_source_access import grant
    from tests.test_source_security_lifecycle import accepted, status, transition, transition_body

    w, api = ownership_world, client_a_admin
    run = await input_owner_draft(api, w)
    scope, _ = bind_input_owner_scope(w, run, whole_property=True)
    response = await api.post(f"/api/accounting-scopes/{scope}/grants", json={
        "user_id": str(w["user_a_admin_id"]), "capabilities": ["read", "prepare", "confirm", "approve", "export"],
        "reason": "T26 explicit synthetic finance administration",
    }, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 201, response.text
    domain, _ = await grant(api, w, w["property"], caps=["read", "prepare", "export"])

    async def reader_property():
        response = await api.post(f"/api/property-data-domains/{domain}/grants", json={
            "user_id": str(w["user_a_readonly_id"]), "capabilities": ["read"],
            "reason": "T26 explicit input source read",
        }, headers={"Idempotency-Key": str(uuid.uuid4())})
        assert response.status_code == 201, response.text
        return response.json()["id"]

    property_receipt = await reader_property()
    contact_grants = {}
    for owner in ("seller", "buyer"):
        contact_domain, _ = await grant_contact(api, w, w[owner + "_person"], caps=["read"])
        response = await api.post(f"/api/person-data-domains/{contact_domain}/grants", json={
            "user_id": str(w["user_a_readonly_id"]), "capabilities": ["read"], "reason_code": "TEAM_ACCESS",
        }, headers={"Idempotency-Key": str(uuid.uuid4())})
        assert response.status_code == 201, response.text
        contact_grants[owner] = (contact_domain, response.json()["id"])
    decision = await accepted(api)
    response = await transition(api, transition_body(await status(api), "ENFORCED", decision))
    assert response.status_code == 201, response.text
    path = f"/api/weg-settlements/{run}/input-owners"
    async with ownership_client(w, "user_a_readonly_id") as reader:
        response = await reader.get(path)
        assert response.status_code == 200, response.text
        assert response.json() == sorted([w["seller"], w["buyer"]])
        revoked = await api.post(f"/api/property-data-domains/{domain}/grants/{property_receipt}/revoke")
        assert revoked.status_code == 204, revoked.text
        # A visible draft is not authority to leak a shortened list of input owners.
        assert (await reader.get(f"/api/weg-settlements/{run}")).status_code == 200
        assert (await reader.get(path)).status_code == 404
        await reader_property()
        assert (await reader.get(path)).status_code == 200
        contact_domain, receipt = contact_grants["buyer"]
        revoked = await api.post(f"/api/person-data-domains/{contact_domain}/grants/{receipt}/revoke")
        assert revoked.status_code == 204, revoked.text
        assert (await reader.get(path)).status_code == 404
