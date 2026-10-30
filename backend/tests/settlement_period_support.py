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
