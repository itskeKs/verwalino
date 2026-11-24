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

import importlib
import os
import re
import uuid
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.financial_coverage_helpers import attest_fixture
from tests.migration_rehearsal import lock_for_rehearsal
from tests.payment_reversal_support import book_then_reverse, payment_clock as payment_clock  # noqa: F401
from app.services.payment_service import REVERSAL_COPIED_COLUMNS
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

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


def _insert_reversing_payment_sql() -> str:
    """Like `_insert_payment_sql`, for a row that claims to reverse another payment (`reverses_id`)."""
    return """
        INSERT INTO payment (
            id, organization_id, bank_account_id, property_id,
            direction, amount, value_date, payment_type,
            tenant_profile_id, owner_profile_id, lease_agreement_id,
            period_year, period_month, status, booked_at, booked_by, reverses_id
        ) VALUES (
            :id, :org, :acct, :prop,
            :dir, :amount, :vdate, :ptype,
            :tenant, :owner, :lease,
            :year, :month, :status, :booked_at, :booked_by, :reverses
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
# Test 1b: the reversal links carry the organization in their key (#718, 0206).
#   `reverses_id` and `reversed_by_id` reference payment(organization_id, id). Foreign key
#   checks run without row-level security, so a plain key on `id` let organization B name a
#   payment of organization A: accepted when the id existed, refused when it did not (an
#   existence probe for foreign payment ids). With the organization in the key both are
#   refused with the same SQLSTATE.
# ===========================================================================
class TestReversalLinksCarryTheOrganization:
    @staticmethod
    async def _insert(
        session, m6_seed, org, *, reverses_id=None, reversed_by_id=None, pid=None, status="booked", direction="OUT",
    ) -> uuid.UUID:
        """Insert one payment of organization `org` ("a" or "b") as `app_rw`, with its links set on insert."""
        pid = pid or uuid.uuid4()
        other = org == "b"
        booked = status == "booked"
        await _set_org(session, m6_seed["org_b_id" if other else "org_a_id"])
        await session.execute(
            text("""
                INSERT INTO payment (
                    id, organization_id, bank_account_id, property_id,
                    direction, amount, value_date, payment_type,
                    tenant_profile_id, lease_agreement_id, period_year, period_month,
                    status, booked_at, booked_by, reverses_id, reversed_by_id
                ) VALUES (
                    :id, :org, :acct, :prop,
                    :direction, 150.00, :vdate, 'NK_VORAUSZAHLUNG',
                    :tenant, :lease, 2025, 6,
                    :status, :booked_at, :booked_by, :reverses, :reversed_by
                )
            """),
            {
                "id": str(pid), "org": str(m6_seed["org_b_id" if other else "org_a_id"]), "direction": direction,
                "acct": str(m6_seed["acct_b_org" if other else "acct_a"]),
                "prop": str(m6_seed["prop_b_org" if other else "prop_a"]),
                "vdate": _VDATE, "tenant": str(m6_seed["tenant_b" if other else "tenant"]),
                "lease": None if other else str(m6_seed["lease"]), "status": status,
                "booked_at": _BOOKED_AT if booked else None,
                "booked_by": str(m6_seed["user_b_admin_id" if other else "user_a_admin_id"]) if booked else None,
                "reverses": None if reverses_id is None else str(reverses_id),
                "reversed_by": None if reversed_by_id is None else str(reversed_by_id),
            },
        )
        return pid

    @classmethod
    async def _attempt(cls, session, m6_seed, org, **links) -> str:
        """SQLSTATE of the insert, or "accepted"."""
        try:
            async with session.begin_nested():
                await cls._insert(session, m6_seed, org, **links)
            return "accepted"
        except Exception as exc:  # noqa: BLE001 - the state code is the answer
            return getattr(getattr(exc, "orig", None), "sqlstate", repr(exc))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("column", ["reverses_id", "reversed_by_id"])
    async def test_a_payment_cannot_name_a_payment_of_another_organization(
        self, app_rw_session, m6_seed, column,
    ):
        """As organization B, the id of A's payment and an unknown id are refused alike: no existence probe.

        Both links are tried on a draft: a booked row naming a payment that is not its Storno or original is refused by
        the trigger first (#720, #721), before the key is checked at the end of the statement, so the key is what a
        draft shows."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        foreign = await self._insert(session, m6_seed, "a")
        outcomes = [
            await self._attempt(session, m6_seed, "b", status="draft", **{column: foreign}),
            await self._attempt(session, m6_seed, "b", status="draft", **{column: uuid.uuid4()}),
        ]
        await savepoint.rollback()
        assert outcomes == ["23503", "23503"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("org", ["a", "b"])
    async def test_a_reversal_pair_of_one_organization_is_accepted(self, app_rw_session, m6_seed, org):
        """The control: a payment, its Storno and the forward pointer inside one organization pass."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        original = await self._insert(session, m6_seed, org)
        storno = await self._insert(session, m6_seed, org, reverses_id=original, direction="IN")
        await session.execute(text("UPDATE payment SET reversed_by_id = :s WHERE id = :id"),
                              {"s": str(storno), "id": str(original)})
        linked = (await session.execute(text("SELECT reversed_by_id FROM payment WHERE id = :id"),
                                        {"id": str(original)})).scalar_one()
        await savepoint.rollback()
        assert linked == storno


# Revision 0206 itself: the keys it adds, the data it refuses and what its downgrade restores. Each test runs the
# revision's functions against the head schema inside a rollback-only transaction of the owner, `payment` locked first
# as for every revision that takes a NOWAIT lock.
REVERSAL_REVISION = "migrations.versions.0206_payment_reversal_organization_keys"
COMPOSITE_KEYS = [
    ("fk_payment_reversed_by_id_tenant",
     "FOREIGN KEY (organization_id, reversed_by_id) REFERENCES payment(organization_id, id) ON DELETE RESTRICT", True, False, "s"),
    ("fk_payment_reverses_id_tenant",
     "FOREIGN KEY (organization_id, reverses_id) REFERENCES payment(organization_id, id) ON DELETE RESTRICT", True, False, "s"),
]
SIMPLE_KEYS = [
    ("fk_payment_reversed_by_id_payment",
     "FOREIGN KEY (reversed_by_id) REFERENCES payment(id) ON DELETE RESTRICT", True, False, "s"),
    ("fk_payment_reverses_id_payment",
     "FOREIGN KEY (reverses_id) REFERENCES payment(id) ON DELETE RESTRICT", True, False, "s"),
]


@contextmanager
def _rehearsal():
    engine = create_engine(SYNC_URL)
    try:
        with engine.connect() as db:
            transaction = db.begin()
            try:
                with Operations.context(MigrationContext.configure(db)):
                    lock_for_rehearsal(db, "payment")
                    yield db
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def _reversal_keys(db):
    """Name, definition, validated, deferrable and match type of every foreign key on the two reversal columns."""
    return [tuple(row) for row in db.execute(text("""
        SELECT conname, pg_get_constraintdef(oid), convalidated, condeferrable, confmatchtype
        FROM pg_constraint WHERE conrelid = 'public.payment'::regclass AND contype = 'f' AND conname ~ '^fk_payment_reverse'
        ORDER BY conname"""))]


def _insert_as_runtime(
    db, m6_seed, org, *, status="booked", reverses_id=None, reversed_by_id=None, direction="OUT",
) -> uuid.UUID:
    """Insert one payment of organization `org` ("a" or "b") as `app_rw` inside the owner's transaction."""
    other = org == "b"
    pid = uuid.uuid4()
    db.execute(text("SELECT set_config('app.current_org_id', :org, true)"),
               {"org": str(m6_seed["org_b_id" if other else "org_a_id"])})
    db.execute(text("SET LOCAL ROLE app_rw"))
    try:
        db.execute(text("""
            INSERT INTO payment (id, organization_id, bank_account_id, property_id, direction, amount, value_date,
                payment_type, tenant_profile_id, lease_agreement_id, period_year, period_month,
                status, booked_at, booked_by, reverses_id, reversed_by_id)
            VALUES (:id, :org, :acct, :prop, :direction, 150.00, :vdate, 'NK_VORAUSZAHLUNG', :tenant, :lease, 2025, 6,
                :status, :booked_at, :booked_by, :reverses, :reversed_by)"""), {
            "id": str(pid), "org": str(m6_seed["org_b_id" if other else "org_a_id"]), "direction": direction,
            "acct": str(m6_seed["acct_b_org" if other else "acct_a"]),
            "prop": str(m6_seed["prop_b_org" if other else "prop_a"]), "vdate": _VDATE,
            "tenant": str(m6_seed["tenant_b" if other else "tenant"]),
            "lease": None if other else str(m6_seed["lease"]), "status": status,
            "booked_at": _BOOKED_AT if status == "booked" else None,
            "booked_by": str(m6_seed["user_b_admin_id" if other else "user_a_admin_id"]) if status == "booked" else None,
            "reverses": None if reverses_id is None else str(reverses_id),
            "reversed_by": None if reversed_by_id is None else str(reversed_by_id)})
    finally:
        db.execute(text("RESET ROLE"))
    return pid


class TestReversalKeyRevision:
    def test_the_head_schema_has_the_composite_keys_and_no_simple_ones(self):
        with _rehearsal() as db:
            assert _reversal_keys(db) == COMPOSITE_KEYS

    def test_downgrade_restores_the_simple_keys_and_upgrade_the_composite_ones_over_a_linked_pair(self, m6_seed):
        migration = importlib.import_module(REVERSAL_REVISION)
        with _rehearsal() as db:
            original = _insert_as_runtime(db, m6_seed, "a")
            storno = _insert_as_runtime(db, m6_seed, "a", reverses_id=original, direction="IN")
            db.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(m6_seed["org_a_id"])})
            db.execute(text("SET LOCAL ROLE app_rw"))
            db.execute(text("UPDATE payment SET reversed_by_id = :s WHERE id = :id"), {"s": str(storno), "id": str(original)})
            db.execute(text("RESET ROLE"))
            # The inserts queued deferred checks on payment, which would block the ALTER TABLE of the downgrade.
            db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            pair = text("SELECT id, reverses_id, reversed_by_id FROM payment WHERE id IN (:a, :b) ORDER BY id")
            rows = [tuple(row) for row in db.execute(pair, {"a": str(original), "b": str(storno)})]
            assert sorted(rows) == sorted([(original, None, storno), (storno, original, None)])
            migration.downgrade()
            assert _reversal_keys(db) == SIMPLE_KEYS
            assert [tuple(row) for row in db.execute(pair, {"a": str(original), "b": str(storno)})] == rows
            migration.upgrade()
            assert _reversal_keys(db) == COMPOSITE_KEYS
            assert [tuple(row) for row in db.execute(pair, {"a": str(original), "b": str(storno)})] == rows

    def test_upgrade_refuses_a_payment_naming_a_payment_of_another_organization_and_changes_nothing(self, m6_seed):
        migration = importlib.import_module(REVERSAL_REVISION)
        with _rehearsal() as db:
            migration.downgrade()
            foreign = _insert_as_runtime(db, m6_seed, "a")
            # The plain keys accept what the composite keys refuse: drafts of organization B naming A's payment on
            # either link, and on both (one row, counted once).
            offenders = [
                _insert_as_runtime(db, m6_seed, "b", status="draft", reverses_id=foreign),
                _insert_as_runtime(db, m6_seed, "b", status="draft", reversed_by_id=foreign),
                _insert_as_runtime(db, m6_seed, "b", status="draft", reverses_id=foreign, reversed_by_id=foreign),
            ]
            with pytest.raises(DBAPIError) as refused:
                with db.begin_nested():
                    migration.upgrade()
            message = str(refused.value.orig)
            assert refused.value.orig.pgcode == "P0001"
            assert "Cannot upgrade 0206: 3 payment row(s) name a payment of another organization" in message
            # The count is all it says: no id of a payment or of an organization. The hint names the query that lists the rows.
            hint = refused.value.orig.diag.message_hint
            assert "SELECT p.id, p.organization_id FROM public.payment p" in hint
            assert not any(str(identity) in message + hint
                           for identity in [foreign, *offenders, m6_seed["org_a_id"], m6_seed["org_b_id"]])
            assert _reversal_keys(db) == SIMPLE_KEYS
            assert db.scalar(text("SELECT count(*) FROM payment WHERE id = ANY(CAST(:ids AS uuid[]))"),
                             {"ids": [str(identity) for identity in offenders]}) == 3
            # Once the rows are gone the upgrade goes through.
            db.execute(text("DELETE FROM payment WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": [str(identity) for identity in offenders]})
            migration.upgrade()
            assert _reversal_keys(db) == COMPOSITE_KEYS


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

    async def _insert_reversing(
        self, session, m6_seed, reverses, *, status="booked", org="a", pid=None
    ) -> uuid.UUID:
        """Insert a payment that claims to reverse `reverses`; org "b" inserts it as organization B."""
        pid = pid or uuid.uuid4()
        other = org == "b"
        booked = status == "booked"
        await _set_org(session, m6_seed["org_b_id" if other else "org_a_id"])
        await session.execute(
            text(_insert_reversing_payment_sql()),
            {
                "id": str(pid), "org": str(m6_seed["org_b_id" if other else "org_a_id"]),
                "acct": str(m6_seed["acct_b_org" if other else "acct_a"]),
                "prop": str(m6_seed["prop_b_org" if other else "prop_a"]),
                "dir": "OUT", "amount": "150.00", "vdate": _VDATE,
                "ptype": "NK_VORAUSZAHLUNG",
                "tenant": str(m6_seed["tenant_b" if other else "tenant"]), "owner": None,
                "lease": None if other else str(m6_seed["lease"]),
                "year": 2025, "month": 6, "status": status,
                "booked_at": _BOOKED_AT if booked else None,
                "booked_by": str(m6_seed["user_a_admin_id"]) if booked else None,
                "reverses": None if reverses is None else str(reverses),
            },
        )
        await _set_org(session, m6_seed["org_a_id"])
        return pid

    @staticmethod
    async def _link(session, original, storno):
        await session.execute(
            text("UPDATE payment SET reversed_by_id = :s WHERE id = :id"),
            {"s": None if storno is None else str(storno), "id": str(original)},
        )

    @staticmethod
    async def _reversed_by(session, original):
        row = await session.execute(
            text("SELECT reversed_by_id FROM payment WHERE id = :id"), {"id": str(original)}
        )
        return row.scalar_one()

    @pytest.mark.asyncio
    async def test_set_reversed_by_id_on_booked_allowed(self, app_rw_session, m6_seed):
        """The SINGLE allowed mutation of a booked row: NULL -> the id of its booked Storno."""
        session = app_rw_session
        await _set_org(session, m6_seed["org_a_id"])
        pid = await self._insert_booked(session, m6_seed)
        # The real Storno: booked, same organization, reverses_id -> the original.
        storno_id = await self._insert_reversing(session, m6_seed, pid)
        # Setting reversed_by_id on the booked original is the allowed mutation.
        await self._link(session, pid, storno_id)
        assert await self._reversed_by(session, pid) == storno_id

    @pytest.mark.asyncio
    async def test_reversed_by_id_cannot_be_repointed_or_cleared_once_set(self, app_rw_session, m6_seed):
        """A reversal link is part of the booking chain: once set, no statement changes it."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        original = await self._insert_booked(session, m6_seed)
        storno = await self._insert_reversing(session, m6_seed, original)
        await self._link(session, original, storno)
        # Candidates a forged link could name: a second Storno of the same payment (a draft: only one booked Storno
        # can name a payment, #721), an unrelated booked payment.
        second_storno = await self._insert_reversing(session, m6_seed, original, status="draft")
        unrelated = await self._insert_booked(session, m6_seed)
        for target in (second_storno, unrelated, None, original):
            with pytest.raises(Exception, match="GoBD.*reversed_by_id.*booked"):
                async with session.begin_nested():
                    await self._link(session, original, target)
            assert await self._reversed_by(session, original) == storno
        # Writing the same link again and the technical timestamp stay allowed.
        await self._link(session, original, storno)
        await session.execute(
            text("UPDATE payment SET updated_at = now() WHERE id = :id"), {"id": str(original)}
        )
        assert await self._reversed_by(session, original) == storno
        await savepoint.rollback()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("claim", ["no_reverses_id", "reverses_another_payment", "draft", "itself"])
    async def test_reversed_by_id_must_name_the_booked_storno_of_the_same_organization(
        self, app_rw_session, m6_seed, claim,
    ):
        """NULL -> id is accepted only for a booked payment of this organization that reverses this row.

        A booked row that reverses itself, which an earlier version of this test also claimed, can no longer be
        written: a booked Storno must mirror a booked original that is another row (#721)."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        original = await self._insert_booked(session, m6_seed)
        other = await self._insert_booked(session, m6_seed)
        subject, claimed = original, original
        if claim == "no_reverses_id":
            claimed = await self._insert_reversing(session, m6_seed, None)
        elif claim == "reverses_another_payment":
            claimed = await self._insert_reversing(session, m6_seed, other)
        elif claim == "draft":
            claimed = await self._insert_reversing(session, m6_seed, original, status="draft")
        with pytest.raises(Exception, match="GoBD.*reversed_by_id.*booked"):
            async with session.begin_nested():
                await self._link(session, subject, claimed)
        assert await self._reversed_by(session, subject) is None
        # The refusal is about the claim: the real Storno of the same payment still links.
        storno = await self._insert_reversing(session, m6_seed, original)
        await self._link(session, original, storno)
        assert await self._reversed_by(session, original) == storno
        await savepoint.rollback()

    @pytest.mark.asyncio
    async def test_reversed_by_id_must_name_a_storno_of_the_same_organization_without_the_foreign_key(
        self, migrator_session, m6_seed,
    ):
        """The trigger's own organization rule: since 0206 the key refuses to store a Storno of another organization.

        The row is built with the key dropped and the INSERT trigger disabled (the Storno of another organization is
        refused on insert since #721), inside a savepoint that restores both, so the UPDATE trigger is what refuses the
        link. The fixture commits when the test ends, also when an assertion failed: the savepoint is rolled back in a
        `finally` so that the shared database never loses the key or the trigger.
        """
        session = migrator_session
        savepoint = await session.begin_nested()
        try:
            await session.execute(text("ALTER TABLE payment DROP CONSTRAINT fk_payment_reverses_id_tenant"))
            await session.execute(text("ALTER TABLE payment DISABLE TRIGGER trg_payment_immutability_insert"))
            await _set_org(session, m6_seed["org_a_id"])
            await session.execute(text("SET LOCAL ROLE app_rw"))
            original = await self._insert_booked(session, m6_seed)
            claimed = await self._insert_reversing(session, m6_seed, original, org="b")
            with pytest.raises(Exception, match="GoBD.*reversed_by_id.*booked"):
                async with session.begin_nested():
                    await self._link(session, original, claimed)
            assert await self._reversed_by(session, original) is None
        finally:
            await savepoint.rollback()

    @pytest.mark.asyncio
    async def test_reversal_link_check_does_not_depend_on_what_the_actor_can_see(
        self, migrator_session, m6_seed,
    ):
        """A legitimate link is not refused because row-level security hides the Storno from the actor."""
        session = migrator_session
        savepoint = await session.begin_nested()
        await session.execute(text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(m6_seed["org_a_id"])})
        await session.execute(text("SET LOCAL ROLE app_rw"))
        original = await self._insert_booked(session, m6_seed)
        storno = await self._insert_reversing(session, m6_seed, original)
        await session.execute(text("RESET ROLE"))
        # Stand-in for a scope the actor cannot read: the actor's queries no longer see the Storno.
        await session.execute(text(f"CREATE POLICY hide_storno ON payment AS RESTRICTIVE FOR SELECT USING (id <> '{storno}')"))
        await session.execute(text("SET LOCAL ROLE app_rw"))
        assert (await session.execute(text("SELECT count(*) FROM payment WHERE id = :id"), {"id": str(storno)})).scalar_one() == 0
        await self._link(session, original, storno)
        await session.execute(text("RESET ROLE"))
        assert await self._reversed_by(session, original) == storno
        await savepoint.rollback()

    @pytest.mark.asyncio
    async def test_the_runtime_role_cannot_use_the_immutability_function_to_probe_another_organization(
        self, app_rw_session, m6_seed,
    ):
        """As organization B, attaching the definer function to a temp table must not tell what A's payments are."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        original = await self._insert_booked(session, m6_seed)
        storno = await self._insert_reversing(session, m6_seed, original)

        async def probe(guess):
            """SQLSTATE of the attempt, or "accepted": the update passes only for the real Storno of the row."""
            try:
                async with session.begin_nested():
                    await session.execute(text(
                        "CREATE TEMP TABLE payment_probe (LIKE public.payment INCLUDING DEFAULTS) ON COMMIT DROP"))
                    await session.execute(text(
                        "CREATE TRIGGER probe BEFORE UPDATE ON payment_probe FOR EACH ROW "
                        "EXECUTE FUNCTION public.trg_payment_immutability()"))
                    # A row that names organization A's payment; B can read neither it nor the Storno.
                    await session.execute(text(
                        "INSERT INTO payment_probe (id, organization_id, bank_account_id, property_id, direction, "
                        "amount, value_date, payment_type, status) VALUES (:id, :org, :acct, :prop, 'IN', 150.00, "
                        ":vdate, 'NK_VORAUSZAHLUNG', 'booked')"),
                        {"id": str(original), "org": str(m6_seed["org_a_id"]), "acct": str(m6_seed["acct_a"]),
                         "prop": str(m6_seed["prop_a"]), "vdate": _VDATE})
                    await session.execute(
                        text("UPDATE payment_probe SET reversed_by_id = :g"), {"g": str(guess)})
                    await session.execute(text("DROP TABLE payment_probe"))
                return "accepted"
            except Exception as exc:  # noqa: BLE001 - the state code is the answer
                return getattr(getattr(exc, "orig", None), "sqlstate", repr(exc))

        await _set_org(session, m6_seed["org_b_id"])
        # Insufficient privilege (42501) for the real Storno and for a wrong guess alike: no pass/refuse oracle.
        assert [await probe(storno), await probe(uuid.uuid4())] == ["42501", "42501"]
        # The trigger on payment itself still fires for the runtime role, without EXECUTE on the function.
        await _set_org(session, m6_seed["org_a_id"])
        await self._link(session, original, storno)
        with pytest.raises(Exception, match="GoBD.*reversed_by_id.*booked"):
            async with session.begin_nested():
                await self._link(session, original, uuid.uuid4())
        assert await self._reversed_by(session, original) == storno
        await savepoint.rollback()

    def test_reversal_link_migration_roundtrip_restores_the_previous_function(self, monkeypatch):
        """0192 replaces only the function; its downgrade restores the body 0018 created, exactly."""
        import importlib
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import create_engine
        from tests.conftest import MIGRATOR_SYNC_URL

        first = importlib.import_module("migrations.versions.0018_payment_immutability_trigger")
        migration = importlib.import_module("migrations.versions.0192_payment_reversal_link_immutable")
        issued = []
        monkeypatch.setattr(first, "op", type("RecordingOp", (), {"execute": staticmethod(issued.append)}))
        first.upgrade()
        created = issued[0].split("$$")[1]  # the body and settings 0018 gave the function

        function = text("""SELECT prosrc, prosecdef, proconfig, pg_get_triggerdef(t.oid) AS trigger
            FROM pg_proc p, pg_trigger t
            WHERE p.oid = 'trg_payment_immutability()'::regprocedure AND t.tgname = 'trg_payment_immutability'""")
        # Who may execute the function, as the repo's other ACL tests read it: the stored ACL, or the
        # default of its owner while none is stored (revoke + grant stores an equal ACL, not NULL).
        executors = text("""SELECT array_agg(CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE a.grantee::regrole::text END
                ORDER BY a.grantee) FROM pg_proc p
            CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a
            WHERE p.oid = 'trg_payment_immutability()'::regprocedure AND a.privilege_type = 'EXECUTE'""")
        default_executors = text("""SELECT array_agg(CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE a.grantee::regrole::text END
                ORDER BY a.grantee) FROM pg_proc p
            CROSS JOIN LATERAL aclexplode(acldefault('f', p.proowner)) a
            WHERE p.oid = 'trg_payment_immutability()'::regprocedure AND a.privilege_type = 'EXECUTE'""")
        engine = create_engine(MIGRATOR_SYNC_URL)
        try:
            with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
                savepoint = db.begin_nested()
                try:
                    # The chain undoes 0208 and 0207 before 0192: the function 0192 made is the head's only until then.
                    importlib.import_module(MIRROR_REVISION).downgrade()
                    importlib.import_module(BOOKING_REVISION).downgrade()
                    current = db.execute(function).one()
                    assert current.prosecdef and "row_security=off" in current.proconfig
                    # Only the owner may execute the definer function; the runtime role may not.
                    assert db.scalar(executors) == ["app_migrator"]
                    assert not db.scalar(text("SELECT has_function_privilege('app_rw', 'trg_payment_immutability()', 'EXECUTE')"))
                    migration.downgrade()
                    restored = db.execute(function).one()
                    assert restored.prosrc == created
                    assert (restored.prosecdef, restored.proconfig) == (False, None)
                    assert restored.trigger == current.trigger
                    # The ACL 0018 left: its owner's default, EXECUTE for PUBLIC included.
                    assert db.scalar(executors) == db.scalar(default_executors) == ["PUBLIC", "app_migrator"]
                    assert db.scalar(text("SELECT has_function_privilege('app_rw', 'trg_payment_immutability()', 'EXECUTE')"))
                    migration.upgrade()
                    assert db.execute(function).one() == current
                    assert db.scalar(executors) == ["app_migrator"]
                finally:
                    savepoint.rollback()
        finally:
            engine.dispose()

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
# Test 2b: the reversal link of a payment that becomes booked (#720).
#   A booked payment's `reversed_by_id` is NULL or the id of its booked Storno of the same organization (#717 made it
#   so for the UPDATE of a booked row). The rule holds for every way a row becomes booked: the INSERT as booked, the
#   booking of a draft that already carries the link and the booking that sets the link in the same UPDATE. One rule
#   in `trg_payment_immutability()`, one refusal: SQLSTATE P0001, "GoBD: reversed_by_id of booked payment must ...".
# ===========================================================================
_LINKED_INSERT = (
    "INSERT INTO payment (id, organization_id, bank_account_id, property_id, direction, amount, value_date, "
    "payment_type, tenant_profile_id, lease_agreement_id, period_year, period_month, status, booked_at, booked_by, "
    "reverses_id, reversed_by_id) VALUES ")
BOOK_WAYS = ["insert_as_booked", "link_a_draft_then_book_it", "book_a_draft_and_link_it_in_one_update"]
LINK_REFUSAL = "GoBD.*reversed_by_id.*booked"
_LINK_DRAFT = "UPDATE payment SET reversed_by_id = :reversed_by WHERE id = :id"
_BOOK = "UPDATE payment SET status = 'booked', booked_at = :booked_at, booked_by = :booked_by WHERE id = :id"
_BOOK_AND_LINK = ("UPDATE payment SET reversed_by_id = :reversed_by, status = 'booked', booked_at = :booked_at, "
                  "booked_by = :booked_by WHERE id = :id")


def _linked_values(n: str = "") -> str:
    """The VALUES tuple of one payment, its parameters suffixed with `n` so that a statement can insert several."""
    return (f"(:id{n}, :org{n}, :acct{n}, :prop{n}, :direction{n}, 150.00, :vdate{n}, 'NK_VORAUSZAHLUNG', :tenant{n}, "
            f":lease{n}, 2025, 6, :status{n}, :booked_at{n}, :booked_by{n}, :reverses{n}, :reversed_by{n})")


def _linked_params(
    m6_seed, pid, n: str = "", *, org="a", status="booked", reverses=None, reversed_by=None, direction="OUT",
) -> dict:
    """Parameters of one `_linked_values` tuple; a Storno takes the direction opposite to its original's "OUT"."""
    other, booked = org == "b", status == "booked"
    return {
        f"id{n}": str(pid), f"org{n}": str(m6_seed["org_b_id" if other else "org_a_id"]), f"direction{n}": direction,
        f"acct{n}": str(m6_seed["acct_b_org" if other else "acct_a"]),
        f"prop{n}": str(m6_seed["prop_b_org" if other else "prop_a"]), f"vdate{n}": _VDATE,
        f"tenant{n}": str(m6_seed["tenant_b" if other else "tenant"]),
        f"lease{n}": None if other else str(m6_seed["lease"]), f"status{n}": status,
        f"booked_at{n}": _BOOKED_AT if booked else None,
        f"booked_by{n}": str(m6_seed["user_b_admin_id" if other else "user_a_admin_id"]) if booked else None,
        f"reverses{n}": None if reverses is None else str(reverses),
        f"reversed_by{n}": None if reversed_by is None else str(reversed_by),
    }


