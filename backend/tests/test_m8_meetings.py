"""M8 tests -- owners meeting, agenda items, resolutions, register entries
(Beschluss-Sammlung), immutability, RLS, sequential numbering, and PDF smoke.

Uses the same fixtures as M0-M7 (seed_data, app_rw_session, migrator_session,
client_a_admin/clerk/readonly, client_b_admin).

Test groups:
  1. Pure integration: meeting lifecycle (create -> hold -> protocol).
  2. Agenda items: sequential numbering, CRUD.
  3. Resolutions: create, link to TOP, delete guard.
  4. Register entries (Beschluss-Sammlung):
     - Sequential numbering (entry 1, entry 2, ...).
     - Immutability: UPDATE wording/number/date FAILS, challenge annotation OK.
     - DELETE always FAILS.
     - No gaps (DELETE forbidden -> no gaps possible).
  5. RLS / fail-closed: org B cannot see org A's meetings.
  6. RBAC: read-only cannot create/delete.
  7. PDF smoke: Niederschrift + Beschluss-Sammlung.
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
# Seed: WEG property for M8 tests
# ===========================================================================
@pytest.fixture(scope="session")
def m8_seed(seed_data):
    """Create a WEG property in org_a and org_b for M8 tests."""
    import psycopg2

    org_a = str(seed_data["org_a_id"])
    org_b = str(seed_data["org_b_id"])
    ids = {
        "prop_m8": uuid.uuid4(),
        "prop_b_m8": uuid.uuid4(),
    }

    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type, street, zip_code, city) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["prop_m8"]), org_a, "WEG Testhaus", "WEG",
             "Teststr. 1", "10115", "Berlin"),
        )
        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) "
            "VALUES (%s,%s,%s,%s)",
            (str(ids["prop_b_m8"]), org_b, "WEG Beta", "WEG"),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {**seed_data, **{k: v for k, v in ids.items()}}


# ===========================================================================
# 1. Meeting lifecycle
# ===========================================================================
class TestMeetingLifecycle:
    @pytest.mark.asyncio
    async def test_create_and_get_meeting(self, client_a_admin, m8_seed):
        resp = await client_a_admin.post("/api/meetings", json={
            "property_id": str(m8_seed["prop_m8"]),
            "meeting_date": "2026-03-15",
            "meeting_type": "ORDENTLICH",
            "location": "Buero Berlin",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "PLANNED"
        assert data["meeting_type"] == "ORDENTLICH"
        assert data["location"] == "Buero Berlin"

        # GET
        mid = data["id"]
        resp2 = await client_a_admin.get(f"/api/meetings/{mid}")
        assert resp2.status_code == 200
        assert resp2.json()["id"] == mid

    @pytest.mark.asyncio
    async def test_mark_held(self, client_a_admin, m8_seed):
        resp = await client_a_admin.post("/api/meetings", json={
            "property_id": str(m8_seed["prop_m8"]),
            "meeting_date": "2026-04-01",
            "meeting_type": "AUSSERORDENTLICH",
            "location": "Online",
        })
        mid = resp.json()["id"]

        resp2 = await client_a_admin.post(f"/api/meetings/{mid}/hold")
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "HELD"

        # Cannot hold again
        resp3 = await client_a_admin.post(f"/api/meetings/{mid}/hold")
        assert resp3.status_code == 409

    @pytest.mark.asyncio
    async def test_cannot_delete_held_meeting(self, client_a_admin, m8_seed):
        resp = await client_a_admin.post("/api/meetings", json={
            "property_id": str(m8_seed["prop_m8"]),
            "meeting_date": "2026-05-01",
            "meeting_type": "ORDENTLICH",
            "location": "Buero",
        })
        mid = resp.json()["id"]
        await client_a_admin.post(f"/api/meetings/{mid}/hold")

        resp2 = await client_a_admin.delete(f"/api/meetings/{mid}")
        assert resp2.status_code == 409

    @pytest.mark.asyncio
    async def test_delete_planned_meeting(self, client_a_admin, m8_seed):
        resp = await client_a_admin.post("/api/meetings", json={
            "property_id": str(m8_seed["prop_m8"]),
            "meeting_date": "2026-06-01",
            "meeting_type": "ORDENTLICH",
            "location": "Temp",
        })
        mid = resp.json()["id"]

        resp2 = await client_a_admin.delete(f"/api/meetings/{mid}")
        assert resp2.status_code == 204

    @pytest.mark.asyncio
    async def test_update_held_meeting_only_protocol(self, client_a_admin, m8_seed):
        resp = await client_a_admin.post("/api/meetings", json={
            "property_id": str(m8_seed["prop_m8"]),
            "meeting_date": "2026-06-10",
            "meeting_type": "ORDENTLICH",
            "location": "Buero",
        })
        mid = resp.json()["id"]
        await client_a_admin.post(f"/api/meetings/{mid}/hold")

        # Can update protocol text
        resp2 = await client_a_admin.put(f"/api/meetings/{mid}", json={
            "protocol_text": "Protokoll der Versammlung...",
        })
        assert resp2.status_code == 200
        assert resp2.json()["protocol_text"] == "Protokoll der Versammlung..."

        # Cannot change date of HELD meeting
        resp3 = await client_a_admin.put(f"/api/meetings/{mid}", json={
            "meeting_date": "2026-07-01",
        })
        assert resp3.status_code == 409

    @pytest.mark.asyncio
    async def test_list_meetings_by_property(self, client_a_admin, m8_seed):
        prop_id = str(m8_seed["prop_m8"])
        resp = await client_a_admin.get(f"/api/meetings?property_id={prop_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        for m in data:
            assert m["property_id"] == prop_id


# ===========================================================================
# 2. Agenda items
# ===========================================================================
class TestAgendaItems:
    @pytest.mark.asyncio
    async def test_create_and_list_agenda_items(self, client_a_admin, m8_seed):
        resp = await client_a_admin.post("/api/meetings", json={
            "property_id": str(m8_seed["prop_m8"]),
            "meeting_date": "2026-07-01",
            "meeting_type": "ORDENTLICH",
            "location": "Buero",
        })
        mid = resp.json()["id"]

        # Create TOPs
        r1 = await client_a_admin.post(f"/api/meetings/{mid}/agenda", json={
            "meeting_id": mid,
            "title": "Begruessung",
        })
        assert r1.status_code == 201
        assert r1.json()["item_number"] == 1

        r2 = await client_a_admin.post(f"/api/meetings/{mid}/agenda", json={
            "meeting_id": mid,
            "title": "Jahresabrechnung",
            "description": "Beschluss ueber die Jahresabrechnung 2025",
        })
        assert r2.status_code == 201
        assert r2.json()["item_number"] == 2

        # List
        resp3 = await client_a_admin.get(f"/api/meetings/{mid}/agenda")
        assert resp3.status_code == 200
        items = resp3.json()
        assert len(items) >= 2
        assert items[0]["item_number"] == 1
        assert items[1]["item_number"] == 2

    @pytest.mark.asyncio
    async def test_delete_agenda_item(self, client_a_admin, m8_seed):
        resp = await client_a_admin.post("/api/meetings", json={
            "property_id": str(m8_seed["prop_m8"]),
            "meeting_date": "2026-07-02",
            "meeting_type": "ORDENTLICH",
            "location": "Buero",
        })
        mid = resp.json()["id"]
        r1 = await client_a_admin.post(f"/api/meetings/{mid}/agenda", json={
            "meeting_id": mid,
            "title": "Loeschen-Test",
        })
        item_id = r1.json()["id"]
        resp2 = await client_a_admin.delete(f"/api/meetings/agenda/{item_id}")
        assert resp2.status_code == 204


# ===========================================================================
# 3. Resolutions
# ===========================================================================
class TestResolutions:
    @pytest.mark.asyncio
    async def test_create_resolution_with_top(self, client_a_admin, m8_seed):
        # Meeting + TOP
        resp = await client_a_admin.post("/api/meetings", json={
            "property_id": str(m8_seed["prop_m8"]),
            "meeting_date": "2026-08-01",
            "meeting_type": "ORDENTLICH",
            "location": "Buero",
        })
        mid = resp.json()["id"]
        r_top = await client_a_admin.post(f"/api/meetings/{mid}/agenda", json={
            "meeting_id": mid,
            "title": "Wirtschaftsplan",
        })
        top_id = r_top.json()["id"]

        # Resolution linked to TOP
        r_res = await client_a_admin.post("/api/meetings/resolutions", json={
            "meeting_id": mid,
            "agenda_item_id": top_id,
            "wording": "Der Wirtschaftsplan fuer 2026 wird genehmigt.",
            "votes_yes": 7,
            "votes_no": 2,
            "votes_abstain": 1,
            "result": "ANGENOMMEN",
            "majority_type": "einfache Mehrheit",
        })
        assert r_res.status_code == 201
        data = r_res.json()
        assert data["result"] == "ANGENOMMEN"
        assert data["votes_yes"] == 7
        assert data["agenda_item_id"] == top_id

    @pytest.mark.asyncio
    async def test_create_standalone_resolution(self, client_a_admin, m8_seed):
        resp = await client_a_admin.post("/api/meetings", json={
            "property_id": str(m8_seed["prop_m8"]),
            "meeting_date": "2026-08-02",
            "meeting_type": "ORDENTLICH",
            "location": "Buero",
        })
        mid = resp.json()["id"]

        r_res = await client_a_admin.post("/api/meetings/resolutions", json={
            "meeting_id": mid,
            "wording": "Der Verwalter wird fuer 2025 entlastet.",
            "votes_yes": 10,
            "votes_no": 0,
            "votes_abstain": 0,
            "result": "ANGENOMMEN",
        })
        assert r_res.status_code == 201
        assert r_res.json()["agenda_item_id"] is None


# ===========================================================================
# 4. Register entries (Beschluss-Sammlung)
# ===========================================================================
class TestRegisterEntries:
    @pytest.mark.asyncio
    async def test_enter_into_register_sequential(self, client_a_admin, m8_seed):
        """Two resolutions get sequential numbers 1 and 2 in the register."""
        # Fresh meeting
        resp = await client_a_admin.post("/api/meetings", json={
            "property_id": str(m8_seed["prop_m8"]),
            "meeting_date": "2026-09-01",
            "meeting_type": "ORDENTLICH",
            "location": "Saal 1",
        })
        mid = resp.json()["id"]

        # Two resolutions
        r1 = await client_a_admin.post("/api/meetings/resolutions", json={
            "meeting_id": mid,
            "wording": "Beschluss eins: Abrechnung genehmigt.",
            "votes_yes": 5, "votes_no": 0, "votes_abstain": 0,
            "result": "ANGENOMMEN",
        })
        r2 = await client_a_admin.post("/api/meetings/resolutions", json={
            "meeting_id": mid,
            "wording": "Beschluss zwei: Ruecklage erhoehen.",
            "votes_yes": 4, "votes_no": 1, "votes_abstain": 0,
            "result": "ANGENOMMEN",
        })

        # Enter into register
        e1 = await client_a_admin.post(
            f"/api/meetings/resolutions/{r1.json()['id']}/register"
        )
        assert e1.status_code == 201
        # sequence_number is relative to property; may not be 1 if other tests
        # already inserted entries for this property
        seq1 = e1.json()["sequence_number"]
        assert seq1 >= 1

        e2 = await client_a_admin.post(
            f"/api/meetings/resolutions/{r2.json()['id']}/register"
        )
        assert e2.status_code == 201
        seq2 = e2.json()["sequence_number"]
        assert seq2 == seq1 + 1, f"Expected {seq1 + 1}, got {seq2} (lueckenlos)"

    @pytest.mark.asyncio
    async def test_duplicate_register_entry_rejected(self, client_a_admin, m8_seed):
        """Entering the same resolution twice should fail."""
        resp = await client_a_admin.post("/api/meetings", json={
            "property_id": str(m8_seed["prop_m8"]),
            "meeting_date": "2026-09-02",
            "meeting_type": "ORDENTLICH",
            "location": "Saal 2",
        })
        mid = resp.json()["id"]

        r = await client_a_admin.post("/api/meetings/resolutions", json={
            "meeting_id": mid,
            "wording": "Einmal-Beschluss.",
            "votes_yes": 5, "votes_no": 0, "votes_abstain": 0,
            "result": "ANGENOMMEN",
        })
        rid = r.json()["id"]

        e1 = await client_a_admin.post(f"/api/meetings/resolutions/{rid}/register")
        assert e1.status_code == 201

        e2 = await client_a_admin.post(f"/api/meetings/resolutions/{rid}/register")
        assert e2.status_code == 409

    @pytest.mark.asyncio
    async def test_challenge_annotation(self, client_a_admin, m8_seed):
        """Setting the Anfechtungsvermerk must succeed."""
        resp = await client_a_admin.post("/api/meetings", json={
            "property_id": str(m8_seed["prop_m8"]),
            "meeting_date": "2026-09-03",
            "meeting_type": "ORDENTLICH",
            "location": "Saal 3",
        })
        mid = resp.json()["id"]

        r = await client_a_admin.post("/api/meetings/resolutions", json={
            "meeting_id": mid,
            "wording": "Anfechtbar.",
            "votes_yes": 3, "votes_no": 2, "votes_abstain": 0,
            "result": "ANGENOMMEN",
        })
        rid = r.json()["id"]

        e = await client_a_admin.post(f"/api/meetings/resolutions/{rid}/register")
        eid = e.json()["id"]

        # Set annotation
        resp2 = await client_a_admin.put(
            f"/api/meetings/register/entry/{eid}/challenge",
            json={"annotation": "Angefochten am 01.10.2026 durch Eigentuemer Mueller."},
        )
        assert resp2.status_code == 200
        assert resp2.json()["challenge_annotation"].startswith("Angefochten")

        # Clear annotation
        resp3 = await client_a_admin.put(
            f"/api/meetings/register/entry/{eid}/challenge",
            json={"annotation": None},
        )
        assert resp3.status_code == 200
        assert resp3.json()["challenge_annotation"] is None

    @pytest.mark.asyncio
    async def test_cannot_delete_resolution_in_register(self, client_a_admin, m8_seed):
        """A resolution that is in the register cannot be deleted."""
        resp = await client_a_admin.post("/api/meetings", json={
            "property_id": str(m8_seed["prop_m8"]),
            "meeting_date": "2026-09-04",
            "meeting_type": "ORDENTLICH",
            "location": "Saal 4",
        })
        mid = resp.json()["id"]

        r = await client_a_admin.post("/api/meetings/resolutions", json={
            "meeting_id": mid,
            "wording": "Geschuetzt.",
            "votes_yes": 5, "votes_no": 0, "votes_abstain": 0,
            "result": "ANGENOMMEN",
        })
        rid = r.json()["id"]

        await client_a_admin.post(f"/api/meetings/resolutions/{rid}/register")

        resp2 = await client_a_admin.delete(f"/api/meetings/resolutions/{rid}")
        assert resp2.status_code == 409

    @pytest.mark.asyncio
    async def test_list_register_entries(self, client_a_admin, m8_seed):
        prop_id = str(m8_seed["prop_m8"])
        resp = await client_a_admin.get(f"/api/meetings/register/{prop_id}")
        assert resp.status_code == 200
        entries = resp.json()
        assert isinstance(entries, list)
        # Check sequential ordering
        for i in range(1, len(entries)):
            assert entries[i]["sequence_number"] > entries[i - 1]["sequence_number"]


# ===========================================================================
# 4b. Register immutability (DB-level trigger tests)
# ===========================================================================
class TestRegisterImmutability:
    @pytest.mark.asyncio
    async def test_update_wording_fails(self, migrator_session, m8_seed):
        """UPDATE wording on a register entry must be rejected by the trigger."""
        # Find any existing register entry (from tests above via seed)
        # We insert one directly via SQL to test the trigger.
        org_a = str(m8_seed["org_a_id"])
        prop_id = str(m8_seed["prop_m8"])

        # First create supporting data: meeting + resolution
        meeting_id = str(uuid.uuid4())
        resolution_id = str(uuid.uuid4())
        entry_id = str(uuid.uuid4())

        await migrator_session.execute(text(
            "INSERT INTO owners_meeting (id, organization_id, property_id, meeting_date, "
            "meeting_type, status, location) VALUES (:id, :org, :prop, :dt, :mt, :st, :loc)"
        ), {"id": meeting_id, "org": org_a, "prop": prop_id,
            "dt": date(2026, 1, 1), "mt": "ORDENTLICH", "st": "HELD", "loc": "Test"})

        await migrator_session.execute(text(
            "INSERT INTO resolution (id, organization_id, meeting_id, wording, "
            "votes_yes, votes_no, votes_abstain, result) "
            "VALUES (:id, :org, :mid, :w, :vy, :vn, :va, :r)"
        ), {"id": resolution_id, "org": org_a, "mid": meeting_id,
            "w": "Trigger-Test-Wortlaut", "vy": 5, "vn": 0, "va": 0, "r": "ANGENOMMEN"})

        # Get next sequence number
        result = await migrator_session.execute(text(
            "SELECT COALESCE(MAX(sequence_number), 0) + 1 AS next_seq "
            "FROM resolution_register_entry WHERE organization_id = :org AND property_id = :prop"
        ), {"org": org_a, "prop": prop_id})
        next_seq = result.scalar_one()

        await migrator_session.execute(text(
            "INSERT INTO resolution_register_entry (id, organization_id, property_id, "
            "sequence_number, entry_date, location, wording, resolution_id) "
            "VALUES (:id, :org, :prop, :seq, :dt, :loc, :w, :rid)"
        ), {"id": entry_id, "org": org_a, "prop": prop_id, "seq": next_seq,
            "dt": date(2026, 1, 1), "loc": "Test", "w": "Original-Wortlaut",
            "rid": resolution_id})

        # Try to UPDATE wording -> must fail
        with pytest.raises(Exception, match="Immutability.*wording"):
            await migrator_session.execute(text(
                "UPDATE resolution_register_entry SET wording = 'Geaendert' WHERE id = :id"
            ), {"id": entry_id})

    @pytest.mark.asyncio
    async def test_update_sequence_number_fails(self, migrator_session, m8_seed):
        """UPDATE sequence_number on a register entry must be rejected."""
        org_a = str(m8_seed["org_a_id"])
        prop_id = str(m8_seed["prop_m8"])

        meeting_id = str(uuid.uuid4())
        resolution_id = str(uuid.uuid4())
        entry_id = str(uuid.uuid4())

        await migrator_session.execute(text(
            "INSERT INTO owners_meeting (id, organization_id, property_id, meeting_date, "
            "meeting_type, status, location) VALUES (:id, :org, :prop, :dt, :mt, :st, :loc)"
        ), {"id": meeting_id, "org": org_a, "prop": prop_id,
            "dt": date(2026, 2, 1), "mt": "ORDENTLICH", "st": "HELD", "loc": "Test"})

        await migrator_session.execute(text(
            "INSERT INTO resolution (id, organization_id, meeting_id, wording, "
            "votes_yes, votes_no, votes_abstain, result) "
            "VALUES (:id, :org, :mid, :w, :vy, :vn, :va, :r)"
        ), {"id": resolution_id, "org": org_a, "mid": meeting_id,
            "w": "Seq-Test", "vy": 5, "vn": 0, "va": 0, "r": "ANGENOMMEN"})

        result = await migrator_session.execute(text(
            "SELECT COALESCE(MAX(sequence_number), 0) + 1 AS next_seq "
            "FROM resolution_register_entry WHERE organization_id = :org AND property_id = :prop"
        ), {"org": org_a, "prop": prop_id})
        next_seq = result.scalar_one()

        await migrator_session.execute(text(
            "INSERT INTO resolution_register_entry (id, organization_id, property_id, "
            "sequence_number, entry_date, location, wording, resolution_id) "
            "VALUES (:id, :org, :prop, :seq, :dt, :loc, :w, :rid)"
        ), {"id": entry_id, "org": org_a, "prop": prop_id, "seq": next_seq,
            "dt": date(2026, 2, 1), "loc": "Test", "w": "Seq-Test-Wortlaut",
            "rid": resolution_id})

        with pytest.raises(Exception, match="Immutability.*sequence_number"):
            await migrator_session.execute(text(
                "UPDATE resolution_register_entry SET sequence_number = 9999 WHERE id = :id"
            ), {"id": entry_id})

    @pytest.mark.asyncio
    async def test_delete_register_entry_fails(self, migrator_session, m8_seed):
        """DELETE of any register entry must be rejected by the trigger."""
        org_a = str(m8_seed["org_a_id"])
        prop_id = str(m8_seed["prop_m8"])

        meeting_id = str(uuid.uuid4())
        resolution_id = str(uuid.uuid4())
        entry_id = str(uuid.uuid4())

        await migrator_session.execute(text(
            "INSERT INTO owners_meeting (id, organization_id, property_id, meeting_date, "
            "meeting_type, status, location) VALUES (:id, :org, :prop, :dt, :mt, :st, :loc)"
        ), {"id": meeting_id, "org": org_a, "prop": prop_id,
            "dt": date(2026, 3, 1), "mt": "ORDENTLICH", "st": "HELD", "loc": "Test"})

        await migrator_session.execute(text(
            "INSERT INTO resolution (id, organization_id, meeting_id, wording, "
            "votes_yes, votes_no, votes_abstain, result) "
            "VALUES (:id, :org, :mid, :w, :vy, :vn, :va, :r)"
        ), {"id": resolution_id, "org": org_a, "mid": meeting_id,
            "w": "Delete-Test", "vy": 5, "vn": 0, "va": 0, "r": "ANGENOMMEN"})

        result = await migrator_session.execute(text(
            "SELECT COALESCE(MAX(sequence_number), 0) + 1 AS next_seq "
            "FROM resolution_register_entry WHERE organization_id = :org AND property_id = :prop"
        ), {"org": org_a, "prop": prop_id})
        next_seq = result.scalar_one()

        await migrator_session.execute(text(
            "INSERT INTO resolution_register_entry (id, organization_id, property_id, "
            "sequence_number, entry_date, location, wording, resolution_id) "
            "VALUES (:id, :org, :prop, :seq, :dt, :loc, :w, :rid)"
        ), {"id": entry_id, "org": org_a, "prop": prop_id, "seq": next_seq,
            "dt": date(2026, 3, 1), "loc": "Test", "w": "Delete-Test-Wortlaut",
            "rid": resolution_id})

        with pytest.raises(Exception, match="Immutability.*DELETE"):
            await migrator_session.execute(text(
                "DELETE FROM resolution_register_entry WHERE id = :id"
            ), {"id": entry_id})

    @pytest.mark.asyncio
    async def test_challenge_annotation_allowed(self, migrator_session, m8_seed):
        """UPDATE challenge_annotation on a register entry must succeed."""
        org_a = str(m8_seed["org_a_id"])
        prop_id = str(m8_seed["prop_m8"])

        meeting_id = str(uuid.uuid4())
        resolution_id = str(uuid.uuid4())
        entry_id = str(uuid.uuid4())

        await migrator_session.execute(text(
            "INSERT INTO owners_meeting (id, organization_id, property_id, meeting_date, "
            "meeting_type, status, location) VALUES (:id, :org, :prop, :dt, :mt, :st, :loc)"
        ), {"id": meeting_id, "org": org_a, "prop": prop_id,
            "dt": date(2026, 4, 1), "mt": "ORDENTLICH", "st": "HELD", "loc": "Test"})

        await migrator_session.execute(text(
            "INSERT INTO resolution (id, organization_id, meeting_id, wording, "
            "votes_yes, votes_no, votes_abstain, result) "
            "VALUES (:id, :org, :mid, :w, :vy, :vn, :va, :r)"
        ), {"id": resolution_id, "org": org_a, "mid": meeting_id,
            "w": "Annotation-Test", "vy": 5, "vn": 0, "va": 0, "r": "ANGENOMMEN"})

        result = await migrator_session.execute(text(
            "SELECT COALESCE(MAX(sequence_number), 0) + 1 AS next_seq "
            "FROM resolution_register_entry WHERE organization_id = :org AND property_id = :prop"
        ), {"org": org_a, "prop": prop_id})
        next_seq = result.scalar_one()

        await migrator_session.execute(text(
            "INSERT INTO resolution_register_entry (id, organization_id, property_id, "
            "sequence_number, entry_date, location, wording, resolution_id) "
            "VALUES (:id, :org, :prop, :seq, :dt, :loc, :w, :rid)"
        ), {"id": entry_id, "org": org_a, "prop": prop_id, "seq": next_seq,
            "dt": date(2026, 4, 1), "loc": "Test", "w": "Annotation-Test-Wortlaut",
            "rid": resolution_id})

        # This must NOT raise
        await migrator_session.execute(text(
            "UPDATE resolution_register_entry SET challenge_annotation = 'Angefochten' "
            "WHERE id = :id"
        ), {"id": entry_id})

        # Verify
        row = await migrator_session.execute(text(
            "SELECT challenge_annotation FROM resolution_register_entry WHERE id = :id"
        ), {"id": entry_id})
        assert row.scalar_one() == "Angefochten"

    @pytest.mark.asyncio
    async def test_update_entry_date_fails(self, migrator_session, m8_seed):
        """UPDATE entry_date on a register entry must be rejected."""
        org_a = str(m8_seed["org_a_id"])
        prop_id = str(m8_seed["prop_m8"])

        meeting_id = str(uuid.uuid4())
        resolution_id = str(uuid.uuid4())
        entry_id = str(uuid.uuid4())

        await migrator_session.execute(text(
            "INSERT INTO owners_meeting (id, organization_id, property_id, meeting_date, "
            "meeting_type, status, location) VALUES (:id, :org, :prop, :dt, :mt, :st, :loc)"
        ), {"id": meeting_id, "org": org_a, "prop": prop_id,
            "dt": date(2026, 5, 1), "mt": "ORDENTLICH", "st": "HELD", "loc": "Test"})

        await migrator_session.execute(text(
            "INSERT INTO resolution (id, organization_id, meeting_id, wording, "
            "votes_yes, votes_no, votes_abstain, result) "
            "VALUES (:id, :org, :mid, :w, :vy, :vn, :va, :r)"
        ), {"id": resolution_id, "org": org_a, "mid": meeting_id,
            "w": "Date-Test", "vy": 5, "vn": 0, "va": 0, "r": "ANGENOMMEN"})

        result = await migrator_session.execute(text(
            "SELECT COALESCE(MAX(sequence_number), 0) + 1 AS next_seq "
            "FROM resolution_register_entry WHERE organization_id = :org AND property_id = :prop"
        ), {"org": org_a, "prop": prop_id})
        next_seq = result.scalar_one()

        await migrator_session.execute(text(
            "INSERT INTO resolution_register_entry (id, organization_id, property_id, "
            "sequence_number, entry_date, location, wording, resolution_id) "
            "VALUES (:id, :org, :prop, :seq, :dt, :loc, :w, :rid)"
        ), {"id": entry_id, "org": org_a, "prop": prop_id, "seq": next_seq,
            "dt": date(2026, 5, 1), "loc": "Test", "w": "Date-Test-Wortlaut",
            "rid": resolution_id})

        with pytest.raises(Exception, match="Immutability.*entry_date"):
            await migrator_session.execute(text(
                "UPDATE resolution_register_entry SET entry_date = '2099-01-01' WHERE id = :id"
            ), {"id": entry_id})


# ===========================================================================
# 5. RLS / tenant isolation
# ===========================================================================
class TestRLSIsolation:
    @pytest.mark.asyncio
    async def test_org_b_cannot_see_org_a_meetings(self, client_a_admin, client_b_admin, m8_seed):
        """Org B must not see org A's meetings."""
        # Create a meeting in org A
        resp = await client_a_admin.post("/api/meetings", json={
            "property_id": str(m8_seed["prop_m8"]),
            "meeting_date": "2026-10-01",
            "meeting_type": "ORDENTLICH",
            "location": "Org-A-Only",
        })
        mid = resp.json()["id"]

        # Org B tries to GET it -> 404
        resp2 = await client_b_admin.get(f"/api/meetings/{mid}")
        assert resp2.status_code == 404

    @pytest.mark.asyncio
    async def test_org_b_cannot_see_org_a_register(self, client_a_admin, client_b_admin, m8_seed):
        prop_a = str(m8_seed["prop_m8"])
        resp = await client_b_admin.get(f"/api/meetings/register/{prop_a}")
        assert resp.status_code == 200
        # Should be empty (org B cannot see org A's entries)
        assert resp.json() == []


