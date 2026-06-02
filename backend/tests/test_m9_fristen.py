"""M9 Fristenmonitor integration tests -- endpoint, assembly, RBAC, isolation.

The deadline arithmetic + classification is unit-tested in tests/engine/
test_fristen.py. Here we test that the service correctly assembles engine inputs
from the DB and that the endpoint is read-only for all roles and tenant-scoped.

Seed: a MIET property with a confirmed 2024 cost entry and a tenant lease ->
a §556 III BK obligation whose deadline (2025-12-31) is OVERDUE at the 2026
reference date.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal

import pytest

SYNC_URL = os.environ.get(
    "HV_DATABASE_URL_SYNC",
    "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
)

REF = "2026-06-02"


@pytest.fixture(scope="session")
def m9_fristen_seed(seed_data):
    import psycopg2

    org_a = str(seed_data["org_a_id"])
    ids = {
        "prop": uuid.uuid4(),
        "unit": uuid.uuid4(),
        "person": uuid.uuid4(),
        "tenant_profile": uuid.uuid4(),
        "lease": uuid.uuid4(),
        "cost_entry": uuid.uuid4(),
    }

    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1"
        )
        cost_category_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
            (str(ids["prop"]), org_a, "Fristenhaus", "MIET"),
        )
        cur.execute(
            "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, heated_area, mea) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["unit"]), org_a, str(ids["prop"]), "F-1", "WOHNUNG", 75, 75, 500),
        )
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
            (str(ids["person"]), org_a, "Frieda", "Frist"),
        )
        cur.execute(
            "INSERT INTO tenant_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["tenant_profile"]), org_a, str(ids["person"])),
        )
        # Open-ended lease from 2024 -> overlaps the 2024 fiscal year.
        cur.execute(
            "INSERT INTO lease_agreement (id, organization_id, unit_id, tenant_profile_id, "
            "valid_from, valid_to, base_rent, advance_payment) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["lease"]), org_a, str(ids["unit"]), str(ids["tenant_profile"]),
             date(2024, 1, 1), None, Decimal("600.00"), Decimal("120.00")),
        )
        # Confirmed cost entry for the 2024 fiscal year.
        cur.execute(
            "INSERT INTO cost_entry (id, organization_id, property_id, cost_category_id, "
            "cost_category_plan_version, invoice_date, service_period_from, service_period_to, "
            "gross_amount, tenant_apportionable, status, source, confirmed_at, confirmed_by) "
            "VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,'UMLAGEFAEHIG','confirmed','MANUAL',now(),%s)",
            (str(ids["cost_entry"]), org_a, str(ids["prop"]), str(cost_category_id),
             date(2024, 6, 1), date(2024, 1, 1), date(2024, 12, 31),
             Decimal("900.00"), str(seed_data["user_a_admin_id"])),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {**seed_data, **{k: str(v) for k, v in ids.items()}}


def _bk_item(items):
    return next(
        (
            i
            for i in items
            if i["kind"] == "BK_ABRECHNUNG" and "Fristenhaus" in i["reference"]
        ),
        None,
    )


class TestFristenmonitor:
    @pytest.mark.asyncio
    async def test_bk_obligation_is_overdue(self, client_a_admin, m9_fristen_seed):
        r = await client_a_admin.get("/api/fristen", params={"reference_date": REF})
        assert r.status_code == 200, r.text
        item = _bk_item(r.json())
        assert item is not None, "expected a BK obligation for Fristenhaus"
        assert item["reference"] == "Fristenhaus -- BK 2024"
        assert item["due_date"] == "2025-12-31"  # §556 III: period end + 12 months
        assert item["status"] == "OVERDUE"
        assert item["is_statutory"] is True
        assert item["is_provisional"] is False

    @pytest.mark.asyncio
    async def test_default_reference_date(self, client_a_admin, m9_fristen_seed):
        r = await client_a_admin.get("/api/fristen")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @pytest.mark.asyncio
    async def test_nachrichtlich_items_present(self, client_a_admin, m9_fristen_seed):
        r = await client_a_admin.get("/api/fristen", params={"reference_date": REF})
        kinds = {i["kind"] for i in r.json()}
        assert "OFFENE_POSTEN" in kinds
        assert "BELEG_AUFBEWAHRUNG" in kinds

    @pytest.mark.asyncio
    async def test_readonly_can_read(self, client_a_readonly, m9_fristen_seed):
        r = await client_a_readonly.get("/api/fristen", params={"reference_date": REF})
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_unauthenticated_rejected(self, client_unauth, m9_fristen_seed):
        r = await client_unauth.get("/api/fristen")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_cross_org_isolation(self, client_b_admin, m9_fristen_seed):
        """Org B's monitor must not contain org A's Fristenhaus obligation."""
        r = await client_b_admin.get("/api/fristen", params={"reference_date": REF})
        assert r.status_code == 200
        assert _bk_item(r.json()) is None