class TestPaymentBecomesBooked:
    @staticmethod
    async def _insert(
        session, m6_seed, pid=None, *, org="a", status="booked", reverses=None, reversed_by=None, direction="OUT",
    ):
        """Insert one payment of organization `org` ("a" or "b") as `app_rw`, its links set on insert."""
        pid = pid or uuid.uuid4()
        await _set_org(session, m6_seed["org_b_id" if org == "b" else "org_a_id"])
        await session.execute(
            text(_LINKED_INSERT + _linked_values()),
            _linked_params(m6_seed, pid, org=org, status=status, reverses=reverses, reversed_by=reversed_by,
                           direction=direction))
        await _set_org(session, m6_seed["org_a_id"])
        return pid

    @staticmethod
    async def _outcome(session, work) -> tuple[str, str]:
        """(SQLSTATE, message) of what `work` runs inside a savepoint; ("accepted", "") when it passes."""
        try:
            async with session.begin_nested():
                await work()
            return "accepted", ""
        except DBAPIError as exc:
            return getattr(exc.orig, "sqlstate", repr(exc)), str(exc.orig)

    @staticmethod
    async def _state(session, pid):
        """(status, reversed_by_id) of the payment, None when there is no such row."""
        row = (await session.execute(
            text("SELECT status, reversed_by_id FROM payment WHERE id = :id"), {"id": str(pid)})).first()
        return None if row is None else tuple(row)

    async def _book_a_draft(self, session, m6_seed, way, pid, claimed):
        """Book the existing draft `pid` in one of the two draft ways with `reversed_by_id` naming `claimed`.

        The second way links the draft first: a draft may carry any link its keys allow until it is booked, so that
        step must pass. Returns the outcome of the booking statement."""
        params = {"id": str(pid), "reversed_by": str(claimed), "booked_at": _BOOKED_AT,
                  "booked_by": str(m6_seed["user_a_admin_id"])}
        if way == "link_a_draft_then_book_it":
            await session.execute(text(_LINK_DRAFT), params)
            statement = _BOOK
        else:
            statement = _BOOK_AND_LINK
        return await self._outcome(session, lambda: session.execute(text(statement), params))

    async def _book(self, session, m6_seed, way, pid, claimed):
        """Make a new payment `pid` booked in one of the three ways, its `reversed_by_id` naming `claimed`."""
        if way == "insert_as_booked":
            params = _linked_params(m6_seed, pid, reversed_by=claimed)
            return await self._outcome(session, lambda: session.execute(text(_LINKED_INSERT + _linked_values()), params))
        await self._insert(session, m6_seed, pid, status="draft")
        return await self._book_a_draft(session, m6_seed, way, pid, claimed)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("way", BOOK_WAYS)
    @pytest.mark.parametrize("target", ["unrelated_booked_payment", "storno_of_another_payment", "itself"])
    async def test_a_payment_cannot_become_booked_naming_a_payment_that_is_not_its_storno(
        self, app_rw_session, m6_seed, way, target,
    ):
        """Each way a row becomes booked is refused like the later UPDATE of a booked row, and writes nothing booked."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        unrelated = await self._insert(session, m6_seed)
        other_original = await self._insert(session, m6_seed)
        other_storno = await self._insert(session, m6_seed, reverses=other_original, direction="IN")
        subject = uuid.uuid4()
        claimed = {"unrelated_booked_payment": unrelated, "storno_of_another_payment": other_storno,
                   "itself": subject}[target]
        sqlstate, message = await self._book(session, m6_seed, way, subject, claimed)
        state = await self._state(session, subject)
        await savepoint.rollback()
        assert sqlstate == "P0001", message
        assert re.search(LINK_REFUSAL, message) and f"(id={subject})" in message, message
        if way == "insert_as_booked":
            assert state is None
        else:
            # The draft is still a draft; the link of the second way was set on the draft, the third never was.
            assert state == ("draft", claimed if way == "link_a_draft_then_book_it" else None)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("way", BOOK_WAYS[1:])
    async def test_a_draft_storno_does_not_qualify(self, app_rw_session, m6_seed, way):
        """The Storno named at booking must itself be booked. A draft original with a booked Storno no longer exists
        (#721 books a Storno only for a booked original), so there is no accepting half of this test any more."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        subject, storno = uuid.uuid4(), uuid.uuid4()
        await self._insert(session, m6_seed, subject, status="draft")
        await self._insert(session, m6_seed, storno, status="draft", reverses=subject, direction="IN")
        refused = await self._book_a_draft(session, m6_seed, way, subject, storno)
        state = await self._state(session, subject)
        await savepoint.rollback()
        assert refused[0] == "P0001" and re.search(LINK_REFUSAL, refused[1]), refused
        assert state == ("draft", storno if way == "link_a_draft_then_book_it" else None)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("way", ["insert_as_booked", "a_draft_original"])
    async def test_a_payment_cannot_become_booked_naming_a_storno_that_was_booked_ahead_of_it(
        self, app_rw_session, m6_seed, way,
    ):
        """#717's rule accepts the booked Storno that reverses the row; since #721 that Storno itself is refused.

        A booked Storno needs a booked original that exists and that it mirrors, so a payment can never become booked
        while its Storno is already booked: the statement that inserts the Storno ahead of its original (which #720
        accepted) and a booked Storno naming a draft original both fail at the Storno, with the mirror refusal."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        subject, storno = uuid.uuid4(), uuid.uuid4()
        if way == "insert_as_booked":
            outcome = await self._outcome(session, lambda: session.execute(
                text(_LINKED_INSERT + _linked_values("1") + ", " + _linked_values("2")),
                {**_linked_params(m6_seed, storno, "1", reverses=subject, direction="IN"),
                 **_linked_params(m6_seed, subject, "2", reversed_by=storno)}))
        else:
            await self._insert(session, m6_seed, subject, status="draft")
            outcome = await self._outcome(session, lambda: self._insert(
                session, m6_seed, storno, reverses=subject, direction="IN"))
        states = [await self._state(session, identity) for identity in (subject, storno)]
        await savepoint.rollback()
        assert outcome[0] == "P0001" and re.search(STORNO_REFUSAL, outcome[1]), outcome
        assert states == [None if way == "insert_as_booked" else ("draft", None), None]

    @pytest.mark.asyncio
    async def test_an_insert_naming_a_storno_that_follows_it_in_the_statement_is_refused(self, app_rw_session, m6_seed):
        """The same two rows in the other order: the Storno does not exist yet when the original's row is checked."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        subject, storno = uuid.uuid4(), uuid.uuid4()
        sqlstate, message = await self._outcome(session, lambda: session.execute(
            text(_LINKED_INSERT + _linked_values("1") + ", " + _linked_values("2")),
            {**_linked_params(m6_seed, subject, "1", reversed_by=storno),
             **_linked_params(m6_seed, storno, "2", reverses=subject)}))
        states = [await self._state(session, identity) for identity in (subject, storno)]
        await savepoint.rollback()
        assert sqlstate == "P0001", message
        assert states == [None, None]

    @pytest.mark.asyncio
    async def test_the_controls_a_null_link_and_the_application_order_stay_accepted(self, app_rw_session, m6_seed):
        """Inserted as booked with no link, booked from a draft with no link, then reversed the way `reverse_payment` does."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        booked = await self._insert(session, m6_seed)
        draft = await self._insert(session, m6_seed, status="draft")
        await session.execute(text(_BOOK), {"id": str(draft), "booked_at": _BOOKED_AT,
                                            "booked_by": str(m6_seed["user_a_admin_id"])})
        for original in (booked, draft):
            storno = await self._insert(session, m6_seed, reverses=original, direction="IN")
            await session.execute(
                text("UPDATE payment SET reversed_by_id = :s WHERE id = :id"), {"s": str(storno), "id": str(original)})
            assert await self._state(session, original) == ("booked", storno)
        await savepoint.rollback()

    @pytest.mark.asyncio
    async def test_a_link_to_a_payment_of_another_organization_is_refused_by_the_rule_before_the_key(
        self, app_rw_session, m6_seed,
    ):
        """Naming a booked payment of organization B from organization A.

        On the insert and on the booking that sets the link the rule fires first (P0001): a BEFORE trigger runs ahead of
        the key, which is checked when the statement ends and would answer 23503. Setting the link on a draft, with no
        booking, is the key's alone (23503)."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        foreign = await self._insert(session, m6_seed, org="b")
        insert = await self._book(session, m6_seed, "insert_as_booked", uuid.uuid4(), foreign)
        book_and_link = await self._book(
            session, m6_seed, "book_a_draft_and_link_it_in_one_update", uuid.uuid4(), foreign)
        draft = await self._insert(session, m6_seed, status="draft")
        link_only = await self._outcome(session, lambda: session.execute(
            text("UPDATE payment SET reversed_by_id = :f WHERE id = :id"), {"f": str(foreign), "id": str(draft)}))
        await savepoint.rollback()
        assert [insert[0], book_and_link[0], link_only[0]] == ["P0001", "P0001", "23503"], (insert, book_and_link, link_only)

    @pytest.mark.asyncio
    async def test_the_rule_refuses_a_storno_of_another_organization_without_the_foreign_keys(
        self, migrator_session, m6_seed,
    ):
        """The rule's own organization comparison, with both keys dropped so that nothing but the trigger can refuse.

        Organization B books a Storno that claims to reverse the payment of organization A that is about to be booked.
        Since #721 the INSERT trigger refuses that Storno at its own insert (its original is not in organization B), so
        the Storno is built with that trigger disabled and the trigger is enabled again before the payment that names
        it is booked. The keys and the trigger are changed inside a savepoint that is rolled back in a `finally`: the
        fixture commits when the test ends, also when an assertion failed, and the shared database must never lose a
        key or a trigger."""
        session = migrator_session
        savepoint = await session.begin_nested()
        try:
            await session.execute(text("ALTER TABLE payment DROP CONSTRAINT fk_payment_reverses_id_tenant"))
            await session.execute(text("ALTER TABLE payment DROP CONSTRAINT fk_payment_reversed_by_id_tenant"))
            await session.execute(text("ALTER TABLE payment DISABLE TRIGGER trg_payment_immutability_insert"))
            await _set_org(session, m6_seed["org_a_id"])
            await session.execute(text("SET LOCAL ROLE app_rw"))
            ways = ("insert_as_booked", "book_a_draft_and_link_it_in_one_update")
            subjects = {way: uuid.uuid4() for way in ways}
            stornos_of_b = {way: await self._insert(session, m6_seed, org="b", reverses=subjects[way], direction="IN")
                            for way in ways}
            await session.execute(text("RESET ROLE"))
            # The inserts queued deferred checks on payment, which would block the ALTER TABLE.
            await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            await session.execute(text("ALTER TABLE payment ENABLE TRIGGER trg_payment_immutability_insert"))
            await session.execute(text("SET LOCAL ROLE app_rw"))
            outcomes = [await self._book(session, m6_seed, way, subjects[way], stornos_of_b[way]) for way in ways]
            assert [outcome[0] for outcome in outcomes] == ["P0001", "P0001"], outcomes
        finally:
            await savepoint.rollback()


# Revision 0207: the trigger on INSERT and the function that checks every way a row becomes booked.
BOOKING_REVISION = "migrations.versions.0207_payment_reversal_link_when_booked"
INSERT_TRIGGER = "trg_payment_immutability_insert"


def _function(db):
    """Body, definer rights, settings and ACL of `trg_payment_immutability()`."""
    return tuple(db.execute(text(
        "SELECT prosrc, prosecdef, proconfig, proacl::text FROM pg_proc WHERE oid = 'trg_payment_immutability()'::regprocedure"
    )).one())


def _payment_triggers(db):
    return [tuple(row) for row in db.execute(text(
        "SELECT tgname, pg_get_triggerdef(oid), tgenabled FROM pg_trigger "
        "WHERE tgrelid = 'public.payment'::regclass AND NOT tgisinternal ORDER BY tgname"))]


def _runtime_insert(db, m6_seed, pid, **links) -> str:
    """SQLSTATE of one insert as `app_rw` under organization A inside the owner's transaction, or "accepted"."""
    db.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(m6_seed["org_a_id"])})
    try:
        with db.begin_nested():
            db.execute(text("SET LOCAL ROLE app_rw"))
            db.execute(text(_LINKED_INSERT + _linked_values()), _linked_params(m6_seed, pid, **links))
        return "accepted"
    except DBAPIError as exc:
        return exc.orig.pgcode
    finally:
        db.execute(text("RESET ROLE"))


