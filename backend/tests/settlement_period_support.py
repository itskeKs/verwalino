"""Synthetic period fixtures using only the public HTTP API."""


async def create(client, path, payload):
    answer = await client.post(path, json=payload)
    assert answer.status_code == 201, answer.text
    return answer.json()


async def period_property(client, month=4):
    prop = await create(client, "/api/properties", {
        "name": "Aprilhaus", "management_type": "MISCH", "fiscal_year_start_month": month, "mea_denominator": 1000,
    })
    unit = await create(client, "/api/units", {
        "property_id": prop["id"], "number": "1", "usage_type": "WOHNUNG", "area": "70", "mea": "1000",
    })
    person = await create(client, "/api/persons", {"last_name": "Periodentest"})
    tenant = await create(client, "/api/tenant-profiles", {"person_id": person["id"]})
    owner = await create(client, "/api/owner-profiles", {"person_id": person["id"]})
    await create(client, "/api/ownership", {
        "unit_id": unit["id"], "owner_profile_id": owner["id"], "valid_from": "2020-01-01",
    })
    lease = await create(client, "/api/leases", {
        "unit_id": unit["id"], "tenant_profile_id": tenant["id"], "valid_from": "2020-01-01",
        "base_rent": "700.00", "advance_payment": "100.00",
    })
    account = await create(client, "/api/bank-accounts", {
        "property_id": prop["id"], "account_type": "LAUFEND", "label": "Periodenkonto",
    })
    return {"property": prop, "unit": unit, "lease": lease, "tenant": tenant, "owner": owner, "account": account}


def statement(data, start="2025-04-01", end="2026-03-31"):
    return {"property_id": data["property"]["id"], "lease_agreement_id": data["lease"]["id"],
        "period_start": start, "period_end": end}


async def booked(client, data, year, month, *, hausgeld=False, amount="100.00"):
    payload = {"bank_account_id": data["account"]["id"], "direction": "IN", "amount": amount,
        "value_date": "2026-07-05", "payment_type": "HAUSGELD_VORSCHUSS" if hausgeld else "NK_VORAUSZAHLUNG",
        "period_year": year, "period_month": month}
    if hausgeld:
        payload["owner_profile_id"] = data["owner"]["id"]
    else:
        payload.update(tenant_profile_id=data["tenant"]["id"], lease_agreement_id=data["lease"]["id"])
    payment = await create(client, "/api/payments", payload)
    answer = await client.post(f"/api/payments/{payment['id']}/book")
    assert answer.status_code == 200, answer.text
    return payment


async def finalized_plan(client, data, start, end):
    from tests.financial_coverage_helpers import attest_fixture

    plan = await create(client, "/api/economic-plans", {"property_id": data["property"]["id"],
        "plan_year": int(start[:4]), "period_start": start, "period_end": end})
    inputs = {"planned_costs": [{"category_code": "GARTENPFLEGE", "label": "Garten", "planned_amount": "1200.00"}],
        "reserve_contribution_total": "0.00"}
    await attest_fixture(client, "economic_plan", plan["id"], inputs)
    answer = await client.post(f"/api/economic-plans/{plan['id']}/finalize", json=inputs)
    assert answer.status_code == 200, answer.text
    return answer.json()


async def period_scope(client, data, start, user_id):
    from uuid import uuid4

    answer = await client.post("/api/accounting-scopes", headers={"Idempotency-Key": str(uuid4())},
        json={"property_id": data["property"]["id"], "scope_type": "WEG",
              "label": "Abrechnungskreis", "valid_from": start})
    assert answer.status_code == 201, answer.text
    scope_id = answer.json()["id"]
    answer = await client.post(f"/api/accounting-scopes/{scope_id}/grants",
        headers={"Idempotency-Key": str(uuid4())}, json={"user_id": str(user_id),
            "capabilities": ["read", "prepare"], "reason": "Synthetic period fixture"})
    assert answer.status_code == 201, answer.text
    return scope_id
