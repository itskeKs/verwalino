"""T12 Journey test: Mietjahr full flow.

Creates a rental property with units, tenants, lease agreements, 11 of 12
months paid, confirmed costs, settlement run with derived advances, PDF,
one month rent arrears balance, and a dunning notice with PDF.

Uses the same session-scoped fixtures as other journey tests (seed_data,
client_a_admin, app_rw_session, migrator_session).
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone as _tz
from decimal import Decimal

import pytest


SYNC_URL = os.environ.get(
    "HV_DATABASE_URL_SYNC",
    "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
)


# ===========================================================================
# Seed: A full rental property for org_a with 1 unit, 1 tenant, 1 lease.
# 11 of 12 months are paid (Jan–Nov), December is unpaid -> arrears.
# ===========================================================================
@pytest.fixture(scope="session")
def t12_seed(seed_data):
    import psycopg2

    org = str(seed_data["org_a_id"])
    ids = {
        "property": uuid.uuid4(),
        "unit": uuid.uuid4(),
        "person_tenant": uuid.uuid4(),
        "tenant_profile": uuid.uuid4(),
        "lease": uuid.uuid4(),
        "bank_account": uuid.uuid4(),
    }

    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()

        # Property (rental)
        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
            (str(ids["property"]), org, "Miethaus T12", "MIET"),
        )

        # Unit
        cur.execute(
            "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, heated_area, mea) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["unit"]), org, str(ids["property"]), "1", "WOHNUNG", 75, 75, 400),
        )

        # Tenant person + profile
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name, street, zip_code, city) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["person_tenant"]), org, "Max", "Mieter", "Mietstraße 1", "12345", "Mieterstadt"),
        )
        cur.execute(
            "INSERT INTO tenant_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["tenant_profile"]), org, str(ids["person_tenant"])),
        )

        # Lease: base_rent 500, NK advance 100. Full year 2025, open-ended.
        cur.execute(
            "INSERT INTO lease_agreement (id, organization_id, unit_id, tenant_profile_id, "
            "valid_from, valid_to, base_rent, advance_payment) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["lease"]), org, str(ids["unit"]), str(ids["tenant_profile"]),
             date(2025, 1, 1), None, Decimal("500.00"), Decimal("100.00")),
        )

        # Bank account
        cur.execute(
            "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
            "VALUES (%s,%s,%s,%s,%s)",
            (str(ids["bank_account"]), org, str(ids["property"]), "LAUFEND", "Konto T12"),
        )

        # Payments: 11 months (Jan–Nov) fully paid (500 rent + 100 NK = 600 each).
        # December NOT paid -> 600 arrears.
        for month in range(1, 12):  # 1 to 11
            for ptype, amt in [("KALTMIETE", "500.00"), ("NK_VORAUSZAHLUNG", "100.00")]:
                pid = uuid.uuid4()
                cur.execute(
                    "INSERT INTO payment (id, organization_id, bank_account_id, property_id, "
                    "direction, amount, value_date, payment_type, tenant_profile_id, "
                    "lease_agreement_id, period_year, period_month, status, booked_at, booked_by) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (str(pid), org, str(ids["bank_account"]), str(ids["property"]),
                     "IN", amt, date(2025, month, 5), ptype, str(ids["tenant_profile"]),
                     str(ids["lease"]), 2025, month, "booked",
                     datetime(2025, month, 5, 10, 0, tzinfo=_tz.utc),
                     str(seed_data["user_a_admin_id"])),
                )

        # Get cost category IDs (must exist in migration)
        def cat(code):
            cur.execute(
                "SELECT id FROM cost_category WHERE code=%s AND plan_version=1", (code,)
            )
            return cur.fetchone()[0]

        # Confirmed cost entries (active chain) for 2025.
        def add_cost(code, gross, key, appo, appo_amount=None):
            ce = uuid.uuid4()
            cur.execute(
                "INSERT INTO cost_entry (id, organization_id, property_id, cost_category_id, cost_category_plan_version, "
                "invoice_date, service_period_from, service_period_to, gross_amount, tenant_distribution_key, "
                "tenant_apportionable, apportionable_tenant_amount, status, source, confirmed_at, confirmed_by) "
                "VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,'confirmed','MANUAL',now(),%s)",
                (
                    str(ce), org, str(ids["property"]), str(cat(code)),
                    date(2025, 6, 1), date(2025, 1, 1), date(2025, 12, 31),
                    gross, key, appo, appo_amount, str(seed_data["user_a_admin_id"]),
                ),
            )
            return ce

        ids["ce_grundsteuer"] = add_cost("GRUNDSTEUER", Decimal("2400.00"), "WOHNFLAECHE", "UMLAGEFAEHIG")
        ids["ce_muell"] = add_cost("MUELLBESEITIGUNG", Decimal("480.00"), "EINHEIT", "UMLAGEFAEHIG")
        ids["ce_wasser"] = add_cost("KALTWASSER", Decimal("600.00"), "VERBRAUCH", "UMLAGEFAEHIG")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {**seed_data, **{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in ids.items()}}


# ===========================================================================
# Journey test: Full rental year flow
# ===========================================================================
class TestJourneyMiete:
    @pytest.mark.asyncio
    async def test_full_rental_year_flow(self, client_a_admin, t12_seed):
        """Create property, units, tenant, lease, 11/12 payments, costs,
        settlement with derived advances, PDF, one month arrears, dunning."""

        # 1. Verify property exists
        resp = await client_a_admin.get(f"/api/properties/{t12_seed['property']}")
        assert resp.status_code == 200, resp.text
        prop = resp.json()
        assert prop["name"] == "Miethaus T12"

        # 2. Verify unit exists
        resp = await client_a_admin.get(f"/api/units?property_id={t12_seed['property']}")
        assert resp.status_code == 200, resp.text
        units = resp.json()
        unit = next((item for item in units if item["id"] == t12_seed["unit"]), None)
        assert unit is not None
        assert unit["property_id"] == t12_seed["property"]
        assert unit["number"] == "1"

        # 3. Verify lease agreement exists
        resp = await client_a_admin.get(
            "/api/leases", params={"unit_id": t12_seed["unit"]}
        )
        assert resp.status_code == 200, resp.text
        leases = resp.json()
        lease = next((item for item in leases if item["id"] == t12_seed["lease"]), None)
        assert lease is not None
        assert lease["unit_id"] == t12_seed["unit"]
        assert lease["tenant_profile_id"] == t12_seed["tenant_profile"]
        assert lease["base_rent"] == "500.00"
        assert lease["advance_payment"] == "100.00"

        # 4. Verify payments (11 months * 2 types = 22 payments)
        resp = await client_a_admin.get(
            f"/api/payments?lease_agreement_id={t12_seed['lease']}&status=booked"
        )
        assert resp.status_code == 200
        payments = resp.json()
        assert len(payments) == 22, f"Expected 22 payments, got {len(payments)}"

        # 5. Create settlement run (Jan–Dec 2025)
        resp = await client_a_admin.post("/api/settlements", json={
            "property_id": t12_seed["property"],
            "lease_agreement_id": t12_seed["lease"],
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
            "advance_payments_total": "1200.00",  # 12 * 100 NK advance
        })
        assert resp.status_code == 201, resp.text
        settlement = resp.json()
        run_id = settlement["id"]

        # 6. Verify settlement was created
        resp = await client_a_admin.get(f"/api/settlements/{run_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["advance_payments_total"] == "1200.00"
        assert resp.json()["advance_payments_source"] == "MANUAL"

        # 7. Derive BK advances from the 11 booked NK payments.
        resp = await client_a_admin.patch(
            f"/api/settlements/{run_id}/advance-payments-derived?period_year=2025"
        )
        assert resp.status_code == 200, resp.text
        derived = resp.json()
        assert derived["id"] == run_id
        assert derived["advance_payments_total"] == "1100.00"
        assert derived["advance_payments_source"] == "DERIVED"

        # 8. Coverage must attest the value/source active after derivation.
        await self._attest_settlement(
            client_a_admin, run_id, "1100.00", "DERIVED"
        )

        # 9. Finalize settlement
        resp = await client_a_admin.post(
            f"/api/settlements/{run_id}/finalize",
            json={
                "unit_metrics": [
                    {"unit_id": t12_seed["unit"], "consumption": "600"}
                ]
            },
        )
        assert resp.status_code == 200, resp.text
        finalized = resp.json()
        assert finalized["status"] == "finalized"
        assert finalized["total_cost"] == "3480.00"
        assert finalized["advance_payments_total"] == "1100.00"
        assert finalized["advance_payments_source"] == "DERIVED"
        assert finalized["balance"] == "2380.00"

        # 10. Finalized settlement PDF.
        resp = await client_a_admin.get(f"/api/settlements/{run_id}/pdf")
        assert resp.status_code == 200, resp.text
        assert "application/pdf" in resp.headers.get("content-type", "").lower()
        assert resp.content[:5] == b"%PDF-"

        # 11. The BK balance is the cost allocation minus derived advances.
        resp = await client_a_admin.get(f"/api/settlements/{run_id}")
        assert resp.status_code == 200, resp.text
        persisted = resp.json()
        assert persisted["status"] == "finalized"
        assert persisted["balance"] == "2380.00"

        # 12. The Mietkonto independently has exactly December's 600 arrears.
        resp = await client_a_admin.post(
            "/api/dunning/tenant",
            json={
                "tenant_profile_id": t12_seed["tenant_profile"],
                "lease_agreement_id": t12_seed["lease"],
                "period_start": "2025-12-01",
                "period_end": "2025-12-31",
                "reference_date": "2026-01-15",
                "grace_days": 0,
            },
        )
        assert resp.status_code == 201, resp.text
        dunning = resp.json()
        assert dunning["status"] == "draft"
        assert dunning["principal_amount"] == "600.00"
        assert dunning["tenant_profile_id"] == t12_seed["tenant_profile"]
        items = sorted(dunning["included_items"], key=lambda item: item["payment_type"])
        assert items == [
            {
                "period_year": 2025,
                "period_month": 12,
                "payment_type": "KALTMIETE",
                "soll": "500.00",
                "ist": "0.00",
                "open_amount": "500.00",
            },
            {
                "period_year": 2025,
                "period_month": 12,
                "payment_type": "NK_VORAUSZAHLUNG",
                "soll": "100.00",
                "ist": "0.00",
                "open_amount": "100.00",
            },
        ]

        resp = await client_a_admin.post(f"/api/dunning/{dunning['id']}/issue")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "issued"

        resp = await client_a_admin.get(f"/api/dunning/{dunning['id']}/pdf")
        assert resp.status_code == 200, resp.text
        assert "application/pdf" in resp.headers.get("content-type", "").lower()
        assert resp.content[:5] == b"%PDF-"

    @staticmethod
    async def _attest_settlement(client, run_id, amount, source):
        """Attest financial input coverage for a settlement run."""
        from tests.financial_coverage_helpers import attest_fixture

        await attest_fixture(client, "settlement", run_id, {
            "advance_payments_total": amount,
            "advance_payments_source": source,
        })

    @pytest.mark.asyncio
    async def test_pdf_generation(self, client_a_admin, t12_seed):
        """Verify PDF generation for settlement and dunning."""
        # Create and finalize settlement
        resp = await client_a_admin.post("/api/settlements", json={
            "property_id": t12_seed["property"],
            "lease_agreement_id": t12_seed["lease"],
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
            "advance_payments_total": "1200.00",
        })
        assert resp.status_code == 201
        run_id = resp.json()["id"]

        # Attest financial input coverage (required before finalize)
        await self._attest_settlement(client_a_admin, run_id, "1200.00", "MANUAL")

        # Finalize
        resp = await client_a_admin.post(
            f"/api/settlements/{run_id}/finalize",
            json={
                "unit_metrics": [
                    {"unit_id": t12_seed["unit"], "consumption": "600"}
                ]
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "finalized"
        assert resp.json()["total_cost"] == "3480.00"
        assert resp.json()["advance_payments_total"] == "1200.00"
        assert resp.json()["advance_payments_source"] == "MANUAL"
        assert resp.json()["balance"] == "2280.00"

        # Get PDF
        resp = await client_a_admin.get(f"/api/settlements/{run_id}/pdf")
        assert resp.status_code == 200, resp.text
        # Verify PDF content type
        assert "application/pdf" in resp.headers.get("content-type", "").lower()
        assert resp.content[:5] == b"%PDF-"
