"""Explicit evidence for synthetic positive accounting fixtures."""
async def attest_fixture(client, kind, subject_id, values):
    response = await client.post("/api/financial-input-coverage", json={
        "subject_kind": kind, "subject_id": subject_id, "values": values,
        "confirmed_complete": True, "reason": "Synthetic fixture: complete period records checked",
        "source_document_ids": [],
    })
    assert response.status_code == 201, response.text
    return response.json()


def weg_values(seed, opening="0.00", withdrawals="0.00"):
    return {"reserve_opening_balance": opening, "reserve_withdrawals_total": withdrawals,
        "paid_advances": [{"owner_profile_id": seed[key], "paid_total": "0.00"}
                          for key in ("owner_a", "owner_b")]}


async def finalize_weg_fixture(client, subject_id, seed, opening="0.00", withdrawals="0.00"):
    values = weg_values(seed, opening, withdrawals)
    await attest_fixture(client, "settlement", subject_id, values)
    response = await client.post(f"/api/weg-settlements/{subject_id}/finalize", json=values)
    assert response.status_code == 200, response.text
    return response
