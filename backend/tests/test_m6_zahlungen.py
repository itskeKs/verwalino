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


@pytest.mark.asyncio
async def test_owner_statement_exposes_signed_cutoff_balances_and_original_due_dates(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.api import payments
    from app.engine.mietkonto import ExpectedCharge, account_statement

    cutoff = date(2026, 7, 2)
    owner = uuid.uuid4()
    result = account_statement([ExpectedCharge(2026, 7, 'HAUSGELD_VORSCHUSS', Decimal('600.00'),
        owner_profile_id=owner, due_date=date(2026, 7, 3))], [], as_of=cutoff)
    statement = AsyncMock(return_value=result)
    monkeypatch.setattr(payments, 'PaymentService', lambda *args: SimpleNamespace(account_statement_for_owner=statement))
    response = await payments.owner_statement(owner, date(2026, 7, 1), date(2026, 7, 31),
        economic_plan_id=None, accounting_scope_id=None, as_of=cutoff,
        auth=SimpleNamespace(organization_id=uuid.uuid4(), user_id=uuid.uuid4()), db=None)
    output = response.model_dump(mode='json')
    assert output['balance'] == output['not_yet_due_balance'] == '600.00'
    assert output['due_balance'] == '0.00'
    assert output['open_items'][0]['due_date'] == '2026-07-03'
    assert output['open_items'][0]['is_due'] is False
    statement.assert_awaited_once_with(owner, None, date(2026, 7, 1), date(2026, 7, 31), None, as_of=cutoff)


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
        # For POST the foreign or unknown owner must look absent even when the
        # selected type also disallows an owner. The PATCH setup needs a valid draft.
        body.update(payment_type="KALTMIETE" if method == "post" else "SONDERZAHLUNG",
                    owner_profile_id=str(m6_seed["owner_b"]))
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
    @pytest.mark.parametrize("payment_type", ["KALTMIETE", "NK_VORAUSZAHLUNG", "HK_VORAUSZAHLUNG"])
    async def test_rent_draft_requires_a_tenant_instead_of_an_owner(self, client_a_clerk, m6_seed, payment_type):
        body = {
            "bank_account_id": str(m6_seed["acct_a"]), "direction": "IN",
            "amount": "75.25", "value_date": "2025-06-01", "payment_type": payment_type,
            "period_year": 2025, "period_month": 6,
        }
        response = await client_a_clerk.post("/api/payments", json={**body, "owner_profile_id": str(m6_seed["owner"])})
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == {
            "code": "PAYMENT_COUNTERPARTY_TYPE_MISMATCH",
            "message": "Dieser Zahlungstyp gehört zu einem Mietverhältnis, nicht zu einem Eigentümer.",
        }
        response = await client_a_clerk.post("/api/payments", json={
            **body, "tenant_profile_id": str(m6_seed["tenant"]), "lease_agreement_id": str(m6_seed["lease"]),
        })
        assert response.status_code == 201, response.text
        assert response.json()["tenant_profile_id"] == str(m6_seed["tenant"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payment_type", ["HAUSGELD_VORSCHUSS", "RUECKLAGENANTEIL", "SONDERZAHLUNG"])
    async def test_owner_draft_requires_an_owner(self, client_a_clerk, m6_seed, payment_type):
        body = {
            "bank_account_id": str(m6_seed["acct_a"]), "direction": "IN",
            "amount": "75.25", "value_date": "2025-06-01", "payment_type": payment_type,
        }
        response = await client_a_clerk.post("/api/payments", json=body)
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == {
            "code": "PAYMENT_COUNTERPARTY_TYPE_MISMATCH",
            "message": "Dieser Zahlungstyp erfordert einen Eigentümer als Gegenpartei.",
        }
        response = await client_a_clerk.post("/api/payments", json={**body, "owner_profile_id": str(m6_seed["owner"])})
        assert response.status_code == 201, response.text
        assert response.json()["owner_profile_id"] == str(m6_seed["owner"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payment_type", ["KALTMIETE", "HK_VORAUSZAHLUNG"])
    async def test_create_maps_the_database_counterparty_check(self, client_a_clerk, m6_seed, payment_type, monkeypatch):
        from app.services import payment_service

        # Exercise the two real CHECK constraints when the service guard does not catch the input.
        monkeypatch.setattr(payment_service, "validate_payment_counterparty", lambda *_: None)
        response = await client_a_clerk.post("/api/payments", json={
            "bank_account_id": str(m6_seed["acct_a"]), "direction": "IN",
            "amount": "75.25", "value_date": "2025-06-01", "payment_type": payment_type,
            "owner_profile_id": str(m6_seed["owner"]),
        })
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == {
            "code": "PAYMENT_COUNTERPARTY_TYPE_MISMATCH",
            "message": "Dieser Zahlungstyp gehört zu einem Mietverhältnis, nicht zu einem Eigentümer.",
        }

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
    async def test_cutoff_keeps_full_year_and_defaults_to_today(self, client_a_clerk, m6_seed):
        from unittest.mock import patch

        for month in range(1, 11):
            for kind, amount in [('KALTMIETE', '600.00' if month == 4 else '700.00'),
                                 ('NK_VORAUSZAHLUNG', '150.00')]:
                response = await client_a_clerk.post('/api/payments', json={
                    'bank_account_id': str(m6_seed['acct_b']), 'direction': 'IN', 'amount': amount,
                    'value_date': '2026-12-31' if month == 1 else f'2026-{month:02d}-05', 'payment_type': kind,
                    'tenant_profile_id': str(m6_seed['tenant_d']),
                    'lease_agreement_id': str(m6_seed['lease_d']), 'period_year': 2026, 'period_month': month})
                assert response.status_code == 201, response.text
                booked = await client_a_clerk.post(f"/api/payments/{response.json()['id']}/book")
                assert booked.status_code == 200, booked.text
        url = f"/api/payments/mietkonto/lease/{m6_seed['lease_d']}"
        params = {'period_start': '2026-01-01', 'period_end': '2026-12-31'}
        original_items = None
        for cutoff, due, future in [('2026-11-01', '100.00', '1700.00'),
                                    ('2026-11-03', '100.00', '1700.00'),
                                    ('2026-11-04', '950.00', '850.00'),
                                    ('2026-11-05', '950.00', '850.00'),
                                    ('2026-11-15', '950.00', '850.00')]:
            response = await client_a_clerk.get(url, params={**params, 'as_of': cutoff})
            assert response.status_code == 200, response.text
            body = response.json()
            assert (body['soll_total'], body['ist_total'], body['balance']) == ('10200.00', '8400.00', '1800.00')
            assert (body['due_balance'], body['not_yet_due_balance']) == (due, future)
            items = body['open_items']
            assert all(item['is_due'] == (item['period_month'] < 11 or
                item['period_month'] == 11 and cutoff >= '2026-11-04') for item in items)
            amounts = [{key: value for key, value in item.items() if key != 'is_due'} for item in items]
            if original_items is None:
                original_items = amounts
            assert amounts == original_items
        with patch('app.services.payment_service.date') as clock:
            clock.today.return_value = date(2026, 11, 1)
            response = await client_a_clerk.get(url, params=params)
        assert response.json()['due_balance'] == '100.00'
        assert response.json()['not_yet_due_balance'] == '1700.00'
        invalid = await client_a_clerk.get(url, params={**params, 'as_of': 'invalid'})
        assert invalid.status_code == 422

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
            body["heating_advance_payments_total"] = "0.00"
        r = await client.post("/api/settlements", json=body)
        assert r.status_code == 201, r.text
        if advance is not None:
            await attest_fixture(client, "settlement", r.json()["id"],
                {'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': "MANUAL", "advance_payments_total": advance, "advance_payments_source": "MANUAL"})
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
            {'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': "DERIVED", "advance_payments_total": "1800.00", "advance_payments_source": "DERIVED"})
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
        body = {"reserve_override_reason": "Reviewed synthetic opening balance", "reserve_opening_balance": "0.00"}
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


# ===========================================================================
# Booking the monthly Hausgeld of a finalized economic plan in one step (#186).
#
# Expected values come from the domain: "Gartenstraße 5", plan 2026 of 12.000,00 EUR over
# MEA 600 / 400 gives Anna Berg 7.200,00 EUR (600,00 a month) and unit 2 4.800,00 EUR
# (400,00 a month). Karl Weiß owns unit 2 until 30.06., Lena Roth from 01.07.
# ===========================================================================
PLAN_PATH = "/api/economic-plans/{plan}/hausgeld-booking"
ALL_MONTHS = range(1, 13)


async def _hausgeld_world(client, *, karl_until="2026-06-30", lena_from="2026-07-01", plan="finalized",
                          planned="12000.00"):
    async def create(path, body):
        response = await client.post(path, json=body)
        assert response.status_code == 201, f"{path}: {response.text}"
        return response.json()

    prop = await create("/api/properties", {"name": "Gartenstraße 5", "management_type": "WEG", "mea_denominator": 1000})
    units, owners = {}, {}
    for number, mea in (("1", "600"), ("2", "400")):
        units[number] = await create("/api/units", {
            "property_id": prop["id"], "number": number, "usage_type": "WOHNUNG", "mea": mea, "area": "70"})
    for key, first, last in (("anna", "Anna", "Berg"), ("karl", "Karl", "Weiß"), ("lena", "Lena", "Roth")):
        person = await create("/api/persons", {"first_name": first, "last_name": last})
        owners[key] = (await create("/api/owner-profiles", {"person_id": person["id"]}))["id"]
    await create("/api/ownership", {"unit_id": units["1"]["id"], "owner_profile_id": owners["anna"], "valid_from": "2020-01-01"})
    await create("/api/ownership", {"unit_id": units["2"]["id"], "owner_profile_id": owners["karl"],
        "valid_from": "2020-01-01", "valid_to": karl_until})
    await create("/api/ownership", {"unit_id": units["2"]["id"], "owner_profile_id": owners["lena"], "valid_from": lena_from})
    account = await create("/api/bank-accounts", {"property_id": prop["id"], "account_type": "LAUFEND", "label": "Hausgeldkonto"})
    world = {"property": prop["id"], "units": units, "owners": owners, "account": account["id"], "plan": None}
    if plan is not None:
        created = await create("/api/economic-plans", {"property_id": prop["id"], "plan_year": 2026,
            "period_start": "2026-01-01", "period_end": "2026-12-31", "monthly_due_day": 1})
        inputs = {"planned_costs": [{"category_code": "GARTENPFLEGE", "label": "Garten", "planned_amount": planned}],
                  "reserve_contribution_total": "0.00"}
        if plan == "finalized":
            await attest_fixture(client, "economic_plan", created["id"], inputs)
            answer = await client.post(f"/api/economic-plans/{created['id']}/finalize", json=inputs)
            assert answer.status_code == 200, answer.text
        world["plan"] = created["id"]
    return world


def _booking(world, owner, months=ALL_MONTHS, amount="600.00", **overrides):
    return {"bank_account_id": world["account"], "rows": [
        {"owner_profile_id": world["owners"][owner], "period_year": 2026, "period_month": month,
         "amount": amount, "value_date": f"2026-{month:02d}-01"} for month in months], **overrides}


async def _booked(client, world, **filters):
    response = await client.get("/api/payments", params={"bank_account_id": world["account"], **filters})
    assert response.status_code == 200, response.text
    return response.json()


def _by_owner(rows, world):
    names = {owner_id: key for key, owner_id in world["owners"].items()}
    grouped: dict[str, dict[int, str]] = {}
    for row in rows:
        grouped.setdefault(names[row["owner_profile_id"]], {})[row["period_month"]] = row["amount"]
    return grouped


class TestHausgeldBooking:
    @pytest.mark.asyncio
    async def test_offer_follows_the_ownership_periods_of_the_plan(self, client_a_admin):
        world = await _hausgeld_world(client_a_admin)
        response = await client_a_admin.get(PLAN_PATH.format(plan=world["plan"]))
        assert response.status_code == 200, response.text
        rows = response.json()
        assert len(rows) == 24
        assert not any(row["booked"] for row in rows)
        assert {row["period_year"] for row in rows} == {2026}
        assert _by_owner(rows, world) == {
            "anna": {month: "600.00" for month in ALL_MONTHS},
            "karl": {month: "400.00" for month in range(1, 7)},
            "lena": {month: "400.00" for month in range(7, 13)},
        }
        # The plan's due day of the month, not a shifted bank day (1 January and 1 February are no bank days).
        assert {row["value_date"] for row in rows if row["owner_profile_id"] == world["owners"]["anna"]} == {
            f"2026-{month:02d}-01" for month in ALL_MONTHS}

    @pytest.mark.asyncio
    async def test_one_request_creates_and_books_every_row_and_the_statement_balances(self, client_a_clerk, client_a_admin):
        world = await _hausgeld_world(client_a_admin)
        body = _booking(world, "anna")
        body["rows"][0]["value_date"] = "2026-01-05"
        response = await client_a_clerk.post(PLAN_PATH.format(plan=world["plan"]), json=body)
        assert response.status_code == 201, response.text
        created = response.json()
        assert [row["period_month"] for row in created] == list(ALL_MONTHS)
        assert {row["status"] for row in created} == {"booked"}
        assert {(row["payment_type"], row["direction"], row["period_year"], row["bank_account_id"], row["property_id"])
                for row in created} == {("HAUSGELD_VORSCHUSS", "IN", 2026, world["account"], world["property"])}
        assert {row["owner_profile_id"] for row in created} == {world["owners"]["anna"]}
        assert created[0]["value_date"] == "2026-01-05" and created[1]["value_date"] == "2026-02-01"
        assert sum(Decimal(row["amount"]) for row in created) == Decimal("7200.00")
        listed = await _booked(client_a_admin, world, payment_type="HAUSGELD_VORSCHUSS", status="booked")
        assert sorted(row["id"] for row in listed) == sorted(row["id"] for row in created)
        statement = await client_a_admin.get(f"/api/payments/mietkonto/owner/{world['owners']['anna']}", params={
            "period_start": "2026-01-01", "period_end": "2026-12-31", "economic_plan_id": world["plan"]})
        assert statement.status_code == 200, statement.text
        assert (statement.json()["soll_total"], statement.json()["ist_total"], statement.json()["balance"]) == (
            "7200.00", "7200.00", "0.00")
        again = (await client_a_admin.get(PLAN_PATH.format(plan=world["plan"]))).json()
        assert {row["booked"] for row in again if row["owner_profile_id"] == world["owners"]["anna"]} == {True}
        assert {row["booked"] for row in again if row["owner_profile_id"] != world["owners"]["anna"]} == {False}

    @pytest.mark.asyncio
    async def test_rows_are_ordinary_booked_payments_like_single_ones(self, client_a_admin, migrator_session):
        world = await _hausgeld_world(client_a_admin)
        single = (await client_a_admin.post("/api/payments", json={
            "bank_account_id": world["account"], "direction": "IN", "amount": "600.00", "value_date": "2026-01-05",
            "payment_type": "HAUSGELD_VORSCHUSS", "owner_profile_id": world["owners"]["anna"],
            "period_year": 2026, "period_month": 1})).json()
        assert (await client_a_admin.post(f"/api/payments/{single['id']}/book")).status_code == 200
        bulk = (await client_a_admin.post(PLAN_PATH.format(plan=world["plan"]), json=_booking(world, "anna", [2]))).json()[0]
        single = (await client_a_admin.get(f"/api/payments/{single['id']}")).json()
        ignored = {"id", "value_date", "period_month", "booked_at", "retention_until", "note"}
        assert {k: v for k, v in bulk.items() if k not in ignored} == {k: v for k, v in single.items() if k not in ignored}
        assert (bulk["retention_until"], bulk["note"]) == (single["retention_until"], single["note"])
        for payment in (single, bulk):
            actions = (await migrator_session.scalars(text(
                "SELECT action FROM audit_event WHERE entity_type='payment' AND entity_id=:id"),
                {"id": payment["id"]})).all()
            assert sorted(actions) == ["payment.booked", "payment.created"]
            bound = await migrator_session.scalar(text(
                "SELECT scope_id FROM accounting_scope_binding WHERE payment_id=:id"), {"id": payment["id"]})
            assert bound is None  # the unscoped property gives bulk and single rows the same (legacy) context

    @pytest.mark.asyncio
    async def test_an_edited_amount_and_value_date_are_booked_as_entered(self, client_a_admin):
        world = await _hausgeld_world(client_a_admin)
        body = _booking(world, "anna", [3], amount="590.50")
        body["rows"][0]["value_date"] = "2026-02-27"
        response = await client_a_admin.post(PLAN_PATH.format(plan=world["plan"]), json=body)
        assert response.status_code == 201, response.text
        assert (response.json()[0]["amount"], response.json()[0]["value_date"]) == ("590.50", "2026-02-27")

    @pytest.mark.asyncio
    async def test_one_invalid_row_books_nothing(self, client_a_admin):
        world = await _hausgeld_world(client_a_admin)
        body = _booking(world, "anna", range(1, 5))
        body["rows"][3]["owner_profile_id"] = world["owners"]["lena"]  # Lena owns unit 2 only from July: April is not hers
        response = await client_a_admin.post(PLAN_PATH.format(plan=world["plan"]), json=body)
        assert response.status_code == 422, response.text
        assert await _booked(client_a_admin, world) == []

    @pytest.mark.asyncio
    async def test_a_refusal_after_some_rows_were_booked_rolls_all_of_them_back(self, client_a_admin, monkeypatch):
        from fastapi import HTTPException
        from app.services.payment_service import PaymentService

        world = await _hausgeld_world(client_a_admin)
        original, calls = PaymentService.book, []

        async def refuse_third(service, payment_id):
            calls.append(payment_id)
            if len(calls) == 3:
                raise HTTPException(409, "Synthetic refusal on the third row")
            return await original(service, payment_id)

        monkeypatch.setattr(PaymentService, "book", refuse_third)
        response = await client_a_admin.post(PLAN_PATH.format(plan=world["plan"]), json=_booking(world, "anna"))
        assert response.status_code == 409, response.text
        assert len(calls) == 3  # two rows were created and booked before the refusal
        assert await _booked(client_a_admin, world) == []
        offer = (await client_a_admin.get(PLAN_PATH.format(plan=world["plan"]))).json()
        assert not any(row["booked"] for row in offer)

    @pytest.mark.asyncio
    async def test_a_booked_month_is_neither_offered_nor_bookable_a_second_time(self, client_a_admin):
        world = await _hausgeld_world(client_a_admin)
        first = await client_a_admin.post(PLAN_PATH.format(plan=world["plan"]), json=_booking(world, "anna", range(1, 4)))
        assert first.status_code == 201, first.text
        offer = (await client_a_admin.get(PLAN_PATH.format(plan=world["plan"]))).json()
        anna = {row["period_month"]: row["booked"] for row in offer if row["owner_profile_id"] == world["owners"]["anna"]}
        assert anna == {month: month <= 3 for month in ALL_MONTHS}
        # March again together with the still open April: refused as a whole.
        second = await client_a_admin.post(PLAN_PATH.format(plan=world["plan"]), json=_booking(world, "anna", [3, 4]))
        assert second.status_code == 409, second.text
        assert second.json()["detail"]["code"] == "HAUSGELD_MONTH_BOOKED"
        assert sorted(row["period_month"] for row in await _booked(client_a_admin, world)) == [1, 2, 3]
        # The same month for another owner is unaffected: nothing else is booked there.
        other = await client_a_admin.post(PLAN_PATH.format(plan=world["plan"]), json=_booking(world, "karl", [3], amount="400.00"))
        assert other.status_code == 201, other.text

    @pytest.mark.asyncio
    async def test_two_identical_requests_at_the_same_moment_book_once(self, client_a_admin, client_a_clerk):
        import asyncio

        world = await _hausgeld_world(client_a_admin)
        body = _booking(world, "anna")
        results = await asyncio.gather(
            client_a_admin.post(PLAN_PATH.format(plan=world["plan"]), json=body),
            client_a_clerk.post(PLAN_PATH.format(plan=world["plan"]), json=body))
        assert sorted(result.status_code for result in results) == [201, 409], [r.text for r in results]
        booked = await _booked(client_a_admin, world, owner_profile_id=world["owners"]["anna"])
        assert sorted(row["period_month"] for row in booked) == list(ALL_MONTHS)
        assert sum(Decimal(row["amount"]) for row in booked) == Decimal("7200.00")

    @pytest.mark.asyncio
    async def test_a_reversed_month_is_offered_and_bookable_again(self, client_a_admin):
        world = await _hausgeld_world(client_a_admin)
        created = (await client_a_admin.post(PLAN_PATH.format(plan=world["plan"]), json=_booking(world, "anna", [1]))).json()
        assert (await client_a_admin.post(f"/api/payments/{created[0]['id']}/reverse")).status_code == 201
        offer = (await client_a_admin.get(PLAN_PATH.format(plan=world["plan"]))).json()
        assert not any(row["booked"] for row in offer)
        again = await client_a_admin.post(PLAN_PATH.format(plan=world["plan"]), json=_booking(world, "anna", [1]))
        assert again.status_code == 201, again.text

    @pytest.mark.asyncio
    async def test_the_buyer_of_a_unit_gets_the_months_from_the_transfer_on_the_first(self, client_a_admin):
        world = await _hausgeld_world(client_a_admin)
        base = PLAN_PATH.format(plan=world["plan"])
        karl_july = await client_a_admin.post(base, json=_booking(world, "karl", [7], amount="400.00"))
        assert karl_july.status_code == 422, karl_july.text
        lena_june = await client_a_admin.post(base, json=_booking(world, "lena", [6], amount="400.00"))
        assert lena_june.status_code == 422, lena_june.text
        assert await _booked(client_a_admin, world) == []
        karl = await client_a_admin.post(base, json=_booking(world, "karl", range(1, 7), amount="400.00"))
        lena = await client_a_admin.post(base, json=_booking(world, "lena", range(7, 13), amount="400.00"))
        assert (karl.status_code, lena.status_code) == (201, 201), (karl.text, lena.text)
        statement = await client_a_admin.get(f"/api/payments/mietkonto/owner/{world['owners']['lena']}", params={
            "period_start": "2026-01-01", "period_end": "2026-12-31", "economic_plan_id": world["plan"]})
        assert (statement.json()["soll_total"], statement.json()["balance"]) == ("2400.00", "0.00")

    @pytest.mark.asyncio
    async def test_a_transfer_inside_a_month_splits_that_month_by_owner_days_between_both_owners(self, client_a_admin):
        # Karl owns until 15.07. (15 of 31 days), Lena from 16.07. (16 days): 400,00 EUR split 193,55 / 206,45.
        world = await _hausgeld_world(client_a_admin, karl_until="2026-07-15", lena_from="2026-07-16")
        rows = (await client_a_admin.get(PLAN_PATH.format(plan=world["plan"]))).json()
        grouped = _by_owner(rows, world)
        assert grouped["karl"] == {**{month: "400.00" for month in range(1, 7)}, 7: "193.55"}
        assert grouped["lena"] == {7: "206.45", **{month: "400.00" for month in range(8, 13)}}
        assert Decimal(grouped["karl"][7]) + Decimal(grouped["lena"][7]) == Decimal("400.00")
        both = await client_a_admin.post(PLAN_PATH.format(plan=world["plan"]), json={
            "bank_account_id": world["account"], "rows": [
                {"owner_profile_id": world["owners"]["karl"], "period_year": 2026, "period_month": 7,
                 "amount": "193.55", "value_date": "2026-07-01"},
                {"owner_profile_id": world["owners"]["lena"], "period_year": 2026, "period_month": 7,
                 "amount": "206.45", "value_date": "2026-07-01"}]})
        assert both.status_code == 201, both.text

    @pytest.mark.asyncio
    async def test_a_unit_without_an_owner_in_a_plan_month_offers_and_books_nothing(self, client_a_admin):
        # Karl until 31.03., Lena from 01.05.: nobody owns unit 2 in April.
        world = await _hausgeld_world(client_a_admin, karl_until="2026-03-31", lena_from="2026-05-01")
        for response in (
            await client_a_admin.get(PLAN_PATH.format(plan=world["plan"])),
            await client_a_admin.post(PLAN_PATH.format(plan=world["plan"]), json=_booking(world, "anna", [1])),
        ):
            assert response.status_code == 422, response.text
            assert response.json()["detail"]["code"] == "hausgeld_ownership_invalid"
        assert await _booked(client_a_admin, world) == []

    @pytest.mark.asyncio
    async def test_a_plan_that_is_not_finalized_offers_and_books_nothing(self, client_a_admin):
        world = await _hausgeld_world(client_a_admin, plan="draft")
        for response in (
            await client_a_admin.get(PLAN_PATH.format(plan=world["plan"])),
            await client_a_admin.post(PLAN_PATH.format(plan=world["plan"]), json=_booking(world, "anna", [1])),
        ):
            assert response.status_code == 409, response.text
            assert response.json()["detail"]["code"] == "ECONOMIC_PLAN_NOT_FINALIZED"
        assert await _booked(client_a_admin, world) == []

    @pytest.mark.asyncio
    async def test_a_superseded_plan_offers_and_books_nothing(self, client_a_admin):
        world = await _hausgeld_world(client_a_admin)
        assert (await client_a_admin.post(f"/api/economic-plans/{world['plan']}/correction")).status_code == 201
        for response in (
            await client_a_admin.get(PLAN_PATH.format(plan=world["plan"])),
            await client_a_admin.post(PLAN_PATH.format(plan=world["plan"]), json=_booking(world, "anna", [1])),
        ):
            assert response.status_code == 409, response.text
            assert response.json()["detail"]["code"] == "ECONOMIC_PLAN_SUPERSEDED"
        assert await _booked(client_a_admin, world) == []

    @pytest.mark.asyncio
    async def test_a_plan_line_without_an_amount_is_not_offered_or_bookable(self, client_a_admin):
        world = await _hausgeld_world(client_a_admin, planned="0.00")
        response = await client_a_admin.get(PLAN_PATH.format(plan=world["plan"]))
        assert response.status_code == 200, response.text
        assert response.json() == []
        refused = await client_a_admin.post(PLAN_PATH.format(plan=world["plan"]), json=_booking(world, "anna", [1], amount="1.00"))
        assert refused.status_code == 422, refused.text
        assert await _booked(client_a_admin, world) == []

    @pytest.mark.asyncio
    async def test_a_zero_line_of_one_owner_leaves_the_other_owners_rows(self, client_a_admin, seed_data):
        import psycopg2

        world = await _hausgeld_world(client_a_admin, plan=None)
        org, actor, plan = str(seed_data["org_a_id"]), str(seed_data["user_a_admin_id"]), uuid.uuid4()
        with psycopg2.connect(SYNC_URL) as connection, connection.cursor() as cursor:
            cursor.execute("INSERT INTO economic_plan(id,organization_id,property_id,plan_year,period_start,period_end,status,"
                           "engine_version,monthly_due_day) VALUES (%s,%s,%s,2026,'2026-01-01','2026-12-31','draft','fixture',1)",
                           (str(plan), org, world["property"]))
            for unit, owner, annual, monthly in (("1", "anna", "7200.00", "600.00"), ("2", "karl", "0.00", "0.00")):
                cursor.execute("INSERT INTO economic_plan_line(organization_id,economic_plan_id,line_type,unit_id,owner_profile_id,"
                               "label,planned_amount,monthly_amount,sort_order) VALUES (%s,%s,'OWNER_ADVANCE',%s,%s,'Hausgeldvorschuss',%s,%s,1)",
                               (org, str(plan), world["units"][unit]["id"], world["owners"][owner], annual, monthly))
            cursor.execute("UPDATE economic_plan SET status='finalized',finalized_at=now(),finalized_by=%s,snapshot_payload='{}',"
                           "snapshot_sha256=%s WHERE id=%s", (actor, "d" * 64, str(plan)))
        rows = (await client_a_admin.get(PLAN_PATH.format(plan=plan))).json()
        assert _by_owner(rows, world) == {"anna": {month: "600.00" for month in ALL_MONTHS}}
        refused = await client_a_admin.post(PLAN_PATH.format(plan=plan), json=_booking(world, "karl", [1], amount="400.00"))
        assert refused.status_code == 422, refused.text
        assert await _booked(client_a_admin, world) == []

    @pytest.mark.asyncio
    async def test_foreign_records_answer_404_and_book_nothing(self, client_a_admin, client_b_admin):
        world = await _hausgeld_world(client_a_admin)
        base = PLAN_PATH.format(plan=world["plan"])
        assert (await client_a_admin.get(base)).status_code == 200  # the plan exists for its own organization
        foreign_property = (await client_b_admin.post("/api/properties", json={"name": "Beta WEG", "management_type": "WEG"})).json()
        foreign_account = (await client_b_admin.post("/api/bank-accounts", json={
            "property_id": foreign_property["id"], "account_type": "LAUFEND", "label": "Beta"})).json()
        foreign_person = (await client_b_admin.post("/api/persons", json={"last_name": "Beta"})).json()
        foreign_owner = (await client_b_admin.post("/api/owner-profiles", json={"person_id": foreign_person["id"]})).json()
        # Another organization's plan, as a known id or an unknown one, looks the same.
        missing = [(await client_b_admin.get(base)), (await client_b_admin.post(base, json=_booking(world, "anna", [1]))),
                   (await client_b_admin.get(PLAN_PATH.format(plan=uuid.uuid4())))]
        assert {response.status_code for response in missing} == {404}
        assert missing[0].json() == missing[2].json()
        # Own plan with another organization's bank account or owner.
        account = _booking(world, "anna", [1], bank_account_id=foreign_account["id"])
        owner = _booking(world, "anna", [1])
        owner["rows"][0]["owner_profile_id"] = foreign_owner["id"]
        unknown_owner = _booking(world, "anna", [1])
        unknown_owner["rows"][0]["owner_profile_id"] = str(uuid.uuid4())
        responses = [await client_a_admin.post(base, json=body) for body in (account, owner, unknown_owner)]
        assert [response.status_code for response in responses] == [404, 404, 404], [r.text for r in responses]
        assert responses[1].json() == responses[2].json()
        assert await _booked(client_a_admin, world) == []
        assert await _booked(client_b_admin, world) == []

    @pytest.mark.asyncio
    async def test_the_account_must_belong_to_the_plan_property_and_be_active(self, client_a_admin):
        world = await _hausgeld_world(client_a_admin)
        base = PLAN_PATH.format(plan=world["plan"])
        other = await _hausgeld_world(client_a_admin, plan=None)
        elsewhere = await client_a_admin.post(base, json=_booking(world, "anna", [1], bank_account_id=other["account"]))
        assert elsewhere.status_code == 422, elsewhere.text
        assert elsewhere.json()["detail"]["code"] == "HAUSGELD_ACCOUNT_PROPERTY_MISMATCH"
        assert (await client_a_admin.patch(f"/api/bank-accounts/{world['account']}", json={"is_active": False})).status_code == 200
        inactive = await client_a_admin.post(base, json=_booking(world, "anna", [1]))
        assert inactive.status_code == 409, inactive.text  # the refusal of a single payment on an inactive account
        assert await _booked(client_a_admin, world) == [] and await _booked(client_a_admin, other) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("change", [
        {"amount": "0.00"}, {"amount": "-5.00"}, {"amount": "600.001"}, {"amount": "abc"}, {"amount": "NaN"},
        {"amount": "1000000000000.00"}, {"amount": 600.0}, {"period_month": 13}, {"value_date": "not a date"},
    ])
    async def test_invalid_row_values_are_refused_and_book_nothing(self, client_a_admin, change):
        world = await _hausgeld_world(client_a_admin)
        body = _booking(world, "anna", [1, 2])
        body["rows"][1].update(change)
        response = await client_a_admin.post(PLAN_PATH.format(plan=world["plan"]), json=body)
        assert response.status_code == 422, response.text
        assert await _booked(client_a_admin, world) == []

    @pytest.mark.asyncio
    async def test_the_request_names_each_month_once_and_stays_bounded(self, client_a_admin):
        world = await _hausgeld_world(client_a_admin)
        base = PLAN_PATH.format(plan=world["plan"])
        duplicate = _booking(world, "anna", [1, 1])
        empty = _booking(world, "anna", [])
        too_many = _booking(world, "anna", [1] * 145)
        responses = [await client_a_admin.post(base, json=body) for body in (duplicate, empty, too_many)]
        assert [response.status_code for response in responses] == [422, 422, 422], [r.text for r in responses]
        assert await _booked(client_a_admin, world) == []

    @pytest.mark.asyncio
    async def test_a_read_only_member_may_read_but_not_book(self, client_a_admin, client_a_readonly):
        world = await _hausgeld_world(client_a_admin)
        base = PLAN_PATH.format(plan=world["plan"])
        assert (await client_a_readonly.get(base)).status_code == 200
        assert (await client_a_readonly.post(base, json=_booking(world, "anna", [1]))).status_code == 403
        assert await _booked(client_a_admin, world) == []