class TestBookedLinkRevision:
    def test_the_schema_checks_inserts_and_downgrade_restores_the_previous_function_and_triggers(self, m6_seed):
        migration = importlib.import_module(BOOKING_REVISION)
        previous = importlib.import_module(
            "migrations.versions.0192_payment_reversal_link_immutable").CURRENT_FUNCTION.split("$$")[1]
        with _rehearsal() as db:
            # The chain undoes 0208 before 0207: the function and trigger 0207 made are the head's only until then.
            importlib.import_module(MIRROR_REVISION).downgrade()
            head_function, head_triggers = _function(db), _payment_triggers(db)
            assert head_function[0] != previous
            assert INSERT_TRIGGER in [trigger[0] for trigger in head_triggers]
            unrelated = uuid.uuid4()
            assert _runtime_insert(db, m6_seed, unrelated) == "accepted"
            assert _runtime_insert(db, m6_seed, uuid.uuid4(), reversed_by=unrelated) == "P0001"

            migration.downgrade()
            # 0192's function exactly (body, definer rights, settings, ACL) and the triggers as they were before 0207.
            assert _function(db) == (previous, *head_function[1:])
            assert _payment_triggers(db) == [trigger for trigger in head_triggers if trigger[0] != INSERT_TRIGGER]
            # What the issue describes is possible again: a booked insert naming an unrelated booked payment.
            assert _runtime_insert(db, m6_seed, uuid.uuid4(), reversed_by=unrelated) == "accepted"

            migration.upgrade()
            assert (_function(db), _payment_triggers(db)) == (head_function, head_triggers)
            assert _runtime_insert(db, m6_seed, uuid.uuid4(), reversed_by=unrelated) == "P0001"


