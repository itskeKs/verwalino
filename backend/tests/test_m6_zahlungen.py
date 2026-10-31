"""M6 tests -- payment persistence, RLS, immutability, Vermoegens-Kohaerenz,
the Mietkonto API and the DERIVED abrechnungs-anbindung (DERIVED == MANUAL).

Uses the same fixtures as M0-M5 (seed_data, app_rw_session, migrator_session,
client_a_admin/clerk/readonly, client_b_admin).

The Soll/Ist/Saldo + DERIVED math is proven in tests/engine/ (Golden +
property-based, incl. I6-D Spitze-invariance and I6-E DERIVED==MANUAL). Here we
prove the DB invariants (Composite-FK querbuchen, booked-immutability, RLS) and
the full create->book->statement / DERIVED-into-abrechnung path.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal

from tests.financial_coverage_helpers import attest_fixture
import pytest
from sqlalchemy import text

SYNC_URL = os.environ.get(
    "HV_DATABASE_URL_SYNC",
    "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
)


# ===========================================================================
# Seed: TWO properties under org_a (A + B) for the querbuchen test, each with a
# LAUFEND bank account; a lease + tenant on property A; an owner on property A.
# Plus a minimal property under org_b for cross-tenant tests.
# ===========================================================================
@pytest.fixture(scope="session")
def m6_seed(seed_data):
    import psycopg2

    org_a = str(seed_data["org_a_id"])
    org_b = str(seed_data["org_b_id"])
    ids = {
        "prop_a": uuid.uuid4(),
        "prop_b": uuid.uuid4(),
        "acct_a": uuid.uuid4(),
        "transaction": uuid.uuid4(),
        "acct_b": uuid.uuid4(),
        "unit_a": uuid.uuid4(),
        "person_t": uuid.uuid4(),
        "tenant": uuid.uuid4(),
        "tenant_b": uuid.uuid4(),
        "person_o": uuid.uuid4(),
        "owner": uuid.uuid4(),
        "lease": uuid.uuid4(),
        # Dedicated unit/tenant/lease for the DERIVED==MANUAL exit test (isolated
        # NK payments, own unit so its BK total is deterministic).
        "unit_d": uuid.uuid4(),
        "person_td": uuid.uuid4(),
        "tenant_d": uuid.uuid4(),
        "lease_d": uuid.uuid4(),
        # org_b
        "prop_b_org": uuid.uuid4(),
        "acct_b_org": uuid.uuid4(),
        "person_o_b": uuid.uuid4(),
        "owner_b": uuid.uuid4(),
    }

    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()

        # --- org_a: property A + property B (same org -> querbuchen risk) ---
        for pid, name in [(ids["prop_a"], "Haus A"), (ids["prop_b"], "Haus B")]:
            cur.execute(
                "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
                (str(pid), org_a, name, "WEG"),
            )
        cur.execute(
            "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
            "VALUES (%s,%s,%s,%s,%s)",
            (str(ids["acct_a"]), org_a, str(ids["prop_a"]), "LAUFEND", "Konto A"),
        )
        cur.execute(
            "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
            "VALUES (%s,%s,%s,%s,%s)",
            (str(ids["acct_b"]), org_a, str(ids["prop_b"]), "LAUFEND", "Konto B"),
        )
        cur.execute(
            "INSERT INTO bank_transaction (id,organization_id,bank_account_id,booking_date,value_date,amount,import_hash,status) "
            "VALUES (%s,%s,%s,'2025-06-01','2025-06-01',1,%s,'NEU')",
            (str(ids["transaction"]), org_a, str(ids["acct_a"]), uuid.uuid4().hex),
        )

        # Unit + owner + tenant + lease on property A.
        cur.execute(
            "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, heated_area, mea) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["unit_a"]), org_a, str(ids["prop_a"]), "1", "WOHNUNG", 100, 100, 1000),
        )
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
            (str(ids["person_o"]), org_a, "Olga", "Owner"),
        )
        cur.execute(
            "INSERT INTO owner_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["owner"]), org_a, str(ids["person_o"])),
        )
        cur.execute(
            "INSERT INTO ownership_history (id, organization_id, unit_id, owner_profile_id, valid_from, mea_share) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (str(uuid.uuid4()), org_a, str(ids["unit_a"]), str(ids["owner"]), date(2000, 1, 1), 1000),
        )
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
            (str(ids["person_t"]), org_a, "Tina", "Tenant"),
        )
        cur.execute(
            "INSERT INTO tenant_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["tenant"]), org_a, str(ids["person_t"])),
        )
        # Full-year lease: base_rent 700, NK advance 150. valid_to NULL (open).
        cur.execute(
            "INSERT INTO lease_agreement (id, organization_id, unit_id, tenant_profile_id, "
            "valid_from, valid_to, base_rent, advance_payment) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["lease"]), org_a, str(ids["unit_a"]), str(ids["tenant"]),
             date(2025, 1, 1), None, Decimal("700.00"), Decimal("150.00")),
        )

        # Dedicated unit/tenant/lease on property B for the DERIVED==MANUAL exit
        # test (isolated: prop_b has only this unit, so the BK total is trivially
        # the whole cost entry). Owner of prop_b unit = the same owner (reuse).
        cur.execute(
            "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, heated_area, mea) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["unit_d"]), org_a, str(ids["prop_b"]), "1", "WOHNUNG", 100, 100, 1000),
        )
        cur.execute(
            "INSERT INTO ownership_history (id, organization_id, unit_id, owner_profile_id, valid_from, mea_share) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (str(uuid.uuid4()), org_a, str(ids["unit_d"]), str(ids["owner"]), date(2000, 1, 1), 1000),
        )
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
            (str(ids["person_td"]), org_a, "Derek", "Derived"),
        )
        cur.execute(
            "INSERT INTO tenant_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["tenant_d"]), org_a, str(ids["person_td"])),
        )
        cur.execute(
            "INSERT INTO lease_agreement (id, organization_id, unit_id, tenant_profile_id, "
            "valid_from, valid_to, base_rent, advance_payment) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["lease_d"]), org_a, str(ids["unit_d"]), str(ids["tenant_d"]),
             date(2025, 1, 1), None, Decimal("700.00"), Decimal("150.00")),
        )
        # One confirmed apportionable cost entry on property B so the BK run has a
        # real total. GRUNDSTEUER 1200 full year, WOHNFLAECHE; single unit -> the
        # whole 1200 lands on the tenant.
        ids["ce_grundsteuer_b"] = uuid.uuid4()
        cur.execute("SELECT id FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1")
        cat_grundsteuer = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO cost_entry (id, organization_id, property_id, cost_category_id, "
            "cost_category_plan_version, invoice_date, service_period_from, service_period_to, "
            "gross_amount, tenant_distribution_key, tenant_apportionable, status, source, "
            "confirmed_at, confirmed_by) "
            "VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,'confirmed','MANUAL',now(),%s)",
            (str(ids["ce_grundsteuer_b"]), org_a, str(ids["prop_b"]), str(cat_grundsteuer),
             date(2025, 6, 1), date(2025, 1, 1), date(2025, 12, 31),
             Decimal("1200.00"), "WOHNFLAECHE", "UMLAGEFAEHIG", str(seed_data["user_a_admin_id"])),
        )

        # --- org_b: a property + account (cross-tenant isolation) ---
        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
            (str(ids["prop_b_org"]), org_b, "Haus Beta", "WEG"),
        )
        cur.execute(
            "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
            "VALUES (%s,%s,%s,%s,%s)",
            (str(ids["acct_b_org"]), org_b, str(ids["prop_b_org"]), "LAUFEND", "Konto Beta"),
        )
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
            (str(ids["person_o_b"]), org_b, "Bodo", "Beta"),
        )
        cur.execute(
            "INSERT INTO owner_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["owner_b"]), org_b, str(ids["person_o_b"])),
        )
        cur.execute(
            "INSERT INTO tenant_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["tenant_b"]), org_b, str(ids["person_o_b"])),
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {**seed_data, **ids}


async def _set_org(session, org_id):
    await session.execute(
        text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(org_id)}
    )


def _insert_payment_sql() -> str:
    return """
        INSERT INTO payment (
            id, organization_id, bank_account_id, property_id,
            direction, amount, value_date, payment_type,
            tenant_profile_id, owner_profile_id, lease_agreement_id,
            period_year, period_month, status, booked_at, booked_by
        ) VALUES (
            :id, :org, :acct, :prop,
            :dir, :amount, :vdate, :ptype,
            :tenant, :owner, :lease,
            :year, :month, :status, :booked_at, :booked_by
        )
    """


# asyncpg binds DATE/TIMESTAMPTZ params at the protocol layer (it infers the
# type from the target column), so plain string literals fail with
# "'str' object has no attribute 'toordinal'". Pass native date/datetime.
from datetime import datetime, timezone as _tz

_VDATE = date(2025, 6, 1)
_BOOKED_AT = datetime(2025, 6, 2, 10, 0, 0, tzinfo=_tz.utc)


# ===========================================================================
# Test 1: Composite-FK -- querbuchen scheitert (m6 section 7.1 #8, ADR-0010).
#   A payment with account of property A but property_id of property B (same
#   org) is rejected by the Composite-FK.
# ===========================================================================
class TestVermoegensKohaerenz:
    @pytest.mark.asyncio
    async def test_querbuchen_rejected_by_composite_fk(self, app_rw_session, m6_seed):
        session = app_rw_session
        await _set_org(session, m6_seed["org_a_id"])
        # Account A belongs to property A; claim property B -> DB-impossible.
        with pytest.raises(Exception) as exc:
            await session.execute(
                text(_insert_payment_sql()),
                {
                    "id": str(uuid.uuid4()),
                    "org": str(m6_seed["org_a_id"]),
                    "acct": str(m6_seed["acct_a"]),
                    "prop": str(m6_seed["prop_b"]),  # WRONG property for account A
                    "dir": "IN", "amount": "150.00", "vdate": _VDATE,
                    "ptype": "NK_VORAUSZAHLUNG",
                    "tenant": str(m6_seed["tenant"]), "owner": None,
                    "lease": str(m6_seed["lease"]),
                    "year": 2025, "month": 6, "status": "draft",
                    "booked_at": None, "booked_by": None,
                },
            )
        # Composite-FK violation (foreign key on bank_account coherence).
        assert "foreign key" in str(exc.value).lower() or "fk_payment_bank_account_coherence" in str(exc.value).lower()

    @pytest.mark.asyncio
    async def test_correct_property_accepted(self, app_rw_session, m6_seed):
        session = app_rw_session
        await _set_org(session, m6_seed["org_a_id"])
        pid = uuid.uuid4()
        # Account A + property A -> coherent, accepted.
        await session.execute(
            text(_insert_payment_sql()),
            {
                "id": str(pid),
                "org": str(m6_seed["org_a_id"]),
                "acct": str(m6_seed["acct_a"]),
                "prop": str(m6_seed["prop_a"]),  # correct
                "dir": "IN", "amount": "150.00", "vdate": _VDATE,
                "ptype": "NK_VORAUSZAHLUNG",
                "tenant": str(m6_seed["tenant"]), "owner": None,
                "lease": str(m6_seed["lease"]),
                "year": 2025, "month": 6, "status": "draft",
                "booked_at": None, "booked_by": None,
            },
        )
        row = await session.execute(
            text("SELECT property_id FROM payment WHERE id = :id"), {"id": str(pid)}
        )
        assert row.scalar_one() == m6_seed["prop_a"]


# ===========================================================================
# Test 2: GoBD -- booked payment is immutable (m6 section 7.1 #9; ADR-0003).
# ===========================================================================
class TestPaymentImmutability:
    async def _insert_booked(self, session, m6_seed) -> uuid.UUID:
        pid = uuid.uuid4()
        await session.execute(
            text(_insert_payment_sql()),
            {
                "id": str(pid),
                "org": str(m6_seed["org_a_id"]),
                "acct": str(m6_seed["acct_a"]),
                "prop": str(m6_seed["prop_a"]),
                "dir": "IN", "amount": "150.00", "vdate": _VDATE,
                "ptype": "NK_VORAUSZAHLUNG",
                "tenant": str(m6_seed["tenant"]), "owner": None,
                "lease": str(m6_seed["lease"]),
                "year": 2025, "month": 6, "status": "booked",
                "booked_at": _BOOKED_AT, "booked_by": str(m6_seed["user_a_admin_id"]),
            },
        )
        return pid

    @pytest.mark.asyncio
    async def test_update_amount_on_booked_rejected(self, app_rw_session, m6_seed):
        session = app_rw_session
        await _set_org(session, m6_seed["org_a_id"])
        pid = await self._insert_booked(session, m6_seed)
        with pytest.raises(Exception, match="GoBD.*amount.*booked"):
            await session.execute(
                text("UPDATE payment SET amount = '999.00' WHERE id = :id"), {"id": str(pid)}
            )

    @pytest.mark.asyncio
    async def test_update_type_on_booked_rejected(self, app_rw_session, m6_seed):
        session = app_rw_session
        await _set_org(session, m6_seed["org_a_id"])
        pid = await self._insert_booked(session, m6_seed)
        with pytest.raises(Exception, match="GoBD.*payment_type.*booked"):
            await session.execute(
                text("UPDATE payment SET payment_type = 'KALTMIETE' WHERE id = :id"),
                {"id": str(pid)},
            )

    @pytest.mark.asyncio
    async def test_delete_booked_forbidden(self, app_rw_session, m6_seed):
        session = app_rw_session
        await _set_org(session, m6_seed["org_a_id"])
        pid = await self._insert_booked(session, m6_seed)
        with pytest.raises(Exception, match="GoBD.*DELETE.*booked"):
            await session.execute(
                text("DELETE FROM payment WHERE id = :id"), {"id": str(pid)}
            )

    @pytest.mark.asyncio
    async def test_set_reversed_by_id_on_booked_allowed(self, app_rw_session, m6_seed):
        """The SINGLE allowed mutation of a booked row: setting reversed_by_id."""
        session = app_rw_session
        await _set_org(session, m6_seed["org_a_id"])
        pid = await self._insert_booked(session, m6_seed)
        # A real Storno row must exist (self-FK reversed_by_id -> payment.id).
        storno_id = uuid.uuid4()
        await session.execute(
            text(_insert_payment_sql()),
            {
                "id": str(storno_id), "org": str(m6_seed["org_a_id"]),
                "acct": str(m6_seed["acct_a"]), "prop": str(m6_seed["prop_a"]),
                "dir": "OUT", "amount": "150.00", "vdate": _VDATE,
                "ptype": "NK_VORAUSZAHLUNG", "tenant": str(m6_seed["tenant"]),
                "owner": None, "lease": str(m6_seed["lease"]),
                "year": 2025, "month": 6, "status": "booked",
                "booked_at": _BOOKED_AT, "booked_by": str(m6_seed["user_a_admin_id"]),
            },
        )
        # Setting reversed_by_id on the booked original is the allowed mutation.
        await session.execute(
            text("UPDATE payment SET reversed_by_id = :s WHERE id = :id"),
            {"s": str(storno_id), "id": str(pid)},
        )
        row = await session.execute(
            text("SELECT reversed_by_id FROM payment WHERE id = :id"), {"id": str(pid)}
        )
        assert row.scalar_one() == storno_id

    @pytest.mark.asyncio
    async def test_delete_draft_allowed(self, app_rw_session, m6_seed):
        session = app_rw_session
        await _set_org(session, m6_seed["org_a_id"])
        pid = uuid.uuid4()
        await session.execute(
            text(_insert_payment_sql()),
            {
                "id": str(pid), "org": str(m6_seed["org_a_id"]),
                "acct": str(m6_seed["acct_a"]), "prop": str(m6_seed["prop_a"]),
                "dir": "IN", "amount": "150.00", "vdate": _VDATE,
                "ptype": "NK_VORAUSZAHLUNG", "tenant": str(m6_seed["tenant"]),
                "owner": None, "lease": str(m6_seed["lease"]),
                "year": 2025, "month": 6, "status": "draft",
                "booked_at": None, "booked_by": None,
            },
        )
        await session.execute(text("DELETE FROM payment WHERE id = :id"), {"id": str(pid)})
        row = await session.execute(text("SELECT id FROM payment WHERE id = :id"), {"id": str(pid)})
        assert row.first() is None


# ===========================================================================
# Test 3: CHECK constraints -- counterparty/type consistency + positive amount.
# ===========================================================================
class TestPaymentChecks:
    @pytest.mark.asyncio
    async def test_tenant_type_requires_tenant(self, app_rw_session, m6_seed):
        """NK_VORAUSZAHLUNG with owner set (no tenant) -> CHECK violation."""
        session = app_rw_session
        await _set_org(session, m6_seed["org_a_id"])
        with pytest.raises(Exception, match="ck_payment_counterparty_type|check constraint"):
            await session.execute(
                text(_insert_payment_sql()),
                {
                    "id": str(uuid.uuid4()), "org": str(m6_seed["org_a_id"]),
                    "acct": str(m6_seed["acct_a"]), "prop": str(m6_seed["prop_a"]),
                    "dir": "IN", "amount": "150.00", "vdate": _VDATE,
                    "ptype": "NK_VORAUSZAHLUNG",
                    "tenant": None, "owner": str(m6_seed["owner"]),  # WRONG: owner on a tenant type
                    "lease": str(m6_seed["lease"]),
                    "year": 2025, "month": 6, "status": "draft",
                    "booked_at": None, "booked_by": None,
                },
            )

    @pytest.mark.asyncio
    async def test_negative_amount_rejected(self, app_rw_session, m6_seed):
        session = app_rw_session
        await _set_org(session, m6_seed["org_a_id"])
        with pytest.raises(Exception, match="ck_payment_amount_positive|check constraint"):
            await session.execute(
                text(_insert_payment_sql()),
                {
                    "id": str(uuid.uuid4()), "org": str(m6_seed["org_a_id"]),
                    "acct": str(m6_seed["acct_a"]), "prop": str(m6_seed["prop_a"]),
                    "dir": "IN", "amount": "-5.00", "vdate": _VDATE,
                    "ptype": "NK_VORAUSZAHLUNG", "tenant": str(m6_seed["tenant"]),
                    "owner": None, "lease": str(m6_seed["lease"]),
                    "year": 2025, "month": 6, "status": "draft",
                    "booked_at": None, "booked_by": None,
                },
            )


# ===========================================================================
# Test 4: RLS -- payment is org-isolated (cross-tenant returns nothing).
# ===========================================================================
class TestPaymentRLS:
    @pytest.mark.asyncio
    async def test_org_b_cannot_see_org_a_payment(self, app_rw_session, m6_seed):
        session = app_rw_session
        # Insert as org_a.
        await _set_org(session, m6_seed["org_a_id"])
        pid = uuid.uuid4()
        await session.execute(
            text(_insert_payment_sql()),
            {
                "id": str(pid), "org": str(m6_seed["org_a_id"]),
                "acct": str(m6_seed["acct_a"]), "prop": str(m6_seed["prop_a"]),
                "dir": "IN", "amount": "150.00", "vdate": _VDATE,
                "ptype": "NK_VORAUSZAHLUNG", "tenant": str(m6_seed["tenant"]),
                "owner": None, "lease": str(m6_seed["lease"]),
                "year": 2025, "month": 6, "status": "draft",
                "booked_at": None, "booked_by": None,
            },
        )
        # Switch to org_b -> must not see it.
        await _set_org(session, m6_seed["org_b_id"])
        row = await session.execute(text("SELECT id FROM payment WHERE id = :id"), {"id": str(pid)})
        assert row.first() is None

    @pytest.mark.asyncio
    async def test_no_org_context_returns_nothing(self, app_rw_session, m6_seed):
        """Fail-closed: with no org context, payment queries return nothing."""
        session = app_rw_session
        await session.execute(text("SELECT set_config('app.current_org_id', '', true)"))
        row = await session.execute(text("SELECT count(*) FROM payment"))
        assert row.scalar_one() == 0


# ===========================================================================
# Test 5: API -- create / book / list / reverse + RBAC + foreign 404.
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("field, key", [
    ("bank_account_id", "acct_a"), ("tenant_profile_id", "tenant"),
    ("owner_profile_id", "owner"), ("lease_agreement_id", "lease"),
    ("settlement_run_id", None),
])
async def test_foreign_and_unknown_payment_parents_leave_no_changes(
    client_a_admin, client_b_admin, m6_seed, migrator_session, method, field, key,
):
    report = await client_a_admin.post("/api/weg-settlements/asset-reports", json={
        "property_id": str(m6_seed["prop_a"]),
        "period_start": "2025-01-01", "period_end": "2025-12-31",
    })
    assert report.status_code == 201, report.text
    foreign = str(m6_seed[key]) if key else report.json()["id"]
    body = {"bank_account_id": str(m6_seed["acct_b_org"]), "direction": "IN",
            "amount": "1.00", "value_date": "2025-06-01", "payment_type": "NK_VORAUSZAHLUNG",
            "tenant_profile_id": str(m6_seed["tenant_b"])}
    if field == "owner_profile_id":
        body.pop("tenant_profile_id")
        body.update(payment_type="SONDERZAHLUNG", owner_profile_id=str(m6_seed["owner_b"]))
    path = "/api/payments"
    if method == "patch":
        draft = await client_b_admin.post(path, json=body)
        assert draft.status_code == 201, draft.text
        path += "/" + draft.json()["id"]
    before = (await migrator_session.scalars(text(
        "SELECT to_jsonb(p)::text FROM payment p ORDER BY id"))).all()
    responses = []
    for parent in (foreign, str(uuid.uuid4())):
        payload = (body if method == "post" else {}) | {field: parent}
        response = await getattr(client_b_admin, method)(path, json=payload)
        assert response.status_code == 404, response.text
        responses.append(response.json())
    assert responses[0] == responses[1]
    assert (await migrator_session.scalars(text(
        "SELECT to_jsonb(p)::text FROM payment p ORDER BY id"))).all() == before


class TestPaymentAPI:
    @pytest.mark.asyncio
    async def test_create_book_list_reverse_flow(self, client_a_clerk, m6_seed):
        # Create draft.
        r = await client_a_clerk.post(
            "/api/payments",
            json={
                "bank_account_id": str(m6_seed["acct_a"]),
                "direction": "IN",
                "amount": "150.00",
                "value_date": "2025-06-01",
                "payment_type": "NK_VORAUSZAHLUNG",
                "tenant_profile_id": str(m6_seed["tenant"]),
                "lease_agreement_id": str(m6_seed["lease"]),
                "period_year": 2025,
                "period_month": 6,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        pid = body["id"]
        assert body["status"] == "draft"
        # property_id is mirrored from the account (ADR-0010), not client-chosen.
        assert body["property_id"] == str(m6_seed["prop_a"])

        # Book it.
        r = await client_a_clerk.post(f"/api/payments/{pid}/book")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "booked"
        assert r.json()["retention_until"] is not None

        # Booked -> PATCH rejected (409).
        r = await client_a_clerk.patch(f"/api/payments/{pid}", json={"amount": "999.00"})
        assert r.status_code == 409

        # Booked -> DELETE rejected (409, GoBD).
        r = await client_a_clerk.delete(f"/api/payments/{pid}")
        assert r.status_code == 409

        # Reverse -> Storno row (booked OUT, reverses_id -> original).
        r = await client_a_clerk.post(f"/api/payments/{pid}/reverse")
        assert r.status_code == 201, r.text
        storno = r.json()
        assert storno["direction"] == "OUT"
        assert storno["reverses_id"] == pid
        assert storno["status"] == "booked"

        # Original now carries reversed_by_id.
        r = await client_a_clerk.get(f"/api/payments/{pid}")
        assert r.json()["reversed_by_id"] == storno["id"]

    @pytest.mark.asyncio
    async def test_readonly_cannot_create(self, client_a_readonly, m6_seed):
        r = await client_a_readonly.post(
            "/api/payments",
            json={
                "bank_account_id": str(m6_seed["acct_a"]),
                "direction": "IN", "amount": "100.00", "value_date": "2025-06-01",
                "payment_type": "NK_VORAUSZAHLUNG",
                "tenant_profile_id": str(m6_seed["tenant"]),
                "lease_agreement_id": str(m6_seed["lease"]),
                "period_year": 2025,
            },
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_readonly_can_list(self, client_a_readonly, m6_seed):
        r = await client_a_readonly.get("/api/payments")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_foreign_id_404(self, client_a_admin, m6_seed):
        r = await client_a_admin.get(f"/api/payments/{uuid.uuid4()}")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_org_b_payment_invisible_to_org_a_404(self, client_a_admin, client_b_admin, m6_seed):
        # org_b creates a valid SONDERZAHLUNG (owner type) against its own account.
        r = await client_b_admin.post(
            "/api/payments",
            json={
                "bank_account_id": str(m6_seed["acct_b_org"]),
                "direction": "IN", "amount": "100.00", "value_date": "2025-06-01",
                "payment_type": "SONDERZAHLUNG",
                "owner_profile_id": str(m6_seed["owner_b"]),
                "period_year": 2025,
            },
        )
        assert r.status_code == 201, r.text
        org_b_payment_id = r.json()["id"]
        # org_a must NOT see org_b's payment by its real id (RLS isolation -> 404).
        r2 = await client_a_admin.get(f"/api/payments/{org_b_payment_id}")
        assert r2.status_code == 404


# ===========================================================================
# Test 6: Mietkonto API -- Soll/Ist/Saldo + offene Posten for the seeded lease.
# ===========================================================================
class TestMietkontoAPI:
    @pytest.mark.asyncio
    async def test_lease_statement_full_year_paid(self, client_a_clerk, m6_seed):
        # Book 12x KALTMIETE 700 + 12x NK 150 for the lease (full year, paid).
        # Isolation: lease_d + year 2030 (untouched by other tests; lease_d is on
        # prop_b -> use acct_b). The shared `lease` on prop_a is contaminated by
        # the direct-session immutability tests, whose booked rows commit.
        for m in range(1, 13):
            for ptype, amt in [("KALTMIETE", "700.00"), ("NK_VORAUSZAHLUNG", "150.00")]:
                r = await client_a_clerk.post(
                    "/api/payments",
                    json={
                        "bank_account_id": str(m6_seed["acct_b"]),
                        "direction": "IN", "amount": amt, "value_date": f"2030-{m:02d}-05",
                        "payment_type": ptype, "tenant_profile_id": str(m6_seed["tenant_d"]),
                        "lease_agreement_id": str(m6_seed["lease_d"]),
                        "period_year": 2030, "period_month": m,
                    },
                )
                assert r.status_code == 201, r.text
                await client_a_clerk.post(f"/api/payments/{r.json()['id']}/book")

        r = await client_a_clerk.get(
            f"/api/payments/mietkonto/lease/{m6_seed['lease_d']}",
            params={"period_start": "2030-01-01", "period_end": "2030-12-31"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Jahres-Soll 10.200, Ist 10.200, Saldo 0 (Golden 1 at the DB level).
        assert body["soll_total"] == "10200.00"
        assert body["ist_total"] == "10200.00"
        assert body["balance"] == "0.00"

    @pytest.mark.asyncio
    async def test_open_items_after_underpayment(self, client_a_clerk, m6_seed):
        # Isolation: use lease_d + year 2099, a (lease, year) NO other test touches,
        # so the open item is deterministic regardless of suite ordering (the DB
        # persists across the session; only one partial payment lands here).
        r = await client_a_clerk.post(
            "/api/payments",
            json={
                "bank_account_id": str(m6_seed["acct_b"]),  # lease_d is on prop_b
                "direction": "IN", "amount": "600.00", "value_date": "2099-06-05",
                "payment_type": "KALTMIETE", "tenant_profile_id": str(m6_seed["tenant_d"]),
                "lease_agreement_id": str(m6_seed["lease_d"]),
                "period_year": 2099, "period_month": 6,
            },
        )
        assert r.status_code == 201
        await client_a_clerk.post(f"/api/payments/{r.json()['id']}/book")

        # Statement for June 2099 only: Soll = 700 (KALT) + 150 (NK) = 850; Ist 600.
        r = await client_a_clerk.get(
            f"/api/payments/open-items/lease/{m6_seed['lease_d']}",
            params={"period_start": "2099-06-01", "period_end": "2099-06-30"},
        )
        assert r.status_code == 200, r.text
        items = r.json()
        # Select the June-2099 rows precisely (the statement also surfaces Ist-only
        # rows tagged to other periods, which is informative, not an error).
        kalt = next(
            i for i in items
            if i["payment_type"] == "KALTMIETE" and i["period_year"] == 2099 and i["period_month"] == 6
        )
        assert kalt["open_amount"] == "100.00"  # 700 - 600
        nk = next(
            i for i in items
            if i["payment_type"] == "NK_VORAUSZAHLUNG" and i["period_year"] == 2099 and i["period_month"] == 6
        )
        assert nk["open_amount"] == "150.00"  # 150 - 0 (unpaid)


# ===========================================================================
# Test 7: DERIVED == MANUAL (m6 section 7.1 #5 / 7.3 -- the M6 EXIT).
#   Two BK runs over the same lease/period/costs: one MANUAL advance=1800, one
#   DERIVED after booking 12x NK 150. Both yield the SAME advance_payments_total,
#   total_cost and balance. The KALTMIETE payments do NOT flow into the NK sum.
# ===========================================================================
class TestDerivedEqualsManualExit:
    async def _create_run(self, client, m6_seed, advance: str | None):
        body = {
            "property_id": str(m6_seed["prop_b"]),
            "lease_agreement_id": str(m6_seed["lease_d"]),
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
        }
        if advance is not None:
            body["advance_payments_total"] = advance
        r = await client.post("/api/settlements", json=body)
        assert r.status_code == 201, r.text
        if advance is not None:
            await attest_fixture(client, "settlement", r.json()["id"],
                {"advance_payments_total": advance, "advance_payments_source": "MANUAL"})
        return r.json()["id"]

    @pytest.mark.asyncio
    async def test_derived_advance_matches_manual_and_balance(self, client_a_admin, m6_seed):
        # --- MANUAL run: advance 1800 entered by hand. ---
        run_manual = await self._create_run(client_a_admin, m6_seed, "1800.00")
        fin_m = await client_a_admin.post(f"/api/settlements/{run_manual}/finalize", json={})
        assert fin_m.status_code == 200, fin_m.text
        m = fin_m.json()
        # Single unit on prop_b -> whole 1200 GRUNDSTEUER to the tenant.
        assert m["total_cost"] == "1200.00"
        assert m["advance_payments_total"] == "1800.00"
        assert m["balance"] == "-600.00"  # 1200 - 1800 = -600 (Guthaben)
        assert m["advance_payments_source"] == "MANUAL"

        # --- Book 12x NK 150 (+ 12x KALTMIETE 700 as a decoy) for 2025/lease_d. ---
        for mo in range(1, 13):
            for ptype, amt in [("NK_VORAUSZAHLUNG", "150.00"), ("KALTMIETE", "700.00")]:
                r = await client_a_admin.post(
                    "/api/payments",
                    json={
                        "bank_account_id": str(m6_seed["acct_b"]),  # prop_b account
                        "direction": "IN", "amount": amt, "value_date": f"2025-{mo:02d}-05",
                        "payment_type": ptype, "tenant_profile_id": str(m6_seed["tenant_d"]),
                        "lease_agreement_id": str(m6_seed["lease_d"]),
                        "period_year": 2025, "period_month": mo,
                    },
                )
                assert r.status_code == 201, r.text
                await client_a_admin.post(f"/api/payments/{r.json()['id']}/book")

        # --- DERIVED run: pull the advance from the booked NK payments. ---
        run_derived = await self._create_run(client_a_admin, m6_seed, None)
        # Set DERIVED advance (12 * 150 = 1800; the 12*700 KALTMIETE must NOT count).
        r = await client_a_admin.patch(
            f"/api/settlements/{run_derived}/advance-payments-derived",
            params={"period_year": 2025},
        )
        assert r.status_code == 200, r.text
        assert r.json()["advance_payments_source"] == "DERIVED"
        assert r.json()["advance_payments_total"] == "1800.00"  # NK only, not KALTMIETE

        await attest_fixture(client_a_admin, "settlement", run_derived,
            {"advance_payments_total": "1800.00", "advance_payments_source": "DERIVED"})
        fin_d = await client_a_admin.post(f"/api/settlements/{run_derived}/finalize", json={})
        assert fin_d.status_code == 200, fin_d.text
        d = fin_d.json()

        # THE EXIT ASSERTION: DERIVED == MANUAL on the correctness-critical numbers.
        assert d["total_cost"] == m["total_cost"]            # 1200.00
        assert d["advance_payments_total"] == m["advance_payments_total"]  # 1800.00
        assert d["balance"] == m["balance"]                  # -600.00
        # (The snapshot hash differs ONLY by the advance_payments_source field --
        #  "MANUAL" vs "DERIVED"; the per-cent result is identical. The bit-
        #  identical-hash invariant for equal source is proven in I6-E.)


# ===========================================================================
# Test 8: WEG Spitze unberuehrt by DERIVED Hausgeld payments (m6 §7.1 #6 / I6-D).
#   A finalized plan + a WEG run; compute once WITHOUT payments and once WITH the
#   DERIVED HAUSGELD_VORSCHUSS payments. The Abrechnungsspitze per owner is
#   IDENTICAL -- payments never enter the Spitze (only the nachrichtliche
#   ADVANCE_PAYMENT line). Driven via the API (like the M4 tests); the DERIVED
#   list is produced by the real weg_settlement_service.derive_paid_advances.
# ===========================================================================
class TestWegSpitzeUnberuehrtByPayments:
    async def _create_finalized_plan(self, client, m6_seed) -> str:
        r = await client.post("/api/economic-plans", json={
            "property_id": str(m6_seed["prop_a"]),
            "plan_year": 2025, "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert r.status_code == 201, r.text
        plan_id = r.json()["id"]
        body = {
            "planned_costs": [
                {"category_code": "GRUNDSTEUER", "label": "Grundsteuer",
                 "planned_amount": "1200.00", "cost_group": "UMLAGEFAEHIG"},
            ],
            "reserve_contribution_total": "0.00",
        }
        await attest_fixture(client, "economic_plan", plan_id, body)
        r = await client.post(f"/api/economic-plans/{plan_id}/finalize", json=body)
        assert r.status_code == 200, r.text
        return plan_id

    async def _weg_spitze(self, client, m6_seed, plan_id, paid_advances):
        r = await client.post("/api/weg-settlements", json={
            "property_id": str(m6_seed["prop_a"]), "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert r.status_code == 201, r.text
        run_id = r.json()["id"]
        body = {"reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "0.00", "reserve_withdrawals_total": "0.00"}
        body["paid_advances"] = paid_advances if paid_advances is not None else [
            {"owner_profile_id": str(m6_seed["owner"]), "paid_total": "0.00"}]
        await attest_fixture(client, "settlement", run_id, body)
        r = await client.post(f"/api/weg-settlements/{run_id}/compute", json=body)
        assert r.status_code == 200, r.text
        lines = r.json()["lines"]
        spitze = {
            ln["addressee_owner_profile_id"]: ln["amount"]
            for ln in lines if ln["line_type"] == "ABRECHNUNGSSPITZE"
        }
        advance = {
            ln["addressee_owner_profile_id"]: ln["amount"]
            for ln in lines if ln["line_type"] == "ADVANCE_PAYMENT"
        }
        return spitze, advance

    @pytest.mark.asyncio
    async def test_spitze_identical_with_and_without_payments(self, client_a_admin, m6_seed):
        plan_id = await self._create_finalized_plan(client_a_admin, m6_seed)

        # (a) No payments -> baseline Spitze.
        spitze_none, _ = await self._weg_spitze(client_a_admin, m6_seed, plan_id, None)
        assert spitze_none  # at least one owner

        # (b) Book HAUSGELD_VORSCHUSS payments, derive them via the real M4 bridge.
        for mo in range(1, 13):
            r = await client_a_admin.post("/api/payments", json={
                "bank_account_id": str(m6_seed["acct_a"]), "direction": "IN",
                "amount": "500.00", "value_date": f"2025-{mo:02d}-05",
                "payment_type": "HAUSGELD_VORSCHUSS",
                "owner_profile_id": str(m6_seed["owner"]),
                "period_year": 2025, "period_month": mo,
            })
            assert r.status_code == 201, r.text
            await client_a_admin.post(f"/api/payments/{r.json()['id']}/book")

        from app.services.weg_settlement_service import WegSettlementService
        from app.core.db import get_tenant_session

        async with get_tenant_session(m6_seed["org_a_id"]) as session:
            svc = WegSettlementService(session, m6_seed["org_a_id"], m6_seed["user_a_admin_id"])
            derived = await svc.derive_paid_advances(uuid.UUID(plan_id), 2025)
        # derive_paid_advances returns ManualOwnerAdvance; map to the API body.
        paid_body = [
            {"owner_profile_id": str(d.owner_profile_id), "paid_total": str(d.paid_total)}
            for d in derived
        ]
        # The owner paid 12 * 500 = 6000 (the DERIVED nachrichtliche sum).
        assert any(p["paid_total"] == "6000.00" for p in paid_body)

        spitze_pay, advance_pay = await self._weg_spitze(
            client_a_admin, m6_seed, plan_id, paid_body
        )

        # THE I6-D ASSERTION: the Spitze is identical with and without payments.
        assert spitze_pay == spitze_none
        # And the payment DID surface nachrichtlich (the ADVANCE_PAYMENT line).
        assert advance_pay.get(str(m6_seed["owner"])) == "6000.00"


@pytest.mark.asyncio
async def test_correct_draft_month_before_booking(client_a_clerk, m6_seed):
    resp = await client_a_clerk.post("/api/payments", json={
        "bank_account_id": str(m6_seed["acct_b"]), "direction": "IN", "amount": "150.00",
        "value_date": "2088-06-05", "payment_type": "NK_VORAUSZAHLUNG",
        "tenant_profile_id": str(m6_seed["tenant_d"]), "lease_agreement_id": str(m6_seed["lease_d"]),
        "period_year": 2088, "period_month": 6,
    })
    assert resp.status_code == 201, resp.text
    payment_id = resp.json()["id"]
    resp = await client_a_clerk.patch(f"/api/payments/{payment_id}", json={"period_month": 7})
    assert resp.status_code == 200, resp.text
    assert resp.json()["period_month"] == 7
    resp = await client_a_clerk.post(f"/api/payments/{payment_id}/book")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "booked"
    for month, last, expected in ((6, 30, "0.00"), (7, 31, "150.00")):
        resp = await client_a_clerk.get(f"/api/payments/mietkonto/lease/{m6_seed['lease_d']}", params={
            "period_start": f"2088-{month:02d}-01", "period_end": f"2088-{month:02d}-{last}",
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["ist_total"] == expected
