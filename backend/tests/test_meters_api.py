"""T29 meter endpoints: authentication, roles and tenant-scoped data."""
from __future__ import annotations

import uuid
from contextlib import closing

import psycopg2
import pytest

from tests.conftest import MIGRATOR_SYNC_URL


@pytest.mark.asyncio
async def test_meter_list_requires_authentication(client_unauth):
    response = await client_unauth.get("/api/meters")
    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_meter_list_is_available_to_authenticated_members(client_a_admin):
    response = await client_a_admin.get("/api/meters")
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_meter_creation_rejects_readonly_member(client_a_readonly):
    response = await client_a_readonly.post("/api/meters", json={
        "property_id": str(uuid.uuid4()),
        "meter_number": "T29-READONLY",
        "meter_type": "KALTWASSER",
        "location": "Synthetischer Testkeller",
    })
    assert response.status_code == 403, response.text


@pytest.fixture
def meter_world(seed_data):
    ids = {key: str(uuid.uuid4()) for key in (
        "property", "other_property", "foreign_property", "unit", "other_unit", "foreign_unit",
    )}
    with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as conn:
        with conn, conn.cursor() as cur:
            for key, org in (("property", "org_a_id"), ("other_property", "org_a_id"),
                             ("foreign_property", "org_b_id")):
                cur.execute("INSERT INTO property(id,organization_id,name,management_type) VALUES (%s,%s,%s,'MIET')",
                            (ids[key], str(seed_data[org]), f"T29 {ids[key]}"))
            for unit, prop, org in (("unit", "property", "org_a_id"),
                                    ("other_unit", "other_property", "org_a_id"),
                                    ("foreign_unit", "foreign_property", "org_b_id")):
                cur.execute("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (%s,%s,%s,'1','WOHNUNG')",
                            (ids[unit], str(seed_data[org]), ids[prop]))
    return ids


def meter_body(world, **changes):
    return {"property_id": world["property"], "meter_number": "T29-WATER",
            "meter_type": "KALTWASSER", "location": "Testkeller", **changes}


async def new_meter(client, world, **changes):
    response = await client.post("/api/meters", json=meter_body(world, **changes))
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_meter_crud_filters_and_bidirectional_org_isolation(client_a_admin, client_b_admin, meter_world):
    w = meter_world
    common = await new_meter(client_a_admin, w)
    unit = await new_meter(client_a_admin, w, unit_id=w["unit"], meter_number="UNIT")
    foreign = await new_meter(client_b_admin, w, property_id=w["foreign_property"], unit_id=w["foreign_unit"])
    own = await client_a_admin.get("/api/meters", params={"property_id": w["property"]})
    assert own.status_code == 200
    assert {row["id"] for row in own.json()} == {common["id"], unit["id"]}
    selected = await client_a_admin.get("/api/meters", params={"unit_id": w["unit"]})
    assert selected.status_code == 200
    assert [row["id"] for row in selected.json()] == [unit["id"]]
    for client, hidden in ((client_a_admin, foreign), (client_b_admin, unit)):
        assert hidden["id"] not in {row["id"] for row in (await client.get("/api/meters")).json()}
        for suffix in ("", "/readings"):
            response = await client.get(f"/api/meters/{hidden['id']}{suffix}")
            assert response.status_code == 404, response.text
        assert (await client.patch(f"/api/meters/{hidden['id']}", json={"location": "denied"})).status_code == 404
        assert (await client.post(f"/api/meters/{hidden['id']}/readings", json={"reading_date": "2026-01-01", "value": "1"})).status_code == 404


@pytest.mark.asyncio
async def test_meter_readings_are_exact_ordered_append_only_and_survive_patch(client_a_admin, meter_world):
    meter = await new_meter(client_a_admin, meter_world, unit_id=meter_world["unit"])
    path = f"/api/meters/{meter['id']}"
    expected = []
    for day, value, formatted in (("2026-12-31", "123.4567", "123.4567"),
                                  ("2026-01-01", "0", "0.0000"),
                                  ("2026-06-01", "1.2", "1.2000")):
        response = await client_a_admin.post(path + "/readings", json={"reading_date": day, "value": value, "period": "2026"})
        assert response.status_code == 201, response.text
        row = response.json()
        assert row["meter_id"] == meter["id"] and row["value"] == formatted
        expected.append(row)
    expected.sort(key=lambda row: (row["reading_date"], row["id"]))
    assert (await client_a_admin.get(path + "/readings")).json() == expected
    patched = await client_a_admin.patch(path, json={"meter_number": "UPDATED", "meter_type": "WARMWASSER", "location": "Bad"})
    assert patched.status_code == 200, patched.text
    for key in ("id", "property_id", "unit_id"):
        assert patched.json()[key] == meter[key]
    assert patched.json()["location"] == "Bad"
    assert (await client_a_admin.get(path + "/readings")).json() == expected
    duplicate = await client_a_admin.post(path + "/readings", json={"reading_date": "2026-01-01", "value": "2.0000"})
    assert duplicate.status_code == 201, duplicate.text
    assert len((await client_a_admin.get(path + "/readings")).json()) == 4


