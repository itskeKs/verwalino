"""T04: end/notes remain editable; financial history locks monetary changes."""

import uuid

import psycopg2
import pytest

from tests.conftest import MIGRATOR_SYNC_URL


@pytest.fixture
def lease_seed(seed_data):
    ids = {key: str(uuid.uuid4()) for key in (
        "property", "unit", "person", "tenant", "next_person", "next_tenant", "bank",
    )}
    org = str(seed_data["org_a_id"])
    with psycopg2.connect(MIGRATOR_SYNC_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO property (id,organization_id,name,management_type) VALUES (%s,%s,'T04','MIET')", (ids["property"], org))
            cur.execute("INSERT INTO unit (id,organization_id,property_id,number,usage_type,area) VALUES (%s,%s,%s,'1','WOHNUNG',75)", (ids["unit"], org, ids["property"]))
            for person, tenant in (("person", "tenant"), ("next_person", "next_tenant")):
                cur.execute("INSERT INTO person (id,organization_id,first_name,last_name) VALUES (%s,%s,'Test','Mieter')", (ids[person], org))
                cur.execute("INSERT INTO tenant_profile (id,organization_id,person_id) VALUES (%s,%s,%s)", (ids[tenant], org, ids[person]))
            cur.execute("INSERT INTO bank_account (id,organization_id,property_id,account_type,label) VALUES (%s,%s,%s,'LAUFEND','T04')", (ids["bank"], org, ids["property"]))
    return {**seed_data, **ids}


async def create_lease(client, seed, **changes):
    body = {"unit_id": seed["unit"], "tenant_profile_id": seed["tenant"],
            "valid_from": "2025-01-01", "base_rent": "500.00",
            "advance_payment": "100.00", "notes": "Erhalten"}
    body.update(changes)
    response = await client.post("/api/leases", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def book_payment(seed, lease_id):
    with psycopg2.connect(MIGRATOR_SYNC_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO payment
                (id,organization_id,bank_account_id,property_id,direction,amount,value_date,
                 payment_type,tenant_profile_id,lease_agreement_id,period_year,period_month,
                 status,booked_at,booked_by)
                VALUES (%s,%s,%s,%s,'IN',500,'2025-01-05','KALTMIETE',%s,%s,2025,1,'booked',now(),%s)""",
                (str(uuid.uuid4()), str(seed["org_a_id"]), seed["bank"], seed["property"],
                 seed["tenant"], lease_id, str(seed["user_a_admin_id"])))


@pytest.mark.asyncio
async def test_end_booked_lease_and_create_successor(client_a_admin, lease_seed):
    lease = await create_lease(client_a_admin, lease_seed)
    book_payment(lease_seed, lease["id"])
    response = await client_a_admin.patch(f"/api/leases/{lease['id']}", json={"valid_to": "2025-12-31"})
    assert response.status_code == 200, response.text
    assert response.json()["valid_to"] == "2025-12-31"
    assert response.json()["notes"] == "Erhalten"
    successor = await create_lease(client_a_admin, lease_seed,
        tenant_profile_id=lease_seed["next_tenant"], valid_from="2026-01-01")
    assert successor["valid_from"] == "2026-01-01"
    listing = await client_a_admin.get("/api/leases", params={"unit_id": lease_seed["unit"]})
    assert listing.status_code == 200, listing.text
    assert len(listing.json()) == 2


@pytest.mark.asyncio
async def test_notes_remain_editable_after_booking(client_a_admin, lease_seed):
    lease = await create_lease(client_a_admin, lease_seed)
    book_payment(lease_seed, lease["id"])
    response = await client_a_admin.patch(f"/api/leases/{lease['id']}", json={"notes": "Gekuendigt"})
    assert response.status_code == 200, response.text
    assert response.json()["notes"] == "Gekuendigt"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["base_rent", "advance_payment", "heating_advance_payment", "deposit"])
async def test_booked_lease_rejects_amount_changes(client_a_admin, lease_seed, field):
    lease = await create_lease(client_a_admin, lease_seed)
    book_payment(lease_seed, lease["id"])
    response = await client_a_admin.patch(f"/api/leases/{lease['id']}", json={field: "999.00"})
    assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_patch_rejects_overlapping_extension(client_a_admin, lease_seed):
    lease = await create_lease(client_a_admin, lease_seed, valid_to="2025-12-31")
    await create_lease(client_a_admin, lease_seed,
        tenant_profile_id=lease_seed["next_tenant"], valid_from="2026-01-01")
    response = await client_a_admin.patch(f"/api/leases/{lease['id']}", json={"valid_to": "2026-01-31"})
    assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_unbooked_amount_update_preserves_notes(client_a_admin, lease_seed):
    lease = await create_lease(client_a_admin, lease_seed)
    response = await client_a_admin.patch(f"/api/leases/{lease['id']}", json={"base_rent": "550.00"})
    assert response.status_code == 200, response.text
    assert response.json()["base_rent"] == "550.00"
    assert response.json()["notes"] == "Erhalten"


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['base_rent', 'heating_advance_payment'])
async def test_finalized_lease_locks_amounts_but_not_end(client_a_admin, lease_seed, field):
    lease = await create_lease(client_a_admin, lease_seed)
    with psycopg2.connect(MIGRATOR_SYNC_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO settlement_run
                (id,organization_id,property_id,lease_agreement_id,run_type,period_start,period_end,
                 advance_payments_source,status,snapshot_sha256,engine_version,finalized_at)
                VALUES (%s,%s,%s,%s,'BK_MIETER','2025-01-01','2025-12-31',
                        'MANUAL','finalized',%s,'m3.1.0',now())""",
                (str(uuid.uuid4()), str(lease_seed["org_a_id"]), lease_seed["property"], lease["id"], "a" * 64))
    response = await client_a_admin.patch(f"/api/leases/{lease['id']}", json={field: "550.00"})
    assert response.status_code == 409, response.text
    response = await client_a_admin.patch(f"/api/leases/{lease['id']}", json={"valid_to": "2025-12-31"})
    assert response.status_code == 200, response.text
    assert response.json()["valid_to"] == "2025-12-31"
