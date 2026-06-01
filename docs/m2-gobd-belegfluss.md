# M2-Design: Kosten, Standardkontenplan & GoBD-Belegfluss

Stand: 2026-06-01 · Version 0.1 (Design, vor Implementierung)
Bezug: [architecture.md](architecture.md) §3 (GoBD-Unveränderbarkeit), §5.4 (I2-tragende Entitäten) · [roadmap.md](roadmap.md) M2 · SDD v0.2 §5.3/§6.4, §6.5, §6.6, §7, §8/§9, §14, §15, §16
Autor: `engine-correctness` (Opus 4.8) · Status: **Entwurf zur Bestätigung durch den Menschen — kein Anwendungscode, keine Migration, kein Commit**
ADR zu diesem Dokument: [adr/0003-gobd-unveraenderbarkeit-mechanik.md](adr/0003-gobd-unveraenderbarkeit-mechanik.md)

> Dieses Dokument vertieft die in `architecture.md` §3 und §5.4 gelegten Grundlagen zu einer **implementierbaren** Spezifikation für M2 (Kosten & GoBD-Belegfluss). Es trifft die korrektheits-/architekturkritischen Entscheidungen (Unveränderbarkeits-Mechanik, Versionskette, Statusmaschine, Storage-Abstraktion) und übergibt eine abarbeitbare Checkliste an `bulk-impl` (Opus 4.6).
>
> **Verbindlichkeit:** SDD (v0.2), Roadmap, `architecture.md` und die ADRs 0001/0002 sind die Quelle der Wahrheit und bleiben **unangetastet**. Dieses Dokument ist additiv. Entscheidungen, die der Mensch bestätigen sollte, sind mit **[ENTSCHEIDUNG]** markiert; offene SDD-§18-Punkte mit **[OFFEN]**.
>
> **Keine Selbst-Zertifizierung:** Dieses Design macht die Belegablage *transparent, nachvollziehbar und auditierbar* und legt Annahmen offen. Es kann nicht garantieren, dass die Ablage im konkreten Fall prüfungs-/gerichtsfest ist. Vor Produktivsetzung der GoBD-/DSGVO-relevanten Teile (spätestens M9) ist eine fachliche Abnahme durch Steuerberater/Fachanwalt vorgesehen.

---

## 0. Geltungsbereich und Anschluss an M0/M1

**M2-Exit (Roadmap):** „Eine hochgeladene PDF-Rechnung wird als Vorschlag erkannt, bestätigt, unveränderbar archiviert und ist abrechnungsrelevant." Dieses Kriterium ist die Messlatte.

M2 baut auf bereits implementierten und verifizierten Fundamenten (58 Tests grün) auf und übernimmt deren Konventionen **unverändert**:

| Vorhandenes Fundament (M0/M1) | M2 nutzt es so |
| --- | --- |
| RLS-Muster: `ENABLE`+`FORCE`, vier Policies pro Tenant-Tabelle (`org_isolation_select/insert/update/delete`) mit `NULLIF(current_setting('app.current_org_id', true), '')::uuid`, fail-closed | Jede neue Tenant-Tabelle erhält exakt dieses Muster (eigene Migration, analog `0002`/`0004`). |
| `app_rw`-Grant-Muster; `audit_event` append-only via `GRANT SELECT, INSERT` + weggelassene DELETE-Policy + Coverage-Test | Vorbild für Beleg-Unveränderbarkeit (ADR-0003). |
| `TENANT_TABLES`-Registry + `test_rls_coverage.py` (jede Tabelle muss eingeordnet sein, sonst Test rot) | Wird um die M2-Tabellen erweitert; `cost_category` kommt nach Annahme A-1-Entscheidung in `TENANT_TABLES` **oder** `GLOBAL_TABLES`. |
| Repository-Schicht (`TenantRepository`, expliziter `organization_id`-Filter zusätzlich zu RLS) | Alle M2-Repos erben davon; append-only-Repos analog `AuditRepository` (kein `update`/`delete`). |
| `AuditEvent` + `AuditRepository` | M2 schreibt Beleg-/Kosten-/Freigabe-Events darüber (§6). |
| Geld-/Quoten-Konvention: `NUMERIC(14,2)` für EUR, `NUMERIC(12,6)` für Anteile, app-seitig `Decimal`; UUID-PK; `TIMESTAMPTZ`; `ondelete="RESTRICT"` auf FKs zu `organization` | Durchgängig übernommen. |
| Temporale EXCLUDE-Constraints (btree_gist) als Vorbild für DB-erzwungene Integrität | Für Versions-Eindeutigkeit der Belege wiederverwendet (§4). |
| Swappable `PdfRenderer`-Protocol (`app/core/pdf.py`) | Vorbild für die Storage-Abstraktion (§5) und für die OCR-Extraktions-Abstraktion (§3.5). |
| `mandate_type`/`usage_type` als `String` + `CHECK (... IN (...))`-Constraint (keine DB-Enums) | M2 nutzt dieselbe String+CHECK-Konvention für alle Aufzählungen (Status, Verteilerschlüssel, Umlagefähigkeit). |
| `decode_session_token` liefert `uid`/`oid`/`role`; `require_write_dep` (OWNER_ADMIN|CLERK) | Belegfreigabe ist eine Schreib-Operation → `require_write_dep`; READ_ONLY darf nicht freigeben. |

**Neue Tabellen in M2:** `cost_category`, `cost_entry`, `invoice_document`. Optional (siehe §3.5) eine Extraktions-Hilfstabelle `document_extraction` — Empfehlung: vorerst **nicht** als eigene Tabelle, sondern als JSONB-Spalte am `invoice_document` (Begründung in §3.5).

**Nicht in M2:** Abrechnungs-Snapshots (`settlement_run`/`settlement_line`) — M3/M4. E-Rechnung-Parsing (XRechnung/ZUGFeRD) — M5 (M2 spezifiziert nur das Zusammenspiel mit der Statusmaschine, damit M5 andockt). Zahlungen — M6.

---

## 1. Standardkontenplan (`cost_category`)

### 1.1 Fachliche Quelle und Anspruch