# ===========================================================================
# Test 2c: a booked Storno mirrors its original and is the only one for it (#721, 0208).
#   `reverse_payment` writes a Storno that equals its original in `REVERSAL_COPIED_COLUMNS` with the opposite direction,
#   and the organization import accepts only such pairs. The database took any booked payment as the Storno of another
#   booked payment (the same direction, another amount) and several booked payments naming one original. Half one is the
#   function `trg_payment_immutability()`, on INSERT as booked and on the booking of a draft; half two is the unique index
#   `uq_payment_booked_storno`.
# ===========================================================================
STORNO_REFUSAL = "GoBD.*Storno must mirror"
MIRROR_REVISION = "migrations.versions.0208_payment_storno_mirrors_original"
STORNO_INDEX = "uq_payment_booked_storno"

_PAYMENT_INSERT = (
    "INSERT INTO payment (id, organization_id, bank_account_id, property_id, management_mandate_id, direction, amount, "
    "value_date, payment_type, tenant_profile_id, owner_profile_id, lease_agreement_id, period_year, period_month, "
    "settlement_run_id, reserve_cost_entry_id, reserve_register_entry_id, status, booked_at, booked_by, reverses_id) "
    "VALUES (:id, :organization_id, :bank_account_id, :property_id, :management_mandate_id, :direction, :amount, "
    ":value_date, :payment_type, :tenant_profile_id, :owner_profile_id, :lease_agreement_id, :period_year, "
    ":period_month, :settlement_run_id, :reserve_cost_entry_id, :reserve_register_entry_id, :status, :booked_at, "
    ":booked_by, :reverses_id)")
