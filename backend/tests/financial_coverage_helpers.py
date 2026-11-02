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
        "reserve_override_reason": "Synthetic fixture: reviewed reserve correction",
        "paid_advances": [{"owner_profile_id": seed[key], "paid_total": "0.00"}
                          for key in ("owner_a", "owner_b")]}


async def finalize_weg_fixture(client, subject_id, seed, opening="0.00", withdrawals="0.00"):
    from decimal import Decimal
    values = weg_values(seed, opening, withdrawals)
    path = f"/api/weg-settlements/{subject_id}/reserve-ledger"
    ledger = (await client.get(path)).json()
    delta = Decimal(withdrawals) - Decimal(ledger['withdrawals_total'])
    if delta:
        evidence = {key.removeprefix('reserve_'): value for key, value in (await reserve_evidence(client, seed)).items()}
        response = await client.post(path + '/overrides', json={'component': 'WITHDRAWAL', 'amount': str(delta),
            'reason': 'Synthetic fixture: documented withdrawal correction', **evidence})
        assert response.status_code == 201, response.text
    await attest_fixture(client, "settlement", subject_id, values)
    response = await client.post(f"/api/weg-settlements/{subject_id}/finalize", json=values)
    assert response.status_code == 200, response.text
    return response


async def reserve_evidence(api, w):
    meeting = await api.post('/api/meetings', json={'property_id': w['property'], 'meeting_date': '2025-01-15', 'meeting_type': 'ORDENTLICH'})
    assert meeting.status_code == 201, meeting.text
    assert (await api.post(f"/api/meetings/{meeting.json()['id']}/hold")).status_code == 200
    resolution = await api.post('/api/meetings/resolutions', json={'meeting_id': meeting.json()['id'],
        'wording': 'Repair funded from reserve', 'votes_yes': 2, 'votes_no': 0, 'votes_abstain': 0, 'result': 'ANGENOMMEN'})
    assert resolution.status_code == 201, resolution.text
    register = await api.post(f"/api/meetings/resolutions/{resolution.json()['id']}/register")
    assert register.status_code == 201, register.text
    return {'reserve_cost_entry_id': w['ce_garten'], 'reserve_register_entry_id': register.json()['id']}