SDD §7: **fester** Standardkontenplan für Phase 1; eigene Kategorien werden nicht angelegt (SDD §3 Nicht-Ziel: „kein frei konfigurierbarer Kontenplan"). Jede Kategorie bringt **Defaults** für Umlagefähigkeit und Verteilerschlüssel mit, die pro Kostenposition manuell übersteuert werden können (SDD §7, §8). `architecture.md` §16/§1.2 verlangt „Kostenkategorien als **versionierte** Systemdaten".

Die Defaults bilden **rechtliche Vorgaben** ab (SDD §17: BetrKV, BGB §556, WEG §16, CO2KostAufG …). Genau deshalb sind sie versioniert: Ändert sich die Rechtslage (z. B. Kabel/Breitband-Umlagefähigkeit seit 2024, SDD §7.2), darf eine **neue Version** des Kontenplans entstehen, ohne dass alte, bereits abgerechnete Kostenpositionen rückwirkend ihre Default-Herkunft verlieren. Das ist die Brücke zwischen „fester Plan" (keine Nutzer-Anlage) und „versioniert" (Recht ändert sich).

### 1.2 [ENTSCHEIDUNG] Global vs. pro-Org kopiert (Annahme A-1)

`architecture.md` Annahme A-1 hält dies offen. **Empfehlung: globaler, versionierter Stammdatensatz (kein `organization_id`), in `GLOBAL_TABLES`** — analog zur Tabelle `organization`.

Begründung:
- SDD §3/§7 schließt org-eigene Kategorien in Phase 1 **explizit aus**. Ein pro-Org kopierter Plan wäre Komplexität (RLS, Seed je Org, Drift zwischen Orgs) ohne fachlichen Gegenwert, solange Orgs den Plan nicht anpassen dürfen.
- „Versioniert" wird **nicht** durch pro-Org-Kopien erreicht, sondern durch eine `version`-Achse auf der globalen Tabelle (siehe 1.3). Eine Kostenposition referenziert die konkrete Kategorie-Version, die zum Erfassungszeitpunkt galt — damit ist die Herkunft prüffest, ohne den Plan zu vervielfältigen.
- Lesezugriff für alle Orgs, Schreibzugriff nur via Migration/Seed (Systemrolle). Das entspricht exakt der Behandlung anderer globaler Systemstammdaten in ADR-0001/`architecture.md` §2.5.

**Konsequenz für `test_rls_coverage.py`:** `cost_category` wird zu `GLOBAL_TABLES` hinzugefügt und der Test `test_global_tables_have_no_rls` deckt sie ab (kein RLS). **Falls** der Mensch A-1 kippt (Orgs dürfen den Plan anpassen/versionieren), wird `cost_category` stattdessen tenant-behaftet (`organization_id`, RLS, in `TENANT_TABLES`) und pro Org beim Org-Anlegen geseedet — der Modell-Umbau ist auf diese eine Tabelle begrenzt, weil `cost_entry` ohnehin nur per FK auf eine Kategorie-Zeile zeigt. **Diese Entscheidung sollte vor der M2-Implementierung fallen.**

### 1.3 Schema `cost_category` (Empfehlung: global)

| Spalte | Typ | Bedeutung |
| --- | --- | --- |
| `id` | `UUID` PK | wie überall. |
| `plan_version` | `SMALLINT NOT NULL` | Version des Gesamt-Kontenplans (1 in Phase 1). Erlaubt spätere Rechtsänderungen als neue Version. |
| `code` | `String(50) NOT NULL` | stabiler fachlicher Schlüssel, z. B. `GRUNDSTEUER`, `KALTWASSER`, `CO2_KOSTEN`, `INSTANDHALTUNG`. Sprach-/UI-unabhängig. |
| `label` | `String(255) NOT NULL` | Anzeigename, z. B. „Grundsteuer / öffentliche Lasten". |
| `cost_group` | `String(20) NOT NULL` | `UMLAGEFAEHIG` \| `GEMISCHT_PRUEFEN` \| `NICHT_UMLAGEFAEHIG` (entspricht SDD §7.1/§7.2/§7.3). `CHECK`-Constraint. |
| `tenant_apportionability_default` | `String(24) NOT NULL` | Mieter-BK-Default als kontrolliertes Vokabular (siehe 1.4). |
| `owner_billing_relevant_default` | `BOOLEAN NOT NULL` | Spalte „Eigentümerabrechnung ja" aus SDD §7 (durchgehend `true` im Plan, aber explizit modelliert). |
| `owner_distribution_key_default` | `String(20) NULL` | Default-Verteilerschlüssel Eigentümerabrechnung (kontrolliertes Vokabular, §1.5). |
| `tenant_distribution_key_default` | `String(20) NULL` | Default-Verteilerschlüssel Mieterabrechnung. NULL, wenn nicht umlagefähig. |
| `reserve_relevant` | `BOOLEAN NOT NULL DEFAULT false` | markiert Rücklagen-Kategorien (Erhaltungsrücklage) für die getrennte WEG-Ausweisung (SDD §11, M4). |
| `legal_hint` | `String(500) NULL` | Hinweistext (z. B. „seit 2024 eingeschränkte Umlagefähigkeit"), für UI/Plausibilität. |
| `sort_order` | `SMALLINT NOT NULL DEFAULT 0` | feste Reihenfolge in Listen/PDF. |
| `is_active` | `BOOLEAN NOT NULL DEFAULT true` | erlaubt das Stilllegen einer Kategorie in einer neuen Plan-Version, ohne sie zu löschen (Historien-Integrität). |
| `created_at` | `TIMESTAMPTZ` | wie überall. |

Constraints: `UNIQUE (plan_version, code)`; `CHECK (cost_group IN (...))`; `CHECK (tenant_apportionability_default IN (...))`; `CHECK (owner_distribution_key_default IS NULL OR owner_distribution_key_default IN (...))` (analog tenant). **Kein** `organization_id`, **kein** RLS (globaler Stammdatensatz).

`cost_entry` referenziert `cost_category_id` (FK). Weil eine bestehende Zeile nie inhaltlich geändert, sondern höchstens via neue `plan_version` ersetzt wird, bleibt die zum Erfassungszeitpunkt gewählte Kategorie-Version prüffest erhalten. **[ENTSCHEIDUNG]** ob `cost_entry` zusätzlich `plan_version` redundant mitschreibt (Empfehlung: ja, als denormalisierter Prüf-Anker — billig und macht Abrechnungen unabhängig von späteren Plan-Versionen).

### 1.4 Kontrolliertes Vokabular: Umlagefähigkeit (Mieter-BK-Default)

Direkt aus SDD §7.1–§7.3 abgeleitet (String + CHECK, kein DB-Enum, konsistent zu M0/M1):

| Wert | Bedeutung | SDD-Quelle |
| --- | --- | --- |
| `UMLAGEFAEHIG` | grundsätzlich auf Mieter umlagefähig | §7.1 |
| `UMLAGEFAEHIG_EXTERN_PRUEFEN` | umlagefähig, aber über externe Abrechnung (Heizung/Warmwasser/Messdienst) — Doppelabrechnung vermeiden | §7.1/§7.2 (Heizung, Warmwasser, Messdienst) |
| `GEMISCHT_PRUEFEN` | gemischt, Anteile herausrechnen (Hausmeister: Verwaltungs-/Reparaturanteil raus) | §7.2 (Hausmeister) |
| `TEILWEISE_PRUEFEN` | nur ein Anteil umlagefähig (CO2: nur Mieteranteil, 10-Stufen) | §7.2 (CO2) |
| `MANUELL_PRUEFEN` | nur bei konkreter mietvertraglicher Grundlage (Sonstige BK; Kabel/Breitband seit 2024) | §7.2 |
| `NICHT_UMLAGEFAEHIG` | nie Betriebskosten (Verwaltung, Instandhaltung, Rücklage, Sonderumlage …) | §7.3 |

Wichtig: Diese Werte sind **Defaults/Flags**, keine automatische Buchungsentscheidung. Die Engine (M3) entscheidet je nach effektivem Wert *pro Kostenposition*, ob/wie umgelegt wird; bei `*_PRUEFEN`-Werten ist eine bewusste Nutzer-Entscheidung nötig (Vorschlagswerkzeug, SDD §8/§9 — „keine automatische Buchung ohne Freigabe").

### 1.5 Kontrolliertes Vokabular: Verteilerschlüssel (SDD §8)

`MEA` · `WOHNFLAECHE` · `BEHEIZTE_FLAECHE` · `EINHEIT` · `PERSONENZAHL` · `VERBRAUCH` · `EXTERNE_ABRECHNUNG` · `MANUELLER_BETRAG`.

Dies ist genau die SDD-§8-Liste. Eigentümer- und Mieterabrechnung können je Kostenposition **unterschiedliche** Schlüssel tragen (SDD §8 Beispiel: Eigentümer nach MEA, Mieter nach Wohnfläche/extern). Deshalb getrennte Default-Spalten in `cost_category` (1.3) **und** getrennte effektive Spalten in `cost_entry` (§2). Die *Semantik* jedes Schlüssels (wie verteilt wird) ist M3 (Verteilungs-Engine) — M2 speichert nur die Auswahl.

### 1.6 Seed/Migration der SDD-§7-Tabellen

Der Plan wird als **Daten-Seed in einer eigenen Alembic-Migration** angelegt (nicht in einem Fixture, nicht zur Laufzeit), damit er versioniert, reviewbar und in jeder Umgebung identisch ist — dieselbe Disziplin wie bei RLS-Policies (ADR-0002).

- Migration `0005_cost_category` (Vorschlag): `CREATE TABLE cost_category` + `INSERT`-Zeilen für **alle** Kategorien aus SDD §7.1, §7.2, §7.3 mit `plan_version = 1`, `code`, `label`, `cost_group`, Default-Umlagefähigkeit und Default-Verteilerschlüsseln **exakt gemäß den SDD-Tabellen**. Beispiel-Zeilen (vollständige Liste in der Migration):

  | code | cost_group | tenant_apport_default | owner_dist_default | tenant_dist_default | reserve |
  | --- | --- | --- | --- | --- | --- |
  | `GRUNDSTEUER` | UMLAGEFAEHIG | UMLAGEFAEHIG | `MEA` | `WOHNFLAECHE` | false |
  | `KALTWASSER` | UMLAGEFAEHIG | UMLAGEFAEHIG | `VERBRAUCH` | `VERBRAUCH` | false |
  | `HEIZUNG_ZENTRAL` | UMLAGEFAEHIG | UMLAGEFAEHIG_EXTERN_PRUEFEN | `EXTERNE_ABRECHNUNG` | `EXTERNE_ABRECHNUNG` | false |
  | `HAUSMEISTER` | GEMISCHT_PRUEFEN | GEMISCHT_PRUEFEN | `MEA` | `WOHNFLAECHE` | false |
  | `CO2_KOSTEN` | GEMISCHT_PRUEFEN | TEILWEISE_PRUEFEN | `EXTERNE_ABRECHNUNG` | `EXTERNE_ABRECHNUNG` | false |
  | `KABEL_BREITBAND` | GEMISCHT_PRUEFEN | MANUELL_PRUEFEN | `EINHEIT` | `EINHEIT` | false |
  | `WEG_VERWALTERKOSTEN` | NICHT_UMLAGEFAEHIG | NICHT_UMLAGEFAEHIG | `MEA` | NULL | false |
  | `INSTANDHALTUNG` | NICHT_UMLAGEFAEHIG | NICHT_UMLAGEFAEHIG | `MEA` | NULL | false |
  | `ERHALTUNGSRUECKLAGE` | NICHT_UMLAGEFAEHIG | NICHT_UMLAGEFAEHIG | `MEA` | NULL | true |
  | `SONDERUMLAGE` | NICHT_UMLAGEFAEHIG | NICHT_UMLAGEFAEHIG | `MEA` | NULL | false |

  Die exakten Default-Verteilerschlüssel pro Zeile folgen der SDD-§7-Spalte „Standard-Verteilung". Wo die SDD mehrere Optionen nennt („Fläche oder MEA"), wählt der Seed einen sinnvollen Default (für Eigentümer i. d. R. `MEA`, für Mieter i. d. R. `WOHNFLAECHE`); der Nutzer kann pro Kostenposition übersteuern. **[ENTSCHEIDUNG]** pro Mehrdeutigkeit den konkreten Default bestätigen — das ist eine fachliche Festlegung, keine technische. *(Bei A-1 = pro-Org: identischer Seed, aber je Org beim Anlegen ausgeführt und tenant-behaftet.)*
- `downgrade()` löscht die Seed-Zeilen und die Tabelle.
- **Idempotenz/Erweiterung:** Eine künftige Rechtsänderung kommt als **neue Migration** mit `plan_version = 2` und neuen Zeilen — alte Zeilen bleiben unangetastet (Versionierung, 1.1).

---

## 2. Kostenposition (`cost_entry`)

### 2.1 Fachliche Quelle

SDD §5.3/§6.4 (Felder), §14 (Leistungszeitraum, nicht nur Rechnungsdatum), §8 (getrennte Verteilerschlüssel), §9 (Status). Tenant-behaftet (`organization_id`, RLS — vier Policies wie M1).

### 2.2 Schema `cost_entry`

| Spalte | Typ | Bedeutung / Begründung |
| --- | --- | --- |
| `id` | `UUID` PK | |
| `organization_id` | `UUID NOT NULL` FK→`organization` (`RESTRICT`) | Mandantentrennung (I1), RLS. |
| `property_id` | `UUID NOT NULL` FK→`property` (`RESTRICT`) | Objektbezug (SDD §6.4). |
| `management_mandate_id` | `UUID NULL` FK→`management_mandate` (`RESTRICT`) | optionaler Mandatsbezug (SDD §6.4) — Anker für Vermögenstrennung (architecture.md §2.6). |
| `cost_category_id` | `UUID NOT NULL` FK→`cost_category` (`RESTRICT`) | Kategorie aus Standardkontenplan. |
| `cost_category_plan_version` | `SMALLINT NOT NULL` | denormalisierter Prüf-Anker (siehe 1.3) — macht die Abrechnung unabhängig von späteren Plan-Versionen. |
| `vendor_id` | `UUID NULL` FK→`vendor` (`RESTRICT`) | Lieferant/Rechnungsaussteller (SDD §6.2/§6.4). Nullable, weil Entwürfe aus OCR den Lieferanten evtl. erst nach Review zugeordnet bekommen. |
| `invoice_date` | `DATE NOT NULL` | Rechnungsdatum (SDD §6.4). |
| `service_period_from` | `DATE NOT NULL` | **Leistungszeitraum Beginn** (SDD §6.4/§14 — Kern für anteilige Verteilung in M3, *nicht* das Rechnungsdatum). |
| `service_period_to` | `DATE NOT NULL` | Leistungszeitraum Ende. `CHECK (service_period_to >= service_period_from)`. |
| `gross_amount` | `NUMERIC(14,2) NOT NULL` | Bruttobetrag (I4, nie Float). Vorzeichen erlaubt (Gutschriften). |
| `vat_amount` | `NUMERIC(14,2) NULL` | USt-Betrag, wenn verfügbar (SDD §6.4 — v. a. aus E-Rechnung, M5). |
| `vat_rate` | `NUMERIC(5,2) NULL` | USt-Satz in Prozent (z. B. 19.00), wenn verfügbar. |
| `document_number` | `String(100) NULL` | Belegnummer (SDD §6.4). |
| `owner_distribution_key` | `String(20) NULL` | **effektiver** Verteilerschlüssel Eigentümerabrechnung (SDD §8) — vorbelegt aus Kategorie-Default, manuell übersteuerbar. CHECK gegen §1.5-Vokabular. |
| `tenant_distribution_key` | `String(20) NULL` | **effektiver** Verteilerschlüssel Mieterabrechnung — getrennt vom Eigentümer-Schlüssel (SDD §8). |
| `tenant_apportionable` | `String(24) NOT NULL` | **effektive** Umlagefähigkeit (Mieter), vorbelegt aus Kategorie-Default, übersteuerbar. CHECK gegen §1.4-Vokabular. |
| `apportionable_tenant_amount` | `NUMERIC(14,2) NULL` | bei `TEILWEISE_PRUEFEN`/`GEMISCHT_PRUEFEN` der explizit als umlagefähig bestimmte Mieteranteil (z. B. CO2-Mieteranteil, herausgerechneter Hausmeister-Anteil). NULL = ganzer/keiner Betrag je nach `tenant_apportionable`. **[OFFEN]** genaue Modellierung des Vorwegabzugs/Anteils ist M3-Detail (SDD §18: Hausmeister-/CO2-Aufteilung); M2 hält das Feld bereit. |
| `status` | `String(16) NOT NULL` | Belegfluss-Status: `draft_extracted` \| `needs_review` \| `confirmed` \| `rejected` (SDD §9). CHECK. Default `needs_review` bei manueller Anlage? → siehe 2.3. |
| `source` | `String(12) NOT NULL` | Herkunft: `MANUAL` \| `PDF_IMPORT` \| `E_INVOICE` (E-Rechnung erst M5, Wert reserviert). CHECK. |
| `confirmed_at` | `TIMESTAMPTZ NULL` | Zeitpunkt der Freigabe (Übergang nach `confirmed`). |
| `confirmed_by` | `UUID NULL` FK→`user` (`SET NULL`) | Wer freigegeben hat (Audit/Nachvollziehbarkeit). |
| `superseded_by_id` | `UUID NULL` FK self (`RESTRICT`) | bei Korrektur: zeigt auf die Nachfolge-Kostenposition (Versionskette, §3.4). |
| `created_at`/`updated_at` | `TIMESTAMPTZ` | wie überall. |

Indizes: `organization_id`; `property_id`; `(organization_id, status)` (häufiger Filter: „offene Vorschläge"); `cost_category_id`; `(property_id, service_period_from, service_period_to)` (M3 zieht Kosten je Objekt/Zeitraum). FK-Spalten je indiziert.

**Begründung Leistungszeitraum NOT NULL (SDD §14):** Die Verteilungs-Engine (M3) berechnet anteilige Zeiträume bei Eigentümer-/Mieterwechsel **anhand des Leistungszeitraums**, nicht des Rechnungsdatums (eine im Januar gestellte Rechnung für Dezember-Leistung gehört ins Vorjahr). Ein fehlender Leistungszeitraum würde die Kernkorrektheit von M3 untergraben — daher Pflichtfeld. Bei OCR-Entwürfen, bei denen der Zeitraum noch nicht erkannt ist, hält die Anwendung den Datensatz im Status `needs_review`; die Freigabe nach `confirmed` setzt einen gültigen Zeitraum voraus (Validierungsregel, 2.3).

### 2.3 Statusabhängige Pflichtfelder

Im Entwurf (`draft_extracted`/`needs_review`) dürfen Felder unvollständig sein (OCR liefert evtl. nicht alles). Der **Übergang nach `confirmed`** ist die Stelle, an der fachliche Vollständigkeit erzwungen wird (Anwendungs-Validierung im Confirm-Service, §3.3):
- `cost_category_id`, `gross_amount`, `invoice_date`, `service_period_from/to`, `tenant_apportionable`, mindestens ein Verteilerschlüssel je relevanter Abrechnungssphäre müssen gesetzt/plausibel sein.
- Bei `*_PRUEFEN`-Umlagefähigkeit: bewusste Nutzer-Bestätigung des effektiven Werts (kein stilles Default-Durchwinken).

> **[ENTSCHEIDUNG]** Default-Status bei **manueller** Anlage: `needs_review` (konservativ, Vier-Augen-fähig) vs. direkt editierbarer Entwurf, den derselbe Nutzer sofort `confirmed` setzen darf. Empfehlung: manuelle Anlage startet als normaler, editierbarer Datensatz; `confirmed` ist immer ein **expliziter** zweiter Schritt (nie implizit beim Speichern), damit „abrechnungsrelevant" eine bewusste Handlung bleibt (SDD §9 Geist).

---

## 3. Belegfluss-Statusmaschine (SDD §8/§9)

### 3.1 Zustände

```
        (Upload PDF / E-Rechnung)                  (manuelle Anlage)
                 │                                        │
                 ▼                                        ▼
        ┌─────────────────┐   unsicher / kritisch  ┌──────────────┐
        │ draft_extracted │ ─────────────────────▶ │ needs_review │
        └────────┬────────┘                         └──────┬───────┘
                 │  (Nutzer prüft / korrigiert Felder; Beleg-Inhalt editierbar)
                 │                                         │
                 │  reject                                 │ reject
                 ▼                                         ▼
            ┌──────────┐                              ┌──────────┐
            │ rejected │ ◀────────────────────────────│ rejected │
            └──────────┘                              └────┬─────┘
                                                           │ confirm  (Freigabe)
                                                           ▼
                                                   ┌───────────────┐
                                                   │   confirmed   │  ← INHALT EINGEFROREN
                                                   └───────┬───────┘
                                                           │ Korrektur nötig?
                                                           │  → NIE Mutation
                                                           ▼
                                          neue cost_entry-Version (status=needs_review/confirmed),
                                          Original.superseded_by_id → neue Zeile  (siehe 3.4)
```

### 3.2 Erlaubte Übergänge (präzise)

| Von | Nach | Bedingung | Effekt |
| --- | --- | --- | --- |
| `draft_extracted` | `needs_review` | Erkennung unsicher / kritische Kategorie | Inhalt bleibt editierbar. |
| `draft_extracted` | `confirmed` | Pflichtfelder vollständig (2.3), Nutzer bestätigt | **Einfrieren** (3.3). |
| `draft_extracted` | `rejected` | Nutzer verwirft | Datensatz inaktiv, bleibt erhalten (kein Hard-Delete im belegtragenden Pfad). |
| `needs_review` | `confirmed` | wie oben | **Einfrieren**. |
| `needs_review` | `rejected` | verwerfen | inaktiv. |
| `confirmed` | — | **kein** weiterer Statuswechsel durch Mutation | Korrektur ausschließlich via Neuversion (3.4). |
| `rejected` | (Entwurf) | optional „wieder aufnehmen" | **[ENTSCHEIDUNG]**: erlauben oder verworfen = endgültig? Empfehlung: `rejected` ist editierbar zurückholbar **solange kein Beleg festgeschrieben** wurde; nach Festschreibung nicht. |

Jeder Übergang erzeugt ein `audit_event` (§6). Übergänge, die nicht in der Tabelle stehen, sind verboten (Service prüft; DB-Trigger blockt die kritischen, ADR-0003).

### 3.3 „Eingefroren ab `confirmed`" — was genau heißt das

Der **inhaltstragende Teil** der Kostenposition (Betrag, USt, Kategorie, Lieferant, Datum, Leistungszeitraum, Verteilerschlüssel, Umlagefähigkeit, Objekt/Mandat) ist ab `confirmed` **unveränderbar** — DB-erzwungen per `BEFORE UPDATE`-Trigger (ADR-0003). Gleichzeitig wird, **falls** ein Beleg angehängt ist, dessen `invoice_document`-`active`-Version mit `released_at`/`released_by` **freigegeben** und damit ebenfalls eingefroren (§4). „Confirm der Kostenposition" und „Release des Belegs" sind dieselbe Transaktion (§3.6).

Reine Status-/Verkettungsfelder (`status` von `confirmed`→`superseded`-Markierung via `superseded_by_id`) sind die **einzige** zulässige Änderung an einer eingefrorenen Zeile — der Trigger lässt genau diese Spalten zu und nichts anderes (ADR-0003 §1).

### 3.4 Korrektur = neue Version (statt Mutation, SDD §14)

Muss eine `confirmed`-Kostenposition korrigiert werden (falscher Betrag, falsche Kategorie):
1. Es wird eine **neue** `cost_entry`-Zeile angelegt (eigene `id`), mit den korrigierten Werten, Status `needs_review` (oder direkt `confirmed`, wenn die Korrektur eindeutig ist), `source` = `MANUAL`.
2. Die alte Zeile wird über `superseded_by_id` auf die neue Zeile verwiesen. Ihr Inhalt bleibt **unverändert** (eingefroren). Sie wird in Abrechnungen nicht mehr als aktiv gezählt — die Aktiv-Logik wählt die jüngste Zeile einer Kette ohne `superseded_by_id`.
3. Der zugehörige Beleg wird, falls er ebenfalls korrigiert wird (neue Datei), als **neue `invoice_document`-Version** angehängt (§4.4); referenziert die korrigierte/zutreffende Kostenposition.
4. `audit_event`: `cost_entry.superseded` (alt) + `cost_entry.created`/`confirmed` (neu), mit Bezug.

**Aktiv-Definition (für M3-Engine):** Eine Kette beginnt mit der Erst-Zeile und verläuft über `superseded_by_id`. „Aktiv" ist die einzige Zeile der Kette mit `superseded_by_id IS NULL` **und** Status `confirmed`. Das ist die Zeile, die abgerechnet wird. *(Symmetrisch zur Versionskette der Belege, §4.3 — bewusst zwei nebeneinanderliegende, aber getrennte Ketten: eine fachliche `cost_entry`-Korrekturkette, eine datei-/dokumentbezogene `invoice_document`-Versionskette.)*

> **[ENTSCHEIDUNG]** `superseded_by_id` (Vorwärts-Zeiger, hier vorgeschlagen) vs. `supersedes_id` (Rückwärts-Zeiger wie bei `invoice_document` in architecture.md §5.4). Empfehlung: bei `cost_entry` **Vorwärts** (`superseded_by_id`), weil die Aktiv-Abfrage „WHERE superseded_by_id IS NULL" der häufigste, performanteste Query ist; bei `invoice_document` bleibt es beim **Rückwärts**-Zeiger `supersedes_id` (architecture.md §5.4), dort ist die Versions-Historie das Leitbild. Beide sind valide; wichtig ist, dass je Tabelle **eine** Richtung konsistent verwendet wird. Bei der Implementierung festklopfen.

### 3.5 Zusammenspiel mit assistiertem PDF-Import/OCR (SDD §8/§9) — nur Schnittstelle

Die **Extraktions-Implementierung** (OCR/Parsing) macht `bulk-impl`; M2-Design legt nur das Zusammenspiel fest:
- Beim Upload entsteht **sofort** ein `invoice_document` (Datei + SHA-256 gespeichert) im Versions-Zustand `active`, aber **ohne** `released_at` (noch nicht freigegeben) und eine `cost_entry` im Status `draft_extracted` mit den extrahierten Feldvorschlägen.
- Die Extraktion ist hinter einer **schmalen, austauschbaren Schnittstelle** (Protocol) gekapselt — exakt das Muster von `PdfRenderer` (`app/core/pdf.py`): `DocumentExtractor.extract(file_bytes, mime) -> ExtractionResult`. Default: lokale OCR/Parsing-Stub-Implementierung; später austauschbar gegen einen externen Dienst oder ein lokales Modell (SDD §16). E-Rechnung (M5) ist eine **zweite** Implementierung derselben Schnittstelle (strukturierte Felder statt OCR-Raten, SDD §6.5) — dadurch dockt M5 ohne Statusmaschinen-Änderung an.
- Das Extraktionsergebnis (erkannte Felder + Konfidenzen + Roh-Text-Merkmale) wird als **JSONB-Spalte `extraction_payload` am `invoice_document`** gespeichert, **nicht** als eigene Tabelle. Begründung: 1:1 zum Dokument, kein eigener Lebenszyklus, keine Cross-Entity-Query nötig; eine eigene Tabelle wäre unnötige Normalisierung. *(Falls später mehrere Extraktionsläufe je Dokument verglichen werden sollen, lässt sich das zu einer Tabelle `document_extraction` herauslösen — dann tenant-behaftet mit RLS. Für M2 nicht nötig.)*
- Konfidenz-Schwelle entscheidet Start-Status: niedrige Konfidenz / kritische Kategorie → `needs_review` statt `draft_extracted`. Die genaue Schwelle ist eine `bulk-impl`-Implementierungsfrage; **kein** Vorschlag wird je automatisch `confirmed` (SDD §9, §3 Nicht-Ziel: „keine automatische Buchung ohne Freigabe").

### 3.6 Freigabe-Transaktion (atomar)

Confirm einer Kostenposition mit angehängtem Beleg ist **eine** DB-Transaktion (eine `get_db`-Session, ein `session.begin()`):
1. Validierung der Pflichtfelder (2.3); bei Verstoß → 422, kein Statuswechsel.
2. `cost_entry.status = 'confirmed'`, `confirmed_at = now()`, `confirmed_by = uid`.
3. Falls Beleg vorhanden: `invoice_document` (active-Version) `released_at = now()`, `released_by = uid`, `retention_until` berechnen (§7), `content_sha256` **(re-)verifizieren** gegen die gespeicherte Datei (§4.5).
4. `audit_event`: `cost_entry.confirmed` und (falls Beleg) `document.released`.
5. Commit. Ab Commit greifen die Unveränderbarkeits-Trigger (ADR-0003): die Zeilen sind eingefroren.

Schlägt einer der Schritte fehl, rollt die ganze Transaktion zurück — es gibt **keinen** Zustand „Beleg freigegeben, Kostenposition nicht" oder umgekehrt.

---

## 4. `invoice_document` + Unveränderbarkeits-Mechanik (Kern, I2)

### 4.1 Fachliche Quelle

SDD §6.6 (Unveränderbarkeit, Prüfsumme, Versionierung, 8 Jahre), §13 (Entität), §16 (revisionssichere Ablage, Snapshots). `architecture.md` §3.2 (Bausteine) und §5.4 (Felder). Tenant-behaftet (`organization_id`, RLS).

### 4.2 Schema `invoice_document`

| Spalte | Typ | Bedeutung / Begründung |
| --- | --- | --- |
| `id` | `UUID` PK | |
| `organization_id` | `UUID NOT NULL` FK→`organization` (`RESTRICT`) | I1, RLS. |
| `cost_entry_id` | `UUID NULL` FK→`cost_entry` (`RESTRICT`) | zugehörige Kostenposition. Nullable nur im kurzen Moment zwischen Upload und Zuordnung; Regelfall gesetzt. **[ENTSCHEIDUNG]** ob 1:1 (ein Beleg ↔ eine Kostenposition) oder 1:n (ein Beleg über mehrere Positionen, z. B. Sammelrechnung). Empfehlung M2: 1:1 plus optionale spätere Aufteilung als eigene M-Frage; SDD §6.4 spricht von „optional PDF-Datei je Kostenposition" → 1:1 ausreichend. |
| `storage_uri` | `String(1024) NOT NULL` | logischer Verweis auf die Datei im Objektspeicher (§5). **Nicht** der Dateiinhalt. |
| `storage_backend` | `String(16) NOT NULL` | `LOCAL_FS` (M2) \| `S3` (später). Erlaubt Migration des Speichers ohne Schemaänderung. |
| `storage_version` | `String(128) NULL` | versionskennung des Backends (S3 Object-Version-ID / WORM-Marker); bei lokalem FS NULL oder Dateigenerations-Marker. |
| `original_filename` | `String(255) NULL` | Anzeigename beim Upload. |
| `mime_type` | `String(100) NOT NULL` | z. B. `application/pdf`, `application/xml` (E-Rechnung M5). |
| `content_byte_size` | `BIGINT NOT NULL` | Größe; Teil der Integritätsmerkmale. |
| `content_sha256` | `String(64) NOT NULL` | **SHA-256 über den Dateiinhalt** (Hex). Kern der Integrität (SDD §6.6). |
| `version` | `SMALLINT NOT NULL DEFAULT 1` | laufende Versionsnummer der logischen Beleg-Kette. |
| `supersedes_id` | `UUID NULL` FK self (`RESTRICT`) | Rückwärts-Zeiger auf die Vorgänger-Version (architecture.md §5.4). Erst-Version: NULL. |
| `status` | `String(12) NOT NULL DEFAULT 'active'` | `active` \| `superseded`. CHECK. Pro logischer Kette zu jedem Zeitpunkt **genau eine** `active`-Zeile (§4.3). |
| `released_at` | `TIMESTAMPTZ NULL` | Freigabezeitpunkt. NULL = noch nicht festgeschrieben (editierbarer Beleg-Entwurf). Gesetzt = **eingefroren** (ADR-0003 Einfrier-Bedingung). |
| `released_by` | `UUID NULL` FK→`user` (`SET NULL`) | Wer freigegeben hat. |
| `retention_until` | `DATE NULL` | Aufbewahrungsanker (§7). Wird bei `released_at` berechnet. NULL bis Freigabe. |
| `superseded_at` | `TIMESTAMPTZ NULL` | wann diese Version abgelöst wurde. |
| `extraction_payload` | `JSONB NULL` | OCR/Parsing-Ergebnis (§3.5), nur für Vorschlag/Review relevant. |
| `created_at`/`updated_at` | `TIMESTAMPTZ` | |

Indizes: `organization_id`; `cost_entry_id`; `(organization_id, status)`; `content_sha256` (Duplikaterkennung / Integritätsabfrage); `supersedes_id`. `retention_until` (für den späteren M9-Aufbewahrungs-Scan).

### 4.3 Versionskette und „genau eine active"

- Erst-Upload: `version = 1`, `supersedes_id = NULL`, `status = 'active'`.
- Korrektur: neue Zeile `version = N+1`, `supersedes_id = <id der bisherigen active>`, `status = 'active'`; die bisherige Zeile wird auf `status = 'superseded'`, `superseded_at = now()` gesetzt (das ist der **einzige** erlaubte UPDATE einer freigegebenen Zeile, ADR-0003).
- **DB-Garantie „höchstens eine active je Kette":** über einen **partiellen Unique-Index** auf der Ketten-Wurzel. Da `supersedes_id` eine verkettete Liste bildet, wird zusätzlich eine stabile `chain_root_id`-Spalte geführt (auf die Wurzel der Kette zeigend; Erst-Version = eigene `id`), und ein
  `CREATE UNIQUE INDEX uq_active_doc_per_chain ON invoice_document (chain_root_id) WHERE status = 'active'`
  stellt sicher, dass pro logischer Beleg-Kette nie zwei `active`-Versionen existieren. **[ENTSCHEIDUNG]** `chain_root_id` (denormalisiert, einfacher Unique-Index — empfohlen) vs. ein EXCLUDE/Trigger über die rekursive Kette (komplexer). Empfehlung: `chain_root_id`, weil es dem btree_gist-/partiellen-Index-Stil der Codebasis entspricht und die Aktiv-Abfrage trivial macht.

### 4.4 Korrektur eines bereits freigegebenen Belegs

Symmetrisch zu §3.4, aber auf Dateiebene:
1. Neue Datei in den Objektspeicher schreiben, SHA-256 berechnen.
2. Neue `invoice_document`-Zeile (`version+1`, `supersedes_id`=alte active, `chain_root_id` unverändert, `status='active'`); alte Zeile → `superseded`.
3. Falls fachlich nötig, parallel eine `cost_entry`-Korrekturversion (§3.4).
4. `audit_event`: `document.superseded` + `document.version_created` (+ ggf. `document.released` der neuen Version, wenn sie sofort freigegeben wird).

Das Original bleibt physisch und in der DB erhalten (8-Jahre-Aufbewahrung, §7) — Korrektur fügt hinzu, ersetzt nie.

### 4.5 Integritäts-/Hash-Mechanik

- **Berechnung:** SHA-256 über die exakten Datei-Bytes beim Upload; erneut **verifiziert** bei der Freigabe (§3.6 Schritt 3) — stellt sicher, dass zwischen Upload und Freigabe nichts verändert wurde.
- **Verifikations-Funktion:** eine Storage-unabhängige Hilfsfunktion `verify_document_integrity(doc) -> bool`, die die Datei aus dem Backend liest, den Hash neu berechnet und gegen `content_sha256` prüft. Wird genutzt: (a) bei Freigabe, (b) durch einen späteren M9-Audit-/Prüf-Lauf, (c) in Tests (§8).
- **Was der Hash leistet / nicht leistet:** Er macht **Manipulation erkennbar** (Tamper-Evidenz). Er verhindert sie nicht allein — das tun die WORM-Eigenschaften des Speichers (S3 Object-Lock, später) plus die DB-Unveränderbarkeit (Trigger/Grants, ADR-0003). Hash + WORM + Trigger zusammen ergeben Revisionssicherheit; einzeln keiner.

### 4.6 Unveränderbarkeits-Erzwingung → ADR-0003

Die Wahl der DB-nahen Mechanik ist in **[ADR-0003](adr/0003-gobd-unveraenderbarkeit-mechanik.md)** getroffen: **status-bewusster `BEFORE UPDATE/DELETE`-Trigger** als primäre Erzwingung (weil Unveränderbarkeit hier *status-bedingt* ist: editierbar bis Freigabe, danach eingefroren) **+ entzogenes DELETE-Grant** auf `invoice_document` als zweite Linie (Belegdateien werden nie gelöscht). Kurzfassung der Trigger-Regeln:
- `invoice_document`: ist `released_at IS NOT NULL`, blockt der Trigger jede UPDATE-Änderung an `content_sha256`/`storage_uri`/`storage_version`/`version`/`released_at`/`released_by`/`content_byte_size`; erlaubt **nur** `active→superseded` (+ `superseded_at`). DELETE freigegebener Zeilen immer verboten.
- `cost_entry`: ist `status = 'confirmed'`, blockt der Trigger jede inhaltliche Spaltenänderung; erlaubt nur das Setzen von `superseded_by_id` (Korrekturverkettung). DELETE einer `confirmed`-Zeile verboten; Entwürfe/`rejected` löschbar.

---

## 5. Objektspeicher-Abstraktion

### 5.1 Prinzip (wie `PdfRenderer`)

Schmales, austauschbares Interface hinter einem **Protocol** — exakt das Muster von `app/core/pdf.py`. Vorschlag: `app/core/storage.py`.

```text
@runtime_checkable
class DocumentStore(Protocol):
    def put(self, key: str, data: bytes, *, content_type: str) -> StoredObjectRef: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    # bewusst KEIN delete() im Protokoll der Phase 1 -- Belege werden nie gelöscht.
    # Eine spätere, retention-gesteuerte Entfernung (M9) ist eine separate,
    # privilegierte Operation, nicht Teil des normalen Belegpfads.

@dataclass(frozen=True)
class StoredObjectRef:
    backend: str          # "LOCAL_FS" | "S3"
    uri: str              # -> invoice_document.storage_uri
    version: str | None   # -> invoice_document.storage_version (S3 version id / WORM marker)
    sha256: str           # zur Quervalidierung gegen die DB
    byte_size: int
```

- **M2-Default `LocalFsDocumentStore`:** schreibt unter ein konfiguriertes Wurzelverzeichnis, Pfad **content-adressiert** und mandantenpartitioniert, z. B. `/<root>/<organization_id>/<sha256[:2]>/<sha256>.bin`. Datei wird nach dem Schreiben read-only gesetzt (chmod) als schwacher WORM-Ersatz; die echte Unveränderbarkeit trägt die DB (ADR-0003) plus später S3 Object-Lock. Schreibt **nie** über eine existierende Datei (gleicher Hash = identischer Inhalt → idempotent; abweichender Inhalt bekäme einen anderen Hash und damit anderen Pfad).
- **Später `S3DocumentStore`:** S3-kompatibel mit **Object-Lock (WORM, Compliance-Mode)** + **Versionierung** (SDD §16, architecture.md §1.1). `storage_version` hält die S3-Version-ID. EU-Region (architecture.md A-6).
- Auswahl über Config (`settings`), nicht im Belegcode hartverdrahtet — Engine-Swap wie beim Renderer (`get_store()`/`set_store()`).

### 5.2 Wo liegt die Datei, wie referenziert die DB sie

- **Datei:** ausschließlich im Objektspeicher. Nie in der DB (kein BLOB) — die DB hält nur Metadaten + Hash + `storage_uri`/`storage_version`/`storage_backend`.
- **Referenz:** `invoice_document.storage_uri` (+ Backend + Version). Content-Adressierung (Pfad = Hash) gibt eine zweite, implizite Integritätskopplung: ein abweichender Inhalt kann nicht „heimlich" unter demselben Pfad liegen.
- **Mandantenpartition im Pfad:** zusätzliche Verteidigung gegen Cross-Org-Vermischung auf Dateisystemebene — konsistent zur Mandantentrennung (I1), auch wenn die Autorisierung über die DB/RLS läuft.

### 5.3 Vertraulichkeit (kurz)

Belege enthalten personenbezogene Daten (DSGVO). Der lokale Speicher liegt in der EU (architecture.md A-6); bei S3 zusätzlich serverseitige Verschlüsselung. Kein öffentlicher Zugriff; Auslieferung an die UI nur über authentifizierte, tenant-geprüfte API-Endpunkte (die Datei wird vom Backend gestreamt, nicht per öffentlicher URL).

---

## 6. Audit-Trail-Nutzung (bestehendes `audit_event` aus M0)

M2 ist der erste Milestone, in dem `audit_event` **intensiv** genutzt wird (architecture.md §5.4). Geschrieben über das vorhandene `AuditRepository` (append-only). Protokollierte Vorgänge (`action`-Werte, `entity_type` = `cost_entry` bzw. `invoice_document`):

| action | wann | payload_diff (Auszug) |
| --- | --- | --- |
| `cost_entry.created` | Anlage (manuell oder aus Import) | source, category, gross_amount, status |
| `cost_entry.updated` | Feldänderung im Entwurf | geänderte Felder (vorher/nachher) |
| `cost_entry.status_changed` | jeder Statuswechsel | from→to |
| `cost_entry.confirmed` | Freigabe | confirmed_by, snapshot der freigegebenen Felder |
| `cost_entry.rejected` | Verwerfen | grund (optional) |
| `cost_entry.superseded` | Korrekturkette | superseded_by_id (neue Version) |
| `document.uploaded` | Upload | filename, mime, sha256, byte_size |
| `document.released` | Freigabe (mit cost_entry.confirmed, gleiche Tx) | released_by, sha256, retention_until |
| `document.superseded` | neue Beleg-Version | new_version_id, version |
| `document.integrity_verified` | Hash-Check-Lauf | result (ok/fail) |

- **Wer/Wann/Was** (SDD §6.6 Nachvollziehbarkeit): `actor_user_id` aus dem Session-Kontext (`uid`), `occurred_at` server-gesetzt, `entity_type`/`entity_id`, `payload_diff` als JSONB.
- **Tenant-Bindung:** `audit_event.organization_id` aus dem Kontext; RLS aktiv (M0). Append-only bleibt durch entzogene UPDATE/DELETE-Grants gewahrt (M0, `test_rls_coverage.py`).
- **Hash-Chain (optional, architecture.md §3.2d):** `prev_event_hash` ist bereits im Schema (M0). **[ENTSCHEIDUNG]** ob die Kette in M2 schon **befüllt/erzwungen** wird (jedes neue Event hasht das vorherige je `organization_id`) oder erst in M9 gehärtet wird. Empfehlung: in M2 das Feld **konsistent füllen** (billig: SHA-256 über kanonisch serialisiertes Vorgänger-Event je Org), die Verifikation/Erzwingung aber als M9-Härtung; so existiert ab M2 eine lückenerkennende Kette, ohne M2 zu überladen. Dies ist kein blockierender Punkt.

---

## 7. GoBD-Anforderungs-Mapping und DSGVO-Verzahnung

### 7.1 GoBD → Bausteine

| GoBD-Anforderung (SDD §6.6, §16, §17) | Wie in M2 erfüllt |
| --- | --- |
| **Unveränderbarkeit** | `BEFORE UPDATE/DELETE`-Trigger auf `cost_entry` (ab `confirmed`) und `invoice_document` (ab `released_at`), + entzogenes DELETE-Grant auf Belege (ADR-0003). Korrektur nur als Neuversion (§3.4/§4.4). |
| **Integrität** | SHA-256 je Beleg (`content_sha256`), verifiziert bei Freigabe und durch Prüf-Lauf (§4.5); content-adressierte Ablage (§5.2); später S3 Object-Lock/WORM. |
| **Nachvollziehbarkeit** (wer/wann/was) | `audit_event` für alle Beleg-/Kosten-/Freigabe-Vorgänge (§6); `confirmed_by`/`released_by` direkt an den Entitäten; optionale Hash-Chain. |
| **Vollständigkeit** | Append-only-Versionsketten (keine Lücken durch Löschen); `status`-Feld statt Hard-Delete; `rejected` bleibt erhalten; partieller Unique-Index „genau eine active" (§4.3) verhindert verwaiste/doppelte Aktiv-Zustände. |
| **Auffindbarkeit / maschinelle Auswertbarkeit** | strukturierte, indizierte Metadaten in der DB (Objekt, Lieferant, Datum, Leistungszeitraum, Kategorie); Datei content-adressiert auffindbar; Export ist eine reine Lese-Query (kein Spezialformat in M2 nötig). |
| **8-Jahre-Aufbewahrung** (BEG IV) | `retention_until` je Beleg, gesetzt bei Freigabe (§7.3). Vor Ablauf wird **nichts** entfernt. |
| **Verfahrensdokumentation** (SDD §6.6) | Dieses Dokument + ADR-0003 + die dokumentierte Statusmaschine (§3) bilden die Verfahrensdokumentation des Belegflusses (Eingang → Vorschlag → Freigabe → Abrechnung). |

### 7.2 DSGVO-Verzahnung (architecture.md §3.3 bestätigt)

M2 ändert das in M0/M1 angelegte Konzept **nicht**, sondern bestätigt und konkretisiert es für Belege:
- **Beleg bleibt aufbewahrungspflichtig:** Ein freigegebener Beleg (inkl. der darin enthaltenen Namen/Anschrift des Rechnungsadressaten) ist 8 Jahre unverändert aufzubewahren. Eine DSGVO-Löschanfrage löscht **nicht** den Beleg; die gesetzliche Aufbewahrungspflicht ist die Rechtsgrundlage gegen den Löschanspruch (Verarbeitungseinschränkung statt Löschung).
- **Personenbezogene Reinstammdaten bleiben lösch-/sperrbar:** Die `person`-Stammdaten (M1: `deletion_requested_at`, `processing_restricted`, `anonymized_at`) sind getrennt vom Beleg und können gelöscht/anonymisiert/gesperrt werden, ohne die Belegsubstanz anzutasten. `cost_entry` referenziert den Lieferanten per FK (`vendor_id`) — nicht durch Hineinkopieren von Stammdaten —, sodass eine spätere Sperrung der Stammdaten die Belegmetadaten nicht bricht.
- **Trennlinie:** Der innerhalb der Belegdatei abgedruckte Adressat ist Belegbestandteil (aufbewahrungspflichtig); die in `person` gehaltenen Reinstammdaten sind löschbar/sperrbar. Diese Trennung ist genau architecture.md §3.3.

### 7.3 [OFFEN] Aufbewahrungs-Startdatum je Belegart (SDD §18)

`retention_until` braucht ein maßgebliches Startdatum + 8 Jahre. **Welches Datum je Belegart gilt (Rechnungsdatum vs. Ende des Wirtschafts-/Kalenderjahres vs. Festschreibedatum), ist in SDD §18 explizit offen** und wird hier **nicht eigenmächtig entschieden**. M2 hält das Feld und die Berechnungs-Schnittstelle bereit; die konkrete Regel ist mit Steuerberater/Fachanwalt zu klären (spätestens vor M9).
- **Vorläufige, klar als Annahme markierte Default-Berechnung für M2-Tests:** `retention_until = (Ende des Kalenderjahres von `invoice_date`) + 8 Jahre`. Das ist eine konservative, gängige Lesart der handels-/steuerrechtlichen Fristberechnung (Frist beginnt mit Ablauf des Kalenderjahres). **Diese Annahme ist provisorisch** und vom Menschen/Fachberater zu bestätigen — sie steht hier nur, damit `bulk-impl` eine testbare Funktion hat, nicht als Rechtsaussage.

> **Keine Selbst-Zertifizierung:** Die Aufbewahrungslogik ist transparent und auditierbar gemacht; ihre rechtliche Richtigkeit im Einzelfall ist fachlich abzunehmen.

---

## 8. Implementierungs-Checkliste für `bulk-impl` (Opus 4.6)

> Architektur-/Korrektheitsentscheidungen sind in diesem Dokument + ADR-0003 getroffen. `bulk-impl` setzt sie um und **eskaliert** nur bei einer offenen Spezifikationsfrage (SDD §18, hier mit **[OFFEN]** markiert) oder einer ungeklärten **[ENTSCHEIDUNG]**. **Voraussetzung: der Mensch hat (a) A-1 (Kontenplan global vs. pro-Org) und (b) die offenen [ENTSCHEIDUNG]-Punkte bestätigt.** Vorher nicht committen.

### 8.1 Datenmodell + Migrationen (Reihenfolge analog M0/M1)

- [ ] **Migration `0005_cost_category`:** Tabelle (§1.3) + vollständiger Seed aller SDD-§7-Kategorien mit Defaults (§1.6). Bei A-1=global: **kein** `organization_id`, **kein** RLS. Bei A-1=pro-Org: `organization_id`, RLS-Migration, Seed je Org beim Anlegen.
- [ ] **Migration `0006_cost_entry_invoice_document`:** Tabellen `cost_entry` (§2.2) und `invoice_document` (§4.2) mit allen CHECK-Constraints (Status, Verteilerschlüssel, Umlagefähigkeit, `service_period_to >= service_period_from`), FKs (`RESTRICT` zu `organization`, wie M1), Indizes (§2.2/§4.2), `chain_root_id` + partieller Unique-Index `uq_active_doc_per_chain` (§4.3).
- [ ] **Grants:** `GRANT SELECT, INSERT, UPDATE, DELETE ON cost_entry TO app_rw`; `GRANT SELECT, INSERT, UPDATE ON invoice_document TO app_rw` (**kein DELETE** — ADR-0003 Backstop, [ENTSCHEIDUNG] bestätigen). `cost_category`: `GRANT SELECT ... TO app_rw` (global, nur lesen).
- [ ] **Migration `0007_m2_rls_policies`:** für `cost_entry` (und `invoice_document`) die vier Policies exakt im Muster von `0002`/`0004` (`org_isolation_select/insert/update/delete`, `NULLIF(... , '')::uuid`, fail-closed). `invoice_document`: DELETE-Policy **weglassen** (kein DELETE-Grant → analog `audit_event`-Präzedenz).
- [ ] **Migration `0008_immutability_triggers`** (eigener, reviewbarer Schritt — ADR-0003): PL/pgSQL `BEFORE UPDATE/DELETE`-Trigger für `cost_entry` (Einfrieren ab `confirmed`) und `invoice_document` (Einfrieren ab `released_at`), mit präziser inhaltstragend-vs.-zulässig-Spaltenliste. Trigger-Quelltext ausführlich kommentiert. `downgrade()` droppt Trigger + Funktion.
- [ ] **`TENANT_TABLES` in `tests/test_rls_coverage.py` erweitern:** `cost_entry`, `invoice_document` (und `cost_category` → `GLOBAL_TABLES` bei A-1=global, sonst `TENANT_TABLES`). `test_no_unknown_tables` zwingt diese Einordnung ohnehin.
- [ ] ORM-Modelle (`app/models/cost_category.py`, `cost_entry.py`, `invoice_document.py`) im Stil von `lease_agreement.py` (Mixins, `Numeric`, `Mapped`); Geld als `Decimal`.

### 8.2 Storage-Modul

- [ ] `app/core/storage.py`: `DocumentStore`-Protocol + `StoredObjectRef` (§5.1), `LocalFsDocumentStore` (content-adressiert, mandantenpartitioniert, read-only nach Schreiben), `get_store()/set_store()` (Swap-Muster wie `pdf.py`). **Kein `delete()`** im Protokoll.
- [ ] `verify_document_integrity(doc, store)` (§4.5): liest Datei, vergleicht SHA-256 mit `content_sha256`.
- [ ] Config-Eintrag (`settings`) für Storage-Wurzel/Backend.

### 8.3 Extraktions-Schnittstelle (PDF-Import) — nur Gerüst + Stub

- [ ] `DocumentExtractor`-Protocol (§3.5) + Stub-Default (liefert leere/teilbefüllte `ExtractionResult` mit Konfidenzen); echte OCR-Anbindung ist `bulk-impl`-Implementierung, Schnittstelle hier festgelegt.
- [ ] Ergebnis → `invoice_document.extraction_payload` (JSONB) + Vorbelegung der `cost_entry`-Felder; Start-Status nach Konfidenz (`draft_extracted`/`needs_review`); **nie** automatisch `confirmed`.

### 8.4 Statusmaschine + Services

- [ ] `app/services/cost_entry_service.py` mit den erlaubten Übergängen (§3.2): `create`, `update_draft`, `confirm`, `reject`, `create_correction_version` (§3.4). Übergänge, die nicht erlaubt sind, → 409/422; DB-Trigger ist die zweite Linie.
- [ ] `confirm` als **atomare** Transaktion (§3.6): Pflichtfeld-Validierung (2.3) → `cost_entry.confirmed` + Beleg-`released` + `retention_until` + Hash-Verify + `audit_event`, alles in einer `session.begin()`.
- [ ] `app/services/invoice_document_service.py`: `upload` (Datei→Store, SHA-256, Zeile `active`/nicht freigegeben, `audit_event`), `create_new_version` (§4.4), `verify_integrity`.
- [ ] Repositories im Stil von `base.py`/`audit_repo.py`: `CostCategoryRepository` (global, nur Lesen — kein `organization_id`-Filter), `CostEntryRepository` (tenant, mit Aktiv-Ketten-Query §3.4), `InvoiceDocumentRepository` (tenant, mit Aktiv-/Versions-Query §4.3, **kein** `delete`).
- [ ] `audit_event`-Schreiben über vorhandenes `AuditRepository`; optional `prev_event_hash` füllen (§6, [ENTSCHEIDUNG]).

### 8.5 API (API-first, RBAC wie M0)

- [ ] `app/api/cost_categories.py`: `GET /api/cost-categories` (Liste, alle Rollen lesen).
- [ ] `app/api/cost_entries.py`: `GET` (Liste/Detail), `POST` (Anlage, `require_write_dep`), `PATCH` (Entwurf bearbeiten, `require_write_dep`), `POST /{id}/confirm` (`require_write_dep`), `POST /{id}/reject` (`require_write_dep`), `POST /{id}/correction` (Neuversion, `require_write_dep`). READ_ONLY: nur GET. Direktzugriff auf fremde ID → 404 (nicht 403), wie M0.
- [ ] `app/api/invoice_documents.py`: `POST /upload` (multipart, `require_write_dep`), `GET` (Liste/Metadaten), `GET /{id}/content` (authentifizierter, tenant-geprüfter Datei-Stream — keine öffentliche URL), `POST /{id}/version` (Neuversion), `GET /{id}/verify` (Integritätscheck). **Kein** DELETE-Endpunkt.
- [ ] Router in `app/main.py` registrieren.

### 8.6 Tests, die die Unveränderbarkeit BEWEISEN (Kern der M2-Abnahme)

Im Stil von `tests/test_tenant_isolation.py`/`test_rls_coverage.py`, mit den vorhandenen Fixtures (`migrator_session`, `app_rw_session`, `client_a_admin/clerk/readonly`, `client_b_admin`). Diese Tests sind die objektive Messlatte für I2:

1. **`cost_entry` confirmed ist unveränderbar (Trigger):** über `app_rw_session` (org-Kontext gesetzt) eine `confirmed`-Zeile per direktem `UPDATE` der `gross_amount` ändern → DB wirft Exception. Beweist ADR-0003 auf DB-Ebene, unabhängig vom Service.
2. **`cost_entry` confirmed: DELETE verboten:** direktes `DELETE` einer `confirmed`-Zeile → Exception. Entwurf/`rejected`-Zeile: DELETE **erlaubt** (Positivtest, beweist statusbedingte Differenzierung).
3. **`cost_entry` draft ist editierbar:** `UPDATE` einer `needs_review`-Zeile → erfolgreich. (Positivtest gegen Übersperrung.)
4. **`invoice_document` released ist unveränderbar:** `UPDATE` von `content_sha256`/`storage_uri` einer freigegebenen Zeile → Exception; `active→superseded` → erlaubt (einziger zulässiger UPDATE).
5. **`invoice_document`: kein DELETE-Grant für `app_rw`:** analog `test_audit_event_no_update_delete_for_app_rw` → `information_schema.table_privileges` enthält **kein** `DELETE` für `app_rw` auf `invoice_document` (+ vorhandenes `SELECT/INSERT/UPDATE`).
6. **Korrektur erzeugt neue Version (cost_entry):** `confirm` → `create_correction_version` → alte Zeile `superseded_by_id` gesetzt + Inhalt unverändert; neue Zeile mit korrigiertem Wert; Aktiv-Query liefert genau die neue Zeile.
7. **Korrektur erzeugt neue Beleg-Version:** `upload`+release → `create_new_version` → alte Zeile `superseded`, neue `active`, `supersedes_id` korrekt; partieller Unique-Index lässt **keine** zweite `active` je Kette zu (Negativtest: manuelles zweites `active` → Unique-Verletzung).
8. **Hash-Verifikation:** `verify_document_integrity` ist `True` für unveränderte Datei; künstlich veränderte Datei im Store (Test-Manipulation) → `False`. Beweist Tamper-Evidenz.
9. **Hash bei Upload≠Freigabe:** Re-Verify in der Confirm-Transaktion schlägt fehl, wenn die Datei zwischen Upload und Freigabe abweicht (Transaktion rollt zurück, kein eingefrorener inkonsistenter Zustand).
10. **fail-closed / RLS (cost_entry, invoice_document):** Session **ohne** `app.current_org_id` → `SELECT` liefert null Zeilen; Client A sieht keine `cost_entry`/`invoice_document` von Org B; Direktzugriff auf fremde Beleg-ID → 404. (Erweitert `test_tenant_isolation.py` um die M2-Tabellen.)
11. **Statusmaschine:** unerlaubter Übergang (z. B. `confirmed → needs_review` per API) → 409/422; `confirm` ohne Pflichtfelder → 422 ohne Statuswechsel; READ_ONLY darf weder anlegen noch freigeben → 403.
12. **RLS-Coverage erweitert:** `test_rls_coverage.py` grün mit den neuen Tabellen (jede Tenant-Tabelle RLS+FORCE+Policies; `cost_category` korrekt als global ohne RLS, falls A-1=global).
13. **Atomarität der Freigabe:** wenn das `audit_event`-Schreiben in der Confirm-Tx scheitert (simuliert), bleibt `cost_entry` `needs_review` und der Beleg unreleased (Rollback). 
14. **`retention_until`-Berechnung** (mit der **als provisorisch markierten** Annahme aus §7.3): gesetzt bei Freigabe, NULL davor. Test prüft die Berechnung, nicht die Rechtskonformität.

### 8.7 Definition of Done für M2 (Übergabe an M3)

- [ ] Migrationen `0005`–`0008` als explizite, reviewbare Alembic-Schritte; `downgrade()` jeweils sauber.
- [ ] `cost_category` vollständig geseedet gemäß SDD §7; A-1-Entscheidung umgesetzt.
- [ ] `cost_entry`/`invoice_document` mit RLS (vier Policies, fail-closed) + Unveränderbarkeits-Trigger (ADR-0003) + Grant-Backstop.
- [ ] Storage-Modul (lokal, content-adressiert, swappable Protocol) + Hash-Verifikation.
- [ ] Statusmaschine + atomare Freigabe-Transaktion; `audit_event` für alle Vorgänge.
- [ ] API: Upload, Liste, Confirm, Reject, Version/Korrektur, Datei-Stream, Verify — RBAC korrekt, fremde ID → 404.
- [ ] **Unveränderbarkeits-Tests 1–14 grün** (kein UPDATE/DELETE auf `confirmed`/`released`; Korrektur = neue Version; Hash-Verifikation; fail-closed/RLS) — zusätzlich zu den weiterhin grünen 58 M0/M1-Tests.
- [ ] M2-Exit objektiv erfüllt: PDF hochladen → Vorschlag (`draft_extracted`) → bestätigen (`confirmed`) → unveränderbar archiviert (Trigger+Hash) → abrechnungsrelevant (Aktiv-Query). End-to-End-Test deckt diesen Pfad ab.
- [ ] Keine offene **[ENTSCHEIDUNG]**/**[OFFEN]** unmarkiert „weggeraten"; Eskalationen dokumentiert.

---

## 9. Was dieses Dokument bewusst NICHT entscheidet

- **Verteilungs-/Abrechnungslogik (M3):** *wie* ein Verteilerschlüssel rechnet, anteilige Zeiträume aus dem Leistungszeitraum, CO2-/Hausmeister-Aufteilung. M2 speichert nur Auswahl/Beträge/Anteilsfelder; die Engine ist M3 (`engine-correctness`).
- **Abrechnungs-Snapshots (`settlement_run`/`settlement_line`):** M3/M4 — derselbe Unveränderbarkeits-Trigger (ADR-0003 §3) wird dort wiederverwendet.
- **E-Rechnung-Parsing (XRechnung/ZUGFeRD):** M5 — dockt als zweite `DocumentExtractor`-Implementierung an die hier definierte Statusmaschine an.
- **Aufbewahrungs-Startdatum je Belegart:** SDD §18, **[OFFEN]**, fachlich zu klären (§7.3).
- **Genaue Anteils-/Vorwegabzugsmodellierung** (Hausmeister, CO2, Gewerbe): SDD §18, M3.
- **Hash-Chain-Härtung / DSGVO-Löschmechanik:** M9.

---

## 10. Zusammenfassung der offenen Entscheidungen (für den Menschen)

| # | Punkt | Empfehlung | Wann |
| --- | --- | --- | --- |
| **E-1** | `cost_category` global vs. pro-Org (Annahme A-1) | **global, versioniert** (kein RLS) | vor M2-Impl |
| **E-2** | Unveränderbarkeits-Mechanik | **status-bewusster Trigger + entzogenes DELETE-Grant auf Belege** (ADR-0003) | bestätigen vor M2-Impl |
| **E-3** | DELETE auf `invoice_document` für `app_rw` komplett entziehen? | **ja** (Backstop, Belege nie löschen) | vor M2-Impl |
| **E-4** | Verkettungsrichtung `cost_entry` | **Vorwärts (`superseded_by_id`)**; `invoice_document` bleibt Rückwärts (`supersedes_id`) | bei Impl festklopfen |
| **E-5** | `chain_root_id` + partieller Unique-Index „genau eine active" | **ja** (einfach, codebasis-konform) | bei Impl |
| **E-6** | Default-Status bei manueller Anlage; `confirm` immer expliziter Zweitschritt | editierbarer Datensatz, `confirm` nie implizit | bei Impl |
| **E-7** | Konkrete Default-Verteilerschlüssel bei SDD-Mehrdeutigkeit („Fläche oder MEA") | Eigentümer i. d. R. `MEA`, Mieter i. d. R. `WOHNFLAECHE`, übersteuerbar | fachlich, vor Seed |
| **O-1** | **[OFFEN, SDD §18]** Aufbewahrungs-Startdatum je Belegart | nicht eigenmächtig; provisorische Test-Annahme (KJ-Ende + 8 J.) klar markiert; StB/Fachanwalt | vor M9 |
