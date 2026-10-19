"""Anzeige-Namen für PDF-Renderer -- Auflösung (live) UND Einfrieren (Snapshot).

Hintergrund (ADR-0012, Abschnitt „Render-Zeit-Namensauflösung" + ADR-0003):
Die sechs PDF-Renderer zeigen Klartext-Namen (Objekt, Mieter, Eigentümer,
Mahn-Adressat) statt UUIDs. Diese Namen wurden bisher **ausschließlich zur
Render-Zeit** live aus `property`/`person`/Profil aufgelöst. Für einen
**finalisierten** (aufbewahrungspflichtigen, unveränderbaren) Lauf ist das nicht
selbsttragend: Wird die Person später DSGVO-anonymisiert (ADR-0012 §4), zeigte
ein erneutes Rendern des eingefrorenen Dokuments einen Platzhalter statt des
ursprünglichen Namens -- ein Bruch der GoBD-Vollständigkeit/Unverändertheit.

Lösung: Beim **Finalisieren/Ausstellen** (und nur dort, bevor die
Unveränderbarkeits-Trigger greifen -- OLD.status ist noch 'draft') werden die
für das Dokument nötigen Anzeige-Namen in den eingefrorenen Snapshot geschrieben.
Die Renderer bleiben **unverändert**: sie bekommen weiterhin ein `*DisplayNames`
-Objekt; nur die **Quelle** ändert sich -- eingefrorene Werte, wenn vorhanden,
sonst (Entwurf oder Altbestand) weiterhin Live-Auflösung.

Dieses Modul ist die **einzige Wahrheit** für beide Richtungen, damit
Einfrier- und Render-Pfad nie auseinanderlaufen:

  * ``resolve_*_values``  -> plain dict (nur str/nested str) = das, was
    eingefroren bzw. live aufgelöst wird. Nutzt nur Repositories (keine
    Render-/WeasyPrint-Abhängigkeit) -- deshalb im Finalize-Pfad unbedenklich.
  * ``*_render_names``     -> das `*DisplayNames`-Dataclass für den Renderer:
    eingefroren (finalisiert + vorhanden) ODER live.

Payload-Struktur (versioniert, ADR-0012 §„Einfrieren"): unter dem
Schlüssel ``display_names`` im ``snapshot_payload`` (settlement_run /
economic_plan) bzw. in der Spalte ``dunning_notice.frozen_display_names``:

    {"schema_version": 1, "values": { ... }}

Der Wrapper hält die Werte von den Dataclass-Feldern getrennt und erlaubt
spätere, rückwärtskompatible Formatänderungen (unbekannte Version -> None ->
Live-Fallback statt falscher Rekonstruktion).
"""

from __future__ import annotations

import uuid
from dataclasses import fields
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.temporal import db_exclusive_to_api_inclusive
from app.models.settlement_run import SettlementRun
from app.repositories.lease_agreement_repo import LeaseAgreementRepository
from app.repositories.person_repo import PersonRepository
from app.repositories.property_repo import PropertyRepository
from app.repositories.settlement_repo import SettlementRunRepository
from app.repositories.tenant_profile_repo import TenantProfileRepository
from app.repositories.unit_repo import UnitRepository

# Version des eingefrorenen Anzeige-Namen-Blocks. Bei einer künftigen
# strukturellen Änderung hochzählen; alte Blöcke bleiben lesbar oder fallen
# bewusst auf Live-Auflösung zurück (read_frozen_values -> None).
DISPLAY_NAMES_SCHEMA_VERSION = 1

# Schlüssel des eingefrorenen Blocks im snapshot_payload (settlement_run /
# economic_plan). Für dunning_notice ist es eine eigene Spalte gleichen Inhalts.
DISPLAY_NAMES_KEY = "display_names"