_BOOK_DRAFT = "UPDATE payment SET status = 'booked', booked_at = :booked_at, booked_by = :booked_by WHERE id = :id"


def _payment_row(m6_seed, **overrides) -> dict:
    """Parameters of `_PAYMENT_INSERT`: a booked incoming rent advance of organization A; `overrides` replaces any."""
    return {
        "id": str(uuid.uuid4()), "organization_id": str(m6_seed["org_a_id"]), "bank_account_id": str(m6_seed["acct_a"]),
        "property_id": str(m6_seed["prop_a"]), "management_mandate_id": None, "direction": "IN",
        "amount": Decimal("150.00"), "value_date": _VDATE, "payment_type": "NK_VORAUSZAHLUNG",
        "tenant_profile_id": str(m6_seed["tenant"]), "owner_profile_id": None,
        "lease_agreement_id": str(m6_seed["lease"]), "period_year": 2025, "period_month": 6, "settlement_run_id": None,
        "reserve_cost_entry_id": None, "reserve_register_entry_id": None, "status": "booked",
        "booked_at": _BOOKED_AT, "booked_by": str(m6_seed["user_a_admin_id"]), "reverses_id": None, **overrides,
    }


def _storno_row(m6_seed, original: dict, **overrides) -> dict:
    """The Storno `reverse_payment` writes for `original`: its copied columns, the opposite direction, its own id."""
    copied = {column: original[column] for column in REVERSAL_COPIED_COLUMNS}
    opposite = "OUT" if original["direction"] == "IN" else "IN"
    return _payment_row(m6_seed, **{**copied, "direction": opposite, "reverses_id": original["id"], **overrides})