# ===========================================================================
# 6. RBAC
# ===========================================================================
class TestRBAC:
    @pytest.mark.asyncio
    async def test_readonly_cannot_create_meeting(self, client_a_readonly, m8_seed):
        resp = await client_a_readonly.post("/api/meetings", json={
            "property_id": str(m8_seed["prop_m8"]),
            "meeting_date": "2026-11-01",
            "meeting_type": "ORDENTLICH",
            "location": "Nope",
        })
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_readonly_can_list_meetings(self, client_a_readonly, m8_seed):
        resp = await client_a_readonly.get("/api/meetings")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_clerk_can_create_meeting(self, client_a_clerk, m8_seed):
        resp = await client_a_clerk.post("/api/meetings", json={
            "property_id": str(m8_seed["prop_m8"]),
            "meeting_date": "2026-11-02",
            "meeting_type": "ORDENTLICH",
            "location": "Clerk-Buero",
        })
        assert resp.status_code == 201


# ===========================================================================
# 7. PDF smoke tests
# ===========================================================================
class TestPDFSmoke:
    @pytest.mark.asyncio
    async def test_niederschrift_pdf(self, client_a_admin, m8_seed):
        """Niederschrift PDF renders without error and starts with %PDF."""
        # Create meeting with TOP + resolution
        resp = await client_a_admin.post("/api/meetings", json={
            "property_id": str(m8_seed["prop_m8"]),
            "meeting_date": "2026-12-01",
            "meeting_type": "ORDENTLICH",
            "location": "PDF-Test-Buero",
        })
        mid = resp.json()["id"]

        await client_a_admin.post(f"/api/meetings/{mid}/agenda", json={
            "meeting_id": mid,
            "title": "PDF-Test TOP",
        })

        await client_a_admin.post("/api/meetings/resolutions", json={
            "meeting_id": mid,
            "wording": "PDF-Test-Beschluss genehmigt.",
            "votes_yes": 5, "votes_no": 0, "votes_abstain": 0,
            "result": "ANGENOMMEN",
        })

        pdf_resp = await client_a_admin.get(f"/api/meetings/{mid}/pdf")
        assert pdf_resp.status_code == 200
        assert pdf_resp.headers["content-type"] == "application/pdf"
        assert pdf_resp.content[:4] == b"%PDF"

    @pytest.mark.asyncio
    async def test_register_pdf(self, client_a_admin, m8_seed):
        """Beschluss-Sammlung PDF renders without error and starts with %PDF."""
        prop_id = str(m8_seed["prop_m8"])
        pdf_resp = await client_a_admin.get(f"/api/meetings/register/{prop_id}/pdf")
        assert pdf_resp.status_code == 200
        assert pdf_resp.headers["content-type"] == "application/pdf"
        assert pdf_resp.content[:4] == b"%PDF"

    @pytest.mark.asyncio
    async def test_nonexistent_meeting_pdf_404(self, client_a_admin):
        fake_id = str(uuid.uuid4())
        resp = await client_a_admin.get(f"/api/meetings/{fake_id}/pdf")
        assert resp.status_code == 404


# ===========================================================================
# 8. Register entry grant test (no DELETE for app_rw)
# ===========================================================================
class TestRegisterEntryGrants:
    @pytest.mark.asyncio
    async def test_no_delete_grant_for_app_rw(self, migrator_session):
        """app_rw should NOT have DELETE on resolution_register_entry (defense-in-depth)."""
        result = await migrator_session.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE table_name = 'resolution_register_entry' AND grantee = 'app_rw'"
            )
        )
        privileges = {row.privilege_type for row in result.fetchall()}
        assert "DELETE" not in privileges, (
            "app_rw should NOT have DELETE on resolution_register_entry"
        )
        assert "SELECT" in privileges
        assert "INSERT" in privileges
        assert "UPDATE" in privileges
