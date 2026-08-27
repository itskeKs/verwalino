"""M9: Snapshot-Namens-Einfrieren (ADR-0012) -- Golden/Invarianten-Stil.

Beweist den in ADR-0012 (Abschnitt „Render-Zeit-Namensauflösung") beschriebenen
Feinschliff: finalisierte/ausgestellte Dokumente frieren ihre Anzeige-Namen im
Snapshot ein, sodass ein späteres DSGVO-Anonymisieren der Person das bereits
eingefrorene, aufbewahrungspflichtige Dokument NICHT mehr verändert. Entwürfe und
Altbestand (finalisiert/ausgestellt OHNE eingefrorene Namen) lösen weiterhin live
auf.

Geforderte Szenarien:
  (1) Namen werden beim Finalisieren/Ausstellen eingefroren.
  (2) Nachträgliche Personen-Anonymisierung ändert das gerenderte finalisierte
      Dokument nicht mehr.
  (3) Altbestand-Fallback rendert weiterhin (Live-Auflösung).
  (4) Entwürfe lösen live auf.

Abgedeckt für die beiden PERSONENNAMEN-tragenden Mechaniken:
  * BK-Abrechnung -> Namen im gehashten settlement_run.snapshot_payload["display_names"].
  * Mahnung       -> Namen in der eigenen, vom Trigger geschützten Spalte
                     dunning_notice.frozen_display_names.
WEG-Jahresabrechnung und Wirtschaftsplan nutzen exakt denselben
snapshot_payload-Mechanismus wie die BK-Abrechnung (identischer Freeze-Code im
Service); die reine Render-Quellen-Entscheidung ist DB-frei mitgetestet.

Der Vermögensbericht und die Versammlungs-PDFs tragen keine Personennamen (nur
Objektdaten) und sind damit vom DSGVO-Anonymisierungs-Konflikt nicht betroffen
(s. ADR-0012).
"""

from __future__ import annotations

import os
import types
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services.render_names import (
    DISPLAY_NAMES_KEY,
    DISPLAY_NAMES_SCHEMA_VERSION,
    freeze_block,
    read_frozen_values,
    settlement_render_names,
    vermoegensbericht_render_names,
    weg_render_names,
)

SYNC_URL = os.environ.get(
    "HV_DATABASE_URL_SYNC",
    "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
)


def _anonymize_person_sql(person_id: str) -> None:
    """Simuliere die DSGVO-Anonymisierung einer Person direkt in der DB.

    Entspricht ADR-0012 §4 (Platzhalter last_name='Anonymisiert', übrige
    Klartextfelder leer). Für den Test genügt die Neutralisierung der Namens-
    und Adressfelder; die *Art* der Neutralisierung ist unerheblich -- geprüft
    wird, dass der Render-Pfad die Live-Person NICHT mehr befragt.
    """
    import psycopg2

    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE person SET first_name='', last_name='Anonymisiert', "
            "street='', zip_code='', city='' WHERE id=%s",
            (person_id,),
        )
    finally:
        conn.close()


