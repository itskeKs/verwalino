"""ADR-0006: valid_to convention -- API is INCLUSIVE, storage is EXCLUSIVE.

Follow-up to the M3 read-edge fix (settlement_service `-1`). The DB keeps its
HALF-OPEN `[valid_from, valid_to)` storage (valid_to = first NON-occupied day,
EXCLUDE `[)`, point-in-time `valid_to > target`) UNCHANGED. The inclusive<->exclusive
conversion lives at the API boundary (app.core.temporal):

  * WRITE  (POST /api/leases, /api/ownership): user enters the inclusive last
    occupied day -> stored as +1 day (exclusive).
  * DISPLAY (LeaseOut/OwnershipOut on list/detail/at_date): stored exclusive
    bound -> returned as -1 day (inclusive), so the user sees what they entered.
  * ENGINE read-edge (settlement_service): UNCHANGED, still -1; the conversion
    is NOT applied twice.

These tests drive the REAL endpoints (and read the DB directly to pin the storage
side), so they prove the round-trip consistency end-to-end. The pure-math of the
conversion helpers is also unit-tested at the bottom.

Why no existing test breaks: the M1 raw-DB assertions in test_m1_stammdaten.py
read `valid_to` straight from the table via SQL (storage side, still exclusive)
and the engine tests build engine inputs directly (already tag-inclusive). No
prior test read `valid_to` *through* the lease/ownership API, so the API now
having inclusive semantics is purely additive coverage.
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


# ===========================================================================
# Seed: minimal Mischobjekt (one unit) + an owner + two tenant profiles, via
# the migrator so it persists across the API client's own transactions. Leases
# and ownership are NOT seeded here -- the tests create them via the API, which
# is exactly the write edge under test. A full-year GRUNDSTEUER cost lets the
# adjacency test compute a pure time split on a single unit (unit bears 100%).
# ===========================================================================
@pytest.fixture(scope="function")
def convention_seed(seed_data):
    import psycopg2

    org = str(seed_data["org_a_id"])
    ids = {
        "property": uuid.uuid4(),
        "u1": uuid.uuid4(),
        "person_owner": uuid.uuid4(),
        "owner_profile": uuid.uuid4(),
        # A SECOND owner profile, used only by the ownership-roundtrip test. The
        # ownership_history EXCLUDE is per (unit_id, owner_profile_id), and the
        # seed already gives `owner_profile` an OPEN-ENDED row on u1; a disjoint
        # period for a *different* owner profile is the clean way to exercise the
        # write/display conversion without colliding with that open-ended row.
        "owner_profile_2": uuid.uuid4(),
        "person_owner_2": uuid.uuid4(),
        "p_a": uuid.uuid4(),
        "p_b": uuid.uuid4(),
        "tp_a": uuid.uuid4(),
        "tp_b": uuid.uuid4(),
    }
    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()

        def cat(code):
            cur.execute(
                "SELECT id FROM cost_category WHERE code=%s AND plan_version=1", (code,)
            )
            return cur.fetchone()[0]

        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
            (str(ids["property"]), org, "Konvention Mischhaus", "MISCH"),
        )
        # Single unit, area 80 -> bears 100% of any Flaeche cost; time split is pure.
        cur.execute(
            "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, heated_area, mea) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["u1"]), org, str(ids["property"]), "1", "WOHNUNG", 80, 80, 300),
        )
        # Owner owns the unit the whole time (so a vacancy day, if any, is attributable).
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
            (str(ids["person_owner"]), org, "Olga", "Owner"),
        )
        cur.execute(
            "INSERT INTO owner_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["owner_profile"]), org, str(ids["person_owner"])),
        )
        cur.execute(
            "INSERT INTO ownership_history (id, organization_id, unit_id, owner_profile_id, valid_from, mea_share) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (str(uuid.uuid4()), org, str(ids["u1"]), str(ids["owner_profile"]), date(2000, 1, 1), None),
        )
        # Second owner profile (no ownership_history row of its own -> free for the
        # roundtrip test to add a disjoint period via the API).
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
            (str(ids["person_owner_2"]), org, "Otto", "Owner2"),
        )
        cur.execute(
            "INSERT INTO owner_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["owner_profile_2"]), org, str(ids["person_owner_2"])),
        )
        for pkey, tpkey, first in [("p_a", "tp_a", "Anna"), ("p_b", "tp_b", "Bert")]:
            cur.execute(
                "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
                (str(ids[pkey]), org, first, "Mieter"),
            )
            cur.execute(
                "INSERT INTO tenant_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
                (str(ids[tpkey]), org, str(ids[pkey])),
            )

        # A confirmed, full-year GRUNDSTEUER (WOHNFLAECHE), 1200.00. With one unit,
        # the unit bears the whole 1200.00, split purely by time between tenants.
        ce = uuid.uuid4()
        cur.execute(
            "INSERT INTO cost_entry (id, organization_id, property_id, cost_category_id, cost_category_plan_version, "
            "invoice_date, service_period_from, service_period_to, gross_amount, tenant_distribution_key, "
            "tenant_apportionable, status, source, confirmed_at, confirmed_by) "
            "VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,'confirmed','MANUAL',now(),%s)",
            (
                str(ce), org, str(ids["property"]), str(cat("GRUNDSTEUER")),
                date(2025, 6, 1), date(2025, 1, 1), date(2025, 12, 31),
                Decimal("1200.00"), "WOHNFLAECHE", "UMLAGEFAEHIG",
                str(seed_data["user_a_admin_id"]),
            ),
        )
        ids["ce_grundsteuer"] = ce
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {**seed_data, **{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in ids.items()}}


async def _db_valid_to(app_rw_session, org_id, table, row_id):
    """Read the RAW stored valid_to straight from the table (storage side)."""
    await app_rw_session.execute(
        text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(org_id)}
    )
    row = await app_rw_session.execute(
        text(f"SELECT valid_to FROM {table} WHERE id=:id"), {"id": str(row_id)}
    )
    return row.scalar_one()


# ===========================================================================
# Roundtrip: API inclusive in -> API inclusive out; DB exclusive in between.
# ===========================================================================
class TestLeaseRoundtrip:
    @pytest.mark.asyncio
    async def test_inclusive_in_inclusive_out_exclusive_stored(
        self, client_a_admin, convention_seed, app_rw_session
    ):
        """POST 'endet 30.06.' (2025-06-30) -> GET returns 2025-06-30; DB has 2025-07-01."""
        resp = await client_a_admin.post("/api/leases", json={
            "unit_id": convention_seed["u1"],
            "tenant_profile_id": convention_seed["tp_a"],
            "valid_from": "2025-01-01",
            "valid_to": "2025-06-30",  # inclusive last occupied day, as the user enters it
            "base_rent": "800.00",
            "advance_payment": "100.00",
        })
        assert resp.status_code == 201, resp.text
        created = resp.json()
        lease_id = created["id"]

        # (a) POST response echoes the inclusive date the user sent.
        assert created["valid_to"] == "2025-06-30"
        assert created["valid_from"] == "2025-01-01"

        # (b) GET list returns the same inclusive date.
        listed = await client_a_admin.get(f"/api/leases?unit_id={convention_seed['u1']}")
        assert listed.status_code == 200
        row = next(r for r in listed.json() if r["id"] == lease_id)
        assert row["valid_to"] == "2025-06-30"

        # (c) Storage holds the EXCLUSIVE bound: 30.06. + 1 = 01.07.
        stored = await _db_valid_to(app_rw_session, convention_seed["org_a_id"], "lease_agreement", lease_id)
        assert stored == date(2025, 7, 1)

    @pytest.mark.asyncio
    async def test_open_ended_lease_stays_null(
        self, client_a_admin, convention_seed, app_rw_session
    ):
        """valid_to omitted/null -> stored NULL and returned null (open-ended)."""
        resp = await client_a_admin.post("/api/leases", json={
            "unit_id": convention_seed["u1"],
            "tenant_profile_id": convention_seed["tp_a"],
            "valid_from": "2025-01-01",
            "valid_to": None,
            "base_rent": "800.00",
            "advance_payment": "100.00",
        })
        assert resp.status_code == 201, resp.text
        lease_id = resp.json()["id"]
        assert resp.json()["valid_to"] is None
        stored = await _db_valid_to(app_rw_session, convention_seed["org_a_id"], "lease_agreement", lease_id)
        assert stored is None

    @pytest.mark.asyncio
    async def test_at_date_path_returns_inclusive_valid_to(
        self, client_a_admin, convention_seed
    ):
        """The tenant_at_date path also returns the inclusive valid_to (routes via _to_out).

        Point query for a day INSIDE the lease (15.06.) must find the tenant whose
        contract 'ends 30.06.'; the returned valid_to is the inclusive 2025-06-30,
        not the stored exclusive 2025-07-01. (at_date itself is a point in time and
        is NOT a period bound -- it is correctly NOT converted.)
        """
        await client_a_admin.post("/api/leases", json={
            "unit_id": convention_seed["u1"],
            "tenant_profile_id": convention_seed["tp_a"],
            "valid_from": "2025-01-01",
            "valid_to": "2025-06-30",
            "base_rent": "800.00",
            "advance_payment": "100.00",
        })
        # 15.06. is inside -> found; 01.07. is the first non-occupied day -> not found.
        inside = await client_a_admin.get(
            f"/api/leases?unit_id={convention_seed['u1']}&at_date=2025-06-15"
        )
        assert inside.status_code == 200
        assert len(inside.json()) == 1
        assert inside.json()[0]["valid_to"] == "2025-06-30"

        # 30.06. (the inclusive last day) is still occupied -> found.
        last_day = await client_a_admin.get(
            f"/api/leases?unit_id={convention_seed['u1']}&at_date=2025-06-30"
        )
        assert len(last_day.json()) == 1

        # 01.07. (first non-occupied day) -> nobody (no successor seeded).
        after = await client_a_admin.get(
            f"/api/leases?unit_id={convention_seed['u1']}&at_date=2025-07-01"
        )
        assert after.json() == []


class TestOwnershipRoundtrip:
    @pytest.mark.asyncio
    async def test_inclusive_in_inclusive_out_exclusive_stored(
        self, client_a_admin, convention_seed, app_rw_session
    ):
        """Same roundtrip for ownership: in 2025-06-30, out 2025-06-30, stored 2025-07-01.

        Uses owner_profile_2 (which has no seeded period) on a disjoint window, so
        the EXCLUDE on (unit_id, owner_profile_id) is not the thing under test --
        only the +1/-1 conversion is.
        """
        resp = await client_a_admin.post("/api/ownership", json={
            "unit_id": convention_seed["u1"],
            "owner_profile_id": convention_seed["owner_profile_2"],
            "valid_from": "2030-01-01",
            "valid_to": "2030-06-30",
            "mea_share": "300.000000",
        })
        assert resp.status_code == 201, resp.text
        oh_id = resp.json()["id"]
        assert resp.json()["valid_to"] == "2030-06-30"
        stored = await _db_valid_to(app_rw_session, convention_seed["org_a_id"], "ownership_history", oh_id)
        assert stored == date(2030, 7, 1)


# ===========================================================================
# Adjacency end-to-end: 'A bis 30.06.' + 'B ab 01.07.' via API -> no 409, then
# compute -> A = Jan-Jun, B = Jul-Dec day-exact, Sigma time = 1, no phantom day.
# This composes the WRITE edge (+1) with the ENGINE read-edge (-1): the two must
# cancel so that adjacent inclusive inputs become DB-adjacent storage rows that
# the engine then splits without losing or doubling the boundary day.
# ===========================================================================
class TestAdjacencyEndToEnd:
    async def _create_run(self, client, seed, lease_id):
        resp = await client.post("/api/settlements", json={
            'heating_advance_payments_total': '0.00', "property_id": seed["property"],
            "lease_agreement_id": lease_id,
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
            "advance_payments_total": "0.00",
        })
        assert resp.status_code == 201, resp.text
        from tests.test_financial_input_policy import attest
        coverage = await attest(client, "settlement", resp.json()["id"],
                                {'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': "MANUAL", "advance_payments_total": "0.00", "advance_payments_source": "MANUAL"})
        coverage.raise_for_status()
        return resp.json()["id"]

    async def _post_lease(self, client, seed, tp_key, valid_from, valid_to):
        resp = await client.post("/api/leases", json={
            "unit_id": seed["u1"],
            "tenant_profile_id": seed[tp_key],
            "valid_from": valid_from,
            "valid_to": valid_to,
            "base_rent": "800.00",
            "advance_payment": "100.00",
        })
        return resp

    @pytest.mark.asyncio
    async def test_adjacent_leases_via_api_no_409(self, client_a_admin, convention_seed):
        """'A bis 2025-06-30' then 'B ab 2025-07-01' both accepted (no false EXCLUDE 409).

        Pre-ADR-0006, if a user had entered 30.06. as the raw stored bound, B from
        01.07. was fine -- but the user's *inclusive* 30.06. and 01.07. only become
        non-overlapping because the write edge maps 30.06. -> stored 01.07., which
        is exactly B's valid_from -> daterange [.,2025-07-01) and [2025-07-01,.) do
        not overlap under '[)'.
        """
        ra = await self._post_lease(client_a_admin, convention_seed, "tp_a", "2025-01-01", "2025-06-30")
        assert ra.status_code == 201, ra.text
        rb = await self._post_lease(client_a_admin, convention_seed, "tp_b", "2025-07-01", None)
        assert rb.status_code == 201, rb.text  # no false overlap 409

    @pytest.mark.asyncio
    async def test_adjacent_split_is_day_exact_and_loses_no_cent(
        self, client_a_admin, convention_seed
    ):
        """A = 01.01.-30.06. (181 d), B = 01.07.-31.12. (184 d); 181+184=365; A+B cost = 1200.00.

        Drives the public POST (write edge) + /compute (engine read-edge). If the
        write edge were missing (-> A stored with valid_to=2025-06-30), the engine
        read-edge would compute A's last day as 2025-06-29 and inject a phantom
        1-day vacancy (A+B < 1200.00). If the read-edge were double-applied, the day
        counts would shift again. Both failure modes are caught by the exact 365 /
        1200.00 assertions below.
        """
        ra = await self._post_lease(client_a_admin, convention_seed, "tp_a", "2025-01-01", "2025-06-30")
        rb = await self._post_lease(client_a_admin, convention_seed, "tp_b", "2025-07-01", None)
        lease_a, lease_b = ra.json()["id"], rb.json()["id"]

        run_a = await self._create_run(client_a_admin, convention_seed, lease_a)
        run_b = await self._create_run(client_a_admin, convention_seed, lease_b)
        ca = await client_a_admin.post(f"/api/settlements/{run_a}/compute", json={})
        cb = await client_a_admin.post(f"/api/settlements/{run_b}/compute", json={})
        assert ca.status_code == 200, ca.text
        assert cb.status_code == 200, cb.text

        ga = next(ln for ln in ca.json()["lines"] if ln["cost_category_code"] == "GRUNDSTEUER")
        gb = next(ln for ln in cb.json()["lines"] if ln["cost_category_code"] == "GRUNDSTEUER")

        # Day-exact split: A Jan-Jun = 181, B Jul-Dec = 184, total 365.
        assert ga["time_share_days"] == 181
        assert gb["time_share_days"] == 184
        assert ga["time_share_total_days"] == gb["time_share_total_days"] == 365
        assert ga["time_share_days"] + gb["time_share_days"] == 365  # Sigma time = 1, no phantom day

        # 1200 * 181/365 = 595.07 ; 1200 * 184/365 = 604.93 ; sum = full unit cost.
        assert ga["amount"] == "595.07"
        assert gb["amount"] == "604.93"
        assert Decimal(ga["amount"]) + Decimal(gb["amount"]) == Decimal("1200.00")


# ===========================================================================
# Off-by-one guard: a single test that fails if EITHER the write (+1) or the
# display (-1) conversion is removed. It pins both ends against one POST.
# ===========================================================================
class TestOffByOneGuard:
    @pytest.mark.asyncio
    async def test_write_and_display_conversions_are_both_required(
        self, client_a_admin, convention_seed, app_rw_session
    ):
        """One lease 'endet 30.06.' pins both edges:

          * DROP the write (+1): storage would hold 2025-06-30, the DB assert
            (== 2025-07-01) goes RED.
          * DROP the display (-1): GET would return 2025-07-01, the GET assert
            (== 2025-06-30) goes RED.
          * DOUBLE-apply on display (-2): GET would return 2025-06-29, RED.

        The two stored-vs-returned facts cannot both hold unless exactly one +1 is
        applied on write and exactly one -1 on display.
        """
        resp = await client_a_admin.post("/api/leases", json={
            "unit_id": convention_seed["u1"],
            "tenant_profile_id": convention_seed["tp_b"],
            "valid_from": "2025-03-01",
            "valid_to": "2025-06-30",
            "base_rent": "800.00",
            "advance_payment": "100.00",
        })
        assert resp.status_code == 201, resp.text
        lease_id = resp.json()["id"]

        returned = resp.json()["valid_to"]
        stored = await _db_valid_to(app_rw_session, convention_seed["org_a_id"], "lease_agreement", lease_id)

        # Inclusive on the wire, exclusive in storage, and they differ by exactly 1 day.
        assert returned == "2025-06-30"          # display -1 required
        assert stored == date(2025, 7, 1)        # write +1 required
        assert (stored - date.fromisoformat(returned)).days == 1


# ===========================================================================
# Unit test: the conversion helpers themselves (single source of truth).
# ===========================================================================
class TestTemporalHelpers:
    def test_write_edge_adds_one_day(self):
        from app.core.temporal import inclusive_last_day_to_db_exclusive as w

        assert w(date(2025, 6, 30)) == date(2025, 7, 1)
        assert w(date(2025, 12, 31)) == date(2026, 1, 1)   # year boundary
        assert w(date(2024, 2, 29)) == date(2024, 3, 1)    # leap day
        assert w(None) is None

    def test_display_edge_subtracts_one_day(self):
        from app.core.temporal import db_exclusive_to_api_inclusive as d

        assert d(date(2025, 7, 1)) == date(2025, 6, 30)
        assert d(date(2026, 1, 1)) == date(2025, 12, 31)
        assert d(None) is None

    def test_roundtrip_is_identity_on_inclusive_value(self):
        from app.core.temporal import (
            db_exclusive_to_api_inclusive as d,
            inclusive_last_day_to_db_exclusive as w,
        )

        for day in (date(2025, 1, 1), date(2025, 6, 30), date(2025, 12, 31), date(2024, 2, 29)):
            assert d(w(day)) == day

    def test_engine_alias_equals_display_alias(self):
        """ADR-0006: the engine read-edge and the API display edge are the SAME math."""
        from app.core.temporal import (
            db_exclusive_to_api_inclusive as d,
            db_exclusive_to_engine_inclusive as e,
        )

        for stored in (date(2025, 7, 1), date(2026, 1, 1), date(2025, 4, 1)):
            assert e(stored) == d(stored)
        assert e(None) is d(None) is None
