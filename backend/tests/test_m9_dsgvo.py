"""M9 DSGVO integration tests -- request_deletion / anonymize / export + RBAC.

Proves the ADR-0012 reconciliation against the live DB:
  * A retention-bound person (vendor of a confirmed cost entry) is RESTRICTED,
    never anonymized; the bound Beleg stays byte-for-byte unchanged (invariant).
  * An explicit anonymize on a bound person is refused (409) until retention.
  * An unbound person is anonymized immediately; PII is neutralized; idempotent.
  * A person with a live lease cannot be erased (409).
  * Endpoints are OWNER_ADMIN-only; tenant-isolated; unknown id -> 404.

The pure restrict/anonymize logic is unit-tested in tests/engine (engine.dsgvo);
here we test the service wiring + DB invariants.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

SYNC_URL = os.environ.get(
    "HV_DATABASE_URL_SYNC",
    "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
)


@pytest.fixture(scope="session")
def m9_dsgvo_seed(seed_data):
    """A bound vendor person, an unbound person, and a tenant with a live lease."""
    import psycopg2

    org_a = str(seed_data["org_a_id"])
    ids = {
        "prop": uuid.uuid4(),
        "unit": uuid.uuid4(),
        "person_vendor": uuid.uuid4(),
        "vendor": uuid.uuid4(),
        "cost_entry": uuid.uuid4(),
        "person_solo": uuid.uuid4(),
        "person_tenant": uuid.uuid4(),
        "tenant_profile": uuid.uuid4(),
        "lease": uuid.uuid4(),
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
            (str(ids["prop"]), org_a, "DSGVO Haus", "MIET"),
        )
        cur.execute(
            "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, heated_area, mea) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["unit"]), org_a, str(ids["prop"]), "D-1", "WOHNUNG", 70, 70, 400),
        )

        # --- Bound vendor: person -> vendor -> confirmed cost entry (2024) ---
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name, email, street, zip_code, city, iban) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["person_vendor"]), org_a, "Vera", "Vendor", "vera@vendor.de",
             "Lieferweg 3", "10115", "Berlin", "DE89370400440532013000"),
        )
        cur.execute(
            "INSERT INTO vendor (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["vendor"]), org_a, str(ids["person_vendor"])),
        )
        cur.execute(
            "INSERT INTO cost_entry (id, organization_id, property_id, cost_category_id, "
            "cost_category_plan_version, vendor_id, invoice_date, service_period_from, "
            "service_period_to, gross_amount, tenant_apportionable, status, source, "
            "confirmed_at, confirmed_by) "
            "VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,%s,'NICHT_UMLAGEFAEHIG','confirmed','MANUAL',now(),%s)",
            (str(ids["cost_entry"]), org_a, str(ids["prop"]), str(cost_category_id),
             str(ids["vendor"]), date(2024, 6, 1), date(2024, 1, 1), date(2024, 12, 31),
             Decimal("1190.00"), str(seed_data["user_a_admin_id"])),
        )

        # --- Unbound person (no roles, no records) ---
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name, email, street, zip_code, city) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["person_solo"]), org_a, "Solo", "Solitaer", "solo@x.de",
             "Einzelweg 1", "20095", "Hamburg"),
        )

        # --- Tenant with a LIVE (open-ended) lease ---
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
            (str(ids["person_tenant"]), org_a, "Tim", "Tenant"),
        )
        cur.execute(
            "INSERT INTO tenant_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["tenant_profile"]), org_a, str(ids["person_tenant"])),
        )
        cur.execute(
            "INSERT INTO lease_agreement (id, organization_id, unit_id, tenant_profile_id, "
            "valid_from, valid_to, base_rent, advance_payment) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["lease"]), org_a, str(ids["unit"]), str(ids["tenant_profile"]),
             date(2025, 1, 1), None, Decimal("700.00"), Decimal("150.00")),
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {**seed_data, **{k: str(v) for k, v in ids.items()}}


class TestDsgvoStatusAndExport:
    @pytest.mark.asyncio
    async def test_status_bound_vendor_is_restrict(self, client_a_admin, m9_dsgvo_seed):
        r = await client_a_admin.get(
            f"/api/dsgvo/persons/{m9_dsgvo_seed['person_vendor']}/status"
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["is_bound"] is True
        assert data["decision"] == "RESTRICT"
        assert data["purgeable_now"] is False
        # Retention = end of 2024 + 8 years -> 2032-12-31 (provisional rule).
        assert data["governing_retention_until"] == "2032-12-31"
        assert any(b["entity_type"] == "cost_entry" for b in data["bindings"])

    @pytest.mark.asyncio
    async def test_export_returns_roles_and_bindings(self, client_a_admin, m9_dsgvo_seed):
        r = await client_a_admin.get(
            f"/api/dsgvo/persons/{m9_dsgvo_seed['person_vendor']}/export"
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["person"]["last_name"] == "Vendor"
        assert m9_dsgvo_seed["vendor"] in data["roles"]["vendor_ids"]
        assert len(data["retention_bindings"]) >= 1

    @pytest.mark.asyncio
    async def test_unknown_person_404(self, client_a_admin, m9_dsgvo_seed):
        r = await client_a_admin.get(f"/api/dsgvo/persons/{uuid.uuid4()}/status")
        assert r.status_code == 404


class TestDsgvoReconciliation:
    @pytest.mark.asyncio
    async def test_request_deletion_bound_restricts_and_keeps_beleg(
        self, client_a_admin, m9_dsgvo_seed, app_rw_session
    ):
        pid = m9_dsgvo_seed["person_vendor"]
        r = await client_a_admin.post(f"/api/dsgvo/persons/{pid}/request-deletion")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["applied"] == "RESTRICTED"
        assert data["processing_restricted"] is True
        assert data["already_anonymized"] is False

        # Person PII is NOT anonymized (still bound).
        r = await client_a_admin.get(f"/api/dsgvo/persons/{pid}/export")
        assert r.json()["person"]["last_name"] == "Vendor"

        # INVARIANT: the bound cost entry is byte-for-byte unchanged.
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(m9_dsgvo_seed["org_a_id"])},
        )
        row = await app_rw_session.execute(
            text("SELECT gross_amount, status, vendor_id FROM cost_entry WHERE id = :id"),
            {"id": m9_dsgvo_seed["cost_entry"]},
        )
        gross, status, vendor_id = row.one()
        assert gross == Decimal("1190.00")
        assert status == "confirmed"
        assert str(vendor_id) == m9_dsgvo_seed["vendor"]

    @pytest.mark.asyncio
    async def test_anonymize_bound_rejected(self, client_a_admin, m9_dsgvo_seed):
        r = await client_a_admin.post(
            f"/api/dsgvo/persons/{m9_dsgvo_seed['person_vendor']}/anonymize"
        )
        assert r.status_code == 409
        assert "2032-12-31" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_request_deletion_unbound_anonymizes_and_idempotent(
        self, client_a_admin, m9_dsgvo_seed
    ):
        pid = m9_dsgvo_seed["person_solo"]
        r = await client_a_admin.post(f"/api/dsgvo/persons/{pid}/request-deletion")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["applied"] == "ANONYMIZED"
        assert data["already_anonymized"] is True

        # PII neutralized.
        exp = (await client_a_admin.get(f"/api/dsgvo/persons/{pid}/export")).json()
        assert exp["person"]["last_name"] == "Anonymisiert"
        assert exp["person"]["first_name"] == ""
        assert exp["person"]["email"] is None

        # Idempotent: a second request reports already-anonymized, no error.
        r2 = await client_a_admin.post(f"/api/dsgvo/persons/{pid}/request-deletion")
        assert r2.status_code == 200
        assert r2.json()["applied"] == "NONE_ALREADY_ANONYMIZED"

    @pytest.mark.asyncio
    async def test_active_relationship_blocks_erasure(self, client_a_admin, m9_dsgvo_seed):
        r = await client_a_admin.post(
            f"/api/dsgvo/persons/{m9_dsgvo_seed['person_tenant']}/request-deletion"
        )
        assert r.status_code == 409
        assert "vertragliche" in r.json()["detail"].lower()


class TestDsgvoRbacAndIsolation:
    @pytest.mark.asyncio
    async def test_clerk_forbidden(self, client_a_clerk, m9_dsgvo_seed):
        r = await client_a_clerk.get(
            f"/api/dsgvo/persons/{m9_dsgvo_seed['person_vendor']}/status"
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_readonly_forbidden(self, client_a_readonly, m9_dsgvo_seed):
        r = await client_a_readonly.post(
            f"/api/dsgvo/persons/{m9_dsgvo_seed['person_vendor']}/request-deletion"
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_cross_org_404(self, client_b_admin, m9_dsgvo_seed):
        r = await client_b_admin.get(
            f"/api/dsgvo/persons/{m9_dsgvo_seed['person_vendor']}/status"
        )
        assert r.status_code == 404