# ===========================================================================
# Teil A -- Reine Entscheidungslogik (DB-frei): Version, Fallback, Rekonstruktion
# ===========================================================================
class TestFrozenBlockLogic:
    def test_freeze_read_roundtrip(self):
        values = {"property_name": "Haus", "tenant_name": "Anna A"}
        block = freeze_block(values)
        assert block["schema_version"] == DISPLAY_NAMES_SCHEMA_VERSION
        assert read_frozen_values(block) == values

    def test_read_unknown_version_falls_back_to_none(self):
        """Unbekannte (zukünftige) Version -> None -> bewusst Live-Fallback."""
        block = {"schema_version": DISPLAY_NAMES_SCHEMA_VERSION + 99, "values": {"x": "y"}}
        assert read_frozen_values(block) is None

    def test_read_missing_or_malformed_block(self):
        assert read_frozen_values(None) is None
        assert read_frozen_values({}) is None
        assert read_frozen_values({"schema_version": DISPLAY_NAMES_SCHEMA_VERSION}) is None
        assert read_frozen_values("nonsense") is None  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_settlement_finalized_uses_frozen_without_db(self):
        """Finalisiert + eingefroren -> frozen Namen, OHNE die DB zu befragen.

        db=None beweist, dass der frozen-Zweig keinerlei Live-Auflösung macht
        (würde sonst mit None-Session crashen).
        """
        run = types.SimpleNamespace(
            status="finalized",
            snapshot_payload={
                DISPLAY_NAMES_KEY: freeze_block(
                    {
                        "property_name": "Frozen-Haus",
                        "property_address": "Frozenweg 1",
                        "unit_label": "W2",
                        "tenant_name": "Frida Frozen",
                        "lease_info": "ab 01.01.2025 (laufend)",
                    }
                )
            },
        )
        dn = await settlement_render_names(None, None, run)
        assert dn.tenant_name == "Frida Frozen"
        assert dn.unit_label == "W2"
        assert dn.property_name == "Frozen-Haus"

    @pytest.mark.asyncio
    async def test_weg_finalized_uses_frozen_owner_names_without_db(self):
        oid = str(uuid.uuid4())
        run = types.SimpleNamespace(
            status="finalized",
            snapshot_payload={
                DISPLAY_NAMES_KEY: freeze_block(
                    {
                        "property_name": "WEG-Haus",
                        "property_address": "",
                        "owner_names": {oid: "Olga Owner"},
                    }
                )
            },
        )
        dn = await weg_render_names(None, None, run)
        assert dn.owner_names == {oid: "Olga Owner"}
        assert dn.property_name == "WEG-Haus"

    @pytest.mark.asyncio
    async def test_vermoegensbericht_finalized_uses_frozen_without_db(self):
        run = types.SimpleNamespace(
            status="finalized",
            snapshot_payload={
                DISPLAY_NAMES_KEY: freeze_block(
                    {"property_name": "VB-Haus", "property_address": "VBweg 2"}
                )
            },
        )
        dn = await vermoegensbericht_render_names(None, None, run)
        assert dn.property_name == "VB-Haus"
        assert dn.property_address == "VBweg 2"

    @pytest.mark.asyncio
    async def test_draft_with_stale_block_is_ignored_guard(self):
        """Entwurf (status != finalized) darf einen im Payload liegenden Block
        NICHT als eingefroren behandeln -- er muss live auflösen. Wir prüfen die
        Guard, indem der Live-Zweig ohne echte Session bewusst fehlschlägt
        (er würde die DB befragen), statt den Block zurückzugeben."""
        run = types.SimpleNamespace(
            status="draft",
            snapshot_payload={DISPLAY_NAMES_KEY: freeze_block({"tenant_name": "X"})},
        )
        with pytest.raises(Exception):
            await settlement_render_names(None, None, run)