def _draft(row: dict) -> dict:
    return {**row, "status": "draft", "booked_at": None, "booked_by": None}


def _different_values(m6_seed) -> dict:
    """For each copied column but the mandate, a value that differs from `_payment_row`'s: what a forged Storno carries.

    `bank_account_id` and `property_id` differ one at a time, which breaks the composite key of the two; the owner breaks
    the counterparty check of a rent type; the three references name rows that do not exist. All of these are checked
    after the triggers, so a refusal with SQLSTATE P0001 comes from the rule and from nothing else."""
    return {
        "bank_account_id": str(m6_seed["acct_b"]), "property_id": str(m6_seed["prop_b"]),
        "amount": Decimal("149.99"), "value_date": date(2025, 6, 2), "payment_type": "KALTMIETE",
        "tenant_profile_id": str(m6_seed["tenant_d"]), "owner_profile_id": str(m6_seed["owner"]),
        "lease_agreement_id": None, "period_year": 2024, "period_month": 7,
        "settlement_run_id": str(uuid.uuid4()), "reserve_cost_entry_id": str(uuid.uuid4()),
        "reserve_register_entry_id": str(uuid.uuid4()),
    }


async def _put(session, m6_seed, row: dict) -> None:
    """Insert one payment as `app_rw` under its organization, then return to organization A."""
    await _set_org(session, row["organization_id"])
    await session.execute(text(_PAYMENT_INSERT), row)
    await _set_org(session, m6_seed["org_a_id"])


async def _book(session, m6_seed, pid: str) -> None:
    await session.execute(text(_BOOK_DRAFT), {
        "id": pid, "booked_at": _BOOKED_AT, "booked_by": str(m6_seed["user_a_admin_id"])})


_outcome = TestPaymentBecomesBooked._outcome


class TestBookedStornoMirrorsItsOriginal:
    @staticmethod
    async def _try(session, m6_seed, row: dict) -> tuple[str, str]:
        return await _outcome(session, lambda: _put(session, m6_seed, row))

    @pytest.mark.asyncio
    async def test_the_probes_of_the_issue_are_refused(self, app_rw_session, m6_seed):
        """A booked Storno in the original's direction for 1.00, and a second booked payment naming the same original."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        original = _payment_row(m6_seed)
        await _put(session, m6_seed, original)
        forged = _payment_row(m6_seed, direction=original["direction"], amount=Decimal("1.00"),
                              reverses_id=original["id"])
        first = await self._try(session, m6_seed, forged)
        forged_state = await TestPaymentBecomesBooked._state(session, forged["id"])
        # The second probe: the real Storno is booked and stays unlinked, then another payment names the same original.
        storno = _storno_row(m6_seed, original)
        await _put(session, m6_seed, storno)
        second = await self._try(session, m6_seed, _payment_row(
            m6_seed, direction=original["direction"], amount=Decimal("1.00"), reverses_id=original["id"]))
        mirrored_again = await self._try(session, m6_seed, _storno_row(m6_seed, original))
        await savepoint.rollback()
        assert first[0] == "P0001" and re.search(STORNO_REFUSAL, first[1]), first
        assert forged_state is None
        assert second[0] == "P0001" and re.search(STORNO_REFUSAL, second[1]), second
        assert mirrored_again[0] == "23505" and STORNO_INDEX in mirrored_again[1], mirrored_again

    @pytest.mark.asyncio
    @pytest.mark.parametrize("direction", ["IN", "OUT"])
    async def test_a_mirrored_storno_is_booked_and_linked_in_either_direction(
        self, app_rw_session, m6_seed, direction,
    ):
        """The control: what `reverse_payment` writes, then the link the application sets afterwards."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        original = _payment_row(m6_seed, direction=direction)
        await _put(session, m6_seed, original)
        storno = _storno_row(m6_seed, original)
        await _put(session, m6_seed, storno)
        await session.execute(text("UPDATE payment SET reversed_by_id = :s WHERE id = :id"),
                              {"s": storno["id"], "id": original["id"]})
        state = await TestPaymentBecomesBooked._state(session, original["id"])
        await savepoint.rollback()
        assert storno["direction"] != direction
        assert state == ("booked", uuid.UUID(storno["id"]))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("direction", ["IN", "OUT"])
    async def test_a_booked_storno_in_the_direction_of_its_original_is_refused(
        self, app_rw_session, m6_seed, direction,
    ):
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        original = _payment_row(m6_seed, direction=direction)
        await _put(session, m6_seed, original)
        outcome = await self._try(session, m6_seed, _storno_row(m6_seed, original, direction=direction))
        await savepoint.rollback()
        assert outcome[0] == "P0001" and re.search(STORNO_REFUSAL, outcome[1]), outcome

    @pytest.mark.asyncio
    @pytest.mark.parametrize("column", [column for column in REVERSAL_COPIED_COLUMNS if column != "management_mandate_id"])
    async def test_a_booked_storno_differing_from_its_original_in_one_copied_column_is_refused(
        self, app_rw_session, m6_seed, column,
    ):
        """Every column `reverse_payment` copies is compared; the mandate has its own test below.

        A column added to `REVERSAL_COPIED_COLUMNS` has no value in `_different_values` and fails this test until the
        database rule compares it and the table names a value for it."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        original = _payment_row(m6_seed)
        await _put(session, m6_seed, original)
        forged = _storno_row(m6_seed, original, **{column: _different_values(m6_seed)[column]})
        refused = await self._try(session, m6_seed, forged)
        forged_state = await TestPaymentBecomesBooked._state(session, forged["id"])
        # The control: the same Storno without the difference is accepted, so the column was the reason.
        accepted = await self._try(session, m6_seed, _storno_row(m6_seed, original))
        await savepoint.rollback()
        assert refused[0] == "P0001" and re.search(STORNO_REFUSAL, refused[1]), (column, refused)
        assert forged_state is None
        assert accepted == ("accepted", "")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("original_mandate, storno_mandate, accepted", [
        (None, None, True), ("first", "first", True),
        (None, "first", True),  # the account was classified into a scope after the original was booked
        ("first", None, False), ("first", "second", False)])
    async def test_a_storno_may_gain_the_mandate_of_a_later_account_scope_but_never_change_it(
        self, app_rw_session, m6_seed, original_mandate, storno_mandate, accepted,
    ):
        """The one exception, exactly the one the import allows (`payment_reversal_pairs`).

        The database derives the mandate from the account's scope when a payment is inserted, so a Storno booked after
        the account was classified carries a mandate that an older original lacks. `reverse_payment` writes no other
        pair: a mandate the original has is copied, and the scope trigger refuses a Storno that differs from it."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        second = str(uuid.uuid4())
        await session.execute(text(
            "INSERT INTO management_mandate (id, organization_id, mandate_type, label, start_date) "
            "VALUES (:id, :org, 'WEG', 'Zweites Mandat', :start)"),
            {"id": second, "org": str(m6_seed["org_a_id"]), "start": date(2025, 1, 1)})
        mandates = {None: None, "first": str(m6_seed["mandate_a_id"]), "second": second}
        original = _payment_row(m6_seed, management_mandate_id=mandates[original_mandate])
        await _put(session, m6_seed, original)
        outcome = await self._try(session, m6_seed, _storno_row(
            m6_seed, original, management_mandate_id=mandates[storno_mandate]))
        await savepoint.rollback()
        if accepted:
            assert outcome == ("accepted", "")
        else:
            assert outcome[0] == "P0001" and re.search(STORNO_REFUSAL, outcome[1]), outcome

    def test_the_storno_of_a_payment_booked_before_its_account_was_classified_gains_the_mandate_of_the_scope(
        self, m6_seed,
    ):
        """The exception as the writers produce it, not as the table above states it.

        The original is booked while the account has no scope, so its mandate is NULL. The account is then classified
        into a scope with a mandate, and the Storno `reverse_payment` writes (the copied columns, the mandate NULL
        among them) gets the mandate of the scope from the account trigger. Without the exception such an original
        could never be reversed."""
        with _rehearsal() as db:
            original = uuid.uuid4()
            assert _runtime_insert(db, m6_seed, original) == "accepted"
            scope, admin = uuid.uuid4(), str(m6_seed["user_a_admin_id"])
            keys = {"id": str(scope), "org": str(m6_seed["org_a_id"]), "prop": str(m6_seed["prop_a"]),
                    "mandate": str(m6_seed["mandate_a_id"]), "admin": admin, "bank": str(m6_seed["acct_a"])}
            db.execute(text("""INSERT INTO accounting_scope
                (id, organization_id, property_id, management_mandate_id, scope_type, label, valid_from, whole_property,
                 origin, created_by)
                VALUES (:id, :org, :prop, :mandate, 'WEG', 'Spaeter klassifiziert', '2025-01-01', true, 'REVIEWED', :admin)"""),
                keys)
            db.execute(text("""INSERT INTO scope_grant (organization_id, scope_id, user_id, capabilities, granted_by, reason)
                VALUES (:org, :id, :admin, ARRAY['read', 'prepare', 'confirm'], :admin, 'Mandate probe')"""), keys)
            db.execute(text("""INSERT INTO accounting_scope_binding
                (organization_id, property_id, scope_id, bank_account_id, origin, reason, reviewed_by)
                VALUES (:org, :prop, :id, :bank, 'REVIEWED', 'Classified after the original was booked', :admin)"""), keys)
            db.execute(text("SELECT set_config('app.current_user_id', :admin, true)"), keys)
            storno = uuid.uuid4()
            assert _runtime_insert(db, m6_seed, storno, reverses=original, direction="IN") == "accepted"
            mandates = db.execute(text("SELECT id, management_mandate_id FROM payment WHERE id IN (:a, :b)"),
                                  {"a": str(original), "b": str(storno)}).all()
            assert sorted(mandates) == sorted([(original, None), (storno, m6_seed["mandate_a_id"])])

    @pytest.mark.asyncio
    async def test_a_booked_storno_needs_a_booked_original_that_is_another_payment_of_its_organization(
        self, app_rw_session, m6_seed,
    ):
        """No original, a draft original (it could still be edited), the Storno itself, a payment of another organization."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        draft_original = _draft(_payment_row(m6_seed))
        await _put(session, m6_seed, draft_original)
        foreign = _payment_row(
            m6_seed, organization_id=str(m6_seed["org_b_id"]), bank_account_id=str(m6_seed["acct_b_org"]),
            property_id=str(m6_seed["prop_b_org"]), tenant_profile_id=str(m6_seed["tenant_b"]),
            lease_agreement_id=None, booked_by=str(m6_seed["user_b_admin_id"]))
        await _put(session, m6_seed, foreign)
        own_id = str(uuid.uuid4())
        outcomes = {
            "unknown": await self._try(session, m6_seed, _storno_row(m6_seed, _payment_row(m6_seed))),
            "draft": await self._try(session, m6_seed, _storno_row(m6_seed, draft_original)),
            "itself": await self._try(session, m6_seed, _payment_row(
                m6_seed, id=own_id, direction="OUT", reverses_id=own_id)),
            "foreign": await self._try(session, m6_seed, _payment_row(
                m6_seed, direction="OUT", reverses_id=foreign["id"])),
        }
        # The control: once the original is booked, the same Storno is accepted.
        await _book(session, m6_seed, draft_original["id"])
        accepted = await self._try(session, m6_seed, _storno_row(m6_seed, draft_original))
        await savepoint.rollback()
        for name, outcome in outcomes.items():
            assert outcome[0] == "P0001" and re.search(STORNO_REFUSAL, outcome[1]), (name, outcome)
        assert accepted == ("accepted", "")

    @pytest.mark.asyncio
    async def test_a_draft_storno_is_checked_when_it_is_booked_not_before(self, app_rw_session, m6_seed):
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        original = _payment_row(m6_seed)
        await _put(session, m6_seed, original)
        draft = _draft(_storno_row(m6_seed, original, amount=Decimal("1.00")))
        saved = await self._try(session, m6_seed, draft)
        booking = await _outcome(session, lambda: _book(session, m6_seed, draft["id"]))
        still_draft = await TestPaymentBecomesBooked._state(session, draft["id"])
        # Corrected while it is a draft, the same booking passes.
        await session.execute(text("UPDATE payment SET amount = :amount WHERE id = :id"),
                              {"amount": original["amount"], "id": draft["id"]})
        corrected = await _outcome(session, lambda: _book(session, m6_seed, draft["id"]))
        booked = await TestPaymentBecomesBooked._state(session, draft["id"])
        await savepoint.rollback()
        assert saved == ("accepted", "")
        assert booking[0] == "P0001" and re.search(STORNO_REFUSAL, booking[1]), booking
        assert still_draft == ("draft", None)
        assert corrected == ("accepted", "") and booked == ("booked", None)

    @pytest.mark.asyncio
    async def test_drafts_naming_one_original_coexist_until_the_first_is_booked(self, app_rw_session, m6_seed):
        """(b) binds a Storno when it is booked: two drafts are fine, the second to be booked is refused."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        original = _payment_row(m6_seed)
        await _put(session, m6_seed, original)
        first, second = (_draft(_storno_row(m6_seed, original)) for _ in range(2))
        saved = [await self._try(session, m6_seed, draft) for draft in (first, second)]
        first_booking = await _outcome(session, lambda: _book(session, m6_seed, first["id"]))
        second_booking = await _outcome(session, lambda: _book(session, m6_seed, second["id"]))
        states = [await TestPaymentBecomesBooked._state(session, draft["id"]) for draft in (first, second)]
        await savepoint.rollback()
        assert saved == [("accepted", "")] * 2
        assert first_booking == ("accepted", "")
        assert second_booking[0] == "23505" and STORNO_INDEX in second_booking[1], second_booking
        assert states == [("booked", None), ("draft", None)]

    @pytest.mark.asyncio
    async def test_every_original_keeps_its_own_storno(self, app_rw_session, m6_seed):
        """The control for (b): the uniqueness is per original, and the same ids in another organization never collide."""
        session = app_rw_session
        savepoint = await session.begin_nested()
        await _set_org(session, m6_seed["org_a_id"])
        originals = [_payment_row(m6_seed) for _ in range(2)]
        for original in originals:
            await _put(session, m6_seed, original)
        outcomes = [await self._try(session, m6_seed, _storno_row(m6_seed, original)) for original in originals]
        await savepoint.rollback()
        assert outcomes == [("accepted", "")] * 2

    @pytest.mark.asyncio
    async def test_the_database_rule_compares_exactly_the_columns_reverse_payment_copies(self, app_rw_session):
        """Drift guard: the columns the function compares are `REVERSAL_COPIED_COLUMNS`, the mandate as its exception."""
        source = (await app_rw_session.execute(text(
            "SELECT prosrc FROM pg_proc WHERE oid = 'trg_payment_immutability()'::regprocedure"))).scalar_one()
        match = re.search(
            r"\((original\.\w+(?:,\s*original\.\w+)*)\)\s+IS NOT DISTINCT FROM\s+\((NEW\.\w+(?:,\s*NEW\.\w+)*)\)", source)
        assert match, "the function compares the copied columns of the original and the Storno as two row values"
        originals = [name.strip().removeprefix("original.") for name in match.group(1).split(",")]
        stornos = [name.strip().removeprefix("NEW.") for name in match.group(2).split(",")]
        assert originals == stornos
        assert len(set(originals)) == len(originals) and "management_mandate_id" not in originals
        assert set(originals) | {"management_mandate_id"} == set(REVERSAL_COPIED_COLUMNS)
        assert re.search(r"original\.management_mandate_id IS NULL\s+OR original\.management_mandate_id = "
                         r"NEW\.management_mandate_id", source)


