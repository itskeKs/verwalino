"""T04: end/notes remain editable; financial history locks monetary changes."""

import uuid
from decimal import Decimal

import psycopg2
import pytest

from tests.conftest import MIGRATOR_SYNC_URL


DEPOSIT_CAP_MESSAGE = "Die Kaution darf höchstens drei Nettokaltmieten betragen (2.250,00 €)."


@pytest.mark.parametrize("usage_type,deposit,rejected", [
    ("WOHNUNG", "2400.00", True), ("WOHNUNG", "2250.01", True),
    ("WOHNUNG", "2250.00", False), ("WOHNUNG", "2249.99", False),
    ("WOHNUNG", None, False), ("GEWERBE", "2400.00", False),
])
def test_pure_deposit_cap_uses_only_residential_net_rent(usage_type, deposit, rejected):
    from app.engine.deposit import validate_deposit

    amount = Decimal(deposit) if deposit is not None else None
    if rejected:
        with pytest.raises(ValueError, match=r"2\.250,00 €") as error:
            validate_deposit(usage_type, Decimal("750.00"), amount)
        assert str(error.value) == DEPOSIT_CAP_MESSAGE
    else:
        validate_deposit(usage_type, Decimal("750.00"), amount)


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
@pytest.mark.parametrize("value", ["750,00", "NaN", "-1", "1e400", "0.001", "1000000000000"])
@pytest.mark.parametrize("field", ["base_rent", "advance_payment", "heating_advance_payment", "deposit"])
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_invalid_lease_amount_is_a_field_validation_error(client_a_admin, lease_seed, value, field, operation):
    if operation == "create":
        response = await client_a_admin.post("/api/leases", json={
            "unit_id": lease_seed["unit"], "tenant_profile_id": lease_seed["tenant"],
            "valid_from": "2025-01-01", "base_rent": "750.00", "advance_payment": "150.00", field: value,
        })
    else:
        lease = await create_lease(client_a_admin, lease_seed)
        response = await client_a_admin.patch(f"/api/leases/{lease['id']}", json={field: value})
    assert response.status_code == 422, response.text
    assert any(error["loc"] == ["body", field] for error in response.json()["detail"])


@pytest.mark.asyncio
async def test_canonical_lease_amounts_round_trip_with_separate_advances_and_zero_deposit(client_a_admin, lease_seed):
    amounts = {"base_rent": "750.00", "advance_payment": "150.00",
               "heating_advance_payment": "90.00", "deposit": "2250.00"}
    lease = await create_lease(client_a_admin, lease_seed, **amounts)
    assert {field: lease[field] for field in amounts} == amounts
    response = await client_a_admin.patch(f"/api/leases/{lease['id']}", json={"deposit": "0.00"})
    assert response.status_code == 200, response.text
    listing = await client_a_admin.get("/api/leases", params={"unit_id": lease_seed["unit"]})
    assert {field: listing.json()[0][field] for field in amounts} == {**amounts, "deposit": "0.00"}


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


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("usage_type,deposit,status", [
    ("WOHNUNG", "2400.00", 422), ("WOHNUNG", "2250.01", 422),
    ("WOHNUNG", "2250.00", 200), ("WOHNUNG", "2249.99", 200),
    ("GEWERBE", "2400.00", 200),
])
async def test_deposit_cap_on_create_and_update_is_atomic(client_a_admin, lease_seed, operation, usage_type, deposit, status):
    with psycopg2.connect(MIGRATOR_SYNC_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE unit SET usage_type=%s WHERE id=%s", (usage_type, lease_seed["unit"]))
    if operation == "create":
        response = await client_a_admin.post("/api/leases", json={
            "unit_id": lease_seed["unit"], "tenant_profile_id": lease_seed["tenant"],
            "valid_from": "2025-01-01", "base_rent": "750.00", "deposit": deposit,
            "advance_payment": "150.00", "heating_advance_payment": "90.00",
        })
        expected_status = 201 if status == 200 else status
    else:
        await create_lease(client_a_admin, lease_seed, base_rent="750.00", deposit="1500.00")
        existing = (await client_a_admin.get("/api/leases", params={"unit_id": lease_seed["unit"]})).json()[0]
        response = await client_a_admin.patch(f"/api/leases/{existing['id']}", json={
            "deposit": deposit, "notes": "Must change only on success", "valid_to": "2025-12-31",
        })
        expected_status = status
    assert response.status_code == expected_status, response.text
    stored = (await client_a_admin.get("/api/leases", params={"unit_id": lease_seed["unit"]})).json()
    if status == 422:
        assert response.json()["detail"] == DEPOSIT_CAP_MESSAGE
        assert stored == ([] if operation == "create" else [existing])
    else:
        assert stored[0]["deposit"] == deposit


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,status", [
    ({"base_rent": "749.99"}, 422),
    ({"base_rent": "750.00"}, 200),
    ({"base_rent": "700.00", "deposit": "2100.00"}, 200),
    ({"base_rent": "800.00", "deposit": "2400.00"}, 200),
])
async def test_lowering_rent_checks_the_resulting_deposit_pair(client_a_admin, lease_seed, changes, status):
    lease = await create_lease(client_a_admin, lease_seed, base_rent="800.00", deposit="2250.00")
    response = await client_a_admin.patch(f"/api/leases/{lease['id']}", json={**changes, "notes": "New note"})
    assert response.status_code == status, response.text
    stored = (await client_a_admin.get("/api/leases", params={"unit_id": lease_seed["unit"]})).json()[0]
    if status == 422:
        assert response.json()["detail"] == "Die Kaution darf höchstens drei Nettokaltmieten betragen (2.249,97 €)."
        assert stored == lease
    else:
        assert stored["base_rent"] == changes["base_rent"]
        assert stored["deposit"] == changes.get("deposit", lease["deposit"])