# ===========================================================================
# Teil B -- BK-Abrechnung (settlement_run.snapshot_payload["display_names"])
# ===========================================================================
@pytest_asyncio.fixture(scope="function")
def sn_bk_seed(seed_data):
    """Vollständig eigenständiges, minimales BK-Objekt für org_a.

    Eine Einheit, ein Eigentümer (ganzjährig), ein Mieter ("Frida Frozen",
    ganzjährig) und EINE bestätigte, umlagefähige Kostenposition mit Schlüssel
    WOHNFLAECHE -> der BK-Lauf finalisiert OHNE externe/Verbrauchs-Extras. Eigene
    Person, damit die Anonymisierung keine geteilten Seed-Personen berührt.
    """
    import psycopg2

    org = str(seed_data["org_a_id"])
    ids = {
        "property": str(uuid.uuid4()),
        "unit": str(uuid.uuid4()),
        "person_owner": str(uuid.uuid4()),
        "owner_profile": str(uuid.uuid4()),
        "person": str(uuid.uuid4()),
        "tenant_profile": str(uuid.uuid4()),
        "lease": str(uuid.uuid4()),
        "cost_entry": str(uuid.uuid4()),
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
            "INSERT INTO property (id, organization_id, name, management_type, street, zip_code, city) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (ids["property"], org, "SN Haus", "MIET", "Schneeweg 1", "10115", "Berlin"),
        )
        cur.execute(
            "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, heated_area, mea) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (ids["unit"], org, ids["property"], "2", "WOHNUNG", 100, 100, 1000),
        )
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
            (ids["person_owner"], org, "Olga", "Owner"),
        )
        cur.execute(
            "INSERT INTO owner_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (ids["owner_profile"], org, ids["person_owner"]),
        )
        cur.execute(
            "INSERT INTO ownership_history (id, organization_id, unit_id, owner_profile_id, valid_from, mea_share) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (str(uuid.uuid4()), org, ids["unit"], ids["owner_profile"], date(2000, 1, 1), None),
        )
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name, street, zip_code, city) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (ids["person"], org, "Frida", "Frozen", "Frostweg 7", "12345", "Kaltstadt"),
        )
        cur.execute(
            "INSERT INTO tenant_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (ids["tenant_profile"], org, ids["person"]),
        )
        cur.execute(
            "INSERT INTO lease_agreement (id, organization_id, unit_id, tenant_profile_id, "
            "valid_from, valid_to, base_rent, advance_payment) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (ids["lease"], org, ids["unit"], ids["tenant_profile"],
             date(2025, 1, 1), None, Decimal("700.00"), Decimal("100.00")),
        )
        cur.execute(
            "INSERT INTO cost_entry (id, organization_id, property_id, cost_category_id, "
            "cost_category_plan_version, invoice_date, service_period_from, service_period_to, "
            "gross_amount, tenant_distribution_key, tenant_apportionable, status, source, "
            "confirmed_at, confirmed_by) VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,'WOHNFLAECHE', "
            "'UMLAGEFAEHIG','confirmed','MANUAL',now(),%s)",
            (ids["cost_entry"], org, ids["property"], str(cost_category_id),
             date(2025, 6, 1), date(2025, 1, 1), date(2025, 12, 31), Decimal("1200.00"),
             str(seed_data["user_a_admin_id"])),
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


async def _create_bk_draft(client, seed, advance="700.00"):
    resp = await client.post("/api/settlements", json={
        "property_id": seed["property"],
        "lease_agreement_id": seed["lease"],
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "advance_payments_total": advance,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _create_and_finalize_bk(client, seed, advance="700.00"):
    run_id = await _create_bk_draft(client, seed, advance)
    # Eine einzige WOHNFLAECHE-Position, eine Einheit -> keine externen/Verbrauchs-
    # Extras nötig; der Mieter trägt 100 % der Fläche.
    fin = await client.post(f"/api/settlements/{run_id}/finalize", json={})
    assert fin.status_code == 200, fin.text
    return run_id


class TestBkSnapshotNames:
    @pytest.mark.asyncio
    async def test_freeze_on_finalize(self, client_a_admin, sn_bk_seed, app_rw_session):
        """(1) Der finalisierte Snapshot enthält den eingefrorenen Mieternamen."""
        run_id = await _create_and_finalize_bk(client_a_admin, sn_bk_seed)

        await _set_org(app_rw_session, sn_bk_seed["org_a_id"])
        row = await app_rw_session.execute(
            text("SELECT snapshot_payload FROM settlement_run WHERE id=:id"),
            {"id": run_id},
        )
        payload = row.scalar_one()
        block = payload[DISPLAY_NAMES_KEY]
        assert block["schema_version"] == DISPLAY_NAMES_SCHEMA_VERSION
        assert block["values"]["tenant_name"] == "Frida Frozen"
        assert block["values"]["unit_label"].startswith("2")

    @pytest.mark.asyncio
    async def test_anonymization_does_not_change_finalized_render(
        self, client_a_admin, sn_bk_seed, app_rw_session
    ):
        """(2)+(4)+immunity in einer geordneten Sequenz.

        finalisierter Lauf -> frozen; Entwurf -> live; nach Anonymisierung bleibt
        der finalisierte Render unverändert, der Entwurf ändert sich.
        """
        from app.repositories.settlement_repo import SettlementRunRepository

        final_id = await _create_and_finalize_bk(client_a_admin, sn_bk_seed)

        # Ein zweiter, NICHT finalisierter Lauf über dieselbe Einheit/Mieter (Entwurf).
        draft_id = await _create_bk_draft(client_a_admin, sn_bk_seed)

        await _set_org(app_rw_session, sn_bk_seed["org_a_id"])
        repo = SettlementRunRepository(app_rw_session, uuid.UUID(str(sn_bk_seed["org_a_id"])))
        org = uuid.UUID(str(sn_bk_seed["org_a_id"]))

        final_run = await repo.get_by_id(uuid.UUID(final_id))
        draft_run = await repo.get_by_id(uuid.UUID(draft_id))

        # Vor Anonymisierung: beide zeigen den echten Namen.
        assert (await settlement_render_names(app_rw_session, org, final_run)).tenant_name == "Frida Frozen"
        assert (await settlement_render_names(app_rw_session, org, draft_run)).tenant_name == "Frida Frozen"

        # DSGVO-Anonymisierung der Person.
        _anonymize_person_sql(sn_bk_seed["person"])
        app_rw_session.expire_all()
        await _set_org(app_rw_session, sn_bk_seed["org_a_id"])

        # (2) finalisiert: unverändert -- der Renderer nutzt die eingefrorenen Namen.
        final_run = await repo.get_by_id(uuid.UUID(final_id))
        assert (await settlement_render_names(app_rw_session, org, final_run)).tenant_name == "Frida Frozen"

        # (4) Entwurf: löst live auf -> jetzt anonymisiert.
        draft_run = await repo.get_by_id(uuid.UUID(draft_id))
        assert (await settlement_render_names(app_rw_session, org, draft_run)).tenant_name == "Anonymisiert"

    @pytest.mark.asyncio
    async def test_altbestand_finalized_without_frozen_names_falls_back_live(
        self, sn_bk_seed, app_rw_session
    ):
        """(3) Ein finalisierter Lauf OHNE display_names (Altbestand, nicht mehr
        beschreibbar) rendert weiter -- Live-Fallback statt Crash."""
        import psycopg2

        from app.repositories.settlement_repo import SettlementRunRepository

        legacy_id = uuid.uuid4()
        # Direkter, sofort committeter INSERT (Trigger feuert nur bei
        # UPDATE/DELETE): finalisierter Lauf, snapshot_payload OHNE display_names.
        conn = psycopg2.connect(SYNC_URL)
        conn.autocommit = True
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO settlement_run (id, organization_id, property_id, run_type, "
                "period_start, period_end, unit_id, lease_agreement_id, tenant_profile_id, "
                "advance_payments_source, status, snapshot_payload, snapshot_sha256, "
                "engine_version, finalized_at) VALUES (%s,%s,%s,'BK_MIETER', "
                "'2024-01-01','2024-12-31',%s,%s,%s,'MANUAL','finalized', "
                "'{}'::jsonb,'x','test',now())",
                (str(legacy_id), sn_bk_seed["org_a_id"], sn_bk_seed["property"],
                 sn_bk_seed["unit"], sn_bk_seed["lease"], sn_bk_seed["tenant_profile"]),
            )
        finally:
            conn.close()

        await _set_org(app_rw_session, sn_bk_seed["org_a_id"])
        org = uuid.UUID(str(sn_bk_seed["org_a_id"]))
        repo = SettlementRunRepository(app_rw_session, org)
        legacy_run = await repo.get_by_id(legacy_id)
        assert legacy_run is not None

        dn = await settlement_render_names(app_rw_session, org, legacy_run)
        # Rendert (kein Crash) und liefert die LIVE aufgelösten Werte.
        assert dn.tenant_name in ("Frida Frozen", "Anonymisiert")
        assert dn.property_name == "SN Haus"


# ===========================================================================
# Teil C -- Mahnung (dunning_notice.frozen_display_names)
# ===========================================================================
@pytest_asyncio.fixture(scope="function")
def sn_dunning_seed(seed_data):
    """Eigene Mahn-Adressat-Person (getrennt von der BK-Person)."""
    import psycopg2

    org = str(seed_data["org_a_id"])
    ids = {"person": str(uuid.uuid4()), "tenant_profile": str(uuid.uuid4())}
    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name, street, zip_code, city) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (ids["person"], org, "Gustav", "Gemahnt", "Mahnweg 9", "54321", "Fristheim"),
        )
        cur.execute(
            "INSERT INTO tenant_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (ids["tenant_profile"], org, ids["person"]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {**seed_data, **ids}


def _insert_draft_dunning(seed, *, frozen: bool) -> str:
    """Lege eine Entwurfs-Mahnung direkt an. frozen=False lässt
    frozen_display_names NULL (für den Altbestand-Fall bei bereits ausgestellten
    Mahnungen)."""
    import psycopg2

    notice_id = str(uuid.uuid4())
    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO dunning_notice (id, organization_id, tenant_profile_id, level, "
            "issue_date, due_date, principal_amount, fee_amount, total_amount, "
            "included_items, status) VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,'draft')",
            (notice_id, str(seed["org_a_id"]), seed["tenant_profile"],
             date(2025, 3, 1), date(2025, 3, 15),
             Decimal("120.00"), Decimal("0.00"), Decimal("120.00"), "[]"),
        )
    finally:
        conn.close()
    return notice_id


class TestDunningSnapshotNames:
    @pytest.mark.asyncio
    async def test_freeze_on_issue_and_immunity(
        self, sn_dunning_seed, app_rw_session, seed_data
    ):
        """(1) Ausstellen friert den Adressatennamen ein; (2) Anonymisierung
        ändert den Render der ausgestellten Mahnung nicht."""
        from app.repositories.dunning_notice_repo import DunningNoticeRepository
        from app.services.dunning_service import DunningService

        notice_id = _insert_draft_dunning(sn_dunning_seed, frozen=False)
        org = uuid.UUID(str(sn_dunning_seed["org_a_id"]))
        user = uuid.UUID(str(seed_data["user_a_admin_id"]))

        await _set_org(app_rw_session, org)
        svc = DunningService(app_rw_session, org, user)

        # Ausstellen -> friert den Namen ein.
        await svc.issue(uuid.UUID(notice_id))

        repo = DunningNoticeRepository(app_rw_session, org)
        notice = await repo.get_by_id(uuid.UUID(notice_id))
        block = notice.frozen_display_names
        assert block["schema_version"] == DISPLAY_NAMES_SCHEMA_VERSION
        assert block["values"]["person_name"] == "Gustav Gemahnt"

        # Vor Anonymisierung rendert der ausgestellte Bescheid den echten Namen.
        assert (await svc.render_display_names(notice)).person_name == "Gustav Gemahnt"

        # DSGVO-Anonymisierung -> ausgestellte Mahnung bleibt unverändert.
        _anonymize_person_sql(sn_dunning_seed["person"])
        app_rw_session.expire_all()
        await _set_org(app_rw_session, org)
        notice = await repo.get_by_id(uuid.UUID(notice_id))
        assert (await svc.render_display_names(notice)).person_name == "Gustav Gemahnt"

    @pytest.mark.asyncio
    async def test_draft_resolves_live(self, sn_dunning_seed, app_rw_session, seed_data):
        """(4) Eine NICHT ausgestellte (Entwurfs-)Mahnung löst live auf."""
        from app.repositories.dunning_notice_repo import DunningNoticeRepository
        from app.services.dunning_service import DunningService

        notice_id = _insert_draft_dunning(sn_dunning_seed, frozen=False)
        org = uuid.UUID(str(sn_dunning_seed["org_a_id"]))
        user = uuid.UUID(str(seed_data["user_a_admin_id"]))

        await _set_org(app_rw_session, org)
        svc = DunningService(app_rw_session, org, user)
        repo = DunningNoticeRepository(app_rw_session, org)
        notice = await repo.get_by_id(uuid.UUID(notice_id))

        # Entwurf: frozen_display_names ist NULL -> Live-Auflösung.
        assert notice.frozen_display_names is None
        assert (await svc.render_display_names(notice)).person_name == "Gustav Gemahnt"

    @pytest.mark.asyncio
    async def test_altbestand_issued_without_frozen_names_falls_back_live(
        self, sn_dunning_seed, app_rw_session, seed_data
    ):
        """(3) Eine bereits ausgestellte Mahnung OHNE eingefrorene Namen
        (Altbestand) rendert weiter über Live-Auflösung."""
        import psycopg2

        from app.repositories.dunning_notice_repo import DunningNoticeRepository
        from app.services.dunning_service import DunningService

        # Direkt als 'issued' einfügen, frozen_display_names bleibt NULL
        # (Trigger feuert nur bei UPDATE/DELETE, nicht bei INSERT).
        notice_id = str(uuid.uuid4())
        conn = psycopg2.connect(SYNC_URL)
        conn.autocommit = True
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO dunning_notice (id, organization_id, tenant_profile_id, level, "
                "issue_date, due_date, principal_amount, fee_amount, total_amount, "
                "included_items, status, issued_at) VALUES "
                "(%s,%s,%s,2,%s,%s,%s,%s,%s,%s,'issued',now())",
                (notice_id, str(sn_dunning_seed["org_a_id"]), sn_dunning_seed["tenant_profile"],
                 date(2025, 4, 1), date(2025, 4, 15),
                 Decimal("200.00"), Decimal("5.00"), Decimal("205.00"), "[]"),
            )
        finally:
            conn.close()

        org = uuid.UUID(str(sn_dunning_seed["org_a_id"]))
        user = uuid.UUID(str(seed_data["user_a_admin_id"]))
        await _set_org(app_rw_session, org)
        svc = DunningService(app_rw_session, org, user)
        repo = DunningNoticeRepository(app_rw_session, org)
        notice = await repo.get_by_id(uuid.UUID(notice_id))

        assert notice.status == "issued"
        assert notice.frozen_display_names is None
        # Rendert weiter (kein Crash), Live-Auflösung.
        assert (await svc.render_display_names(notice)).person_name == "Gustav Gemahnt"
