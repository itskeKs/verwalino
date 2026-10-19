"""T04: Integrationstest – Mietvertrag ändern und beenden.

Prüft: "Vertrag zum 31.12. beenden, Nachmieter ab 01.01. anlegen".
"""

import pytest
from tests.test_lease_update import lease_seed


@pytest.mark.asyncio
async def test_patch_lease_ending(client_a_admin, lease_seed):
    """T04: Beende einen Vertrag zum 31.12. und lege einen Nachmieter ab 01.01. an."""
    unit_id = lease_seed["unit"]
    tenant_profile_id = lease_seed["tenant"]

    # 1. Erstelle einen Vertrag (unbefristet)
    resp = await client_a_admin.post(
        "/api/leases",
        json={
            "unit_id": unit_id,
            "tenant_profile_id": tenant_profile_id,
            "valid_from": "2025-01-01",
            "valid_to": None,  # unbefristet
            "base_rent": "800.00",
            "advance_payment": "0.00",
            "notes": "Erstmieter",
        },
    )

    assert resp.status_code == 201, f"Vertragserstellung fehlgeschlagen: {resp.json()}"
    lease = resp.json()
    assert lease["valid_to"] is None
    assert lease["notes"] == "Erstmieter"
    lease_id = lease["id"]

    # 2. Beende den Vertrag zum 31.12.
    resp = await client_a_admin.patch(
        f"/api/leases/{lease_id}",
        json={
            "valid_to": "2025-12-31",
            "notes": "Beendet zum 31.12.",
        },
    )

    assert resp.status_code == 200, f"Vertragsaktualisierung fehlgeschlagen: {resp.json()}"
    updated = resp.json()
    # Die API zeigt den inklusiven letzten Miettag an.
    assert updated["valid_to"] == "2025-12-31"
    assert updated["notes"] == "Beendet zum 31.12."

    # 3. Lege einen Nachmieter ab 01.01. an (Überlappung prüfen)
    new_tenant_profile_id = lease_seed["next_tenant"]
    resp = await client_a_admin.post(
        "/api/leases",
        json={
            "unit_id": unit_id,
            "tenant_profile_id": new_tenant_profile_id,
            "valid_from": "2026-01-01",
            "valid_to": None,
            "base_rent": "850.00",
            "advance_payment": "0.00",
            "notes": "Nachmieter",
        },
    )

    # Der erste Vertrag endet vor dem 01.01.2026: keine Überlappung.
    assert resp.status_code == 201, f"Nachmieter-Vertrag fehlgeschlagen: {resp.json()}"
    assert resp.json()["tenant_profile_id"] == new_tenant_profile_id
    assert resp.json()["valid_from"] == "2026-01-01"