# ===========================================================================
# Versionierter Block-Wrapper (eine Stelle für Einfrieren + Lesen)
# ===========================================================================
def freeze_block(values: dict) -> dict:
    """Verpacke die aufgelösten Werte in den versionierten, einzufrierenden Block."""
    return {"schema_version": DISPLAY_NAMES_SCHEMA_VERSION, "values": values}


def read_frozen_values(block: dict | None) -> dict | None:
    """Lies die Werte aus einem eingefrorenen Block.

    Gibt ``None`` zurück, wenn kein Block vorhanden ist oder seine
    ``schema_version`` unbekannt (in der Zukunft liegend) ist -- dann greift
    bewusst der Live-Fallback statt einer falschen Rekonstruktion.
    """
    if not isinstance(block, dict):
        return None
    if block.get("schema_version") != DISPLAY_NAMES_SCHEMA_VERSION:
        return None
    values = block.get("values")
    return values if isinstance(values, dict) else None


def _payload_frozen_block(run_or_plan) -> dict | None:
    """Hole den display_names-Block aus dem snapshot_payload (falls dict)."""
    payload = getattr(run_or_plan, "snapshot_payload", None)
    if isinstance(payload, dict):
        block = payload.get(DISPLAY_NAMES_KEY)
        if isinstance(block, dict):
            return block
    return None


def _build_dataclass(cls, values: dict):
    """Rekonstruiere ein *DisplayNames-Dataclass robust aus einem Werte-dict.

    Es werden nur bekannte Felder übernommen (Vorwärtskompatibilität: ein
    zusätzliches Feld im eingefrorenen Bestand bricht die Rekonstruktion nicht).
    """
    allowed = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in values.items() if k in allowed})


# ===========================================================================
# Gemeinsame Bausteine
# ===========================================================================
def _person_full_name(person, fallback: str) -> str:
    parts = [p for p in [person.first_name, person.last_name] if p]
    return " ".join(parts) if parts else fallback


async def _property_values(
    db: AsyncSession, org_id: uuid.UUID, property_id: uuid.UUID
) -> tuple[str, str]:
    """(property_name, property_address) live aus Stammdaten; Fallback wie bisher."""
    prop_repo = PropertyRepository(db, org_id)
    prop = await prop_repo.get_by_id(property_id)
    if not prop:
        return "—", ""
    parts = [p for p in [prop.street, f"{prop.zip_code} {prop.city}".strip()] if p]
    return prop.name, ", ".join(parts)


async def _owner_names_map(
    db: AsyncSession, org_id: uuid.UUID, property_id: uuid.UUID
) -> dict[str, str]:
    """{owner_profile_id: Name} für alle Eigentümer des Objekts (WEG/Wirtschaftsplan)."""
    runs_repo = SettlementRunRepository(db, org_id)
    owner_names: dict[str, str] = {}
    for op, person in await runs_repo.owner_profiles_for_property(property_id):
        if person:
            owner_names[str(op.id)] = _person_full_name(person, "—")
    return owner_names


# ===========================================================================
# BK-Abrechnung (settlement_run, run_type=BK_MIETER)
# ===========================================================================
async def resolve_settlement_values(
    db: AsyncSession, org_id: uuid.UUID, run: SettlementRun
) -> dict:
    """Live-Auflösung der BK-Anzeige-Namen (identisch zur bisherigen Render-Logik)."""
    property_name, property_address = await _property_values(db, org_id, run.property_id)
    values = {
        "property_name": property_name,
        "property_address": property_address,
        "unit_label": "—",
        "tenant_name": "—",
        "lease_info": "—",
    }

    if run.unit_id:
        unit = await UnitRepository(db, org_id).get_by_id(run.unit_id)
        if unit:
            label = unit.number
            if unit.location:
                label += f" ({unit.location})"
            values["unit_label"] = label

    if run.tenant_profile_id:
        tp = await TenantProfileRepository(db, org_id).get_by_id(run.tenant_profile_id)
        if tp:
            person = await PersonRepository(db, org_id).get_by_id(tp.person_id)
            if person:
                values["tenant_name"] = _person_full_name(person, "—")

    if run.lease_agreement_id:
        lease = await LeaseAgreementRepository(db, org_id).get_by_id(run.lease_agreement_id)
        if lease:
            info = f"ab {lease.valid_from.strftime('%d.%m.%Y')}"
            valid_to = db_exclusive_to_api_inclusive(lease.valid_to)
            if valid_to:
                info += f" bis {valid_to.strftime('%d.%m.%Y')}"
            else:
                info += " (laufend)"
            values["lease_info"] = info

    return values


