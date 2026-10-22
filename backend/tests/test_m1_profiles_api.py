"""M1 additive tests -- profile/vendor API endpoints + update endpoints for person/unit.

Tests the additively created endpoints:
  - POST/GET /api/owner-profiles
  - POST/GET /api/tenant-profiles
  - POST/GET /api/vendors
  - PUT /api/persons/{id}
  - PUT /api/units/{id}
"""

from __future__ import annotations

import pytest
from tests.test_rent_changes import rent_db, rent_world, api_client  # noqa: F401


# ---------------------------------------------------------------------------
# Person update
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_person_update(client_a_admin):
    # Create a person
    resp = await client_a_admin.post("/api/persons", json={
        "first_name": "Udo",
        "last_name": "Testmann",
    })
    assert resp.status_code == 201
    pid = resp.json()["id"]

    # Update
    resp = await client_a_admin.put(f"/api/persons/{pid}", json={
        "last_name": "Geaendert",
        "iban": "DE89370400440532013000",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["last_name"] == "Geaendert"
    assert data["iban"] == "DE89370400440532013000"
    assert data["first_name"] == "Udo"  # unchanged


# ---------------------------------------------------------------------------
# Unit update
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unit_update(client_a_admin):
    # Create property
    resp = await client_a_admin.post("/api/properties", json={
        "name": "Test Haus Update",
        "management_type": "MIET",
        "mea_denominator": 1_000,
    })
    assert resp.status_code == 201
    prop_id = resp.json()["id"]

    # Create unit
    resp = await client_a_admin.post("/api/units", json={
        "property_id": prop_id,
        "number": "EG-99",
        "usage_type": "WOHNUNG",
        "area": "75.50",
    })
    assert resp.status_code == 201
    unit_id = resp.json()["id"]

    # Update
    resp = await client_a_admin.put(f"/api/units/{unit_id}", json={
        "number": "EG-99a",
        "area": "80.00",
        "mea": "250.000000",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["number"] == "EG-99a"
    assert data["area"] == "80.00"
    assert data["mea"] == "250.000000"


# ---------------------------------------------------------------------------
# Owner profile CRUD
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_owner_profile_crud(client_a_admin):
    # Create a person first
    resp = await client_a_admin.post("/api/persons", json={
        "first_name": "Eigent",
        "last_name": "Uemer",
    })
    assert resp.status_code == 201
    person_id = resp.json()["id"]

    # Create owner profile
    resp = await client_a_admin.post("/api/owner-profiles", json={
        "person_id": person_id,
        "notes": "Haupteigentuemer",
    })
    assert resp.status_code == 201
    profile = resp.json()
    assert profile["person_id"] == person_id
    assert profile["notes"] == "Haupteigentuemer"

    # List all
    resp = await client_a_admin.get("/api/owner-profiles")
    assert resp.status_code == 200
    profiles = resp.json()
    assert any(p["id"] == profile["id"] for p in profiles)

    # Filter by person
    resp = await client_a_admin.get(f"/api/owner-profiles?person_id={person_id}")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


# ---------------------------------------------------------------------------
# Tenant profile CRUD
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tenant_profile_crud(client_a_admin):
    resp = await client_a_admin.post("/api/persons", json={
        "first_name": "Miet",
        "last_name": "Erin",
    })
    assert resp.status_code == 201
    person_id = resp.json()["id"]

    resp = await client_a_admin.post("/api/tenant-profiles", json={
        "person_id": person_id,
    })
    assert resp.status_code == 201
    profile = resp.json()
    assert profile["person_id"] == person_id

    resp = await client_a_admin.get("/api/tenant-profiles")
    assert resp.status_code == 200
    assert any(p["id"] == profile["id"] for p in resp.json())


# ---------------------------------------------------------------------------
# Vendor CRUD
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_vendor_crud(client_a_admin):
    resp = await client_a_admin.post("/api/persons", json={
        "first_name": "Dienst",
        "last_name": "Leister",
        "company_name": "Klempner GmbH",
    })
    assert resp.status_code == 201
    person_id = resp.json()["id"]

    resp = await client_a_admin.post("/api/vendors", json={
        "person_id": person_id,
        "tax_id": "12345/67890",
        "vat_id": "DE123456789",
    })
    assert resp.status_code == 201
    vendor = resp.json()
    assert vendor["person_id"] == person_id
    assert vendor["tax_id"] == "12345/67890"

    resp = await client_a_admin.get("/api/vendors")
    assert resp.status_code == 200
    assert any(v["id"] == vendor["id"] for v in resp.json())


# ---------------------------------------------------------------------------
# Tenant isolation: Org B cannot see Org A profiles
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_profile_tenant_isolation(client_a_admin, client_b_admin):
    # Create person + owner profile in org A
    resp = await client_a_admin.post("/api/persons", json={
        "first_name": "Iso",
        "last_name": "Lation",
    })
    person_id = resp.json()["id"]

    resp = await client_a_admin.post("/api/owner-profiles", json={
        "person_id": person_id,
    })
    assert resp.status_code == 201
    profile_id = resp.json()["id"]

    # Org B should not see it
    resp = await client_b_admin.get("/api/owner-profiles")
    assert resp.status_code == 200
    assert not any(p["id"] == profile_id for p in resp.json())


@pytest.mark.asyncio
async def test_t55_vendor_edit_end_and_identity(client_a_admin, client_a_readonly, client_b_admin):
    person = await client_a_admin.post('/api/persons', json={'last_name': 'T55 Werkstatt'})
    assert person.status_code == 201
    created = await client_a_admin.post('/api/vendors', json={'person_id': person.json()['id'], 'notes': 'Alt'})
    assert created.status_code == 201
    vendor = created.json()
    url = f"/api/vendors/{vendor['id']}"
    changed = await client_a_admin.put(url, json={'tax_id': 'TEST-55', 'notes': 'Neu', 'is_active': False})
    assert changed.status_code == 200, changed.text
    listed = await client_a_admin.get('/api/vendors', params={'person_id': person.json()['id']})
    saved = next(row for row in listed.json() if row['id'] == vendor['id'])
    assert saved['person_id'] == vendor['person_id']
    assert (saved['tax_id'], saved['notes'], saved['is_active']) == ('TEST-55', 'Neu', False)
    assert (await client_a_readonly.put(url, json={'notes': 'no'})).status_code == 403
    assert (await client_b_admin.put(url, json={'notes': 'no'})).status_code == 404
    assert (await client_a_admin.put(url, json={'person_id': person.json()['id']})).status_code == 422


@pytest.mark.asyncio
async def test_t55_management_mandate_edit_and_end(client_a_admin, client_a_readonly):
    created = await client_a_admin.post('/api/mandates', json={'mandate_type': 'MIET', 'label': 'T55 Alt', 'start_date': '2026-01-01'})
    assert created.status_code == 201, created.text
    original = created.json()
    url = f"/api/mandates/{original['id']}"
    changed = await client_a_admin.patch(url, json={'label': 'T55 Neu', 'end_date': '2026-10-31'})
    assert changed.status_code == 200, changed.text
    saved = (await client_a_admin.get(url)).json()
    assert saved['label'] == 'T55 Neu'
    assert saved['end_date'] == '2026-10-31'
    assert saved['start_date'] == original['start_date']
    assert (await client_a_admin.patch(url, json={'end_date': '2025-12-31'})).status_code == 422
    assert (await client_a_readonly.patch(url, json={'label': 'no'})).status_code == 403
    assert (await client_a_admin.get(url)).json() == saved


@pytest.mark.asyncio
async def test_t55_property_optional_building_fields_persist_and_clear(client_a_admin):
    created = await client_a_admin.post('/api/properties', json={'name': 'T55 Haus', 'management_type': 'MIET', 'construction_year': 1984, 'total_area': '1234.56'})
    assert created.status_code == 201, created.text
    prop = created.json()
    assert prop['construction_year'] == 1984
    assert prop['total_area'] == '1234.56'
    changed = await client_a_admin.put(f"/api/properties/{prop['id']}", json={'construction_year': 1990, 'total_area': '2345.67'})
    assert changed.status_code == 200, changed.text
    saved = (await client_a_admin.get(f"/api/properties/{prop['id']}")).json()
    assert (saved['construction_year'], saved['total_area']) == (1990, '2345.67')
    cleared = await client_a_admin.put(f"/api/properties/{prop['id']}", json={'construction_year': None, 'total_area': None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()['construction_year'] is None
    assert cleared.json()['total_area'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('fields', [{'construction_year': 0}, {'construction_year': 10000}, {'construction_year': True}, {'total_area': 'NaN'}, {'total_area': 'Infinity'}, {'total_area': '-1'}, {'total_area': '1.001'}, {'total_area': 1.5}])
async def test_t55_property_rejects_invalid_building_values(client_a_admin, fields):
    response = await client_a_admin.post('/api/properties', json={'name': 'T55 Ungültig', 'management_type': 'MIET', **fields})
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_t55_vendor_read_only_contact_grant_is_not_prepare(client_a_admin, client_a_clerk, seed_data):
    from tests.test_person_source_access import contact, audience
    from tests.test_property_source_enforcement import synthetic_enforcement
    person = await contact(client_a_admin)
    created = await client_a_admin.post('/api/vendors', json={'person_id': person, 'notes': 'Original source'})
    assert created.status_code == 201, created.text
    vendor_id = created.json()['id']
    async with synthetic_enforcement(seed_data):
        domain, grant_id = await audience(client_a_admin, person, seed_data['user_a_clerk_id'], ('read',))
        visible = await client_a_clerk.get('/api/vendors', params={'person_id': person})
        assert visible.status_code == 200 and len(visible.json()) == 1
        refused = await client_a_clerk.put(f'/api/vendors/{vendor_id}', json={'notes': 'Unauthorized'})
        assert refused.status_code == 404, refused.text
        assert (await client_a_admin.post(f'/api/person-data-domains/{domain}/grants/{grant_id}/revoke')).status_code == 204
        assert (await client_a_clerk.put(f'/api/vendors/{vendor_id}', json={'is_active': False})).status_code == 404
    saved = next(row for row in (await client_a_admin.get('/api/vendors', params={'person_id': person})).json() if row['id'] == vendor_id)
    assert saved['notes'] == 'Original source' and saved['is_active'] is True


@pytest.mark.asyncio
async def test_t55_bound_mandate_label_only_preserves_financial_period(rent_world, rent_db):
    import uuid
    from sqlalchemy import text
    w = rent_world
    async with api_client(w) as api:
        created = await api.post('/api/mandates', json={'mandate_type': 'MIET', 'label': 'T55 Gebunden', 'start_date': '2026-01-01'})
        assert created.status_code == 201, created.text
        mandate_id = created.json()['id']
    await rent_db.execute(text("""INSERT INTO accounting_scope(id,organization_id,property_id,management_mandate_id,
        scope_type,label,valid_from,valid_to,whole_property,origin,created_by)
        VALUES (:id,:org,:property,:mandate,'MIET','T55 protected period','2026-01-01','2026-12-31',true,'REVIEWED',:admin)"""),
        {**w, 'id': uuid.uuid4(), 'mandate': uuid.UUID(mandate_id)})
    await rent_db.commit()
    async with api_client(w) as api:
        url = f'/api/mandates/{mandate_id}'
        renamed = await api.patch(url, json={'label': 'T55 Nur Name'})
        assert renamed.status_code == 200, renamed.text
        original = (await api.get(url)).json()
        refused = await api.patch(url, json={'end_date': '2026-10-31'})
        assert refused.status_code == 409, refused.text
        assert (await api.get(url)).json() == original


@pytest.mark.asyncio
async def test_t55_live_role_downgrade_blocks_property_and_mandate_edits(rent_world, rent_db):
    from sqlalchemy import text
    w = rent_world
    async with api_client(w) as api:
        created = await api.post('/api/mandates', json={'mandate_type': 'MIET', 'label': 'T55 Live', 'start_date': '2026-01-01'})
        assert created.status_code == 201, created.text
        url = f"/api/mandates/{created.json()['id']}"
        await rent_db.execute(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:admin"), w)
        await rent_db.commit()
        assert (await api.patch(url, json={'label': 'No longer authorized'})).status_code == 403
        assert (await api.put(f"/api/properties/{w['property']}", json={'construction_year': 1984})).status_code == 403
        assert (await api.get(url)).json()['label'] == 'T55 Live'