@pytest.mark.asyncio
async def test_meter_refuses_a_reading_below_its_preceding_reading(client_a_admin, meter_world):
    meter = await new_meter(client_a_admin, meter_world, unit_id=meter_world['unit'])
    path = f"/api/meters/{meter['id']}/readings"
    first = await client_a_admin.post(path, json={'reading_date': '2026-01-01', 'value': '100'})
    assert first.status_code == 201, first.text
    response = await client_a_admin.post(path, json={'reading_date': '2026-01-02', 'value': '99.9999'})
    assert response.status_code == 422, response.text
    assert 'Zählerstand' in response.text
    assert len((await client_a_admin.get(path)).json()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"meter_number": " "}, {"meter_number": "x" * 101}, {"meter_type": ""},
    {"meter_type": "x" * 51}, {"location": "x" * 256},
    {"organization_id": str(uuid.uuid4())}, {"unknown": True},
])
async def test_meter_invalid_creation_is_rejected(client_a_admin, meter_world, changes):
    response = await client_a_admin.post("/api/meters", json=meter_body(meter_world, **changes))
    assert response.status_code == 422, response.text
    assert (await client_a_admin.get("/api/meters", params={"property_id": meter_world["property"]})).json() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"meter_number": None}, {"meter_type": None}, {"location": None},
    {"meter_number": " "}, {"property_id": str(uuid.uuid4())},
    {"unit_id": None}, {"organization_id": str(uuid.uuid4())},
])
async def test_meter_invalid_patch_preserves_original(client_a_admin, meter_world, changes):
    meter = await new_meter(client_a_admin, meter_world)
    path = f"/api/meters/{meter['id']}"
    response = await client_a_admin.patch(path, json=changes)
    assert response.status_code == 422, response.text
    assert (await client_a_admin.get(path)).json() == meter


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["-0.0001", "NaN", "Infinity", "abc", "1.00001", "10000000000.0000", "1e1000"])
async def test_reading_invalid_value_never_persists(client_a_admin, meter_world, value):
    meter = await new_meter(client_a_admin, meter_world)
    path = f"/api/meters/{meter['id']}/readings"
    response = await client_a_admin.post(path, json={"reading_date": "2026-01-01", "value": value})
    assert response.status_code == 422, response.text
    assert (await client_a_admin.get(path)).json() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"reading_date": "2026-02-30"}, {"period": "x" * 51},
    {"meter_id": str(uuid.uuid4())}, {"organization_id": str(uuid.uuid4())},
])
async def test_reading_invalid_fields_never_persist(client_a_admin, meter_world, changes):
    meter = await new_meter(client_a_admin, meter_world)
    path = f"/api/meters/{meter['id']}/readings"
    response = await client_a_admin.post(path, json={"reading_date": "2026-01-01", "value": "1.0000", **changes})
    assert response.status_code == 422, response.text
    assert (await client_a_admin.get(path)).json() == []


@pytest.mark.asyncio
async def test_meter_parent_coherence_and_readonly_access(client_a_admin, client_a_readonly, client_a_clerk, meter_world):
    w = meter_world
    for changes, status in (({"unit_id": w["other_unit"]}, 422),
                            ({"unit_id": w["foreign_unit"]}, 404),
                            ({"property_id": w["foreign_property"]}, 404),
                            ({"property_id": str(uuid.uuid4())}, 404)):
        response = await client_a_admin.post("/api/meters", json=meter_body(w, **changes))
        assert response.status_code == status, response.text
    meter = await new_meter(client_a_clerk, w, unit_id=w["unit"])
    path = f"/api/meters/{meter['id']}"
    assert (await client_a_readonly.get(path)).status_code == 200
    assert (await client_a_readonly.get(path + "/readings")).json() == []
    assert (await client_a_readonly.patch(path, json={"location": "blocked"})).status_code == 403
    assert (await client_a_readonly.post(path + "/readings", json={"reading_date": "2026-01-01", "value": "1"})).status_code == 403
    reading = await client_a_clerk.post(path + "/readings", json={"reading_date": "2026-01-01", "value": "9999999999.9999"})
    assert reading.status_code == 201, reading.text
    assert reading.json()["value"] == "9999999999.9999"


@pytest.mark.asyncio
async def test_meter_reading_committed_before_http_success(client_a_admin, meter_world):
    import httpx
    from app.main import app

    meter = await new_meter(client_a_admin, meter_world)
    observed = []

    async def observe_response(scope, receive, send):
        async def observe_send(message):
            if message["type"] == "http.response.start":
                with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as conn, conn.cursor() as cur:
                    cur.execute("SELECT count(*) FROM meter_reading WHERE meter_id=%s AND reading_date='2026-01-01'",
                                (meter["id"],))
                    observed.append(cur.fetchone()[0])
            await send(message)
        await app(scope, receive, observe_send)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=observe_response),
                                base_url="http://test", cookies=client_a_admin.cookies) as api:
        response = await api.post(f"/api/meters/{meter['id']}/readings",
                                  json={"reading_date": "2026-01-01", "value": "12.3456"})
    assert response.status_code == 201, response.text
    assert observed == [1], "Reading must commit before HTTP success is sent"
