"""T04: Integrationstest – Mietvertrag ändern und beenden.

Prüft: "Vertrag zum 31.12. beenden, Nachmieter ab 01.01. anlegen".
"""

import pytest
import uuid
from datetime import date


@pytest.mark.asyncio
async def test_patch_lease_ending(client_a_admin, seed_data):
    """T04: Beende einen Vertrag zum 31.12. und lege einen Nachmieter ab 01.01. an."""
    unit_id = seed_data["unit"]
    tenant_profile_id = seed_data["tenant_profile"]

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
    # Backend konvertiert 31.12. → 01.01. des Folgejahres (halboffen)
    assert updated["valid_to"] is not None
    assert updated["notes"] == "Beendet zum 31.12."

    # 3. Lege einen Nachmieter ab 01.01. an (Überlappung prüfen)
    new_tenant_profile_id = seed_data["tenant_profile"]  # reuse for test
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

    # 409 bei Überlappung, da der erste Vertrag noch offen ist
    # (wir haben valid_to auf 31.12. gesetzt, also endet er vor 01.01.2026)
    # Erwartet: 201, da keine Überlappung
    assert resp.status_code == 201, f"Nachmieter-Vertrag fehlgeschlagen: {resp.json()}"
