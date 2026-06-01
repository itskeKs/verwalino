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
