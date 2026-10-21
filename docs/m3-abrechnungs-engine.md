# M3-Design: Verteilungs- & Abrechnungs-Engine (Betriebskostenabrechnung Mieter)

Stand: 2026-06-01 · Version 0.1 (Design, vor Implementierung)
Bezug: [sdd-property-management-mvp.md](sdd-property-management-mvp.md) v0.2 §6.9, §7, §8, §11, §12, §14, §15, §17, §18 · [roadmap.md](roadmap.md) M3 · [architecture.md](architecture.md) §3 (Unveränderbarkeit), §5.1 (Konventionen), §5.4 (I2-Entitäten) · [m2-gobd-belegfluss.md](m2-gobd-belegfluss.md) (`cost_entry`, Leistungszeitraum, getrennte Verteilerschlüssel, `apportionable_tenant_amount`)
ADRs zu diesem Dokument: [adr/0003-…](adr/0003-gobd-unveraenderbarkeit-mechanik.md) (wiederverwendet) · [adr/0004-zeitanteilige-verteilung-tagegenau.md](adr/0004-zeitanteilige-verteilung-tagegenau.md) (neu) · [adr/0005-rundung-largest-remainder.md](adr/0005-rundung-largest-remainder.md) (neu)
Autor: `engine-correctness` (Opus 4.8) · Status: **Entwurf zur Bestätigung durch den Menschen — kein Anwendungscode, keine Migration, kein Commit**

> Dieses Dokument spezifiziert die **Verteilungs-/Abrechnungs-Engine** (M3) bis auf die Berechnungsmathematik. M3 ist laut Roadmap der größte, riskanteste und korrektheitskritischste Baustein. Scope M3 ist die **Betriebskostenabrechnung für Mieter** (SDD §6.9, §8, §11); die Engine wird aber so entworfen, dass **M4** (WEG-Jahresabrechnung mit Abrechnungsspitze, Wirtschaftsplan, Vermögensbericht) sie **wiederverwendet** — nicht neu erfindet.
>
> **Verbindlichkeit:** SDD (v0.2), Roadmap, `architecture.md`, `m2-gobd-belegfluss.md` und die ADRs 0001–0003 sind die Quelle der Wahrheit und bleiben **unangetastet**. Dieses Dokument ist additiv. Entscheidungen, die der Mensch bestätigen sollte, sind **[ENTSCHEIDUNG]**; offene SDD-§18-Punkte **[OFFEN/§18]** (nicht eigenmächtig final entschieden; provisorische, klar markierte Annahme für Testfälle; fachliche Abnahme empfohlen).
>
> **Keine Selbst-Zertifizierung:** Dieses Design macht die Abrechnung *transparent, nachrechenbar und auditierbar* und legt jede getroffene Annahme offen. Es kann **nicht** garantieren, dass eine konkrete Abrechnung gerichts-/prüfungsfest ist. Die fachliche Richtigkeit der Verteilungs-, Umlage- und Rundungsregeln im Einzelfall ist durch **Steuerberater/Fachanwalt** abzunehmen, bevor produktiv abgerechnet wird.

---

## 0. Geltungsbereich, Exit-Kriterium, Anschluss an M0–M2

**M3-Exit (Roadmap):** „Eine BK-Abrechnung für ein Mischobjekt mit Mieterwechsel ist **nachrechenbar korrekt** und **formell vollständig**." Dieses Kriterium ist die objektive Messlatte. „Nachrechenbar korrekt" wird durch die **synthetischen Golden-Tests** (§9) mit **handberechneten Soll-Ergebnissen** belegt; „formell vollständig" durch die BGH-Pflichtangaben in `settlement_line`/PDF (§8).

**M3 baut auf verifizierten Fundamenten auf (M0/M1/M2, 141 Tests grün) und übernimmt deren Konventionen unverändert.** Die Engine ist **Konsument** der M1/M2-Daten, nicht deren Erweiterung:

