"""T23 / RF06–RF08: effective-dated rent history through the real lease API.

Each test gets fresh organizations, properties, people and API-created leases.
No shared get-by-person fixture, protection bypass or engine replacement is used.
The new model/repository are deliberately not imported: before implementation the
first API assertion must fail on the missing endpoint, not during collection.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.core.security import create_session_token
from app.main import app
from tests.conftest import MIGRATOR_URL
from tests.financial_coverage_helpers import attest_fixture

pytestmark = pytest.mark.asyncio


def api_client(world, actor="admin", *, foreign=False):
    org = world["foreign_org"] if foreign else world["org"]
    user = world["foreign_admin"] if foreign else world[actor]
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        cookies={settings.session_cookie_name: create_session_token(user, org)},
    )


def history_url(world, lease="lease"):
    return f"/api/leases/{world[lease]}/rent-changes"


def change(valid_from="2026-03-15", base_rent="750.00", advance_payment="150.00"):
    return {"valid_from": valid_from, "base_rent": base_rent,
            "advance_payment": advance_payment}


async def post_change(api, world, *, lease="lease", **values):
    response = await api.post(history_url(world, lease), json=change(**values))
    assert response.status_code == 201, response.text
    row = response.json()
    assert uuid.UUID(row["id"])
    assert row["lease_agreement_id"] == str(world[lease])
    for field, value in change(**values).items():
        assert row[field] == value
    return row


async def statement(api, world, start="2026-01-01", end="2026-04-30"):
    response = await api.get(
        f"/api/payments/mietkonto/lease/{world['lease']}",
        params={"period_start": start, "period_end": end},
    )
    assert response.status_code == 200, response.text
    return response.json()


def expected_rows(body):
    rows = body["open_items"]
    result = {(row["period_year"], row["period_month"], row["payment_type"]):
              Decimal(row["soll"]) for row in rows}
    assert len(result) == len(rows), "Exactly one monthly Soll per payment type"
    return result


@pytest_asyncio.fixture
async def rent_db(seed_data):
    # MIGRATOR_URL is admitted by conftest's disposable-database guard; no fallback URL.
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            yield db
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def rent_world(rent_db):
    world = {key: uuid.uuid4() for key in (
        "org", "foreign_org", "admin", "reader", "clerk", "foreign_admin",
        "property", "hidden_property", "foreign_property", "unit", "hidden_unit",
        "foreign_unit", "person", "hidden_person", "foreign_person", "tenant",
        "hidden_tenant", "foreign_tenant",
    )}
    for org in ("org", "foreign_org"):
        await rent_db.execute(text("INSERT INTO organization(id,name) VALUES (:id,'T23 synthetic')"),
                              {"id": world[org]})
    for actor, org, role in (
        ("admin", "org", "OWNER_ADMIN"), ("reader", "org", "READ_ONLY"),
        ("clerk", "org", "CLERK"), ("foreign_admin", "foreign_org", "OWNER_ADMIN"),
    ):
        params = {"id": world[actor], "org": world[org], "role": role,
                  "email": f"{world[actor]}@rent-change.invalid"}
        await rent_db.execute(text('''INSERT INTO "user"
            (id,organization_id,email,password_hash,display_name)
            VALUES (:id,:org,:email,'unused-synthetic-password','T23')'''), params)
        await rent_db.execute(text("""INSERT INTO membership(organization_id,user_id,role)
            VALUES (:org,:id,:role)"""), params)
    for prefix, org in (("", "org"), ("hidden_", "org"), ("foreign_", "foreign_org")):
        params = {"org": world[org], **{key: world[prefix + key]
                  for key in ("property", "unit", "person", "tenant")}}
        await rent_db.execute(text("""INSERT INTO property(id,organization_id,name,management_type)
            VALUES (:property,:org,'T23 isolated rental','MIET')"""), params)
        await rent_db.execute(text("""INSERT INTO unit
            (id,organization_id,property_id,number,usage_type,area,heated_area,mea)
            VALUES (:unit,:org,:property,'1','WOHNUNG',100,100,1000)"""), params)
        await rent_db.execute(text("""INSERT INTO person(id,organization_id,first_name,last_name)
            VALUES (:person,:org,'Synthetic','T23 tenant')"""), params)
        await rent_db.execute(text("""INSERT INTO tenant_profile(id,organization_id,person_id)
            VALUES (:tenant,:org,:person)"""), params)
    await rent_db.commit()
    async with api_client(world) as api, api_client(world, foreign=True) as foreign:
        for prefix, client in (("", api), ("hidden_", api), ("foreign_", foreign)):
            response = await client.post("/api/leases", json={
                "unit_id": str(world[prefix + "unit"]),
                "tenant_profile_id": str(world[prefix + "tenant"]),
                "valid_from": "2026-01-01", "base_rent": "700.00",
                "advance_payment": "150.00", "notes": "T23 isolated original",
            })
            assert response.status_code == 201, response.text
            world[prefix + "lease"] = uuid.UUID(response.json()["id"])
    return world


async def test_empty_history_for_existing_lease(rent_world):
    async with api_client(rent_world) as api:
        response = await api.get(history_url(rent_world))
    assert response.status_code == 200, response.text
    assert response.json() == []


async def test_history_sorted_persistent_after_new_request_and_original_unchanged(rent_world, rent_db):
    w = rent_world
    async with api_client(w) as api:
        april = await post_change(api, w, valid_from="2026-04-01", base_rent="760.00")
        march = await post_change(api, w)
        january = await post_change(api, w, valid_from="2026-01-01", base_rent="710.00")
    # A new client/request and a separate database transaction, not cached ORM state.
    async with api_client(w) as reloaded:
        response = await reloaded.get(history_url(w))
        assert response.status_code == 200, response.text
        assert response.json() == [january, march, april]
        leases = await reloaded.get("/api/leases", params={"unit_id": str(w["unit"])})
        assert leases.status_code == 200, leases.text
        assert len(leases.json()) == 1
        original = leases.json()[0]
        assert original["base_rent"] == "700.00"
        assert original["advance_payment"] == "150.00"
    stored = (await rent_db.execute(text("""SELECT valid_from,base_rent,advance_payment
        FROM lease_rent_change WHERE organization_id=:org AND lease_agreement_id=:lease
        ORDER BY valid_from"""), w)).all()
    assert stored == [
        (date(2026, 1, 1), Decimal("710.00"), Decimal("150.00")),
        (date(2026, 3, 15), Decimal("750.00"), Decimal("150.00")),
        (date(2026, 4, 1), Decimal("760.00"), Decimal("150.00")),
    ]


async def test_real_mietkonto_mid_march_increase_and_later_query_carries_history(rent_world):
    w = rent_world
    async with api_client(w) as api:
        await post_change(api, w)
    async with api_client(w) as reloaded:
        result = await statement(reloaded, w)
        assert expected_rows(result) == {
            (2026, 1, "KALTMIETE"): Decimal("700.00"),
            (2026, 1, "NK_VORAUSZAHLUNG"): Decimal("150.00"),
            (2026, 2, "KALTMIETE"): Decimal("700.00"),
            (2026, 2, "NK_VORAUSZAHLUNG"): Decimal("150.00"),
            (2026, 3, "KALTMIETE"): Decimal("727.42"),
            (2026, 3, "NK_VORAUSZAHLUNG"): Decimal("150.00"),
            (2026, 4, "KALTMIETE"): Decimal("750.00"),
            (2026, 4, "NK_VORAUSZAHLUNG"): Decimal("150.00"),
        }
        assert result["soll_total"] == "3477.42"
        assert result["ist_total"] == "0.00"
        assert result["balance"] == "3477.42"
        march = await statement(reloaded, w, "2026-03-01", "2026-03-31")
        assert march["soll_total"] == "877.42"
        assert expected_rows(march) == {
            (2026, 3, "KALTMIETE"): Decimal("727.42"),
            (2026, 3, "NK_VORAUSZAHLUNG"): Decimal("150.00"),
        }
        april = await statement(reloaded, w, "2026-04-01", "2026-04-30")
        assert expected_rows(april) == {
            (2026, 4, "KALTMIETE"): Decimal("750.00"),
            (2026, 4, "NK_VORAUSZAHLUNG"): Decimal("150.00"),
        }
        assert april["soll_total"] == "900.00"
        partial = await statement(reloaded, w, "2026-03-10", "2026-03-20")
        assert expected_rows(partial) == {
            (2026, 3, "KALTMIETE"): Decimal("258.06"),
            (2026, 3, "NK_VORAUSZAHLUNG"): Decimal("53.23"),
        }
        assert partial["soll_total"] == "311.29"


async def test_multiple_changes_decrease_advance_and_repeat_are_not_deltas(rent_world):
    w = rent_world
    async with api_client(w) as api:
        # Deliberately insert out of effective-date order, including equal successive rates.
        await post_change(api, w, valid_from="2026-04-01", base_rent="650.00", advance_payment="120.00")
        await post_change(api, w)
        await post_change(api, w, valid_from="2026-05-01", base_rent="650.00", advance_payment="120.00")
        await post_change(api, w, valid_from="2026-03-20", base_rent="600.00", advance_payment="180.00")
        first = await statement(api, w, "2026-03-01", "2026-05-31")
        second = await statement(api, w, "2026-03-01", "2026-05-31")
        assert first == second
        assert expected_rows(first) == {
            (2026, 3, "KALTMIETE"): Decimal("669.35"),
            (2026, 3, "NK_VORAUSZAHLUNG"): Decimal("161.61"),
            (2026, 4, "KALTMIETE"): Decimal("650.00"),
            (2026, 4, "NK_VORAUSZAHLUNG"): Decimal("120.00"),
            (2026, 5, "KALTMIETE"): Decimal("650.00"),
            (2026, 5, "NK_VORAUSZAHLUNG"): Decimal("120.00"),
        }
        assert first["soll_total"] == "2370.96"


@pytest.mark.parametrize("field,value", [
    ("valid_from", "not-a-date"), ("valid_from", "2026-02-30"),
    ("valid_from", "2025-12-31"), ("valid_from", None),
    ("base_rent", "NaN"), ("base_rent", "Infinity"), ("base_rent", "-Infinity"),
    ("base_rent", "-0.01"), ("base_rent", "750.001"),
    ("advance_payment", "NaN"), ("advance_payment", "Infinity"),
    ("advance_payment", "-0.01"), ("advance_payment", "150.001"),
    ("base_rent", "1000000000000.00"), ("advance_payment", "1000000000000.00"),
    ("base_rent", None), ("advance_payment", None),
])
async def test_invalid_date_and_money_rejected_without_history(rent_world, field, value):
    async with api_client(rent_world) as api:
        response = await api.post(history_url(rent_world), json={**change(), field: value})
        assert response.status_code == 422, response.text
        response = await api.get(history_url(rent_world))
        assert response.status_code == 200, response.text
        assert response.json() == []


@pytest.mark.parametrize("field", ["valid_from", "base_rent", "advance_payment"])
async def test_all_rate_fields_and_effective_date_required(rent_world, field):
    body = change()
    del body[field]
    async with api_client(rent_world) as api:
        response = await api.post(history_url(rent_world), json=body)
        assert response.status_code == 422, response.text


@pytest.mark.parametrize("field", ["id", "organization_id", "lease_agreement_id", "lease_id"])
async def test_client_cannot_override_identity_or_organization(rent_world, field):
    async with api_client(rent_world) as api:
        response = await api.post(history_url(rent_world), json={**change(), field: str(uuid.uuid4())})
        assert response.status_code == 422, response.text
        history = await api.get(history_url(rent_world))
        assert history.status_code == 200, history.text
        assert history.json() == []


async def test_lease_start_last_occupied_day_zero_and_far_future_are_valid(rent_world):
    w = rent_world
    async with api_client(w) as api:
        ended = await api.patch(f"/api/leases/{w['lease']}", json={"valid_to": "2026-06-30"})
        assert ended.status_code == 200, ended.text
        await post_change(api, w, valid_from="2026-01-01", base_rent="0.00", advance_payment="0.00")
        await post_change(api, w, valid_from="2026-06-30")
        # API lease ends inclusively June 30; storage valid_to is July 1 (exclusive).
        response = await api.post(history_url(w), json=change(valid_from="2026-07-01"))
        assert response.status_code == 422, response.text
        response = await api.post(history_url(w), json=change(valid_from="2026-07-02"))
        assert response.status_code == 422, response.text
        await post_change(api, w, lease="hidden_lease", valid_from="2099-01-01")


async def test_duplicate_effective_date_conflicts_and_history_is_append_only(rent_world):
    w = rent_world
    async with api_client(w) as api:
        original = await post_change(api, w)
        for body in (change(), change(base_rent="900.00", advance_payment="10.00")):
            response = await api.post(history_url(w), json=body)
            assert response.status_code == 409, response.text
        # No mutation endpoints are offered, either on the collection or on a row.
        for path in (history_url(w), f"{history_url(w)}/{original['id']}"):
            for method in ("PATCH", "DELETE"):
                response = await api.request(method, path, json=change(base_rent="999.00"))
                assert response.status_code in (404, 405), response.text
        history = await api.get(history_url(w))
        assert history.status_code == 200, history.text
        assert history.json() == [original]
        # The same date on another lease is not a duplicate.
        await post_change(api, w, lease="hidden_lease")


async def test_concurrent_duplicate_has_exactly_one_created_row(rent_world, rent_db):
    w = rent_world
    async with api_client(w) as first, api_client(w) as second:
        responses = await asyncio.gather(
            first.post(history_url(w), json=change()),
            second.post(history_url(w), json=change()),
        )
        assert sorted(response.status_code for response in responses) == [201, 409], [
            response.text for response in responses]
        created = next(response.json() for response in responses if response.status_code == 201)
        response = await first.get(history_url(w))
        assert response.status_code == 200, response.text
        assert response.json() == [created]
    audit = (await rent_db.execute(text("""SELECT actor_user_id,payload_diff FROM audit_event
        WHERE organization_id=:org AND entity_type='lease_agreement' AND entity_id=:lease
          AND action='lease.rent_change_created'"""), w)).all()
    assert len(audit) == 1
    assert audit[0].actor_user_id == w["admin"]
    assert audit[0].payload_diff == {"rent_change_id": created["id"], **change()}


async def test_authentication_reader_and_foreign_lease_non_disclosure(rent_world):
    w = rent_world
    async with api_client(w) as admin:
        row = await post_change(admin, w)
        for lease in (uuid.uuid4(), w["foreign_lease"]):
            path = f"/api/leases/{lease}/rent-changes"
            assert (await admin.get(path)).status_code == 404
            assert (await admin.post(path, json=change())).status_code == 404
        for method in ("GET", "POST"):
            response = await admin.request(method, "/api/leases/not-a-uuid/rent-changes",
                                           **({"json": change()} if method == "POST" else {}))
            assert response.status_code == 422, response.text
    async with api_client(w, "reader") as reader:
        response = await reader.get(history_url(w))
        assert response.status_code == 200, response.text
        assert response.json() == [row]
        response = await reader.post(history_url(w), json=change(valid_from="2026-04-01"))
        assert response.status_code == 403, response.text
    async with api_client(w, foreign=True) as foreign:
        assert (await foreign.get(history_url(w))).status_code == 404
        assert (await foreign.post(history_url(w), json=change())).status_code == 404
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get(history_url(w))).status_code == 401
        assert (await anon.post(history_url(w), json=change())).status_code == 401


async def test_database_forces_rls_and_rejects_cross_org_parent_even_for_migrator(rent_world, rent_db, app_rw_session):
    w = rent_world
    async with api_client(w) as api:
        own = await post_change(api, w)
    async with api_client(w, foreign=True) as foreign:
        other = await post_change(foreign, w, lease="foreign_lease")
    flags = (await rent_db.execute(text("""SELECT relrowsecurity,relforcerowsecurity
        FROM pg_class WHERE oid='lease_rent_change'::regclass"""))).one()
    assert flags == (True, True)
    rw = app_rw_session
    assert await rw.scalar(text("SELECT current_user")) == "app_rw"
    await rw.execute(text("""SELECT set_config('app.current_org_id',:org,true),
        set_config('app.current_user_id',:actor,true)"""),
        {"org": str(w["org"]), "actor": str(w["admin"])})
    visible = (await rw.execute(text("SELECT id FROM lease_rent_change"))).scalars().all()
    assert visible == [uuid.UUID(own["id"])]
    assert uuid.UUID(other["id"]) not in visible
    with pytest.raises(DBAPIError) as cross_org:
        async with rent_db.begin_nested():
            await rent_db.execute(text("""INSERT INTO lease_rent_change
                (id,organization_id,lease_agreement_id,valid_from,base_rent,advance_payment)
                VALUES (:id,:org,:foreign_lease,'2026-04-01',1,1)"""),
                {**w, "id": uuid.uuid4()})
            await rent_db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert getattr(cross_org.value.orig, "sqlstate", None) == "23503"


async def test_property_scope_follows_parent_lease_without_enforcement_bypass(rent_world, rent_db, app_rw_session):
    from tests.test_source_security_lifecycle import accepted, status, transition, transition_body

    w = rent_world
    async with api_client(w) as admin:
        own = await post_change(admin, w)
        hidden = await post_change(admin, w, lease="hidden_lease")
        domains = {}
        for prop in ("property", "hidden_property"):
            response = await admin.post("/api/property-data-domains", json={
                "property_id": str(w[prop]), "label": "T23 property audience",
                "reason": "Explicit synthetic property rights",
            }, headers={"Idempotency-Key": str(uuid.uuid4())})
            assert response.status_code == 201, response.text
            domains[prop] = response.json()["id"]
            response = await admin.post(f"/api/property-data-domains/{domains[prop]}/grants", json={
                "user_id": str(w["admin"]), "capabilities": ["read", "prepare", "export"],
                "reason": "Explicit parent lease access",
            }, headers={"Idempotency-Key": str(uuid.uuid4())})
            assert response.status_code == 201, response.text
        # The clerk can prepare only on the first property; the reader only reads it.
        for actor, caps in (("clerk", ["read", "prepare"]), ("reader", ["read"])):
            response = await admin.post(f"/api/property-data-domains/{domains['property']}/grants", json={
                "user_id": str(w[actor]), "capabilities": caps,
                "reason": "T23 restricted property audience",
            }, headers={"Idempotency-Key": str(uuid.uuid4())})
            assert response.status_code == 201, response.text
        # Lease preparation also requires a readable tenant contact; grant it normally.
        for person in ("person", "hidden_person"):
            response = await admin.post("/api/person-data-domains", json={
                "person_id": str(w[person]), "reason_code": "INITIAL_SETUP",
            }, headers={"Idempotency-Key": str(uuid.uuid4())})
            assert response.status_code == 201, response.text
            domain = response.json()["id"]
            actors = ("admin", "clerk", "reader") if person == "person" else ("admin",)
            for actor in actors:
                response = await admin.post(f"/api/person-data-domains/{domain}/grants", json={
                    "user_id": str(w[actor]), "capabilities": ["read"], "reason_code": "TEAM_ACCESS",
                }, headers={"Idempotency-Key": str(uuid.uuid4())})
                assert response.status_code == 201, response.text
        decision = await accepted(admin)
        activated = await transition(admin, transition_body(await status(admin), "ENFORCED", decision))
        assert activated.status_code == 201, activated.text
    async with api_client(w, "reader") as reader:
        response = await reader.get(history_url(w))
        assert response.status_code == 200, response.text
        assert response.json() == [own]
        assert (await reader.post(history_url(w), json=change(valid_from="2026-04-01"))).status_code == 403
    async with api_client(w, "clerk") as clerk:
        # Prove the parent is actually hidden, not merely an empty child history.
        parent = await clerk.get("/api/leases", params={"unit_id": str(w["hidden_unit"])})
        assert parent.status_code == 200, parent.text
        assert parent.json() == []
        assert (await clerk.get(history_url(w, "hidden_lease"))).status_code == 404
        assert (await clerk.post(history_url(w, "hidden_lease"), json=change(valid_from="2026-04-01"))).status_code == 404
        clerk_row = await post_change(clerk, w, valid_from="2026-04-01", base_rent="760.00")
    rw = app_rw_session
    await rw.execute(text("""SELECT set_config('app.current_org_id',:org,true),
        set_config('app.current_user_id',:actor,true)"""),
        {"org": str(w["org"]), "actor": str(w["clerk"])})
    assert await rw.scalar(text("SELECT count(*) FROM lease_agreement WHERE id=:id"),
                           {"id": w["hidden_lease"]}) == 0
    rows = (await rw.execute(text("SELECT id FROM lease_rent_change"))).scalars().all()
    assert set(rows) == {uuid.UUID(own["id"]), uuid.UUID(clerk_row["id"])}
    assert uuid.UUID(hidden["id"]) not in rows
    with pytest.raises(DBAPIError):
        async with rw.begin_nested():
            await rw.execute(text("""INSERT INTO lease_rent_change
                (id,organization_id,lease_agreement_id,valid_from,base_rent,advance_payment)
                VALUES (:id,:org,:hidden_lease,'2026-04-01',1,1)"""),
                {**w, "id": uuid.uuid4()})


async def test_rent_change_leaves_booked_payments_and_finalized_snapshot_untouched(rent_world, rent_db):
    w = rent_world
    async with api_client(w) as api:
        response = await api.get(history_url(w))
        assert response.status_code == 200, response.text
        assert response.json() == []
    bank = uuid.uuid4()
    await rent_db.execute(text("""INSERT INTO bank_account
        (id,organization_id,property_id,account_type,label)
        VALUES (:id,:org,:property,'LAUFEND','T23 payment preservation')"""), {**w, "id": bank})
    await rent_db.execute(text("""INSERT INTO cost_entry
        (id,organization_id,property_id,cost_category_id,cost_category_plan_version,
         invoice_date,service_period_from,service_period_to,gross_amount,
         tenant_distribution_key,tenant_apportionable,status,source,confirmed_at,confirmed_by)
        SELECT :id,:org,:property,id,1,'2026-04-01','2026-01-01','2026-04-30',1200,
               'WOHNFLAECHE','UMLAGEFAEHIG','confirmed','MANUAL',now(),:admin
        FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1"""),
        {**w, "id": uuid.uuid4()})
    await rent_db.commit()
    async with api_client(w) as api:
        booked_ids = []
        for kind, amount in (("KALTMIETE", "700.00"), ("NK_VORAUSZAHLUNG", "150.00")):
            response = await api.post("/api/payments", json={
                "bank_account_id": str(bank), "direction": "IN", "amount": amount,
                "value_date": "2026-03-05", "payment_type": kind,
                "tenant_profile_id": str(w["tenant"]), "lease_agreement_id": str(w["lease"]),
                "period_year": 2026, "period_month": 3,
            })
            assert response.status_code == 201, response.text
            payment_id = response.json()["id"]
            response = await api.post(f"/api/payments/{payment_id}/book")
            assert response.status_code == 200, response.text
            booked_ids.append(payment_id)
        response = await api.post("/api/settlements", json={
            "property_id": str(w["property"]), "lease_agreement_id": str(w["lease"]),
            "period_start": "2026-01-01", "period_end": "2026-04-30",
            "advance_payments_total": "150.00",
        })
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        await attest_fixture(api, "settlement", run_id, {
            "advance_payments_total": "150.00", "advance_payments_source": "MANUAL",
        })
        response = await api.post(f"/api/settlements/{run_id}/finalize", json={})
        assert response.status_code == 200, response.text
        finalized = response.json()
        assert finalized["status"] == "finalized"
        assert finalized["snapshot_sha256"]
        before = (await rent_db.execute(text("""SELECT to_jsonb(p) FROM payment p
            WHERE organization_id=:org AND lease_agreement_id=:lease ORDER BY id"""), w)).scalars().all()
        original_lease = await rent_db.scalar(text("SELECT to_jsonb(l) FROM lease_agreement l WHERE id=:lease"), w)
        snapshot = await rent_db.scalar(text("SELECT to_jsonb(r) FROM settlement_run r WHERE id=:id"),
                                        {"id": uuid.UUID(run_id)})
        await rent_db.commit()
        await post_change(api, w)
        duplicate = await api.post(history_url(w), json=change())
        assert duplicate.status_code == 409, duplicate.text
        response = await api.get(f"/api/settlements/{run_id}")
        assert response.status_code == 200, response.text
        assert response.json() == finalized
        result = await statement(api, w, "2026-03-01", "2026-03-31")
        assert result["soll_total"] == "877.42"
        assert result["ist_total"] == "850.00"
        assert result["balance"] == "27.42"
    after = (await rent_db.execute(text("""SELECT to_jsonb(p) FROM payment p
        WHERE organization_id=:org AND lease_agreement_id=:lease ORDER BY id"""), w)).scalars().all()
    assert after == before
    assert {row["id"] for row in after} == set(booked_ids)
    assert all(row["status"] == "booked" and row["reverses_id"] is None
               and row["reversed_by_id"] is None for row in after)
    assert await rent_db.scalar(text("SELECT to_jsonb(r) FROM settlement_run r WHERE id=:id"),
                               {"id": uuid.UUID(run_id)}) == snapshot

    assert await rent_db.scalar(text("SELECT to_jsonb(l) FROM lease_agreement l WHERE id=:lease"), w) == original_lease


async def test_database_history_is_append_only_and_money_checks_are_hard(rent_world, rent_db, app_rw_session):
    w = rent_world
    async with api_client(w) as api:
        row = await post_change(api, w)
    params = {**w, "id": uuid.UUID(row["id"])}
    # Even a privileged connection cannot silently rewrite retained history.
    for sql in (
        "UPDATE lease_rent_change SET base_rent=999 WHERE id=:id",
        "DELETE FROM lease_rent_change WHERE id=:id",
    ):
        with pytest.raises(DBAPIError):
            async with rent_db.begin_nested():
                await rent_db.execute(text(sql), params)
    for base, advance in (("-0.01", "150"), ("700", "-0.01"), ("NaN", "150"), ("700", "NaN")):
        with pytest.raises(DBAPIError) as invalid_money:
            async with rent_db.begin_nested():
                await rent_db.execute(text("""INSERT INTO lease_rent_change
                    (id,organization_id,lease_agreement_id,valid_from,base_rent,advance_payment)
                    VALUES (:id,:org,:lease,'2026-04-01',CAST(:base AS numeric),CAST(:advance AS numeric))"""),
                    {**w, "id": uuid.uuid4(), "base": base, "advance": advance})
        assert getattr(invalid_money.value.orig, "sqlstate", None) == "23514"
    columns = (await rent_db.execute(text("""SELECT column_name,numeric_precision,numeric_scale
        FROM information_schema.columns WHERE table_schema='public' AND table_name='lease_rent_change'
        AND column_name IN ('base_rent','advance_payment') ORDER BY column_name"""))).all()
    assert columns == [("advance_payment", 14, 2), ("base_rent", 14, 2)]
    rw = app_rw_session
    await rw.execute(text("""SELECT set_config('app.current_org_id',:org,true),
        set_config('app.current_user_id',:actor,true)"""),
        {"org": str(w["org"]), "actor": str(w["admin"])})
    for sql in (
        "UPDATE lease_rent_change SET advance_payment=999 WHERE id=:id",
        "DELETE FROM lease_rent_change WHERE id=:id",
    ):
        # A revoked SQL privilege/denying policy or an immutability trigger are all
        # valid protections, but a successful mutation is never accepted.
        try:
            async with rw.begin_nested():
                result = await rw.execute(text(sql), params)
                assert result.rowcount == 0
        except DBAPIError:
            pass
    async with api_client(w) as api:
        response = await api.get(history_url(w))
        assert response.status_code == 200, response.text
        assert response.json() == [row]


async def test_bank_matching_and_booking_use_effective_march_soll(rent_world, rent_db):
    w = rent_world
    async with api_client(w) as api:
        await post_change(api, w)
        bank, transaction = uuid.uuid4(), uuid.uuid4()
        await rent_db.execute(text("""INSERT INTO bank_account
            (id,organization_id,property_id,account_type,label)
            VALUES (:id,:org,:property,'LAUFEND','T23 changed rent')"""), {**w, "id": bank})
        await rent_db.execute(text("""INSERT INTO bank_transaction
            (id,organization_id,bank_account_id,booking_date,value_date,amount,
             counterparty_name,purpose,import_hash,status)
            VALUES (:id,:org,:bank,'2026-03-20','2026-03-20',877.42,
                    'Synthetic T23 tenant','Warmmiete 03/2026',:hash,'NEU')"""),
            {**w, "id": transaction, "bank": bank, "hash": transaction.hex})
        await rent_db.commit()
        response = await api.get("/api/bank-transactions", params={"bank_account_id": str(bank)})
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == 1 and items[0]["id"] == str(transaction)
        suggestions = items[0]["suggestions"]
        assert len(suggestions) == 1
        lines = suggestions[0]["lines"]
        assert {line["payment_type"]: line["amount"] for line in lines} == {
            "KALTMIETE": "727.42", "NK_VORAUSZAHLUNG": "150.00",
        }
        assert all(line["lease_agreement_id"] == str(w["lease"])
                   and line["period_year"] == 2026 and line["period_month"] == 3 for line in lines)
        response = await api.post(f"/api/bank-transactions/{transaction}/assign", json={"lines": [
            {"lease_agreement_id": str(w["lease"]), "payment_type": kind,
             "amount": amount, "period_year": 2026, "period_month": 3}
            for kind, amount in (("KALTMIETE", "727.42"), ("NK_VORAUSZAHLUNG", "150.00"))
        ]})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ZUGEORDNET"
        assert len(response.json()["payments"]) == 2
        result = await statement(api, w, "2026-03-01", "2026-03-31")
        assert result["soll_total"] == "877.42"
        assert result["ist_total"] == "877.42"
        assert result["balance"] == "0.00"


async def test_rent_change_waits_for_financial_input_lock(rent_world, rent_db):
    w = rent_world
    await rent_db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                          {"key": f"financial-inputs:{w['org']}"})
    blocker = await rent_db.scalar(text("SELECT pg_backend_pid()"))
    async with api_client(w) as api:
        request = asyncio.create_task(api.post(history_url(w), json=change()))
        waiting = False
        try:
            for _ in range(200):
                waiting = await rent_db.scalar(text("""SELECT EXISTS (
                    SELECT 1 FROM pg_locks l WHERE l.locktype='advisory'
                    AND NOT l.granted AND :blocker = ANY(pg_blocking_pids(l.pid)))"""),
                    {"blocker": blocker})
                if waiting or request.done():
                    break
                await asyncio.sleep(0.01)
        finally:
            await rent_db.rollback()
            response = await asyncio.wait_for(request, timeout=15)
        assert response.status_code == 201, response.text
        assert waiting, "Rent changes must wait for the shared financial-input boundary"