def _booked_storno_index(db) -> list[str]:
    return db.execute(text("SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'payment' "
                           "AND indexname = :name"), {"name": STORNO_INDEX}).scalars().all()


class TestStornoMirrorRevision:
    def test_the_head_schema_has_the_index_and_downgrade_restores_the_previous_function_and_drops_it(self, m6_seed):
        migration = importlib.import_module(MIRROR_REVISION)
        previous = importlib.import_module(BOOKING_REVISION).CURRENT_FUNCTION.split("$$")[1]
        with _rehearsal() as db:
            head_function, head_triggers, head_index = _function(db), _payment_triggers(db), _booked_storno_index(db)
            assert head_function[0] != previous
            assert len(head_index) == 1
            assert ("CREATE UNIQUE INDEX uq_payment_booked_storno ON public.payment USING btree "
                    "(organization_id, reverses_id)") in head_index[0]
            assert "'booked'" in head_index[0] and "reverses_id IS NOT NULL" in head_index[0]
            original = uuid.uuid4()
            assert _runtime_insert(db, m6_seed, original) == "accepted"
            same_direction = _runtime_insert(db, m6_seed, uuid.uuid4(), reverses=original)
            mirrored = _runtime_insert(db, m6_seed, uuid.uuid4(), reverses=original, direction="IN")
            second = _runtime_insert(db, m6_seed, uuid.uuid4(), reverses=original, direction="IN")
            assert (same_direction, mirrored, second) == ("P0001", "accepted", "23505")

            migration.downgrade()
            # 0207's function exactly (body, definer rights, settings, ACL); no trigger changes in either direction.
            assert _function(db) == (previous, *head_function[1:])
            assert _payment_triggers(db) == head_triggers
            assert _booked_storno_index(db) == []
            # What the issue describes is possible again: a Storno in the direction of its original, and a second one.
            assert _runtime_insert(db, m6_seed, uuid.uuid4(), reverses=original) == "accepted"

    def test_upgrade_refuses_a_second_booked_storno_of_an_original_and_changes_nothing(self, m6_seed):
        migration = importlib.import_module(MIRROR_REVISION)
        with _rehearsal() as db:
            head_function, head_triggers = _function(db), _payment_triggers(db)
            migration.downgrade()
            downgraded = _function(db)
            first, second = uuid.uuid4(), uuid.uuid4()
            for original in (first, second):
                assert _runtime_insert(db, m6_seed, original) == "accepted"
            # Two originals with two and three booked payments naming them: five rows, whatever they look like.
            for original, names in ((first, 2), (second, 3)):
                for _ in range(names):
                    assert _runtime_insert(db, m6_seed, uuid.uuid4(), reverses=original) == "accepted"
            # A draft naming an original is no violation: it is not counted.
            assert _runtime_insert(db, m6_seed, uuid.uuid4(), status="draft", reverses=first) == "accepted"
            with pytest.raises(DBAPIError) as refused:
                with db.begin_nested():
                    migration.upgrade()
            message = str(refused.value.orig)
            assert refused.value.orig.pgcode == "P0001"
            assert "Cannot upgrade 0208: 5 booked payment(s) name an original that another booked payment also names" in message
            # The count is all it says: no id of a payment or of an organization. The hint names the query that lists them.
            hint = refused.value.orig.diag.message_hint
            assert "SELECT p.organization_id, p.reverses_id, count(*)" in hint
            assert not any(str(identity) in message + hint
                           for identity in [first, second, m6_seed["org_a_id"], m6_seed["org_b_id"]])
            assert _booked_storno_index(db) == [] and _function(db) == downgraded
            assert head_function != downgraded and _payment_triggers(db) == head_triggers


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

    @staticmethod
    def _booking(m6_seed, value_date, counterparty):
        body = {"bank_account_id": str(m6_seed["acct_a"]), "direction": "IN", "amount": "150.00",
                "value_date": value_date, "period_year": int(value_date[:4]),
                "period_month": int(value_date[5:7])}
        if counterparty == "owner":
            return {**body, "payment_type": "SONDERZAHLUNG", "owner_profile_id": str(m6_seed["owner"])}
        return {**body, "payment_type": "KALTMIETE", "tenant_profile_id": str(m6_seed["tenant"]),
                "lease_agreement_id": str(m6_seed["lease"])}

    async def _book_then_reverse(self, client, m6_seed, payment_clock, *, value_date, booked_on, reversed_on,
                                 counterparty="tenant"):
        return await book_then_reverse(client, self._booking(m6_seed, value_date, counterparty), payment_clock,
                                       booked_on=booked_on, reversed_on=reversed_on)

    @pytest.mark.asyncio
    async def test_reversal_of_an_old_payment_is_retained_from_its_own_booking_date(
            self, client_a_clerk, m6_seed, payment_clock):
        original, storno = await self._book_then_reverse(
            client_a_clerk, m6_seed, payment_clock,
            value_date="2016-03-15", booked_on="2016-03-16T09:00:00", reversed_on="2026-11-08T09:00:00")
        # The original keeps the anchor of its own booking; the Storno is a new record of 2026:
        # 31.12.2026 + 8 years, while it still belongs to the accounting period of 2016.
        assert original["retention_until"] == "2024-12-31"
        assert storno["retention_until"] == "2034-12-31"
        assert storno["value_date"] == "2016-03-15"
        assert storno["booked_at"].startswith("2026-11-08")
        fetched = await client_a_clerk.get(f"/api/payments/{storno['id']}")
        assert fetched.json()["retention_until"] == "2034-12-31"
        assert (await client_a_clerk.get(f"/api/payments/{original['id']}")).json()["retention_until"] == "2024-12-31"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("counterparty", ["tenant", "owner"])
    @pytest.mark.parametrize(
        ("value_date", "reversed_on", "original_until", "storno_until"),
        [
            pytest.param("2026-05-01", "2026-11-08T09:00:00", "2034-12-31", "2034-12-31", id="same-year"),
            pytest.param("2028-02-01", "2026-11-08T09:00:00", "2036-12-31", "2036-12-31", id="future-value-date"),
            pytest.param("2016-03-15", "2026-12-31T23:59:59", "2024-12-31", "2034-12-31", id="last-second-of-year"),
            pytest.param("2016-03-15", "2027-01-01T00:00:00", "2024-12-31", "2035-12-31", id="first-second-of-next-year"),
            pytest.param("2026-12-31", "2027-01-01T00:00:00", "2034-12-31", "2035-12-31", id="value-date-in-the-year-before"),
        ],
    )
    async def test_reversal_retention_is_never_earlier_than_the_original_and_follows_its_booking_year(
            self, client_a_clerk, m6_seed, payment_clock, counterparty, value_date, reversed_on,
            original_until, storno_until):
        original, storno = await self._book_then_reverse(
            client_a_clerk, m6_seed, payment_clock, counterparty=counterparty, value_date=value_date,
            booked_on="2026-11-08T08:00:00", reversed_on=reversed_on)
        assert (original["retention_until"], storno["retention_until"]) == (original_until, storno_until)
        assert storno["retention_until"] >= original["retention_until"]
        assert storno["value_date"] == original["value_date"] == value_date

    @pytest.mark.asyncio
    async def test_a_reversal_cannot_be_reversed_so_it_never_gets_a_second_anchor(
            self, client_a_clerk, m6_seed, payment_clock):
        _, storno = await self._book_then_reverse(
            client_a_clerk, m6_seed, payment_clock,
            value_date="2016-03-15", booked_on="2026-11-08T08:00:00", reversed_on="2026-11-08T09:00:00")
        payment_clock("2030-01-01T00:00:00")
        again = await client_a_clerk.post(f"/api/payments/{storno['id']}/reverse")
        assert again.status_code == 409, again.text
        assert (await client_a_clerk.get(f"/api/payments/{storno['id']}")).json()["retention_until"] == "2034-12-31"

    @pytest.mark.asyncio
    async def test_a_reversed_payment_answers_409_to_a_second_reversal_and_keeps_its_link(
            self, client_a_clerk, m6_seed, payment_clock):
        original, storno = await self._book_then_reverse(
            client_a_clerk, m6_seed, payment_clock,
            value_date="2025-06-01", booked_on="2026-11-08T08:00:00", reversed_on="2026-11-08T09:00:00")
        again = await client_a_clerk.post(f"/api/payments/{original['id']}/reverse")
        assert again.status_code == 409, again.text
        assert again.json()["detail"] == "This payment has already been reversed"
        assert (await client_a_clerk.get(f"/api/payments/{original['id']}")).json()["reversed_by_id"] == storno["id"]

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