async def settlement_render_names(
    db: AsyncSession, org_id: uuid.UUID, run: SettlementRun
):
    """SettlementDisplayNames für den Renderer: eingefroren, sonst live."""
    from app.services.settlement_pdf import SettlementDisplayNames

    frozen = read_frozen_values(_payload_frozen_block(run))
    if run.status == "finalized" and frozen is not None:
        return _build_dataclass(SettlementDisplayNames, frozen)
    return _build_dataclass(
        SettlementDisplayNames, await resolve_settlement_values(db, org_id, run)
    )


# ===========================================================================
# WEG-Jahresabrechnung (settlement_run, run_type=WEG_JAHRESABRECHNUNG)
# ===========================================================================
async def resolve_weg_values(
    db: AsyncSession, org_id: uuid.UUID, run: SettlementRun
) -> dict:
    property_name, property_address = await _property_values(db, org_id, run.property_id)
    return {
        "property_name": property_name,
        "property_address": property_address,
        "owner_names": await _owner_names_map(db, org_id, run.property_id),
    }


async def weg_render_names(db: AsyncSession, org_id: uuid.UUID, run: SettlementRun):
    from app.services.weg_jahresabrechnung_pdf import WegDisplayNames

    frozen = read_frozen_values(_payload_frozen_block(run))
    if run.status == "finalized" and frozen is not None:
        return _build_dataclass(WegDisplayNames, frozen)
    return _build_dataclass(WegDisplayNames, await resolve_weg_values(db, org_id, run))


# ===========================================================================
# Vermögensbericht (settlement_run, run_type=VERMOEGENSBERICHT) -- nur Objekt
# ===========================================================================
async def resolve_vermoegensbericht_values(
    db: AsyncSession, org_id: uuid.UUID, run: SettlementRun
) -> dict:
    property_name, property_address = await _property_values(db, org_id, run.property_id)
    return {"property_name": property_name, "property_address": property_address}


async def vermoegensbericht_render_names(
    db: AsyncSession, org_id: uuid.UUID, run: SettlementRun
):
    from app.services.vermoegensbericht_pdf import VermoegensberichtDisplayNames

    frozen = read_frozen_values(_payload_frozen_block(run))
    if run.status == "finalized" and frozen is not None:
        return _build_dataclass(VermoegensberichtDisplayNames, frozen)
    return _build_dataclass(
        VermoegensberichtDisplayNames,
        await resolve_vermoegensbericht_values(db, org_id, run),
    )


# ===========================================================================
# Wirtschaftsplan (economic_plan)
# ===========================================================================
async def resolve_wirtschaftsplan_values(
    db: AsyncSession, org_id: uuid.UUID, plan
) -> dict:
    property_name, property_address = await _property_values(db, org_id, plan.property_id)
    return {
        "property_name": property_name,
        "property_address": property_address,
        "owner_names": await _owner_names_map(db, org_id, plan.property_id),
    }


async def wirtschaftsplan_render_names(db: AsyncSession, org_id: uuid.UUID, plan):
    from app.services.wirtschaftsplan_pdf import WirtschaftsplanDisplayNames

    frozen = read_frozen_values(_payload_frozen_block(plan))
    if plan.status == "finalized" and frozen is not None:
        return _build_dataclass(WirtschaftsplanDisplayNames, frozen)
    return _build_dataclass(
        WirtschaftsplanDisplayNames,
        await resolve_wirtschaftsplan_values(db, org_id, plan),
    )