| Vorhandenes Fundament | M3 nutzt es so |
| --- | --- |
| `cost_entry` mit `service_period_from/_to`, getrennten `owner_/tenant_distribution_key`, `tenant_apportionable`, `apportionable_tenant_amount`, Aktiv-Kette (`status='confirmed' AND superseded_by_id IS NULL`) | **Einzige** Kostenquelle der Engine. Nur **aktive, confirmte** Einträge werden abgerechnet (m2 §3.4). |
| `unit` (`area`, `heated_area`, `mea`, `usage_type`) | Verteilungs-Basisgrößen je Einheit. |
| `ownership_history` (temporal, EXCLUDE, `mea_share`, Lücken/Co-Eigentum erlaubt) | Eigentümer-Zuordnung je Tag → Leerstand-Adressat (M3), Eigentümersphäre (M4). |
| `lease_agreement` (temporal, EXCLUDE pro Einheit, `base_rent`, `advance_payment`) | Mieterperioden je Einheit; Lücke = Leerstand. `advance_payment` = Default-Vorauszahlung (§7). |
| `cost_category` (global, versioniert, `tenant_apportionable_default`, `reserve_relevant`) | Kategorisierung, Trennung umlagefähig/nicht/Rücklage (M4-relevant, M3 nur „umlagefähig?"). |
| RLS-Muster (`ENABLE`+`FORCE`, vier Policies, `NULLIF(current_setting('app.current_org_id', true),'')::uuid`, fail-closed) | `settlement_run`/`settlement_line` bekommen exakt dieses Muster (Migration analog `0007`). |
| ADR-0003-Trigger (`BEFORE UPDATE/DELETE`, status-bewusst, per-Spalte `IS DISTINCT FROM`) | **Wiederverwendet** für `settlement_run`/`settlement_line`, Einfrier-Bedingung `status='finalized'` (ADR-0003 §3). |
| `TenantRepository`, atomare Service-Transaktion (`cost_entry_service.confirm`), `AuditRepository`, swappable `PdfRenderer` (WeasyPrint) | Vorbilder 1:1 für `SettlementRepository`, `settlement_service.finalize`, Audit, PDF. |
| Geld `NUMERIC(14,2)`/`Decimal`, Quoten `NUMERIC(12,6)`, UUID-PK, `TIMESTAMPTZ`, String+CHECK statt DB-Enum, FK `ondelete=RESTRICT` zu `organization` | Durchgängig übernommen. |
| Migrationen `0001`–`0008` vorhanden | M3 fügt **`0009`–`00xx`** additiv hinzu (Reihenfolge §10). |

**Nicht in M3:** WEG-Jahresabrechnung/Abrechnungsspitze, Wirtschaftsplan, Vermögensbericht (M4 — diese Engine ist dafür vorbereitet, §1.3). Zahlungen/Mietkonto (M6 — M3 erfasst die Vorauszahlungssumme **manuell**, §7). E-Rechnung (M5). Mahnwesen (M7).

**Architektur-Leitsatz (SDD §16, hier hart):** Die **Berechnungs-Engine ist eine Schicht reiner Funktionen** (`Decimal` ein/aus, keine DB, keine HTTP, keine I/O), getrennt von Persistenz (Snapshot), API und PDF-Layout. Das ist die Voraussetzung für Testbarkeit und für die Wiederverwendung in M4 (§10).

---

## 1. Datenmodell: `settlement_run` + `settlement_line`

### 1.1 Rolle und Lebenszyklus

Ein **`settlement_run`** ist *ein* Abrechnungslauf für *einen* Adressatenkreis in *einem* Abrechnungszeitraum (z. B. „BK-Abrechnung Wohnung 3, Mieter Müller, 2025"). Er durchläuft genau zwei Zustände:

```
   draft  ──finalize──▶  finalized   (UNVERÄNDERBAR ab hier, ADR-0003)
     │                        │
     │ (neu rechnen,          │ Korrektur nötig?
     │  Eingaben ändern,      │  → NIE Mutation
     │  verwerfen)            ▼
     └───────────────  neue settlement_run-Version (Storno/Neuberechnung),
                        supersedes_id → alter Lauf  (siehe 1.6)
```

- **`draft`:** frei neu berechenbar; `snapshot_payload` wird bei jedem (Neu-)Rechnen überschrieben; nichts ist eingefroren; keine Außenwirkung.
- **`finalized`:** der berechnete Stand ist als **unveränderbarer Snapshot** eingefroren (SDD §16, architecture.md §3.1/§5.4). `snapshot_payload` + `snapshot_sha256` sind ab hier per ADR-0003-Trigger gegen Mutation gesperrt. Das ist der Stand, der als PDF an den Mieter geht. Spätere Stammdatenänderungen (Mieterwechsel nachgepflegt, MEA korrigiert) verändern eine finalisierte Abrechnung **nicht** (das ist der ganze Zweck des Snapshots).

### 1.2 Warum ein Snapshot (und nicht „live aus den Stammdaten rechnen")

SDD §16: „Alle finalen Abrechnungsergebnisse … werden als **unveränderbare Snapshots** gespeichert, damit spätere Stammdatenänderungen alte Dokumente nicht verändern." Eine versendete BK-Abrechnung ist ein rechtsverbindliches Dokument; sie muss exakt reproduzierbar bleiben, auch wenn Jahre später ein Lease korrigiert, eine Einheit umgewidmet oder der Kontenplan in `plan_version 2` überführt wird. Der Snapshot friert **alle** Eingaben *und* alle berechneten Zwischen-/Endwerte ein. Der `snapshot_sha256` macht nachträgliche Manipulation erkennbar (Tamper-Evidenz, analog `invoice_document.content_sha256`, m2 §4.5).

### 1.3 Typdiskriminator — BK jetzt, WEG/Plan/Vermögensbericht reserviert (M4)

`settlement_run.run_type` (String + CHECK) trägt den Abrechnungstyp. **M3 implementiert nur `BK_MIETER`**; die übrigen Werte sind **im Vokabular reserviert**, damit M4 dieselbe Tabelle/Engine ohne Schemabruch nutzt:

| `run_type` | Bedeutung | Milestone |
| --- | --- | --- |
| `BK_MIETER` | Betriebskostenabrechnung für einen Mieter (SDD §6.9, §11) | **M3** |
| `WEG_JAHRESABRECHNUNG` | Hausgeld-/Jahresabrechnung mit Abrechnungsspitze (WEMoG) | M4 (reserviert) |
| `WIRTSCHAFTSPLAN` | Wirtschaftsplan mit Hausgeldvorschlägen | M4 (reserviert) |
| `VERMOEGENSBERICHT` | Vermögensbericht § 28 IV WEG | M4 (reserviert) |

> **[ENTSCHEIDUNG]** Wirtschaftsplan und Vermögensbericht sind fachlich keine „Abrechnung mit Soll-Ist je Kostenart", sondern Plan- bzw. Bestandsdokumente. Empfehlung: **gleiche `settlement_run`-Hülle** (Snapshot-/Unveränderbarkeits-/Audit-Mechanik wiederverwenden) mit eigenem `run_type` und typabhängiger `snapshot_payload`-Struktur; ob `settlement_line` für sie passt oder eine eigene Detailstruktur im Payload sinnvoller ist, wird in M4 entschieden. M3 verbaut nichts.

### 1.4 Schema `settlement_run` (tenant-behaftet, RLS, ab `finalized` unveränderbar)

| Spalte | Typ | Bedeutung / Begründung |
| --- | --- | --- |
| `id` | `UUID` PK | wie überall (`gen_random_uuid()`). |
| `organization_id` | `UUID NOT NULL` FK→`organization` (`RESTRICT`) | I1, RLS. |
| `property_id` | `UUID NOT NULL` FK→`property` (`RESTRICT`) | Objektbezug. |
| `management_mandate_id` | `UUID NULL` FK→`management_mandate` (`RESTRICT`) | Mandatsbezug (Vermögenstrennung-Anker, architecture.md §2.6). |
| `run_type` | `String(24) NOT NULL` | `BK_MIETER` (M3) u. a. (§1.3). CHECK. |
| `period_start` | `DATE NOT NULL` | Abrechnungszeitraum Beginn (BGH-Pflichtangabe, §8). |
| `period_end` | `DATE NOT NULL` | Abrechnungszeitraum Ende. `CHECK (period_end >= period_start)`. |
| **Adressat (BK: genau ein Mieter/Lease):** | | |
| `unit_id` | `UUID NULL` FK→`unit` (`RESTRICT`) | bei `BK_MIETER`: die abgerechnete Einheit. NULL für objektweite Läufe (M4). |
| `lease_agreement_id` | `UUID NULL` FK→`lease_agreement` (`RESTRICT`) | bei `BK_MIETER`: der abgerechnete Mietvertrag/Adressat. *(Mehrere Leases derselben Einheit im Zeitraum → mehrere `settlement_run`s oder ein Lauf mit mehreren Adressaten — siehe [ENTSCHEIDUNG] 1.5.)* |
| `tenant_profile_id` | `UUID NULL` FK→`tenant_profile` (`RESTRICT`) | denormalisierter Adressat (Audit/Anzeige). |
| **Soll/Ist (§7):** | | |
| `advance_payments_total` | `NUMERIC(14,2) NULL` | Summe der vom Mieter geleisteten NK-Vorauszahlungen im Zeitraum — **in M3 manuell erfassbar** (M6 ersetzt das durch echte `Payment`-Summe). |
| `advance_payments_source` | `String(12) NOT NULL DEFAULT 'MANUAL'` | `MANUAL` (M3) \| `DERIVED` (M6, aus Zahlungen). CHECK. Macht die Herkunft prüffest. |
| `total_cost` | `NUMERIC(14,2) NULL` | Summe der umgelegten Soll-Anteile (redundanter Kopf-Wert; muss == Σ `settlement_line.amount` der Soll-Zeilen). |
| `balance` | `NUMERIC(14,2) NULL` | `total_cost − advance_payments_total`; > 0 = Nachzahlung, < 0 = Guthaben (§7). |
| **Snapshot/Unveränderbarkeit (ADR-0003):** | | |
| `status` | `String(12) NOT NULL DEFAULT 'draft'` | `draft` \| `finalized`. CHECK. Einfrier-Bedingung: `finalized`. |
| `snapshot_payload` | `JSONB NULL` | **kanonische, vollständige** Berechnungsbasis + Ergebnis (§1.7). NULL im frischen `draft` vor erstem Rechnen; gesetzt ab erstem Rechnen; eingefroren ab `finalized`. |
| `snapshot_sha256` | `String(64) NULL` | SHA-256 über die **kanonische** JSON-Serialisierung von `snapshot_payload` (§1.8). Gesetzt bei `finalize`. Tamper-Evidenz. |
| `engine_version` | `String(20) NOT NULL` | Version der Engine-Logik (z. B. `m3.1.0`), die den Snapshot erzeugt hat — Reproduzierbarkeit über Code-Änderungen hinweg. |
| `finalized_at` | `TIMESTAMPTZ NULL` | Zeitpunkt der Finalisierung. |
| `finalized_by` | `UUID NULL` FK→`user` (`SET NULL`) | Wer finalisiert hat (Audit). |
| `superseded_by_id` | `UUID NULL` FK self (`RESTRICT`) | bei Storno/Neuberechnung: zeigt auf den Nachfolge-Lauf (Korrekturkette, §1.6 — **Vorwärts**-Zeiger wie `cost_entry`, E-4-Konvention). |
| `created_at`/`updated_at` | `TIMESTAMPTZ` | wie überall. |

Constraints: `CHECK (period_end >= period_start)`; `CHECK (run_type IN (...))`; `CHECK (status IN ('draft','finalized'))`; `CHECK (advance_payments_source IN ('MANUAL','DERIVED'))`. **RLS** vier Policies (analog `0007`). Indizes: `organization_id`; `property_id`; `(organization_id, run_type, status)`; `(property_id, period_start, period_end)`; `lease_agreement_id`; `superseded_by_id`.

### 1.5 Schema `settlement_line` (gehört zum Snapshot des Laufs)

Eine `settlement_line` ist **eine ausgewiesene Position** der Abrechnung: i. d. R. eine Kostenart (Gesamtkosten + Schlüssel + Mieteranteil), plus Sonderzeilen (Vorauszahlung, Saldo). Sie trägt **alle BGH-Pflichtangaben je Position** (§8) strukturiert — nicht nur den Endbetrag —, damit PDF und maschinelle Prüfung vollständig sind.

| Spalte | Typ | Bedeutung |
| --- | --- | --- |
| `id` | `UUID` PK | |
| `organization_id` | `UUID NOT NULL` FK→`organization` (`RESTRICT`) | I1, RLS. |
| `settlement_run_id` | `UUID NOT NULL` FK→`settlement_run` (`RESTRICT`) | Zugehörigkeit. |
| `line_type` | `String(20) NOT NULL` | `COST` (Kostenart) \| `ADVANCE_PAYMENT` (Vorauszahlungs-Abzug) \| `BALANCE` (Nachzahlung/Guthaben) \| `SUBTOTAL` (optional, z. B. Σ umlagefähig). CHECK. |
| `cost_category_id` | `UUID NULL` FK→`cost_category` (`RESTRICT`) | bei `COST`: die Kostenart. |
| `cost_category_code` | `String(50) NULL` | denormalisiert (prüffest, unabhängig von Plan-Versionen). |
| `label` | `String(255) NOT NULL` | Anzeigename der Zeile. |
| `distribution_key` | `String(20) NULL` | **angewandter** Verteilerschlüssel (BGH-Pflicht: „Umlageschlüssel je Position"). CHECK gegen §8-Vokabular. |
| `total_amount` | `NUMERIC(14,2) NULL` | **Gesamtkosten der Kostenart** im Zeitraum (BGH-Pflicht), vor Verteilung. |
| `distribution_base_total` | `NUMERIC(12,6) NULL` | Nenner des Schlüssels (z. B. Gesamt-Wohnfläche, Gesamt-MEA, Summe Verbrauch) — macht den Rechenweg nachvollziehbar. |
| `distribution_base_share` | `NUMERIC(12,6) NULL` | Zähler des Adressaten (z. B. seine Wohnfläche/sein Verbrauch). |
| `time_share_days` | `INTEGER NULL` | belegte Tage (ADR-0004) — für unterjährige Positionen. |
| `time_share_total_days` | `INTEGER NULL` | Tage des Leistungszeitraums (Nenner der Zeitanteilung). |
| `amount` | `NUMERIC(14,2) NOT NULL` | **der dem Adressaten zugerechnete, gerundete Betrag** (ADR-0005). Bei `BALANCE` das Saldo (Vorzeichen). |
| `sort_order` | `SMALLINT NOT NULL DEFAULT 0` | feste Reihenfolge im PDF. |
| `note` | `String(500) NULL` | Hinweis (z. B. „extern abgerechnet", „nur Mieteranteil CO2", „zeitanteilig 73/365 Tage"). |
| `created_at` | `TIMESTAMPTZ` | |

Constraints: `CHECK (line_type IN (...))`; `CHECK (distribution_key IS NULL OR distribution_key IN (...))`. **RLS** vier Policies. Indizes: `organization_id`; `settlement_run_id`; `(settlement_run_id, sort_order)`.

> **Doppelte Wahrheit, bewusst:** `settlement_line` ist die **relationale, abfragbare** Form des Ergebnisses; `snapshot_payload` (JSONB am Run) ist die **kanonische, gehashte** Form. Beide entstehen in derselben `finalize`-Transaktion aus **demselben** Engine-Ergebnis. Ein Test (§9, Invariante) prüft, dass sie konsistent sind (Σ `settlement_line` == Payload-Summen). Begründung: `settlement_line` trägt das PDF und Reporting komfortabel; der Payload-Hash trägt die Unveränderbarkeits-Garantie. Die `settlement_line`-Zeilen eines `finalized`-Laufs sind über denselben ADR-0003-Trigger gegen Mutation gesperrt (Einfrier-Bedingung am Lauf; siehe 1.6).

### 1.6 Unveränderbarkeit via ADR-0003 (wiederverwendetes Muster, keine neue Mechanik)

ADR-0003 §3 hat `settlement_run`/`settlement_line` bereits für „dieselbe Trigger-Mechanik mit Einfrier-Bedingung `status='finalized'`" **vorgemerkt**. M3 löst das ein, ohne etwas Neues zu erfinden:

- **`settlement_run`** (Einfrier-Bedingung `OLD.status = 'finalized'`): `BEFORE UPDATE/DELETE`-Trigger im Muster von `trg_cost_entry_immutability` (Migration `0008`). Inhaltstragende Spalten (alle außer `superseded_by_id`, `updated_at`) sind nach `finalized` per `IS DISTINCT FROM` gesperrt; **einzige** erlaubte Änderung ist das Setzen von `superseded_by_id` (Storno-/Neuberechnungskette, analog `cost_entry` E-4). DELETE eines `finalized`-Laufs → immer Exception. `draft`-Läufe sind frei editier-/löschbar (Neuberechnung).
- **`settlement_line`** (Einfrier-Bedingung: der **Eltern-Lauf** ist `finalized`): Der Trigger prüft via `SELECT status FROM settlement_run WHERE id = OLD.settlement_run_id`. Ist der Lauf `finalized`, ist **jede** Zeilenänderung/-löschung verboten (eine finalisierte Abrechnung hat keine „erlaubte" Zeilenmutation — Korrektur = neuer Lauf). Solange der Lauf `draft` ist, sind Zeilen frei (Neuberechnung ersetzt sie).
- **Grant-Backstop:** `settlement_line` bekommt **kein DELETE-Grant** für `app_rw` ist *nicht* sinnvoll, weil `draft`-Neuberechnung Zeilen löschen muss. Daher: volles CRUD-Grant + Trigger als alleinige statusabhängige Sperre (wie `cost_entry`, nicht wie `invoice_document`). **[ENTSCHEIDUNG]** bestätigen: `settlement_run`/`settlement_line` = Trigger-only (kein Grant-Entzug), weil beide einen editierbaren `draft`-Zustand haben.

**Korrektur einer finalisierten Abrechnung = neuer Lauf (Storno/Neu):** Eine versendete, falsche BK-Abrechnung wird **nicht** geändert. Es entsteht ein neuer `settlement_run` (neue Berechnung), der alte wird über `superseded_by_id` darauf verwiesen und bleibt unverändert erhalten (GoBD, SDD §14). Ob die Korrektur fachlich als „Storno + Neu" oder „korrigierte Zweitabrechnung" auftritt, ist eine M-spätere/fachliche Frage; das **Modell** trägt beides über die Kette.

### 1.7 Inhalt des `snapshot_payload` (kanonisch, vollständig, selbsterklärend)

Der Payload muss **alles** enthalten, was nötig ist, um die Abrechnung **ohne Rückgriff auf veränderliche Stammdaten** zu reproduzieren und nachzuvollziehen. Struktur (konzeptuell; exakte Keys in der Implementierung fixiert und versioniert über `engine_version`):

```jsonc
{
  "engine_version": "m3.1.0",
  "run_type": "BK_MIETER",
  "period": { "start": "2025-01-01", "end": "2025-12-31", "days": 365 },
  "property": { "id": "...", "name": "...", "management_type": "MISCH" },
  "addressee": { "lease_agreement_id": "...", "unit_id": "...", "unit_number": "3",
                 "tenant_name": "...", "lease_periods_in_scope": [ {"from": "...", "to": "..."} ] },
  "inputs": {
    // EINGEFRORENE Kopien aller verwendeten Stammdaten/Kosten -- nicht nur IDs:
    "units": [ { "id": "...", "number": "...", "area": "75.00", "heated_area": "70.00",
                 "mea": "210.500000", "usage_type": "WOHNUNG" }, ... ],
    "lease_periods": [ { "unit_id": "...", "from": "...", "to": "...", "advance_payment": "150.00" }, ... ],
    "vacancy_periods": [ { "unit_id": "...", "from": "...", "to": "...", "owner_profile_id": "..." }, ... ],
    "cost_entries": [ { "id": "...", "category_code": "GRUNDSTEUER", "gross_amount": "1200.00",
                        "service_period": {"from":"...","to":"..."}, "tenant_distribution_key": "WOHNFLAECHE",
                        "tenant_apportionable": "UMLAGEFAEHIG", "apportionable_tenant_amount": null }, ... ],
    "consumption_readings": [ ... ],     // bei VERBRAUCH-Schlüssel
    "external_amounts": [ ... ],         // bei EXTERNE_ABRECHNUNG (Heizung/Wasser), per Einheit
    "advance_payments_total": "1800.00",
    "advance_payments_source": "MANUAL"
  },
  "computation": [
    // pro Kostenposition der vollständige Rechenweg:
    { "cost_entry_id": "...", "category_code": "GRUNDSTEUER",
      "total_amount": "1200.00", "distribution_key": "WOHNFLAECHE",
      "time_share": { "days": 365, "total_days": 365, "factor": "1" },
      "key_base_total": "300.000000", "key_base_share": "75.000000",
      "exact_share": "300.0000000000", "rounded_amount": "300.00",
      "largest_remainder_cent_applied": false }, ...
  ],
  "result": {
    "lines": [ /* exakt die settlement_line-Inhalte */ ],
    "total_cost": "1450.00",
    "advance_payments_total": "1800.00",
    "balance": "-350.00"    // Guthaben
  }
}
```

Die Eingaben werden **als Kopie** eingefroren (nicht als Referenz), denn der Zweck des Snapshots ist gerade die Unabhängigkeit von späteren Stammdatenänderungen (§1.2). Co-Eigentum/`mea_share`, Gewerbe-Markierung etc. werden mitgeführt, soweit sie in die Rechnung eingehen.

### 1.8 Kanonische Serialisierung & Hash (Determinismus ist Pflicht)

Damit `snapshot_sha256` reproduzierbar und damit als Tamper-Evidenz brauchbar ist, muss die JSON-Serialisierung **kanonisch** sein:

- Schlüssel **sortiert**, fester Separator, kein Whitespace-Drift; UTF-8.
- Geld/Quoten als **String** im Payload (z. B. `"300.00"`, `"210.500000"`), **nie** als JSON-`number` (Float-Drift vermeiden) — feste Skala je Feldtyp.
- Datums-/Zeitwerte als ISO-Strings (Daten als `YYYY-MM-DD`).
- Reihenfolge von Listen deterministisch (stabil sortiert nach IDs).

Der Hash wird über genau diese kanonische Bytes-Repräsentation gebildet (eine Hilfsfunktion `canonical_json(payload) -> bytes`, wiederverwendbar für M4 und für den optionalen `audit_event`-Hash-Chain, m2 §6). **Determinismus-Test** (§9): zweimaliges Finalisieren derselben `draft`-Eingaben erzeugt denselben Hash (ADR-0005 garantiert die deterministische Rundung, die das ermöglicht).

---

## 2. Verteilungs-Algorithmus — Mathematik je Verteilerschlüssel

Alle acht Schlüssel sind in `cost_entry.tenant_distribution_key` (M2, SDD §8) als kontrolliertes Vokabular hinterlegt: `MEA · WOHNFLAECHE · BEHEIZTE_FLAECHE · EINHEIT · PERSONENZAHL · VERBRAUCH · EXTERNE_ABRECHNUNG · MANUELLER_BETRAG`. M2 speichert nur die *Auswahl*; **M3 definiert die Semantik** (wie verteilt wird). Eigentümer- und Mieterabrechnung können je Kostenposition **unterschiedliche** Schlüssel tragen (SDD §8); M3 verwendet für die BK-Abrechnung den **`tenant_distribution_key`** und die **`tenant_apportionable`**-Sphäre (§6); M4 verwendet später `owner_distribution_key`.

### 2.1 Gemeinsames Grundprinzip (Gewichtsvektor → zentrale Verteilungsfunktion)

Jeder Schlüssel reduziert sich auf **einen Gewichtsvektor** `wᵢ ≥ 0` über die am Schlüssel teilnehmenden Einheiten/Adressaten. Der zu verteilende Gesamtbetrag `G` wird dann **immer** über die **eine** zentrale Funktion `distribute_amount(G, [w₁..wₙ])` (ADR-0005, Largest-Remainder) verteilt. Damit ist die Verteilungsmathematik **eine** Funktion plus „wie berechne ich den Gewichtsvektor" je Schlüssel:

| Schlüssel | Gewicht `wᵢ` je Einheit i | Nenner `Σw` | Quelle |
| --- | --- | --- | --- |
| `MEA` | `unit.mea` (bzw. `ownership_history.mea_share` bei Co-Eigentum) | Σ MEA aller teilnehmenden Einheiten | M1 `unit`/`ownership_history` |
| `WOHNFLAECHE` | `unit.area` | Σ Flächen | M1 `unit.area` |
| `BEHEIZTE_FLAECHE` | `unit.heated_area` | Σ beheizte Flächen | M1 `unit.heated_area` |
| `EINHEIT` | `1` je teilnehmender Einheit (Pro-Kopf-der-Einheiten) | Anzahl Einheiten | abgeleitet |
| `PERSONENZAHL` | Personenzahl der Einheit im Zeitraum | Σ Personen | **[OFFEN/§18]** Datenquelle (s. 2.3) |
| `VERBRAUCH` | abgelesener Verbrauch der Einheit (`meter_reading`-Differenz) | Σ Verbrauch | M1 `meter`/`meter_reading` |
| `EXTERNE_ABRECHNUNG` | — (kein Gewicht; **direkter** per-Einheit-Betrag) | — | externe Werte (§5) |
| `MANUELLER_BETRAG` | — (kein Gewicht; **direkter** per-Einheit-Betrag) | — | manuelle Eingabe |

### 2.2 Flächen-/MEA-/Einheit-Schlüssel (gewichtete Verteilung)

`MEA`, `WOHNFLAECHE`, `BEHEIZTE_FLAECHE`, `EINHEIT` sind reine **gewichtete** Verteilungen:

```
Anteil_Einheit_i (exakt) = G * wᵢ / Σw          (Decimal, ungerundet)
→ distribute_amount(G, [wᵢ]) liefert die gerundeten, summengenauen Cent-Beträge (ADR-0005).
```

- **Nenner = Summe der teilnehmenden Einheiten**, nicht „alle Einheiten des Objekts blind". Wer „teilnimmt", folgt aus der Kostenposition und der Abrechnungssphäre (z. B. Stellplätze ohne Wohnfläche nehmen an einem Wohnflächen-Schlüssel mit Gewicht 0 nicht teil; eine Gewerbeeinheit kann je nach Vorwegabzug **[OFFEN/§18]**, §6.3, ausgenommen sein).
- **Fehlende Basisgröße** (`area`/`mea` ist NULL) bei einer teilnehmenden Einheit → **harter Fehler/Eskalation**, **keine** stille 0 (das würde Kosten unbemerkt umverteilen). Die Engine validiert vor dem Rechnen (§2.6).
- **MEA bei Co-Eigentum:** Nimmt eine Einheit mit mehreren Bruchteilseigentümern teil (`ownership_history.mea_share`), wird für die **Eigentümer**sphäre (M4) nach `mea_share` weiter aufgeteilt. Für die **Mieter-BK** (M3) ist der Adressat der Mieter der Einheit; die MEA der Einheit ist deren Gewicht (Bruchteilseigentum ist hier irrelevant, weil der Mieter die ganze Einheit nutzt).

### 2.3 `PERSONENZAHL` — Schlüssel definiert, Datenquelle offen

Die Verteilungsmathematik ist identisch zu 2.2 (Gewicht = Personenzahl der Einheit). **[OFFEN/§18]** ist die **Datenquelle**: M1 modelliert keine zeitabhängige Personenzahl je Einheit/Lease. Optionen: (a) Feld am `lease_agreement` (Personenzahl, ggf. mit eigener kleiner Historie bei unterjähriger Änderung), (b) eigene kleine temporale Tabelle. **Provisorische Annahme für M3-Testfälle:** Personenzahl ist ein per-Lease-Wert (konstant über die Lease-Periode); unterjährige Änderung der Personenzahl wird in M3 **nicht** abgebildet. Klar als Annahme markiert; fachliche/Produkt-Klärung vor produktivem Personenschlüssel. Die Engine nimmt die Personenzahl als **Eingabe** entgegen (reine Funktion), sodass die Datenquellen-Entscheidung die Engine nicht berührt.

### 2.4 `VERBRAUCH` (verbrauchsabhängig)

Gewicht = gemessener Verbrauch der Einheit im Abrechnungszeitraum, ermittelt aus `meter`/`meter_reading` (M1: Datum, Stand, Periode) als **Differenz** (Endstand − Anfangsstand) der relevanten Periode. Mathematik wie 2.2 (Gewicht = Verbrauchsmenge). Besonderheiten:

- **Zählertausch / fehlende Ablesung** im Zeitraum: **[OFFEN/§18]** — Schätzung/Ersatzverfahren ist fachlich (HeizkostenV-/Messdienst-Logik) und in M3 **nicht** automatisch. Provisorisch: Die Engine verlangt für `VERBRAUCH` vollständige, plausible Anfangs-/Endablesungen je teilnehmender Einheit; fehlt etwas → Eskalation, keine stille Schätzung.
- **Heizung/Warmwasser** laufen in Phase 1 **nicht** über den `VERBRAUCH`-Schlüssel, sondern über `EXTERNE_ABRECHNUNG` (extern HeizkostenV-konform erstellt, SDD §6.9/§7.1, §5 dieses Dokuments). `VERBRAUCH` ist für eigene Zähler (z. B. Kaltwasser) gedacht, die das System selbst führt (SDD §6.2.1).

#### Periodische Zählervorbelegung seit Phase 2 (T31)

Ohne expliziten Handwert wird je Einheit der eindeutig nächste Anfangs- und
Endstand innerhalb jeweils ±31 Tagen verwendet. Die Differenz bleibt exakt auf
vier Dezimalstellen; es gibt weder Interpolation noch Rundung auf ganze Einheiten.
Gleichabstand, doppelte nächste Ablesungen, identische/verkehrte Ablesetage oder
Rückgänge verlangen Handeingabe. Automatik erfordert genau einen passenden
Einheitszähler (`meter_type`, getrimmt/großgeschrieben, gleich Kostenkategoriecode)
und eine einzige relevante Verbrauchsart. Mehrere mögliche Austauschzähler,
Gemeinschaftszähler und verschiedene Verbrauchskategorien werden nicht still
kombiniert. Eine fehlende Einheit darf nicht aus dem Verteilungsnenner verschwinden.

Der Entwurfsabruf `GET /api/settlements/{id}/consumption-suggestions` verwendet
READ-Kreisrechte mit vollständiger Quellen-/Klassifikationsprüfung. Die Oberfläche
zeigt die tatsächlichen Stand-IDs, Daten und Werte; automatische Vorbelegungen
werden nicht als manuelle Overrides zurückgesendet. Explizite Handwerte, auch 0,
haben Vorrang. Compute und Finalize bestimmen die Basis unter der bestehenden
Finanzsperre frisch. Nur tatsächlich automatisch verwendete Werte ergänzen
`inputs.meter_consumption_sources` im Snapshot; reine Handwertsnapshots und alte
finalisierte Abrechnungen bleiben unverändert. Finale Ansichten fragen keine
aktuellen Vorschläge ab. Die vorhandenen Kosteneditoren konvertieren ihre
UI-Auswahl über `costVocabulary` in kanonische DB-Schlüssel; Ergebniszeilen zeigen
deren menschliche Bezeichnungen.

### 2.5 `EXTERNE_ABRECHNUNG` und `MANUELLER_BETRAG` (direkte per-Einheit-Beträge)

Diese beiden Schlüssel **verteilen nicht** — sie übernehmen einen **bereits feststehenden Betrag je Einheit/Adressat**:

- `EXTERNE_ABRECHNUNG`: der Betrag stammt aus einer extern erstellten Abrechnung (Messdienst Heizung/Wasser), per Einheit (§5). Die Engine ordnet den Mieteranteil dem Adressaten direkt zu (keine Schlüssel-Verteilung). Bei Mieterwechsel innerhalb des Zeitraums: Der externe Messdienst liefert i. d. R. bereits **pro Nutzzeitraum** getrennte Werte (Zwischenablesung bei Auszug). **[OFFEN/§18]** wie diese pro-Mieter-Aufteilung exakt hereinkommt (§5); provisorisch: ein per-(Einheit, Nutzzeitraum)-Betrag.
- `MANUELLER_BETRAG`: ein vom Sachbearbeiter bewusst je Einheit gesetzter Betrag (Sonderfall). Die Engine übernimmt ihn direkt.
- **Invariante auch hier:** Bei einer per-Einheit-Verteilung muss die **Summe der per-Einheit-Beträge == Gesamtbetrag der Kostenposition** sein (Plausibilitätscheck, sonst Eskalation) — sonst wäre die ausgewiesene Gesamtsumme inkonsistent. Bei `EXTERNE_ABRECHNUNG` ist „der Gesamtbetrag" der Mieter-relevante Teil der externen Abrechnung (§5).

### 2.6 Vorab-Validierung (vor jeder Berechnung — fail-loud)

Die Engine prüft **vor** dem Rechnen und **eskaliert** statt still Falsches zu produzieren:

1. Jeder zu verteilende `cost_entry` ist **aktiv & confirmed** (m2 §3.4) und liegt im Objekt; sein Leistungszeitraum **überlappt** den Abrechnungszeitraum (sonst gehört er nicht in diese Abrechnung).
2. Für jeden gewichteten Schlüssel: alle teilnehmenden Einheiten haben die **nötige Basisgröße** (kein NULL bei `area`/`mea`/`heated_area`/Verbrauch); `Σw > 0`.
3. Für per-Einheit-Schlüssel: Summe der Einheit-Beträge == Positions-Gesamtbetrag (±0 Cent).
4. Mieterperioden + Leerstandsperioden decken den Abrechnungszeitraum je Einheit **lückenlos & überschneidungsfrei** ab (folgt aus M1-EXCLUDE; Engine prüft dennoch defensiv, §3.4).
5. `tenant_apportionable`-Sphäre konsistent (kein `*_PRUEFEN` ohne bestimmten Mieteranteil, §6).

Verstöße → strukturierter Fehler mit Positions-/Einheit-Bezug (kein Teil-Snapshot). Die Engine ist hier bewusst **streng**: lieber eine klare Eskalation als eine plausibel aussehende, falsche Abrechnung.

---

## 3. Anteilige Zeiträume (Kern-Korrektheit) — Mieterwechsel & Leerstand

Dies ist die schwierigste und prüfungskritischste Stelle (Roadmap: „BK-Abrechnung für ein Mischobjekt mit **Mieterwechsel**"). Sie verbindet drei Zeitachsen: den **Abrechnungszeitraum** (am `settlement_run`), den **Leistungszeitraum** je Kostenposition (`cost_entry`, M2) und die **Mieter-/Eigentümerperioden** je Einheit (`lease_agreement`/`ownership_history`, M1).

### 3.1 Maßstab: tagegenau (ADR-0004)

Verbindlich tagegenau, tag-inklusiv, exakt als `Decimal` (ADR-0004). Zeitanteil einer Periode an einer Kostenposition:

```
factor = tage(Leistungszeitraum ∩ Periode) / tage(Leistungszeitraum)
       = (min(LZ.to, P.to) − max(LZ.from, P.from) + 1) / (LZ.to − LZ.from + 1)
```

(0, wenn keine Überschneidung). Summe der Faktoren aller lückenlos-überschneidungsfreien Perioden über den Leistungszeitraum = **exakt 1** (ADR-0004 Invariante).

### 3.2 Zwei-Stufen-Verteilung: erst Zeit, dann Schlüssel — exakt verkettet

Für eine Kostenposition, deren Leistungszeitraum sich mit mehreren Mieter-/Leerstandsperioden einer Einheit überschneidet, ist der dem **Adressaten** (= einem bestimmten Lease) zugerechnete **exakte** Betrag:

```
exakt_Adressat = G * (wᵢ / Σw)          // Schlüssel-Anteil der Einheit i
                   * factor_Periode       // Zeitanteil der Adressaten-Periode an der Position
```

Beide Faktoren werden **exakt** (ungerundet) verkettet; die **Largest-Remainder-Rundung greift einmal am Schluss** über alle finalen Adressaten-Beträge der Position (ADR-0005, Reihenfolge-Disziplin). Niemals zwischendurch runden.

**Wichtig — Reihenfolge der Verteilungs-Universen:** Eine Kostenposition wird i. d. R. über **das ganze Objekt** verteilt (alle Einheiten nach Schlüssel), nicht nur über die abgerechnete Einheit. Für eine **einzelne** Mieter-BK (`BK_MIETER`, ein Lease) interessiert nur der Teil, der **dieser** Einheit und **dieser** Mieterperiode zufällt. Die Engine berechnet daher konzeptuell:

1. **Schlüssel-Anteil der Einheit** an der Gesamtposition (Universum = alle teilnehmenden Einheiten des Objekts).
2. **Zeit-Aufteilung dieses Einheit-Anteils** auf die Mieter-/Leerstandsperioden **innerhalb der Einheit** im Abrechnungszeitraum.
3. Für die BK-Abrechnung wird **die** Periode des abgerechneten Leases herausgegriffen; Leerstandsperioden fallen dem **Eigentümer** zu (§3.3) und erscheinen nicht in der Mieter-BK.

So bleibt die Verteilung über das ganze Objekt konsistent (Summe über alle Einheiten/Mieter/Leerstand = G), auch wenn nur eine Einheit abgerechnet wird.

### 3.3 Leerstand → Eigentümer (SDD §6.9)

Eine **Lücke** zwischen zwei Leases einer Einheit (oder vor dem ersten / nach dem letzten Lease, innerhalb des Abrechnungszeitraums) ist **Leerstand**. Der auf Leerstandstage entfallende, zeitanteilige Kostenanteil der Einheit trägt der **Eigentümer** der Einheit in diesem Teilintervall (`ownership_history`):

- In der **Mieter-BK** (M3) erscheint der Leerstandsanteil **nicht** (der Mieter zahlt nur seine eigenen Tage).
- Der Leerstandsanteil ist trotzdem **nicht verloren**: Er ist Teil der Gesamtverteilung (Summe über Mieter + Leerstand + andere Einheiten = G) und landet beim Eigentümer. In M3 wird er als **Eigentümer-Restposten** ausgewiesen/vorgehalten (im Payload als `vacancy_periods`-Zuordnung); die *Belastung* des Eigentümers ist Gegenstand der WEG-/Eigentümerabrechnung (M4) bzw. der Mietobjekt-Eigentümersicht. M3 stellt sicher, dass **kein Cent durch Leerstand verschwindet** (Invariante, §9).
- **Property-based-Invariante (§9):** „Leerstandsanteil landet beim Eigentümer" = für jede Position gilt `Σ(Mieteranteile aller Einheiten) + Σ(Leerstandsanteile) + Σ(Anteile nicht abgerechneter Sphären) == G`.

### 3.4 Halboffene DB-Intervalle vs. tag-inklusive Engine-Rechnung (Edge-Case)

Die M1-EXCLUDE-Constraints nutzen `daterange [from, to)` (oberer Rand exklusiv); `valid_to`/`service_period_to` sind aber fachlich **inklusive** Tage. Die Engine rechnet tag-inklusiv (ADR-0004). An der **Lese-Grenze** (Repository → Engine-Eingabe) wird konsistent interpretiert: `valid_to = 2025-06-30` heißt „belegt **bis einschließlich** 30.06.". Der tag-genaue Wechsel (Auszug 30.06., Einzug Nachmieter 01.07.) ergibt:

- Mieter A: Tage … bis 30.06. (inklusive).
- Mieter B: Tage ab 01.07.
- **keine** Lücke (kein Leerstand am 30.06. oder 01.07.), **kein** Überlapp.

Dies ist ein **expliziter Golden-/Edge-Test** (§9), weil ein Off-by-one hier genau einen Tag Kosten falsch zuordnet. Ebenso getestet: Wechsel am Monatsersten, Wechsel über Jahresgrenze (Leistungszeitraum im Vorjahr, Rechnung im Folgejahr — die Position gehört über `service_period`, nicht `invoice_date`, ins richtige Jahr; SDD §14 / m2 §2.2).

### 3.5 Mehrere Leases derselben Einheit im Abrechnungszeitraum

Wechselt der Mieter unterjährig, gibt es **zwei** Adressaten (zwei Leases) für **eine** Einheit. **[ENTSCHEIDUNG]** Modellierung: Empfehlung — **ein `settlement_run` je Lease/Adressat** (jeder Mieter bekommt seine eigene BK-Abrechnung über seine Tage). Der M3-Exit („BK für ein Mischobjekt mit Mieterwechsel") wird dadurch erfüllt, dass für **dieselbe Einheit** zwei `settlement_run`s entstehen (Alt- und Nachmieter), die zusammen die Einheit-Anteile (ohne Leerstand) tagegenau aufteilen. Alternative (ein Lauf, mehrere Adressatenzeilen) wäre möglich, ist aber für die Mieter-Zustellung (jeder Mieter erhält *sein* Dokument) unpraktisch. Bei der Implementierung festklopfen.

---

## 4. Rundung — kaufmännisch + Largest-Remainder (ADR-0005)

### 4.1 Verbindliche Mechanik

Vollständig in [ADR-0005](adr/0005-rundung-largest-remainder.md). Kurzfassung der Garantie, die hier korrektheitskritisch ist:

- Eine **einzige** reine Funktion `distribute_amount(total: Decimal, weights: list[Decimal]) -> list[Decimal]` rundet Geld auf Cent. Jede Verteilung (jeder Schlüssel, jede Zeitaufteilung) läuft durch sie.
- Algorithmus: exakte Anteile → **abrunden** auf Cent → Rest `k` ganze Cents per **größtem Bruchteil-Rest** vergeben (deterministischer Tie-Break) → **`Σ cᵢ == total` exakt**.
- **Reihenfolge:** exakt rechnen über alle Stufen (Zeit × Schlüssel), **einmal am Schluss** runden — nie zwischendurch.

### 4.2 Warum das die Kern-Korrektheitsregel ist

„Σ der Anteile == Gesamtkosten der Kostenart" ist eine **formelle BGH-Anforderung** und eine rechnerische Selbstverständlichkeit, die naive Cent-Rundung **verletzt** (333,33 × 3 ≠ 1000,00). Largest-Remainder macht die Gleichheit zu einer **Eigenschaft der Funktion**, nicht zu einem Zufall. Das ist als Property-based Test über zufällige Beträge/Gewichte beweisbar (§9) und ist die objektivste einzelne Korrektheitsaussage von M3.

### 4.3 Aggregation über mehrere Kostenarten

Die **Gesamt-Nachzahlung** des Mieters ist `Σ(gerundete Mieteranteile aller Kostenarten) − Vorauszahlungen`. Da jede Kostenart-Verteilung **für sich** summengenau ist (Σ Mieteranteile je Position konsistent zur ausgewiesenen Positions-Gesamtsumme), addieren sich die bereits gerundeten Mieteranteile **ohne erneute Rundung** zur Gesamtsumme (Cent + Cent = Cent). Es gibt **keine** zweite Rundungsstufe auf der Gesamtsumme — das verhindert einen zweiten Drift.

### 4.4 Randfälle der Funktion (getestet)

- **`Σw = 0`** (kein gültiges Gewicht): definierter Fehler/Eskalation (§2.6 Validierung), **keine** stille 0-Verteilung.
- **Negativer Gesamtbetrag** (Gutschrift/Korrekturposition): Largest-Remainder funktioniert vorzeichen-konsistent (abrunden Richtung −∞ vs. kaufmännisch — präzise Definition in ADR-0005/Implementierung festklopfen; getestet).
- **Ein Adressat** (n=1): trivial `[total]`.
- **Rest k=0** (alles geht glatt auf): keine Extra-Cents, alle floor-Werte.

---

## 5. CO2-Aufteilung (10-Stufen) & externe Heiz-/Wasserabrechnung

### 5.1 Grundsatz: Werte kommen herein, die Engine verteilt/übernimmt nur

Phase 1 erstellt Heiz-/Warmwasser- und CO2-Werte **nicht** selbst (SDD §6.9, §7.1/§7.2): Der Messdienst liefert HeizkostenV-konforme per-Einheit-Werte; die CO2-Aufteilung (CO2KostAufG, 10-Stufen-Modell nach kg CO₂/m²·a) wird entweder **aus der externen Heizkostenabrechnung übernommen** oder aus eingegebenen Kennwerten berechnet. **Nur der Mieteranteil ist umlagefähig** (SDD §6.9). M2 hält dafür bereit: `tenant_apportionable = TEILWEISE_PRUEFEN` (CO2) bzw. `UMLAGEFAEHIG_EXTERN_PRUEFEN` (Heizung/Wasser), `tenant_distribution_key = EXTERNE_ABRECHNUNG`, und das Feld **`apportionable_tenant_amount`** (der explizit als Mieteranteil bestimmte Betrag).

**Ergänzung T45 (reine Engine, BK-Anschluss erst T46):** `heizkosten.py` verteilt
vorgegebene, nach §9 getrennte Heiz-/Warmwasserpools mit 50–70 % Verbrauchsanteil
und 30–50 % Grundanteil. Für Heizung wird Fläche/beheizte Fläche/Rauminhalt
explizit gewählt, Warmwasser nutzt Wohn-/Nutzfläche (§§7/8 HeizkostenV). Der
bestätigte §7-Sondergebäudefall erzwingt 70 %. Kosten und Einheiten werden mit
der vorhandenen Largest-Remainder-Funktion in stabiler UUID-Reihenfolge verteilt.
Bei ausdrücklich nicht verbrauchsabhängiger Abrechnung (`consumption_recorded=False`)
weist der TENANT-Kontext 15 % des jeweiligen betroffenen Nutzeranteils als
Kürzung nach §12(1) aus (HALF_UP je Nutzer); Kürzung + Zahlbetrag = Bruttoanteil.
Der Kürzungsbetrag wird nicht umgelegt. WEG_OWNER erhält diese Kürzung nicht.
Einzelne fehlende Ablesungen, gemischte unvollständige Messdaten oder Nullnenner
führen weiterhin zur Eskalation, nicht zu stiller Schätzung oder Nullverbrauch.
Die Engine beurteilt keine §11-Ausnahmen, erfindet keine §9-Kostentrennung und
berechnet keine zusätzlichen 3-%-Kürzungen. Bestehende Fremdabrechnungen und
CO2-Abgrenzungen bleiben unverändert; T45 schaltet keinen UI-/PDF-Weg um.

### 5.2 Externe Heiz-/Wasserabrechnung (`EXTERNE_ABRECHNUNG`)

Die Engine **verteilt nicht**, sondern übernimmt den **per-Einheit-Mieteranteil** direkt als Position der betreffenden Einheit (§2.5). Bei Mieterwechsel: pro Nutzzeitraum getrennte externe Werte (Zwischenablesung). In der BK-Zeile wird `note = "extern abgerechnet (Messdienst)"` und `distribution_key = EXTERNE_ABRECHNUNG` ausgewiesen (BGH: Hinweis auf externe Werte, SDD §11).

> **[OFFEN/§18] Eingabeweg externer Werte.** SDD §18 nennt explizit offen: „Wie externe Heiz-/Wasserabrechnungen genau eingegeben werden: Betrag pro Einheit, PDF-Anhang, Gesamtsumme und Plausibilitätscheck; Übernahme des CO2-Aufteilungswerts." M3 **entscheidet das nicht eigenmächtig**. Die Engine nimmt externe Werte als **strukturierte Eingabe** entgegen (reine Funktion); *wie* sie erfasst/gespeichert werden (eigene kleine Tabelle `external_heating_statement` je `cost_entry`/Einheit/Nutzzeitraum vs. JSONB am `cost_entry`), ist eine [ENTSCHEIDUNG]/§18-Frage.
> **Provisorische Annahme für M3-Testfälle (klar markiert):** Externe Werte liegen als per-(Einheit, Nutzzeitraum)-Mieteranteil vor (`external_amounts` im Payload, §1.7); die Engine summiert sie je Einheit/Adressat und prüft Plausibilität gegen den Positions-Gesamtbetrag. Diese Annahme ist **nicht** rechtlich zertifiziert; der konkrete Eingabeweg und die HeizkostenV-Konformität sind fachlich (Messdienst/StB) abzunehmen.

### 5.3 CO2-Aufteilung (10-Stufen, nur Mieteranteil)

Das CO2KostAufG-Stufenmodell bestimmt, **welcher Prozentsatz** der CO2-Kosten der **Vermieter** trägt (steigend mit der CO₂-Intensität des Gebäudes, kg CO₂/m²·a) — der Rest ist Mieteranteil. **M3 berechnet das Stufenmodell nicht selbst** (das ist fachlich heikel und Teil der externen Heizkostenlogik). Stattdessen:

- Der **Mieteranteil** der CO2-Kosten kommt als **`apportionable_tenant_amount`** in die Kostenposition (aus externer Abrechnung übernommen oder vom Sachbearbeiter aus den Kennwerten bestimmt, M2). Die Engine legt **nur diesen** Mieteranteil um (der Vermieteranteil bleibt beim Eigentümer, erscheint nicht in der Mieter-BK).
- Bei `tenant_apportionable = TEILWEISE_PRUEFEN` ist `apportionable_tenant_amount` der **maßgebliche** umzulegende Betrag (statt `gross_amount`). Die Engine **verlangt** ihn (Validierung §2.6/§6); fehlt er → Eskalation, kein stilles Umlegen des Gesamtbetrags.

> **[OFFEN/§18] CO2-Stufenberechnung.** Ob das System die 10-Stufen-Einstufung (kg CO₂/m²·a → Vermieter-%) **selbst** rechnen soll oder den Mieteranteil **immer** aus der externen Abrechnung übernimmt, ist offen (SDD §6.9 nennt beide Wege). M3 **nimmt provisorisch** den **Übernahme-Weg** an (Mieteranteil als Eingabe `apportionable_tenant_amount`), weil das die Engine sauber von der CO2-Fachlogik trennt und der externen, HeizkostenV-/CO2KostAufG-konformen Quelle vertraut. Eine eigene Stufenberechnung wäre ein abgegrenzter, separat zu testender und **fachlich abzunehmender** Baustein (Empfehlung: eigener Schritt, nicht in den M3-Verteilungskern mischen). Klar als Annahme markiert.

---

## 6. Gemischte/prüfpflichtige Kosten — effektive Werte aus der Kostenposition

### 6.1 Grundsatz: die Engine legt nichts automatisch um

SDD §7.2/§8/§9: Bei `GEMISCHT_PRUEFEN` (Hausmeister), `TEILWEISE_PRUEFEN` (CO2), `MANUELL_PRUEFEN` (Sonstige BK, Kabel/Breitband seit 2024), `UMLAGEFAEHIG_EXTERN_PRUEFEN` (Heizung/Wasser/Messdienst) darf **nicht** automatisch der ganze Betrag umgelegt werden. Die **effektiven** Werte (umzulegender Betrag, Schlüssel, Umlagefähigkeit) sind bereits in der **bestätigten Kostenposition** festgelegt (M2: `tenant_apportionable`, `tenant_distribution_key`, `apportionable_tenant_amount`) — durch eine **bewusste Nutzer-Entscheidung** beim `confirm` (m2 §2.3, „kein stilles Default-Durchwinken"). **M3 rechnet nur mit diesen effektiven Werten** und entscheidet nichts neu.

### 6.2 Konkrete Behandlung je Sphäre in M3

| `tenant_apportionable` | umzulegender Betrag (Mieter) | Engine-Verhalten |
| --- | --- | --- |
| `UMLAGEFAEHIG` | `gross_amount` | normaler Schlüssel (§2). |
| `UMLAGEFAEHIG_EXTERN_PRUEFEN` | per-Einheit-Mieteranteil (extern) | `EXTERNE_ABRECHNUNG` (§5.2). |
| `GEMISCHT_PRUEFEN` (Hausmeister) | `apportionable_tenant_amount` (der herausgerechnete umlagefähige Teil) | legt **nur** diesen Teil um; fehlt er → Eskalation. |
| `TEILWEISE_PRUEFEN` (CO2) | `apportionable_tenant_amount` (Mieteranteil) | §5.3. |
| `MANUELL_PRUEFEN` (Sonstige/Kabel) | `gross_amount` **nur wenn** der Nutzer die Position bewusst als umlagefähig bestätigt hat (sonst gehört sie nicht in die Mieter-BK) | legt um, wenn bestätigt; sonst nicht enthalten. |
| `NICHT_UMLAGEFAEHIG` | — | erscheint **nicht** in der Mieter-BK (gehört in die Eigentümer-/WEG-Abrechnung, M4). |

**Hausmeister-Anteil (`GEMISCHT_PRUEFEN`):** Der Verwaltungs-/Reparatur-/Instandhaltungsanteil ist **vorab** (in der Kostenposition) herausgerechnet; M3 legt den verbleibenden, in `apportionable_tenant_amount` hinterlegten umlagefähigen Teil nach dem gewählten Schlüssel um. Die Engine prüft, dass bei `GEMISCHT_PRUEFEN` ein `apportionable_tenant_amount` gesetzt ist (sonst Eskalation).

### 6.3 Gewerbe-Vorwegabzug — [OFFEN/§18]

SDD §18: „Ob und wie Gewerbeeinheiten einen **Vorwegabzug** benötigen (andere Umlagelogik, USt-Option)." Bei Mischnutzung (Wohnen + Gewerbe) kann ein **Vorwegabzug** der auf Gewerbe entfallenden Kosten **vor** der Verteilung auf die Wohnungen rechtlich geboten sein (wenn Gewerbe zu erheblichen Mehrkosten führt). **M3 entscheidet das nicht eigenmächtig.**

- **Provisorische Annahme für M3-Testfälle (klar markiert):** **Kein** automatischer Vorwegabzug in M3. `unit.usage_type = GEWERBE` ist im Modell vorhanden und wird in den Snapshot mitgeführt; eine Gewerbeeinheit nimmt nach dem **gewählten Schlüssel** an der Verteilung teil wie jede andere Einheit (z. B. nach Fläche). Ein gewünschter Vorwegabzug wird in M3 **manuell** abgebildet (eigene Kostenposition für den Gewerbe-Anteil mit `MANUELLER_BETRAG`/Ausschluss der Wohnungen), nicht automatisch berechnet.
- Die **automatische** Vorwegabzugs-Logik (und USt-Option bei Gewerbe) ist ein abgegrenzter, fachlich abzunehmender Baustein (Empfehlung: spätere Spezifikationsrunde / mit StB). Hier festgehalten, **nicht weggeraten**.

---

## 7. Soll/Ist: Vorauszahlungen → Nachzahlung/Guthaben

### 7.1 Rechnung

```
Soll (total_cost)        = Σ gerundete Mieteranteile aller umlagefähigen Kostenarten
Ist (advance_payments)   = Summe der NK-Vorauszahlungen des Mieters im Zeitraum
balance                  = total_cost − advance_payments_total
   balance > 0  → Nachzahlung (Mieter zahlt)
   balance < 0  → Guthaben (Mieter erhält zurück)
```

`balance`, `total_cost`, `advance_payments_total` stehen am `settlement_run` (Kopf) **und** als `settlement_line` (`ADVANCE_PAYMENT`, `BALANCE`) für PDF/Prüfung.

### 7.2 Herkunft der Vorauszahlungssumme — [ENTSCHEIDUNG], M3 manuell

Zahlungen (M6) existieren noch nicht (Roadmap: M6 hängt an M3). Daher wird die **Vorauszahlungssumme in M3 manuell erfasst** (Roadmap M3 explizit: „Vorauszahlungssumme zunächst manuell"). Festlegung:

- **Feld am Abrechnungslauf je Mieter:** `settlement_run.advance_payments_total` (`NUMERIC(14,2)`), gesetzt durch den Sachbearbeiter beim Anlegen/Berechnen des `draft`. `advance_payments_source = 'MANUAL'`.
- **Vorbelegungs-Hilfe (kein Automatismus):** Die UI **darf** einen Vorschlag aus `lease_agreement.advance_payment` × anteilige Monate/Tage des Zeitraums anbieten; der **maßgebliche** Wert bleibt aber der manuell bestätigte Betrag (was tatsächlich gezahlt wurde, kann abweichen — Rückstände). Die Engine rechnet mit dem bestätigten `advance_payments_total`, nicht mit der Soll-Vorauszahlung aus dem Vertrag.
- **M6-Übergang vorbereitet:** Ab M6 wird `advance_payments_source = 'DERIVED'` und der Wert aus erfassten `Payment`s gezogen (Roadmap M6: „echte Nachzahlung/Guthaben statt manueller Vorauszahlungssumme"). Das Feld/`source`-Diskriminator macht den Wechsel sauber, ohne Schemabruch.

> **[ENTSCHEIDUNG]** Speicherort der manuellen Vorauszahlungssumme: **Feld am `settlement_run`** (empfohlen, hier festgelegt) vs. eigene kleine Tabelle. Empfehlung: Feld am Lauf — es gehört semantisch in den eingefrorenen Snapshot (was bei der Abrechnung als gezahlt zugrunde gelegt wurde, ist Teil des unveränderbaren Dokuments). Bestätigen.

### 7.3 Künftige Vorauszahlung vorschlagen (Phase 2, T28)

Die historische M3-Planung in §7.2 ist vom heutigen Ist-Pfad zu unterscheiden:
M6 kann gebuchte NK-Zahlungen ableiten; T28 verändert weder dieses Ist noch einen
finalisierten Lauf. `GET /api/settlements/{id}/advance-proposal` liefert für einen
aktiven finalisierten BK-Lauf den eingefrorenen Mieter-Kostenanteil geteilt durch
seine eingefrorene Mietdauer innerhalb des Abrechnungszeitraums. Ganze Kalendermonate
zählen als eins, Teilmonate als belegte Tage/Kalendertage des Monats. Kalendergewichte
bleiben exakt rational; Geld bleibt Decimal und wird einmal kaufmännisch auf Cent
gerundet. Beispiel: Januar bis 05.02.2024, 0,17 EUR / (1 + 5/29) = 0,15 EUR.
Fehlende, negative oder inkohärente historische Basis ergibt 409, keine Nullannahme.

Die Detailseite zeigt Betrag und Basis; Leser sehen keine Übernahmesteuerung.
POST an dieselbe URL nimmt nur `valid_from` und ergänzt nach Live-Rollen-, Kreis-
und History-Prepare-Prüfung über den gemeinsamen T23-Service eine unveränderliche
volle Rate. Die Kaltmiete wird aus der bis zum Stichtag gültigen Miethistorie erhalten.
Vertragsgrenzen und doppelte Stichtage bleiben geschützt; gemeinsame Finanz-/Zeilensperre,
Audit und Rollback gelten. Die neue Rate ist vor HTTP 201 committet. Originalvertrag,
Abrechnungssnapshot/-zeilen und gebuchte Zahlungen werden nicht verändert.
Der echte UI-Prüffall finalisiert beleggebundene Mieterkosten von 1.200 EUR, übernimmt
100 EUR ab 01.01.2027 und prüft alle 24 Mietkontoposten: Jahres-Soll 9.600 statt
10.200 EUR bei unverändertem Ist null.

---

## 8. Formelle Pflichtangaben (BGH) + PDF-Layout

### 8.1 BGH-Pflichtangaben → wo sie getragen werden

SDD §11 listet die formellen Mindestanforderungen (BGH-Rechtsprechung für Mieterabrechnungen). Jede ist strukturiert in `settlement_run`/`settlement_line` abgelegt (nicht nur im PDF), damit sie maschinell prüfbar **und** unveränderbar im Snapshot sind:

| BGH-Pflichtangabe (SDD §11) | Träger im Modell |
| --- | --- |
| Objekt und **Abrechnungszeitraum** | `settlement_run.property_id`, `period_start`/`period_end` |
| Adressat und Einheitenbezug | `settlement_run.unit_id`/`lease_agreement_id`/`tenant_profile_id` + Payload-Adressat |
| **Gesamtkosten je Kostenart** | `settlement_line(line_type='COST').total_amount` |
| **angewandter Verteilerschlüssel je Position** | `settlement_line.distribution_key` (+ `distribution_base_total`/`_share` für den Rechenweg) |
| **Berechnung des Anteils** des Adressaten | `settlement_line.distribution_base_share` / `time_share_days` / `amount` (+ Payload `computation[]` voller Rechenweg) |
| **Abzug der Vorauszahlungen** | `settlement_line(line_type='ADVANCE_PAYMENT')` + `advance_payments_total` |
| **Nachzahlung oder Guthaben** | `settlement_line(line_type='BALANCE')` + `settlement_run.balance` |
| Hinweis auf externe Abrechnungswerte | `settlement_line.note` (z. B. „extern abgerechnet") |

Damit ist „formell vollständig" (M3-Exit) eine **prüfbare** Eigenschaft: Ein Test (§9) assertet, dass für einen `BK_MIETER`-Lauf alle diese Felder vorhanden/konsistent sind.

> **[OFFEN/§18] Exakte PDF-Struktur.** SDD §18: „Exakte PDF-Struktur für Betriebskostenabrechnung …" ist offen. M3 stellt **alle Daten** für ein vollständiges PDF bereit; das **konkrete Layout** (Spaltenanordnung, Texte, Fußnoten, Einwendungsfrist-Hinweis nach § 556 III BGB) ist eine [OFFEN/§18]-/Design-Frage. Provisorisch wird ein funktionales, BGH-vollständiges Standardlayout angenommen; das finale Layout ist mit dem Produkt/Fachseite abzustimmen.

### 8.2 PDF-Pipeline (WeasyPrint, M0-Infrastruktur)

Die PDF-Erzeugung nutzt die in M0 angelegte, swappable Pipeline (`app/core/pdf.py`, `PdfRenderer`-Protocol, Default **WeasyPrint**). M3:

- erzeugt **HTML** aus dem **finalisierten** `settlement_run`/`settlement_line` (nie aus `draft` — das versendete Dokument muss dem eingefrorenen Snapshot entsprechen) via Template (z. B. `app/templates/bk_abrechnung.html`), dann `render_pdf(html)`.
- Die PDF-Schicht ist **strikt getrennt** von der Engine (SDD §16): Sie liest nur das fertige Ergebnis, rechnet **nichts** neu. Test: PDF-Werte == `settlement_line`-Werte (keine Drift zwischen Berechnung und Darstellung).
- WeasyPrint ist deterministisch (gleicher Input → gleiches PDF), passend zur Snapshot-Philosophie.

---

## 9. Synthetische Golden-Tests (objektive M3-Messlatte)

Es gibt **keine** realen Beispielabrechnungen. Die Prüffälle sind **synthetisch** aus den Rechtsregeln + **handberechneten** Soll-Ergebnissen konstruiert. Zwei Test-Arten, beide verbindlich für die M3-Abnahme:

### 9.1 Golden-Tests (handberechnete Soll-Ergebnisse, exakte Cent-Werte)

Jeder Golden-Test ist ein vollständiges, **von Hand durchgerechnetes** Szenario mit im Test hinterlegtem Soll-Ergebnis (jeder Cent). Pflicht-Szenarien:

1. **Trivial-Verteilung Fläche:** 1 Objekt, 3 Wohnungen (75/50/25 m²), 1 Kostenart (Grundsteuer 1200,00 €/Jahr), ganzjährig, kein Wechsel. Soll: 600/400/200 €. Prüft Schlüssel + Σ == Gesamt.
2. **Rundungsfall (Largest-Remainder):** 1000,00 € ÷ 3 gleiche Einheiten → 333,34 / 333,33 / 333,33 (Σ = 1000,00). Prüft, dass **kein** Cent verloren geht und der Tie-Break deterministisch ist.
3. **Unterjähriger Mieterwechsel (Kern):** 1 Wohnung, Mieter A 01.01.–30.06., Mieter B 01.07.–31.12.; Kostenart ganzjährig. Soll: tagegenaue Aufteilung (181/365 vs. 184/365 in 2025, kein Schaltjahr), zwei `settlement_run`s. Prüft ADR-0004 + tag-inklusiven Wechsel (§3.4).
4. **Leerstand:** 1 Wohnung, Mieter A 01.01.–31.03., dann Leerstand 01.04.–31.05., Mieter B ab 01.06.; Kostenart ganzjährig. Soll: A trägt 90 Tage, B trägt 214 Tage, **61 Tage Leerstand beim Eigentümer** (nicht in Mieter-BK). Prüft §3.3 + Invariante „Leerstand → Eigentümer".
5. **Mischobjekt mit Mieterwechsel + Leerstand + mehrere Kostenarten/Schlüssel + CO2 (Exit-Szenario):** Mischobjekt, mehrere Einheiten (eine vermietet mit unterjährigem Wechsel, eine eigengenutzt/Eigentümer, eine zeitweise leer, eine Gewerbeeinheit), Kostenarten mit **verschiedenen Schlüsseln** (Grundsteuer nach Fläche, Müll nach Einheit, Kaltwasser nach Verbrauch, Heizung extern, **CO2 mit Mieteranteil aus `apportionable_tenant_amount`**, Hausmeister `GEMISCHT_PRUEFEN` mit herausgerechnetem Anteil, WEG-Verwalterkosten `NICHT_UMLAGEFAEHIG` → darf **nicht** in Mieter-BK auftauchen). **Vollständig handberechnet.** Dies ist das **direkte Abbild des M3-Exit-Kriteriums**.
6. **Leistungszeitraum über Jahresgrenze:** Rechnung mit `invoice_date` im Januar 2026, `service_period` Dezember 2025 → gehört in die 2025-Abrechnung (über `service_period`, nicht `invoice_date`). Prüft SDD §14.
7. **Externe Abrechnung mit Zwischenablesung bei Mieterwechsel:** Heizkosten extern, getrennte per-Mieter-Werte. Prüft §5.2 (direkte Übernahme, keine Schlüssel-Verteilung).
8. **Snapshot/Hash-Determinismus:** Szenario 5 zweimal finalisiert (auf identischen Eingaben) → **identischer** `snapshot_sha256`. Prüft §1.8 + ADR-0005-Determinismus.

Jeder Golden-Test prüft **nicht nur die Endsumme**, sondern die **einzelnen `settlement_line`-Beträge, Schlüssel-Basiswerte und Zeitanteile** gegen die Handrechnung — sonst könnte ein kompensierender Doppelfehler durchrutschen.

### 9.2 Property-based Invarianten (Hypothesis, über zufällige Eingaben)

Diese fangen Fehlerklassen, die Einzelbeispiele übersehen (architecture.md §6: pytest + Hypothesis ist genau dafür gewählt). Verbindliche Invarianten:

- **I-A „Summe der Anteile = Gesamtkosten":** Für zufällige `total ∈ [−10000, 10000]` €, zufällige Gewichtsvektoren (n ∈ [1, 50], `wᵢ ≥ 0`, `Σw > 0`): `Σ distribute_amount(total, w) == total` **exakt** (in Cent). Die fundamentalste Invariante (ADR-0005).
- **I-B „kein Cent geht verloren / Nichtnegativität der Sub-Cent-Verteilung":** Jeder Anteil weicht um **höchstens 1 Cent** vom abgerundeten exakten Anteil ab; vergebene Extra-Cents == `k`.
- **I-C „Zeitanteile summieren sich zu 1":** Für zufällige, lückenlos-überschneidungsfreie Periodenzerlegungen eines Leistungszeitraums: `Σ factor == 1` exakt (ADR-0004).
- **I-D „Leerstandsanteil landet beim Eigentümer / Vollständigkeit":** Für zufällige Misch-Szenarien gilt je Position `Σ(Mieteranteile) + Σ(Leerstandsanteile) + Σ(sonstige Sphären) == G` (nichts verschwindet).
- **I-E „Determinismus":** Zweimal dieselbe Eingabe → bit-identischer kanonischer Payload und identischer Hash.
- **I-F „Nicht-Umlagefähiges erscheint nie in der Mieter-BK":** `NICHT_UMLAGEFAEHIG`-Positionen tauchen in keinem `BK_MIETER`-`settlement_line` auf.
- **I-G „Konsistenz Payload ↔ settlement_line":** Σ/Werte in `snapshot_payload.result` == Σ/Werte der `settlement_line`-Zeilen.

Diese Invarianten sind die **objektive, modellunabhängige** Messlatte: Sie gelten für *jede* gültige Eingabe, nicht nur für die ausgedachten Beispiele.

---

## 10. Implementierungs-Checkliste für `engine-correctness` (M3 baut wegen Korrektheit `engine-correctness`, nicht `bulk-impl`)

> M3 ist laut Roadmap-Modellzuordnung **`engine-correctness` (Opus 4.8)** — die Verteilungs-Engine ist korrektheitskritisch. Die Reihenfolge ist **Engine zuerst (reine Funktionen + Tests), dann Persistenz/Snapshot, dann API, zuletzt PDF** (Roadmap: „hier zuerst die Engine, dann das PDF"). **Voraussetzung:** der Mensch hat die [ENTSCHEIDUNG]en (§Schluss) und die ADRs 0004/0005 bestätigt. Vorher kein Commit.

### 10.1 Engine als reine Funktionen (zuerst, höchste Testdichte)

- [ ] `app/engine/` (neues Paket, **bewusst getrennt** von `services`/`api`/`models` — SDD §16): keine DB-, HTTP-, ORM-Importe; ausschließlich `Decimal`, `date`, Dataclasses ein/aus.
- [ ] `app/engine/rounding.py`: `distribute_amount(total: Decimal, weights: list[Decimal]) -> list[Decimal]` (ADR-0005, Largest-Remainder, deterministischer Tie-Break). **Zuerst** mit Property-based Tests (I-A/I-B) absichern, bevor irgendetwas darauf baut.
- [ ] `app/engine/periods.py`: tagegenaue Zeitarithmetik (ADR-0004) — `day_count(from, to)`, `overlap_days(p1, p2)`, `time_factor(service_period, period)`, `split_into_tenant_and_vacancy_periods(abrechnungszeitraum, leases) -> list[Period]` (mit Eigentümer-Adressat bei Leerstand). Tests I-C + tag-inklusive Edge-Cases (§3.4).
- [ ] `app/engine/distribution.py`: je Schlüssel die Gewichtsvektor-Berechnung (§2) + die Zwei-Stufen-Verkettung (Zeit × Schlüssel, §3.2). Eingaben als eingefrorene Dataclasses (Spiegel des Payload-`inputs`).
- [ ] `app/engine/settlement.py`: orchestriert eine `BK_MIETER`-Berechnung → `EngineResult` (Zeilen + Summen + Rechenweg) als reine Datenstruktur. **Validierung fail-loud** (§2.6).
- [ ] `app/engine/canonical.py`: `canonical_json(payload) -> bytes` + `sha256_hex(bytes)` (§1.8) — deterministisch, sortiert, Geld als String.
- [ ] Golden-Tests (§9.1) als `tests/engine/test_golden_*.py` mit handberechneten Soll-Werten; Property-based (§9.2) als `tests/engine/test_invariants.py` (Hypothesis).

### 10.2 Persistenz & Snapshot (danach)

- [ ] Modelle `app/models/settlement_run.py`, `settlement_line.py` (Stil `lease_agreement.py`/`cost_entry.py`: Mixins, `Numeric`, `Mapped`, String+CHECK). Geld `Decimal`.
- [ ] **Migration `0009_settlement_tables`:** `settlement_run` + `settlement_line` mit CHECK-Constraints, FKs (`RESTRICT` zu `organization`), Indizes (§1.4/§1.5), `superseded_by_id` (Vorwärts, E-4-Konvention).
- [ ] **Migration `0010_settlement_rls_policies`:** vier Policies je Tabelle exakt im Muster `0007` (`NULLIF(current_setting('app.current_org_id', true),'')::uuid`, fail-closed). Beide Tabellen mit DELETE-Policy (beide haben editierbaren `draft`-Zustand → volles CRUD-Grant; [ENTSCHEIDUNG] 1.6).
- [ ] **Migration `0011_settlement_immutability_triggers`** (ADR-0003 §3, Muster von `0008`): `BEFORE UPDATE/DELETE`-Trigger für `settlement_run` (Einfrier-Bedingung `status='finalized'`, einzig erlaubte Änderung `superseded_by_id`) und `settlement_line` (Einfrier-Bedingung: Eltern-Lauf `finalized` → jede Mutation verboten). Trigger-Quelltext ausführlich kommentiert; `downgrade()` droppt Trigger + Funktion.
- [ ] `TENANT_TABLES` in `tests/test_rls_coverage.py` um `settlement_run`, `settlement_line` erweitern.
- [ ] Repository `app/repositories/settlement_repo.py` (Stil `cost_entry_repo.py`): Aktiv-Query (`superseded_by_id IS NULL`), Lese-Aggregation der `cost_entry`-Aktivkette + `unit`/`lease`/`ownership` als Engine-Eingabe.

### 10.3 Service (Snapshot/Finalize atomar)

- [ ] `app/services/settlement_service.py` (Stil `cost_entry_service.py`): `create_draft`, `compute` (rechnet Engine, schreibt `snapshot_payload` + `settlement_line` im `draft`, **überschreibbar**), `set_advance_payments` (§7), `finalize` (**atomar**, analog `confirm`), `create_correction_run` (Storno/Neu, §1.6).
- [ ] `finalize` als **eine** Transaktion: Engine final rechnen → `snapshot_payload` setzen → `snapshot_sha256` berechnen → `settlement_line` (re-)schreiben → `status='finalized'`, `finalized_at/by`, `total_cost`/`balance` → `audit_event` (`settlement.finalized`) → Commit. Ab Commit greift der ADR-0003-Trigger. Rollback bei jedem Fehler (kein halb-finalisierter Lauf).
- [ ] `audit_event`-Actions: `settlement.created`, `settlement.computed`, `settlement.finalized`, `settlement.superseded` (über vorhandenes `AuditRepository`).

### 10.4 API (API-first, RBAC wie M0/M2)

Seit Phase 2 (T27) ergänzt `POST /api/settlements/batch` die Einzelanlage um einen
atomaren Sammellauf mit Objekt, inklusivem Zeitraum und optionalem Abrechnungskreis.
Nur im Zeitraum überlappende Verträge der berechtigten Kreiseinheiten werden über
`create_draft` angelegt; aktive BK-Läufe mit identischem Vertrag/Objekt/Zeitraum/Kreis
werden unverändert übersprungen. Antwort: `created` und `skipped` mit Vertrags-/Lauf-ID.
Die vorhandene orgweite Finanzsperre serialisiert parallele Sammelläufe; ein Fehler
rollt auch Audit und bereits angelegte Entwürfe zurück. Source-/Kreisrechte werden
vor einer Leerantwort geprüft. Keine Berechnung, Coverage oder Finalisierung und
keine erfundenen Vorauszahlungen: neue Finanzwerte bleiben `null`.
Unter `/abrechnungen` führt „BK-Sammellauf“ durch Objekt, Zeitraum und Kreis;
Ergebniszahlen und Liste werden aktualisiert, dieselbe Periode ist wiederholbar.

Implementiertes BK-Verhalten: `POST /api/settlements/{id}/finalize` beendet die DB-Transaktion durch `Depends(get_db, scope="function")` **vor** der HTTP-Erfolgsantwort. Damit sieht ein unmittelbar folgender PDF-Request bereits den finalisierten Snapshot; Entwürfe bleiben mit HTTP 409 gesperrt. Die Regression prüft den gespeicherten Status aus einer unabhängigen DB-Session bei `http.response.start`, da ASGITransport allein das spätere Request-Cleanup abwartet und diese Race Condition verdeckt.

- [ ] `app/api/settlements.py`: `POST` (Lauf anlegen, `require_write_dep`), `POST /{id}/compute`, `PATCH /{id}/advance-payments`, `POST /{id}/finalize`, `POST /{id}/correction`, `GET` (Liste/Detail, alle Rollen lesen), `GET /{id}/pdf` (finalisiert → PDF-Stream). READ_ONLY: nur GET. Fremde ID → **404** (nicht 403), wie M0/M2. Router in `app/main.py` registrieren.

### 10.5 PDF (zuletzt, strikt getrennt)

- [ ] `app/templates/bk_abrechnung.html` (alle BGH-Pflichtangaben §8.1) + `app/services/settlement_pdf.py`, das **nur** aus dem finalisierten `settlement_run`/`settlement_line` rendert (`render_pdf`, WeasyPrint). **Keine** Neuberechnung. Test: PDF-Werte == `settlement_line`-Werte.

### 10.6 Definition of Done für M3 (Übergabe an M4)

- [ ] Engine-Paket rein (keine DB/HTTP-Importe), `Decimal` durchgängig, eine zentrale `distribute_amount`.
- [ ] **Golden-Tests §9.1 grün** (handberechnete Soll-Werte, inkl. Exit-Szenario 5) — die objektive „nachrechenbar korrekt"-Messlatte.
- [ ] **Property-based Invarianten §9.2 grün** (I-A bis I-G), insb. „Σ Anteile == Gesamtkosten", „Σ Zeitanteile == 1", „Leerstand → Eigentümer", Determinismus.
- [ ] `settlement_run`/`settlement_line` mit RLS (vier Policies, fail-closed) + ADR-0003-Trigger (`finalized` unveränderbar) + Snapshot-Hash.
- [ ] `finalize` atomar; `audit_event` für alle Vorgänge; Korrektur = neuer Lauf (nie Mutation).
- [ ] BGH-Pflichtangaben vollständig in `settlement_line`/PDF (formell vollständig); PDF rechnet nichts neu.
- [ ] RLS-Coverage-Test + `test_tenant_isolation` um die M3-Tabellen erweitert; **alle 141 bestehenden Tests bleiben grün.**
- [ ] M3-Exit objektiv erfüllt: BK-Abrechnung für ein Mischobjekt mit Mieterwechsel + Leerstand ist **nachrechenbar korrekt** (Golden-Test 5) und **formell vollständig** (Pflichtangaben-Test).
- [ ] Keine **[ENTSCHEIDUNG]**/**[OFFEN/§18]** unmarkiert „weggeraten"; provisorische Annahmen klar gekennzeichnet; fachliche Abnahme (StB/Fachanwalt) empfohlen.

---

## 11. Getroffene Annahmen (M3)

| # | Annahme | Auswirkung wenn falsch | Klären mit |
| --- | --- | --- | --- |
| **M3-A1** | Zeitanteil **tagegenau** (ADR-0004), nicht monatsgenau. | Monatsgenau würde andere Cent-Zuordnung bei Wechsel ergeben; Engine-Methode + Tests müssten angepasst. | Mensch/StB (ADR-0004) |
| **M3-A2** | Rundung **kaufmännisch + Largest-Remainder** (ADR-0005); Σ == Gesamt exakt. | Abweichende vorgeschriebene Rundung → zentrale Funktion anpassen. | Mensch/StB (ADR-0005) |
| **M3-A3** | Ein `settlement_run` **je Mieter/Lease** (Mieterwechsel = zwei Läufe je Einheit). | Ein-Lauf-mehrere-Adressaten würde Modell/PDF ändern. | Mensch (§3.5) |
| **M3-A4** | Mieteranteil bei CO2/Heizung kommt als **Eingabe** (`apportionable_tenant_amount`/extern); Engine berechnet das Stufenmodell **nicht** selbst. | Eigene 10-Stufen-Berechnung wäre ein abgegrenzter, fachlich abzunehmender Zusatzbaustein. | StB/Messdienst (§5) |
| **M3-A5** | **Kein** automatischer Gewerbe-Vorwegabzug in M3; Gewerbe nimmt nach Schlüssel teil; Vorwegabzug ggf. manuell. | Automatischer Vorwegabzug ist eigener Baustein. | StB/Fachanwalt (§6.3) |
| **M3-A6** | Vorauszahlungssumme **manuell** am Lauf (`advance_payments_source='MANUAL'`); M6 ersetzt durch `DERIVED`. | M6-Anschluss über `source`-Diskriminator vorbereitet. | (Roadmap M6) |
| **M3-A7** | Personenzahl (`PERSONENZAHL`-Schlüssel) als per-Lease-Wert, konstant über Lease-Periode (M1 modelliert das nicht zeitabhängig). | Zeitabhängige Personenzahl bräuchte Modellergänzung. | Produkt (§2.3) |
| **M3-A8** | Externe Werte/Verbrauch müssen **vollständig & plausibel** vorliegen; fehlend → Eskalation, **keine** stille Schätzung. | Ersatz-/Schätzverfahren (HeizkostenV) ist eigener, fachlicher Baustein. | StB/Messdienst (§2.4/§5) |

---

## 12. Zusammenfassung der offenen Entscheidungen (für den Menschen)

| # | Punkt | Typ | Empfehlung | Wann |
| --- | --- | --- | --- | --- |
| **E3-1** | Zeitmaßstab tagegenau vs. monatsgenau | [ENTSCHEIDUNG] | **tagegenau** (ADR-0004) | vor M3-Impl |
| **E3-2** | Rundungsmechanik | [ENTSCHEIDUNG] | **kaufmännisch + Largest-Remainder** (ADR-0005), Σ==Gesamt exakt | vor M3-Impl |
| **E3-3** | Ein Lauf je Mieter/Lease vs. ein Lauf mit mehreren Adressaten | [ENTSCHEIDUNG] | **ein Lauf je Mieter** (§3.5) | bei Impl |
| **E3-4** | Speicherort manuelle Vorauszahlungssumme | [ENTSCHEIDUNG] | **Feld am `settlement_run`** (`advance_payments_total` + `_source`) (§7.2) | bei Impl |
| **E3-5** | `settlement_run`/`settlement_line`: Trigger-only (kein Grant-Entzug, weil `draft` editierbar) | [ENTSCHEIDUNG] | **Trigger-only**, volles CRUD-Grant (§1.6) | bei Impl |
| **E3-6** | `run_type`-Hülle für WEG/Plan/Vermögensbericht in derselben Tabelle | [ENTSCHEIDUNG] | **ja, gleiche Hülle**, Detail-Payload je Typ in M4 (§1.3) | M4 (jetzt reservieren) |
| **O3-1** | Eingabeweg externer Heiz-/Wasserwerte (Betrag/Einheit, PDF, Plausibilität) | [OFFEN/§18] | nicht eigenmächtig; provisorisch per-(Einheit,Nutzzeitraum); StB/Messdienst | vor produktiv |
| **O3-2** | CO2 10-Stufen: selbst rechnen vs. Mieteranteil übernehmen | [OFFEN/§18] | provisorisch **übernehmen** (`apportionable_tenant_amount`); eigene Stufenlogik = separater, abnahmepflichtiger Baustein | vor produktiv |
| **O3-3** | Gewerbe-Vorwegabzug (andere Umlagelogik, USt-Option) | [OFFEN/§18] | provisorisch **keiner** automatisch; ggf. manuell; StB/Fachanwalt | spätere Runde |
| **O3-4** | Exakte PDF-Struktur BK-Abrechnung (Layout, Einwendungsfrist-Hinweis) | [OFFEN/§18] | Daten vollständig bereit; Layout mit Produkt/Fachseite | vor produktiv |
| **O3-5** | Datenquelle/Logik `PERSONENZAHL`, Verbrauchs-Ersatzverfahren | [OFFEN/§18] | provisorische Annahmen (M3-A7/A8); fachlich klären | vor produktivem Einsatz dieser Schlüssel |

> **Keine Selbst-Zertifizierung (Haltung):** Dieses Design macht die Verteilung, die anteiligen Zeiträume, die Rundung und die Soll/Ist-Rechnung **transparent, deterministisch und nachrechenbar** (Golden-Tests + Property-based Invarianten als objektive Messlatte). Es kann **nicht** garantieren, dass eine konkrete BK-Abrechnung im Einzelfall gerichts-/prüfungsfest ist. Die fachliche Richtigkeit der Regeln (insb. CO2-Stufen, Gewerbe-Vorwegabzug, externe Heizkosten, Aufbewahrung) ist vor produktivem Einsatz durch **Steuerberater/Fachanwalt** abzunehmen.

---

## 13. Was dieses Dokument bewusst NICHT entscheidet

- **WEG-Jahresabrechnung / Abrechnungsspitze (WEMoG), Wirtschaftsplan, Vermögensbericht (§ 28 IV WEG):** M4 — nutzt diese Engine (`owner_distribution_key`, Eigentümersphäre, `reserve_relevant`-Trennung) und dieselbe Snapshot-/Trigger-Mechanik wieder; `run_type` ist reserviert (§1.3).
- **CO2-10-Stufen-Eigenberechnung, Gewerbe-Vorwegabzug, Verbrauchs-Ersatzverfahren:** abgegrenzte, fachlich abzunehmende Bausteine (§5/§6/§2.4, SDD §18).
- **Echte Zahlungen/Mietkonto (Vorauszahlung `DERIVED`):** M6 (§7.2).
- **Exaktes PDF-Layout / Einwendungsfrist-Texte:** SDD §18 (§8.1).
- **§ 35a EStG-Ausweis** in Eigentümerabrechnungen: M4/SDD §18.
- **Endgültige Rechtsabnahme:** Steuerberater/Fachanwalt — nicht durch dieses Dokument ersetzbar.