async def _single_hausgeld(client, world, *, value_date, amount="600.00", owner="anna", **period):
    """A Hausgeld payment through the single path (create, then book); without a month unless given."""
    created = await client.post("/api/payments", json={
        "bank_account_id": world["account"], "direction": "IN", "amount": amount, "value_date": value_date,
        "payment_type": "HAUSGELD_VORSCHUSS", "owner_profile_id": world["owners"][owner], **period})
    assert created.status_code == 201, created.text
    booked = await client.post(f"/api/payments/{created.json()['id']}/book")
    assert booked.status_code == 200, booked.text
    return booked.json()


def _anna_booked(offer, world):
    return {row["period_month"]: row["booked"] for row in offer if row["owner_profile_id"] == world["owners"]["anna"]}


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
    async def test_a_payment_without_a_month_marks_the_month_of_its_value_date_as_booked(self, client_a_admin):
        # The owner statement assigns a Hausgeld payment without a period to the month of its value date.
        world = await _hausgeld_world(client_a_admin)
        base = PLAN_PATH.format(plan=world["plan"])
        single = await _single_hausgeld(client_a_admin, world, value_date="2026-01-05")
        offer = (await client_a_admin.get(base)).json()
        assert _anna_booked(offer, world) == {month: month == 1 for month in ALL_MONTHS}
        refused = await client_a_admin.post(base, json=_booking(world, "anna", [1, 2]))
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["code"] == "HAUSGELD_MONTH_BOOKED"
        assert [row["id"] for row in await _booked(client_a_admin, world)] == [single["id"]]
        statement = await client_a_admin.get(f"/api/payments/mietkonto/owner/{world['owners']['anna']}", params={
            "period_start": "2026-01-01", "period_end": "2026-12-31", "economic_plan_id": world["plan"]})
        assert statement.json()["ist_total"] == "600.00"  # the statement counts the same payment
        february = await client_a_admin.post(base, json=_booking(world, "anna", [2]))
        assert february.status_code == 201, february.text

    @pytest.mark.asyncio
    async def test_a_payment_without_a_month_assigned_by_a_year_marks_the_month_of_its_value_date(self, client_a_admin):
        world = await _hausgeld_world(client_a_admin)
        await _single_hausgeld(client_a_admin, world, value_date="2026-03-02", period_year=2026)
        offer = (await client_a_admin.get(PLAN_PATH.format(plan=world["plan"]))).json()
        assert _anna_booked(offer, world) == {month: month == 3 for month in ALL_MONTHS}

    @pytest.mark.asyncio
    async def test_a_payment_without_a_month_dated_in_another_year_does_not_block_the_month(self, client_a_admin):
        world = await _hausgeld_world(client_a_admin)
        base = PLAN_PATH.format(plan=world["plan"])
        await _single_hausgeld(client_a_admin, world, value_date="2027-01-05")
        await _single_hausgeld(client_a_admin, world, value_date="2025-12-31")
        assert not any(row["booked"] for row in (await client_a_admin.get(base)).json())
        january = await client_a_admin.post(base, json=_booking(world, "anna", [1]))
        assert january.status_code == 201, january.text

    @pytest.mark.asyncio
    async def test_a_reversed_payment_without_a_month_frees_its_month_again(self, client_a_admin):
        world = await _hausgeld_world(client_a_admin)
        base = PLAN_PATH.format(plan=world["plan"])
        single = await _single_hausgeld(client_a_admin, world, value_date="2026-01-05")
        assert _anna_booked((await client_a_admin.get(base)).json(), world)[1] is True
        assert (await client_a_admin.post(f"/api/payments/{single['id']}/reverse")).status_code == 201
        assert not any(row["booked"] for row in (await client_a_admin.get(base)).json())
        january = await client_a_admin.post(base, json=_booking(world, "anna", [1]))
        assert january.status_code == 201, january.text

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
